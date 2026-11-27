"""Testy przepływu logowania pracownika."""

from datetime import date, datetime, time, timedelta
from django.core.management import call_command

from django.core import mail
from django.test import TestCase
from django.utils import timezone
from datetime import date
from konta.models import Dostepnosc
from konta.models import (
    Dostepnosc,
    Lekcja,
    Nauczyciel,
    Pracownik,
    PrzedmiotNauczyciela,
    PrzedmiotUcznia,
    PrzypomnienieLekcji,
    RaportPlatnosci,
    Uczen,
    Zgloszenie,
)

import os
from unittest import mock

from konta.views import kolizja_lekcji_nauczyciela

class _ZamrozonaData(date):
    """Do testów: „dziś” = 2026-10-01, żeby fixture'y 2026-10-xx były w przyszłości
    (formularz ucznia blokuje daty z przeszłości)."""

    @classmethod
    def today(cls):
        return date(2026, 10, 1)


class TestCaseZamrozonaData(TestCase):
    """Baza dla testów formularza ucznia: zamraża date.today() w konta.forms na 2026-10-01."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        patcher = mock.patch('konta.forms.date', _ZamrozonaData)
        patcher.start()
        cls.addClassCleanup(patcher.stop)

class PrzeplywLogowaniaTests(TestCase):
    def setUp(self):
        self.user = Pracownik.objects.create_user(
            'j.kowalski', 'jan@firma.pl', 'Sup3rHaslo!',
            first_name='Jan', last_name='Kowalski',
            stanowisko='Księgowy', telefon='600700800',
        )

    def test_zle_haslo_pokazuje_blad(self):
        r = self.client.post('/', {'username': 'j.kowalski', 'password': 'zle'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Nieprawidłowa')

    def test_pulpit_bez_logowania_przekierowuje(self):
        r = self.client.get('/pulpit/')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, '/?next=/pulpit/')

    def test_udane_logowanie(self):
        self.user.is_superuser = True
        self.user.save()
        r = self.client.post('/', {'username': 'j.kowalski', 'password': 'Sup3rHaslo!'}, follow=True)
        self.assertTrue(r.context['user'].is_authenticated)
        self.assertRedirects(r, '/pulpit/')

    def test_udane_logowanie_pracownik(self):
        r = self.client.post('/', {'username': 'j.kowalski', 'password': 'Sup3rHaslo!'}, follow=True)
        self.assertTrue(r.context['user'].is_authenticated)
        self.assertRedirects(r, '/uczniowie/')
    def test_udane_logowanie_z_next_wraca_do_next(self):
        r = self.client.post('/?next=/cms/', {'username': 'j.kowalski', 'password': 'Sup3rHaslo!'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, '/cms/')

    def test_logowanie_ignoruje_zewnetrzny_next(self):
        r = self.client.post('/?next=//evil.example/x', {'username': 'j.kowalski', 'password': 'Sup3rHaslo!'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, '/pulpit/')

    def test_zalogowany_z_next_idzie_do_next(self):
        self.client.post('/', {'username': 'j.kowalski', 'password': 'Sup3rHaslo!'})
        r = self.client.get('/?next=/cms/')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, '/cms/')
        
    def test_wylogowanie(self):
        self.client.post('/', {'username': 'j.kowalski', 'password': 'Sup3rHaslo!'})
        r = self.client.get('/wyloguj/', follow=True)
        self.assertFalse(r.context['user'].is_authenticated)

    def test_zmiana_hasla(self):
        self.client.post('/', {'username': 'j.kowalski', 'password': 'Sup3rHaslo!'})
        dane = {
            'old_password': 'Sup3rHaslo!',
            'new_password1': 'N0weSup3rHaslo!',
            'new_password2': 'N0weSup3rHaslo!',
        }
        r = self.client.post('/zmiana-hasla/', dane, follow=True)
        self.assertEqual(r.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('N0weSup3rHaslo!'))


class ZakladkiTests(TestCase):
    """Zakładki: szef widzi wszystko, zwykły pracownik tylko formularz ucznia."""

    def setUp(self):
        self.user = Pracownik.objects.create_superuser('test', 't@f.pl', 'Sup3rHaslo!')

    def test_zakladki_dostepne_po_zalogowaniu(self):
        self.client.force_login(self.user)
        for adres in ['/pulpit/', '/uczniowie/', '/platnosci/']:
            r = self.client.get(adres)
            self.assertEqual(r.status_code, 200, f'{adres} nie dziala')

    def test_zakladki_blokowane_bez_zalogowania(self):
        oczekiwane = {'/pulpit/': 302, '/uczniowie/': 302, '/platnosci/': 302}
        for adres, kod in oczekiwane.items():
            r = self.client.get(adres)
            self.assertEqual(r.status_code, kod, f'{adres} wpuszcza bez logowania')

    def test_pracownik_blokowany_na_panel_szefa(self):
        pracownik = Pracownik.objects.create_user('pracownik', 'p@f.pl', 'Sup3rHaslo!')
        self.client.force_login(pracownik)
        r = self.client.get('/platnosci/')
        self.assertEqual(r.status_code, 403, 'platnosci wpuszcza pracownika')
        r = self.client.get('/uzytkownicy/')
        self.assertEqual(r.status_code, 403, 'uzytkownicy wpuszcza pracownika')
        # lekcje: pracownik widzi swoich uczniow (nowa zasada)
        r = self.client.get('/lekcje/')
        self.assertEqual(r.status_code, 200)
        r = self.client.get('/pulpit/')
        self.assertRedirects(r, '/uczniowie/')

class DodawanieUczniaTests(TestCaseZamrozonaData):
    def setUp(self):
        self.user = Pracownik.objects.create_superuser('test', 't@f.pl', 'Sup3rHaslo!')
        self.nauczyciel = Nauczyciel.objects.create(
            imie='Jan', nazwisko='Kowalski', email='jan@k.pl',
        )
        PrzedmiotNauczyciela.objects.create(
            nauczyciel=self.nauczyciel, przedmiot='matematyka', poziom='srednia rozszerzenie',
        )

    def test_formularz_ucznia_zapisuje_do_bazy(self):
        self.client.force_login(self.user)
                # nauczyciel musi mieć zaznaczone godziny w kalendarzu (17:00 mieści się w 16:00-19:00)
        Dostepnosc.objects.create(
            nauczyciel=self.nauczyciel,
            data_od=date(2026, 10, 5), data_do=date(2026, 10, 5),
            godzina_od='16:00', godzina_do='19:00',
        )
        dane = {
            'data_pierwszej_lekcji': '2026-10-05',
            'godzina_pierwszej_lekcji': '16:00',
            'przedmiot': 'matematyka',
            'poziom': 'srednia rozszerzenie',
            'imie_ucznia': 'Kasia',
            'nazwisko_rodzica': 'Nowak',
            'rodzic': 'Ewa Nowak',
            'telefon_glowny': '600100200',
            'email_glowny': 'ewa@f.pl',
            'telefon_dodatkowy': '600300400',
            'email_dodatkowy': 'rezerwacja@f.pl',
            'korepetytor': 'Jan Kowalski',
            'notatka_od_rodzica': 'Uwaga na alergię',
            'adresat_meet': 'glowny',
            'auto_wyslij_meet': 'on',
        }
        r = self.client.post('/uczniowie/', dane, follow=True)
        self.assertContains(r, 'został dodany')
        self.assertEqual(Uczen.objects.count(), 1)
        uczen = Uczen.objects.first()
        self.assertEqual(uczen.kto_umowil, 'test')
    def test_mail_potwierdzenia_ma_ustawiony_przedmiot_i_poziom(self):
        # mail po umówieniu ma przedmiot i poziom z formularza (np. angielski), nie „zawsze matma”
        PrzedmiotNauczyciela.objects.create(
            nauczyciel=self.nauczyciel, przedmiot='angielski', poziom='srednia rozszerzenie',
        )
        self.client.force_login(self.user)
        Dostepnosc.objects.create(
            nauczyciel=self.nauczyciel,
            data_od=date(2026, 10, 5), data_do=date(2026, 10, 5),
            godzina_od='16:00', godzina_do='19:00',
        )
        dane = {
            'data_pierwszej_lekcji': '2026-10-05',
            'godzina_pierwszej_lekcji': '16:00',
            'przedmiot': 'angielski',
            'poziom': 'srednia rozszerzenie',
            'imie_ucznia': 'Kasia',
            'nazwisko_rodzica': 'Nowak',
            'rodzic': 'Ewa Nowak',
            'telefon_glowny': '600100200',
            'email_glowny': 'ewa@f.pl',
            'telefon_dodatkowy': '',
            'email_dodatkowy': '',
            'korepetytor': 'Jan Kowalski',
            'notatka_od_rodzica': '',
            'auto_wyslij_meet': 'on',
        }
        r = self.client.post('/uczniowie/', dane, follow=True)
        self.assertEqual(r.status_code, 200)
        maile = [m for m in mail.outbox if m.subject.startswith('Umówienie na korepetycje')]
        self.assertEqual(len(maile), 1)
        tresc = maile[0].body
        self.assertIn('Dziękuję za umówienie się na korepetycje', tresc)
        self.assertIn('Przedmiot: angielski', tresc)
        self.assertIn('Poziom: srednia rozszerzenie', tresc)
        self.assertIn('Pierwsza lekcja: 05.10.2026 o godz. 16:00', tresc)
        self.assertIn('messengerze', tresc)
        self.assertNotIn('matematyka', tresc)
    def test_korepetytor_filtruje_się_po_przedmiocie(self):
        inny = Nauczyciel.objects.create(
            imie='Anna', nazwisko='Nowak', email='anna@n.pl',
        )
        PrzedmiotNauczyciela.objects.create(
            nauczyciel=inny, przedmiot='angielski', poziom='srednia podstawa',
        )
        self.client.force_login(self.user)
        r = self.client.post(
            '/uczniowie/',
            {'przedmiot': 'matematyka', 'poziom': 'srednia rozszerzenie',
             'wybierz_przedmiot': '1'},
            follow=True,
        )
        opcje = r.context['form'].fields['korepetytor'].choices
        wartosci = [w for w, _ in opcje]
        self.assertIn('Jan Kowalski', wartosci)
        self.assertNotIn('Anna Nowak', wartosci)

    def test_korepetytor_filtruje_się_po_poziomie(self):
        podstawowy = Nauczyciel.objects.create(
            imie='Ola', nazwisko='Wiśniewska', email='ola@n.pl',
        )
        PrzedmiotNauczyciela.objects.create(
            nauczyciel=podstawowy, przedmiot='matematyka', poziom='srednia podstawa',
        )
        self.client.force_login(self.user)
        r = self.client.post(
            '/uczniowie/',
            {'przedmiot': 'matematyka', 'poziom': 'srednia rozszerzenie',
             'wybierz_przedmiot': '1'},
            follow=True,
        )
        opcje = r.context['form'].fields['korepetytor'].choices
        wartosci = [w for w, _ in opcje]
        self.assertIn('Jan Kowalski', wartosci)
        self.assertNotIn('Ola Wiśniewska', wartosci)

    def test_godzina_1_lekcji_sloty_z_przerwa_co_5_min(self):
        # korepetytor ma zakres 18:00-20:00 i już lekcję o 18:00 (przerwa 5 min):
        # AJAX pokazuje 19:05 (i NIE 18:30), a formularz przyjmuje 19:05
        self.client.force_login(self.user)
        Dostepnosc.objects.create(
            nauczyciel=self.nauczyciel, data_od=date(2026, 10, 5), data_do=date(2026, 10, 5),
            godzina_od='18:00', godzina_do='20:00',
        )
        inny = Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime(2026, 10, 5, 18)),
            przedmiot='matematyka', poziom='srednia rozszerzenie',
            imie_ucznia='Ola', nazwisko_rodzica='Lis', rodzic='Ala Lis',
            telefon_glowny='600', email_glowny='o@f.pl', telefon_dodatkowy='',
            korepetytor='Jan Kowalski', kto_umowil='test',
        )
        Lekcja.objects.create(uczen=inny, data=inny.pierwsza_lekcja)  # zajęte 18:00
        r = self.client.get(
            '/korepetytorzy/dane/?tryb=godziny&przedmiot=matematyka'
            '&poziom=srednia rozszerzenie&data=2026-10-05'
        )
        etykiety = [g['godzina'] for g in r.json()['godziny']]
        self.assertIn('19:05', etykiety)    # 18:00 + 60 min + 5 min przerwy
        self.assertIn('20:10', etykiety)
        self.assertNotIn('18:00', etykiety)
        self.assertNotIn('18:30', etykiety)
        self.assertNotIn('19:00', etykiety)
        # POST z godziną 19:05 przechodzi
        dane = {
            'data_pierwszej_lekcji': '2026-10-05', 'godzina_pierwszej_lekcji': '19:05',
            'przedmiot': 'matematyka', 'poziom': 'srednia rozszerzenie',
            'imie_ucznia': 'Kasia', 'nazwisko_rodzica': 'Nowak', 'rodzic': 'Ewa Nowak',
            'telefon_glowny': '600100200', 'email_glowny': 'ewa@f.pl',
            'telefon_dodatkowy': '', 'email_dodatkowy': '',
            'korepetytor': 'Jan Kowalski', 'notatka_od_rodzica': '', 'auto_wyslij_meet': 'on',
        }
        r = self.client.post('/uczniowie/', dane, follow=True)
        self.assertContains(r, 'został dodany')
        self.assertEqual(Uczen.objects.filter(imie_ucznia='Kasia').count(), 1)
        # a 18:30 jest odrzucane (koliduje z przerwą po lekcji 18:00)
        dane['godzina_pierwszej_lekcji'] = '18:30'
        dane['imie_ucznia'] = 'Kasia2'
        r = self.client.post('/uczniowie/', dane, follow=True)
        self.assertContains(r, 'nie może przyjąć lekcji')
        self.assertFalse(Uczen.objects.filter(imie_ucznia='Kasia2').exists())

    def test_dwie_lekcje_pod_rzad_dostaja_slot_z_przerwa(self):
        # zgloszenie szefa: po lekcji 11:00 druga lekcja ma byc dostepna o 12:05 (przerwa 5 min), nie dopiero 13:00
        self.client.force_login(self.user)
        Dostepnosc.objects.create(
            nauczyciel=self.nauczyciel, data_od=date(2026, 10, 5), data_do=date(2026, 10, 5),
            godzina_od='11:00', godzina_do='14:00',
        )
        dane = {
            'data_pierwszej_lekcji': '2026-10-05', 'godzina_pierwszej_lekcji': '11:00',
            'przedmiot': 'matematyka', 'poziom': 'srednia rozszerzenie',
            'imie_ucznia': 'Kasia', 'nazwisko_rodzica': 'Nowak', 'rodzic': 'Ewa Nowak',
            'telefon_glowny': '600100200', 'email_glowny': 'ewa@f.pl',
            'telefon_dodatkowy': '', 'email_dodatkowy': '',
            'korepetytor': 'Jan Kowalski', 'notatka_od_rodzica': '', 'auto_wyslij_meet': 'on',
        }
        r = self.client.post('/uczniowie/', dane, follow=True)
        self.assertContains(r, 'został dodany')
        # AJAX godzin (jak w formularzu) pokazuje 12:05
        r = self.client.get(
            '/korepetytorzy/dane/?tryb=godziny&przedmiot=matematyka'
            '&poziom=srednia rozszerzenie&data=2026-10-05'
        )
        etykiety = [g['godzina'] for g in r.json()['godziny']]
        self.assertIn('12:05', etykiety)
        self.assertNotIn('12:00', etykiety)
        self.assertNotIn('11:00', etykiety)
        # druga lekcja o 12:05 przechodzi
        dane['godzina_pierwszej_lekcji'] = '12:05'
        dane['imie_ucznia'] = 'Ola'
        r = self.client.post('/uczniowie/', dane, follow=True)
        self.assertContains(r, 'został dodany')
        self.assertTrue(Uczen.objects.filter(imie_ucznia='Ola').exists())
        # i kolejna wolna godzina idzie dalej o przerwe: 13:10
        from konta.views import wolne_godziny_nauczyciela
        self.assertIn('13:10', wolne_godziny_nauczyciela(self.nauczyciel, date(2026, 10, 5)))
        self.assertNotIn('13:00', wolne_godziny_nauczyciela(self.nauczyciel, date(2026, 10, 5)))

class NauczycieleTests(TestCase):
    def setUp(self):
        self.user = Pracownik.objects.create_superuser('test', 't@f.pl', 'Sup3rHaslo!')
        self.nauczyciel = Nauczyciel.objects.create(
            imie='Jan', nazwisko='Kowalski', email='jan@k.pl',
        )
        PrzedmiotNauczyciela.objects.create(
            nauczyciel=self.nauczyciel, przedmiot='matematyka', poziom='srednia podstawa',
        )

    def test_kreator_generuje_sloty_z_przerwa(self):
        self.client.force_login(self.user)
        adres = f'/nauczyciele/{self.nauczyciel.id}/kalendarz/dane/'
        r = self.client.post(adres, {'dzien': '2026-10-05', 'akcja': 'przelacz_godzine', 'godzina': '16:00'})
        self.assertEqual(r.status_code, 200)
        # NOWE: godzina od razu jako cykl co tydzień aż do końca wakacji (31.08.2027) — 48 terminów
        self.assertEqual(Dostepnosc.objects.count(), 48)
        slot = Dostepnosc.objects.filter(data_od=date(2026, 10, 5)).first()
        self.assertEqual(slot.godzina_od.strftime('%H:%M'), '16:00')
        self.assertEqual(slot.godzina_do.strftime('%H:%M'), '16:30')
        self.assertTrue(slot.cykliczny)
        self.assertEqual(slot.cykliczny_okres, 1)
        self.assertTrue(Dostepnosc.objects.filter(data_od=date(2026, 10, 12), godzina_od='16:00').exists())
        # drugi klik w tym dniu: usuwa TYLKO 05.10 — cykl w kolejnych tygodniach zostaje
        r = self.client.post(adres, {'dzien': '2026-10-05', 'akcja': 'przelacz_godzine', 'godzina': '16:00'})
        self.assertEqual(Dostepnosc.objects.count(), 47)
        self.assertFalse(Dostepnosc.objects.filter(data_od=date(2026, 10, 5)).exists())
        self.assertTrue(Dostepnosc.objects.filter(data_od=date(2026, 10, 12), godzina_od='16:00').exists())

    def test_kalendarz_pokazuje_tydzien(self):
        self.client.force_login(self.user)
        Dostepnosc.objects.create(
            nauczyciel=self.nauczyciel,
            data_od='2026-10-05', data_do='2026-10-05',
            godzina_od='16:00', godzina_do='16:30',
        )
        r = self.client.get(
            f'/moj-tydzien/{self.nauczyciel.id}/',
            {'rok': 2026, 'miesiac': 10, 'dzien': 5},
            follow=True,
        )
        self.assertContains(r, '16:00')
        self.assertContains(r, '16:30')



    def test_archiwum_pokazuje_tylko_zakonczonych_i_wylaczonych(self):
        self.client.force_login(self.user)
        # self.nauczyciel (Jan Kowalski) jest aktywny -> nie pokazuje się w archiwum
        Nauczyciel.objects.create(imie='Ola', nazwisko='Nieaktywna', email='o@n.pl', aktywny=False)
        Pracownik.objects.create_user('prac_aktywny', 'pa@f.pl', 'Sup3rHaslo!')
        Pracownik.objects.create_user('prac_wylaczony', 'pw@f.pl', 'Sup3rHaslo!', is_active=False)
        r = self.client.get('/archiwum/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Archiwum uczniów')
        self.assertContains(r, 'Ola Nieaktywna')        # nieaktywny korepetytor -> w archiwum
        self.assertNotContains(r, 'Jan Kowalski')        # aktywny korepetytor -> NIE ma w archiwum
        self.assertContains(r, 'prac_wylaczony')         # wyłączone konto -> w archiwum
        self.assertNotContains(r, 'prac_aktywny')        # aktywne konto -> NIE ma w archiwum


    def test_archiwum_sekcja_osoby_umawiajace_i_czerwony_x(self):
        self.client.force_login(self.user)
        Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime(2026, 10, 5, 17)),
            przedmiot='matematyka', poziom='rozszerzony',
            imie_ucznia='Kasia', nazwisko_rodzica='Nowak', rodzic='Ewa Nowak',
            telefon_glowny='600100200', email_glowny='e@f.pl',
            telefon_dodatkowy='600300400', email_dodatkowy='r@f.pl',
            korepetytor='Jan Kowalski', umowil=self.user, zakonczona=True,
        )
        r = self.client.get('/archiwum/')
        self.assertContains(r, 'Osoby umawiające — wyłączone')
        self.assertNotContains(r, 'Pracownicy — wyłączeni')
        self.assertContains(r, 'ikona-usun')    # X przy „usuń ucznia” (szef); 🗑 tylko przy kontach w archiwum

    def test_pasek_nauczyciela_pod_zakladkami(self):
        # pasek z imieniem + przedmiotami widzi tylko zalogowany korepetytor, na całej szerokości pod zakładkami
        n = Nauczyciel.objects.create(imie='Jan', nazwisko='Kowalski', email='j@k.pl')
        PrzedmiotNauczyciela.objects.create(nauczyciel=n, przedmiot='matematyka', poziom='srednia podstawa')
        u = Pracownik.objects.create_user('jk', 'jk@f.pl', 'Sup3rHaslo!', nauczyciel=n)
        self.client.force_login(u)
        r = self.client.get('/moj-tydzien/')
        self.assertContains(r, 'pasek-nauczyciela')
        self.assertContains(r, 'Jan Kowalski')
        self.assertContains(r, 'matematyka')
        # osoba bez konta korepetytora (tu: superuser bez nauczyciela) paska NIE widzi
        self.client.force_login(self.user)
        r2 = self.client.get('/uczniowie/')
        self.assertNotContains(r2, 'pasek-nauczyciela')

    def test_edytuj_ucznia_przedmioty_obok_formularza(self):
        # na stronie edycji ucznia formularz i sekcja przedmiotów są obok siebie (jedna siatka)
        uczen = Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime(2026, 10, 5, 17)),
            przedmiot='matematyka', poziom='rozszerzony',
            imie_ucznia='Kasia', nazwisko_rodzica='Nowak', rodzic='Ewa Nowak',
            telefon_glowny='600100200', email_glowny='e@f.pl',
            telefon_dodatkowy='600300400', email_dodatkowy='r@f.pl',
            korepetytor='Jan Kowalski', umowil=self.user,
        )
        self.client.force_login(self.user)
        r = self.client.get(f'/uczniowie/{uczen.id}/edytuj/')
        self.assertContains(r, 'edytuj-uklad')
        self.assertContains(r, 'Edytuj ucznia')
        self.assertContains(r, 'Przedmioty ucznia')

    def test_uczniowie_pokazuja_dostepnosc(self):
        self.client.force_login(self.user)
        Dostepnosc.objects.create(
            nauczyciel=self.nauczyciel,
            data_od='2026-10-05', data_do='2026-10-10',
            godzina_od='16:00', godzina_do='20:00',
        )
        r = self.client.get('/uczniowie/', {'rok': 2026, 'miesiac': 10, 'dzien': 5})
        self.assertContains(r, 'Kalendarz dostępności')
        self.assertContains(r, 'Jan Kowalski')
        self.assertContains(r, '16:00')
        self.assertContains(r, '20:00')

    def test_uczniowie_dostepnosc_pokazuje_przedmioty_nauczyciela(self):
        # obok nazwiska w nawiasie mają być przedmioty korepetytora (posortowane, bez poziomów)
        PrzedmiotNauczyciela.objects.create(
            nauczyciel=self.nauczyciel, przedmiot='angielski', poziom='podstawowka',
        )
        self.client.force_login(self.user)
        Dostepnosc.objects.create(
            nauczyciel=self.nauczyciel,
            data_od='2026-10-05', data_do='2026-10-10',
            godzina_od='16:00', godzina_do='20:00',
        )
        r = self.client.get('/uczniowie/', {'rok': 2026, 'miesiac': 10, 'dzien': 5})
        self.assertContains(r, 'Jan Kowalski')
        # przedmioty sa teraz kolorowane wg poziomu (span.przedmiot-poziom)
        self.assertContains(r, 'przedmiot-poziom poziom-podstawowka">angielski')
        self.assertContains(r, '>matematyka</span>')
        self.assertContains(r, 'nauczyciel-grupa') # nazwisko + przedmioty w jednej grupie z lewej

class PlatnosciTests(TestCase):
    def setUp(self):
        self.user = Pracownik.objects.create_superuser('test', 't@f.pl', 'Sup3rHaslo!')

    def dodaj_ucznia(self, imie='Kasia', zaplacone=False):
        return Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime(2026, 10, 5, 17)),
            przedmiot='matematyka', poziom='rozszerzony',
            imie_ucznia=imie, nazwisko_rodzica='Nowak', rodzic='Ewa Nowak',
            telefon_glowny='600100200', email_glowny='e@f.pl',
            telefon_dodatkowy='600300400', email_dodatkowy='r@f.pl',
            korepetytor='Jan Kowalski', notatka_od_rodzica='ok', kto_umowil='Jan Kowalski',
            zaplacone=zaplacone,
        )

    def test_platnosci_pokazuja_uczniow(self):
        self.client.force_login(self.user)
        self.dodaj_ucznia()
        r = self.client.get('/platnosci/')
        self.assertContains(r, 'Kasia Nowak')
        self.assertContains(r, 'Jan Kowalski')
        self.assertContains(r, 'niezapłacone')

    def test_platnosci_maja_siatke_jak_uzytkownicy(self):
        # wszystkie tabele w Płatnościach mają klasę tabela-siata (pionowe linie między kolumnami)
        self.client.force_login(self.user)
        self.dodaj_ucznia()
        r = self.client.get('/platnosci/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'tabela tabela-siata')

    def test_raport_i_historia_w_ukladzie_pelnoszerokim(self):
        # "Wyślij raport" i "Historia płatności" siedzą WEWNĄTRZ .platnosci-uklad razem z "Zapłacone"
        # (zamknięcie </div> ukladu jest dopiero PO sekcji Historii) -> pełna szerokość jak pozostałe karty
        self.client.force_login(self.user)
        self.dodaj_ucznia('Kasia', zaplacone=True)
        r = self.client.get('/platnosci/')
        html = r.content.decode()
        frag_raport = html[html.find('<h2>Zapłacone'):html.find('<h2>Wyślij raport</h2>')]
        self.assertNotIn('\n</div>\n', frag_raport)                     # raport NIE jest poza ukladem
        self.assertIn('\n</div>\n', html[html.find('Historia płatności'):])  # uklad zamknięty dopiero po Historii

    def test_platnosci_blokowane_bez_zalogowania(self):
        r = self.client.get('/platnosci/')
        # niezalogowany dostaje przekierowanie na logowanie (nie wie, ze strona istnieje)
        self.assertEqual(r.status_code, 302)

    def test_przycisk_zaplacone_zmienia_status(self):
        self.client.force_login(self.user)
        uczen = self.dodaj_ucznia()
        r = self.client.post('/platnosci/', {'oznacz_zaplacone': '1', 'uczen_id': uczen.id}, follow=True)
        self.assertContains(r, 'zapłacone')
        uczen.refresh_from_db()
        self.assertTrue(uczen.zaplacone)

    def test_usuwanie_ucznia_z_listy(self):
        self.client.force_login(self.user)
        uczen = self.dodaj_ucznia()
        r = self.client.post('/platnosci/', {'usun_ucznia': '1', 'uczen_id': uczen.id}, follow=True)
        self.assertContains(r, 'usunięto')
        self.assertEqual(Uczen.objects.count(), 0)

    def test_raport_zablokowany_bez_zaplaty(self):
        self.client.force_login(self.user)
        self.dodaj_ucznia()
        r = self.client.post('/platnosci/', {'wyslij_raport': '1', 'email_raportu': 'szef@f.pl'}, follow=True)
        self.assertContains(r, 'Brak zapłaconych płatności')
        self.assertEqual(Uczen.objects.count(), 1)

    def test_raport_wysyla_i_czysci_liste(self):
        self.client.force_login(self.user)
        self.dodaj_ucznia('Kasia', zaplacone=True)
        self.dodaj_ucznia('Ola', zaplacone=True)
        r = self.client.post('/platnosci/', {'wyslij_raport': '1', 'email_raportu': 'szef@f.pl'}, follow=True)
        self.assertContains(r, 'Raport wysłano')
        # uczniowie zostają w aplikacji (zajęcia bez zmian) — czyścimy tylko znacznik zapłaty
        self.assertEqual(Uczen.objects.count(), 2)
        self.assertEqual(Uczen.objects.filter(zaplacone=True).count(), 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['szef@f.pl'])
        self.assertEqual(RaportPlatnosci.objects.count(), 1)

    def test_cofnij_zaplacone_nie_usuwa_ucznia(self):
        # "Cofnij" tylko odznacza zapłatę — uczeń zostaje w aplikacji i wraca do nieopłaconych
        self.client.force_login(self.user)
        uczen = self.dodaj_ucznia('Kasia', zaplacone=True)
        r = self.client.post('/platnosci/', {'odznacz_zaplacone': '1', 'uczen_id': uczen.id}, follow=True)
        self.assertContains(r, 'cofnięto status opłacone')
        uczen.refresh_from_db()
        self.assertEqual(Uczen.objects.count(), 1)      # uczeń NIE został usunięty
        self.assertFalse(uczen.zaplacone)
        self.assertIsNone(uczen.rozliczono_kiedy)       # NIE trafił do historii raportów
        r = self.client.get('/platnosci/')
        self.assertContains(r, 'Kasia Nowak')           # wraca na listę nieopłaconych

    def test_pobierz_raport_jako_html_z_siatka(self):
        # plik raportu to HTML z tabelą (kolumny + siatka jak na stronie), nie sam tekst
        self.client.force_login(self.user)
        uczen = self.dodaj_ucznia('Kasia', zaplacone=True)
        r = self.client.get('/raport-platnosci/')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r['Content-Type'].startswith('text/html'))
        self.assertIn('.html', r['Content-Disposition'])
        self.assertContains(r, '<table')
        self.assertContains(r, 'Kasia Nowak')
        self.assertContains(r, 'Razem zapłaconych płatności: 1')
        uczen.refresh_from_db()
        self.assertFalse(uczen.zaplacone)
        self.assertIsNotNone(uczen.rozliczono_kiedy)    # pobranie raportu = archiwizacja do historii
        self.assertEqual(RaportPlatnosci.objects.count(), 1)

    def test_historia_raportow_html_dla_nowych_txt_dla_starych(self):
        # nowe raporty z historii pobierają się jako .html, stare (przed zmianą) jako .txt
        self.client.force_login(self.user)
        nowy = RaportPlatnosci.objects.create(
            zawartosc='<html><table><tr><td>x</td></tr></table></html>',
            email_odbiorcy='a@f.pl', wyslal='test')
        stary = RaportPlatnosci.objects.create(
            zawartosc='stary raport tekstowy',
            email_odbiorcy='b@f.pl', wyslal='test')
        r = self.client.get(f'/raport-platnosci/{nowy.id}/')
        self.assertTrue(r['Content-Type'].startswith('text/html'))
        self.assertIn('.html', r['Content-Disposition'])
        self.assertContains(r, '<table')
        r = self.client.get(f'/raport-platnosci/{stary.id}/')
        self.assertTrue(r['Content-Type'].startswith('text/plain'))
        self.assertIn('.txt', r['Content-Disposition'])
        self.assertContains(r, 'stary raport tekstowy')

    def test_platnosci_pokazuja_lekcje_cykliczne_pogrupowane_miesiacem(self):
        # kolumna "Lekcje cykliczne": przyszłe lekcje ucznia pogrupowane po miesiącu
        from konta.views import MIESIACE
        self.client.force_login(self.user)
        uczen = self.dodaj_ucznia()
        dzis = timezone.localdate()
        d1 = dzis + timedelta(days=3)
        if d1.month == 12:
            d2 = d1.replace(year=d1.year + 1, month=1, day=5)
        else:
            d2 = d1.replace(month=d1.month + 1, day=5)
        Lekcja.objects.create(uczen=uczen, data=timezone.make_aware(datetime(d1.year, d1.month, d1.day, 17, 0)))
        Lekcja.objects.create(uczen=uczen, data=timezone.make_aware(datetime(d2.year, d2.month, d2.day, 17, 0)))
        r = self.client.get('/platnosci/')
        self.assertContains(r, 'Lekcje cykliczne')
        self.assertContains(r, f'{d1.day:02d}.{d1.month:02d} 17:00')
        self.assertContains(r, f'{d2.day:02d}.{d2.month:02d} 17:00')
        self.assertContains(r, f'{MIESIACE[d1.month - 1][:3]} {d1.year}')

    def test_po_wyslaniu_raportu_lekcje_zostaja_a_historia_pokazuje_terminy(self):
        # raport (mail lub plik) NIE czyści lekcji — uczeń zostaje z zajęciami,
        # a w Historii nadal widać jego terminy pogrupowane miesiącem
        self.client.force_login(self.user)
        uczen = self.dodaj_ucznia('Kasia', zaplacone=True)
        dzis = timezone.localdate()
        d1 = dzis + timedelta(days=3)
        Lekcja.objects.create(uczen=uczen, data=timezone.make_aware(datetime(d1.year, d1.month, d1.day, 17, 0)))
        self.client.post('/platnosci/', {'wyslij_raport': '1', 'email_raportu': 'szef@f.pl'}, follow=True)
        self.assertEqual(Lekcja.objects.filter(uczen=uczen).count(), 1)   # lekcja została
        r = self.client.get('/platnosci/')
        self.assertContains(r, 'Historia płatności')
        self.assertContains(r, f'{d1.day:02d}.{d1.month:02d} 17:00')      # historia pokazuje terminy

    def test_kolumna_lekcje_cykliczne_ma_mini_kalendarz_miesieczny(self):
        # komórka "Lekcje cykliczne" = mini-kalendarz: strzałki ‹ › + dane JSON w data-lekcje,
        # a pierwszy miesiąc jest też wyrenderowany serwerowo jako tekst (działa bez JS)
        self.client.force_login(self.user)
        uczen = self.dodaj_ucznia()
        dzis = timezone.localdate()
        d1 = dzis + timedelta(days=3)
        Lekcja.objects.create(uczen=uczen, data=timezone.make_aware(datetime(d1.year, d1.month, d1.day, 17, 0)))
        r = self.client.get('/platnosci/')
        self.assertContains(r, 'mini-kal')
        self.assertContains(r, 'data-lekcje=')
        self.assertContains(r, 'mini-strz')
        self.assertContains(r, f'{d1.day:02d}.{d1.month:02d} 17:00')   # pierwszy miesiąc jako tekst

    def test_platnosci_podzielone_na_cyklicznych_i_pojedynczych(self):
        # dwa osobne kafelki Płatności: "zajęcia cykliczne" (zajecia_cykliczne=True)
        # i "pojedyncza 1. lekcja" — tak samo w Zapłaconych
        self.client.force_login(self.user)
        cykliczny = self.dodaj_ucznia('Kasia')
        cykliczny.zajecia_cykliczne = True
        cykliczny.save()
        self.dodaj_ucznia('Ola')   # bez flagi = pojedyncza 1. lekcja
        z = self.dodaj_ucznia('Zosia', zaplacone=True)
        z.zajecia_cykliczne = True
        z.save()
        r = self.client.get('/platnosci/')
        html = r.content.decode()
        self.assertContains(r, 'Płatności — zajęcia cykliczne')
        self.assertContains(r, 'Płatności — pojedyncza 1. lekcja')
        self.assertContains(r, 'Zapłacone — zajęcia cykliczne')
        self.assertContains(r, 'Zapłacone — pojedyncza 1. lekcja')
        self.assertContains(r, 'tabela-platnosci-cykliczni')
        self.assertContains(r, 'tabela-platnosci-pojedynczy')
        self.assertContains(r, 'tabela-platnosci-zaplacone-cykliczni')
        # Kasia (cykliczna) jest w kafelku cyklicznym, Ola (pojedyncza) w drugim kafelku
        i_cyk = html.find('Płatności — zajęcia cykliczne')
        i_pojed = html.find('Płatności — pojedyncza 1. lekcja')
        self.assertLess(i_cyk, html.find('Kasia Nowak'))
        self.assertLess(html.find('Kasia Nowak'), i_pojed)
        self.assertLess(i_pojed, html.find('Ola Nowak'))

class UzytkownicyTests(TestCase):
    """Zakładka użytkownicy: dodawanie kont i uprawnienia (tylko szef)."""

    def setUp(self):
        self.user = Pracownik.objects.create_superuser('szef', 's@f.pl', 'Sup3rHaslo!')

    def test_zakladka_blokowana_dla_pracownika(self):
        pracownik = Pracownik.objects.create_user('prac', 'p@f.pl', 'Sup3rHaslo!')
        self.client.force_login(pracownik)
        r = self.client.get('/uzytkownicy/')
        self.assertEqual(r.status_code, 403)

    def test_koordynator_moze_dodawac_konta_bez_admina(self):
        # rola koordynatora zapisana na koncie — login moze byc dowolny
        koordynator = Pracownik.objects.create_user('ala', 'k@f.pl', 'Sup3rHaslo!')
        koordynator.stanowisko = 'koordynator'
        koordynator.save()
        self.client.force_login(koordynator)
        r = self.client.get('/uzytkownicy/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Dodaj użytkownika')
        # lista ról bez admina
        self.assertNotContains(r, 'value="admin"')
        # ale sekcję Admini widzi jako podgląd (bez przycisków zarządzania)
        self.assertContains(r, 'Admini')
        self.assertContains(r, 'tylko szef')
        # koordynator nie może utworzyć konta admina (obejście formularza)
        dane = {
            'username': 'nowy.admin',
            'first_name': 'Nowy',
            'last_name': 'Admin',
            'email': 'n@f.pl',
            'telefon': '',
            'haslo1': 'M0taneHaslo!',
            'haslo2': 'M0taneHaslo!',
            'rola': 'admin',
        }
        r = self.client.post('/uzytkownicy/', dane, follow=True)
        self.assertFalse(Pracownik.objects.filter(username='nowy.admin').exists())
        self.assertContains(r, 'Popraw błędy w formularzu')
        # koordynator może utworzyć zwykłego pracownika
        dane['username'] = 'j.kowalska'
        dane['rola'] = 'pracownik'
        r = self.client.post('/uzytkownicy/', dane, follow=True)
        self.assertContains(r, 'został dodany')
        self.assertTrue(Pracownik.objects.filter(username='j.kowalska').exists())

    def test_dodawanie_pracownika(self):
        self.client.force_login(self.user)
        dane = {
            'username': 'j.nowak',
            'first_name': 'Janina',
            'last_name': 'Nowak',
            'email': 'j.nowak@f.pl',
            'telefon': '600700900',
            'haslo1': 'M0taneHaslo!',
            'haslo2': 'M0taneHaslo!',
            'rola': 'pracownik',
        }
        r = self.client.post('/uzytkownicy/', dane, follow=True)
        self.assertContains(r, 'został dodany')
        nowy = Pracownik.objects.get(username='j.nowak')
        self.assertFalse(nowy.is_superuser)
        self.assertTrue(nowy.check_password('M0taneHaslo!'))

    def test_dodawanie_szefa(self):
        self.client.force_login(self.user)
        dane = {
            'username': 'szef2',
            'email': 'szef2@f.pl',
            'haslo1': 'M0taneHaslo!',
            'haslo2': 'M0taneHaslo!',
            'rola': 'admin',
        }
        r = self.client.post('/uzytkownicy/', dane, follow=True)
        self.assertContains(r, 'został dodany')
        nowy = Pracownik.objects.get(username='szef2')
        self.assertTrue(nowy.is_superuser)

    def test_rozne_hasla(self):
        self.client.force_login(self.user)
        dane = {
            'username': 'x',
            'email': 'x@f.pl',
            'haslo1': 'M0taneHaslo!',
            'haslo2': 'InneHaslo!',
            'rola': 'pracownik',
        }
        r = self.client.post('/uzytkownicy/', dane)
        self.assertContains(r, 'identyczne')
        self.assertFalse(Pracownik.objects.filter(username='x').exists())

    def test_przelaczanie_szefa(self):
        self.client.force_login(self.user)
        pracownik = Pracownik.objects.create_user('prac', 'p@f.pl', 'Sup3rHaslo!')
        r = self.client.get(f'/uzytkownicy/{pracownik.id}/przelacz-szefa/', follow=True)
        pracownik.refresh_from_db()
        self.assertTrue(pracownik.is_superuser)
        r = self.client.get(f'/uzytkownicy/{pracownik.id}/przelacz-szefa/', follow=True)
        pracownik.refresh_from_db()
        self.assertFalse(pracownik.is_superuser)

    def test_samemu_sobie_nie_odbierzesz(self):
        self.client.force_login(self.user)
        r = self.client.get(f'/uzytkownicy/{self.user.id}/przelacz-szefa/', follow=True)
        self.assertContains(r, 'samemu sobie')
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_superuser)

    def test_konto_admin_to_szef(self):
        admin = Pracownik.objects.create_user('Admin', 'a@f.pl', 'Sup3rHaslo!')
        self.client.force_login(admin)
        r = self.client.get('/uzytkownicy/')
        self.assertEqual(r.status_code, 200)

    def test_blad_w_polowie_tworzenia_konta_nie_zostawia_konta_i_nie_wycieka(self):
        # awaria podczas zapisu przedmiotów: konto NIE może zostać w bazie (transakcja),
        # a komunikat nie może zawierać treści wyjątku (np. szczegółów bazy)
        from unittest.mock import patch
        self.client.force_login(self.user)
        dane = {
            'username': 'awaria', 'first_name': 'A', 'last_name': 'B',
            'email': 'awaria@f.pl', 'telefon': '',
            'haslo1': 'M0taneHaslo!', 'haslo2': 'M0taneHaslo!',
            'rola': 'nauczyciel',
            'w_przedmiot': ['matematyka'], 'w_poziom': ['srednia podstawa'], 'w_stawka': ['50'],
        }
        with patch('konta.forms.PrzedmiotNauczyciela.objects.update_or_create', side_effect=RuntimeError('SECRET DB DETAIL 123')):
            r = self.client.post('/uzytkownicy/', dane, follow=True)
        self.assertFalse(Pracownik.objects.filter(username='awaria').exists(), 'po błędzie konto nie może zostać w bazie')
        self.assertFalse(Nauczyciel.objects.filter(email='awaria@f.pl').exists(), 'karta nauczyciela też nie może zostać')
        tresc = r.content.decode()
        self.assertNotIn('SECRET DB DETAIL', tresc, 'komunikat nie może wyciekać treści wyjątku')
        self.assertContains(r, 'Nie udało się utworzyć konta')

    def test_duplikat_email_karty_korepetytora_ma_czytelny_komunikat(self):
        Nauczyciel.objects.create(imie='Jan', nazwisko='Kowalski', email='jan@k.pl')
        self.client.force_login(self.user)
        dane = {
            'username': 'nowy.jan', 'first_name': 'Jan', 'last_name': 'Kowalski',
            'email': 'jan@k.pl', 'telefon': '',
            'haslo1': 'M0taneHaslo!', 'haslo2': 'M0taneHaslo!',
            'rola': 'nauczyciel',
            'w_przedmiot': ['matematyka'], 'w_poziom': ['srednia podstawa'], 'w_stawka': ['50'],
        }
        r = self.client.post('/uzytkownicy/', dane, follow=True)
        self.assertFalse(Pracownik.objects.filter(username='nowy.jan').exists())
        self.assertContains(r, 'karta korepetytora z tym adresem e-mail')

class EdycjaKorepetytoraTests(TestCaseZamrozonaData):
    """Pełna edycja konta korepetytora: dane, e-mail (synchronizacja z kartą), przedmioty."""

    def setUp(self):
        self.szef = Pracownik.objects.create_superuser('szef', 's@f.pl', 'Sup3rHaslo!')
        self.nauczyciel = Nauczyciel.objects.create(imie='Jan', nazwisko='Kowalski', email='jan@k.pl')
        PrzedmiotNauczyciela.objects.create(nauczyciel=self.nauczyciel, przedmiot='matematyka', poziom='srednia podstawa', stawka=50)
        self.user = Pracownik.objects.create_user('jk', 'jk@f.pl', 'Sup3rHaslo!', nauczyciel=self.nauczyciel)
        self.uczen = Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime(2026, 10, 5, 17)),
            przedmiot='matematyka', poziom='srednia podstawa', szkola='SP 1',
            imie_ucznia='Kasia', nazwisko_rodzica='Nowak', rodzic='Ewa Nowak',
            telefon_glowny='600100200', email_glowny='e@f.pl', telefon_dodatkowy='',
            korepetytor='Jan Kowalski', kto_umowil='szef', umowil=self.szef,
        )

    def test_szef_edytuje_email_korepetytora_synchronizacja_z_karta(self):
        self.client.force_login(self.szef)
        r = self.client.post(f'/uzytkownicy/{self.user.id}/edytuj/', {
            'first_name': 'Jan', 'last_name': 'Kowalski',
            'email': 'jan.nowy@f.pl', 'telefon': '600111222',
        }, follow=True)
        self.assertEqual(r.status_code, 200)
        self.user.refresh_from_db()
        self.nauczyciel.refresh_from_db()
        self.assertEqual(self.user.email, 'jan.nowy@f.pl')
        self.assertEqual(self.nauczyciel.email, 'jan.nowy@f.pl')
        self.assertEqual(self.user.telefon, '600111222')

    def test_zmiana_imienia_synchronizuje_karte_i_uczniow(self):
        self.client.force_login(self.szef)
        self.client.post(f'/uzytkownicy/{self.user.id}/edytuj/', {
            'first_name': 'Janusz', 'last_name': 'Kowal', 'email': '', 'telefon': '',
        }, follow=True)
        self.nauczyciel.refresh_from_db()
        self.uczen.refresh_from_db()
        self.assertEqual(f'{self.nauczyciel.imie} {self.nauczyciel.nazwisko}', 'Janusz Kowal')
        self.assertEqual(self.uczen.korepetytor, 'Janusz Kowal')

    def test_dodaj_przedmiot_i_zmien_stawke(self):
        from decimal import Decimal
        self.client.force_login(self.szef)
        r = self.client.post(f'/uzytkownicy/{self.user.id}/edytuj/', {
            'dodaj_przedmiot': '1', 'przedmiot': 'angielski', 'poziom': 'podstawowka', 'stawka': '70',
        }, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(PrzedmiotNauczyciela.objects.filter(
            nauczyciel=self.nauczyciel, przedmiot='angielski', poziom='podstawowka', stawka=70,
        ).exists())
        # ta sama para przedmiot+poziom -> tylko aktualizacja stawki, bez duplikatu
        self.client.post(f'/uzytkownicy/{self.user.id}/edytuj/', {
            'dodaj_przedmiot': '1', 'przedmiot': 'angielski', 'poziom': 'podstawowka', 'stawka': '80,50',
        }, follow=True)
        wiersze = PrzedmiotNauczyciela.objects.filter(nauczyciel=self.nauczyciel, przedmiot='angielski', poziom='podstawowka')
        self.assertEqual(wiersze.count(), 1)
        self.assertEqual(wiersze.first().stawka, Decimal('80.50'))

    def test_usun_przedmiot(self):
        self.client.force_login(self.szef)
        wiersz = PrzedmiotNauczyciela.objects.get(nauczyciel=self.nauczyciel, przedmiot='matematyka')
        self.client.post(f'/uzytkownicy/{self.user.id}/edytuj/', {
            'usun_przedmiot': '1', 'przedmiot_id': wiersz.id,
        }, follow=True)
        self.assertFalse(PrzedmiotNauczyciela.objects.filter(pk=wiersz.id).exists())

    def test_zla_stawka_odrzucona(self):
        self.client.force_login(self.szef)
        self.client.post(f'/uzytkownicy/{self.user.id}/edytuj/', {
            'dodaj_przedmiot': '1', 'przedmiot': 'fizyka', 'poziom': 'studia', 'stawka': 'abc',
        }, follow=True)
        self.assertFalse(PrzedmiotNauczyciela.objects.filter(nauczyciel=self.nauczyciel, przedmiot='fizyka').exists())

    def test_koordynator_edytuje_korepetytora_ale_nie_admina(self):
        koord = Pracownik.objects.create_user('koord', 'k@f.pl', 'Sup3rHaslo!')
        koord.stanowisko = 'koordynator'
        koord.save()
        self.client.force_login(koord)
        r = self.client.post(f'/uzytkownicy/{self.user.id}/edytuj/', {
            'first_name': 'Jan', 'last_name': 'Kowalski', 'email': 'k.koord@f.pl', 'telefon': '',
        }, follow=True)
        self.assertEqual(r.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'k.koord@f.pl')
        r = self.client.get(f'/uzytkownicy/{self.szef.id}/edytuj/')
        self.assertEqual(r.status_code, 403)

    def test_pracownik_nie_edytuje(self):
        prac = Pracownik.objects.create_user('prac', 'p@f.pl', 'Sup3rHaslo!')
        self.client.force_login(prac)
        r = self.client.get(f'/uzytkownicy/{self.user.id}/edytuj/')
        self.assertEqual(r.status_code, 403)

    def test_strona_edycji_ma_email_i_przedmioty(self):
        self.client.force_login(self.szef)
        r = self.client.get(f'/uzytkownicy/{self.user.id}/edytuj/')
        self.assertContains(r, 'id_email')
        self.assertContains(r, 'Przedmioty korepetytora')
        self.assertContains(r, 'matematyka')
        # formularz edycji i przedmioty obok siebie (2 kolumny, pełna szerokość jak pasek zakładek)
        self.assertContains(r, 'edytuj-uklad')

class KontoNauczycielaTests(TestCase):
    """Konto dla nauczyciela: dodawanie i dostęp tylko do własnego kalendarza."""

    def setUp(self):
        self.admin = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')

    def dodaj_konto_nauczyciela(self):
        self.client.force_login(self.admin)
        dane = {
            'username': 'j.kowalski',
            'first_name': 'Jan',
            'last_name': 'Kowalski',
            'email': 'jan@k.pl',
            'haslo1': 'M0taneHaslo!',
            'haslo2': 'M0taneHaslo!',
            'rola': 'nauczyciel',
            'w_przedmiot': ['matematyka'],
            'w_poziom': ['srednia podstawa'],
            'w_stawka': ['50'],
        }
        r = self.client.post('/uzytkownicy/', dane, follow=True)
        self.assertContains(r, 'został dodany')
        return Pracownik.objects.get(username='j.kowalski')

    def test_konto_nauczyciela_tworzy_karte_nauczyciela(self):
        user = self.dodaj_konto_nauczyciela()
        self.assertIsNotNone(user.nauczyciel)
        self.assertEqual(user.nauczyciel.imie, 'Jan')
        self.assertEqual(user.nauczyciel.nazwisko, 'Kowalski')

    def test_nauczyciel_po_zalogowaniu_widzi_swoj_kalendarz(self):
        user = self.dodaj_konto_nauczyciela()
        self.client.logout()
        self.client.login(username='j.kowalski', password='M0taneHaslo!')
        r = self.client.get('/pulpit/', follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Mój tydzień')

    def test_nauczyciel_nie_wejdzie_do_cudzego_kalendarza(self):
        user = self.dodaj_konto_nauczyciela()
        inny = Nauczyciel.objects.create(imie='Ola', nazwisko='Nowak', email='ola@n.pl')
        self.client.logout()
        self.client.login(username='j.kowalski', password='M0taneHaslo!')
        r = self.client.get(f'/moj-tydzien/{inny.id}/')
        self.assertEqual(r.status_code, 403)

    def test_nauczyciel_nie_widzi_panelu_admina(self):
        user = self.dodaj_konto_nauczyciela()
        self.client.logout()
        self.client.login(username='j.kowalski', password='M0taneHaslo!')
        for adres in ['/uzytkownicy/', '/platnosci/']:
            r = self.client.get(adres)
            self.assertEqual(r.status_code, 403, f'{adres} wpuszcza nauczyciela')
        # nauczyciel nie ma zakladki Lekcje (to kalendarze uczniow pracownika)
        # nauczyciel MA zakładkę Lekcje — widzi w niej tylko swoich uczniów
        r = self.client.get('/lekcje/')
        self.assertEqual(r.status_code, 200, 'lekcje nie wpuszcza nauczyciela')

    def test_zwykly_pracownik_bez_konta_nauczyciela(self):
        self.client.force_login(self.admin)
        dane = {
            'username': 'prac',
            'email': 'p@f.pl',
            'haslo1': 'M0taneHaslo!',
            'haslo2': 'M0taneHaslo!',
            'rola': 'pracownik',
        }
        r = self.client.post('/uzytkownicy/', dane, follow=True)
        self.assertContains(r, 'został dodany')
        user = Pracownik.objects.get(username='prac')
        self.assertIsNone(user.nauczyciel)


class KreatorDyspozycyjnoscTests(TestCase):
    """Kreator dyspozycyjności: pierwsze logowanie nauczyciela i generowanie slotów."""

    def setUp(self):
        self.admin = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        # konto nauczyciela tworzone przez zakladke Uzytkownicy
        self.client.force_login(self.admin)
        dane = {
            'username': 'j.kowalski',
            'first_name': 'Jan',
            'last_name': 'Kowalski',
            'email': 'jan@k.pl',
            'haslo1': 'M0taneHaslo!',
            'haslo2': 'M0taneHaslo!',
            'rola': 'nauczyciel',
            'w_przedmiot': ['matematyka'],
            'w_poziom': ['srednia podstawa'],
            'w_stawka': ['50'],
        }
        self.client.post('/uzytkownicy/', dane, follow=True)
        self.nauczyciel = Pracownik.objects.get(username='j.kowalski').nauczyciel

    def test_nauczyciel_od_razu_trafia_do_kalendarza(self):
        self.client.logout()
        self.client.login(username='j.kowalski', password='M0taneHaslo!')
        r = self.client.get('/pulpit/', follow=True)
        # wlasny kalendarz, bez przymusowego kreatora dyspozycyjnosci
        self.assertContains(r, 'Mój tydzień')
        self.assertNotContains(r, 'Twoja dyspozycyjność')


    def test_kreator_generuje_sloty_z_przerwa(self):
        # kreator formularzowy zastąpiony: godziny dodaje się w "Mój tydzień" (klik/plusik),
        # przerwę ustawia się przyciskiem tygodnia (AJAX). Sprawdzamy nową drogę:
        self.client.logout()
        self.client.login(username='j.kowalski', password='M0taneHaslo!')
        # strona tygodnia działa
        r = self.client.get('/moj-tydzien/')
        self.assertContains(r, 'Mój tydzień')
        # dodanie godziny 16:00 przez AJAX (jak kliknięcie plusika)
        r = self.client.post(
            f'/nauczyciele/{self.nauczyciel.id}/kalendarz/dane/',
            {'dzien': '2026-10-01', 'akcja': 'przelacz_godzine', 'godzina': '16:00'},
        )
        self.assertEqual(r.status_code, 200)
        dane = r.json()
        self.assertTrue(any(k['zaznaczony'] for k in dane['kafelki']))
        # ustawienie przerwy 10 min przez AJAX (przycisk "10 min" w tygodniu)
        r = self.client.post(
            f'/nauczyciele/{self.nauczyciel.id}/kalendarz/dane/',
            {'dzien': '2026-10-01', 'akcja': 'zapisz_przerwe', 'przerwa': '10'},
        )
        self.assertEqual(r.json()['przerwa'], 10)
        sloty = list(Dostepnosc.objects.filter(nauczyciel=self.nauczyciel))
        # NOWE: 16:00 od razu co tydzień do 31.08.2027 — 48 kopii tej samej godziny, wszystkie cykliczne
        self.assertEqual(len(sloty), 48)
        self.assertEqual(sorted(d.godzina_od.strftime('%H:%M') for d in sloty), ['16:00'] * 48)
        self.assertTrue(all(d.cykliczny and d.cykliczny_okres == 1 for d in sloty))

    def test_usun_zakres_usuwa_tylko_wybrany_dzien_cykl_zostaje(self):
        # zakres zaznaczony przez korepetytora jest od razu cykliczny co tydzień do 31.08,
        # a ✖ (usun_zakres) kasuje godziny tylko z jednego, wybranego dnia
        self.client.logout()
        self.client.login(username='j.kowalski', password='M0taneHaslo!')
        adres = f'/nauczyciele/{self.nauczyciel.id}/kalendarz/dane/'
        r = self.client.post(adres, {'dzien': '2026-10-01', 'akcja': 'zaznacz_zakres', 'od': '14:00', 'do': '16:00'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('co tydzień', r.json()['komunikat'])
        # 4 sloty (14:00–16:00) w dniu 01.10 + 4 sloty co tydzień do 26.08.2027 (47 tygodni) = 192
        self.assertEqual(Dostepnosc.objects.filter(nauczyciel=self.nauczyciel).count(), 192)
        r = self.client.post(adres, {'dzien': '2026-10-08', 'akcja': 'usun_zakres', 'od': '14:00', 'do': '16:00'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('tylko w dniu', r.json()['komunikat'])
        self.assertEqual(Dostepnosc.objects.filter(nauczyciel=self.nauczyciel).count(), 188)
        self.assertFalse(Dostepnosc.objects.filter(nauczyciel=self.nauczyciel, data_od=date(2026, 10, 8)).exists())
        self.assertEqual(Dostepnosc.objects.filter(nauczyciel=self.nauczyciel, data_od=date(2026, 10, 1)).count(), 4)
        self.assertEqual(Dostepnosc.objects.filter(nauczyciel=self.nauczyciel, data_od=date(2026, 10, 15)).count(), 4)

    def test_pracownik_nie_wejdzie_do_cudzego_kalendarza(self):
        Dostepnosc.objects.create(
            nauczyciel=self.nauczyciel,
            data_od='2026-10-01', data_do='2026-10-01',
            godzina_od='16:00', godzina_do='16:30',
        )
        pracownik = Pracownik.objects.create_user('prac', 'p@f.pl', 'Sup3rHaslo!')
        self.client.force_login(pracownik)
        # osoba umawiająca nie widzi cudzego kalendarza (403)
        r = self.client.get(f'/moj-tydzien/{self.nauczyciel.id}/')
        self.assertEqual(r.status_code, 403)
        # i nie może dopisać godzin przez AJAX
        r = self.client.post(
            f'/nauczyciele/{self.nauczyciel.id}/kalendarz/dane/',
            {'dzien': '2026-10-01', 'akcja': 'przelacz_godzine', 'godzina': '17:00'},
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Dostepnosc.objects.filter(nauczyciel=self.nauczyciel).count(), 1)

    def test_koordynator_nie_wejdzie_do_cudzego_kalendarza(self):
        k = Pracownik.objects.create_user('koord1', 'k@f.pl', 'Sup3rHaslo!')
        k.stanowisko = 'koordynator'
        k.save()
        self.client.force_login(k)
        r = self.client.get(f'/moj-tydzien/{self.nauczyciel.id}/')
        self.assertEqual(r.status_code, 403)

    def test_szef_widzi_kalendarz_nauczyciela(self):
        Dostepnosc.objects.create(
            nauczyciel=self.nauczyciel,
            data_od='2026-10-01', data_do='2026-10-01',
            godzina_od='16:00', godzina_do='16:30',
        )
        self.client.force_login(self.admin)
        r = self.client.get(f'/moj-tydzien/{self.nauczyciel.id}/?rok=2026&miesiac=10&dzien=1')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '16:00')

    def _adres(self):
        return f'/nauczyciele/{self.nauczyciel.id}/kalendarz/dane/'

    def _uczen(self):
        return Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime(2026, 10, 5, 16)),
            przedmiot='matematyka', poziom='rozszerzony',
            imie_ucznia='Kasia', nazwisko_rodzica='Nowak', rodzic='Ewa Nowak',
            telefon_glowny='600100200', email_glowny='e@f.pl',
            telefon_dodatkowy='600300400', email_dodatkowy='r@f.pl',
            korepetytor='Jan Kowalski', umowil=self.admin,
        )

    def test_zakres_od_razu_cykliczny_co_tydzien_do_31_08(self):
        self.client.force_login(self.admin)
        r = self.client.post(self._adres(), {
            'dzien': '2026-10-05', 'akcja': 'zaznacz_zakres', 'od': '14:00', 'do': '16:00',
        })
        self.assertEqual(r.status_code, 200)
        self.assertIn('co tydzień', r.json()['komunikat'])
        sloty = Dostepnosc.objects.filter(nauczyciel=self.nauczyciel)
        self.assertTrue(sloty.exists())
        self.assertTrue(all(t.cykliczny and t.cykliczny_okres == 1 for t in sloty))
        self.assertTrue(sloty.filter(data_od='2026-10-12').exists())   # +7 dni
        ostatni = max(t.data_od for t in sloty)
        self.assertLessEqual(ostatni, date(2027, 8, 31))  # do końca sierpnia następnego roku

class LekcjeCykliczneTests(TestCase):
    """Lekcje cykliczne + widoczność uczniów per pracownik."""

    def setUp(self):
        self.admin = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.pracownik = Pracownik.objects.create_user('prac', 'p@f.pl', 'Sup3rHaslo!')
        self.uczen = Uczen.objects.create(
            pierwsza_lekcja='2026-10-06T17:00',
            przedmiot='matematyka', poziom='srednia podstawa',
            imie_ucznia='Kasia', nazwisko_rodzica='Nowak', rodzic='Ewa Nowak',
            telefon_glowny='600100200', email_glowny='ewa@f.pl',
            telefon_dodatkowy='600300400', email_dodatkowy='r@f.pl',
            korepetytor='Jan Kowalski', notatka_od_rodzica='ok',
            kto_umowil='Jan Kowalski', umowil=self.pracownik,
        )
        self.nauczyciel = Nauczyciel.objects.create(imie='Jan', nazwisko='Kowalski', email='jan@n.pl')
        d = date(2026, 1, 1)
        while d <= date(2027, 12, 31):
            Dostepnosc.objects.create(
                nauczyciel=self.nauczyciel, data_od=d, data_do=d,
                godzina_od=time(16, 0), godzina_do=time(20, 0),
            )
            d += timedelta(days=1)

    def test_pracownik_widzi_tylko_swoich_uczniow(self):
        Uczen.objects.create(
            pierwsza_lekcja='2026-10-07T17:00',
            przedmiot='fizyka', poziom='srednia podstawa',
            imie_ucznia='Ola', nazwisko_rodzica='Wiśniewska', rodzic='Ala Wiśniewska',
            telefon_glowny='600100200', email_glowny='ola@f.pl',
            telefon_dodatkowy='600300400', email_dodatkowy='r@f.pl',
            korepetytor='Jan Kowalski', notatka_od_rodzica='ok',
            kto_umowil='Admin', umowil=self.admin,
        )
        self.client.force_login(self.pracownik)
        r = self.client.get('/lekcje/')
        self.assertContains(r, 'Kasia')
        self.assertNotContains(r, 'Ola')

    def test_admin_widzi_wszystkich(self):
        self.client.force_login(self.admin)
        r = self.client.get('/lekcje/')
        self.assertContains(r, 'Kasia')

    def test_pracownik_umawia_lekcje_cykliczna(self):
        self.client.force_login(self.pracownik)
        dane = {
            'slot_dzien': ['1'],
            'slot_godzina': ['16:00'],
            'okres': 'rok',
            'link_meet': 'https://meet.google.com/abc-defg-hij',
        }
        r = self.client.post(f'/uczniowie/{self.uczen.id}/lekcja-cykliczna/', dane, follow=True)
        self.assertContains(r, 'Stałe lekcje umówione')
        # lekcje: wszystkie w tym miesiacu, wszystkie we wtorki
        lekcje = Lekcja.objects.filter(uczen=self.uczen)
        self.assertTrue(lekcje.exists())
        dzis = date.today()
        for lekcja in lekcje:
            dz = lekcja.data.date()
            koniec_roku = date(dzis.year + 1, 6, 30) if dzis.month >= 7 else date(dzis.year, 6, 30)
            self.assertLessEqual(dz, koniec_roku)
            self.assertGreaterEqual(dz, dzis)
            self.assertEqual(dz.weekday(), 1)  # 1 = wtorek
        # link Meet zapisany przy uczniu, ale mail nie idzie od razu —
        # rodzic dostanie go w przypomnieniu 60 min przed lekcją
        self.assertEqual(len(mail.outbox), 0)
        self.uczen.refresh_from_db()
        self.assertEqual(self.uczen.link_meet, 'https://meet.google.com/abc-defg-hij')
        # przypomnienie 60 min przed lekcją zawiera link Meet
        pierwsza = Lekcja.objects.filter(uczen=self.uczen).order_by('data').first()
        pierwsza.data = timezone.now() + timedelta(minutes=30)
        pierwsza.save()
        call_command('wyslij_przypomnienia')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('https://meet.google.com/abc-defg-hij', mail.outbox[0].body)
        # blokady u korepetytora: tyle samo co lekcji
        # blokada = slot 60 min (16:00-17:00); w setUp sa tylko zakresy 16:00-20:00
        blokady = Dostepnosc.objects.filter(nauczyciel=self.nauczyciel, godzina_od=time(16, 0), godzina_do=time(17, 0))
        self.assertEqual(blokady.count(), lekcje.count())
    def test_okres_rok_startujacy_w_wakacje_lipiec_sierpien(self):
        # start w lipcu: "do końca roku szkolnego" musi kończyć się 30.06.2027,
        # a nie 30.06.2026 (data z przeszłości — wtedy powstawało 0 lekcji)
        self.client.force_login(self.pracownik)
        start = date(2026, 7, 15)
        dane = {
            'data_poczatkowa': start.isoformat(),
            'slot_dzien': [str(start.weekday())],
            'slot_godzina': ['16:00'],
            'okres': 'rok',
            'link_meet': '',
        }
        r = self.client.post(f'/uczniowie/{self.uczen.id}/lekcja-cykliczna/', dane, follow=True)
        self.assertContains(r, 'Stałe lekcje umówione')
        lekcje = Lekcja.objects.filter(uczen=self.uczen)
        self.assertTrue(lekcje.exists())
        daty = [timezone.localtime(l.data).date() for l in lekcje]
        self.assertGreaterEqual(min(daty), start)
        self.assertLessEqual(max(daty), date(2027, 6, 30))
        self.assertTrue(all(d.weekday() == start.weekday() for d in daty))
    def test_usuniecie_ucznia_zwalnia_blokady_wszystkich_korepetytorow(self):
        # uczeń z przedmiotem dodatkowym u INNEGO nauczyciela — usunięcie musi
        # zwolnić godziny OBU nauczycieli i usunąć ucznia z bazy (cascade)
        anna = Nauczyciel.objects.create(imie='Anna', nazwisko='Nowak', email='a@n.pl')
        karta = PrzedmiotUcznia.objects.create(
            uczen=self.uczen, przedmiot='fizyka', poziom='podstawowka', korepetytor='Anna Nowak',
        )
        dzis = date.today()
        Lekcja.objects.create(
            uczen=self.uczen,
            data=timezone.make_aware(datetime.combine(dzis + timedelta(days=1), time(17, 0))),
        )
        Lekcja.objects.create(
            uczen=self.uczen,
            data=timezone.make_aware(datetime.combine(dzis + timedelta(days=2), time(18, 0))),
            przedmiot_karta=karta,
        )
        for n, d, g in [
            (self.nauczyciel, dzis + timedelta(days=1), time(17, 0)),
            (anna, dzis + timedelta(days=2), time(18, 0)),
        ]:
            Dostepnosc.objects.create(
                nauczyciel=n, data_od=d, data_do=d, godzina_od=g,
                godzina_do=(datetime.combine(date(2000, 1, 1), g) + timedelta(minutes=60)).time(),
            )
        self.client.force_login(self.admin)
        r = self.client.post(f'/uczniowie/{self.uczen.id}/usun/', follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Uczen.objects.filter(pk=self.uczen.id).exists())
        self.assertEqual(Lekcja.objects.filter(uczen_id=self.uczen.id).count(), 0)
        self.assertEqual(Dostepnosc.objects.filter(nauczyciel=self.nauczyciel, godzina_od=time(17, 0)).count(), 0)
        self.assertEqual(Dostepnosc.objects.filter(nauczyciel=anna, godzina_od=time(18, 0)).count(), 0)

    def test_cykliczna_nie_tworzy_nachodzacych_lekcji(self):
        # dwa sloty na ten sam dzień (17:00 i 17:00) — drugi koliduje z pierwszym
        # (stary bug: oba przechodziły, bo walidacja omijała lekcje tego samego ucznia)
        self.client.force_login(self.pracownik)
        dzis = date.today()
        r = self.client.post(f'/uczniowie/{self.uczen.id}/lekcja-cykliczna/', {
            'okres': 'miesiac',
            'data_poczatkowa': dzis.isoformat(),
            'slot_dzien': [str(dzis.weekday()), str(dzis.weekday())],
            'slot_godzina': ['16:00', '16:00'],
            'link_meet': '',
        }, follow=True)
        self.assertContains(r, 'umówione')
        self.assertNotContains(r, 'Pominięto')
        qs = Lekcja.objects.filter(uczen=self.uczen)
        self.assertEqual(qs.count(), qs.values('data').distinct().count(), 'nie może być duplikatów terminów')
        godziny = {timezone.localtime(l.data).strftime('%H:%M') for l in qs}
        self.assertEqual(godziny, {'16:00'})
    def test_cykliczna_dla_przedmiotu_dodatkowego_przez_karta(self):
        # karta przedmiotu dodatkowego u INNEGO nauczyciela + cykliczna przez ?karta=ID:
        # lekcje powstają z przedmiot_karta i blokują grafik tego nauczyciela
        anna = Nauczyciel.objects.create(imie='Anna', nazwisko='Nowak', email='a@n.pl')
        karta = PrzedmiotUcznia.objects.create(
            uczen=self.uczen, przedmiot='fizyka', poziom='podstawowka', korepetytor='Anna Nowak',
        )
        dzis = date.today()
        d = dzis
        while d <= date(2027, 6, 30):
            if d.weekday() == dzis.weekday():
                Dostepnosc.objects.create(
                    nauczyciel=anna, data_od=d, data_do=d,
                    godzina_od=time(16, 0), godzina_do=time(20, 0),
                )
            d += timedelta(days=1)
        self.client.force_login(self.pracownik)
        r = self.client.post(
            f'/uczniowie/{self.uczen.id}/lekcja-cykliczna/?karta={karta.id}',
            {
                'okres': 'miesiac',
                'data_poczatkowa': dzis.isoformat(),
                'slot_dzien': [str(dzis.weekday())],
                'slot_godzina': ['16:00'],
                'link_meet': '',
            },
            follow=True,
        )
        self.assertEqual(r.status_code, 200)
        lekcje = Lekcja.objects.filter(uczen=self.uczen, przedmiot_karta=karta)
        self.assertTrue(lekcje.exists())
        self.assertTrue(Dostepnosc.objects.filter(nauczyciel=anna, godzina_od=time(16, 0)).exists())
        self.assertFalse(Lekcja.objects.filter(uczen=self.uczen, przedmiot_karta__isnull=True).exists())

    def test_ajax_godziny_zwraca_date_i_dni_pracy(self):
        self.client.force_login(self.pracownik)
        dzis = date.today()
        r = self.client.get(f'/korepetytorzy/dane/?nauczyciel={self.nauczyciel.id}&data={dzis.isoformat()}')
        dane = r.json()
        self.assertTrue(dane['godziny'])
        self.assertEqual(dane['data'], dzis.isoformat())
        self.assertIn(dzis.weekday(), dane['dni_pracy'])

    def test_cykliczna_bez_wolnych_godzin_dostaje_czerwony_blad_i_zero_lekcji(self):
        self.client.force_login(self.pracownik)
        dzis = date.today()
        r = self.client.post(f'/uczniowie/{self.uczen.id}/lekcja-cykliczna/', {
            'okres': 'miesiac',
            'data_poczatkowa': dzis.isoformat(),
            'slot_dzien': [str(dzis.weekday())],
            'slot_godzina': ['23:50'],
            'link_meet': '',
        }, follow=True)
        self.assertContains(r, 'Nie wpisałem żadnego terminu')
        self.assertEqual(Lekcja.objects.filter(uczen=self.uczen).count(), 0)

    def test_pracownik_nie_umowi_cyklicznej_cudzemu_uczniowi(self):
        obcy = Uczen.objects.create(
            pierwsza_lekcja='2026-10-07T17:00',
            przedmiot='fizyka', poziom='srednia podstawa',
            imie_ucznia='Ola', nazwisko_rodzica='Wiśniewska', rodzic='Ala Wiśniewska',
            telefon_glowny='600100200', email_glowny='ola@f.pl',
            telefon_dodatkowy='600300400', email_dodatkowy='r@f.pl',
            korepetytor='Jan Kowalski', notatka_od_rodzica='ok',
            kto_umowil='Admin', umowil=self.admin,
        )
        self.client.force_login(self.pracownik)
        r = self.client.get(f'/uczniowie/{obcy.id}/lekcja-cykliczna/')
        self.assertEqual(r.status_code, 403)

    def test_pracownik_widzi_zakladke_lekcje(self):
        self.client.force_login(self.pracownik)
        r = self.client.get('/lekcje/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Kasia')
        # stary kalendarz lekcji ucznia usunięty — adres daje teraz 404
        r = self.client.get(f'/lekcje/{self.uczen.id}/kalendarz/')
        self.assertEqual(r.status_code, 404)

    def test_dni_pracy_tylko_z_grafikiem_nauczyciela(self):
        # AJAX dni_pracy pokazuje tylko dni tygodnia, w ktorych korepetytor ma godziny
        n2 = Nauczyciel.objects.create(imie='Ewa', nazwisko='Lis', email='e2@f.pl')
        dzis = date.today()
        d = dzis + timedelta(days=1)
        while d.weekday() != 0:
            d += timedelta(days=1)
        for i in range(2):
            dd = d + timedelta(weeks=i)
            Dostepnosc.objects.create(
                nauczyciel=n2, data_od=dd, data_do=dd,
                godzina_od=time(16, 0), godzina_do=time(20, 0),
            )
        self.client.force_login(self.pracownik)
        r = self.client.get(f'/korepetytorzy/dane/?nauczyciel={n2.id}&data={dzis.isoformat()}')
        self.assertEqual(r.json()['dni_pracy'], [0])  # tylko poniedzialki

    def test_strona_cyklicznej_chowa_dni_bez_grafika(self):
        self.client.force_login(self.pracownik)
        r = self.client.get(f'/uczniowie/{self.uczen.id}/lekcja-cykliczna/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'slot_dzien')
        # JS pobiera dni_pracy i chowa dni tygodnia, w ktorych korepetytor nie pracuje
        self.assertContains(r, 'dni_pracy')

    def test_lekcje_pokazuja_terminy_cyklicznych(self):
        # w zakładce Lekcje przy zajęciach cyklicznych widać najbliższe terminy (kiedy lekcja)
        self.uczen.zajecia_cykliczne = True
        self.uczen.save(update_fields=['zajecia_cykliczne'])
        d = date.today() + timedelta(days=3)
        Lekcja.objects.create(uczen=self.uczen, data=timezone.make_aware(datetime.combine(d, time(17, 0))))
        self.client.force_login(self.pracownik)
        r = self.client.get('/lekcje/')
        self.assertContains(r, 'Zajęcia cykliczne')
        self.assertContains(r, f'{d.day:02d}.{d.month:02d} 17:00')

class NauczycielTylkoKalendarzTests(TestCase):
    """Konto nauczyciela widzi wyłącznie swój kalendarz — bez zakładki Uczniowie."""

    def setUp(self):
        self.admin = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.client.force_login(self.admin)
        dane = {
            'username': 'j.kowalski',
            'first_name': 'Jan',
            'last_name': 'Kowalski',
            'email': 'jan@k.pl',
            'haslo1': 'M0taneHaslo!',
            'haslo2': 'M0taneHaslo!',
            'rola': 'nauczyciel',
            'w_przedmiot': ['matematyka'],
            'w_poziom': ['srednia podstawa'],
            'w_stawka': ['50'],
        }
        self.client.post('/uzytkownicy/', dane, follow=True)
        self.nauczyciel_user = Pracownik.objects.get(username='j.kowalski')

    def test_nauczyciel_nie_ma_wstepu_do_uczniow(self):
        self.client.force_login(self.nauczyciel_user)
        r = self.client.get('/uczniowie/')
        self.assertEqual(r.status_code, 403, 'uczniowie wpuszcza nauczyciela')

    def test_nauczyciel_widzi_w_menu_tylko_swoj_kalendarz(self):
        self.client.force_login(self.nauczyciel_user)
        r = self.client.get('/pulpit/', follow=True)
        html = r.content.decode()
        self.assertIn('Mój kalendarz', html)
        self.assertNotIn(f'>Uczniowie</a>', html)

    def test_pracownik_widzi_uczniow_normalnie(self):
        pracownik = Pracownik.objects.create_user('prac', 'p@f.pl', 'Sup3rHaslo!')
        self.client.force_login(pracownik)
        r = self.client.get('/uczniowie/')
        self.assertEqual(r.status_code, 200)

class StawkiPrzedmiotowTests(TestCase):
    """Stawki per przedmiot przy tworzeniu konta nauczyciela."""

    def setUp(self):
        self.admin = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')

    def test_nauczyciel_ze_stawkami_per_przedmiot(self):
        self.client.force_login(self.admin)
        dane = {
            'username': 'a.nowak',
            'first_name': 'Anna',
            'last_name': 'Nowak',
            'email': 'anna@n.pl',
            'haslo1': 'M0taneHaslo!',
            'haslo2': 'M0taneHaslo!',
            'rola': 'nauczyciel',
            'w_przedmiot': ['angielski', 'matematyka'],
            'w_poziom': ['srednia podstawa', 'srednia rozszerzenie'],
            'w_stawka': ['40', '50'],
        }
        r = self.client.post('/uzytkownicy/', dane, follow=True)
        self.assertContains(r, 'został dodany')
        user = Pracownik.objects.get(username='a.nowak')
        wiersze = {(p.przedmiot, p.poziom): p for p in user.nauczyciel.przedmioty.all()}
        self.assertEqual(str(wiersze[('angielski', 'srednia podstawa')].stawka), '40.00')
        self.assertEqual(str(wiersze[('matematyka', 'srednia rozszerzenie')].stawka), '50.00')

    def test_nauczyciel_bez_przedmiotu_odrzucony(self):
        self.client.force_login(self.admin)
        dane = {
            'username': 'b.blad',
            'email': 'b@f.pl',
            'haslo1': 'M0taneHaslo!',
            'haslo2': 'M0taneHaslo!',
            'rola': 'nauczyciel',
        }
        r = self.client.post('/uzytkownicy/', dane)
        self.assertContains(r, 'min. jeden przedmiot')
        self.assertFalse(Pracownik.objects.filter(username='b.blad').exists())

class StaleLekcjeMultiSlotTests(TestCase):
    """Stałe lekcje: wiele terminów tygodniowo i preset do końca roku szkolnego."""

    def setUp(self):
        self.admin = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.pracownik = Pracownik.objects.create_user('prac', 'p@f.pl', 'Sup3rHaslo!')
        self.nauczyciel = Nauczyciel.objects.create(imie='Jan', nazwisko='Kowalski', email='j@n.pl')
        d = date(2026, 1, 1)
        while d <= date(2027, 12, 31):
            Dostepnosc.objects.create(
                nauczyciel=self.nauczyciel, data_od=d, data_do=d,
                godzina_od=time(16, 0), godzina_do=time(20, 0),
            )
            d += timedelta(days=1)

        self.uczen = Uczen.objects.create(
            pierwsza_lekcja='2026-10-06T17:00',
            przedmiot='matematyka', poziom='srednia podstawa',
            imie_ucznia='Kasia', nazwisko_rodzica='Nowak', rodzic='Ewa Nowak',
            telefon_glowny='600100200', email_glowny='ewa@f.pl',
            telefon_dodatkowy='600300400', email_dodatkowy='r@f.pl',
            korepetytor='Jan Kowalski', notatka_od_rodzica='ok',
            kto_umowil='Prac Testowy', umowil=self.pracownik,
        )

    def test_dwa_terminy_tygodniowo(self):
        self.client.force_login(self.pracownik)
        dane = {
            'slot_dzien': ['1', '3'],      # wtorek + czwartek
            'slot_godzina': ['16:00', '17:05'],
            'okres': 'rok',
        }
        r = self.client.post(f'/uczniowie/{self.uczen.id}/lekcja-cykliczna/', dane, follow=True)
        self.assertContains(r, 'Stałe lekcje umówione')
        # lekcje: wtorki 17:20 i czwartki 19:15, w tym miesiacu
        lekcje = Lekcja.objects.filter(uczen=self.uczen)
        self.assertTrue(lekcje.exists())
        dzis = date.today()
        for lekcja in lekcje:
            dz = lekcja.data.date()
            koniec_roku = date(dzis.year + 1, 6, 30) if dzis.month >= 7 else date(dzis.year, 6, 30)
            self.assertLessEqual(dz, koniec_roku)
            self.assertGreaterEqual(dz, dzis)
            self.assertIn(dz.weekday(), [1, 3])  # wtorek (1) albo czwartek (3)
        # godziny: obie obecne
        from django.utils import timezone as _tz
        godziny = set(
            _tz.localtime(l.data).strftime('%H:%M') for l in lekcje
        )
        self.assertIn('16:00', godziny)
        self.assertIn('17:05', godziny)
        # blokady u korepetytora na obie godziny
        self.assertTrue(
            Dostepnosc.objects.filter(nauczyciel=self.nauczyciel, godzina_od='16:00').exists()
        )
        self.assertTrue(
            Dostepnosc.objects.filter(nauczyciel=self.nauczyciel, godzina_od='17:05').exists()
        )

    def test_preset_rok_szkolny(self):
        self.client.force_login(self.pracownik)
        dane = {
            'slot_dzien': ['1'],
            'slot_godzina': ['16:00'],
            'okres': 'rok',
        }
        r = self.client.post(f'/uczniowie/{self.uczen.id}/lekcja-cykliczna/', dane, follow=True)
        self.assertContains(r, 'Stałe lekcje umówione')
        from django.utils import timezone as _tz
        ostatnia = Lekcja.objects.filter(uczen=self.uczen).order_by('-data').first()
        lok = _tz.localtime(ostatnia.data).date()
        self.assertEqual(lok.month, 6)
        self.assertLessEqual(lok.day, 30)
        from datetime import date as _date, timedelta as _td
        self.assertEqual(lok.weekday(), 1)
        koniec = _date(lok.year, 6, 30)
        while koniec.weekday() != 1:
            koniec -= _td(days=1)
        self.assertEqual(lok, koniec)
    def test_zawsze_i_data_z_przeszlosci(self):
        self.client.force_login(self.pracownik)
        dzis = date.today()
        stara = dzis - timedelta(days=21)
        dane = {
            'slot_dzien': ['1'],
            'slot_godzina': ['16:00'],
            'okres': 'zawsze',
            'data_poczatkowa': stara.isoformat(),
        }
        r = self.client.post(f'/uczniowie/{self.uczen.id}/lekcja-cykliczna/', dane, follow=True)
        self.assertContains(r, 'Stałe lekcje umówione')
        lekcje = Lekcja.objects.filter(uczen=self.uczen).order_by('data')
        self.assertTrue(lekcje.exists())
        pierwsza = timezone.localtime(lekcje.first().data).date()
        ostatnia = timezone.localtime(lekcje.last().data).date()
        # pierwsza lekcja wpada w przeszlosc (dokladnie pierwszy wtorek od podanej daty)
        self.assertLess(pierwsza, dzis)
        self.assertEqual(pierwsza.weekday(), 1)
        # horyzont "zawsze": ok. 90 dni od daty poczatkowej
        self.assertGreater(ostatnia, dzis + timedelta(days=60))
        # po odswiezeniu (symulacja nocy z CRON) lekcje dalej sa przedluzane
        call_command('odnow_cykliczne')
        self.assertTrue(Lekcja.objects.filter(uczen=self.uczen).exists())

class NoweFunkcjeTests(TestCase):
    """Testy nowych funkcji: odwołane zajęcia, rozdzielone poziomy, odnawianie cyklicznych."""

    def setUp(self):
        self.user = Pracownik.objects.create_superuser('szef', 's@f.pl', 'Sup3rHaslo!')
        self.nauczyciel = Nauczyciel.objects.create(imie='Jan', nazwisko='Kowalski', email='j@k.pl')
        self.uczen = Uczen.objects.create(
            pierwsza_lekcja=timezone.now() + timedelta(days=1),
            przedmiot='matematyka', poziom='studia', szkola='X',
            imie_ucznia='Ala', nazwisko_rodzica='Testowa', rodzic='Ewa',
            telefon_glowny='123', email_glowny='a@b.pl',
            korepetytor='Jan Kowalski', umowil=self.user,
        )

    def test_odwolana_lekcja_nie_blokuje_kafelek(self):
        data_lekcji = timezone.now() + timedelta(days=2)
        lekcja = Lekcja.objects.create(uczen=self.uczen, data=data_lekcji)
        lekcja.odwolana = True
        lekcja.save(update_fields=['odwolana'])
        po = kolizja_lekcji_nauczyciela(
            self.nauczyciel,
            timezone.localtime(data_lekcji).date(),
            timezone.localtime(data_lekcji).replace(tzinfo=None),
        )
        self.assertFalse(po, 'odwołana lekcja nie może blokować terminu nauczyciela')

    def test_przypomnienie_nie_idzie_do_archiwum(self):
        lekcja = Lekcja.objects.create(
            uczen=self.uczen,
            data=timezone.now() + timedelta(minutes=30),
        )
        self.uczen.zakonczona = True
        self.uczen.save(update_fields=['zakonczona'])
        call_command('wyslij_przypomnienia')
        self.assertEqual(len(mail.outbox), 0, 'uczeń z archiwum nie może dostać przypomnienia')
        self.assertTrue(PrzypomnienieLekcji.objects.filter(lekcja=lekcja).exists())

    def test_poziomy_rozszerzenie_i_studia_osobno(self):
        from konta.forms import POZIOMY, POZIOM_UCZEN_NA_NAUCZYCIELA
        klucze = [k for k, _ in POZIOMY]
        self.assertIn('srednia rozszerzenie', klucze)
        self.assertIn('studia', klucze)
        self.assertEqual(POZIOM_UCZEN_NA_NAUCZYCIELA['studia'], 'studia')
        self.assertEqual(POZIOM_UCZEN_NA_NAUCZYCIELA['srednia rozszerzenie'], 'srednia rozszerzenie')
        self.assertNotIn('srednia rozszerzenie + studia', POZIOM_UCZEN_NA_NAUCZYCIELA)
        

    def test_odnowienie_cyklicznych_dopisuje_lekcje(self):
        self.uczen.zajecia_cykliczne = True
        self.uczen.save(update_fields=['zajecia_cykliczne'])
        d = timezone.now().replace(hour=17, minute=0, second=0, microsecond=0)
        while d.weekday() != 4:
            d -= timedelta(days=1)
        for i in range(3):
            Lekcja.objects.create(uczen=self.uczen, data=timezone.make_aware(d.replace(tzinfo=None) - timedelta(weeks=i)))
        przed = Lekcja.objects.filter(uczen=self.uczen, data__gte=timezone.now()).count()
        call_command('odnow_cykliczne')
        po = Lekcja.objects.filter(uczen=self.uczen, data__gte=timezone.now()).count()
        self.assertGreater(po, przed, 'komenda powinna dopisać przyszłe cykliczne lekcje')
        for lekcja in Lekcja.objects.filter(uczen=self.uczen, data__gte=timezone.now()):
            self.assertEqual(timezone.localtime(lekcja.data).weekday(), 4)

    def test_przywrocenie_z_archiwum_odtwarza_blokady(self):
        lekcja = Lekcja.objects.create(
            uczen=self.uczen,
            data=timezone.make_aware(
                (timezone.now() + timedelta(days=7)).replace(hour=17, minute=0, second=0, microsecond=0, tzinfo=None)
            ),
        )
        self.client.force_login(self.user)
        self.client.post(f'/uczniowie/{self.uczen.id}/zakoncz/', follow=True)
        self.assertEqual(Dostepnosc.objects.filter(nauczyciel=self.nauczyciel).count(), 0)
        self.client.post(f'/uczniowie/{self.uczen.id}/przywroc/', follow=True)
        self.assertEqual(Dostepnosc.objects.filter(nauczyciel=self.nauczyciel).count(), 1)

    def test_raport_nie_usuwa_uczniow(self):
        self.uczen.zaplacone = True
        self.uczen.save(update_fields=['zaplacone'])
        self.client.force_login(self.user)
        self.client.post('/platnosci/', {'wyslij_raport': '1', 'email_raportu': 'szef@f.pl'}, follow=True)
        self.assertTrue(Uczen.objects.filter(id=self.uczen.id).exists(), 'uczeń musi zostać w bazie po raporcie')
        self.assertFalse(Uczen.objects.get(id=self.uczen.id).zaplacone)





class ResetHaslaLinkTests(TestCase):
    """Link do ustawienia hasła: ważny 3 dni, potem osobna strona „czas minął”."""

    def setUp(self):
        from konta.tokens import token_generator_resetu
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode
        self.token_generator = token_generator_resetu
        self.user = Pracownik.objects.create_user(
            'a.nowak', 'anna@firma.pl', 'Sup3rHaslo!',
        )
        self.uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        self.token = token_generator_resetu.make_token(self.user)

    def _url(self, token):
        from django.urls import reverse
        return reverse('reset_hasla_potwierdz', kwargs={'uidb64': self.uid, 'token': token})

    def _stary_token(self, dni=4):
        from datetime import datetime
        stary_ts = self.token_generator._num_seconds(datetime.now()) - dni * 24 * 60 * 60
        return self.token_generator._make_token_with_timestamp(
            self.user, stary_ts, self.token_generator.secret
        )

    def test_wazny_link_pokazuje_formularz(self):
        r = self.client.get(self._url(self.token), follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Ustaw nowe hasło')

    def test_waznym_linkiem_mozna_zmienic_haslo(self):
        self.client.get(self._url(self.token))  # zapamiętanie tokena w sesji + redirect
        url_set = self._url('set-password')
        r = self.client.post(url_set, {
            'new_password1': 'NoweHaslo123!',
            'new_password2': 'NoweHaslo123!',
        }, follow=True)
        self.assertContains(r, 'Gotowe')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NoweHaslo123!'))

    def test_przeterminowany_link_przekierowuje_na_czas_minal(self):
        from django.urls import reverse
        r = self.client.get(self._url(self._stary_token()))
        self.assertRedirects(r, reverse('reset_hasla_wygasl'))

    def test_przeterminowany_link_przy_post_tez_przekierowuje(self):
        from django.urls import reverse
        r = self.client.post(self._url(self._stary_token()), {
            'new_password1': 'NoweHaslo123!',
            'new_password2': 'NoweHaslo123!',
        })
        self.assertRedirects(r, reverse('reset_hasla_wygasl'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Sup3rHaslo!'), 'hasło nie może się zmienić po terminie')

    def test_falszywy_link_pokazuje_zwykly_blad(self):
        r = self.client.get(self._url('abc-xyz'))
        self.assertEqual(r.status_code, 200)

    def test_link_dziala_mimo_ze_ktos_sie_zalogowal(self):
        # logowanie (zmiana last_login) NIE unieważnia linku — ma działać 3 dni
        self.user.last_login = timezone.now()
        self.user.save(update_fields=['last_login'])
        r = self.client.get(self._url(self.token), follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Ustaw nowe hasło')

    def test_link_2_dniowy_nadal_wazny_4_dniowy_wygasl(self):
        r = self.client.get(self._url(self._stary_token(dni=2)), follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Ustaw nowe hasło')
        from django.urls import reverse
        r = self.client.get(self._url(self._stary_token(dni=4)))
        self.assertRedirects(r, reverse('reset_hasla_wygasl'))

    def test_link_juz_uzyty_pokazuje_zwykly_blad(self):
        self.client.get(self._url(self.token))
        self.client.post(self._url('set-password'), {
            'new_password1': 'NoweHaslo123!',
            'new_password2': 'NoweHaslo123!',
        })
        r = self.client.get(self._url(self.token))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Link nie działa')

    def test_strona_czas_minal_dziala(self):
        from django.urls import reverse
        r = self.client.get(reverse('reset_hasla_wygasl'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Czas minął')
        self.assertContains(r, '3 dni')




class KalendarzKorepetytoraTests(TestCaseZamrozonaData):
    """Poprawki szefa: przerwa min. 5 min, ✖ zamiast kosza, spójna lista godzin."""

    def setUp(self):
        from datetime import time as czas
        from konta.views import nauczyciel_wolny_o, wolne_godziny_nauczyciela
        from konta.models import PrzerwaDnia
        self.PrzerwaDnia = PrzerwaDnia
        self.wolny_o = nauczyciel_wolny_o
        self.wolne_godziny = wolne_godziny_nauczyciela
        self.czas = czas
        self.nauczyciel = Nauczyciel.objects.create(
            imie='Anna', nazwisko='Kowalska', email='anna@firma.pl',
        )
        PrzedmiotNauczyciela.objects.create(
            nauczyciel=self.nauczyciel, przedmiot='matematyka',
            poziom='podstawowka', stawka=100,
        )
        self.user = Pracownik.objects.create_user(
            'szef', 'szef@firma.pl', 'Sup3rHaslo!', is_superuser=True,
        )
        self.dzien = date(2026, 11, 9)  # poniedziałek

    def _sloty(self, od, do):
        kursor = datetime.combine(self.dzien, self.czas(*map(int, od.split(':'))))
        koniec = datetime.combine(self.dzien, self.czas(*map(int, do.split(':'))))
        while kursor < koniec:
            Dostepnosc.objects.create(
                nauczyciel=self.nauczyciel,
                data_od=self.dzien, data_do=self.dzien,
                godzina_od=kursor.time(),
                godzina_do=(kursor + timedelta(minutes=30)).time(),
            )
            kursor += timedelta(minutes=30)

    def test_domyslna_przerwa_nauczyciela_to_5_min(self):
        self.assertEqual(self.nauczyciel.przerwa, 5)

    def test_przerwa_0_z_kalendarza_zapisuje_sie_jako_5(self):
        self.client.force_login(self.user)
        r = self.client.post(f'/nauczyciele/{self.nauczyciel.id}/kalendarz/dane/', {
            'dzien': self.dzien.isoformat(), 'akcja': 'zapisz_przerwe', 'przerwa': '0',
        })
        self.assertEqual(r.status_code, 200)
        wpis = self.PrzerwaDnia.objects.get(nauczyciel=self.nauczyciel, data=self.dzien)
        self.assertEqual(wpis.minuty, 5)

    def test_krzywy_slot_lekcji_nie_rozbija_zakresu(self):
        self._sloty('11:00', '13:00')
        Dostepnosc.objects.create(
            nauczyciel=self.nauczyciel, data_od=self.dzien, data_do=self.dzien,
            godzina_od=self.czas(11, 10), godzina_do=self.czas(12, 10),
        )
        start = datetime.combine(self.dzien, self.czas(11, 0))
        self.assertTrue(self.wolny_o(self.nauczyciel, self.dzien, start))

    def test_lista_godzin_zgodna_z_walidacja_formularza(self):
        self._sloty('11:00', '14:00')
        uczen = Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime.combine(self.dzien, self.czas(11, 0))),
            przedmiot='matematyka', poziom='podstawowka', szkola='SP 1',
            imie_ucznia='Kasia', nazwisko_rodzica='Nowak', rodzic='Ewa Nowak',
            telefon_glowny='600100200', email_glowny='ewa@f.pl', telefon_dodatkowy='',
            korepetytor='Anna Kowalska', kto_umowil='szef',
        )
        Lekcja.objects.create(uczen=uczen, data=uczen.pierwsza_lekcja)
        wolne = self.wolne_godziny(self.nauczyciel, self.dzien)
        # rytm z przerwą: zakres 11:00-14:00, przerwa 5 min, zajęta 11:00 -> 12:05, 13:10, 14:00, 14:15
        self.assertEqual(wolne, ['12:05', '13:10', '14:15'])
        for h in range(6, 21):
            for m in range(0, 60, 5):
                etykieta = f'{h:02d}:{m:02d}'
                start = datetime.combine(self.dzien, self.czas(h, m))
                self.assertEqual(
                    self.wolny_o(self.nauczyciel, self.dzien, start),
                    etykieta in wolne,
                    f'niezgodność listy z walidacją dla {etykieta}',
                )

    def test_ajax_godziny_pokazuja_korepetytorow_i_omijaja_zajete(self):
        self._sloty('11:00', '14:00')
        uczen = Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime.combine(self.dzien, self.czas(11, 0))),
            przedmiot='matematyka', poziom='podstawowka', szkola='SP 1',
            imie_ucznia='Kasia', nazwisko_rodzica='Nowak', rodzic='Ewa Nowak',
            telefon_glowny='600100200', email_glowny='ewa@f.pl', telefon_dodatkowy='',
            korepetytor='Anna Kowalska', kto_umowil='szef',
        )
        Lekcja.objects.create(uczen=uczen, data=uczen.pierwsza_lekcja)
        self.client.force_login(self.user)
        r = self.client.get(
            '/korepetytorzy/dane/?tryb=godziny&przedmiot=matematyka'
            f'&poziom=podstawowka&data={self.dzien.isoformat()}'
        )
        self.assertEqual(r.status_code, 200)
        godziny = r.json()['godziny']
        self.assertTrue(godziny, 'lista godzin nie może być pusta')
        self.assertTrue(all('Kowalska' in g['korepetytor'] for g in godziny))
        etykiety = [g['godzina'] for g in godziny]
        self.assertNotIn('11:00', etykiety)
        self.assertIn('12:05', etykiety)
        self.assertIn('13:10', etykiety)
        self.assertIn('14:15', etykiety)
        self.assertNotIn('12:00', etykiety)
        self.assertNotIn('14:00', etykiety)

    def test_ajax_korepetytorzy_tylko_pracujacy_w_dniu(self):
        # sama data (bez godziny): tylko korepetytorzy z zaznaczonymi godzinami tego dnia
        n2 = Nauczyciel.objects.create(imie='Ewa', nazwisko='Lis', email='e2@f.pl')
        PrzedmiotNauczyciela.objects.create(nauczyciel=n2, przedmiot='matematyka', poziom='podstawowka')
        self._sloty('11:00', '14:00')   # Anna Kowalska pracuje 2026-11-09
        self.client.force_login(self.user)
        r = self.client.get(f'/korepetytorzy/dane/?przedmiot=matematyka&poziom=podstawowka&data={self.dzien.isoformat()}')
        nazwy = [k['nazwa'] for k in r.json()['korepetytorzy']]
        self.assertIn('Anna Kowalska', nazwy)
        self.assertNotIn('Ewa Lis', nazwy)   # uczy matmy, ale nie pracuje tego dnia

    def _dane_ucznia(self, godzina):
        return {
            'data_pierwszej_lekcji': self.dzien.isoformat(),
            'godzina_pierwszej_lekcji': godzina,
            'przedmiot': 'matematyka',
            'poziom': 'podstawowka',
            'imie_ucznia': 'Kasia',
            'nazwisko_rodzica': 'Nowak',
            'rodzic': 'Ewa Nowak',
            'telefon_glowny': '600100200',
            'email_glowny': 'ewa@f.pl',
            'telefon_dodatkowy': '',
            'email_dodatkowy': '',
            'korepetytor': 'Anna Kowalska',
            'notatka_od_rodzica': '',
            'auto_wyslij_meet': 'on',
        }

    def test_dodanie_ucznia_w_godzine_spoza_kalendarza_odrzucone(self):
        self._sloty('11:00', '12:00')
        self.client.force_login(self.user)
        r = self.client.post('/uczniowie/', self._dane_ucznia('12:30'), follow=True)
        self.assertContains(r, 'nie może przyjąć lekcji')
        self.assertEqual(Uczen.objects.count(), 0)

    def test_dodanie_ucznia_w_wolna_godzine_przechodzi(self):
        self._sloty('11:00', '14:00')
        self.client.force_login(self.user)
        r = self.client.post('/uczniowie/', self._dane_ucznia('13:10'), follow=True)
        self.assertContains(r, 'został dodany')
        self.assertEqual(Uczen.objects.count(), 1)



class PoprawkiSzefaTests(TestCaseZamrozonaData):
    """Komunikaty znikają po 3 s; cykliczne co 5 min i tylko wolne godziny; okresy bez 'zawsze'."""

    def setUp(self):
        from datetime import time as czas
        self.czas = czas
        self.nauczyciel = Nauczyciel.objects.create(
            imie='Anna', nazwisko='Kowalska', email='anna@firma.pl',
        )
        PrzedmiotNauczyciela.objects.create(
            nauczyciel=self.nauczyciel, przedmiot='matematyka',
            poziom='podstawowka', stawka=100,
        )
        self.user = Pracownik.objects.create_user(
            'szef', 'szef@firma.pl', 'Sup3rHaslo!', is_superuser=True,
        )
        self.uczen = Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime(2026, 11, 9, 11, 0)),
            przedmiot='matematyka', poziom='podstawowka', szkola='SP 1',
            imie_ucznia='Kasia', nazwisko_rodzica='Nowak', rodzic='Ewa Nowak',
            telefon_glowny='600100200', email_glowny='ewa@f.pl', telefon_dodatkowy='',
            korepetytor='Anna Kowalska', kto_umowil='szef', umowil=self.user,
        )
        self.dzien = date(2026, 11, 9)  # poniedziałek
        Dostepnosc.objects.create(
            nauczyciel=self.nauczyciel, data_od=self.dzien, data_do=self.dzien,
            godzina_od=self.czas(11, 0), godzina_do=self.czas(13, 0),
        )
        d = date(2026, 11, 1)
        while d <= date(2027, 12, 31):
            Dostepnosc.objects.create(
                nauczyciel=self.nauczyciel, data_od=d, data_do=d,
                godzina_od=self.czas(11, 0), godzina_do=self.czas(13, 0),
            )
            d += timedelta(days=1)        

    def test_komunikat_znika_po_7s_i_po_kliknieciu_gdziekolwiek(self):
        r = self.client.get('/')
        self.assertContains(r, '7000')
        self.assertContains(r, 'zamknijKomunikaty')
        self.assertContains(r, "document.addEventListener('click', zamknijKomunikaty)")
        self.assertContains(r, 'komunikaty')
        self.assertNotContains(r, '3000')

    def test_ajax_wolne_godziny_nauczyciela_rytm(self):
        self.client.force_login(self.user)
        r = self.client.get(f'/korepetytorzy/dane/?nauczyciel={self.nauczyciel.id}&data={self.dzien.isoformat()}')
        self.assertEqual(r.status_code, 200)
        godziny = [g['godzina'] for g in r.json()['godziny']]
        # siatka rytmu 65 min (60 lekcja + 5 przerwy) od 11:00; 12:05+60 wychodzi poza 13:00
        self.assertEqual(godziny, ['11:00', '12:05', '13:10'])

    def test_cykliczna_co_2_tyg_usunieta_zawsze_co_tydzien(self):
        # opcja „co 2 tygodnie” usunięta: mimo wysłania co_2_tygodnie=on lekcje idą co TYDZIEŃ
        self.client.force_login(self.user)
        r = self.client.post(f'/uczniowie/{self.uczen.id}/lekcja-cykliczna/', {
            'okres': 'rok',
            'co_2_tygodnie': 'on',
            'data_poczatkowa': '2026-11-09',
            'slot_dzien': ['0'],
            'slot_godzina': ['11:00'],
            'link_meet': '',
        }, follow=True)
        self.assertEqual(r.status_code, 200)
        lekcje = Lekcja.objects.filter(uczen=self.uczen).order_by('data')
        daty = [timezone.localtime(l.data).date().isoformat() for l in lekcje]
        self.assertIn('2026-11-09', daty)
        self.assertIn('2026-11-16', daty)      # tydzień pośredni — JEST (co tydzień, nie co 14 dni)
        self.assertIn('2026-11-23', daty)
        self.assertIn('2026-11-30', daty)
        self.assertNotIn('2027-07-05', daty)   # poza okresem „rok” (do 30.06.2027)
        self.assertEqual(len(daty), 34)        # wszystkie poniedziałki 09.11.2026–28.06.2027
        godziny = {timezone.localtime(l.data).strftime('%H:%M') for l in lekcje}
        self.assertEqual(godziny, {'11:00'})

    def test_odwolaj_i_przywroc_jedna_lekcje_cykl_zostaje(self):
        # cykl co tydzień (miesiąc): 4 lekcje w listopadzie; odwołanie jednej nie kasuje cyklu
        self.client.force_login(self.user)
        self.client.post(f'/uczniowie/{self.uczen.id}/lekcja-cykliczna/', {
            'okres': 'miesiac',
            'data_poczatkowa': '2026-11-09',
            'slot_dzien': ['0'],
            'slot_godzina': ['11:00'],
            'link_meet': '',
        }, follow=True)
        self.assertEqual(Lekcja.objects.filter(uczen=self.uczen).count(), 4)
        lekcja = Lekcja.objects.get(uczen=self.uczen, data__date=date(2026, 11, 16))
        adres = f'/nauczyciele/{self.nauczyciel.id}/kalendarz/dane/'
        r = self.client.post(adres, {'dzien': '2026-11-16', 'akcja': 'odwolaj_lekcje', 'lekcja_id': lekcja.id})
        self.assertEqual(r.status_code, 200)
        lekcja.refresh_from_db()
        self.assertTrue(lekcja.odwolana)
        self.assertEqual(Lekcja.objects.filter(uczen=self.uczen).count(), 4)
        self.assertEqual(Lekcja.objects.filter(uczen=self.uczen, odwolana=False).count(), 3)
        r = self.client.post(adres, {'dzien': '2026-11-16', 'akcja': 'przywroc_lekcje', 'lekcja_id': lekcja.id})
        self.assertEqual(r.status_code, 200)
        lekcja.refresh_from_db()
        self.assertFalse(lekcja.odwolana)
        self.assertEqual(Lekcja.objects.filter(uczen=self.uczen, odwolana=False).count(), 4)

    def test_przesun_jedna_lekcje_tylko_w_tym_tygodniu(self):
        self.client.force_login(self.user)
        self.client.post(f'/uczniowie/{self.uczen.id}/lekcja-cykliczna/', {
            'okres': 'miesiac',
            'data_poczatkowa': '2026-11-09',
            'slot_dzien': ['0'],
            'slot_godzina': ['11:00'],
            'link_meet': '',
        }, follow=True)
        lekcja = Lekcja.objects.get(uczen=self.uczen, data__date=date(2026, 11, 9))
        adres = f'/nauczyciele/{self.nauczyciel.id}/kalendarz/dane/'
        # przesunięcie w tym samym tygodniu (pon 09.11 11:00 → wt 10.11 12:00) — działa
        r = self.client.post(adres, {
            'dzien': '2026-11-09', 'akcja': 'przesun_lekcje', 'lekcja_id': lekcja.id,
            'nowy_dzien': '2026-11-10', 'nowa_godzina': '12:05',
        })
        self.assertEqual(r.status_code, 200)
        lekcja.refresh_from_db()
        self.assertEqual(timezone.localtime(lekcja.data).date(), date(2026, 11, 10))
        self.assertEqual(timezone.localtime(lekcja.data).strftime('%H:%M'), '12:05')
        # cykl zostaje: liczba lekcji i pozostałe poniedziałki bez zmian
        self.assertEqual(Lekcja.objects.filter(uczen=self.uczen).count(), 4)
        self.assertTrue(Lekcja.objects.filter(uczen=self.uczen, data__date=date(2026, 11, 16)).exists())
        # przesunięcie poza ten tydzień → odrzucone (400)
        r = self.client.post(adres, {
            'dzien': '2026-11-10', 'akcja': 'przesun_lekcje', 'lekcja_id': lekcja.id,
            'nowy_dzien': '2026-11-17', 'nowa_godzina': '12:05',
        })
        self.assertEqual(r.status_code, 400)

    def test_moj_tydzien_pokazuje_odwolana_lekcje_z_przyciskiem_przywroc(self):
        # odwołana lekcja widać w „Mój tydzień” jako przekreśloną z przyciskiem ↩ (cykl zostaje)
        self.client.force_login(self.user)
        self.client.post(f'/uczniowie/{self.uczen.id}/lekcja-cykliczna/', {
            'okres': 'miesiac',
            'data_poczatkowa': '2026-11-09',
            'slot_dzien': ['0'],
            'slot_godzina': ['11:00'],
            'link_meet': '',
        }, follow=True)
        lekcja = Lekcja.objects.get(uczen=self.uczen, data__date=date(2026, 11, 16))
        self.client.post(
            f'/nauczyciele/{self.nauczyciel.id}/kalendarz/dane/',
            {'dzien': '2026-11-16', 'akcja': 'odwolaj_lekcje', 'lekcja_id': lekcja.id},
        )
        r = self.client.get(f'/moj-tydzien/{self.nauczyciel.id}/', {'rok': 2026, 'miesiac': 11, 'dzien': 16})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'odwołana')
        self.assertContains(r, 'lekcja-przywroc')
        # pozostałe (nieodwołane) lekcje nadal mają ✖ odwołaj i ↔ przesuń
        self.assertContains(r, 'lekcja-odwolaj')
        self.assertContains(r, 'lekcja-przesun')

    def test_cykliczna_w_tym_samym_czasie_co_pierwsza_lekcja_nie_blokuje(self):
        self.client.force_login(self.user)
        Lekcja.objects.create(
            uczen=self.uczen,
            data=timezone.make_aware(datetime(2026, 11, 9, 11, 0)),
        )
        r = self.client.post(f'/uczniowie/{self.uczen.id}/lekcja-cykliczna/', {
            'okres': 'miesiac',
            'data_poczatkowa': '2026-11-09',
            'slot_dzien': ['0'],
            'slot_godzina': ['11:00'],
            'link_meet': '',
        }, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'Pominięto')
        daty = [timezone.localtime(l.data).date().isoformat() for l in Lekcja.objects.filter(uczen=self.uczen)]
        self.assertIn('2026-11-09', daty)
        self.assertIn('2026-11-16', daty)

    def test_cykliczna_bez_kwadracika_co_tydzien(self):
        # bez checkboxa: co tydzień w wybranym dniu, w ramach okresu
        self.client.force_login(self.user)
        self.client.post(f'/uczniowie/{self.uczen.id}/lekcja-cykliczna/', {
            'okres': 'miesiac',
            'data_poczatkowa': '2026-11-09',
            'slot_dzien': ['0'],
            'slot_godzina': ['11:00'],
            'link_meet': '',
        }, follow=True)
        daty = [timezone.localtime(l.data).date().isoformat() for l in Lekcja.objects.filter(uczen=self.uczen)]
        self.assertIn('2026-11-09', daty)
        self.assertIn('2026-11-16', daty)      # co tydzień
        self.assertIn('2026-11-23', daty)
        self.assertIn('2026-11-30', daty)      # koniec miesiąca wciąż w okresie
        self.assertNotIn('2026-12-07', daty)   # poza miesiącem

    def test_okres_zawsze_nie_wraca_jako_90_dni(self):
        self.client.force_login(self.user)
        self.client.post(f'/uczniowie/{self.uczen.id}/lekcja-cykliczna/', {
            'okres': 'zawsze',
            'data_poczatkowa': '2026-11-09',
            'slot_dzien': ['0'],
            'slot_godzina': ['11:00'],
            'link_meet': '',
        }, follow=True)
        pozno = Lekcja.objects.filter(
            uczen=self.uczen, data__date__gte=date(2027, 3, 1)
        ).exists()
        self.assertTrue(pozno, 'okres powinien domyślnie iść do końca roku szkolnego, a nie 90 dni')


class komunikatBleduKalendarzaTests(TestCaseZamrozonaData):
    """Gdy godzina nie przechodzi walidacji — komunikat mówi DOKŁADNIE, co przeszkadza."""

    def setUp(self):
        self.n = Nauczyciel.objects.create(imie='Tomek', nazwisko='Tomek', email='t@f.pl')
        PrzedmiotNauczyciela.objects.create(nauczyciel=self.n, przedmiot='matematyka', poziom='podstawowka', stawka=100)
        self.user = Pracownik.objects.create_user('szef', 's@f.pl', 'Sup3rHaslo!', is_superuser=True)
        self.dzien = date(2026, 11, 6)
        Dostepnosc.objects.create(
            nauczyciel=self.n, data_od=self.dzien, data_do=self.dzien,
            godzina_od=time(6, 0), godzina_do=time(6, 30),
        )

    def test_komunikat_mowi_o_zakresie(self):
        self.client.force_login(self.user)
        dane = {
            'data_pierwszej_lekcji': '2026-11-06', 'godzina_pierwszej_lekcji': '08:00',
            'przedmiot': 'matematyka', 'poziom': 'podstawowka',
            'imie_ucznia': 'Kasia', 'nazwisko_rodzica': 'Nowak', 'rodzic': 'Ewa Nowak',
            'telefon_glowny': '600100200', 'email_glowny': 'ewa@f.pl',
            'telefon_dodatkowy': '', 'email_dodatkowy': '',
            'korepetytor': 'Tomek Tomek', 'notatka_od_rodzica': '', 'auto_wyslij_meet': 'on',
        }
        r = self.client.post('/uczniowie/', dane, follow=True)
        self.assertContains(r, 'nie mieści się w jego zaznaczonych godzinach')
        self.assertEqual(Uczen.objects.count(), 0)
class KalendarzZbiorczyTests(TestCase):
    """Zbiorczy kalendarz miesięczny: siatka miesiąca, chipy wolny/lekcja, tylko odczyt."""

    def setUp(self):
        self.admin = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.pracownik = Pracownik.objects.create_user('prac', 'p@f.pl', 'Sup3rHaslo!')
        self.n = Nauczyciel.objects.create(imie='Jan', nazwisko='Kowalski', email='j@n.pl')
        self.teacher_user = Pracownik.objects.create_user('jk', 'jk@n.pl', 'Sup3rHaslo!', nauczyciel=self.n)
        dzis = date.today()
        self.uczen = Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime.combine(dzis, time(17, 0))),
            przedmiot='matematyka', poziom='podstawowka', szkola='SP1',
            imie_ucznia='Kasia', nazwisko_rodzica='Nowak', rodzic='Ewa Nowak',
            telefon_glowny='600', email_glowny='e@f.pl', telefon_dodatkowy='', email_dodatkowy='',
            korepetytor='Jan Kowalski', kto_umowil='prac', umowil=self.pracownik,
        )
        Dostepnosc.objects.create(
            nauczyciel=self.n, data_od=dzis, data_do=dzis,
            godzina_od=time(16, 0), godzina_do=time(20, 0),
        )
        Lekcja.objects.create(uczen=self.uczen, data=self.uczen.pierwsza_lekcja)  # dziś 17:00
        self.anna = Nauczyciel.objects.create(imie='Anna', nazwisko='Nowak', email='a@n.pl')
        inny_dzien = dzis + timedelta(days=3)
        Dostepnosc.objects.create(
            nauczyciel=self.anna, data_od=inny_dzien, data_do=inny_dzien,
            godzina_od=time(9, 0), godzina_do=time(12, 0),
        )

    def test_szef_i_koordynator_widza_siatke_miesiaca(self):
        koordynator = Pracownik.objects.create_user('koord', 'k@f.pl', 'Sup3rHaslo!')
        koordynator.stanowisko = 'koordynator'
        koordynator.save()
        for user in (self.admin, koordynator):
            self.client.force_login(user)
            r = self.client.get('/kalendarz/')
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, 'karta-kalendarz')
            self.assertContains(r, 'Kalendarz')
            self.assertContains(r, 'poniedziałek')
            self.assertContains(r, 'Kasia Nowak')
            self.assertContains(r, 'Jan Kowalski')
            self.assertContains(r, '17:00')
            self.assertContains(r, 'chip-lekcja')
            self.assertContains(r, 'Anna Nowak wolny')
            self.assertContains(r, '09:00–12:00')
            self.assertContains(r, 'chip-wolny')

    def test_pracownik_ma_podglad(self):
        # każdy zalogowany ma podgląd zbiorczego kalendarza (tylko odczyt)
        self.client.force_login(self.pracownik)
        r = self.client.get('/kalendarz/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Kasia Nowak')

    def test_caly_kafelek_dnia_jest_klikalny(self):
        # cały kwadracik dnia to link (nie tylko numer daty)
        self.client.force_login(self.admin)
        r = self.client.get('/kalendarz/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '<a class="kalendarz-komorka')
        self.assertContains(r, 'dzien=')
        
    def test_klik_w_dzien_przechodzi_do_pelnego_widoku_dnia(self):
        # klik w kafelek dnia = pełny widok dnia (godzina po godzinie), ze strzałką wróć
        self.client.force_login(self.admin)
        dzis = date.today()
        r = self.client.get(f'/kalendarz/?dzien={dzis.day}')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'kalendarz-dzien-widok')
        self.assertContains(r, 'dzien-wiersz-godzina')
        self.assertContains(r, 'strzalka-wroc')
        self.assertContains(r, 'z Kasia Nowak')
        self.assertContains(r, 'Jan Kowalski')
        self.assertContains(r, 'matematyka')
        self.assertContains(r, 'podstawowka')
        self.assertContains(r, '17:00')
        self.assertNotContains(r, 'kalendarz-siata')
        r2 = self.client.get('/kalendarz/')
        self.assertContains(r2, 'kalendarz-siata')
        self.assertNotContains(r2, 'kalendarz-dzien-widok')
        self.assertNotContains(r2, 'strzalka-wroc')

    def test_lekcja_cykliczna_ma_jasnoniebieski_chip(self):
        self.client.force_login(self.admin)
        dzis = date.today()
        self.uczen.zajecia_cykliczne = True
        self.uczen.save(update_fields=['zajecia_cykliczne'])
        r = self.client.get(f'/kalendarz/?dzien={dzis.day}')
        self.assertContains(r, 'chip-dzien chip-lekcja-cykliczna')
        self.uczen.zajecia_cykliczne = False
        self.uczen.save(update_fields=['zajecia_cykliczne'])
        r = self.client.get(f'/kalendarz/?dzien={dzis.day}')
        self.assertContains(r, 'chip-dzien chip-lekcja"')
        self.assertNotContains(r, 'chip-dzien chip-lekcja-cykliczna')
    def test_nauczyciel_ma_podglad(self):
        self.client.force_login(self.teacher_user)
        r = self.client.get('/kalendarz/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Kasia Nowak')

    def test_strzalki_zmieniaja_miesiac(self):
        self.client.force_login(self.admin)
        r = self.client.get('/kalendarz/?rok=2026&miesiac=12')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'grudzień 2026')
        r = self.client.get('/kalendarz/?rok=2026&miesiac=1')
        self.assertContains(r, 'styczeń 2026')

    def test_dzien_pokazuje_lekcje_spoza_pelnej_godziny(self):
        # lekcja o 17:05 (slot z rytmu 60 min + przerwa) też ma być w widoku dnia (wiersz 17:00)
        self.client.force_login(self.admin)
        d = date.today() + timedelta(days=1)
        Lekcja.objects.create(
            uczen=self.uczen,
            data=timezone.make_aware(datetime.combine(d, time(17, 5))),
        )
        r = self.client.get(f'/kalendarz/?rok={d.year}&miesiac={d.month}&dzien={d.day}')
        self.assertContains(r, '17:05')

    def test_dzien_pokazuje_kto_umowil(self):
        # w widoku dnia przy lekcji widać, kto ją zapisał (umówił)
        self.client.force_login(self.admin)
        dzis = date.today()
        r = self.client.get(f'/kalendarz/?rok={dzis.year}&miesiac={dzis.month}&dzien={dzis.day}')
        self.assertContains(r, 'umówił: prac')

    def test_dzien_siatka_kolumny_korepetytorow(self):
        # widok dnia = siatka: kolumny to korepetytorzy (imię nazwisko + przedmioty), wiersze 6:00–21:00
        self.client.force_login(self.admin)
        dzis = date.today()
        r = self.client.get(f'/kalendarz/?dzien={dzis.day}')
        self.assertContains(r, 'kalendarz-dzien-siata')
        self.assertContains(r, 'dzien-kolumna-naglowek')
        self.assertContains(r, 'Jan Kowalski')      # nagłówek kolumny korepetytora
        self.assertContains(r, '06:00')              # pierwsza godzina
        self.assertContains(r, '21:00')              # ostatnia godzina (okno 6–21)
        self.assertContains(r, 'z Kasia Nowak')      # lekcja w komórce
        self.assertContains(r, 'komorka-lekcja')     # zielona komórka lekcji 17:00
        self.assertContains(r, 'komorka-wolny')      # szare wolne (zakres 16:00–20:00)


class PrzypomnieniaJutroTests(TestCase):
    """Digest „jutro”: jeden mail do rodziców (lista jutrzejszych lekcji)
    i po jednym mailu do każdego korepetytora — tylko raz dziennie na osobę."""

    def setUp(self):
        self.admin = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.jan = Nauczyciel.objects.create(imie='Jan', nazwisko='Kowalski', email='jan@firma.pl')
        self.anna = Nauczyciel.objects.create(imie='Anna', nazwisko='Nowak', email='anna@firma.pl')
        self.jutro = timezone.localtime(timezone.now()).date() + timedelta(days=1)
        self.uczen = Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime.combine(self.jutro, time(8, 0))),
            przedmiot='matematyka', poziom='podstawowka', szkola='SP1',
            imie_ucznia='Kasia', nazwisko_rodzica='Nowak', rodzic='Ewa Nowak',
            telefon_glowny='600', email_glowny='ewa@f.pl', telefon_dodatkowy='', email_dodatkowy='',
            korepetytor='Jan Kowalski', kto_umowil='admin', umowil=self.admin,
            link_meet='https://meet.google.com/abc-defg-hij',
        )
        self.karta = PrzedmiotUcznia.objects.create(
            uczen=self.uczen, przedmiot='fizyka', poziom='podstawowka', korepetytor='Anna Nowak',
        )
        Lekcja.objects.create(uczen=self.uczen, data=timezone.make_aware(datetime.combine(self.jutro, time(8, 0))))
        Lekcja.objects.create(
            uczen=self.uczen,
            data=timezone.make_aware(datetime.combine(self.jutro, time(10, 15))),
            przedmiot_karta=self.karta,
        )

    def test_digest_jutro_rodzice_i_nauczyciele(self):
        call_command('wyslij_przypomnienia_jutro')
        # 1 mail do rodziców + po 1 do każdego nauczyciela = 3
        self.assertEqual(len(mail.outbox), 3)
        mail_rodzica = [m for m in mail.outbox if m.to == ['ewa@f.pl']][0]
        self.assertIn(f'{self.jutro:%d.%m.%Y}', mail_rodzica.subject)
        self.assertIn('08:00', mail_rodzica.body)
        self.assertIn('matematyka z Jan', mail_rodzica.body)
        self.assertIn('10:15', mail_rodzica.body)
        self.assertIn('fizyka z Anna', mail_rodzica.body)
        self.assertIn('https://meet.google.com/abc-defg-hij', mail_rodzica.body)
        mail_jana = [m for m in mail.outbox if m.to == ['jan@firma.pl']][0]
        self.assertIn('Kasia Nowak', mail_jana.body)
        self.assertIn('08:00', mail_jana.body)
        mail_anny = [m for m in mail.outbox if m.to == ['anna@firma.pl']][0]
        self.assertIn('10:15', mail_anny.body)
        # drugi raz tego samego dnia — już nie wysyła (znacznik)
        call_command('wyslij_przypomnienia_jutro')
        self.assertEqual(len(mail.outbox), 3)

    def test_archiwum_ucznia_nie_dostaje_digestu(self):
        self.uczen.zakonczona = True
        self.uczen.save(update_fields=['zakonczona'])
        call_command('wyslij_przypomnienia_jutro')
        self.assertEqual(len(mail.outbox), 2)  # tylko nauczyciele
        self.assertFalse(any(m.to == ['ewa@f.pl'] for m in mail.outbox))

class SiatkaRytmuTests(TestCase):
    """Formularz dodawania ucznia: rytm dnia (60 min lekcji + przerwa) od początku każdego
    przedziału + koniec przedziału włączny + sloty przesunięte o przerwę obok lekcji.
    Zajęta lekcja blokuje 1h + przerwa w obie strony (po 18:00 nie ma 18:30)."""

    def setUp(self):
        from datetime import time as czas
        from konta.views import wolne_godziny_nauczyciela, nauczyciel_wolny_o
        self.czas = czas
        self.wolne = wolne_godziny_nauczyciela
        self.wolny_o = nauczyciel_wolny_o
        self.n = Nauczyciel.objects.create(imie='Tomek', nazwisko='Tomek', email='t@f.pl')
        self.dzien = date(2026, 11, 6)
        k = datetime.combine(self.dzien, czas(10, 0))
        koniec = datetime.combine(self.dzien, czas(14, 0))
        while k < koniec:
            Dostepnosc.objects.create(nauczyciel=self.n, data_od=self.dzien, data_do=self.dzien,
                                      godzina_od=k.time(), godzina_do=(k + timedelta(minutes=30)).time())
            k += timedelta(minutes=30)

    def test_siatka_rytmu_od_poczatku_zakresu(self):
        # przedział 10:00-14:00, przerwa 5 min -> rytm 65 min: 10:00, 11:05, 12:10, 13:15 + koniec 14:00 + 14:20
        wolne = self.wolne(self.n, self.dzien)
        self.assertEqual(wolne, ['10:00', '11:05', '12:10', '13:15', '14:20'])

    def test_ostatni_slot_rytmu_moze_wyjsc_poza_zakres(self):
        # „do 14:00”: sam koniec 14:00 NIE jest slotem (od 13:15 nie minęło pełne 60+przerwa),
        # ale ostatni slot rytmu 14:20 już tak
        self.assertFalse(self.wolny_o(self.n, self.dzien, datetime.combine(self.dzien, self.czas(14, 0))))
        self.assertTrue(self.wolny_o(self.n, self.dzien, datetime.combine(self.dzien, self.czas(14, 20))))
    def test_zajeta_lekcja_blokuje_1h_plus_przerwa(self):
        u = Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime.combine(self.dzien, self.czas(11, 0))),
            przedmiot='matematyka', poziom='podstawowka', szkola='SP', imie_ucznia='K',
            nazwisko_rodzica='N', rodzic='R', telefon_glowny='6', email_glowny='e@f.pl',
            telefon_dodatkowy='', korepetytor='Tomek Tomek', kto_umowil='szef')
        Lekcja.objects.create(uczen=u, data=u.pierwsza_lekcja)
        wolne = self.wolne(self.n, self.dzien)
        # zajęta 11:00 (z przerwą do 12:05); odpada też 10:00 i 11:05,
        # a tuż po lekcji pokazuje się slot przesunięty o przerwę: 12:05
        self.assertEqual(wolne, ['12:05', '12:10', '13:15', '14:20'])
        self.assertTrue(self.wolny_o(self.n, self.dzien, datetime.combine(self.dzien, self.czas(12, 5))))
        self.assertTrue(self.wolny_o(self.n, self.dzien, datetime.combine(self.dzien, self.czas(13, 15))))
        self.assertFalse(self.wolny_o(self.n, self.dzien, datetime.combine(self.dzien, self.czas(11, 0))))
        self.assertFalse(self.wolny_o(self.n, self.dzien, datetime.combine(self.dzien, self.czas(11, 30))))
        self.assertFalse(self.wolny_o(self.n, self.dzien, datetime.combine(self.dzien, self.czas(12, 0))))

    def test_sloty_obok_lekcji_przesuniete_o_przerwe(self):
        # lekcja 11:00 w zakresie 10:00-14:00, przerwa domyślna 5 min:
        # po lekcji wolne 12:05 (11:00 + 60 + 5), 12:00 odpada; 09:55 jest przed zakresem
        u = Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime.combine(self.dzien, self.czas(11, 0))),
            przedmiot='matematyka', poziom='podstawowka', szkola='SP', imie_ucznia='K',
            nazwisko_rodzica='N', rodzic='R', telefon_glowny='6', email_glowny='e@f.pl',
            telefon_dodatkowy='', korepetytor='Tomek Tomek', kto_umowil='szef')
        Lekcja.objects.create(uczen=u, data=u.pierwsza_lekcja)
        wolne = self.wolne(self.n, self.dzien)
        self.assertIn('12:05', wolne)
        self.assertIn('12:10', wolne)
        self.assertNotIn('12:00', wolne)
        self.assertNotIn('09:55', wolne)

    def test_przerwa_dnia_przesuwa_sloty_obok_lekcji(self):
        # jak wyżej, ale przerwa DNIA z „Mój tydzień” = 10 min -> po lekcji 12:10, a 12:05 odpada
        from konta.models import PrzerwaDnia
        u = Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime.combine(self.dzien, self.czas(11, 0))),
            przedmiot='matematyka', poziom='podstawowka', szkola='SP', imie_ucznia='K',
            nazwisko_rodzica='N', rodzic='R', telefon_glowny='6', email_glowny='e@f.pl',
            telefon_dodatkowy='', korepetytor='Tomek Tomek', kto_umowil='szef')
        Lekcja.objects.create(uczen=u, data=u.pierwsza_lekcja)
        self.assertIn('12:05', self.wolne(self.n, self.dzien))
        PrzerwaDnia.objects.create(nauczyciel=self.n, data=self.dzien, minuty=10)
        wolne = self.wolne(self.n, self.dzien)
        self.assertEqual(wolne, ['12:10', '12:20', '13:30'])  # 14:40 jest 40 min poza zakresem

    def test_slot_przed_lekcja_z_przerwa(self):
        # zakres od 6:00: przed lekcją 11:00 przy przerwie 5 min wolne 09:55 (10:00 zajęte)
        k = datetime.combine(self.dzien, self.czas(6, 0))
        koniec = datetime.combine(self.dzien, self.czas(10, 0))
        while k < koniec:
            Dostepnosc.objects.create(nauczyciel=self.n, data_od=self.dzien, data_do=self.dzien,
                                      godzina_od=k.time(), godzina_do=(k + timedelta(minutes=30)).time())
            k += timedelta(minutes=30)
        u = Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime.combine(self.dzien, self.czas(11, 0))),
            przedmiot='matematyka', poziom='podstawowka', szkola='SP', imie_ucznia='K',
            nazwisko_rodzica='N', rodzic='R', telefon_glowny='6', email_glowny='e@f.pl',
            telefon_dodatkowy='', korepetytor='Tomek Tomek', kto_umowil='szef')
        Lekcja.objects.create(uczen=u, data=u.pierwsza_lekcja)
        wolne = self.wolne(self.n, self.dzien)
        self.assertIn('09:55', wolne)  # 11:00 - 60 min lekcji - 5 min przerwy
        self.assertNotIn('10:00', wolne)
        self.assertNotIn('10:20', wolne)
        self.assertIn('12:05', wolne)

    def test_slot_poza_zakresem_maksymalnie_30_min(self):
        # zakres 6:00-18:00, przerwa 5 min: ostatni slot rytmu to 17:55,
        # kolejny 19:00 byłby 60 min poza zakresem -> odpada (max 30 min poza zakresem)
        k = datetime.combine(self.dzien, self.czas(6, 0))
        koniec = datetime.combine(self.dzien, self.czas(18, 0))
        while k < koniec:
            Dostepnosc.objects.create(nauczyciel=self.n, data_od=self.dzien, data_do=self.dzien,
                                      godzina_od=k.time(), godzina_do=(k + timedelta(minutes=30)).time())
            k += timedelta(minutes=30)
        wolne = self.wolne(self.n, self.dzien)
        self.assertIn('17:55', wolne)
        self.assertNotIn('19:00', wolne)

    def test_nie_mozna_umowic_dwoch_nachodzacych_lekcji(self):
        # zgłoszony bug: po lekcji 18:00 dało się ustawić 18:30 u tego samego korepetytora
        u = Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime.combine(self.dzien, self.czas(18, 0))),
            przedmiot='matematyka', poziom='podstawowka', szkola='SP', imie_ucznia='K',
            nazwisko_rodzica='N', rodzic='R', telefon_glowny='6', email_glowny='e@f.pl',
            telefon_dodatkowy='', korepetytor='Tomek Tomek', kto_umowil='szef')
        Dostepnosc.objects.create(nauczyciel=self.n, data_od=self.dzien, data_do=self.dzien,
                                  godzina_od=self.czas(14, 0), godzina_do=self.czas(21, 0))
        Lekcja.objects.create(uczen=u, data=u.pierwsza_lekcja)  # 18:00
        wolne = self.wolne(self.n, self.dzien)
        self.assertNotIn('18:00', wolne)
        self.assertNotIn('18:30', wolne)   # nachodzi na 18:00-19:00
        self.assertNotIn('17:35', wolne)    # nachodzi na przerwę przed lekcją 18:00
        self.assertIn('16:55', wolne)       # 16:55-18:00 tylko styka się z 18:00
        self.assertIn('19:05', wolne)       # 18:00 + 60 + 5 min przerwy
        self.assertIn('16:30', wolne)
        self.assertNotIn('21:00', wolne)    # sam koniec zakresu nie jest osobnym slotem
        self.assertFalse(self.wolny_o(self.n, self.dzien, datetime.combine(self.dzien, self.czas(18, 30))))
        self.assertTrue(self.wolny_o(self.n, self.dzien, datetime.combine(self.dzien, self.czas(19, 5))))

class DodatkowyPrzedmiotZGrafikiemTests(TestCase):
    """Dodatkowy przedmiot z datą+godziną od razu zajmuje grafik korepetytora."""

    def setUp(self):
        from datetime import time as czas
        from konta.models import PrzedmiotUcznia
        self.PrzedmiotUcznia = PrzedmiotUcznia
        self.czas = czas
        self.n = Nauczyciel.objects.create(imie='Anna', nazwisko='Kowalska', email='a@f.pl')
        PrzedmiotNauczyciela.objects.create(nauczyciel=self.n, przedmiot='fizyka', poziom='podstawowka', stawka=100)
        self.user = Pracownik.objects.create_user('szef', 's@f.pl', 'Sup3rHaslo!', is_superuser=True)
        self.uczen = Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime(2026, 11, 9, 16, 0)),
            przedmiot='matematyka', poziom='podstawowka', szkola='SP 1',
            imie_ucznia='Kasia', nazwisko_rodzica='Nowak', rodzic='Ewa Nowak',
            telefon_glowny='600100200', email_glowny='ewa@f.pl', telefon_dodatkowy='',
            korepetytor='Jan Kowalski', kto_umowil='szef', umowil=self.user,
        )
        self.dzien = date(2026, 11, 9)
        Dostepnosc.objects.create(
            nauczyciel=self.n, data_od=self.dzien, data_do=self.dzien,
            godzina_od=czas(11, 0), godzina_do=czas(13, 0),
        )

    def _dane(self, data, godzina):
        return {
            'dodaj_przedmiot': '1',
            'przedmiotdod-przedmiot': 'fizyka',
            'przedmiotdod-poziom': 'podstawowka',
            'przedmiotdod-korepetytor': 'Anna Kowalska',
            'przedmiotdod-data_pierwszej_lekcji': data,
            'przedmiotdod-godzina_pierwszej_lekcji': godzina,
        }

    def test_dodanie_przedmiotu_blokuje_grafik_korepetytora(self):
        self.client.force_login(self.user)
        r = self.client.post(f'/uczniowie/{self.uczen.id}/edytuj/', self._dane('2026-11-09', '11:00'), follow=True)
        self.assertContains(r, 'grafik korepetytora zablokowany')
        karta = self.PrzedmiotUcznia.objects.get(uczen=self.uczen, przedmiot='fizyka')
        self.assertTrue(Lekcja.objects.filter(
            uczen=self.uczen, przedmiot_karta=karta, data__date=self.dzien, data__hour=11,
        ).exists())
        self.assertTrue(Dostepnosc.objects.filter(
            nauczyciel=self.n, data_od=self.dzien, godzina_od=self.czas(11, 0),
        ).exists())


    def test_mail_potwierdzenia_przy_dodatkowym_przedmiocie_ma_jego_przedmiot(self):
        # dodanie przedmiotu dodatkowego (fizyka) -> mail do rodzica z fizyką, nie z głównym przedmiotem
        self.client.force_login(self.user)
        r = self.client.post(f'/uczniowie/{self.uczen.id}/edytuj/', self._dane('2026-11-09', '11:00'), follow=True)
        self.assertEqual(r.status_code, 200)
        maile = [m for m in mail.outbox if m.subject.startswith('Umówienie na korepetycje')]
        self.assertEqual(len(maile), 1)
        self.assertIn('Przedmiot: fizyka', maile[0].body)
        self.assertIn('Poziom: podstawowka', maile[0].body)
        self.assertNotIn('matematyka', maile[0].body)


    def test_zajeta_godzina_nie_tworzy_przedmiotu(self):
        inny = Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime(2026, 11, 9, 11, 0)),
            przedmiot='fizyka', poziom='podstawowka', szkola='SP 1',
            imie_ucznia='Ola', nazwisko_rodzica='Lis', rodzic='Ala Lis',
            telefon_glowny='600', email_glowny='o@f.pl', telefon_dodatkowy='',
            korepetytor='Anna Kowalska', kto_umowil='szef',
        )
        Lekcja.objects.create(uczen=inny, data=inny.pierwsza_lekcja)
        self.client.force_login(self.user)
        r = self.client.post(f'/uczniowie/{self.uczen.id}/edytuj/', self._dane('2026-11-09', '11:00'), follow=True)
        self.assertContains(r, 'nie może przyjąć lekcji')
        self.assertFalse(self.PrzedmiotUcznia.objects.filter(uczen=self.uczen, przedmiot='fizyka').exists())



class UsuwanieKontTests(TestCase):
    """Usuwanie kont (uzytkownicy/<id>/usun/) — TYLKO szef, nawet twórca konta nie może."""

    def setUp(self):
        self.szef = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.tworca = Pracownik.objects.create_user('tworca', 't@f.pl', 'Sup3rHaslo!')
        self.konto = Pracownik.objects.create_user('do_usuniecia', 'd@f.pl', 'Sup3rHaslo!')
        self.konto.utworzyl = self.tworca
        self.konto.save()

    def test_tworca_konta_nie_moze_go_usunac(self):
        self.client.force_login(self.tworca)
        r = self.client.post(f'/uzytkownicy/{self.konto.id}/usun/')
        self.assertEqual(r.status_code, 403)
        self.assertTrue(Pracownik.objects.filter(pk=self.konto.pk).exists())

    def test_szef_usuwa_konto(self):
        self.client.force_login(self.szef)
        r = self.client.post(f'/uzytkownicy/{self.konto.id}/usun/')
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Pracownik.objects.filter(pk=self.konto.pk).exists())

    def test_nie_mozna_usunac_wlasnego_konta(self):
        self.client.force_login(self.szef)
        r = self.client.post(f'/uzytkownicy/{self.szef.id}/usun/')
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Pracownik.objects.filter(pk=self.szef.pk).exists())


class UsunCalyZakresTests(TestCase):
    """🗑 Usuń cały zakres (akcja usun_caly_zakres): kasuje cykl od wybranego dnia w przód."""

    def setUp(self):
        self.szef = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.nauczyciel = Nauczyciel.objects.create(imie='Anna', nazwisko='Kowalska', email='anna@k.pl')
        self.tutor = Pracownik.objects.create_user('anna', 'anna@f.pl', 'Sup3rHaslo!', nauczyciel=self.nauczyciel)
        # cykliczny zakres 14:00–16:00 w każdą środę od 07.10.2026 do 31.08.2027 (4 sloty po 30 min)
        d = date(2026, 10, 7)
        while d <= date(2027, 8, 31):
            if d.weekday() == 2:
                for godz in (time(14, 0), time(14, 30), time(15, 0), time(15, 30)):
                    Dostepnosc.objects.create(
                        nauczyciel=self.nauczyciel, data_od=d, data_do=d,
                        godzina_od=godz,
                        godzina_do=(datetime.combine(d, godz) + timedelta(minutes=30)).time(),
                        cykliczny=True, cykliczny_okres=1,
                    )
            d += timedelta(days=1)
        self.adres = f'/nauczyciele/{self.nauczyciel.id}/kalendarz/dane/'

    def test_szef_usuwa_caly_zakres_od_dnia_w_przod(self):
        self.client.force_login(self.szef)
        r = self.client.post(self.adres, {'dzien': '2026-10-14', 'akcja': 'usun_caly_zakres', 'od': '14:00', 'do': '16:00'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('cały zakres', r.json()['komunikat'])
        # zostają tylko środy PRZED 14.10 (czyli tylko 07.10) — 4 sloty
        self.assertEqual(Dostepnosc.objects.filter(nauczyciel=self.nauczyciel).count(), 4)
        self.assertEqual(Dostepnosc.objects.filter(nauczyciel=self.nauczyciel, data_od=date(2026, 10, 7)).count(), 4)
        self.assertFalse(Dostepnosc.objects.filter(nauczyciel=self.nauczyciel, data_od__gte=date(2026, 10, 14)).exists())

    def test_korepetytor_moze_usunac_caly_zakres_gdy_brak_lekcji(self):
        self.client.force_login(self.tutor)
        r = self.client.post(self.adres, {'dzien': '2026-10-14', 'akcja': 'usun_caly_zakres', 'od': '14:00', 'do': '16:00'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Dostepnosc.objects.filter(nauczyciel=self.nauczyciel).count(), 4)
        self.assertEqual(Zgloszenie.objects.count(), 0)

    def test_strona_moj_tydzien_ma_przycisk_usun_caly_zakres_przy_cyklu(self):
        self.client.force_login(self.tutor)
        r = self.client.get(f'/moj-tydzien/{self.nauczyciel.id}/?rok=2026&miesiac=10&dzien=7')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'zakres-usun-cykl')
        self.assertContains(r, 'usun_caly_zakres')


class ZgloszenieKoordynatorTests(TestCase):
    """Korepetytor (nie-szef) nie rusza zajęć: dostaje 403 + zgłoszenie leci do koordynatora."""

    def setUp(self):
        self.szef = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.nauczyciel = Nauczyciel.objects.create(imie='Anna', nazwisko='Kowalska', email='anna@k.pl')
        self.tutor = Pracownik.objects.create_user('anna', 'anna@f.pl', 'Sup3rHaslo!', nauczyciel=self.nauczyciel)
        self.uczen = Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime(2026, 10, 14, 14)),
            przedmiot='matematyka', poziom='podstawa', szkola='SP 1',
            imie_ucznia='Kasia', nazwisko_rodzica='Nowak', rodzic='Ewa Nowak',
            telefon_glowny='600', email_glowny='e@f.pl', telefon_dodatkowy='',
            korepetytor='Anna Kowalska', kto_umowil='szef', umowil=self.szef,
        )
        self.lekcja = Lekcja.objects.create(uczen=self.uczen, data=timezone.make_aware(datetime(2026, 10, 14, 14)))
        Dostepnosc.objects.create(
            nauczyciel=self.nauczyciel, data_od=date(2026, 10, 14), data_do=date(2026, 10, 14),
            godzina_od=time(14, 0), godzina_do=time(16, 0), cykliczny=True, cykliczny_okres=1,
        )
        self.adres = f'/nauczyciele/{self.nauczyciel.id}/kalendarz/dane/'

    def test_korepetytor_usun_zakres_z_lekcja_dostaje_403_i_zgloszenie(self):
        self.client.force_login(self.tutor)
        r = self.client.post(self.adres, {'dzien': '2026-10-14', 'akcja': 'usun_zakres', 'od': '14:00', 'do': '16:00'})
        self.assertEqual(r.status_code, 403)
        self.assertIn('koordynatorem', r.json()['blad'])
        self.assertTrue(Dostepnosc.objects.filter(nauczyciel=self.nauczyciel, data_od=date(2026, 10, 14)).exists())
        zg = Zgloszenie.objects.filter(autor=self.tutor, kategoria='zajęcia')
        self.assertEqual(zg.count(), 1)
        self.assertIn('14:00', zg.first().tresc)
        self.assertIn('/moj-tydzien/', zg.first().strona)

    def test_korepetytor_usun_caly_zakres_z_lekcja_dostaje_403(self):
        self.client.force_login(self.tutor)
        r = self.client.post(self.adres, {'dzien': '2026-10-14', 'akcja': 'usun_caly_zakres', 'od': '14:00', 'do': '16:00'})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(Dostepnosc.objects.filter(nauczyciel=self.nauczyciel).exists())
        self.assertEqual(Zgloszenie.objects.filter(autor=self.tutor).count(), 1)

    def test_korepetytor_wyczysc_dzien_z_lekcja_dostaje_403(self):
        self.client.force_login(self.tutor)
        r = self.client.post(self.adres, {'dzien': '2026-10-14', 'akcja': 'wyczysc_dzien'})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(Dostepnosc.objects.filter(nauczyciel=self.nauczyciel).exists())
        self.assertEqual(Zgloszenie.objects.filter(autor=self.tutor).count(), 1)

    def test_korepetytor_odwolaj_lekcje_dostaje_403_i_lekcja_zostaje(self):
        self.client.force_login(self.tutor)
        r = self.client.post(self.adres, {'dzien': '2026-10-14', 'akcja': 'odwolaj_lekcje', 'lekcja_id': self.lekcja.id})
        self.assertEqual(r.status_code, 403)
        self.lekcja.refresh_from_db()
        self.assertFalse(self.lekcja.odwolana)
        self.assertEqual(Zgloszenie.objects.filter(autor=self.tutor, kategoria='zajęcia').count(), 1)

    def test_korepetytor_przesun_lekcje_dostaje_403_i_lekcja_zostaje(self):
        self.client.force_login(self.tutor)
        r = self.client.post(self.adres, {
            'dzien': '2026-10-14', 'akcja': 'przesun_lekcje', 'lekcja_id': self.lekcja.id,
            'nowy_dzien': '2026-10-15', 'nowa_godzina': '14:00',
        })
        self.assertEqual(r.status_code, 403)
        self.lekcja.refresh_from_db()
        self.assertEqual(timezone.localtime(self.lekcja.data).date(), date(2026, 10, 14))
        self.assertEqual(Zgloszenie.objects.filter(autor=self.tutor).count(), 1)

    def test_szef_moze_wszystko_bez_zgloszenia(self):
        self.client.force_login(self.szef)
        r = self.client.post(self.adres, {'dzien': '2026-10-14', 'akcja': 'usun_zakres', 'od': '14:00', 'do': '16:00'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('UWAGA', r.json()['komunikat'])
        self.assertFalse(Dostepnosc.objects.filter(nauczyciel=self.nauczyciel, data_od=date(2026, 10, 14)).exists())
        r = self.client.post(self.adres, {'dzien': '2026-10-14', 'akcja': 'odwolaj_lekcje', 'lekcja_id': self.lekcja.id})
        self.assertEqual(r.status_code, 200)
        self.lekcja.refresh_from_db()
        self.assertTrue(self.lekcja.odwolana)
        self.assertEqual(Zgloszenie.objects.filter(autor=self.szef).count(), 0)

    def test_korepetytor_moze_dodawac_godziny_i_przerwe_bez_zgloszenia(self):
        self.client.force_login(self.tutor)
        r = self.client.post(self.adres, {'dzien': '2026-10-15', 'akcja': 'zapisz_przerwe', 'przerwa': '10'})
        self.assertEqual(r.status_code, 200)
        r = self.client.post(self.adres, {'dzien': '2026-10-15', 'akcja': 'zaznacz_zakres', 'od': '18:00', 'do': '20:00'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Zgloszenie.objects.filter(autor=self.tutor).count(), 0)


class UsuwanieTylkoSzefTests(TestCase):
    """Osoba umawiająca i koordynator mogą tylko EDYTOWAĆ uczniów —
    usuwanie zajęć (przedmiot z lekcjami) i kończenie współpracy: TYLKO szef."""

    def setUp(self):
        self.szef = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.koord = Pracownik.objects.create_user('koordynator1', 'k@f.pl', 'Sup3rHaslo!')
        self.koord.stanowisko = 'koordynator'
        self.koord.save()
        self.umawiajacy = Pracownik.objects.create_user('umawiacz', 'u@f.pl', 'Sup3rHaslo!')
        self.nauczyciel = Nauczyciel.objects.create(imie='Anna', nazwisko='Kowalska', email='anna@k.pl')
        self.uczen = Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime(2026, 10, 14, 14)),
            przedmiot='matematyka', poziom='podstawa', szkola='SP 1',
            imie_ucznia='Kasia', nazwisko_rodzica='Nowak', rodzic='Ewa Nowak',
            telefon_glowny='600', email_glowny='e@f.pl', telefon_dodatkowy='',
            korepetytor='Anna Kowalska', kto_umowil='umawiacz', umowil=self.umawiajacy,
        )
        self.karta = PrzedmiotUcznia.objects.create(
            uczen=self.uczen, przedmiot='fizyka', poziom='rozszerzenie', korepetytor='Anna Kowalska',
        )
        self.lekcja = Lekcja.objects.create(
            uczen=self.uczen, data=timezone.make_aware(datetime(2026, 10, 14, 15)),
            przedmiot_karta=self.karta,
        )
        Dostepnosc.objects.create(
            nauczyciel=self.nauczyciel, data_od=date(2026, 10, 14), data_do=date(2026, 10, 14),
            godzina_od=time(15, 0), godzina_do=time(16, 0),
        )

    def test_umawiajacy_nie_moze_zakoncz_wspolpracy(self):
        self.client.force_login(self.umawiajacy)
        r = self.client.post(f'/uczniowie/{self.uczen.id}/zakoncz/')
        self.assertEqual(r.status_code, 403)
        self.uczen.refresh_from_db()
        self.assertFalse(self.uczen.zakonczona)

    def test_koordynator_nie_moze_zakoncz_wspolpracy(self):
        self.client.force_login(self.koord)
        r = self.client.post(f'/uczniowie/{self.uczen.id}/zakoncz/')
        self.assertEqual(r.status_code, 403)
        self.uczen.refresh_from_db()
        self.assertFalse(self.uczen.zakonczona)

    def test_szef_moze_zakoncz_wspolprace(self):
        self.client.force_login(self.szef)
        r = self.client.post(f'/uczniowie/{self.uczen.id}/zakoncz/', follow=True)
        self.assertEqual(r.status_code, 200)
        self.uczen.refresh_from_db()
        self.assertTrue(self.uczen.zakonczona)

    def test_umawiajacy_nie_moze_usunac_przedmiotu(self):
        self.client.force_login(self.umawiajacy)
        r = self.client.post(f'/uczniowie/{self.uczen.id}/edytuj/', {
            'usun_przedmiot': '1', 'przedmiot_ucznia_id': self.karta.id,
        })
        self.assertEqual(r.status_code, 403)
        self.assertTrue(PrzedmiotUcznia.objects.filter(pk=self.karta.id).exists())

    def test_koordynator_nie_moze_usunac_przedmiotu(self):
        self.client.force_login(self.koord)
        r = self.client.post(f'/uczniowie/{self.uczen.id}/edytuj/', {
            'usun_przedmiot': '1', 'przedmiot_ucznia_id': self.karta.id,
        })
        self.assertEqual(r.status_code, 403)
        self.assertTrue(PrzedmiotUcznia.objects.filter(pk=self.karta.id).exists())

    def test_szef_moze_usunac_przedmiot_razem_z_lekcja_i_blokada(self):
        self.client.force_login(self.szef)
        r = self.client.post(f'/uczniowie/{self.uczen.id}/edytuj/', {
            'usun_przedmiot': '1', 'przedmiot_ucznia_id': self.karta.id,
        }, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(PrzedmiotUcznia.objects.filter(pk=self.karta.id).exists())
        self.assertFalse(Lekcja.objects.filter(pk=self.lekcja.id).exists())
        self.assertFalse(Dostepnosc.objects.filter(nauczyciel=self.nauczyciel, data_od=date(2026, 10, 14)).exists())

    def test_umawiajacy_widzi_tylko_edycje_bez_przycisku_zakoncz(self):
        self.client.force_login(self.umawiajacy)
        r = self.client.get('/lekcje/')
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'Zakończ współpracę')
        self.assertContains(r, 'Edytuj dane ucznia')
        self.client.force_login(self.szef)
        r = self.client.get('/lekcje/')
        self.assertContains(r, 'Zakończ współpracę')



class LegendaPoziomowUczniowieTests(TestCase):
    """Zakładka Uczniowie: kalendarz dostępności pokazuje przedmioty korepetytora
    w kolorze poziomu + legenda kolorów na samym dole panelu."""

    def setUp(self):
        self.szef = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.nauczyciel = Nauczyciel.objects.create(imie='Anna', nazwisko='Kowalska', email='anna@k.pl', aktywny=True)
        PrzedmiotNauczyciela.objects.create(nauczyciel=self.nauczyciel, przedmiot='matematyka', poziom='podstawowka', stawka=100)
        PrzedmiotNauczyciela.objects.create(nauczyciel=self.nauczyciel, przedmiot='fizyka', poziom='studia', stawka=150)
        Dostepnosc.objects.create(
            nauczyciel=self.nauczyciel, data_od=date.today(), data_do=date.today(),
            godzina_od=time(14, 0), godzina_do=time(16, 0),
        )

    def test_strona_uczniowie_ma_legende_i_kolory_poziomow(self):
        self.client.force_login(self.szef)
        r = self.client.get('/uczniowie/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'legenda-poziomow')
        self.assertContains(r, 'poziom-podstawowka')
        self.assertContains(r, 'poziom-srednia-podstawa')
        self.assertContains(r, 'poziom-srednia-rozszerzenie')
        self.assertContains(r, 'poziom-studia')
        # przedmioty nauczyciela pokolorowane wg poziomu, jaki uczy
        self.assertContains(r, 'przedmiot-poziom poziom-podstawowka">matematyka')
        self.assertContains(r, 'przedmiot-poziom poziom-studia">fizyka')


class PlatnosciHistoriaTests(TestCase):
    """Raport płatności (mail lub plik): opłacone pozycje trafiają do HISTORII,
    a nie wracają do tabeli nieopłaconych."""

    def setUp(self):
        self.szef = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.uczen = Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime(2026, 10, 14, 14)),
            przedmiot='matematyka', poziom='podstawa', szkola='SP 1',
            imie_ucznia='Kasia', nazwisko_rodzica='Nowak', rodzic='Ewa Nowak',
            telefon_glowny='600', email_glowny='e@f.pl', telefon_dodatkowy='',
            korepetytor='Anna Kowalska', kto_umowil='szef', umowil=self.szef,
            zaplacone=True,
        )
        self.client.force_login(self.szef)

    def test_wyslij_raport_mailowy_idzie_do_historii_nie_do_nieoplaconych(self):
        r = self.client.post('/platnosci/', {'wyslij_raport': '1', 'email_raportu': 'biuro@f.pl'}, follow=True)
        self.assertEqual(r.status_code, 200)
        self.uczen.refresh_from_db()
        self.assertFalse(self.uczen.zaplacone)
        self.assertIsNotNone(self.uczen.rozliczono_kiedy)
        self.assertEqual(RaportPlatnosci.objects.count(), 1)
        r = self.client.get('/platnosci/')
        self.assertContains(r, 'Historia płatności')
        self.assertContains(r, 'Kasia Nowak')

    def test_pobranie_raportu_pliku_tez_idzie_do_historii(self):
        r = self.client.get('/raport-platnosci/')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r['Content-Type'].startswith('text/html'))   # raport jako plik HTML z tabelą
        self.assertIn('.html', r['Content-Disposition'])
        self.assertIn('<table', r.content.decode())
        self.uczen.refresh_from_db()
        self.assertFalse(self.uczen.zaplacone)
        self.assertIsNotNone(self.uczen.rozliczono_kiedy)
        self.assertEqual(RaportPlatnosci.objects.count(), 1)
        rid = RaportPlatnosci.objects.first().id
        r = self.client.get(f'/raport-platnosci/{rid}/')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Kasia Nowak', r.content)

    def test_raport_nie_usuwa_lekcji_uczen_zostaje_w_zakladce_lekcje(self):
        Lekcja.objects.create(uczen=self.uczen, data=timezone.make_aware(datetime(2026, 10, 14, 14)))
        Lekcja.objects.create(uczen=self.uczen, data=timezone.make_aware(datetime(2026, 10, 21, 14)))
        przed = Lekcja.objects.filter(uczen=self.uczen).count()
        self.client.post('/platnosci/', {'wyslij_raport': '1', 'email_raportu': 'biuro@f.pl'})
        self.assertEqual(Lekcja.objects.filter(uczen=self.uczen).count(), przed)
        self.assertTrue(Uczen.objects.filter(pk=self.uczen.pk, zakonczona=False).exists())
        r = self.client.get('/lekcje/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Kasia Nowak')


class KontaArchiwumTests(TestCase):
    """Użytkownicy: bez przycisków admin/usuń/zakończ współpracę; X dezaktywuje
    z potwierdzeniem i konto ląduje w Archiwum (bez logowania); przywrócenie
    z Archiwum wraca do Użytkowników i logowanie działa."""

    def setUp(self):
        self.szef = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.prac = Pracownik.objects.create_user('prac', 'p@f.pl', 'Sup3rHaslo!')
        self.client.force_login(self.szef)

    def test_uzytkownicy_bez_niebezpiecznych_przyciskow(self):
        r = self.client.get('/uzytkownicy/')
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'przelacz_szefa')
        self.assertNotContains(r, 'usun_konto')
        self.assertNotContains(r, 'zakoncz_wspolprace')
        self.assertNotContains(r, 'nadaj admina')
        self.assertNotContains(r, 'odebrać admina')
        self.assertContains(r, f'/uzytkownicy/{self.prac.id}/konto/')

    def test_dezaktywacja_z_potwierdzeniem_i_bez_logowania(self):
        r = self.client.get(f'/uzytkownicy/{self.prac.id}/konto/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Czy na pewno chcesz dezaktywować konto?')
        r = self.client.post(f'/uzytkownicy/{self.prac.id}/konto/', follow=True)
        self.assertEqual(r.status_code, 200)
        self.prac.refresh_from_db()
        self.assertFalse(self.prac.is_active)
        r = self.client.get('/uzytkownicy/')
        self.assertNotContains(r, 'p@f.pl')
        r = self.client.get('/archiwum/')
        self.assertContains(r, 'prac')
        self.assertContains(r, '/przywroc/')   # ikonka ✔ przywróć konto
        self.client.logout()
        self.assertFalse(self.client.login(username='prac', password='Sup3rHaslo!'))

    def test_przywrocenie_z_archiwum_wraca_do_uzytkownikow_i_logowanie_dziala(self):
        self.client.post(f'/uzytkownicy/{self.prac.id}/konto/')
        self.prac.refresh_from_db()
        self.assertFalse(self.prac.is_active)
        r = self.client.get(f'/uzytkownicy/{self.prac.id}/przywroc/', follow=True)
        self.assertEqual(r.status_code, 200)
        self.prac.refresh_from_db()
        self.assertTrue(self.prac.is_active)
        r = self.client.get('/uzytkownicy/')
        self.assertContains(r, 'p@f.pl')
        r = self.client.get('/archiwum/')
        self.assertNotContains(r, '>prac</td>')
        self.client.logout()
        self.assertTrue(self.client.login(username='prac', password='Sup3rHaslo!'))

    def test_dezaktywacja_korepetytora_wylacza_tez_karte_i_laduje_w_archiwum(self):
        # konto korepetytora wyłączone -> karta aktywny=False -> korepetytor widać w Archiwum
        n = Nauczyciel.objects.create(imie='Ola', nazwisko='Zet', email='o@z.pl')
        tutor = Pracownik.objects.create_user('ola', 'o@z.pl', 'Sup3rHaslo!', nauczyciel=n)
        self.client.post(f'/uzytkownicy/{tutor.id}/konto/')
        tutor.refresh_from_db()
        n.refresh_from_db()
        self.assertFalse(tutor.is_active)
        self.assertFalse(n.aktywny)
        r = self.client.get('/archiwum/')
        self.assertContains(r, 'Ola Zet')   # korepetytor NIE znika bez śladu
        self.assertContains(r, '/przywroc/') # ikonka ✔ przywróć konto
        # przywrócenie konta przywraca też kartę
        self.client.get(f'/uzytkownicy/{tutor.id}/przywroc/')
        n.refresh_from_db()
        self.assertTrue(n.aktywny)

    def test_usun_na_zawsze_tylko_z_archiwum_i_tylko_dla_szefa(self):
        # w Użytkownicy NIE ma przycisku usun_konto, w Archiwum jest (szef)
        r = self.client.get('/uzytkownicy/')
        self.assertNotContains(r, 'usun_konto')
        self.client.post(f'/uzytkownicy/{self.prac.id}/konto/')
        r = self.client.get('/archiwum/')
        self.assertContains(r, f'/uzytkownicy/{self.prac.id}/usun/')
        self.assertContains(r, '🗑')            # sama ikonka śmietnika, bez napisu
        self.assertNotContains(r, '<th>Status</th>')   # w archiwum nie ma kolumny Status
        self.assertNotContains(r, 'edytuj_uzytkownika')  # edycja kont tylko w Użytkownikach
        # GET = ekran potwierdzenia, POST = kasuje na stałe i wraca do archiwum
        r = self.client.get(f'/uzytkownicy/{self.prac.id}/usun/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'na stałe')
        r = self.client.post(f'/uzytkownicy/{self.prac.id}/usun/', follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Pracownik.objects.filter(pk=self.prac.id).exists())
        self.assertContains(r, 'Archiwum')
        # koordynator nie może usuwać na zawsze (403)
        koord = Pracownik.objects.create_user('koord', 'k@f.pl', 'Sup3rHaslo!', stanowisko='koordynator')
        prac2 = Pracownik.objects.create_user('prac2', 'p2@f.pl', 'Sup3rHaslo!', is_active=False)
        self.client.force_login(koord)
        r = self.client.get('/uzytkownicy/')  # koordynator widzi zakładkę, ale bez usun
        self.assertNotContains(r, 'usun_konto')
        r = self.client.post(f'/uzytkownicy/{prac2.id}/usun/')
        self.assertEqual(r.status_code, 403)

    def test_usun_na_zawsze_korepetytora_kasuje_tez_karte(self):
        n = Nauczyciel.objects.create(imie='Ola', nazwisko='Zet', email='o@z.pl')
        tutor = Pracownik.objects.create_user('ola', 'o@z.pl', 'Sup3rHaslo!', nauczyciel=n)
        self.client.post(f'/uzytkownicy/{tutor.id}/konto/')  # -> archiwum
        self.client.post(f'/uzytkownicy/{tutor.id}/usun/')
        self.assertFalse(Pracownik.objects.filter(pk=tutor.id).exists())
        self.assertFalse(Nauczyciel.objects.filter(pk=n.id).exists())  # żadnych "duchów"

    def test_uzytkownicy_siatka_bez_statusu_zakresow_kalendarza_i_kolorow(self):
        n = Nauczyciel.objects.create(imie='Jan', nazwisko='Kowalski', email='j@k.pl')
        tutor = Pracownik.objects.create_user('jk', 'jk@k.pl', 'Sup3rHaslo!', nauczyciel=n)
        PrzedmiotNauczyciela.objects.create(nauczyciel=n, przedmiot='matematyka', poziom='srednia podstawa', stawka=120)
        r = self.client.get('/uzytkownicy/')
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, '<th>Status</th>')     # status usunięty z Użytkowników
        self.assertContains(r, 'tabela-siata')            # siatka kolumn jak w Archiwum/Lekcjach
        self.assertContains(r, 'matematyka')             # przedmioty korepetytora
        self.assertNotContains(r, 'przedmiot-poziom')     # kolory tylko w Uczniowie
        self.assertNotContains(r, '<th>Zakresy</th>')     # zakresy widać w Kalendarzu zbiorczym
        self.assertNotContains(r, '<th>Kalendarz</th>')

    def test_lekcje_maja_siatke_jak_uzytkownicy(self):
        Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime(2026, 10, 6, 17)),
            przedmiot='matematyka', poziom='podstawowka',
            imie_ucznia='Kasia', nazwisko_rodzica='Nowak', rodzic='Ewa Nowak',
            telefon_glowny='600100200', email_glowny='e@f.pl',
            korepetytor='Jan Kowalski', kto_umowil='admin',
        )
        r = self.client.get('/lekcje/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'tabela-siata')

class PelneGodzinyMojTydzienTests(TestCase):
    """Pelne godziny w Moj tydzien (bez :30) i panel przesuwania zajec tylko dla szefa."""

    def setUp(self):
        self.szef = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.nauczyciel_user = Pracownik.objects.create_user('j.kowalski', 'jan@k.pl', 'M0taneHaslo!')
        self.nauczyciel = Nauczyciel.objects.create(imie='Jan', nazwisko='Kowalski', email='jan@k.pl')
        self.nauczyciel_user.nauczyciel = self.nauczyciel
        self.nauczyciel_user.save()

    def test_moj_tydzien_pokazuje_tylko_pelne_godziny(self):
        self.client.force_login(self.szef)
        r = self.client.get(f'/moj-tydzien/{self.nauczyciel.id}/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'value="07:00"')
        self.assertNotContains(r, 'value="07:30"')

    def test_kafelki_tylko_pelne_godziny(self):
        self.client.force_login(self.szef)
        r = self.client.post(
            f'/nauczyciele/{self.nauczyciel.id}/kalendarz/dane/',
            {'dzien': '2026-10-01', 'akcja': 'przelacz_godzine', 'godzina': '16:00'},
        )
        self.assertEqual(r.status_code, 200)
        kafelki = r.json()['kafelki']
        self.assertTrue(kafelki)
        self.assertTrue(all(k['etykieta'].endswith(':00') for k in kafelki))

    def test_kafelki_zajete_i_przerwa_dla_lekcji_spoza_pelnej_godziny(self):
        # lekcja 11:50-12:50, przerwa dnia 30 min: kafelki 11:00 i 12:00 zajete, 13:00 = przerwa
        from konta.models import PrzerwaDnia
        u = Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime(2026, 10, 1, 11, 50)),
            przedmiot='matematyka', poziom='podstawowka', szkola='SP', imie_ucznia='K',
            nazwisko_rodzica='N', rodzic='R', telefon_glowny='6', email_glowny='e@f.pl',
            telefon_dodatkowy='', korepetytor='Jan Kowalski', kto_umowil='admin',
        )
        Lekcja.objects.create(uczen=u, data=u.pierwsza_lekcja)
        PrzerwaDnia.objects.create(nauczyciel=self.nauczyciel, data=date(2026, 10, 1), minuty=30)
        self.client.force_login(self.szef)
        r = self.client.post(
            f'/nauczyciele/{self.nauczyciel.id}/kalendarz/dane/',
            {'dzien': '2026-10-01', 'akcja': 'przelacz_godzine', 'godzina': '16:00'},
        )
        self.assertEqual(r.status_code, 200)
        kafelki = {k['etykieta']: k for k in r.json()['kafelki']}
        self.assertTrue(kafelki['11:00']['zajety'])
        self.assertTrue(kafelki['12:00']['zajety'])
        self.assertFalse(kafelki['13:00']['zajety'])
        self.assertTrue(kafelki['13:00']['przerwa'])   # przerwa 12:50-13:20 zahacza o 13:00
        self.assertFalse(kafelki['14:00']['przerwa'])

    def test_moj_tydzien_pokazuje_zaznaczony_zakres_i_przerwe(self):
        # poprawka testera: korepetytor widzi, jaki zakres godzin zaznaczył (i jaka przerwa)
        from konta.models import PrzerwaDnia
        Dostepnosc.objects.create(
            nauczyciel=self.nauczyciel, data_od=date(2026, 10, 5), data_do=date(2026, 10, 5),
            godzina_od=time(6, 0), godzina_do=time(15, 0),
        )
        PrzerwaDnia.objects.create(nauczyciel=self.nauczyciel, data=date(2026, 10, 5), minuty=10)
        self.client.force_login(self.szef)
        r = self.client.get(f'/moj-tydzien/{self.nauczyciel.id}/?rok=2026&miesiac=10&dzien=5')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '06:00–15:00')          # zakres w pigulce dnia
        self.assertContains(r, 'Zaznaczone w tym dniu:')  # podpowiedź w okienku "+"
        self.assertContains(r, 'przerwa: 10 min')

    def test_panel_przesuwania_tylko_dla_szefa(self):
        self.client.force_login(self.nauczyciel_user)
        r = self.client.get(f'/moj-tydzien/{self.nauczyciel.id}/')
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'Przesuń jedno zajęcie')
        self.client.force_login(self.szef)
        r = self.client.get(f'/moj-tydzien/{self.nauczyciel.id}/')
        self.assertContains(r, 'Przesuń jedno zajęcie')

    def test_zgloszenia_bez_przycisku_rozpatrz(self):
        self.client.force_login(self.szef)
        r = self.client.get('/zgloszenia/')
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'rozpatrz')


class DiscordPierwszaLekcjaTests(TestCaseZamrozonaData):
    """Discord: korepetytor sam wpisuje swoje ID (Mój tydzień); przy 1. lekcji leci webhook ze wzmianką."""

    def setUp(self):
        self.szef = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.nauczyciel_user = Pracownik.objects.create_user('j.kowalski', 'jan@k.pl', 'M0taneHaslo!')
        self.nauczyciel = Nauczyciel.objects.create(imie='Jan', nazwisko='Kowalski', email='jan@k.pl')
        self.nauczyciel_user.nauczyciel = self.nauczyciel
        self.nauczyciel_user.save()
        PrzedmiotNauczyciela.objects.create(
            nauczyciel=self.nauczyciel, przedmiot='matematyka', poziom='srednia rozszerzenie',
        )

    def _dane_ucznia(self):
        return {
            'data_pierwszej_lekcji': '2026-10-05',
            'godzina_pierwszej_lekcji': '16:00',
            'przedmiot': 'matematyka',
            'poziom': 'srednia rozszerzenie',
            'imie_ucznia': 'Kasia',
            'nazwisko_rodzica': 'Nowak',
            'rodzic': 'Ewa Nowak',
            'telefon_glowny': '600100200',
            'email_glowny': 'ewa@f.pl',
            'korepetytor': 'Jan Kowalski',
        }

    def _dodaj_dostepnosc(self):
        Dostepnosc.objects.create(
            nauczyciel=self.nauczyciel,
            data_od=date(2026, 10, 5), data_do=date(2026, 10, 5),
            godzina_od='16:00', godzina_do='19:00',
        )

    def test_nauczyciel_sam_zapisuje_discord_id(self):
        self.client.force_login(self.nauczyciel_user)
        r = self.client.post(
            f'/nauczyciele/{self.nauczyciel.id}/kalendarz/dane/',
            {'dzien': '2026-10-05', 'akcja': 'zapisz_discord_id', 'discord_id': '111222333444555666'},
        )
        self.assertEqual(r.status_code, 200)
        self.nauczyciel.refresh_from_db()
        self.assertEqual(self.nauczyciel.discord_id, '111222333444555666')

    def test_zapisz_discord_id_odrzuca_niecyfry(self):
        self.client.force_login(self.nauczyciel_user)
        r = self.client.post(
            f'/nauczyciele/{self.nauczyciel.id}/kalendarz/dane/',
            {'dzien': '2026-10-05', 'akcja': 'zapisz_discord_id', 'discord_id': 'abc'},
        )
        self.assertEqual(r.status_code, 400)
        self.nauczyciel.refresh_from_db()
        self.assertEqual(self.nauczyciel.discord_id, '')

    def test_pierwsza_lekcja_wysyla_webhook_ze_wzmianka(self):
        self._dodaj_dostepnosc()
        self.nauczyciel.discord_id = '111222333444555666'
        self.nauczyciel.save()
        self.client.force_login(self.szef)
        with mock.patch.dict(os.environ, {'DISCORD_WEBHOOK_URL': 'https://discord.com/api/webhooks/test/abc'}):
            with mock.patch('urllib.request.urlopen') as urlopen:
                odp = mock.MagicMock()
                odp.status = 204
                urlopen.return_value.__enter__.return_value = odp
                r = self.client.post('/uczniowie/', self._dane_ucznia(), follow=True)
        self.assertContains(r, 'został dodany')
        self.assertEqual(urlopen.call_count, 1)
        cialo = urlopen.call_args[0][0].data.decode('utf-8')
        self.assertIn('<@111222333444555666>', cialo)
        self.assertIn('Kasia', cialo)
        self.assertIn('05.10.2026', cialo)

    def test_bez_discord_id_wysyla_bez_wzmianki(self):
        self._dodaj_dostepnosc()
        self.client.force_login(self.szef)
        with mock.patch.dict(os.environ, {'DISCORD_WEBHOOK_URL': 'https://discord.com/api/webhooks/test/abc'}):
            with mock.patch('urllib.request.urlopen') as urlopen:
                odp = mock.MagicMock()
                odp.status = 204
                urlopen.return_value.__enter__.return_value = odp
                r = self.client.post('/uczniowie/', self._dane_ucznia(), follow=True)
        self.assertContains(r, 'został dodany')
        self.assertEqual(urlopen.call_count, 1)
        cialo = urlopen.call_args[0][0].data.decode('utf-8')
        self.assertNotIn('<@', cialo)
        self.assertIn('Kasia', cialo)

    def test_bez_webhooka_nie_pada(self):
        self._dodaj_dostepnosc()
        self.nauczyciel.discord_id = '111222333444555666'
        self.nauczyciel.save()
        self.client.force_login(self.szef)
        env = {k: v for k, v in os.environ.items() if k != 'DISCORD_WEBHOOK_URL'}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch('urllib.request.urlopen') as urlopen:
                r = self.client.post('/uczniowie/', self._dane_ucznia(), follow=True)
        self.assertContains(r, 'został dodany')
        urlopen.assert_not_called()


class SiatkaWszedzieTests(TestCase):
    """Wszystkie tabele w panelu mają siatkę (tabela-siata) — żadna nie zostaje
    jako samo class="tabela" (bez linii między kolumnami)."""

    def setUp(self):
        self.szef = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.client.force_login(self.szef)

    def test_wszystkie_strony_panelu_bez_tabel_bez_siatki(self):
        for adres in ['/pulpit/', '/uczniowie/', '/archiwum/', '/lekcje/', '/kalendarz/', '/platnosci/', '/uzytkownicy/', '/zgloszenia/']:
            with self.subTest(adres=adres):
                r = self.client.get(adres)
                self.assertEqual(r.status_code, 200)
                self.assertNotContains(r, 'class="tabela"')   # każda tabela to "tabela tabela-siata"

    def test_strony_edycji_tez_maja_siatke(self):
        uczen = Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime(2026, 10, 5, 17)),
            przedmiot='matematyka', poziom='rozszerzony',
            imie_ucznia='Kasia', nazwisko_rodzica='Nowak', rodzic='Ewa Nowak',
            telefon_glowny='600100200', email_glowny='e@f.pl',
            telefon_dodatkowy='', email_dodatkowy='',
            korepetytor='Jan Kowalski', notatka_od_rodzica='ok', kto_umowil='Jan',
        )
        n = Nauczyciel.objects.create(imie='Jan', nazwisko='Kowalski', email='j@f.pl')
        tutor = Pracownik.objects.create_user('ola', 'o@f.pl', 'Sup3rHaslo!', nauczyciel=n)
        for adres in [f'/uczniowie/{uczen.id}/edytuj/', f'/uzytkownicy/{tutor.id}/edytuj/']:
            with self.subTest(adres=adres):
                r = self.client.get(adres)
                self.assertEqual(r.status_code, 200)
                self.assertNotContains(r, 'class="tabela"')

class BlokadaLogowaniaTests(TestCase):
    """Po 5 nieudanych próbach logowanie blokuje się tylko na okno 10 minut
    (a nie do końca sesji) — i komunikat podpowiada reset hasła."""

    def setUp(self):
        self.user = Pracownik.objects.create_user('ola', 'o@f.pl', 'Sup3rHaslo!')

    def _post(self, haslo='zle-haslo'):
        return self.client.post('/', {'username': 'ola', 'password': haslo})   # logowanie jest pod adresem '/'

    def test_po_5_probach_blokuje_sie_z_komunikatem(self):
        for _ in range(5):
            self._post()
        r = self._post()
        self.assertContains(r, 'Zbyt wiele nieudanych prób')
        self.assertContains(r, 'Nie pamiętasz hasła?')

    def test_po_minieciu_okna_10_min_mozna_sprobowac_znowu(self):
        for _ in range(5):
            self._post()
        self.assertContains(self._post(), 'Zbyt wiele nieudanych prób')
        # przesuwamy okno w czasie o 11 minut — blokada puszcza
        s = self.client.session
        s['okno_logowania'] = timezone.now().timestamp() - 11 * 60
        s.save()
        r = self._post()
        self.assertNotContains(r, 'Zbyt wiele nieudanych prób')

    def test_dobre_haslo_loguje_mimo_wczesniejszych_bledow(self):
        self._post()
        self._post()
        r = self._post('Sup3rHaslo!')
        self.assertEqual(r.status_code, 302)   # redirect po udanym logowaniu

    def test_stara_sesja_z_blokada_sama_sie_odblokowuje(self):
        # sesja zablokowana STARYM kodem (licznik 5 w sesji, bez okna czasowego)
        # po wdrożeniu nowego kodu sama się odblokowuje — nie trzeba czyścić ciasteczek
        s = self.client.session
        s['proby_logowania'] = 5
        s.save()
        r = self._post('Sup3rHaslo!')
        self.assertEqual(r.status_code, 302)   # od razu wchodzi, mimo że licznik był na 5

class EdycjaUzytkownikaTests(TestCase):
    """Edytuj użytkownika = formularz jak przy dodawaniu, ale wypełniony danymi;
    można też zmienić login i ustawić nowe hasło (puste = bez zmiany)."""

    def setUp(self):
        self.szef = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.prac = Pracownik.objects.create_user('prac', 'p@f.pl', 'StareHaslo123!')
        self.client.force_login(self.szef)

    def test_edycja_wypelniona_danymi_i_zmiana_loginu_i_hasla(self):
        r = self.client.get(f'/uzytkownicy/{self.prac.id}/edytuj/')
        self.assertContains(r, 'value="prac"')       # login podstawiony
        self.assertContains(r, 'value="p@f.pl"')     # e-mail podstawiony
        self.assertContains(r, 'id_haslo1')          # pola nowego hasła są na formularzu
        r = self.client.post(f'/uzytkownicy/{self.prac.id}/edytuj/', {
            'username': 'pracownik1', 'first_name': 'Piotr', 'last_name': 'Nowak',
            'email': 'p@f.pl', 'telefon': '600',
            'haslo1': 'NoweHaslo123!', 'haslo2': 'NoweHaslo123!',
        }, follow=True)
        self.prac.refresh_from_db()
        self.assertEqual(self.prac.username, 'pracownik1')
        self.assertTrue(self.prac.check_password('NoweHaslo123!'))
        # stary login odrzucony, nowy działa
        self.client.logout()
        r = self.client.post('/', {'username': 'prac', 'password': 'NoweHaslo123!'})
        self.assertNotEqual(r.status_code, 302)
        r = self.client.post('/', {'username': 'pracownik1', 'password': 'NoweHaslo123!'})
        self.assertEqual(r.status_code, 302)

    def test_zajety_login_jest_odrzucony(self):
        Pracownik.objects.create_user('inny', 'i@f.pl', 'Sup3rHaslo!')
        r = self.client.post(f'/uzytkownicy/{self.prac.id}/edytuj/', {
            'username': 'inny', 'first_name': '', 'last_name': '', 'email': '', 'telefon': '',
            'haslo1': '', 'haslo2': '',
        }, follow=True)
        self.assertContains(r, 'już zajęty')
        self.prac.refresh_from_db()
        self.assertEqual(self.prac.username, 'prac')

    def test_puste_haslo_zostawia_stare(self):
        self.client.post(f'/uzytkownicy/{self.prac.id}/edytuj/', {
            'username': 'prac', 'first_name': 'P', 'last_name': 'N', 'email': 'p@f.pl', 'telefon': '',
            'haslo1': '', 'haslo2': '',
        })
        self.prac.refresh_from_db()
        self.assertTrue(self.prac.check_password('StareHaslo123!'))


class KalendarzPrzeszleDniTests(TestCaseZamrozonaData):
    """W dodawaniu ucznia data 1. lekcji: tylko dziś i przyszłość (przeszłe dni szare)."""

    def setUp(self):
        self.user = Pracownik.objects.create_superuser('test', 't@f.pl', 'Sup3rHaslo!')

    def test_data_z_przeszlosci_odrzucona_serwerowo(self):
        self.client.force_login(self.user)
        wczoraj = date(2026, 9, 30).isoformat()   # „wczoraj” wg zamrożonej daty (2026-10-01)
        dane = {
            'data_pierwszej_lekcji': wczoraj,
            'godzina_pierwszej_lekcji': '17:00',
            'przedmiot': 'matematyka', 'poziom': 'podstawowka',
            'imie_ucznia': 'Kasia', 'nazwisko_rodzica': 'Nowak', 'rodzic': 'Ewa Nowak',
            'telefon_glowny': '600100200', 'email_glowny': 'ewa@f.pl',
            'telefon_dodatkowy': '', 'email_dodatkowy': '',
            'korepetytor': 'Anna Kowalska', 'kto_umowil': 'test',
            'notatka_od_rodzica': '', 'auto_wyslij_meet': 'on',
        }
        r = self.client.post('/uczniowie/', dane, follow=True)
        self.assertContains(r, 'nie może być z przeszłości')
        self.assertFalse(Uczen.objects.filter(imie_ucznia='Kasia').exists())

    def test_kalendarz_w_uczniowie_blokuje_przeszle_dni(self):
        self.client.force_login(self.user)
        r = self.client.get('/uczniowie/')
        self.assertContains(r, 'min="')   # pole daty ma min = dziś
        # tydzień z przeszłości: wszystkie dni mają klasę przeszly (szare)
        r = self.client.get('/uczniowie/?rok=2020&miesiac=1&dzien=6')
        self.assertContains(r, 'przeszly')


class EtykietyKorepetytorTests(TestCase):
    """W widocznym UI tylko „korepetytor” — słowo „nauczyciel” zostaje wyłącznie w kodzie."""

    def setUp(self):
        self.szef = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.client.force_login(self.szef)

    def test_etykiety_mowia_korepetytor_zamiast_nauczyciel(self):
        niedozwolone = ['>Nauczyciel<', 'nauczyciela lub', 'Szukaj nauczyciela',
                        'stawki nauczyciela', 'nauczyciele nie mają', 'tydzień” nauczyciela',
                        'nauczyciela nie pracuje', 'karta nauczyciela']
class RytmZakresowTests(TestCase):
    """Dwa zakresy w dniu (10–13 i 14–20): każdy liczy własny rytm (60 min + przerwa),
    zakresy nie przesuwają się względem siebie; w kalendarzu widać, o której kończą się zajęcia."""

    def setUp(self):
        self.admin = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.n = Nauczyciel.objects.create(imie='Jan', nazwisko='Kowalski', email='j@n.pl')  # przerwa domyślnie 5
        self.dzien = date(2026, 11, 27)  # piątek
        for od, do in [(time(10, 0), time(13, 0)), (time(14, 0), time(20, 0))]:
            k = datetime.combine(self.dzien, od)
            kon = datetime.combine(self.dzien, do)
            while k < kon:
                Dostepnosc.objects.create(
                    nauczyciel=self.n, data_od=self.dzien, data_do=self.dzien,
                    godzina_od=k.time(), godzina_do=(k + timedelta(minutes=30)).time(),
                )
                k += timedelta(minutes=30)

    def test_kazdy_zakres_ma_wlasny_rytm(self):
        from konta.views import wolne_godziny_nauczyciela
        wolne = wolne_godziny_nauczyciela(self.n, self.dzien)
        # zakres 10–13: 10:00, 11:05, 12:10, 13:15 (ostatni slot max 30 min poza 13:00)
        self.assertEqual(wolne[:4], ['10:00', '11:05', '12:10', '13:15'])
        # zakres 14–20 zaczyna się od 14:00 — nie od 14:15 jak w starym kodzie
        self.assertIn('14:00', wolne)
        self.assertIn('15:05', wolne)
        self.assertNotIn('14:15', wolne)
        self.assertNotIn('12:55', wolne)

    def test_plan_dnia_ajax_ma_rytm_per_zakres(self):
        self.client.force_login(self.admin)
        r = self.client.post(
            f'/nauczyciele/{self.n.id}/kalendarz/dane/',
            {'dzien': self.dzien.isoformat(), 'akcja': 'zapisz_przerwe', 'przerwa': '5'},
        )
        starty = [w['start'] for w in r.json()['plan_dnia'] if w['typ'] == 'lekcja']
        self.assertIn('13:15', starty)   # rytm zakresu 10–13
        self.assertIn('14:00', starty)   # zakres 14–20 zaczyna się od 14:00
        self.assertNotIn('14:15', starty)

    def test_zaznacz_zakres_pokazuje_koniec_zajec(self):
        self.client.force_login(self.admin)
        Dostepnosc.objects.filter(nauczyciel=self.n, data_od=self.dzien).delete()
        r = self.client.post(
            f'/nauczyciele/{self.n.id}/kalendarz/dane/',
            {'dzien': self.dzien.isoformat(), 'akcja': 'zaznacz_zakres', 'od': '10:00', 'do': '13:00'},
        )
        self.assertIn('13:15', r.json()['komunikat'])   # ostatnia lekcja 13:15
        self.assertIn('14:15', r.json()['komunikat'])   # zajęcia kończą się o 14:15

    def test_moj_tydzien_pokazuje_koniec_zajec_zakresu(self):
        self.client.force_login(self.admin)
        r = self.client.get(f'/moj-tydzien/{self.n.id}/?rok=2026&miesiac=11&dzien=27')
        self.assertContains(r, '13:15–14:15')   # zakres 10–13
        self.assertContains(r, '20:30–21:30')   # zakres 14–20

    def test_godzina_w_przerwie_liczona_per_zakres(self):
        from konta.models import PrzerwaDnia
        from konta.views import godzina_w_przerwie
        PrzerwaDnia.objects.create(nauczyciel=self.n, data=self.dzien, minuty=5)
        # przerwa między slotami zakresu 10–13: 12:05–12:10
        self.assertTrue(godzina_w_przerwie(self.n, self.dzien, time(12, 7)))
        # początek drugiego zakresu (14:00) NIE jest przerwą (stary kod tak liczył: 14:00–14:05)
        self.assertFalse(godzina_w_przerwie(self.n, self.dzien, time(14, 2)))
        self.assertFalse(godzina_w_przerwie(self.n, self.dzien, time(14, 0)))