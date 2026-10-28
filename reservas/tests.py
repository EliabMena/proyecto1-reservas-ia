import json
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Booking, Resource


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
