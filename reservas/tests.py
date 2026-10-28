import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Booking, Resource


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
