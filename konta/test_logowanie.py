"""Testy przepływu logowania pracownika."""

from datetime import date, datetime, timedelta
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
    PrzypomnienieLekcji,
    RaportPlatnosci,
    Uczen,
   
)

from konta.views import kolizja_lekcji_nauczyciela

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
        for adres in ['/pulpit/', '/uczniowie/', '/platnosci/']:
            r = self.client.get(adres)
            self.assertEqual(r.status_code, 200, f'{adres} nie dziala')

    def test_zakladki_blokowane_bez_zalogowania(self):
        oczekiwane = {'/pulpit/': 302, '/uczniowie/': 302, '/platnosci/': 403}
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

class DodawanieUczniaTests(TestCase):
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
            'godzina_pierwszej_lekcji': '17:00',
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


class NauczycieleTests(TestCase):
    def setUp(self):
        self.user = Pracownik.objects.create_superuser('test', 't@f.pl', 'Sup3rHaslo!')
        self.nauczyciel = Nauczyciel.objects.create(
            imie='Jan', nazwisko='Kowalski', email='jan@k.pl',
        )
        PrzedmiotNauczyciela.objects.create(
            nauczyciel=self.nauczyciel, przedmiot='matematyka', poziom='srednia podstawa',
        )

    def test_kalendarz_dane_przelacza_godzine(self):
        self.client.force_login(self.user)
        adres = f'/nauczyciele/{self.nauczyciel.id}/kalendarz/dane/'
        r = self.client.post(adres, {'dzien': '2026-10-05', 'akcja': 'przelacz_godzine', 'godzina': '16:00'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Dostepnosc.objects.count(), 1)
        slot = Dostepnosc.objects.first()
        self.assertEqual(slot.data_od, date(2026, 10, 5))
        self.assertEqual(slot.godzina_od.strftime('%H:%M'), '16:00')
        self.assertEqual(slot.godzina_do.strftime('%H:%M'), '16:30')
        r = self.client.post(adres, {'dzien': '2026-10-05', 'akcja': 'przelacz_godzine', 'godzina': '16:00'})
        self.assertEqual(Dostepnosc.objects.count(), 0)

    def test_kalendarz_pokazuje_tydzien(self):
        self.client.force_login(self.user)
        Dostepnosc.objects.create(
            nauczyciel=self.nauczyciel,
            data_od='2026-10-05', data_do='2026-10-05',
            godzina_od='16:00', godzina_do='16:30',
        )
        r = self.client.get(f'/nauczyciele/{self.nauczyciel.id}/kalendarz/', {'rok': 2026, 'miesiac': 10, 'dzien': 5})
        self.assertContains(r, '16:00')
        self.assertContains(r, '16:30')

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
        # uczniowie zostają w aplikacji (zajęcia bez zmian) — czyścimy tylko znacznik zapłaty
        self.assertEqual(Uczen.objects.count(), 2)
        self.assertEqual(Uczen.objects.filter(zaplacone=True).count(), 0)
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

    def test_koordynator_moze_dodawac_konta_bez_admina(self):
        koordynator = Pracownik.objects.create_user('koordynator.ala', 'k@f.pl', 'Sup3rHaslo!')
        self.client.force_login(koordynator)
        r = self.client.get('/uzytkownicy/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Dodaj użytkownika')
        # lista ról bez admina
        self.assertNotContains(r, 'value="admin"')
        self.assertNotContains(r, 'Admini')
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
        r = self.client.get(f'/nauczyciele/{inny.id}/kalendarz/')
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

    def test_kreator_tylko_dla_nauczyciela(self):
        # stary kreator formularzowy został zastąpiony — przekierowuje na Moj tydzień,
        # który pracownik (bez konta nauczyciela) nie może otworzyć (403 po redyrekcie)
        pracownik = Pracownik.objects.create_user('prac', 'p@f.pl', 'Sup3rHaslo!')
        self.client.force_login(pracownik)
        r = self.client.get('/moja-dyspozycyjnosc/', follow=True)
        self.assertEqual(r.status_code, 403)

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
        godziny = sorted(d.godzina_od.strftime('%H:%M') for d in Dostepnosc.objects.filter(nauczyciel=self.nauczyciel))
        self.assertEqual(godziny, ['16:00'])

    def test_pracownik_widzi_kalendarz_bez_edycji(self):
        Dostepnosc.objects.create(
            nauczyciel=self.nauczyciel,
            data_od='2026-10-01', data_do='2026-10-01',
            godzina_od='16:00', godzina_do='16:30',
        )
        pracownik = Pracownik.objects.create_user('prac', 'p@f.pl', 'Sup3rHaslo!')
        self.client.force_login(pracownik)
        adres = f'/nauczyciele/{self.nauczyciel.id}/kalendarz/?rok=2026&miesiac=10&dzien=1'
        r = self.client.get(adres)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'tylko do podglądu')
        # proba edycji AJAX: POST odrzucony, nic sie nie zmienia
        r = self.client.post(
            f'/nauczyciele/{self.nauczyciel.id}/kalendarz/dane/',
            {'dzien': '2026-10-01', 'akcja': 'przelacz_godzine', 'godzina': '17:00'},
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Dostepnosc.objects.filter(nauczyciel=self.nauczyciel).count(), 1)

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
            'slot_godzina': ['17:00'],
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
        blokady = Dostepnosc.objects.filter(nauczyciel=self.nauczyciel, godzina_od='17:00')
        self.assertEqual(blokady.count(), lekcje.count())

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
        r = self.client.get(f'/lekcje/{self.uczen.id}/kalendarz/')
        self.assertEqual(r.status_code, 200)

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
            'slot_godzina': ['17:20', '19:15'],
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
        self.assertIn('17:20', godziny)
        self.assertIn('19:15', godziny)
        # blokady u korepetytora na obie godziny
        self.assertTrue(
            Dostepnosc.objects.filter(nauczyciel=self.nauczyciel, godzina_od='17:20').exists()
        )
        self.assertTrue(
            Dostepnosc.objects.filter(nauczyciel=self.nauczyciel, godzina_od='19:15').exists()
        )

    def test_preset_rok_szkolny(self):
        self.client.force_login(self.pracownik)
        dane = {
            'slot_dzien': ['1'],
            'slot_godzina': ['17:20'],
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
            'slot_godzina': ['17:20'],
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