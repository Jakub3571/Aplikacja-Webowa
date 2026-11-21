# -*- coding: utf-8 -*-
"""Zabezpieczenia publicznego /api/contact: rate-limit 5/godz. na IP, honeypot,
limit rozmiaru body, walidacja pól. Boty dostają udawany sukces {'ok': true}."""
import json

from django.core.cache import cache
from django.core import mail
from django.test import TestCase


class ApiContactTests(TestCase):
    def setUp(self):
        cache.clear()

    def _post(self, **nadpisz):
        dane = {
            'name': 'Anna Kowal',
            'subject': 'matematyka',
            'level': 'średnia rozszerzenie',
            'goal': 'matura 2027',
            'phone': '600 100 200',
            'email': 'rodzic@example.com',
        }
        dane.update(nadpisz)
        return self.client.post(
            '/api/contact',
            data=json.dumps(dane),
            content_type='application/json',
        )

    def test_prawidlowe_zgloszenie_wysyla_maila(self):
        r = self._post()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {'ok': True})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Anna Kowal', mail.outbox[0].body)

    def test_honeypot_nie_wysyla_maila(self):
        r = self._post(website='http://spam.example')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {'ok': True})  # udajemy sukces
        self.assertEqual(len(mail.outbox), 0)

    def test_rate_limit_5_na_godzine_na_ip(self):
        for i in range(5):
            r = self._post()
            self.assertEqual(r.json(), {'ok': True})
        self.assertEqual(len(mail.outbox), 5)
        # 6. zgłoszenie: nadal ok:true, ale maila już nie ma
        r = self._post()
        self.assertEqual(r.json(), {'ok': True})
        self.assertEqual(len(mail.outbox), 5)

    def test_inny_ip_ma_osobny_limit(self):
        for i in range(5):
            self._post()
        self.assertEqual(len(mail.outbox), 5)
        r = self.client.post(
            '/api/contact',
            data=json.dumps({'name': 'Inny', 'phone': '600111222'}),
            content_type='application/json',
            REMOTE_ADDR='10.9.8.7',
        )
        self.assertEqual(r.json(), {'ok': True})
        self.assertEqual(len(mail.outbox), 6)  # nowe IP = nowy licznik

    def test_za_duze_body_413(self):
        r = self.client.post(
            '/api/contact',
            data='x' * 20_000,
            content_type='application/json',
        )
        self.assertEqual(r.status_code, 413)
        self.assertEqual(len(mail.outbox), 0)

    def test_walidacja_pol(self):
        self.assertEqual(self._post(name='A').status_code, 400)          # za krótkie imię
        self.assertEqual(self._post(phone='abc!!!').status_code, 400)     # zły telefon
        self.assertEqual(self._post(email='nie-email').status_code, 400)  # zły e-mail
        self.assertEqual(len(mail.outbox), 0)

    def test_get_odrzucony(self):
        r = self.client.get('/api/contact')
        self.assertEqual(r.status_code, 405)

    def test_nowe_pola_w_mailu_sa_przyciete(self):
        self._post(goal='x' * 5000)
        self.assertEqual(len(mail.outbox), 1)
        self.assertLess(len(mail.outbox[0].body), 2000)