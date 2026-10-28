from datetime import date, datetime, time, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Booking, Resource
from .views import _calcular_prevision_demanda


class PrevisionDemandaTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='clave-admin-123',
        )
        self.client_user = User.objects.create_user(
            username='cliente',
            password='clave-cliente-123',
        )
        self.resource = Resource.objects.create(name='Pista de prueba')
        self.referencia = date(2026, 11, 15)
        self.url = reverse('prevision_demanda')

    def _reservar(self, *fechas):
        for fecha in fechas:
            Booking.objects.create(
                user=self.client_user,
                resource=self.resource,
                start_time=datetime.fromisoformat(fecha),
                end_time=datetime.fromisoformat(fecha.replace('T10:00:00', 'T11:00:00')),
            )

    def test_solo_los_administradores_pueden_acceder(self):
        respuesta_anonima = self.client.get(self.url)

        self.client.force_login(self.client_user)
        respuesta_cliente = self.client.get(self.url)

        self.assertEqual(respuesta_anonima.status_code, 302)
        self.assertEqual(respuesta_cliente.status_code, 302)

    def test_el_administrador_puede_consultar_el_grafico(self):
        self.client.force_login(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Previsión de demanda')
        self.assertContains(response, 'Histórico')
        self.assertContains(response, 'Estimación')

    def test_calcula_historico_y_prevision_con_datos_suficientes(self):
        self._reservar(
            '2026-09-22T10:00:00+00:00',
            '2026-09-29T10:00:00+00:00',
            '2026-09-30T10:00:00+00:00',
            '2026-10-06T10:00:00+00:00',
            '2026-10-20T10:00:00+00:00',
            '2026-10-27T10:00:00+00:00',
            '2026-10-28T10:00:00+00:00',
            '2026-11-03T10:00:00+00:00',
            '2026-11-10T10:00:00+00:00',
            '2026-11-11T10:00:00+00:00',
            '2026-11-12T10:00:00+00:00',
        )

        datos = _calcular_prevision_demanda(self.referencia)

        self.assertTrue(datos['datos_suficientes'])
        self.assertEqual(datos['semanas_con_datos'], 7)
        self.assertEqual(datos['total_historico'], 11)
        self.assertEqual(datos['historico'][:8], [1, 2, 1, 0, 1, 2, 1, 3])
        self.assertEqual(datos['historico'][8:], [None, None, None, None])
        self.assertEqual(datos['prevision'][:7], [None] * 7)
        self.assertEqual(datos['prevision'][7], 3)
        self.assertEqual(datos['prevision'][8:], [1.8, 1.8, 1.8, 1.8])
        self.assertEqual(datos['promedio_semanal'], 1.8)
        self.assertEqual(len(datos['labels']), 12)

    def test_indica_cuando_no_hay_datos_suficientes(self):
        hoy = timezone.localdate()
        inicio_semana = hoy - timedelta(days=hoy.weekday())
        inicio = timezone.make_aware(
            datetime.combine(inicio_semana, time(10)),
            timezone.get_current_timezone(),
        )
        Booking.objects.create(
            user=self.client_user,
            resource=self.resource,
            start_time=inicio,
            end_time=inicio + timedelta(hours=1),
        )

        datos = _calcular_prevision_demanda()
        self.client.force_login(self.admin)
        response = self.client.get(self.url)

        self.assertFalse(datos['datos_suficientes'])
        self.assertEqual(datos['semanas_con_datos'], 1)
        self.assertIsNone(datos['promedio_semanal'])
        self.assertTrue(all(valor is None for valor in datos['prevision']))
        self.assertFalse(response.context['datos_suficientes'])
        self.assertContains(response, 'No hay datos históricos suficientes')

    def test_la_home_enlaza_la_prevision_para_administradores(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse('home'))

        self.assertContains(response, self.url)
