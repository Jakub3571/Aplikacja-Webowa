# -*- coding: utf-8 -*-
"""Etap 2: panel settera „Umówieni uczniowie” — etapy jako sekcje, checkboxy,
przyciski „Lekcja się odbyła” / „Umów kolejną lekcję” / „Umów ponownie”,
wybór miesiąca, czerwony wiersz po 24 h, usunięcie cyklicznych z widoku."""
from datetime import date, datetime, time, timedelta

from django.test import TestCase
from django.utils import timezone

from konta.models import Dostepnosc, Lekcja, Nauczyciel, OcenaLekcji, Pracownik, Uczen
from konta.views import _etap_ucznia


class PanelSetteraTests(TestCase):
    def setUp(self):
        self.admin = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.setter = Pracownik.objects.create_user('setter', 's@f.pl', 'Sup3rHaslo!')
        self.setter2 = Pracownik.objects.create_user('setter2', 's2@f.pl', 'Sup3rHaslo!')
        self.jan = Nauczyciel.objects.create(imie='Jan', nazwisko='Kowalski', email='jan@n.pl', przerwa=10)
        d = date.today() - timedelta(days=10)
        koniec = date.today() + timedelta(days=90)
        while d <= koniec:
            Dostepnosc.objects.create(
                nauczyciel=self.jan, data_od=d, data_do=d,
                godzina_od=time(16, 0), godzina_do=time(20, 0),
            )
            d += timedelta(days=1)
        self.uczen = Uczen.objects.create(
            pierwsza_lekcja=timezone.now() + timedelta(days=2),
            przedmiot='matematyka', poziom='srednia podstawa', szkola='SP 1',
            imie_ucznia='Kasia', nazwisko_rodzica='Nowak', rodzic='Ewa Nowak',
            telefon_glowny='600100200', email_glowny='ewa@f.pl',
            korepetytor='Jan Kowalski', kto_umowil='setter', umowil=self.setter,
        )

    def _lekcja(self, uczen, kiedy):
        return Lekcja.objects.create(uczen=uczen, data=kiedy)

    def test_naglowek_i_sekcja_nowi(self):
        self.client.force_login(self.setter)
        r = self.client.get('/lekcje/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Umówieni uczniowie')
        self.assertContains(r, 'Nowi uczniowie')
        self.assertContains(r, 'Kasia')
        self.assertNotContains(r, 'Zajęcia cykliczne')

    def test_etapy_w_sekcjach(self):
        self.client.force_login(self.setter)
        lekcja = self._lekcja(self.uczen, timezone.now() - timedelta(days=2))
        r = self.client.get('/lekcje/')
        self.assertContains(r, 'Do potwierdzenia')
        self.assertEqual(_etap_ucznia(self.uczen), 'do_potwierdzenia')
        OcenaLekcji.objects.create(lekcja=lekcja, odbyla_sie=True, temat='Ułamki', skupienie=5, zadanie_domowe='-')
        self.assertEqual(_etap_ucznia(self.uczen), 'odbyla')
        r = self.client.get('/lekcje/')
        self.assertContains(r, 'Odbyła się')
        self.assertContains(r, 'Ułamki')
        ocena = OcenaLekcji.objects.get(lekcja=lekcja)
        ocena.odbyla_sie = False
        ocena.powod_nieodbycia = 'odwolal_rodzic'
        ocena.save()
        self.assertEqual(_etap_ucznia(self.uczen), 'nie_odbyla')
        r = self.client.get('/lekcje/')
        self.assertContains(r, 'Nie odbyła się')
        self.assertContains(r, 'odwołał rodzic')

    def test_przycisk_settera_lekcja_sie_odbyla(self):
        self.client.force_login(self.setter)
        lekcja = self._lekcja(self.uczen, timezone.now() - timedelta(hours=30))
        r = self.client.post('/lekcje/', {'akcja': 'setter_odbyla', 'lekcja_id': lekcja.id}, follow=True)
        self.assertEqual(r.status_code, 200)
        ocena = OcenaLekcji.objects.get(lekcja=lekcja)
        self.assertTrue(ocena.odbyla_sie)
        self.assertEqual(ocena.wypelnil, self.setter)
        self.assertEqual(_etap_ucznia(self.uczen), 'odbyla')

    def test_checkbox_toggle(self):
        self.client.force_login(self.setter)
        lekcja = self._lekcja(self.uczen, timezone.now() - timedelta(hours=2))
        ocena = OcenaLekcji.objects.create(lekcja=lekcja, odbyla_sie=True, temat='x', skupienie=4, zadanie_domowe='-')
        r = self.client.post('/lekcje/', {
            'akcja': 'checkbox', 'ocena_id': ocena.id, 'pole': 'oplacona', 'wartosc': '1',
        })
        self.assertEqual(r.status_code, 302)
        ocena.refresh_from_db()
        self.assertTrue(ocena.oplacona)
        self.client.post('/lekcje/', {
            'akcja': 'checkbox', 'ocena_id': ocena.id, 'pole': 'oplacona', 'wartosc': '0',
        })
        ocena.refresh_from_db()
        self.assertFalse(ocena.oplacona)

    def test_umow_kolejna_lekcje_i_autocheckbox(self):
        self.client.force_login(self.setter)
        lekcja = self._lekcja(self.uczen, timezone.now() - timedelta(hours=2))
        ocena = OcenaLekcji.objects.create(lekcja=lekcja, odbyla_sie=True, temat='x', skupienie=5, zadanie_domowe='-')
        dzien = date.today() + timedelta(days=3)
        r = self.client.post('/lekcje/', {
            'akcja': 'umow_kolejna', 'uczen_id': self.uczen.id,
            'data': dzien.isoformat(), 'godzina': '16:00', 'korepetytor': 'Jan Kowalski',
        }, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(Lekcja.objects.filter(uczen=self.uczen, data__date=dzien, data__hour=16).exists())
        ocena.refresh_from_db()
        self.assertTrue(ocena.kolejna_umowiona)
        obcy = Uczen.objects.create(
            pierwsza_lekcja=timezone.now() + timedelta(days=1),
            przedmiot='fizyka', poziom='podstawowka', szkola='SP 2',
            imie_ucznia='Ola', nazwisko_rodzica='Wis', rodzic='Ala Wis',
            telefon_glowny='600200300', email_glowny='ola@f.pl',
            korepetytor='Jan Kowalski', kto_umowil='setter', umowil=self.setter,
        )
        self._lekcja(obcy, timezone.make_aware(datetime.combine(dzien, time(17, 10))))
        liczba_przed = Lekcja.objects.filter(uczen=self.uczen).count()
        r = self.client.post('/lekcje/', {
            'akcja': 'umow_kolejna', 'uczen_id': self.uczen.id,
            'data': dzien.isoformat(), 'godzina': '17:10', 'korepetytor': 'Jan Kowalski',
        }, follow=True)
        self.assertContains(r, 'nie ma wolnej godziny')
        self.assertEqual(Lekcja.objects.filter(uczen=self.uczen).count(), liczba_przed)

    def test_umow_ponownie_ten_sam_uczen(self):
        self.client.force_login(self.setter)
        lekcja = self._lekcja(self.uczen, timezone.now() - timedelta(days=1))
        OcenaLekcji.objects.create(lekcja=lekcja, odbyla_sie=False, powod_nieodbycia='uczen_nie_przyszedl')
        self.assertEqual(_etap_ucznia(self.uczen), 'nie_odbyla')
        liczba_uczniow = Uczen.objects.count()
        dzien = date.today() + timedelta(days=5)
        r = self.client.post('/lekcje/', {
            'akcja': 'umow_ponownie', 'lekcja_id': lekcja.id,
            'data': dzien.isoformat(), 'godzina': '16:00', 'korepetytor': 'Jan Kowalski',
        }, follow=True)
        self.assertEqual(r.status_code, 200)
        lekcja.refresh_from_db()
        self.assertTrue(lekcja.odwolana)
        self.assertTrue(Lekcja.objects.filter(uczen=self.uczen, odwolana=False, data__date=dzien).exists())
        self.assertEqual(Uczen.objects.count(), liczba_uczniow)
        self.assertEqual(_etap_ucznia(self.uczen), 'nowy')

    def test_wybor_miesiaca(self):
        self.client.force_login(self.setter)
        stary = Uczen.objects.create(
            pierwsza_lekcja=timezone.now() - timedelta(days=60),
            przedmiot='fizyka', poziom='podstawowka', szkola='SP 2',
            imie_ucznia='Ola', nazwisko_rodzica='Wis', rodzic='Ala Wis',
            telefon_glowny='600200300', email_glowny='ola@f.pl',
            korepetytor='Jan Kowalski', kto_umowil='setter', umowil=self.setter,
        )
        lekcja_stara = self._lekcja(stary, timezone.now() - timedelta(days=60))
        OcenaLekcji.objects.create(lekcja=lekcja_stara, odbyla_sie=True, temat='stara', skupienie=5, zadanie_domowe='-')
        dzis = date.today()
        r = self.client.get(f'/lekcje/?rok={dzis.year}&miesiac={dzis.month}')
        self.assertContains(r, 'Kasia')
        self.assertNotContains(r, 'Ola')
        stary_rok = (dzis.year, dzis.month - 2) if dzis.month > 2 else (dzis.year - 1, dzis.month + 10)
        r = self.client.get(f'/lekcje/?rok={stary_rok[0]}&miesiac={stary_rok[1]}')
        self.assertContains(r, 'Ola')
        self.assertNotContains(r, 'Kasia')
        r = self.client.get('/lekcje/')
        self.assertContains(r, 'rok=')
        self.assertContains(r, 'miesiac=')

    def test_czerwony_wiersz_po_24h(self):
        self.client.force_login(self.setter)
        lekcja = self._lekcja(self.uczen, timezone.now() - timedelta(hours=30))
        OcenaLekcji.objects.create(lekcja=lekcja, odbyla_sie=True, temat='x', skupienie=5, zadanie_domowe='-')
        r = self.client.get('/lekcje/')
        self.assertContains(r, 'wiersz-czerwony')
        ocena = OcenaLekcji.objects.get(lekcja=lekcja)
        for pole in ('wrazenia_zebrane', 'oplacona', 'kolejna_umowiona', 'na_fanpage'):
            self.client.post('/lekcje/', {'akcja': 'checkbox', 'ocena_id': ocena.id, 'pole': pole, 'wartosc': '1'})
        r = self.client.get('/lekcje/')
        self.assertNotContains(r, 'wiersz-czerwony')

    def test_bez_uprawnien_do_cudzego_ucznia(self):
        self.client.force_login(self.setter)
        lekcja = self._lekcja(self.uczen, timezone.now() - timedelta(hours=2))
        ocena = OcenaLekcji.objects.create(lekcja=lekcja, odbyla_sie=True, temat='x', skupienie=5, zadanie_domowe='-')
        self.client.force_login(self.setter2)
        r = self.client.get('/lekcje/')
        self.assertNotContains(r, 'Kasia')
        r = self.client.post('/lekcje/', {'akcja': 'checkbox', 'ocena_id': ocena.id, 'pole': 'oplacona', 'wartosc': '1'})
        self.assertEqual(r.status_code, 403)
        ocena.refresh_from_db()
        self.assertFalse(ocena.oplacona)

    def test_szef_widzi_wszystkich(self):
        self.client.force_login(self.admin)
        r = self.client.get('/lekcje/')
        self.assertContains(r, 'Kasia')

class WyszukiwanieSekcjiTests(TestCase):
    """Jedno wyszukiwanie na sekcję (Nowi / Do potwierdzenia / Odbyła się / Nie odbyła się),
    bez zbiorczego pola na górze; zakładka w pasku nazywa się krótko „Umówieni”."""

    def setUp(self):
        self.admin = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.setter = Pracownik.objects.create_user('setter', 's@f.pl', 'Sup3rHaslo!')
        self.uczen = Uczen.objects.create(
            pierwsza_lekcja=timezone.now() + timedelta(days=2),
            przedmiot='matematyka', poziom='podstawówka', szkola='SP 1',
            imie_ucznia='Kasia', nazwisko_rodzica='Nowak', rodzic='Ewa Nowak',
            telefon_glowny='600100200', email_glowny='ewa@f.pl',
            korepetytor='Jan Kowalski', kto_umowil='setter', umowil=self.setter,
        )

    def test_cztery_wyszukiwania_jedno_na_sekcje(self):
        self.client.force_login(self.admin)
        r = self.client.get('/lekcje/')
        self.assertEqual(r.status_code, 200)
        # dokładnie 4 pola wyszukiwania — po jednym nad każdą tabelą sekcji
        self.assertEqual(r.content.count(b'class="szukaj-tabela"'), 4)
        for tabela_id in ('tabela-nowi', 'tabela-do-potwierdzenia', 'tabela-odbyla', 'tabela-nie-odbyla'):
            self.assertContains(r, tabela_id)
        # zbiorcze wyszukiwanie na górze znika
        self.assertNotContains(r, 'data-tabela="tabela-umowieni"')
        # tabele mają wyłączone auto-doklejanie wyszukiwarki z base.html
        self.assertEqual(r.content.count(b'data-szukanie="wylaczone"'), 4)

    def test_zakladka_nazywa_sie_krotko_umowieni(self):
        self.client.force_login(self.admin)
        r = self.client.get('/uczniowie/')
        self.assertContains(r, '>Umówieni</a>')
        self.assertNotContains(r, 'Umówieni</a>')