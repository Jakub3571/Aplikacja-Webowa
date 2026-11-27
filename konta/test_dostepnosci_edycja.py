# -*- coding: utf-8 -*-
"""Poprawki od szefa:
1) Panel 'Kalendarz dostępności' (Uczniowie): klikalne 'wolnych N godzin'
   + zakresy kurczące się o zajęte sloty ('zajęte', gdy cały zakres zajęty).
2) Edycja lekcji cyklicznej z konta osoby umawiającej: formularz podstawia
   obecne sloty, a zmiana godziny ODWOŁUJE stare terminy (odwolana=True)
   i dopisuje nowe; własne lekcje ucznia nie blokują wyboru innej godziny,
   cudze blokują.
"""
from datetime import date, datetime, time, timedelta

from django.test import TestCase
from django.utils import timezone

from konta.models import Dostepnosc, Lekcja, Nauczyciel, Pracownik, Uczen
from konta.views import _tekst_liczby_godzin, _zakresy_wolne_dnia, wolne_godziny_nauczyciela


def _przyszly_dzien(tydzien_dnia):
    """Najbliższy przyszły dzień tygodnia (0=pon) — ściśle w przyszłości."""
    dzis = date.today()
    d = dzis + timedelta(days=(tydzien_dnia - dzis.weekday()) % 7)
    if d <= dzis:
        d += timedelta(days=7)
    return d


def _uczen(imie, nazwisko, korepetytor, umowil):
    return Uczen.objects.create(
        pierwsza_lekcja=timezone.make_aware(datetime.combine(date.today() + timedelta(days=1), time(17, 0))),
        przedmiot='matematyka', poziom='srednia podstawa', szkola='SP 1',
        imie_ucznia=imie, nazwisko_rodzica=nazwisko, rodzic=f'{imie} rodzic',
        telefon_glowny='600100200', email_glowny=f'{imie.lower()}@f.pl',
        korepetytor=korepetytor, kto_umowil='prac', umowil=umowil,
    )


class PanelDostepnosciTests(TestCase):
    """Zadanie 1: panel dostępności pokazuje wolne godziny i kurczące się zakresy."""

    def setUp(self):
        self.admin = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.pracownik = Pracownik.objects.create_user('prac', 'p@f.pl', 'Sup3rHaslo!')
        self.kamil = Nauczyciel.objects.create(imie='Kamil', nazwisko='Kamil', email='k@f.pl', przerwa=10)
        self.uczen = _uczen('Kasia', 'Nowak', 'Kamil Kamil', self.pracownik)
        # Kamil pracuje w przyszły poniedziałek 6:00–15:20 (sloty 30-min jak w "Mój tydzień")
        self.poniedzialek = _przyszly_dzien(0)
        kursor = datetime.combine(date(2000, 1, 1), time(6, 0))
        koniec = datetime.combine(date(2000, 1, 1), time(15, 20))
        while kursor < koniec:
            Dostepnosc.objects.create(
                nauczyciel=self.kamil, data_od=self.poniedzialek, data_do=self.poniedzialek,
                godzina_od=kursor.time(), godzina_do=(kursor + timedelta(minutes=30)).time(),
            )
            kursor += timedelta(minutes=30)

    def _panel(self):
        self.client.force_login(self.pracownik)
        return self.client.get(
            f'/uczniowie/?rok={self.poniedzialek.year}&miesiac={self.poniedzialek.month}&dzien={self.poniedzialek.day}'
        )

    def _lekcja_kamila(self, uczen, godzina):
        return Lekcja.objects.create(
            uczen=uczen,
            data=timezone.make_aware(datetime.combine(self.poniedzialek, godzina)),
        )

    def test_tekst_liczby_godzin_odmiana(self):
        self.assertEqual(_tekst_liczby_godzin(1), 'wolna 1 godzina')
        self.assertEqual(_tekst_liczby_godzin(2), 'wolne 2 godziny')
        self.assertEqual(_tekst_liczby_godzin(5), 'wolnych 5 godzin')
        self.assertEqual(_tekst_liczby_godzin(12), 'wolnych 12 godzin')
        self.assertEqual(_tekst_liczby_godzin(22), 'wolne 22 godziny')

    def test_panel_pokazuje_wolne_godziny_i_zakres(self):
        # 9 slotów rytmu: 6:00, 7:10, ..., 15:20 — wszystkie wolne
        r = self._panel()
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'wolnych 9 godzin')
        self.assertContains(r, '06:00–15:20')
        self.assertContains(r, '11:50')  # lista wolnych godzin po kliknięciu

    def test_zakres_kurczy_sie_o_zajeta_godzine(self):
        # cudzy uczeń zajmuje Kamilowi 11:50 — zakres dzieli się na 6:00–10:40 i 13:00–15:20
        obcy = _uczen('Ola', 'Wis', 'Kamil Kamil', self.admin)
        self._lekcja_kamila(obcy, time(11, 50))
        r = self._panel()
        self.assertContains(r, 'wolnych 8 godzin')
        self.assertContains(r, '06:00–10:40')
        self.assertContains(r, '13:00–15:20')
        self.assertNotContains(r, 'zajęte')
        # pomocniczo: helper bezpośrednio
        wolne = wolne_godziny_nauczyciela(self.kamil, self.poniedzialek)
        self.assertNotIn('11:50', wolne)
        self.assertEqual(
            _zakresy_wolne_dnia(self.kamil, self.poniedzialek, wolne),
            ['06:00–10:40', '13:00–15:20'],
        )

    def test_caly_zakres_zajety_pokazuje_zajete(self):
        obcy = _uczen('Ola', 'Wis', 'Kamil Kamil', self.admin)
        for h, m in [(6, 0), (7, 10), (8, 20), (9, 30), (10, 40), (11, 50), (13, 0), (14, 10), (15, 20)]:
            self._lekcja_kamila(obcy, time(h, m))
        wolne = wolne_godziny_nauczyciela(self.kamil, self.poniedzialek)
        self.assertEqual(wolne, [])
        self.assertEqual(_zakresy_wolne_dnia(self.kamil, self.poniedzialek, wolne), ['zajęte'])
        r = self._panel()
        self.assertContains(r, 'zajęte')
        self.assertNotContains(r, 'wolnych 0 godzin')


class EdycjaCyklicznaTests(TestCase):
    """Zadanie 2: edycja lekcji cyklicznej — zmiana godziny odwołuje stare terminy."""

    def setUp(self):
        self.admin = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.pracownik = Pracownik.objects.create_user('prac', 'p@f.pl', 'Sup3rHaslo!')
        self.uczen = _uczen('Kasia', 'Nowak', 'Jan Kowalski', self.pracownik)
        self.nauczyciel = Nauczyciel.objects.create(imie='Jan', nazwisko='Kowalski', email='jan@n.pl', przerwa=10)
        d = date.today() - timedelta(days=7)
        koniec = date.today() + timedelta(days=400)
        while d <= koniec:
            Dostepnosc.objects.create(
                nauczyciel=self.nauczyciel, data_od=d, data_do=d,
                godzina_od=time(16, 0), godzina_do=time(20, 0),
            )
            d += timedelta(days=1)
        self.wtorek = _przyszly_dzien(1)

    def _post_cykliczna(self, dzien_tyg, godzina, data_od=None, data_do=None):
        dzis = date.today()
        return self.client.post(f'/uczniowie/{self.uczen.id}/lekcja-cykliczna/', {
            'okres': 'miesiac',
            'data_od': (data_od or dzis).isoformat(),
            'data_do': (data_do or (dzis + timedelta(days=92))).isoformat(),
            'slot_dzien': [str(dzien_tyg)],
            'slot_godzina': [godzina],
            'link_meet': '',
        }, follow=True)

    def test_edycja_zmienia_godzine_stare_terminy_odwolane(self):
        self.client.force_login(self.pracownik)
        r = self._post_cykliczna(1, '16:00')
        self.assertContains(r, 'umówione')
        self.assertTrue(Lekcja.objects.filter(uczen=self.uczen, odwolana=False).exists())
        stare = Lekcja.objects.filter(uczen=self.uczen, odwolana=False)
        self.assertTrue(all(timezone.localtime(l.data).strftime('%H:%M') == '16:00' for l in stare))

        # zmiana godziny: wtorek 16:00 -> wtorek 18:20
        r = self._post_cykliczna(1, '18:20')
        self.assertContains(r, 'Odwołano')
        aktywne = Lekcja.objects.filter(uczen=self.uczen, odwolana=False)
        self.assertTrue(aktywne.exists())
        self.assertTrue(all(timezone.localtime(l.data).strftime('%H:%M') == '18:20' for l in aktywne))
        self.assertTrue(all(timezone.localtime(l.data).weekday() == 1 for l in aktywne))
        odwolane = Lekcja.objects.filter(uczen=self.uczen, odwolana=True)
        self.assertTrue(odwolane.exists())
        self.assertTrue(all(timezone.localtime(l.data).strftime('%H:%M') == '16:00' for l in odwolane))

    def test_get_prefilluje_istniejace_sloty(self):
        self.client.force_login(self.pracownik)
        self._post_cykliczna(1, '16:00')
        r = self.client.get(f'/uczniowie/{self.uczen.id}/lekcja-cykliczna/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'data-wybrana-godzina="16:00"')
        self.assertContains(r, 'value="1" selected')

    def test_wlasna_lekcja_nie_blokuje_innej_godziny(self):
        # regresja: własna lekcja cykliczna 16:00 nie blokuje wyboru 18:20 w AJAX
        self.client.force_login(self.pracownik)
        self._post_cykliczna(1, '16:00')
        r = self.client.get(
            f'/korepetytorzy/dane/?nauczyciel={self.nauczyciel.id}'
            f'&data={self.wtorek.isoformat()}&uczen={self.uczen.id}'
        )
        self.assertEqual(r.status_code, 200)
        godziny = [g['godzina'] for g in r.json()['godziny']]
        self.assertIn('16:00', godziny)
        self.assertIn('18:20', godziny)

    def test_cudza_lekcja_blokuje_godzine(self):
        # lekcja INNEGO ucznia Jana o 17:10 blokuje ten slot, 18:20 zostaje wolne
        obcy = _uczen('Ola', 'Wis', 'Jan Kowalski', self.admin)
        Lekcja.objects.create(
            uczen=obcy,
            data=timezone.make_aware(datetime.combine(self.wtorek, time(17, 10))),
        )
        self.client.force_login(self.pracownik)
        r = self.client.get(
            f'/korepetytorzy/dane/?nauczyciel={self.nauczyciel.id}'
            f'&data={self.wtorek.isoformat()}&uczen={self.uczen.id}'
        )
        godziny = [g['godzina'] for g in r.json()['godziny']]
        self.assertNotIn('17:10', godziny)
        self.assertIn('18:20', godziny)
        self.assertIn('16:00', godziny)