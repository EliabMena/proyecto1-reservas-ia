import json
from datetime import datetime

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Booking, Resource


class ConfirmacionReservaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='cliente',
            password='clave-cliente-123',
        )
        self.other_user = User.objects.create_user(
            username='otro',
            password='clave-otro-123',
        )
        self.resource = Resource.objects.create(name='Pista de prueba')
        self.client.force_login(self.user)
        self.url_crear = reverse('api_crear_reserva')

    def _crear_reserva(self, start='2026-11-02T10:00:00+00:00',
                       end='2026-11-02T11:00:00+00:00'):
        return self.client.post(
            self.url_crear,
            data=json.dumps({
                'resource_id': self.resource.id,
                'start_time': start,
                'end_time': end,
                'repeat_weeks': 0,
            }),
            content_type='application/json',
        )

    def _reserva_en_bd(self, start='2026-11-02T10:00:00+00:00',
                       end='2026-11-02T11:00:00+00:00'):
        return Booking.objects.create(
            user=self.user,
            resource=self.resource,
            start_time=datetime.fromisoformat(start),
            end_time=datetime.fromisoformat(end),
        )

    def test_la_respuesta_incluye_identificador_y_datos_principales(self):
        response = self._crear_reserva()

        self.assertTrue(response.json()['success'])
        reserva = Booking.objects.get()
        datos = response.json()['reservas'][0]

        self.assertEqual(datos['id'], reserva.id)
        self.assertEqual(datos['resource'], self.resource.name)
        self.assertEqual(datos['start'], '2026-11-02T10:00:00+00:00')
        self.assertEqual(datos['end'], '2026-11-02T11:00:00+00:00')
        self.assertEqual(
            datos['detail_url'],
            reverse('reserva_detalle', args=[reserva.id]),
        )

    def test_una_reserva_fallida_no_devuelve_confirmacion(self):
        self._reserva_en_bd()

        response = self._crear_reserva(
            start='2026-11-02T10:30:00+00:00',
            end='2026-11-02T11:30:00+00:00',
        )
        datos = response.json()

        self.assertFalse(datos['success'])
        self.assertNotIn('reservas', datos)
        self.assertEqual(Booking.objects.count(), 1)

    def test_el_propietario_puede_consultar_la_reserva(self):
        reserva = self._reserva_en_bd()

        response = self.client.get(reverse('reserva_detalle', args=[reserva.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Reserva confirmada')
        self.assertContains(response, f'Reserva #{reserva.id}')
        self.assertContains(response, self.resource.name)

    def test_otro_usuario_no_puede_consultar_la_reserva(self):
        reserva = self._reserva_en_bd()
        self.client.force_login(self.other_user)

        response = self.client.get(reverse('reserva_detalle', args=[reserva.id]))

        self.assertEqual(response.status_code, 404)

    def test_consultar_una_reserva_requiere_iniciar_sesion(self):
        reserva = self._reserva_en_bd()
        self.client.logout()

        response = self.client.get(reverse('reserva_detalle', args=[reserva.id]))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_el_calendario_incluye_el_modal_de_confirmacion(self):
        response = self.client.get(reverse('calendario_full', args=[self.resource.id]))

        self.assertContains(response, 'id="modal-confirmacion"')
        self.assertContains(response, 'id="lista-confirmaciones"')
        self.assertContains(response, 'Reserva confirmada')
