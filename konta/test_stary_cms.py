# -*- coding: utf-8 -*-
"""Stary CMS (/cms-strony/) jest wyłączony: przekierowuje do /cms/ i nigdy nie zapisuje pliku."""
from django.test import TestCase

from konta.models import Pracownik


class StaryCmsWylaczonyTests(TestCase):
    def setUp(self):
        self.user = Pracownik.objects.create_user('admin', 'admin@firma.pl', 'Sup3rHaslo!')
        self.user.is_superuser = True
        self.user.save()
        self.client.post('/', {'username': 'admin', 'password': 'Sup3rHaslo!'})

    def test_get_przekierowuje_do_nowego_cms(self):
        r = self.client.get('/cms-strony/')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, '/cms/')

    def test_post_tez_przekierowuje_i_nie_zapisuje(self):
        r = self.client.post('/cms-strony/', {'copy_home-headline': 'Nowy nagłówek', 'stat_retention': '1'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, '/cms/')

    def test_bez_logowania_idzie_na_logowanie(self):
        self.client.logout()
        r = self.client.get('/cms-strony/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('next=', r.url)