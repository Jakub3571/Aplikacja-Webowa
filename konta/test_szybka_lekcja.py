# -*- coding: utf-8 -*-
"""Szybkie dodawanie lekcji („+”) w zakładce Lekcje dla konta korepetytora:
wybór własnego ucznia + data + wolna godzina → lekcja zapisana, godzina zablokowana."""
from datetime import date, datetime, time, timedelta

from django.test import TestCase
from django.utils import timezone

from konta.models import Dostepnosc, Lekcja, Nauczyciel, Pracownik, Uczen


class SzybkaLekcjaTests(TestCase):
    def setUp(self):
        self.nauczyciel = Nauczyciel.objects.create(imie='Jan', nazwisko='Kowalski', email='jan@k.pl')
        self.user = Pracownik.objects.create_user('kowal', 'kowal@f.pl', 'Sup3rHaslo!')
        self.user.nauczyciel = self.nauczyciel
        self.user.save()
        self.client.force_login(self.user)
        self.dzien = date.today() + timedelta(days=1)
        # korepetytor pracuje w tym dniu 10:00–14:00
        Dostepnosc.objects.create(
            nauczyciel=self.nauczyciel,
            data_od=self.dzien, data_do=self.dzien,
            godzina_od=time(10, 0), godzina_do=time(14, 0),
        )
        self.uczen = self._uczen('Kasia', 'Nowak', 'Jan Kowalski')
        self.uczen2 = self._uczen('Marek', 'Lis', 'Jan Kowalski')

    def _uczen(self, imie, nazwisko, korepetytor):
        return Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime.combine(self.dzien, time(10, 0))),
            przedmiot='matematyka', poziom='rozszerzony',
            imie_ucznia=imie, nazwisko_rodzica=nazwisko, rodzic='Rodzic ' + nazwisko,
            telefon_glowny='600100200', email_glowny='e@f.pl',
            korepetytor=korepetytor, kto_umowil=korepetytor,
        )

    def _post(self, uczen_id, dzien, godzina):
        return self.client.post('/lekcje/', {
            'szybka_lekcja': '1',
            'uczen_id': str(uczen_id),
            'data': dzien.isoformat(),
            'godzina': godzina,
        })

    def test_korepetytor_dodaje_lekcje_i_blokuje_godzine(self):
        r = self._post(self.uczen.id, self.dzien, '11:00')
        self.assertEqual(r.status_code, 302)
        start = timezone.make_aware(datetime.combine(self.dzien, time(11, 0)))
        self.assertTrue(Lekcja.objects.filter(uczen=self.uczen, data=start).exists())
        self.assertTrue(Dostepnosc.objects.filter(
            nauczyciel=self.nauczyciel, data_od=self.dzien, godzina_od=time(11, 0),
        ).exists())

    def test_nie_mozna_dodac_cudzego_ucznia(self):
        Nauczyciel.objects.create(imie='Ola', nazwisko='Zet', email='ola@k.pl')
        obcy = self._uczen('Olek', 'Kot', 'Ola Zet')
        r = self._post(obcy.id, self.dzien, '11:00')
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Lekcja.objects.filter(uczen=obcy, data__date=self.dzien).exists())

    def test_kolizja_z_inna_lekcja_odrzucona(self):
        Lekcja.objects.create(
            uczen=self.uczen2,
            data=timezone.make_aware(datetime.combine(self.dzien, time(11, 0))),
        )
        r = self._post(self.uczen.id, self.dzien, '11:00')
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Lekcja.objects.filter(uczen=self.uczen, data__date=self.dzien).exists())
        # ale wolna godzina dalej od kolizji przechodzi (13:00, przerwa 5 min po 11:00)
        r = self._post(self.uczen.id, self.dzien, '13:00')
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Lekcja.objects.filter(
            uczen=self.uczen, data=timezone.make_aware(datetime.combine(self.dzien, time(13, 0))),
        ).exists())

    def test_godzina_poza_grafikiem_odrzucona(self):
        r = self._post(self.uczen.id, self.dzien, '15:00')
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Lekcja.objects.filter(uczen=self.uczen, data__date=self.dzien, data__hour=15).exists())

    def test_zwykly_pracownik_bez_karty_nauczyciela_ma_403(self):
        u2 = Pracownik.objects.create_user('zwykly', 'z@f.pl', 'Sup3rHaslo!')
        self.client.force_login(u2)
        r = self._post(self.uczen.id, self.dzien, '11:00')
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Lekcja.objects.filter(data__date=self.dzien, data__hour=11).exists())

    def test_strona_lekcje_pokazuje_plus_dla_nauczyciela(self):
        r = self.client.get('/lekcje/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Dodaj lekcję')
        self.assertContains(r, 'szybka_lekcja')