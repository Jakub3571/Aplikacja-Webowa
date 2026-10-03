"""Testy przepływu logowania pracownika."""
from datetime import date

from django.core import mail
from django.test import TestCase

from konta.models import Dostepnosc, Nauczyciel, Pracownik, RaportPlatnosci, Uczen


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
        r = self.client.post('/', {'username': 'j.kowalski', 'password': 'Sup3rHaslo!'}, follow=True)
        self.assertTrue(r.context['user'].is_authenticated)
        self.assertRedirects(r, '/pulpit/')

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
    def setUp(self):
        self.user = Pracownik.objects.create_user('test', 't@f.pl', 'Sup3rHaslo!')

    def test_zakladki_dostepne_po_zalogowaniu(self):
        self.client.force_login(self.user)
        for adres in ['/pulpit/', '/uczniowie/', '/nauczyciele/', '/platnosci/']:
            r = self.client.get(adres)
            self.assertEqual(r.status_code, 200, f'{adres} nie dziala')

    def test_zakladki_blokowane_bez_zalogowania(self):
        for adres in ['/pulpit/', '/uczniowie/', '/nauczyciele/', '/platnosci/']:
            r = self.client.get(adres)
            self.assertEqual(r.status_code, 302, f'{adres} wpuszcza bez logowania')


class DodawanieUczniaTests(TestCase):
    def setUp(self):
        self.user = Pracownik.objects.create_user('test', 't@f.pl', 'Sup3rHaslo!')
        self.nauczyciel = Nauczyciel.objects.create(
            imie='Jan', nazwisko='Kowalski', przedmiot='matematyka', email='jan@k.pl',
        )

    def test_formularz_ucznia_zapisuje_do_bazy(self):
        self.client.force_login(self.user)
        dane = {
            'pierwsza_lekcja': '2026-10-05T17:00',
            'przedmiot': 'matematyka',
            'poziom': 'rozszerzony',
            'imie_ucznia': 'Kasia',
            'nazwisko_rodzica': 'Nowak',
            'rodzic': 'Ewa Nowak',
            'telefon_glowny': '600100200',
            'email_glowny': 'ewa@f.pl',
            'telefon_dodatkowy': '600300400',
            'email_dodatkowy': 'rezerwacja@f.pl',
            'korepetytor': 'Jan Kowalski',
            'notatka_od_rodzica': 'Uwaga na alergię',
        }
        r = self.client.post('/uczniowie/', dane, follow=True)
        self.assertContains(r, 'został dodany')
        self.assertEqual(Uczen.objects.count(), 1)
        uczen = Uczen.objects.first()
        self.assertEqual(uczen.kto_umowil, 'test')

    def test_korepetytor_filtruje_się_po_przedmiocie(self):
        Nauczyciel.objects.create(
            imie='Anna', nazwisko='Nowak', przedmiot='angielski', email='anna@n.pl',
        )
        self.client.force_login(self.user)
        r = self.client.post('/uczniowie/', {'przedmiot': 'matematyka', 'korepetytor': 'x'}, follow=True)
        self.assertContains(r, 'Jan Kowalski')
        self.assertNotContains(r, 'Anna Nowak')


class NauczycieleTests(TestCase):
    def setUp(self):
        self.user = Pracownik.objects.create_user('test', 't@f.pl', 'Sup3rHaslo!')
        self.nauczyciel = Nauczyciel.objects.create(
            imie='Jan', nazwisko='Kowalski', email='jan@k.pl',
        )

    def test_dodawanie_nauczyciela(self):
        self.client.force_login(self.user)
        dane = {'imie': 'Anna', 'nazwisko': 'Nowak', 'przedmiot': 'matematyka', 'email': 'anna@n.pl'}
        r = self.client.post('/nauczyciele/', dane, follow=True)
        self.assertContains(r, 'został dodany')
        self.assertEqual(Nauczyciel.objects.count(), 2)

    def test_usuwanie_nauczyciela(self):
        self.client.force_login(self.user)
        r = self.client.get(f'/nauczyciele/{self.nauczyciel.id}/usun/', follow=True)
        self.assertContains(r, 'usunięto z listy nauczycieli')
        self.assertEqual(Nauczyciel.objects.count(), 0)

    def test_kalendarz_przelnacza_kafelek(self):
        self.client.force_login(self.user)
        adres = f'/nauczyciele/{self.nauczyciel.id}/kalendarz/?rok=2026&miesiac=10&dzien=5'
        r = self.client.post(adres, {'przelacz_godzine': '1', 'godzina': '16:00'}, follow=True)
        self.assertContains(r, 'oznaczono jako dostępna')
        self.assertEqual(Dostepnosc.objects.count(), 1)
        slot = Dostepnosc.objects.first()
        self.assertEqual(slot.data_od, date(2026, 10, 5))
        self.assertEqual(slot.godzina_od.strftime('%H:%M'), '16:00')
        self.assertEqual(slot.godzina_do.strftime('%H:%M'), '16:30')

        r = self.client.post(adres, {'przelacz_godzine': '1', 'godzina': '16:00'}, follow=True)
        self.assertContains(r, 'odznaczono')
        self.assertEqual(Dostepnosc.objects.count(), 0)

    def test_kalendarz_pokazuje_siatke_miesiaca(self):
        self.client.force_login(self.user)
        Dostepnosc.objects.create(
            nauczyciel=self.nauczyciel,
            data_od='2026-10-05', data_do='2026-10-05',
            godzina_od='16:00', godzina_do='16:30',
        )
        r = self.client.get(f'/nauczyciele/{self.nauczyciel.id}/kalendarz/', {'rok': 2026, 'miesiac': 10, 'dzien': 5})
        self.assertContains(r, 'Październik')
        self.assertContains(r, 'dzien-dostepny')

    def test_uczniowie_pokazuja_dostepnosc(self):
        self.client.force_login(self.user)
        Dostepnosc.objects.create(
            nauczyciel=self.nauczyciel,
            data_od='2026-10-05', data_do='2026-10-10',
            godzina_od='16:00', godzina_do='20:00',
        )
        r = self.client.get('/uczniowie/')
        self.assertContains(r, 'Dostępność nauczycieli')
        self.assertContains(r, 'Jan Kowalski')


class PlatnosciTests(TestCase):
    def setUp(self):
        self.user = Pracownik.objects.create_user('test', 't@f.pl', 'Sup3rHaslo!')

    def dodaj_ucznia(self, imie='Kasia', zaplacone=False):
        return Uczen.objects.create(
            pierwsza_lekcja='2026-10-05T17:00',
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

    def test_platnosci_blokowane_bez_zalogowania(self):
        r = self.client.get('/platnosci/')
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
        self.assertContains(r, 'Nie można wysłać raportu')
        self.assertEqual(Uczen.objects.count(), 1)

    def test_raport_wysyla_i_czysci_liste(self):
        self.client.force_login(self.user)
        self.dodaj_ucznia('Kasia', zaplacone=True)
        self.dodaj_ucznia('Ola', zaplacone=True)
        r = self.client.post('/platnosci/', {'wyslij_raport': '1', 'email_raportu': 'szef@f.pl'}, follow=True)
        self.assertContains(r, 'Raport wysłano')
        self.assertEqual(Uczen.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['szef@f.pl'])
        self.assertEqual(RaportPlatnosci.objects.count(), 1)