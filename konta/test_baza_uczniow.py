# -*- coding: utf-8 -*-
"""Etap 3: baza uczniów (widok szefa /baza-uczniow/) — tabela wszystkich aktywnych
uczniów, sortowanie, szukanie, grupowanie wg „kto umówił”, statystyki miesięczne
per setter, żółte podświetlenie podejrzanych telefonów/e-maili oraz opcjonalny
telefon/e-mail główny w formularzu ucznia ("-")."""
from datetime import date, datetime, time, timedelta

from django.test import TestCase
from django.utils import timezone

from konta.models import Dostepnosc, Lekcja, Nauczyciel, Pracownik, PrzedmiotNauczyciela, Uczen
from konta.test_logowanie import TestCaseZamrozonaData
from konta.views import _podejrzane_kontakt


class BazaUczniowTests(TestCase):
    def setUp(self):
        self.admin = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.setter = Pracownik.objects.create_user('setter', 's@f.pl', 'Sup3rHaslo!')
        self.setter2 = Pracownik.objects.create_user('setter2', 's2@f.pl', 'Sup3rHaslo!')
        self.jan = Nauczyciel.objects.create(imie='Jan', nazwisko='Kowalski', email='jan@n.pl', przerwa=10)
        self.kasia = self._uczen(
            imie='Kasia', nazwisko='Nowak', telefon='600100200', email='ewa@f.pl',
            umowil=self.setter, kto='setter',
        )
        self.ola = self._uczen(
            imie='Ola', nazwisko='Lis', telefon='-', email='-',
            umowil=self.setter2, kto='setter2',
        )
        self.archiwum = self._uczen(
            imie='Tomek', nazwisko='Kot', telefon='600000000', email='t@f.pl',
            umowil=self.setter, kto='setter', zakonczona=True,
        )

    def _uczen(self, imie, nazwisko, telefon, email, umowil, kto, zakonczona=False):
        return Uczen.objects.create(
            pierwsza_lekcja=timezone.now() + timedelta(days=3),
            przedmiot='matematyka', poziom='podstawówka', szkola='SP 1',
            imie_ucznia=imie, nazwisko_rodzica=nazwisko, rodzic='Rodzic ' + nazwisko,
            telefon_glowny=telefon, email_glowny=email,
            korepetytor='Jan Kowalski', kto_umowil=kto, umowil=umowil,
            zakonczona=zakonczona,
        )

    def test_szef_widzi_baze_z_kolumnami(self):
        self.client.force_login(self.admin)
        r = self.client.get('/baza-uczniow/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Baza uczniów')
        self.assertContains(r, 'Kasia Nowak')
        self.assertContains(r, 'Ola Lis')
        # archiwum siedzi osobno — nie ma go w bazie aktywnych
        self.assertNotContains(r, 'Tomek Kot')
        for naglowek in ('Uczeń', 'Rodzic', 'Telefon', 'E-mail', 'Korepetytor',
                         'Kto umówił', 'Przedmiot', 'Poziom', 'Szkoła', 'Data 1. lekcji'):
            self.assertContains(r, naglowek)

    def test_pracownik_i_nauczyciel_nie_maja_dostepu(self):
        self.client.force_login(self.setter)
        r = self.client.get('/baza-uczniow/')
        self.assertEqual(r.status_code, 403)
        konto_nauczyciela = Pracownik.objects.create_user('nauczyciel1', 'n@f.pl', 'Sup3rHaslo!')
        konto_nauczyciela.nauczyciel = self.jan
        konto_nauczyciela.save()
        self.client.force_login(konto_nauczyciela)
        r = self.client.get('/baza-uczniow/')
        self.assertEqual(r.status_code, 403)

    def test_szukanie_filtruje_uczniow(self):
        self.client.force_login(self.admin)
        r = self.client.get('/baza-uczniow/?szukaj=kasia')
        self.assertContains(r, 'Kasia Nowak')
        self.assertNotContains(r, 'Ola Lis')
        r = self.client.get('/baza-uczniow/?szukaj=setter2')
        self.assertContains(r, 'Ola Lis')
        self.assertNotContains(r, 'Kasia Nowak')

    def test_sortowanie_po_nazwisku(self):
        self.client.force_login(self.admin)
        r = self.client.get('/baza-uczniow/?sort=uczen&kierunek=asc')
        tresc = r.content.decode('utf-8')
        self.assertLess(tresc.find('Ola Lis'), tresc.find('Kasia Nowak'))
        r = self.client.get('/baza-uczniow/?sort=uczen&kierunek=desc')
        tresc = r.content.decode('utf-8')
        self.assertLess(tresc.find('Kasia Nowak'), tresc.find('Ola Lis'))

    def test_grupowanie_wg_umowil(self):
        self.client.force_login(self.admin)
        r = self.client.get('/baza-uczniow/?grupuj=umowil')
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(r.context['grupy'])
        nazwy = [g[0] for g in r.context['grupy']]
        self.assertEqual(nazwy, ['setter', 'setter2'])
        self.assertContains(r, 'setter2')
        self.assertContains(r, 'Ola Lis')

    def test_zoltko_podejrzane_kontakty(self):
        self.client.force_login(self.admin)
        r = self.client.get('/baza-uczniow/')
        # Ola ma "-" w telefonie i mailu — dokładnie 2 żółte komórki
        self.assertEqual(r.content.count(b'komorka-podejrzana'), 2)
        self.assertContains(r, 'komorka-podejrzana')

    def test_statystyki_miesieczne_per_setter(self):
        dzis = date.today()
        Uczen.objects.filter(pk=self.kasia.pk).update(dodano=timezone.now())
        Uczen.objects.filter(pk=self.ola.pk).update(
            dodano=timezone.make_aware(datetime(dzis.year, dzis.month, 5, 10, 0))
        )
        Uczen.objects.filter(pk=self.archiwum.pk).update(
            dodano=timezone.make_aware(datetime(dzis.year - 1, 1, 5, 10, 0))
        )
        Lekcja.objects.create(uczen=self.kasia, data=timezone.now() - timedelta(days=1))
        Lekcja.objects.create(uczen=self.kasia, data=timezone.now() - timedelta(days=2), odwolana=True)
        self.client.force_login(self.admin)
        r = self.client.get('/baza-uczniow/')
        stats = {s['nazwa']: s for s in r.context['statystyki']}
        self.assertEqual(stats['setter']['umowieni'], 1)
        self.assertEqual(stats['setter']['odbyte'], 1)
        self.assertEqual(stats['setter2']['umowieni'], 1)
        self.assertEqual(stats['setter2']['odbyte'], 0)

    def test_nawigacja_miesiaca_w_statystykach(self):
        self.client.force_login(self.admin)
        r = self.client.get('/baza-uczniow/?rok=2026&miesiac=10')
        self.assertContains(r, 'październik 2026')
        self.assertContains(r, 'rok=2026&amp;miesiac=9')
        self.assertContains(r, 'rok=2026&amp;miesiac=11')

    def test_podejrzane_kontakty_jednostkowo(self):
        self.assertTrue(_podejrzane_kontakt(''))
        self.assertTrue(_podejrzane_kontakt('-'))
        self.assertTrue(_podejrzane_kontakt('0'))
        self.assertTrue(_podejrzane_kontakt('@'))
        self.assertTrue(_podejrzane_kontakt('600'))
        self.assertFalse(_podejrzane_kontakt('600100200'))
        self.assertTrue(_podejrzane_kontakt('abc', czy_email=True))
        self.assertTrue(_podejrzane_kontakt('ewa@f', czy_email=True))
        self.assertTrue(_podejrzane_kontakt('ewa@', czy_email=True))
        self.assertFalse(_podejrzane_kontakt('ewa@f.pl', czy_email=True))


class FormularzOpcjonalnyKontaktTests(TestCaseZamrozonaData):
    """Telefon/e-mail główny w formularzu ucznia są opcjonalne; „-” = brak danych."""

    def setUp(self):
        self.user = Pracownik.objects.create_superuser('test', 't@f.pl', 'Sup3rHaslo!')
        self.nauczyciel = Nauczyciel.objects.create(
            imie='Jan', nazwisko='Kowalski', email='jan@k.pl',
        )
        PrzedmiotNauczyciela.objects.create(
            nauczyciel=self.nauczyciel, przedmiot='matematyka', poziom='srednia rozszerzenie',
        )
        Dostepnosc.objects.create(
            nauczyciel=self.nauczyciel,
            data_od=date(2026, 10, 5), data_do=date(2026, 10, 5),
            godzina_od='16:00', godzina_do='19:00',
        )
        self.dane = {
            'data_pierwszej_lekcji': '2026-10-05',
            'godzina_pierwszej_lekcji': '16:00',
            'przedmiot': 'matematyka',
            'poziom': 'srednia rozszerzenie',
            'imie_ucznia': 'Kasia',
            'nazwisko_rodzica': 'Nowak',
            'rodzic': 'Ewa Nowak',
            'telefon_dodatkowy': '',
            'email_dodatkowy': '',
            'korepetytor': 'Jan Kowalski',
            'notatka_od_rodzica': '',
        }

    def test_bez_telefonu_i_emaila_jest_ok(self):
        self.client.force_login(self.user)
        r = self.client.post('/uczniowie/', self.dane, follow=True)
        self.assertContains(r, 'został dodany')
        uczen = Uczen.objects.get(imie_ucznia='Kasia')
        self.assertEqual(uczen.telefon_glowny, '-')
        self.assertEqual(uczen.email_glowny, '-')

    def test_myslnik_jako_brak_danych(self):
        self.client.force_login(self.user)
        dane = dict(self.dane, telefon_glowny='-', email_glowny='-')
        r = self.client.post('/uczniowie/', dane, follow=True)
        self.assertContains(r, 'został dodany')
        uczen = Uczen.objects.get(imie_ucznia='Kasia')
        self.assertEqual(uczen.telefon_glowny, '-')
        self.assertEqual(uczen.email_glowny, '-')

    def test_zly_email_nadal_odrzucony(self):
        self.client.force_login(self.user)
        dane = dict(self.dane, telefon_glowny='600100200', email_glowny='nie-ma-małpy')
        r = self.client.post('/uczniowie/', dane)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Wpisz poprawny adres e-mail.')
        self.assertEqual(Uczen.objects.count(), 0)

    def test_prawidlowy_email_przechodzi(self):
        self.client.force_login(self.user)
        dane = dict(self.dane, telefon_glowny='600100200', email_glowny='ewa@f.pl')
        r = self.client.post('/uczniowie/', dane, follow=True)
        self.assertContains(r, 'został dodany')
        uczen = Uczen.objects.get(imie_ucznia='Kasia')
        self.assertEqual(uczen.email_glowny, 'ewa@f.pl')    