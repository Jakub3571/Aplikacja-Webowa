"""Testy przepływu logowania pracownika."""

from datetime import date

from django.core import mail
from django.test import TestCase

from konta.models import (
    Dostepnosc,
    Nauczyciel,
    Pracownik,
    PrzedmiotNauczyciela,
    RaportPlatnosci,
    Uczen,
)


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
        for adres in ['/pulpit/', '/uczniowie/', '/nauczyciele/', '/platnosci/']:
            r = self.client.get(adres)
            self.assertEqual(r.status_code, 200, f'{adres} nie dziala')

    def test_zakladki_blokowane_bez_zalogowania(self):
        oczekiwane = {'/pulpit/': 302, '/uczniowie/': 302, '/nauczyciele/': 403, '/platnosci/': 403}
        for adres, kod in oczekiwane.items():
            r = self.client.get(adres)
            self.assertEqual(r.status_code, kod, f'{adres} wpuszcza bez logowania')

    def test_pracownik_blokowany_na_panel_szefa(self):
        pracownik = Pracownik.objects.create_user('pracownik', 'p@f.pl', 'Sup3rHaslo!')
        self.client.force_login(pracownik)
        for adres in ['/nauczyciele/', '/platnosci/', '/lekcje/']:
            r = self.client.get(adres)
            self.assertEqual(r.status_code, 403, f'{adres} wpuszcza pracownika')
        r = self.client.get('/pulpit/')
        self.assertRedirects(r, '/uczniowie/')


class DodawanieUczniaTests(TestCase):
    def setUp(self):
        self.user = Pracownik.objects.create_superuser('test', 't@f.pl', 'Sup3rHaslo!')
        self.nauczyciel = Nauczyciel.objects.create(
            imie='Jan', nazwisko='Kowalski', email='jan@k.pl',
        )
        PrzedmiotNauczyciela.objects.create(
            nauczyciel=self.nauczyciel, przedmiot='matematyka', poziom='rozszerzenie',
        )

    def test_formularz_ucznia_zapisuje_do_bazy(self):
        self.client.force_login(self.user)
        dane = {
            'data_pierwszej_lekcji': '2026-10-05',
            'godzina_pierwszej_lekcji': '17:00',
            'przedmiot': 'matematyka',
            'poziom': 'szkoła średnia - rozszerzenie',
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
        inny = Nauczyciel.objects.create(
            imie='Anna', nazwisko='Nowak', email='anna@n.pl',
        )
        PrzedmiotNauczyciela.objects.create(
            nauczyciel=inny, przedmiot='angielski', poziom='podstawa',
        )
        self.client.force_login(self.user)
        r = self.client.post(
            '/uczniowie/',
            {'przedmiot': 'matematyka', 'poziom': 'szkoła średnia - rozszerzenie',
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
            nauczyciel=podstawowy, przedmiot='matematyka', poziom='podstawa',
        )
        self.client.force_login(self.user)
        r = self.client.post(
            '/uczniowie/',
            {'przedmiot': 'matematyka', 'poziom': 'szkoła średnia - rozszerzenie',
             'wybierz_przedmiot': '1'},
            follow=True,
        )
        opcje = r.context['form'].fields['korepetytor'].choices
        wartosci = [w for w, _ in opcje]
        self.assertIn('Jan Kowalski', wartosci)
        self.assertNotIn('Ola Wiśniewska', wartosci)


class NauczycieleTests(TestCase):
    def setUp(self):
        self.user = Pracownik.objects.create_superuser('test', 't@f.pl', 'Sup3rHaslo!')
        self.nauczyciel = Nauczyciel.objects.create(
            imie='Jan', nazwisko='Kowalski', email='jan@k.pl',
        )
        PrzedmiotNauczyciela.objects.create(
            nauczyciel=self.nauczyciel, przedmiot='matematyka', poziom='podstawa',
        )

    def test_dodawanie_nauczyciela(self):
        self.client.force_login(self.user)
        dane = {
            'imie': 'Anna', 'nazwisko': 'Nowak', 'email': 'anna@n.pl', 'stawka': '80',
            'przedmioty': ['matematyka', 'fizyka'], 'poziomy': ['podstawa', 'rozszerzenie'],
        }
        r = self.client.post('/nauczyciele/', dane, follow=True)
        self.assertContains(r, 'został dodany')
        self.assertEqual(Nauczyciel.objects.count(), 2)
        nowy = Nauczyciel.objects.get(imie='Anna')
        self.assertEqual(nowy.przedmioty.count(), 2)
        self.assertTrue(
            nowy.przedmioty.filter(przedmiot='fizyka', poziom='rozszerzenie').exists()
        )

    def test_archiwizacja_nauczyciela(self):
        self.client.force_login(self.user)
        r = self.client.get(f'/nauczyciele/{self.nauczyciel.id}/archiwum/', follow=True)
        self.assertContains(r, 'zakończono współpracę')
        self.assertEqual(Nauczyciel.objects.count(), 1)
        self.nauczyciel.refresh_from_db()
        self.assertFalse(self.nauczyciel.aktywny)
        r = self.client.get('/nauczyciele/')
        self.assertContains(r, 'Historia nauczycieli')

    def test_przywracanie_nauczyciela(self):
        self.client.force_login(self.user)
        self.nauczyciel.aktywny = False
        self.nauczyciel.save()
        r = self.client.get(f'/nauczyciele/{self.nauczyciel.id}/przywroc/', follow=True)
        self.assertContains(r, 'wznowiono współpracę')
        self.nauczyciel.refresh_from_db()
        self.assertTrue(self.nauczyciel.aktywny)

    def test_zarchiwizowany_nie_wyswietla_sie_w_formularzu_ucznia(self):
        self.client.force_login(self.user)
        self.nauczyciel.aktywny = False
        self.nauczyciel.save()
        r = self.client.post(
            '/uczniowie/',
            {'przedmiot': 'matematyka', 'poziom': 'szkoła średnia - podstawa',
             'wybierz_przedmiot': '1'},
            follow=True,
        )
        opcje = r.context['form'].fields['korepetytor'].choices
        wartosci = [w for w, _ in opcje]
        self.assertNotIn('Jan Kowalski', wartosci)

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
        self.user = Pracownik.objects.create_superuser('test', 't@f.pl', 'Sup3rHaslo!')

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
        self.assertEqual(r.status_code, 403)

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
        self.assertEqual(Uczen.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['szef@f.pl'])
        self.assertEqual(RaportPlatnosci.objects.count(), 1)

class UzytkownicyTests(TestCase):
    """Zakładka użytkownicy: dodawanie kont i uprawnienia (tylko szef)."""

    def setUp(self):
        self.user = Pracownik.objects.create_superuser('szef', 's@f.pl', 'Sup3rHaslo!')

    def test_zakladka_blokowana_dla_pracownika(self):
        pracownik = Pracownik.objects.create_user('prac', 'p@f.pl', 'Sup3rHaslo!')
        self.client.force_login(pracownik)
        r = self.client.get('/uzytkownicy/')
        self.assertEqual(r.status_code, 403)

    def test_dodawanie_pracownika(self):
        self.client.force_login(self.user)
        dane = {
            'username': 'j.nowak',
            'first_name': 'Janina',
            'last_name': 'Nowak',
            'email': 'j.nowak@f.pl',
            'stanowisko': 'Księgowa',
            'telefon': '600700900',
            'haslo1': 'M0taneHaslo!',
            'haslo2': 'M0taneHaslo!',
            'uprawnienia': 'pracownik',
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
            'uprawnienia': 'admin',
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
            'uprawnienia': 'pracownik',
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
        r = self.client.get('/nauczyciele/')
        self.assertEqual(r.status_code, 200)