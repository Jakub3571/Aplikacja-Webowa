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
        r = self.client.get(
            f'/nauczyciele/{self.nauczyciel.id}/kalendarz/',
            {'rok': 2026, 'miesiac': 10, 'dzien': 5},
            follow=True,
        )
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
        r = self.client.get(adres, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '16:00')
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
            'slot_godzina': ['17:05'],
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
        blokady = Dostepnosc.objects.filter(nauczyciel=self.nauczyciel, godzina_od='17:05')
        self.assertEqual(blokady.count(), lekcje.count())
    def test_okres_rok_startujacy_w_wakacje_lipiec_sierpien(self):
        # start w lipcu: "do końca roku szkolnego" musi kończyć się 30.06.2027,
        # a nie 30.06.2026 (data z przeszłości — wtedy powstawało 0 lekcji)
        self.client.force_login(self.pracownik)
        start = date(2026, 7, 15)
        dane = {
            'data_poczatkowa': start.isoformat(),
            'slot_dzien': [str(start.weekday())],
            'slot_godzina': ['17:05'],
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
        # dwa sloty na ten sam dzień (17:05 i 17:20) — drugi koliduje z pierwszym
        # (stary bug: oba przechodziły, bo walidacja omijała lekcje tego samego ucznia)
        self.client.force_login(self.pracownik)
        dzis = date.today()
        r = self.client.post(f'/uczniowie/{self.uczen.id}/lekcja-cykliczna/', {
            'okres': 'miesiac',
            'data_poczatkowa': dzis.isoformat(),
            'slot_dzien': [str(dzis.weekday()), str(dzis.weekday())],
            'slot_godzina': ['17:05', '17:20'],
            'link_meet': '',
        }, follow=True)
        self.assertContains(r, 'Pominięto')
        godziny = {timezone.localtime(l.data).strftime('%H:%M') for l in Lekcja.objects.filter(uczen=self.uczen)}
        self.assertEqual(len(godziny), 1)

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
            'slot_godzina': ['17:05', '18:10'],
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
        self.assertIn('17:05', godziny)
        self.assertIn('18:10', godziny)
        # blokady u korepetytora na obie godziny
        self.assertTrue(
            Dostepnosc.objects.filter(nauczyciel=self.nauczyciel, godzina_od='17:05').exists()
        )
        self.assertTrue(
            Dostepnosc.objects.filter(nauczyciel=self.nauczyciel, godzina_od='18:10').exists()
        )

    def test_preset_rok_szkolny(self):
        self.client.force_login(self.pracownik)
        dane = {
            'slot_dzien': ['1'],
            'slot_godzina': ['17:05'],
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
            'slot_godzina': ['17:05'],
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
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode
        self.token_generator = default_token_generator
        self.user = Pracownik.objects.create_user(
            'a.nowak', 'anna@firma.pl', 'Sup3rHaslo!',
        )
        self.uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        self.token = default_token_generator.make_token(self.user)

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
        self.assertContains(r, 'Link nie działa')

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




class KalendarzKorepetytoraTests(TestCase):
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
        for etykieta in ('11:00', '12:00'):
            self.assertNotIn(etykieta, wolne)
        self.assertIn('12:05', wolne)
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
        self.assertNotIn('12:00', etykiety)
        self.assertIn('12:05', etykiety)

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
        r = self.client.post('/uczniowie/', self._dane_ucznia('12:05'), follow=True)
        self.assertContains(r, 'został dodany')
        self.assertEqual(Uczen.objects.count(), 1)



class PoprawkiSzefaTests(TestCase):
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

    def test_komunikat_ma_skrypt_znikania_po_3s(self):
        r = self.client.get('/')
        self.assertContains(r, '3000')
        self.assertContains(r, 'komunikaty')

    def test_ajax_wolne_godziny_nauczyciela_rytm(self):
        self.client.force_login(self.user)
        r = self.client.get(f'/korepetytorzy/dane/?nauczyciel={self.nauczyciel.id}&data={self.dzien.isoformat()}')
        self.assertEqual(r.status_code, 200)
        godziny = [g['godzina'] for g in r.json()['godziny']]
        # siatka rytmu 65 min (60 lekcja + 5 przerwy) od 11:00; 12:05+60 wychodzi poza 13:00
        self.assertEqual(godziny, ['11:00'])


    def test_cykliczna_kwadracik_co_2_tygodnie(self):
        # checkbox co_2_tygodnie + okres rok: lekcje tylko co 14 dni (09.11, 23.11, ...)
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
        self.assertIn('2026-11-23', daty)      # +14 dni
        self.assertNotIn('2026-11-16', daty)   # tydzień pośredni — NIE ma
        self.assertNotIn('2026-11-30', daty)   # +21 dni — NIE ma
        godziny = {timezone.localtime(l.data).strftime('%H:%M') for l in lekcje}
        self.assertEqual(godziny, {'11:00'})

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


class komunikatBleduKalendarzaTests(TestCase):
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
            'data_pierwszej_lekcji': '2026-11-06', 'godzina_pierwszej_lekcji': '06:00',
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

    def test_szef_i_pracownik_widza_siatke_miesiaca(self):
        for user in (self.admin, self.pracownik):
            self.client.force_login(user)
            r = self.client.get('/kalendarz/')
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, 'Kalendarz')
            self.assertContains(r, 'poniedziałek')
            self.assertContains(r, 'Kasia Nowak')       # zielony chip lekcji (uczeń)
            self.assertContains(r, 'Jan Kowalski')      # nauczyciel przy lekcji
            self.assertContains(r, '17:00')
            self.assertContains(r, 'chip-lekcja')
            self.assertContains(r, 'Anna Nowak wolny')  # szary chip wolnego nauczyciela
            self.assertContains(r, '09:00–12:00')
            self.assertContains(r, 'chip-wolny')

    def test_nauczyciel_ma_403(self):
        self.client.force_login(self.teacher_user)
        r = self.client.get('/kalendarz/')
        self.assertEqual(r.status_code, 403)

    def test_strzalki_zmieniaja_miesiac(self):
        self.client.force_login(self.admin)
        r = self.client.get('/kalendarz/?rok=2026&miesiac=12')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'grudzień 2026')
        r = self.client.get('/kalendarz/?rok=2026&miesiac=1')
        self.assertContains(r, 'styczeń 2026')

class SiatkaRytmuTests(TestCase):
    """Krok 4: siatka rytmu (60 min lekcji + przerwa) od początku każdego przedziału;
    zajęta lekcja blokuje 1h + przerwa w obie strony (po 18:00 nie ma 18:30)."""

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
        # przedział 10:00-14:00, przerwa 5 min -> rytm 65 min: 10:00, 11:05, 12:10
        wolne = self.wolne(self.n, self.dzien)
        self.assertEqual(wolne, ['10:00', '11:05', '12:10'])

    def test_zajeta_lekcja_blokuje_1h_plus_przerwa(self):
        u = Uczen.objects.create(
            pierwsza_lekcja=timezone.make_aware(datetime.combine(self.dzien, self.czas(11, 0))),
            przedmiot='matematyka', poziom='podstawowka', szkola='SP', imie_ucznia='K',
            nazwisko_rodzica='N', rodzic='R', telefon_glowny='6', email_glowny='e@f.pl',
            telefon_dodatkowy='', korepetytor='Tomek Tomek', kto_umowil='szef')
        Lekcja.objects.create(uczen=u, data=u.pierwsza_lekcja)
        wolne = self.wolne(self.n, self.dzien)
        # zajęta 11:00; odpada też 10:00 (jej przerwa 11:00-11:05 zahacza o 11:00),
        # zostają łańcuchy wokół lekcji: 12:05 (po niej) i 12:10 (z siatki)
        self.assertNotIn('10:00', wolne)
        self.assertNotIn('11:00', wolne)
        self.assertNotIn('11:30', wolne)
        self.assertNotIn('12:00', wolne)
        self.assertIn('12:05', wolne)
        self.assertIn('12:10', wolne)
        self.assertTrue(self.wolny_o(self.n, self.dzien, datetime.combine(self.dzien, self.czas(12, 5))))
        self.assertFalse(self.wolny_o(self.n, self.dzien, datetime.combine(self.dzien, self.czas(11, 0))))
        self.assertFalse(self.wolny_o(self.n, self.dzien, datetime.combine(self.dzien, self.czas(11, 30))))
        self.assertFalse(self.wolny_o(self.n, self.dzien, datetime.combine(self.dzien, self.czas(12, 0))))

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
        self.assertNotIn('17:35', wolne)   # jej przerwa zahaczyłaby o 18:00
        self.assertIn('16:55', wolne)      # kończy się z przerwą tuż przed 18:00
        self.assertIn('19:05', wolne)      # koniec lekcji 19:00 + przerwa 5 min
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