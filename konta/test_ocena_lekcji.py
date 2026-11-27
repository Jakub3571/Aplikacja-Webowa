# -*- coding: utf-8 -*-
"""Etap 1: formularz korepetytora po lekcji (OcenaLekcji) + etapy ucznia u settera."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from konta.models import Lekcja, Nauczyciel, OcenaLekcji, Pracownik, Uczen
from konta.views import _etap_ucznia


class Etap1SetUp(TestCase):
    def setUp(self):
        self.admin = Pracownik.objects.create_superuser('admin', 'a@f.pl', 'Sup3rHaslo!')
        self.pracownik = Pracownik.objects.create_user('prac', 'p@f.pl', 'Sup3rHaslo!')
        self.jan = Nauczyciel.objects.create(imie='Jan', nazwisko='Kowalski', email='jan@n.pl')
        self.konto_jan = Pracownik.objects.create_user('jan', 'jan2@n.pl', 'Sup3rHaslo!', nauczyciel=self.jan)
        self.inny = Nauczyciel.objects.create(imie='Anna', nazwisko='Nowak', email='a2@n.pl')
        self.konto_anna = Pracownik.objects.create_user('anna', 'a3@n.pl', 'Sup3rHaslo!', nauczyciel=self.inny)
        self.uczen = Uczen.objects.create(
            pierwsza_lekcja=timezone.now() - timedelta(days=1),
            przedmiot='matematyka', poziom='srednia podstawa', szkola='SP 1',
            imie_ucznia='Kasia', nazwisko_rodzica='Nowak', rodzic='Ewa Nowak',
            telefon_glowny='600100200', email_glowny='ewa@f.pl',
            korepetytor='Jan Kowalski', kto_umowil='prac', umowil=self.pracownik,
        )
        self.lekcja = Lekcja.objects.create(
            uczen=self.uczen,
            data=timezone.now() - timedelta(days=1),
        )

    def _post(self, client, dane):
        return client.post(f'/lekcja/{self.lekcja.id}/ocena/', dane, follow=True)


class EtapyUczniaTests(Etap1SetUp):
    def test_etap_nowy_gdy_lekcja_w_przyszlosci(self):
        self.lekcja.data = timezone.now() + timedelta(days=2)
        self.lekcja.save()
        self.assertEqual(_etap_ucznia(self.uczen), 'nowy')

    def test_etap_do_potwierdzenia_po_terminie_bez_formularza(self):
        self.assertEqual(_etap_ucznia(self.uczen), 'do_potwierdzenia')

    def test_etap_odbyla_i_nie_odbyla_wg_oceny(self):
        OcenaLekcji.objects.create(lekcja=self.lekcja, odbyla_sie=True, temat='Ułamki')
        self.assertEqual(_etap_ucznia(self.uczen), 'odbyla')
        ocena = OcenaLekcji.objects.get(lekcja=self.lekcja)
        ocena.odbyla_sie = False
        ocena.powod_nieodbycia = 'uczen_nie_przyszedl'
        ocena.save()
        self.assertEqual(_etap_ucznia(self.uczen), 'nie_odbyla')


class FormularzPoLekcjiTests(Etap1SetUp):
    def test_korepetytor_wypelnia_tak(self):
        self.client.force_login(self.konto_jan)
        r = self._post(self.client, {
            'odbyla_sie': 'tak', 'temat': 'Ułamki dziesiętne',
            'skupienie': '5', 'dobrze_poszlo': 'szybko złapała',
            'nie_poszlo': '', 'zadanie_domowe': 'zadania 1-3 str. 42',
        })
        self.assertEqual(r.status_code, 200)
        ocena = OcenaLekcji.objects.get(lekcja=self.lekcja)
        self.assertTrue(ocena.odbyla_sie)
        self.assertEqual(ocena.temat, 'Ułamki dziesiętne')
        self.assertEqual(ocena.skupienie, 5)
        self.assertEqual(ocena.zadanie_domowe, 'zadania 1-3 str. 42')
        self.assertEqual(ocena.wypelnil, self.konto_jan)
        self.assertEqual(_etap_ucznia(self.uczen), 'odbyla')

    def test_korepetytor_wypelnia_nie_z_powodem(self):
        self.client.force_login(self.konto_jan)
        r = self._post(self.client, {
            'odbyla_sie': 'nie', 'powod_nieodbycia': 'odwolal_rodzic',
            'temat': 'IGNORUJ', 'skupienie': '1', 'zadanie_domowe': 'IGNORUJ',
        })
        self.assertEqual(r.status_code, 200)
        ocena = OcenaLekcji.objects.get(lekcja=self.lekcja)
        self.assertFalse(ocena.odbyla_sie)
        self.assertEqual(ocena.powod_nieodbycia, 'odwolal_rodzic')
        self.assertEqual(ocena.temat, '')
        self.assertIsNone(ocena.skupienie)
        self.assertEqual(ocena.zadanie_domowe, '')
        self.assertEqual(_etap_ucznia(self.uczen), 'nie_odbyla')

    def test_nie_bez_powodu_odrzucone(self):
        self.client.force_login(self.konto_jan)
        r = self._post(self.client, {'odbyla_sie': 'nie'})
        self.assertContains(r, 'Wybierz powód')
        self.assertFalse(OcenaLekcji.objects.filter(lekcja=self.lekcja).exists())

    def test_skupienie_ponizej_4_wymaga_wyjasnienia(self):
        self.client.force_login(self.konto_jan)
        r = self._post(self.client, {
            'odbyla_sie': 'tak', 'temat': 'x', 'skupienie': '3', 'zadanie_domowe': '-',
        })
        self.assertContains(r, 'co dokładnie się działo')
        self.assertFalse(OcenaLekcji.objects.filter(lekcja=self.lekcja).exists())
        r = self._post(self.client, {
            'odbyla_sie': 'tak', 'temat': 'x', 'skupienie': '3',
            'skupienie_wyjasnienie': 'rozpraszał się telefonem', 'zadanie_domowe': '-',
        })
        self.assertEqual(r.status_code, 200)
        ocena = OcenaLekcji.objects.get(lekcja=self.lekcja)
        self.assertEqual(ocena.skupienie, 3)
        self.assertEqual(ocena.skupienie_wyjasnienie, 'rozpraszał się telefonem')

    def test_brak_odpowiedzi_na_pyt1_odrzucone(self):
        self.client.force_login(self.konto_jan)
        r = self._post(self.client, {'temat': 'x'})
        self.assertContains(r, 'czy lekcja się odbyła')
        self.assertFalse(OcenaLekcji.objects.filter(lekcja=self.lekcja).exists())

    def test_get_formularza_i_dostep(self):
        self.client.force_login(self.konto_jan)
        r = self.client.get(f'/lekcja/{self.lekcja.id}/ocena/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Czy lekcja się odbyła')
        self.client.force_login(self.pracownik)
        r = self.client.get(f'/lekcja/{self.lekcja.id}/ocena/')
        self.assertEqual(r.status_code, 403)
        self.client.force_login(self.konto_anna)
        r = self.client.get(f'/lekcja/{self.lekcja.id}/ocena/')
        self.assertEqual(r.status_code, 403)
        self.client.force_login(self.admin)
        r = self.client.get(f'/lekcja/{self.lekcja.id}/ocena/')
        self.assertEqual(r.status_code, 200)

    def test_moj_tydzien_pokazuje_link_do_formularza(self):
        self.client.force_login(self.konto_jan)
        r = self.client.get('/moj-tydzien/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, f'/lekcja/{self.lekcja.id}/ocena/')

    def test_ponowne_wypelnienie_aktualizuje_ocena(self):
        self.client.force_login(self.konto_jan)
        self._post(self.client, {
            'odbyla_sie': 'tak', 'temat': 'stary temat', 'skupienie': '5', 'zadanie_domowe': '-',
        })
        self._post(self.client, {
            'odbyla_sie': 'tak', 'temat': 'nowy temat', 'skupienie': '4', 'zadanie_domowe': 'z.5',
        })
        self.assertEqual(OcenaLekcji.objects.filter(lekcja=self.lekcja).count(), 1)
        ocena = OcenaLekcji.objects.get(lekcja=self.lekcja)
        self.assertEqual(ocena.temat, 'nowy temat')
        self.assertEqual(ocena.skupienie, 4)