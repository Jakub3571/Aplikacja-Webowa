# -*- coding: utf-8 -*-
"""Wydajność kalendarza_dane: cykliczne powtórzenia zakresu, powtórzenia tygodniowe,
usuwanie zakresu i cofanie powtórzeń robią stałą liczbę zapytań (bulk) zamiast pętli
get_or_create/delete po każdym slocie — duży zakres 6:00–19:00 zapisuje się od razu."""
from datetime import date, datetime, time, timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from konta.models import Dostepnosc, Lekcja, Nauczyciel, Pracownik, PrzerwaDnia, Uczen


class KalendarzCyklicznoscTests(TestCase):
    def setUp(self):
        self.nauczyciel = Nauczyciel.objects.create(imie='Jan', nazwisko='Kowalski', email='jan@k.pl')
        self.user = Pracownik.objects.create_superuser('szef', 's@f.pl', 'Sup3rHaslo!')
        self.client.force_login(self.user)
        self.dzien = date(2026, 10, 5)  # poniedziałek
        self.adres = f'/nauczyciele/{self.nauczyciel.id}/kalendarz/dane/'

    def _zaznacz(self, od='06:00', do='19:00', cyklicznosc='1'):
        return self.client.post(self.adres, {
            'dzien': self.dzien.isoformat(),
            'akcja': 'zaznacz_zakres',
            'od': od, 'do': do,
            'cyklicznosc': cyklicznosc,
        })

    def test_zakres_cykliczny_tworzy_powtorzenia_stala_liczba_zapytan(self):
        with CaptureQueriesContext(connection) as ctx:
            r = self._zaznacz()
        self.assertEqual(r.status_code, 200)
        # stara pętla get_or_create robiła tu ~2900 zapytań; teraz ma być kilkadziesiąt
        self.assertLess(len(ctx), 60)
        # 26 slotów (6:00–19:00) w dniu źródłowym i w każdym przyszłym tym dniu tygodnia
        self.assertEqual(
            Dostepnosc.objects.filter(nauczyciel=self.nauczyciel, data_od=self.dzien).count(), 26)
        przyszly = self.dzien + timedelta(days=7)
        self.assertEqual(
            Dostepnosc.objects.filter(nauczyciel=self.nauczyciel, data_od=przyszly).count(), 26)
        liczba = Dostepnosc.objects.filter(nauczyciel=self.nauczyciel).count()
        self.assertEqual(liczba, 26 + 26 * 52)
        # drugie zapisanie tego samego zakresu nie duplikuje terminów
        r = self._zaznacz()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Dostepnosc.objects.filter(nauczyciel=self.nauczyciel).count(), liczba)

    def test_zakres_cykliczny_co_2_tygodnie(self):
        r = self._zaznacz(od='10:00', do='12:00', cyklicznosc='2')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            Dostepnosc.objects.filter(
                nauczyciel=self.nauczyciel, data_od=self.dzien + timedelta(days=14),
            ).count(), 4)
        self.assertEqual(
            Dostepnosc.objects.filter(
                nauczyciel=self.nauczyciel, data_od=self.dzien + timedelta(days=7),
            ).count(), 0)

    def test_powtorz_tygodniowo_masowo_i_z_przerwa(self):
        for h in range(10, 12):
            Dostepnosc.objects.create(
                nauczyciel=self.nauczyciel, data_od=self.dzien, data_do=self.dzien,
                godzina_od=time(h, 0), godzina_do=time(h, 30),
            )
        PrzerwaDnia.objects.create(nauczyciel=self.nauczyciel, data=self.dzien, minuty=10)
        with CaptureQueriesContext(connection) as ctx:
            r = self.client.post(self.adres, {
                'dzien': self.dzien.isoformat(), 'akcja': 'powtorz_tygodniowo',
            })
        self.assertEqual(r.status_code, 200)
        self.assertLess(len(ctx), 60)
        przyszly = self.dzien + timedelta(days=7)
        self.assertEqual(
            Dostepnosc.objects.filter(nauczyciel=self.nauczyciel, data_od=przyszly).count(), 2)
        self.assertTrue(PrzerwaDnia.objects.filter(
            nauczyciel=self.nauczyciel, data=przyszly, minuty=10).exists())
        # ponowne powtórzenie nie duplikuje
        liczba = Dostepnosc.objects.count()
        self.client.post(self.adres, {'dzien': self.dzien.isoformat(), 'akcja': 'powtorz_tygodniowo'})
        self.assertEqual(Dostepnosc.objects.count(), liczba)

    def test_usun_zakres_zostawia_sloty_z_lekcjami(self):
        self._zaznacz(od='10:00', do='12:00', cyklicznosc='1')
        uczen = Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime.combine(self.dzien, time(10, 0))),
            przedmiot='matematyka', poziom='podstawowa',
            imie_ucznia='Kasia', nazwisko_rodzica='Nowak', rodzic='Rodzic Nowak',
            telefon_glowny='600100200', email_glowny='e@f.pl',
            korepetytor='Jan Kowalski', kto_umowil='Jan Kowalski',
        )
        przyszly = self.dzien + timedelta(days=7)
        Lekcja.objects.create(
            uczen=uczen,
            data=timezone.make_aware(datetime.combine(przyszly, time(10, 0))),
        )
        with CaptureQueriesContext(connection) as ctx:
            r = self.client.post(self.adres, {
                'dzien': self.dzien.isoformat(), 'akcja': 'usun_zakres',
                'od': '10:00', 'do': '12:00',
            })
        self.assertEqual(r.status_code, 200)
        self.assertLess(len(ctx), 60)
        self.assertIn('UWAGA', r.json()['komunikat'])
        # slot 10:00 w przyszłym tygodniu ma lekcję → zostaje; pozostałe 3 sloty tego dnia usunięte
        self.assertEqual(
            Dostepnosc.objects.filter(nauczyciel=self.nauczyciel, data_od=przyszly).count(), 1)
        self.assertTrue(Dostepnosc.objects.filter(
            nauczyciel=self.nauczyciel, data_od=przyszly, godzina_od=time(10, 0)).exists())
        # dzień źródłowy usunięty w tym zakresie
        self.assertEqual(
            Dostepnosc.objects.filter(nauczyciel=self.nauczyciel, data_od=self.dzien).count(), 0)

    def test_cofnij_powtorzenia_masowo(self):
        self._zaznacz(od='10:00', do='11:00', cyklicznosc='1')
        self.assertGreater(Dostepnosc.objects.count(), 4)
        with CaptureQueriesContext(connection) as ctx:
            r = self.client.post(self.adres, {
                'dzien': self.dzien.isoformat(), 'akcja': 'cofnij_powtorzenia',
            })
        self.assertEqual(r.status_code, 200)
        self.assertLess(len(ctx), 60)
        # zostają tylko 2 sloty dnia źródłowego
        self.assertEqual(Dostepnosc.objects.filter(nauczyciel=self.nauczyciel).count(), 2)