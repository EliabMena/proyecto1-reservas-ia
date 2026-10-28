import json
from datetime import datetime

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Availability, Booking, Resource


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

    def _reservar_en_bd(self, start, end):
        return Booking.objects.create(
            user=self.user,
            resource=self.resource,
            start_time=datetime.fromisoformat(start),
            end_time=datetime.fromisoformat(end),
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
