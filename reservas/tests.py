import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Booking, Resource


class PerfilUsuarioTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='ana',
            password='clave-segura-123',
            first_name='Ana',
            last_name='García',
            email='ana@example.com',
        )
        self.other_user = User.objects.create_user(
            username='otro',
            password='clave-segura-456',
            first_name='Otro',
            last_name='Usuario',
            email='otro@example.com',
        )

    def test_requiere_autenticacion(self):
        response = self.client.get(reverse('api_perfil'))

        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()['success'])

    def test_devuelve_los_datos_del_usuario_autenticado(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('api_perfil'))
        perfil = response.json()['perfil']

        self.assertEqual(response.status_code, 200)
        self.assertEqual(perfil['username'], self.user.username)
        self.assertEqual(perfil['nombre'], 'Ana García')
        self.assertEqual(perfil['email'], self.user.email)

    def test_no_permite_consultar_los_datos_de_otro_usuario(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse('api_perfil'),
            {'user_id': self.other_user.id, 'username': self.other_user.username},
        )
        contenido = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['perfil']['username'], self.user.username)
        self.assertNotIn(self.other_user.email, contenido)
        self.assertNotIn(self.other_user.first_name, contenido)


class CrearReservaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='ana',
            password='clave-segura-123',
            email='ana@example.com',
        )
        self.other_user = User.objects.create_user(
            username='otro',
            password='clave-segura-456',
        )
        self.resource = Resource.objects.create(name='Pista de prueba')

    def test_requiere_autenticacion(self):
        response = self.client.post(
            reverse('api_crear_reserva'),
            data=json.dumps({
                'resource_id': self.resource.id,
                'start_time': '2026-11-02T10:00:00+00:00',
                'end_time': '2026-11-02T11:00:00+00:00',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Booking.objects.count(), 0)

    def test_registra_la_reserva_a_nombre_del_usuario_autenticado(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('api_crear_reserva'),
            data=json.dumps({
                'resource_id': self.resource.id,
                'start_time': '2026-11-02T10:00:00+00:00',
                'end_time': '2026-11-02T11:00:00+00:00',
                'user_id': self.other_user.id,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
        self.assertEqual(Booking.objects.count(), 1)

        reserva = Booking.objects.get()
        self.assertEqual(reserva.user, self.user)
        self.assertEqual(reserva.resource, self.resource)
