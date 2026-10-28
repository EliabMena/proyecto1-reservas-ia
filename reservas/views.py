import json
from datetime import datetime, timedelta, time

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .models import Availability, Booking, Resource


# Horario por defecto (si no hay Availability definida)
DEFAULT_START = time(9, 0)   # 09:00
DEFAULT_END = time(21, 0)    # 21:00

# Configuración de la previsión de demanda
SEMANAS_HISTORICO = 8
SEMANAS_PREVISION = 4
MIN_SEMANAS_CON_DATOS = 2


def _inicio_semana(fecha):
    """Devuelve el lunes de la semana a la que pertenece una fecha."""
    return fecha - timedelta(days=fecha.weekday())


def _calcular_prevision_demanda(fecha_referencia=None):
    """
    Calcula reservas semanales históricas y una estimación de demanda futura.

    La estimación usa la media de las últimas cuatro semanas históricas. Se
    considera que no hay datos suficientes si no hay reservas en al menos dos
    semanas del periodo histórico.
    """
    hoy = fecha_referencia or timezone.localdate()
    inicio_semana_actual = _inicio_semana(hoy)
    inicio_historico = inicio_semana_actual - timedelta(weeks=SEMANAS_HISTORICO - 1)
    fin_historico = inicio_semana_actual + timedelta(weeks=1)

    zona = timezone.get_current_timezone()
    inicio_dt = timezone.make_aware(datetime.combine(inicio_historico, time.min), zona)
    fin_dt = timezone.make_aware(datetime.combine(fin_historico, time.min), zona)

    semanas = [
        inicio_historico + timedelta(weeks=i)
        for i in range(SEMANAS_HISTORICO)
    ]
    conteos = {semana: 0 for semana in semanas}

    reservas = Booking.objects.filter(
        status=Booking.Status.CONFIRMED,
        start_time__gte=inicio_dt,
        start_time__lt=fin_dt,
    ).only('start_time')

    for reserva in reservas:
        fecha_reserva = timezone.localtime(reserva.start_time).date()
        semana = _inicio_semana(fecha_reserva)
        if semana in conteos:
            conteos[semana] += 1

    etiquetas_historico = [semana.strftime('%d/%m') for semana in semanas]
    valores_historico = [conteos[semana] for semana in semanas]

    semanas_con_datos = sum(1 for valor in valores_historico if valor > 0)
    datos_suficientes = semanas_con_datos >= MIN_SEMANAS_CON_DATOS

    promedio_semanal = None
    if datos_suficientes:
        ventana = valores_historico[-4:]
        promedio_semanal = round(sum(ventana) / len(ventana), 1)
        valores_prevision = [promedio_semanal] * SEMANAS_PREVISION
        datos_prevision = (
            [None] * (SEMANAS_HISTORICO - 1)
            + [valores_historico[-1]]
            + valores_prevision
        )
    else:
        datos_prevision = [None] * (SEMANAS_HISTORICO + SEMANAS_PREVISION)

    etiquetas_prevision = [
        (inicio_semana_actual + timedelta(weeks=i + 1)).strftime('%d/%m')
        for i in range(SEMANAS_PREVISION)
    ]

    return {
        'labels': etiquetas_historico + etiquetas_prevision,
        'historico': valores_historico + [None] * SEMANAS_PREVISION,
        'prevision': datos_prevision,
        'datos_suficientes': datos_suficientes,
        'semanas_con_datos': semanas_con_datos,
        'total_historico': sum(valores_historico),
        'promedio_semanal': promedio_semanal,
    }



def calendario(request, resource_id):
    """
    Muestra un calendario semanal de reservas para un recurso.
    """
    resource = get_object_or_404(Resource, id=resource_id)

    hoy = timezone.localdate()
    delta = 0
    if request.GET.get('week') == 'prev':
        delta = -7
    elif request.GET.get('week') == 'next':
        delta = 7

    inicio_semana = hoy - timedelta(days=hoy.weekday()) + timedelta(days=delta)
    dias_semana = [inicio_semana + timedelta(days=i) for i in range(7)]

    reservas = Booking.objects.filter(
        resource=resource,
        status=Booking.Status.CONFIRMED,
        start_time__date__gte=inicio_semana,
        start_time__date__lte=inicio_semana + timedelta(days=6)
    ).order_by('start_time')

    reservas_por_dia = {dia.isoformat(): [] for dia in dias_semana}
    for reserva in reservas:
        clave = timezone.localtime(reserva.start_time).date().isoformat()
        reservas_por_dia[clave].append(reserva)

    dias_semana = [
        {'fecha': dia, 'reservas': reservas_por_dia[dia.isoformat()]}
        for dia in dias_semana
    ]

    return render(request, 'reservas/calendario.html', {
        'resource': resource,
        'dias_semana': dias_semana,
    })


@ensure_csrf_cookie
def calendario_full(request, resource_id):
    """
    Calendario interactivo con FullCalendar.
    """
    resource = get_object_or_404(Resource, id=resource_id)

    reservas = Booking.objects.filter(
        resource=resource,
        status=Booking.Status.CONFIRMED,
    ).order_by('start_time')
    bloqueos = Availability.objects.filter(resource=resource)

    # Lista de reservas
    reservas_json = [
        {
            "start": reserva.start_time.isoformat(),
            "end": reserva.end_time.isoformat(),
            "title": "Reservado",
            "color": "#10b981",  # Verde reservas
        }
        for reserva in reservas
    ]

    # Lista de bloqueos (rojo)
    bloqueos_json = [
        {
            "start": bloqueo.start_time.isoformat(),
            "end": bloqueo.end_time.isoformat(),
            "title": "No disponible",
        }
        for bloqueo in bloqueos
    ]

    return render(request, 'reservas/calendario_full.html', {
        'resource': resource,
        'reservas_json': json.dumps(reservas_json),
        'bloqueos_json': json.dumps(bloqueos_json),
        'is_authenticated': request.user.is_authenticated,
    })



def _conflicto_disponibilidad(resource_id, start, end):
    """
    Devuelve el motivo por el que una franja no está disponible, si lo hay.
    """
    if Availability.objects.filter(
        resource_id=resource_id,
        start_time__lt=end,
        end_time__gt=start
    ).exists():
        return 'La franja horaria está bloqueada por el administrador.'

    if Booking.objects.filter(
        resource_id=resource_id,
        status=Booking.Status.CONFIRMED,
        start_time__lt=end,
        end_time__gt=start
    ).exists():
        return 'Ya existe una reserva en esta franja horaria.'

    return None


def api_crear_reserva(request):
    """
    Endpoint para crear una nueva reserva, con soporte para repeticiones semanales.
    """
    if request.method == 'POST':
        if not request.user.is_authenticated:
            return JsonResponse({'success': False, 'error': 'Debes iniciar sesión para reservar.'}, status=403)
        try:
            data = json.loads(request.body)
            resource_id = data.get('resource_id')
            start_time = parse_datetime(data.get('start_time'))
            end_time = parse_datetime(data.get('end_time'))
            repeat_weeks = int(data.get('repeat_weeks', 0))

            resource = Resource.objects.filter(pk=resource_id).first()
            if resource is None:
                return JsonResponse({'success': False, 'error': 'El recurso no existe.'})

            if not start_time or not end_time:
                return JsonResponse({'success': False, 'error': 'Las fechas de la reserva no son válidas.'})

            if start_time >= end_time:
                return JsonResponse({'success': False, 'error': 'La hora de fin debe ser posterior a la de inicio.'})

            if repeat_weeks < 0:
                return JsonResponse({'success': False, 'error': 'El número de repeticiones no es válido.'})

            reservas_creadas = []

            with transaction.atomic():
                for i in range(repeat_weeks + 1):
                    start = start_time + timedelta(weeks=i)
                    end = end_time + timedelta(weeks=i)

                    conflicto = _conflicto_disponibilidad(resource_id, start, end)
                    if conflicto:
                        raise ValueError(f'{conflicto} (semana {i + 1})')

                    booking = Booking(
                        user=request.user,
                        resource=resource,
                        start_time=start,
                        end_time=end
                    )
                    booking.full_clean()
                    booking.save()
                    reservas_creadas.append({
                        'id': booking.id,
                        'resource': resource.name,
                        'start': start.isoformat(),
                        'end': end.isoformat(),
                        'detail_url': reverse('reserva_detalle', args=[booking.id]),
                    })

            return JsonResponse({'success': True, 'reservas': reservas_creadas})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Método no permitido'}, status=405)


@login_required(login_url='login')
def mis_reservas(request):
    """
    Muestra únicamente las reservas del usuario autenticado.
    """
    reservas = Booking.objects.filter(
        user=request.user,
    ).select_related('resource').order_by('-start_time')

    return render(request, 'reservas/mis_reservas.html', {
        'reservas': reservas,
        'ahora': timezone.now(),
    })


@login_required(login_url='login')
@require_POST
def cancelar_reserva(request, booking_id):
    """
    Cancela una reserva futura del usuario autenticado.
    """
    reserva = get_object_or_404(Booking, pk=booking_id, user=request.user)

    if reserva.status == Booking.Status.CANCELLED:
        messages.error(request, 'La reserva ya está cancelada.')
    elif reserva.start_time <= timezone.now():
        messages.error(request, 'No se puede cancelar una reserva que ya ha comenzado.')
    else:
        reserva.status = Booking.Status.CANCELLED
        reserva.save(update_fields=['status'])
        messages.success(request, 'La reserva se ha cancelado correctamente.')

    return redirect('mis_reservas')


@login_required(login_url='login')
def reserva_detalle(request, booking_id):
    """
    Muestra la confirmación de una reserva al usuario que la creó.
    """
    reservas = Booking.objects.select_related('resource', 'user')

    if request.user.is_staff:
        reserva = get_object_or_404(reservas, pk=booking_id)
    else:
        reserva = get_object_or_404(reservas, pk=booking_id, user=request.user)

    return render(request, 'reservas/reserva_detalle.html', {
        'reserva': reserva,
    })


@staff_member_required
def prevision_demanda(request):
    """
    Muestra al administrador un gráfico de demanda histórica y estimada.
    """
    datos_grafico = _calcular_prevision_demanda()

    return render(request, 'reservas/prevision_demanda.html', {
        'datos_grafico': datos_grafico,
        'datos_suficientes': datos_grafico['datos_suficientes'],
        'semanas_con_datos': datos_grafico['semanas_con_datos'],
        'total_historico': datos_grafico['total_historico'],
        'promedio_semanal': datos_grafico['promedio_semanal'],
        'semanas_historico': SEMANAS_HISTORICO,
        'semanas_prevision': SEMANAS_PREVISION,
    })


def login_view(request):
    """
    Vista para el inicio de sesión de usuarios.
    """
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            return redirect('/')
        else:
            messages.error(request, "Usuario o contraseña incorrectos.")
    return render(request, 'reservas/login.html')


def logout_view(request):
    """
    Cerrar sesión.
    """
    logout(request)
    return redirect('/login/')

@require_GET
def api_perfil(request):
    """
    Devuelve únicamente los datos personales del usuario autenticado.

    Al obtener el usuario desde la sesión (request.user) no se acepta ningún
    identificador externo, por lo que no es posible consultar los datos de
    otra persona.
    """
    if not request.user.is_authenticated:
        return JsonResponse(
            {'success': False, 'error': 'Debes iniciar sesión para consultar tus datos.'},
            status=403
        )

    user = request.user
    nombre_completo = f"{user.first_name} {user.last_name}".strip()

    return JsonResponse({
        'success': True,
        'perfil': {
            'username': user.username,
            'nombre': nombre_completo or user.username,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'email': user.email,
        }
    })


def api_reservas(request, resource_id):
    """
    API que devuelve todas las reservas de un recurso en formato JSON.
    """
    reservas = Booking.objects.filter(
        resource_id=resource_id,
        status=Booking.Status.CONFIRMED,
    ).order_by('start_time')
    eventos = [
        {
            "title": "Reservado",
            "start": reserva.start_time.isoformat(),
            "end": reserva.end_time.isoformat(),
            "color": "#10b981",  # Verde
        }
        for reserva in reservas
    ]
    return JsonResponse(eventos, safe=False)


def favicon(request):
    """
    Evita el error 404 del navegador al solicitar el favicon.
    """
    return HttpResponse(status=204)


def home(request):
    """
    Vista principal que muestra todas las pistas (resources).
    """
    resources = Resource.objects.all()  # Obtenemos todas las pistas
    return render(request, 'reservas/home.html', {'resources': resources})


