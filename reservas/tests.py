import json
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Availability, Booking, Resource


class RevisionReportePruebasTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='cliente',
            password='clave-cliente-123',
        )
        self.other_user = User.objects.create_user(
            username='otro-usuario',
            password='clave-otro-123',
        )
        self.resource = Resource.objects.create(name='Pista de prueba')
        self.client.force_login(self.user)

    def _crear_reserva(self, user=None, start=None):
        user = user or self.user
        start = start or (
            timezone.localtime(timezone.now()).replace(
                minute=0,
                second=0,
                microsecond=0,
            ) + timedelta(hours=1)
        )
        return Booking.objects.create(
            user=user,
            resource=self.resource,
            start_time=start,
            end_time=start + timedelta(hours=1),
        )

    def _payload_reserva(self):
        return json.dumps({
            'resource_id': self.resource.id,
            'start_time': '2026-12-01T10:00:00+00:00',
            'end_time': '2026-12-01T11:00:00+00:00',
            'repeat_weeks': 0,
        })

    def test_installed_apps_incluye_reservas_una_sola_vez(self):
        self.assertEqual(settings.INSTALLED_APPS.count('reservas'), 1)

    def test_api_reservas_no_expone_el_usuario_de_otras_reservas(self):
        self._crear_reserva(user=self.other_user)

        response = self.client.get(
            reverse('api_reservas', args=[self.resource.id])
        )
        eventos = response.json()
        contenido = response.content.decode()

        self.assertEqual(eventos[0]['title'], 'Reservado')
        self.assertNotIn(self.other_user.username, contenido)
        self.assertNotIn('user', eventos[0])

    def test_calendario_full_no_expone_usuarios_y_genera_cookie_csrf(self):
        self._crear_reserva(user=self.other_user)

        response = self.client.get(
            reverse('calendario_full', args=[self.resource.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.other_user.username)
        self.assertIn('csrftoken', response.cookies)

    def test_crear_reserva_rechaza_peticiones_sin_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        csrf_client.get(reverse('calendario_full', args=[self.resource.id]))
        token = csrf_client.cookies['csrftoken'].value
        url = reverse('api_crear_reserva')

        response_sin_token = csrf_client.post(
            url,
            data=self._payload_reserva(),
            content_type='application/json',
        )
        response_con_token = csrf_client.post(
            url,
            data=self._payload_reserva(),
            content_type='application/json',
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response_sin_token.status_code, 403)
        self.assertEqual(response_con_token.status_code, 200)
        self.assertTrue(response_con_token.json()['success'])
        self.assertEqual(Booking.objects.count(), 1)

    def test_calendario_semanal_muestra_reservas_sin_usuario(self):
        inicio = timezone.localtime(timezone.now()).replace(
            hour=10,
            minute=0,
            second=0,
            microsecond=0,
        )
        self._crear_reserva(user=self.other_user, start=inicio)

        response = self.client.get(reverse('calendario', args=[self.resource.id]))

        self.assertContains(response, 'Reservado')
        self.assertContains(response, '10:00')
        self.assertNotContains(response, self.other_user.username)

    def test_calendario_semanal_navega_a_la_semana_siguiente(self):
        hoy = timezone.localdate()
        lunes_actual = hoy - timedelta(days=hoy.weekday())

        response = self.client.get(
            reverse('calendario', args=[self.resource.id]),
            {'week': 'next'},
        )
        fechas = [dia['fecha'] for dia in response.context['dias_semana']]

        self.assertEqual(len(fechas), 7)
        self.assertEqual(fechas[0], lunes_actual + timedelta(days=7))

    def test_favicon_no_devuelve_404(self):
        response = self.client.get('/favicon.ico')

        self.assertEqual(response.status_code, 204)


class CalendarioDisponibilidadTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='cliente',
            password='clave-segura-123',
        )
        self.resource = Resource.objects.create(name='Pista de prueba')
        self.client.force_login(self.user)
        self.url_crear = reverse('api_crear_reserva')

    def _crear_reserva(self, start, end, repeat_weeks=0):
        return self.client.post(
            self.url_crear,
            data=json.dumps({
                'resource_id': self.resource.id,
                'start_time': start,
                'end_time': end,
                'repeat_weeks': repeat_weeks,
            }),
            content_type='application/json',
        )

    def _reservar_en_bd(self, start, end, status=Booking.Status.CONFIRMED):
        return Booking.objects.create(
            user=self.user,
            resource=self.resource,
            start_time=datetime.fromisoformat(start),
            end_time=datetime.fromisoformat(end),
            status=status,
        )

    def test_permite_reservar_una_franja_disponible(self):
        response = self._crear_reserva(
            '2026-11-02T10:00:00+00:00',
            '2026-11-02T11:00:00+00:00',
        )

        self.assertTrue(response.json()['success'])
        self.assertEqual(Booking.objects.count(), 1)

    def test_calendario_distingue_disponible_reservado_y_bloqueado(self):
        self._reservar_en_bd(
            '2026-11-02T10:00:00+00:00',
            '2026-11-02T11:00:00+00:00',
        )
        Availability.objects.create(
            resource=self.resource,
            start_time=datetime.fromisoformat('2026-11-03T10:00:00+00:00'),
            end_time=datetime.fromisoformat('2026-11-03T11:00:00+00:00'),
        )

        response = self.client.get(reverse('calendario_full', args=[self.resource.id]))

        self.assertContains(response, 'Disponible')
        self.assertContains(response, 'Reservado')
        self.assertContains(response, 'Bloqueado')
        self.assertContains(response, 'No disponible')
        self.assertContains(response, 'selectAllow')

    def test_rechaza_reserva_solapada_con_otra_reserva(self):
        self._reservar_en_bd(
            '2026-11-02T10:00:00+00:00',
            '2026-11-02T11:00:00+00:00',
        )

        response = self._crear_reserva(
            '2026-11-02T10:30:00+00:00',
            '2026-11-02T11:30:00+00:00',
        )

        self.assertFalse(response.json()['success'])
        self.assertIn('reserva', response.json()['error'].lower())
        self.assertEqual(Booking.objects.count(), 1)

    def test_rechaza_reserva_en_franja_bloqueada(self):
        Availability.objects.create(
            resource=self.resource,
            start_time=datetime.fromisoformat('2026-11-02T10:00:00+00:00'),
            end_time=datetime.fromisoformat('2026-11-02T11:00:00+00:00'),
        )

        response = self._crear_reserva(
            '2026-11-02T10:00:00+00:00',
            '2026-11-02T11:00:00+00:00',
        )

        self.assertFalse(response.json()['success'])
        self.assertIn('bloqueada', response.json()['error'].lower())
        self.assertEqual(Booking.objects.count(), 0)

    def test_revalida_la_disponibilidad_al_confirmar(self):
        primera = self._crear_reserva(
            '2026-11-02T10:00:00+00:00',
            '2026-11-02T11:00:00+00:00',
        )
        segunda = self._crear_reserva(
            '2026-11-02T10:00:00+00:00',
            '2026-11-02T11:00:00+00:00',
        )

        self.assertTrue(primera.json()['success'])
        self.assertFalse(segunda.json()['success'])
        self.assertEqual(Booking.objects.count(), 1)

    def test_permite_una_franja_adyacente_a_otra_reserva(self):
        self._reservar_en_bd(
            '2026-11-02T10:00:00+00:00',
            '2026-11-02T11:00:00+00:00',
        )

        response = self._crear_reserva(
            '2026-11-02T11:00:00+00:00',
            '2026-11-02T12:00:00+00:00',
        )

        self.assertTrue(response.json()['success'])
        self.assertEqual(Booking.objects.count(), 2)

    def test_recurrencia_con_conflicto_no_guarda_reservas_parciales(self):
        self._reservar_en_bd(
            '2026-11-09T10:00:00+00:00',
            '2026-11-09T11:00:00+00:00',
        )

        response = self._crear_reserva(
            '2026-11-02T10:00:00+00:00',
            '2026-11-02T11:00:00+00:00',
            repeat_weeks=2,
        )

        self.assertFalse(response.json()['success'])
        self.assertEqual(Booking.objects.count(), 1)

    def test_rechaza_una_reserva_con_fin_anterior_al_inicio(self):
        response = self._crear_reserva(
            '2026-11-02T11:00:00+00:00',
            '2026-11-02T10:00:00+00:00',
        )

        self.assertFalse(response.json()['success'])
        self.assertIn('posterior', response.json()['error'].lower())
        self.assertEqual(Booking.objects.count(), 0)

    def test_rechaza_una_reserva_en_un_recurso_inexistente(self):
        response = self.client.post(
            self.url_crear,
            data=json.dumps({
                'resource_id': 9999,
                'start_time': '2026-11-02T10:00:00+00:00',
                'end_time': '2026-11-02T11:00:00+00:00',
                'repeat_weeks': 0,
            }),
            content_type='application/json',
        )

        self.assertFalse(response.json()['success'])
        self.assertIn('no existe', response.json()['error'].lower())
        self.assertEqual(Booking.objects.count(), 0)

    def test_una_reserva_cancelada_no_bloquea_la_franja(self):
        self._reservar_en_bd(
            '2026-11-02T10:00:00+00:00',
            '2026-11-02T11:00:00+00:00',
            status=Booking.Status.CANCELLED,
        )

        response = self._crear_reserva(
            '2026-11-02T10:00:00+00:00',
            '2026-11-02T11:00:00+00:00',
        )

        self.assertTrue(response.json()['success'])
        self.assertEqual(
            Booking.objects.filter(status=Booking.Status.CONFIRMED).count(),
            1,
        )


class HistorialYCancelacionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='cliente',
            password='clave-cliente-123',
        )
        self.other_user = User.objects.create_user(
            username='otro',
            password='clave-otro-123',
        )
        self.resource = Resource.objects.create(name='Pista propia')
        self.other_resource = Resource.objects.create(name='Pista ajena')
        self.client.force_login(self.user)
        self.url_historial = reverse('mis_reservas')

    def _crear_reserva(self, user=None, resource=None, inicio=None, duracion_horas=1):
        user = user or self.user
        resource = resource or self.resource
        inicio = inicio or (timezone.now() + timedelta(days=2))
        return Booking.objects.create(
            user=user,
            resource=resource,
            start_time=inicio,
            end_time=inicio + timedelta(hours=duracion_horas),
        )

    def test_el_historial_solo_muestra_las_reservas_propias(self):
        reserva_propia = self._crear_reserva()
        reserva_ajena = self._crear_reserva(
            user=self.other_user,
            resource=self.other_resource,
        )

        response = self.client.get(self.url_historial)
        reservas = list(response.context['reservas'])

        self.assertEqual(response.status_code, 200)
        self.assertIn(reserva_propia, reservas)
        self.assertNotIn(reserva_ajena, reservas)
        self.assertContains(response, self.resource.name)
        self.assertNotContains(response, self.other_resource.name)

    def test_el_historial_requiere_iniciar_sesion(self):
        self.client.logout()

        response = self.client.get(self.url_historial)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_cancelar_reserva_futura_actualiza_estado_y_libera_la_franja(self):
        reserva = self._crear_reserva(inicio=timezone.now() + timedelta(days=2))

        response = self.client.post(reverse('cancelar_reserva', args=[reserva.id]))
        reserva.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(reserva.status, Booking.Status.CANCELLED)

        respuesta_api = self.client.post(
            reverse('api_crear_reserva'),
            data=json.dumps({
                'resource_id': self.resource.id,
                'start_time': reserva.start_time.isoformat(),
                'end_time': reserva.end_time.isoformat(),
                'repeat_weeks': 0,
            }),
            content_type='application/json',
        )

        self.assertTrue(respuesta_api.json()['success'])
        self.assertEqual(
            Booking.objects.filter(status=Booking.Status.CONFIRMED).count(),
            1,
        )

    def test_no_se_puede_cancelar_una_reserva_pasada(self):
        reserva = self._crear_reserva(inicio=timezone.now() - timedelta(days=1))

        response = self.client.post(reverse('cancelar_reserva', args=[reserva.id]))
        reserva.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(reserva.status, Booking.Status.CONFIRMED)

    def test_no_se_puede_cancelar_una_reserva_de_otro_usuario(self):
        reserva = self._crear_reserva(user=self.other_user)

        response = self.client.post(reverse('cancelar_reserva', args=[reserva.id]))
        reserva.refresh_from_db()

        self.assertEqual(response.status_code, 404)
        self.assertEqual(reserva.status, Booking.Status.CONFIRMED)

    def test_cancelar_requiere_metodo_post(self):
        reserva = self._crear_reserva()

        response = self.client.get(reverse('cancelar_reserva', args=[reserva.id]))
        reserva.refresh_from_db()

        self.assertEqual(response.status_code, 405)
        self.assertEqual(reserva.status, Booking.Status.CONFIRMED)

    def test_las_reservas_canceladas_no_ocupan_el_calendario(self):
        cancelada = self._crear_reserva(inicio=timezone.now() + timedelta(days=3))
        cancelada.status = Booking.Status.CANCELLED
        cancelada.save(update_fields=['status'])
        confirmada = self._crear_reserva(inicio=timezone.now() + timedelta(days=4))

        response = self.client.get(
            reverse('api_reservas', args=[self.resource.id])
        )
        eventos = response.json()

        self.assertEqual(len(eventos), 1)
        self.assertEqual(eventos[0]['start'], confirmada.start_time.isoformat())
