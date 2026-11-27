import calendar
import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.views import PasswordResetConfirmView
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.mail import send_mail
from django.http import HttpResponse, JsonResponse, request
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .tokens import token_generator_resetu
from .forms import (

    FormularzLogowania,
    FormularzPrzedmiotuUcznia,
    FormularzUcznia,
    FormularzUzytkownika,
    FormularzZmianyHasla,
    FormularzLekcjiCyklicznej,
)

from .models import (
    POZIOMY_NAUCZYCIELA,
    PRZEDMIOTY,
    Dostepnosc,
    Lekcja,
    Nauczyciel,
    Pracownik,
    PrzedmiotNauczyciela,
    PrzedmiotUcznia,
    PrzerwaDnia,
    PrzypomnienieLekcji,
    RaportPlatnosci,
    Uczen,
    Zgloszenie,
)


MIESIACE = [
    'styczeń', 'luty', 'marzec', 'kwiecień', 'maj', 'czerwiec',
    'lipiec', 'sierpień', 'wrzesień', 'październik', 'listopad', 'grudzień',
]

DNI_TYGODNIA = ['Pn', 'Wt', 'Śr', 'Cz', 'Pt', 'So', 'Nd']

GODZINA_LEKCJI = 60

# Kolory poziomów w zakładce Uczniowie (kalendarz dostępności) — tylko tu, nie globalnie
MAPA_KLAS_POZIOMOW = {
    'podstawowka': 'poziom-podstawowka',          # zielony
    'srednia podstawa': 'poziom-srednia-podstawa',  # niebieski
    'srednia rozszerzenie': 'poziom-srednia-rozszerzenie',  # pomarańczowy
    'studia': 'poziom-studia',                      # czerwony
}


def _klasa_poziomu(poziom):
    """Klasa CSS koloru dla danego poziomu nauczania (zakładka Uczniowie)."""
    return MAPA_KLAS_POZIOMOW.get((poziom or '').strip().lower(), 'poziom-inne')

MAX_PROB_LOGOWANIA = 5
OKNO_PROB_SEK = 10 * 60  # okno czasowe: max 5 prób na 10 minut (a nie do końca sesji)


def moze_edytowac_kalendarz(user, nauczyciel_id):
    """Kalendarz edytuje: admin/szef albo nauczyciel-właściciel konta."""
    if czy_szef(user):
        return True
    return getattr(user, 'nauczyciel_id', None) == nauczyciel_id


def rola_context(request):
    """Oddaje role zalogowanego uzytkownika do KAZDEGO szablonu (menu itd.)."""
    u = getattr(request, 'user', None)
    if not u or not u.is_authenticated:
        return {
            'czy_szef_ctx': False,
            'czy_koordynator_ctx': False,
            'wersja_statycznych': settings.STATIC_VERSION,
        }
    return {
        'czy_szef_ctx': czy_szef(u),
        'czy_koordynator_ctx': czy_koordynator(u),
        'wersja_statycznych': settings.STATIC_VERSION,
    }


def czy_szef(u):
    """Prawdziwy szef: superuser LUB konto o nazwie 'Admin'."""
    return bool(u.is_authenticated and (u.is_superuser or u.username.strip().lower() == 'admin'))


def czy_koordynator(u):
    """Koordynator: konto z rola 'koordynator' (pole stanowisko) LUB loginem
    zaczynajacym sie od 'koordynator' (starsze konta).
    Widzi wszystkich uczniow, moze edytowac, ale nie moze usuwac."""
    if not u.is_authenticated:
        return False
    if (getattr(u, 'stanowisko', '') or '').strip().lower() == 'koordynator':
        return True
    return u.username.strip().lower().startswith('koordynator')


def tylko_szef(widok):
    """Puszcza dalej tylko superusera (szefa); reszta dostaje stronę „brak dostępu"."""
    def sprawdz(u):
        if czy_szef(u):
            return True
        raise PermissionDenied
    return user_passes_test(sprawdz, login_url='logowanie')(widok)


def zapisz_blad_maila(gdzie, blad):
    """Zapisuje nieudana wysylke maila do pliku logi_maili.txt w katalogu projektu."""
    plik = Path(settings.BASE_DIR) / 'logi_maili.txt'
    with open(plik, 'a', encoding='utf-8') as f:
        f.write(f'{datetime.now():%Y-%m-%d %H:%M:%S} | {gdzie} | {blad}\n')


def nauczyciel_z_korepetytora(korepetytor):
    """Znajduje kartę nauczyciela po polu 'korepetytor' ucznia (np. 'Anna Kowalska')."""
    if not korepetytor or ' ' not in korepetytor:
        return None
    imie, nazwisko = korepetytor.split(' ', 1)
    return Nauczyciel.objects.filter(imie__iexact=imie.strip(), nazwisko__iexact=nazwisko.strip()).first()
def _nauczyciel_lekcji(lekcja, uczen):
    """Nauczyciel prowadzący daną lekcję: z karty przedmiotu dodatkowego
    albo główny korepetytor ucznia."""
    korepetytor_lekcji = (
        lekcja.przedmiot_karta.korepetytor if lekcja.przedmiot_karta_id else uczen.korepetytor
    )
    return nauczyciel_z_korepetytora(korepetytor_lekcji)


def zwolnij_blokady_ucznia(uczen):
    """Zwalnia zablokowane godziny u WSZYSTKICH korepetytorów ucznia —
    głównego przedmiotu i kart przedmiotów dodatkowych."""
    for lekcja in Lekcja.objects.filter(uczen=uczen).select_related('przedmiot_karta'):
        nauczyciel = _nauczyciel_lekcji(lekcja, uczen)
        if not nauczyciel:
            continue
        start = timezone.localtime(lekcja.data)
        Dostepnosc.objects.filter(
            nauczyciel=nauczyciel,
            data_od=start.date(),
            godzina_od=start.time(),
        ).delete()


def odtworz_blokady_ucznia(uczen):
    """Odtwarza blokady godzin u wszystkich korepetytorów ucznia dla przyszłych
    lekcji. Zwraca (odtworzone, pominięte_kolizje)."""
    odtworzone = 0
    pominiete_kolizje = 0
    for lekcja in (
        Lekcja.objects.filter(uczen=uczen, data__gte=timezone.now()).select_related('przedmiot_karta')
    ):
        nauczyciel = _nauczyciel_lekcji(lekcja, uczen)
        if not nauczyciel:
            continue
        start = timezone.localtime(lekcja.data)
        if inne_lekcje_nauczyciela(nauczyciel, uczen, start.date(), start.time()):
            pominiete_kolizje += 1
            continue
        Dostepnosc.objects.get_or_create(
            nauczyciel=nauczyciel,
            data_od=start.date(),
            data_do=start.date(),
            godzina_od=start.time(),
            godzina_do=(datetime.combine(date(2000, 1, 1), start.time()) + timedelta(minutes=GODZINA_LEKCJI)).time(),
        )
        odtworzone += 1
    return odtworzone, pominiete_kolizje
def lekcja_jest_u_nauczyciela(lekcja, nazwa_nauczyciela):
    """Czy dana lekcja należy do zajęć podanego nauczyciela?
    Lekcja głównego przedmiotu: wg pola 'korepetytor' ucznia.
    Lekcja dodatkowego przedmiotu: wg korepetytora z karty przedmiotu."""
    if lekcja.przedmiot_karta_id:
        return (lekcja.przedmiot_karta.korepetytor or '').strip().lower() == nazwa_nauczyciela
    return (lekcja.uczen.korepetytor or '').strip().lower() == nazwa_nauczyciela

def _przerwa_dnia(nauczyciel, dzien):
    """Przerwa dnia ustawiona w „Mój tydzień” (PrzerwaDnia); gdy jej nie ma —
    domyślna przerwa nauczyciela (minimum 5 min)."""
    if not nauczyciel:
        return 5
    wpis = PrzerwaDnia.objects.filter(nauczyciel=nauczyciel, data=dzien).first()
    if wpis and wpis.minuty:
        return wpis.minuty
    return nauczyciel.przerwa or 5

def kolizja_lekcji_nauczyciela(nauczyciel, dzien, start_lekcji, przerwa_min=None, pomijaj_uczen_id=None):
    """Czy lekcja (60 min od start_lekcji) koliduje z istniejącą lekcją nauczyciela.

    Lekcja efektywnie zajmuje [start, koniec + przerwa) — dwie lekcje kolidują,
    gdy te przedziały zachodzą na siebie. Uwzględnia wszystkie przedmioty nauczyciela.
    pomijaj_uczen_id: lekcje TEGO ucznia nie blokują (cykliczne — pierwsza lekcja już istnieje).
    """
    if not nauczyciel:
        return False
    if przerwa_min is None:
        przerwa_min = _przerwa_dnia(nauczyciel, dzien)
    nazwa_nauczyciela = f'{nauczyciel.imie} {nauczyciel.nazwisko}'.strip().lower()
    moj_koniec = start_lekcji + timedelta(minutes=GODZINA_LEKCJI + przerwa_min)
    for lekcja in Lekcja.objects.select_related('uczen', 'przedmiot_karta').filter(data__date=dzien, odwolana=False):
        if pomijaj_uczen_id and lekcja.uczen_id == pomijaj_uczen_id:
            continue
        if not lekcja_jest_u_nauczyciela(lekcja, nazwa_nauczyciela):
            continue
        l_start = timezone.localtime(lekcja.data).replace(tzinfo=None)
        l_koniec = l_start + timedelta(minutes=GODZINA_LEKCJI + przerwa_min)
        if l_start < moj_koniec and start_lekcji < l_koniec:
            return True
    return False


def inne_lekcje_nauczyciela(nauczyciel, uczen, dzien, godzina_start):
    """Lekcje innych uczniów tego nauczyciela nachodzące na slot (60 min od godzina_start).
    Uwzględnia wszystkie przedmioty nauczyciela (główny i dodatkowe)."""
    if not nauczyciel:
        return []
    start_slotu = datetime.combine(dzien, godzina_start)
    koniec_slotu = start_slotu + timedelta(minutes=GODZINA_LEKCJI)
    nazwa_nauczyciela = f'{nauczyciel.imie} {nauczyciel.nazwisko}'.strip().lower()
    kolidujace = []
    for lekcja in Lekcja.objects.select_related('uczen', 'przedmiot_karta').filter(data__date=dzien, odwolana=False):
        if lekcja.uczen_id == uczen.id:
            continue
        if not lekcja_jest_u_nauczyciela(lekcja, nazwa_nauczyciela):
            continue
        start_lekcji = timezone.localtime(lekcja.data).replace(tzinfo=None)
        koniec_lekcji = start_lekcji + timedelta(minutes=GODZINA_LEKCJI)
        if start_slotu < koniec_lekcji and start_lekcji < koniec_slotu:
            kolidujace.append(lekcja)
    return kolidujace

def _zakresy_dnia(nauczyciel, dzien):
    """Zakresy dostępności nauczyciela w dniu jako lista (start, koniec) datetimeów.
    Skleja sloty, które się stykają LUB nachodzą na siebie (unia przedziałów) —
    dzięki temu pojedynczy „krzywy” slot (np. 11:10–12:10 po umówionej lekcji)
    nie rozbija sąsiednich zakresów i kolejna lekcja dalej mieści się w całości."""
    zakresy = []
    for termin in Dostepnosc.objects.filter(nauczyciel=nauczyciel, data_od__lte=dzien, data_do__gte=dzien).order_by('godzina_od'):
        s = datetime.combine(dzien, termin.godzina_od)
        e = datetime.combine(dzien, termin.godzina_do)
        if zakresy and s <= zakresy[-1][1]:
            zakresy[-1] = (zakresy[-1][0], max(zakresy[-1][1], e))
        else:
            zakresy.append((s, e))
    return zakresy


def _rytm_zakresu(s, e, krok):
    """Sloty rytmu JEDNEGO zakresu (start, koniec): od startu co krok (60 min + przerwa),
    ostatni slot może wyjść max 30 min poza koniec zakresu. Każdy zakres liczy własny rytm —
    zakresy nie „dostosowują się” do siebie nawzajem."""
    sloty = []
    t = s
    while t <= e + timedelta(minutes=30):
        sloty.append(t)
        t += krok
    return sloty


def nauczyciel_wolny_o(nauczyciel, dzien, start_lekcji):
    """Czy nauczyciel może przyjąć lekcję 60 min od start_lekcji — dokładnie te same
    godziny, które zwraca wolne_godziny_nauczyciela (ta sama siatka, kolizje i przerwy).
    Dzięki temu lista rozwijana i walidacja formularza zawsze pokazują to samo."""
    if not nauczyciel:
        return False
    return start_lekcji.strftime('%H:%M') in wolne_godziny_nauczyciela(nauczyciel, dzien)


def wolne_godziny_nauczyciela(nauczyciel, dzien, pomijaj_uczen_id=None):
    """Godziny startu lekcji pokazywane w FORMULARZU dodawania ucznia (i w AJAX) —
    RYTM dnia korepetytora: lekcja 60 min + przerwa TEGO DNIA, liczony od początku
    każdego przedziału dostępności. Przykład: zakres 6:00–15:00, przerwa 10 min
    → 6:00, 7:10, 8:20, 9:30, 10:40, 11:50, 13:00, 14:10. Dodatkowo KONIEC przedziału
    WŁĄCZNY („do 15:00” = można też o 15:00) oraz sloty PRZYLEGŁE do umówionych lekcji,
    przesunięte o przerwę: po lekcji = koniec + przerwa (lekcja 11:50, przerwa 10 → 13:00),
    przed lekcją = start − 60 min − przerwa. Kolizja zajmuje [start, koniec + przerwa).
    Sztywne pełne godziny są TYLKO w kafelkach „Mój tydzień” u korepetytora."""
    wynik = []
    if not nauczyciel:
        return wynik
    przerwa = _przerwa_dnia(nauczyciel, dzien)
    nazwa_nauczyciela = f'{nauczyciel.imie} {nauczyciel.nazwisko}'.strip().lower()
    lekcje_starty = [
        timezone.localtime(l.data).replace(tzinfo=None)
        for l in Lekcja.objects.select_related('uczen', 'przedmiot_karta').filter(data__date=dzien, odwolana=False)
        if lekcja_jest_u_nauczyciela(l, nazwa_nauczyciela)
    ]
    krok = timedelta(minutes=GODZINA_LEKCJI + przerwa)
    for s, e in _zakresy_dnia(nauczyciel, dzien):
        kandydaci = set()
        # rytm liczony osobno dla każdego zakresu (ostatni slot max 30 min poza koniec zakresu)
        kandydaci.update(_rytm_zakresu(s, e, krok))
        for l_start in lekcje_starty:
            po = l_start + timedelta(minutes=GODZINA_LEKCJI + przerwa)
            przed = l_start - timedelta(minutes=GODZINA_LEKCJI + przerwa)
            if s <= po <= e:
                kandydaci.add(po)
            if s <= przed <= e:
                kandydaci.add(przed)
        for start in sorted(kandydaci):
            if kolizja_lekcji_nauczyciela(nauczyciel, dzien, start, przerwa_min=przerwa, pomijaj_uczen_id=pomijaj_uczen_id):
                continue
            wynik.append(start.strftime('%H:%M'))
    return sorted(set(wynik))

def _tekst_liczby_godzin(n):
    """Polska odmiana liczby wolnych godzin: 'wolna 1 godzina', 'wolne 2 godziny',
    'wolnych 5 godzin' (panel dostępności)."""
    if n == 1:
        return 'wolna 1 godzina'
    if n % 10 in (2, 3, 4) and not 12 <= n % 100 <= 14:
        return f'wolne {n} godziny'
    return f'wolnych {n} godzin'


def _zakresy_wolne_dnia(nauczyciel, dzien, wolne):
    """Wolne podzakresy każdego zakresu dostępności dnia (panel 'Kalendarz dostępności'):
    rytm zakresu (60 min + przerwa dnia) bez zajętych slotów, wolne sloty sklejane
    w ciągłe grupy 'od–do' (pierwszy i ostatni wolny slot, np. 6:00–10:40 i 13:00–14:10,
    gdy 11:50 jest zajęte). Gdy WSZYSTKIE sloty zakresu są zajęte — 'zajęte'."""
    przerwa = _przerwa_dnia(nauczyciel, dzien)
    krok = timedelta(minutes=GODZINA_LEKCJI + przerwa)
    wolne_set = set(wolne)
    wynik = []
    for s, e in _zakresy_dnia(nauczyciel, dzien):
        sloty = _rytm_zakresu(s, e, krok)
        wolne_sloty = [t for t in sloty if t.strftime('%H:%M') in wolne_set]
        if not wolne_sloty:
            wynik.append('zajęte')
            continue
        grupy = []
        for t in wolne_sloty:
            if grupy and t - grupy[-1][-1] == krok:
                grupy[-1].append(t)
            else:
                grupy.append([t])
        wynik.extend(f'{g[0]:%H:%M}–{g[-1]:%H:%M}' for g in grupy)
    return wynik


def godzina_w_przerwie(nauczyciel, dzien, godzina_start, uczen_id=None):
    """Sprawdza, czy slot (60 min) wypada w luce przerwy dnia nauczyciela.
    Przerwa to luka między blokami zajęć wyliczana wg planu dnia."""
    if not nauczyciel:
        return False
    wpis = PrzerwaDnia.objects.filter(nauczyciel=nauczyciel, data=dzien).first()
    if not wpis or not wpis.minuty:
        return False
    # przerwy liczone per zakres — tak samo jak rytm w wolne_godziny_nauczyciela:
    # między kolejnymi slotami jednego zakresu; początek kolejnego zakresu NIE jest przerwą
    krok = timedelta(minutes=GODZINA_LEKCJI + wpis.minuty)
    for s, e in _zakresy_dnia(nauczyciel, dzien):
        sloty = _rytm_zakresu(s, e, krok)
        for i, t in enumerate(sloty):
            if i + 1 < len(sloty):
                poczatek_przerwy = (t + timedelta(minutes=GODZINA_LEKCJI)).time()
                koniec_przerwy = sloty[i + 1].time()
                if poczatek_przerwy <= godzina_start < koniec_przerwy:
                    return True
    return False


def _bezpieczny_next(request):
    """Parametr ?next= tylko gdy to ścieżka wewnętrzna (ochrona przed otwarciem zewnętrznej strony)."""
    nxt = request.GET.get('next') or request.POST.get('next') or ''
    if nxt.startswith('/') and not nxt.startswith('//') and not nxt.startswith('/\\'):
        return nxt
    return ''


def logowanie(request):
    """Wyświetla formularz logowania i loguje pracownika po poprawnych danych.
    Ochrona przed zgadywaniem haseł: max 5 nieudanych prób w oknie 10 minut —
    potem blokada tylko do końca tego okna (a nie do wygaśnięcia sesji).
    Po zalogowaniu wraca do ?next= (np. /cms/)."""
    if request.user.is_authenticated:
        nxt = _bezpieczny_next(request)
        return redirect(nxt) if nxt else redirect('pulpit')
    if request.method == 'POST':
        teraz = timezone.now().timestamp()
        okno = request.session.get('okno_logowania', 0)
        if not okno or teraz - okno >= OKNO_PROB_SEK:
            # nowe okno 10-minutowe — licznik prób od zera
            okno = teraz
            request.session['okno_logowania'] = okno
            request.session['proby_logowania'] = 0
        if request.session.get('proby_logowania', 0) >= MAX_PROB_LOGOWANIA:
            pozostalo = int((OKNO_PROB_SEK - (teraz - okno)) // 60) + 1
            messages.error(
                request,
                f'Zbyt wiele nieudanych prób logowania — spróbuj ponownie za ok. {pozostalo} min '
                'albo kliknij „Nie pamiętasz hasła?”.',
            )
            return render(request, 'konta/logowanie.html', {'form': FormularzLogowania(request)})
        request.session['proby_logowania'] = request.session.get('proby_logowania', 0) + 1
    form = FormularzLogowania(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.get_user()
        login(request, user)
        request.session['proby_logowania'] = 0
        request.session.pop('okno_logowania', None)
        messages.success(request, f'Witaj, {user.get_full_name() or user.username}!')
        nxt = _bezpieczny_next(request)
        return redirect(nxt) if nxt else redirect('pulpit')
    return render(request, 'konta/logowanie.html', {'form': form})


def wyslij_powiadomienie_o_uczniu(request, uczen):
    """Wysyła e-mail z powiadomieniem, że do systemu wpisano nowego ucznia.
    Idzie do biura (EMAILE_POWIADOMIEN) oraz do przypisanego korepetytora."""
    odbiorcy = list(getattr(settings, 'EMAILE_POWIADOMIEN', []))
    nauczyciel = nauczyciel_z_korepetytora(uczen.korepetytor)
    if nauczyciel and nauczyciel.email and nauczyciel.email not in odbiorcy:
        odbiorcy.append(nauczyciel.email)
    if not odbiorcy:
        return
    tresc = (
        f'Nowy uczeń w systemie:\n\n'
        f'Uczeń: {uczen.imie_ucznia} {uczen.nazwisko_rodzica}\n'
        f'Przedmiot: {uczen.przedmiot} — poziom: {uczen.poziom}\n'
        f'Korepetytor: {uczen.korepetytor}\n'
        f'Rodzic: {uczen.rodzic} (tel. {uczen.telefon_glowny}, {uczen.email_glowny})\n'
        f'1. lekcja: {uczen.pierwsza_lekcja:%d.%m.%Y %H:%M}\n'
        f'Wpisał: {uczen.kto_umowil}\n'
    )
    try:
        send_mail(
            'Nowy uczeń — Panel pracowniczy',
            tresc,
            None,
            odbiorcy,
        )
    except Exception as e:
        zapisz_blad_maila('powiadomienie_biuro_o_nowym_uczniu', e)


def wyslij_powiadomienie_nauczycielowi(request, uczen):
    """Wysyła korepetytorowi e-mail z informacją o nowym uczniu i terminie 1. lekcji."""
    nauczyciel = nauczyciel_z_korepetytora(uczen.korepetytor)
    if not nauczyciel or not nauczyciel.email:
        return
    tresc = (
        f'Dzień dobry {nauczyciel.imie},\n\n'
        f'do Twojego kalendarza wpisano nowego ucznia:\n\n'
        f'Uczeń: {uczen.imie_ucznia} {uczen.nazwisko_rodzica}\n'
        f'Przedmiot: {uczen.przedmiot} — poziom: {uczen.poziom}\n'
        f'Rodzic: {uczen.rodzic} (tel. {uczen.telefon_glowny})\n'
        f'1. lekcja: {uczen.pierwsza_lekcja:%d.%m.%Y o godz. %H:%M}\n\n'
        f'Zajęcia pojawią się w Twoim kalendarzu w zakładce „Lekcje”.\n\n'
        f'Wpisał: {uczen.kto_umowil}\n'
    )
    try:
        send_mail(
            f'Nowy uczeń: {uczen.imie_ucznia} — 1. lekcja {uczen.pierwsza_lekcja:%d.%m.%Y %H:%M}',
            tresc,
            None,
            [nauczyciel.email],
        )
    except Exception as e:
        zapisz_blad_maila('powiadomienie_nauczycielowi', e)

def wyslij_discord_pierwsza_lekcja(request, uczen):
    """POST na DISCORD_WEBHOOK_URL: info o nowym uczniu i 1. lekcji ze wzmianką <@discord_id>
    korepetytora (jeśli podał ID na swoim koncie w „Mój tydzień")."""
    import os as _os
    webhook = _os.environ.get('DISCORD_WEBHOOK_URL', '')
    if not webhook:
        return
    nauczyciel = nauczyciel_z_korepetytora(uczen.korepetytor)
    if not nauczyciel:
        return
    discord_id = (getattr(nauczyciel, 'discord_id', '') or '').strip()
    wzmianka = f'<@{discord_id}> ' if discord_id else ''
    tresc = (
        f'{wzmianka}**Nowy uczeń u {nauczyciel.imie} {nauczyciel.nazwisko}** — {uczen.imie_ucznia} {uczen.nazwisko_rodzica}\n'
        f'Przedmiot: {uczen.przedmiot} — poziom: {uczen.poziom}\n'
        f'Rodzic: {uczen.rodzic} (tel. {uczen.telefon_glowny})\n'
        f'1. lekcja: {uczen.pierwsza_lekcja:%d.%m.%Y o godz. %H:%M}\n'
        f'Wpisał: {uczen.kto_umowil}'
    )
    try:
        import urllib.request as _urllib
        zadanie = _urllib.Request(
            webhook,
            data=json.dumps({'content': tresc}).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'User-Agent': 'NowoczesneKorepetycje-Panel/1.0'},
            method='POST',
        )
        with _urllib.urlopen(zadanie, timeout=10) as odp:
            odp.read()
    except Exception as e:
        zapisz_blad_maila('discord_pierwsza_lekcja', e)

@login_required
def wyloguj(request):
    """Wylogowuje pracownika i wraca na stronę logowania."""
    logout(request)
    messages.info(request, 'Zostałeś wylogowany. Do zobaczenia!')
    return redirect('logowanie')


@login_required
def pulpit(request):
    """Strona startowa: szef i koordynator widza pulpit, pracownik od razu formularz ucznia."""
    if not czy_szef(request.user) and not czy_koordynator(request.user):
        if getattr(request.user, 'nauczyciel_id', None):
            # nauczyciel: od razu swój tydzień w stylu Calendly (godziny dodaje plusikiem)
            return redirect('moj_tydzien')
        return redirect('uczniowie')
    return render(request, 'konta/pulpit.html')


def dostepnosci_tygodnia(dni):
    """Dostępność aktywnych nauczycieli dzień po dniu (7 dni):
    [{'data', 'nazwa', 'dzisiaj', 'wpisy': [{'nauczyciel', 'zakresy': ['08:00–12:00']}]}].
    Sloty tego samego dnia sklejane w zakresy (tak jak w „Mój tydzień”)."""
    dni_pelne = ['poniedziałek', 'wtorek', 'środa', 'czwartek', 'piątek', 'sobota', 'niedziela']
    nauczyciele = list(
        Nauczyciel.objects.filter(aktywny=True)
        .prefetch_related('przedmioty')
        .order_by('nazwisko', 'imie')
    )
    # przedmioty nauczyciela z klasą koloru poziomu (legenda w zakładce Uczniowie) — raz na nauczyciela, bez N+1
    przedmioty_na_nauczyciela = {
        n.id: [
            {'przedmiot': p.przedmiot, 'klasa': _klasa_poziomu(p.poziom)}
            for p in sorted(n.przedmioty.all(), key=lambda p: p.przedmiot)
        ]
        for n in nauczyciele
    }
    terminy = (
        Dostepnosc.objects
        .filter(data_od__lte=dni[-1], data_do__gte=dni[0])
        .order_by('nauczyciel_id', 'godzina_od')
    )
    po_nauczycielu = {}
    for t in terminy:
        po_nauczycielu.setdefault(t.nauczyciel_id, []).append(t)
    wynik = []
    for dzien in dni:
        wpisy = []
        for n in nauczyciele:
            zakresy = []
            for termin in po_nauczycielu.get(n.id, []):
                if not (termin.data_od <= dzien <= termin.data_do):
                    continue
                s = datetime.combine(dzien, termin.godzina_od)
                e = datetime.combine(dzien, termin.godzina_do)
                if zakresy and s <= zakresy[-1][1]:
                    zakresy[-1] = (zakresy[-1][0], max(zakresy[-1][1], e))
                else:
                    zakresy.append((s, e))
            if zakresy:
                wolne = wolne_godziny_nauczyciela(n, dzien)
                wpisy.append({
                    'nauczyciel': n,
                    'przedmioty': przedmioty_na_nauczyciela.get(n.id, ''),
                    'zakresy': [f'{s:%H:%M}–{e:%H:%M}' for s, e in zakresy],
                    'wolne_godziny': wolne,
                    'liczba_wolnych': len(wolne),
                    'liczba_wolnych_tekst': _tekst_liczby_godzin(len(wolne)),
                    'zakresy_wolne': _zakresy_wolne_dnia(n, dzien, wolne),
                })
        wynik.append({
            'data': dzien,
            'nazwa': dni_pelne[dzien.weekday()],
            'dzisiaj': dzien == date.today(),
            'przeszly': dzien < date.today(),
            'wpisy': wpisy,
        })
    return wynik


def _kontekst_dostepnosci(request):
    """Tydzień (pon–nd) do panelu „Kalendarz dostępności” w Uczniowie — wybór przez ?rok=&miesiac=&dzien=."""
    dzis = date.today()
    try:
        start = date(
            int(request.GET.get('rok') or dzis.year),
            int(request.GET.get('miesiac') or dzis.month),
            int(request.GET.get('dzien') or dzis.day),
        )
    except ValueError:
        start = dzis
    poniedzialek = start - timedelta(days=start.weekday())
    dni = [poniedzialek + timedelta(days=i) for i in range(7)]
    return {
        'tydzien_dostepnosci': dostepnosci_tygodnia(dni),
        'zakres_tygodnia': f'{poniedzialek:%d.%m} – {dni[-1]:%d.%m.%Y}',
        'poprzedni_tydzien': poniedzialek - timedelta(days=7),
        'nastepny_tydzien': poniedzialek + timedelta(days=7),
    }


@login_required
def korepetytorzy_dane(request):
    """AJAX: zwraca listę aktywnych korepetytorów dla przedmiotu i poziomu.
    Gdy podano datę i godzinę 1. lekcji — tylko tych, którzy zaznaczyli ten termin w kalendarzu."""
    if (
        getattr(request.user, 'nauczyciel_id', None)
        and str(request.user.nauczyciel_id) != request.GET.get('nauczyciel', '')
    ):
        return JsonResponse({'blad': 'Brak uprawnień.'}, status=403)
    przedmiot = request.GET.get('przedmiot', '')
    poziom = request.GET.get('poziom', '')
    data_txt = request.GET.get('data', '')
    godzina_txt = request.GET.get('godzina', '')
    # tryb wolnych godzin JEDNEGO nauczyciela w konkretnym dniu (kreator lekcji cyklicznej)
    nauczyciel_id = request.GET.get('nauczyciel', '')
    uczen_id_txt = request.GET.get('uczen', '')
    pomijaj_uczen_id = int(uczen_id_txt) if uczen_id_txt.isdigit() else None
    if nauczyciel_id and data_txt:
        try:
            dzien_tmp = date.fromisoformat(data_txt)
        except ValueError:
            dzien_tmp = None
        if dzien_tmp:
            nt = Nauczyciel.objects.filter(pk=nauczyciel_id).first()
            if nt:
                # dni tygodnia, w które nauczyciel w ogóle pracuje (kolejne 60 dni)
                dni_pracy = set()
                for termin in Dostepnosc.objects.filter(
                    nauczyciel=nt,
                    data_od__lt=date.today() + timedelta(days=60),
                    data_do__gte=date.today(),
                ):
                    d = max(termin.data_od, date.today())
                    koniec = min(termin.data_do, date.today() + timedelta(days=60))
                    while d <= koniec:
                        dni_pracy.add(d.weekday())
                        d += timedelta(days=1)
                dni_pracy = sorted(dni_pracy)
                dni_cykliczne = sorted({
                    t.data_od.weekday()
                    for t in Dostepnosc.objects.filter(nauczyciel=nt, cykliczny=True, data_od__gte=date.today())
                })
                godziny = wolne_godziny_nauczyciela(nt, dzien_tmp, pomijaj_uczen_id=pomijaj_uczen_id)
                if not godziny:
                    for i in range(1, 9):
                        godziny += wolne_godziny_nauczyciela(
                            nt, dzien_tmp + timedelta(weeks=i), pomijaj_uczen_id=pomijaj_uczen_id,
                        )
                    godziny = sorted(set(godziny))
                return JsonResponse({
                    'godziny': [{'godzina': h} for h in godziny],
                    'data': dzien_tmp.isoformat(),
                    'dzien_nazwa': DNI_TYGODNIA[dzien_tmp.weekday()],
                    'dni_pracy': dni_pracy,
                    'dni_cykliczne': dni_cykliczne,
                })
            return JsonResponse({'godziny': []})
    if not przedmiot or not poziom:
        return JsonResponse({'korepetytorzy': []})
    poziom_nauczyciela = {
        'podstawowka': 'podstawowka',
        'podstawówka': 'podstawowka',
        'srednia podstawa': 'srednia podstawa',
        'srednia rozszerzenie': 'srednia rozszerzenie',
        'studia': 'studia',
    }.get(poziom, poziom)
    szukane_poziomy = (poziom_nauczyciela,)

    dzien = None
    godzina = None
    if data_txt:
        try:
            dzien = date.fromisoformat(data_txt)
        except ValueError:
            dzien = None
    if godzina_txt:
        try:
            godzina = datetime.strptime(godzina_txt, '%H:%M').time()
        except ValueError:
            godzina = None

    korepetytorzy = []
    # tryb GODZINY: dla przedmiotu/poziomu/daty zwracamy dostępne godziny lekcji (z przerwami)
    tryb = request.GET.get('tryb', '')
    if tryb == 'godziny' and dzien:
        # te same zasady co walidacja formularza (wolne_godziny_nauczyciela):
        # dostępność + kolizje z lekcjami + przerwy — lista nigdy nie „kłamie”
        godziny_wynik = []
        for n in Nauczyciel.objects.filter(aktywny=True).prefetch_related('przedmioty').order_by('nazwisko', 'imie'):
            pasuje_przedmiot = any(
                p.przedmiot == przedmiot and p.poziom in szukane_poziomy
                for p in n.przedmioty.all()
            )
            if not pasuje_przedmiot:
                continue
            for godzina_txt in wolne_godziny_nauczyciela(n, dzien):
                godziny_wynik.append({'godzina': godzina_txt, 'korepetytor': f'{n.imie} {n.nazwisko}'})
        godziny_wynik = sorted(godziny_wynik, key=lambda g: (g['godzina'], g['korepetytor']))
        return JsonResponse({'godziny': godziny_wynik})
    for n in Nauczyciel.objects.filter(aktywny=True).prefetch_related('przedmioty').order_by('nazwisko', 'imie'):
        pasuje_przedmiot = any(
            p.przedmiot == przedmiot and p.poziom in szukane_poziomy
            for p in n.przedmioty.all()
        )
        if not pasuje_przedmiot:
            continue
        if dzien and godzina:
            # lekcja (60 min) musi mieścić się w zakresie dostępności nauczyciela
            # i nie kolidować z lekcjami ani okienkiem przerwy po nich
            start_lekcji = datetime.combine(dzien, godzina)
            if not nauczyciel_wolny_o(n, dzien, start_lekcji):
                continue
        elif dzien:
            # sama data (bez godziny): tylko korepetytorzy, którzy tego dnia mają zaznaczone godziny
            if not Dostepnosc.objects.filter(nauczyciel=n, data_od__lte=dzien, data_do__gte=dzien).exists():
                continue
        korepetytorzy.append({'nazwa': f'{n.imie} {n.nazwisko}', 'id': n.id})
    return JsonResponse({'korepetytorzy': korepetytorzy})
@login_required
def uczniowie(request):
    """Zakładka uczniowie: lista, formularz dodawania i dostępność nauczycieli.
    Zwykły pracownik widzi tylko uczniów, których sam umówił.
    Konto nauczyciela nie ma tu wstępu (widzi tylko swój kalendarz)."""
    if (
        getattr(request.user, 'nauczyciel_id', None)
        and str(request.user.nauczyciel_id) != request.GET.get('nauczyciel', '')
    ):
        return JsonResponse({'blad': 'Brak uprawnień.'}, status=403)
        raise PermissionDenied
    dost = _kontekst_dostepnosci(request)
    if czy_szef(request.user) or czy_koordynator(request.user):
        uczniowie_lista = list(Uczen.objects.all())
    else:
        uczniowie_lista = list(Uczen.objects.filter(umowil=request.user))
    wybrany_przedmiot = request.POST.get('przedmiot') if request.method == 'POST' else None
    wybrany_poziom = request.POST.get('poziom') if request.method == 'POST' else None

    if request.method == 'POST' and 'wybierz_przedmiot' in request.POST:
        form = FormularzUcznia(
            initial={'przedmiot': wybrany_przedmiot, 'poziom': wybrany_poziom},
            przedmiot=wybrany_przedmiot,
            poziom=wybrany_poziom,
        )
        return render(request, 'konta/uczniowie.html', {
            'form': form,
            'uczniowie': uczniowie_lista,
            'dost': dost,
        })

    form = FormularzUcznia(request.POST or None, przedmiot=wybrany_przedmiot, poziom=wybrany_poziom)

    if request.method == 'POST':
        # Krok 5 na POST: tylko korepetytorzy faktycznie wolni o wybranej dacie+godzinie
        # (wysłana wartość zostaje na liście, żeby walidacja mogła pokazać dokładny komunikat)
        try:
            dzien_post = date.fromisoformat(request.POST.get('data_pierwszej_lekcji', ''))
            godz_post = datetime.strptime(request.POST.get('godzina_pierwszej_lekcji', ''), '%H:%M').time()
            start_post = datetime.combine(dzien_post, godz_post)
        except (ValueError, TypeError):
            start_post = None
        if start_post:
            wybrany = request.POST.get('korepetytor', '')
            wolni = []
            for wartosc, _etykieta in list(form.fields['korepetytor'].choices):
                if not wartosc:
                    continue
                nn = nauczyciel_z_korepetytora(wartosc)
                if nn and nauczyciel_wolny_o(nn, start_post.date(), start_post):
                    wolni.append((wartosc, wartosc))
                elif wartosc == wybrany:
                    wolni.append((wartosc, wartosc))
            if wolni:
                form.fields['korepetytor'].choices = [('', '— wybierz korepetytora —')] + wolni

    if request.method == 'POST' and form.is_valid():
        uczen = form.save(commit=False)
        uczen.pierwsza_lekcja = form.cleaned_data['pierwsza_lekcja']
        # nauczyciel musi być wolny: zakres + brak kolizji z lekcjami i przerwami
        nauczyciel = nauczyciel_z_korepetytora(uczen.korepetytor)
        start_dt = timezone.localtime(uczen.pierwsza_lekcja).replace(tzinfo=None)
        if nauczyciel and not nauczyciel_wolny_o(nauczyciel, start_dt.date(), start_dt):
            # dokładna przyczyna: poza zakresem / kolizja / przerwa dnia
            koniec_lekcji = start_dt + timedelta(minutes=GODZINA_LEKCJI)
            w_zakresie = any(
                s <= start_dt and koniec_lekcji <= e
                for s, e in _zakresy_dnia(nauczyciel, start_dt.date())
            )
            if not w_zakresie:
                powod = ('ta godzina nie mieści się w jego zaznaczonych godzinach tego dnia '
                         '— otwórz jego „Mój tydzień” i sprawdź, czy dzień jest dobry')
            elif kolizja_lekcji_nauczyciela(nauczyciel, start_dt.date(), start_dt):
                powod = 'koliduje z jego inną lekcją (albo przerwą po niej)'
            else:
                powod = 'inny powód'
            messages.error(
                request,
                f'{uczen.korepetytor} nie może przyjąć lekcji {start_dt:%d.%m.%Y} o {start_dt:%H:%M} '
                f'— {powod}. Wybierz godzinę z listy dostępnych (Krok 4) i korepetytora z listy (Krok 5).',
            )
            return render(request, 'konta/uczniowie.html', {
                'form': form,
                'uczniowie': uczniowie_lista,
                'dost': dost,
            })
        uczen.kto_umowil = request.user.get_full_name() or request.user.username
        uczen.umowil = request.user
        # link Meet nauczyciela zapisujemy przy uczniu (jeśli uczen nie ma własnego)
        if not uczen.link_meet and nauczyciel and nauczyciel.link_meet:
            uczen.link_meet = nauczyciel.link_meet
        uczen.save()
        # 1. lekcja dostaje rekord i blokade u korepetytora — inaczej kolejny uczeń
        # moglby zostac umowiony na ta sama godzine (karta ucznia sama w sobie nie blokuje terminu)
        Lekcja.objects.get_or_create(uczen=uczen, data=uczen.pierwsza_lekcja)
        if nauczyciel:
            start_czas = timezone.localtime(uczen.pierwsza_lekcja).time()
            Dostepnosc.objects.get_or_create(
                nauczyciel=nauczyciel,
                data_od=start_dt.date(),
                data_do=start_dt.date(),
                godzina_od=start_czas,
                godzina_do=(datetime.combine(date(2000, 1, 1), start_czas) + timedelta(minutes=GODZINA_LEKCJI)).time(),
            )
        messages.success(request, 'Uczeń został dodany do systemie.')
        wyslij_powiadomienie_o_uczniu(request, uczen)
        wyslij_powiadomienie_nauczycielowi(request, uczen)
        wyslij_discord_pierwsza_lekcja(request, uczen)
        wyslij_potwierdzenie_rodzicowi(request, uczen)
        messages.info(
            request,
            'Do rodzica wysłano potwierdzenie zapisu: termin 1. lekcji'
            + (' i link Meet.' if uczen.link_meet else ' (link Meet poleci osobno, gdy korepetytor go zapisze).'),
        )
        return redirect('uczniowie')

    return render(request, 'konta/uczniowie.html', {
        'form': form,
        'uczniowie': uczniowie_lista,
        'dost': dost,
    })

@login_required
def edytuj_ucznia(request, uczen_id):
    """Edycja danych ucznia: admin może edytować każdego, pracownik — tylko swoich.
    Do tego zarządzanie dodatkowymi przedmiotami ucznia (wielu korepetytorów)."""
    uczen = get_object_or_404(Uczen, pk=uczen_id)
    if not czy_szef(request.user) and not czy_koordynator(request.user) and uczen.umowil_id != request.user.id:
        raise PermissionDenied

    # --- dodatkowe przedmioty ucznia: dodawanie ---
    if request.method == 'POST' and 'dodaj_przedmiot' in request.POST:
        form_przedmiotu = FormularzPrzedmiotuUcznia(request.POST, prefix='przedmiotdod')
        form_przedmiotu.ustaw_korepetytorow(
            request.POST.get('przedmiotdod-przedmiot'),
            request.POST.get('przedmiotdod-poziom'),
            request.POST.get('przedmiotdod-korepetytor'),
        )
        if form_przedmiotu.is_valid():
            przedmiot = form_przedmiotu.cleaned_data['przedmiot']
            poziom = form_przedmiotu.cleaned_data['poziom']
            korepetytor = form_przedmiotu.cleaned_data['korepetytor']
            # nie pozwalamy na duplikat: ten sam przedmiot+poziom już istnieje (główny lub dodatkowy)
            if (uczen.przedmiot == przedmiot and uczen.poziom == poziom) or \
               uczen.przedmioty_ucznia.filter(przedmiot=przedmiot, poziom=poziom).exists():
                messages.error(
                    request,
                    f'{uczen.imie_ucznia} uczy się już {przedmiot} ({poziom}) — nie można dodać drugi raz.',
                )
            else:
                nauczyciel = nauczyciel_z_korepetytora(korepetytor)
                link = ''
                if nauczyciel and nauczyciel.link_meet:
                    link = nauczyciel.link_meet
                # data + godzina 1. lekcji dla tego przedmiotu — od razu zajmuje grafik korepetytora
                try:
                    data_lekcji = form_przedmiotu.cleaned_data['data_pierwszej_lekcji']
                    godzina_txt = form_przedmiotu.cleaned_data['godzina_pierwszej_lekcji']
                    hh, mm = godzina_txt.split(':')
                    czas = time(int(hh), int(mm))
                    start_lekcji = datetime.combine(data_lekcji, czas)
                except (ValueError, TypeError, AttributeError, KeyError):
                    messages.error(request, 'Podaj datę i godzinę 1. lekcji dla tego przedmiotu.')
                    return redirect('edytuj_ucznia', uczen_id=uczen.id)
                if nauczyciel and not nauczyciel_wolny_o(nauczyciel, data_lekcji, start_lekcji):
                    messages.error(
                        request,
                        f'{korepetytor} nie może przyjąć lekcji {data_lekcji:%d.%m.%Y} o {start_lekcji:%H:%M} '
                        '— godzina nie mieści się w jego dostępności albo koliduje z inną lekcją/przerwą. '
                        'Wybierz wolną godzinę z listy.',
                    )
                    return redirect('edytuj_ucznia', uczen_id=uczen.id)
                karta = PrzedmiotUcznia.objects.create(
                    uczen=uczen,
                    przedmiot=przedmiot,
                    poziom=poziom,
                    korepetytor=korepetytor,
                    link_meet=link,
                )
                lekcja = None
                if nauczyciel:
                    data_aware = timezone.make_aware(start_lekcji)
                    lekcja, _ = Lekcja.objects.get_or_create(uczen=uczen, data=data_aware, przedmiot_karta=karta)
                    Dostepnosc.objects.get_or_create(
                        nauczyciel=nauczyciel,
                        data_od=data_lekcji,
                        data_do=data_lekcji,
                        godzina_od=czas,
                        godzina_do=(datetime.combine(date(2000, 1, 1), czas) + timedelta(minutes=GODZINA_LEKCJI)).time(),
                    )
                # potwierdzenie do rodzica z DANYMI Z KARTY (przedmiot dodatkowy, nie główny)
                wyslij_potwierdzenie_rodzicowi(request, uczen, karta=karta, lekcja=lekcja)
                messages.success(
                    request,
                    f'Dodano przedmiot {przedmiot} ({poziom}) — korepetytor: {korepetytor}, '
                    f'1. lekcja {data_lekcji:%d.%m.%Y} o {start_lekcji:%H:%M} (grafik korepetytora zablokowany).',
                )
                return redirect('edytuj_ucznia', uczen_id=uczen.id)
        # formularz z błędami — pokaż z powrotem
        messages.error(request, 'Popraw dane w formularzu dodawania przedmiotu.')
        return redirect('edytuj_ucznia', uczen_id=uczen.id)

    # --- dodatkowe przedmioty ucznia: usuwanie ---
    if request.method == 'POST' and 'usun_przedmiot' in request.POST:
        # usuwanie przedmiotu = usuwanie zajęć ucznia u korepetytora — TYLKO szef
        if not czy_szef(request.user):
            raise PermissionDenied  
        karta_id = request.POST.get('przedmiot_ucznia_id')
        karta = PrzedmiotUcznia.objects.filter(pk=karta_id, uczen=uczen).first()
        if karta:
            # zwalniamy zablokowane godziny tego przedmiotu u jego korepetytora
            nauczyciel = nauczyciel_z_korepetytora(karta.korepetytor)
            if nauczyciel:
                for lekcja in Lekcja.objects.filter(uczen=uczen, przedmiot_karta=karta):
                    start = timezone.localtime(lekcja.data)
                    Dostepnosc.objects.filter(
                        nauczyciel=nauczyciel,
                        data_od=start.date(),
                        godzina_od=start.time(),
                    ).delete()
            # lekcje tego przedmiotu usuwamy razem z kartą (FK jest SET_NULL)
            Lekcja.objects.filter(uczen=uczen, przedmiot_karta=karta).delete()
            karta.delete()
            messages.success(request, f'Usunięto przedmiot {karta} wraz z jego lekcjami.')
        return redirect('edytuj_ucznia', uczen_id=uczen.id)

    if request.method == 'POST':
        form = FormularzUcznia(
            request.POST,
            instance=uczen,
            przedmiot=request.POST.get('przedmiot'),
            poziom=request.POST.get('poziom'),
        )
        # obecny korepetytor musi zostać na liście, nawet jeśli nauczyciel jest już nieaktywny
        if uczen.korepetytor and uczen.korepetytor not in [w for w, _ in form.fields['korepetytor'].choices]:
            form.fields['korepetytor'].choices += [(uczen.korepetytor, uczen.korepetytor)]
        if form.is_valid():
            stary_korepetytor = uczen.korepetytor
            zapisany = form.save(commit=False)
            zapisany.pierwsza_lekcja = form.cleaned_data.get('pierwsza_lekcja') or uczen.pierwsza_lekcja
            zapisany.save()
            # zmiana korepetytora: zwalniamy blokady godzin u poprzedniego
            if stary_korepetytor and stary_korepetytor.strip().lower() != (zapisany.korepetytor or '').strip().lower():
                stary = nauczyciel_z_korepetytora(stary_korepetytor)
                if stary:
                    for lekcja in Lekcja.objects.filter(uczen=uczen):
                        start = timezone.localtime(lekcja.data)
                        Dostepnosc.objects.filter(
                            nauczyciel=stary,
                            data_od=start.date(),
                            godzina_od=start.time(),
                        ).delete()
                    messages.info(
                        request,
                        f'Zmieniono korepetytora — zwolniono zablokowane godziny u {stary_korepetytor}. '
                        'Umów lekcje ponownie w kalendarzu ucznia, aby zablokować godziny u nowego korepetytora.',
                    )
            messages.success(request, f'Dane ucznia {uczen.imie_ucznia} zostały zaktualizowane.')
            return redirect('lekcje')
    else:
        godzina = uczen.pierwsza_lekcja.strftime('%H:%M') if uczen.pierwsza_lekcja else ''
        form = FormularzUcznia(
            instance=uczen,
            przedmiot=uczen.przedmiot,
            poziom=uczen.poziom,
            initial={
                'data_pierwszej_lekcji': uczen.pierwsza_lekcja.date() if uczen.pierwsza_lekcja else None,
                'godzina_pierwszej_lekcji': godzina,
            },
        )
        if uczen.korepetytor and uczen.korepetytor not in [w for w, _ in form.fields['korepetytor'].choices]:
            form.fields['korepetytor'].choices += [(uczen.korepetytor, uczen.korepetytor)]

    return render(request, 'konta/edytuj_ucznia.html', {
        'form': form,
        'form_przedmiotu': FormularzPrzedmiotuUcznia(prefix='przedmiotdod'),
        'uczen': uczen,
        'przedmioty_ucznia': list(uczen.przedmioty_ucznia.all()),
        'moze_usuwac': czy_szef(request.user),
    })


@login_required
def usun_ucznia(request, uczen_id):
    """Usuwa ucznia wraz z jego lekcjami i zwalnia zablokowane godziny u korepetytora.
    TYLKO szef — koordynatorzy i osoby umawiające mogą edytować, ale nie usuwać."""
    uczen = get_object_or_404(Uczen, pk=uczen_id)
    if not czy_szef(request.user):
        raise PermissionDenied
    if request.method == 'POST':
        zwolnij_blokady_ucznia(uczen)
        imie = f'{uczen.imie_ucznia} {uczen.nazwisko_rodzica}'
        uczen.delete()
        messages.success(request, f'Uczeń {imie} został usunięty wraz z lekcjami.')
        return redirect('lekcje')
    return render(request, 'konta/usun_ucznia.html', {'uczen': uczen})


@login_required
def archiwum_uczniow(request):
    """Zakładka archiwum: uczniowie ze zakończoną współpracą, z możliwością przywrócenia."""
    if getattr(request.user, 'nauczyciel_id', None):
        raise PermissionDenied
    if czy_szef(request.user) or czy_koordynator(request.user):
        uczniowie_lista = list(Uczen.objects.filter(zakonczona=True))
    else:
        uczniowie_lista = list(Uczen.objects.filter(umowil=request.user, zakonczona=True))
    fraza = (request.GET.get('szukaj') or '').strip().lower()
    if fraza:
        uczniowie_lista = [
            u for u in uczniowie_lista
            if fraza in u.imie_ucznia.lower()
            or fraza in u.nazwisko_rodzica.lower()
            or fraza in (u.korepetytor or '').lower()
            or fraza in u.przedmiot.lower()
        ]
    liczba_lekcji = {}
    for lekcja in Lekcja.objects.filter(uczen__in=uczniowie_lista):
        liczba_lekcji[lekcja.uczen_id] = liczba_lekcji.get(lekcja.uczen_id, 0) + 1
    for u in uczniowie_lista:
        u.liczba_lekcji = liczba_lekcji.get(u.id, 0)

    """Zakładka archiwum: uczniowie ze zakończoną współpracą, zakończeni korepetytorzy
    i wyłączone konta pracowników — wszystko z możliwością przywrócenia. Aktywni się nie pokazują."""
    korepetytorzy_lista = list(Nauczyciel.objects.filter(aktywny=False).order_by('nazwisko', 'imie'))
    for n in korepetytorzy_lista:
        n.konto_uzytkownika = Pracownik.objects.filter(nauczyciel=n).first()  # powiązane konto (jeśli jest)
    pracownicy_lista = [
        u for u in Pracownik.objects.filter(is_active=False).order_by('username')
        if not u.nauczyciel
        and (u.stanowisko or '').strip().lower() != 'koordynator'
    ]
    koordynatorzy_lista = [
        u for u in Pracownik.objects.filter(is_active=False).order_by('username')
        if not u.nauczyciel
        and (u.stanowisko or '').strip().lower() == 'koordynator'
    ]
    return render(request, 'konta/archiwum.html', {
        'uczniowie': uczniowie_lista,
        'korepetytorzy': korepetytorzy_lista,
        'pracownicy': pracownicy_lista,
        'koordynatorzy': koordynatorzy_lista,
        'fraza': (request.GET.get('szukaj') or '').strip(),
        'czy_szef': czy_szef(request.user),
        'czy_koordynator': czy_koordynator(request.user),
    })


@login_required
def zakoncz_ucznia(request, uczen_id):
    """Archiwizuje ucznia: znika z list bieżących, ale zostaje w archiwum (dane i lekcje nietknięte).
    TYLKO szef — koordynator i osoba umawiająca mogą tylko edytować, nie kończą współpracy."""
    uczen = get_object_or_404(Uczen, pk=uczen_id)
    if not czy_szef(request.user):
        raise PermissionDenied
    if request.method == 'POST':
        zwolnij_blokady_ucznia(uczen)
        uczen.zakonczona = True
        uczen.save(update_fields=['zakonczona'])
        imie = f'{uczen.imie_ucznia} {uczen.nazwisko_rodzica}'
        messages.success(request, f'Współpraca z uczniem {imie} zakończona — trafił do archiwum. Godziny u korepetytora zwolnione.')
        return redirect('lekcje')
    return render(request, 'konta/zakoncz_ucznia.html', {'uczen': uczen})


@login_required
def przywroc_ucznia(request, uczen_id):
    """Przywraca ucznia z archiwum na listę aktywnych."""
    uczen = get_object_or_404(Uczen, pk=uczen_id)
    if not czy_szef(request.user) and uczen.umowil_id != request.user.id:
        raise PermissionDenied
    uczen.zakonczona = False
    uczen.save(update_fields=['zakonczona'])
    imie = f'{uczen.imie_ucznia} {uczen.nazwisko_rodzica}'
    # czyscimy znaczniki "przypomnienie wyslane" z czasow archiwum — inaczej
    # przypomnienia dla przyszlych lekcji nigdy by nie poszly
    PrzypomnienieLekcji.objects.filter(
        lekcja__uczen=uczen,
        lekcja__data__gte=timezone.now(),
    ).delete()
    odtworzone, pominięte_kolizje = odtworz_blokady_ucznia(uczen)
    komunikat = f'Uczeń {imie} przywrócony z archiwum.'
    if odtworzone:
        komunikat += f' Odtworzono {odtworzone} blokad godzin u korepetytora.'
    if pominięte_kolizje:
        komunikat += (
            f' UWAGA: {pominięte_kolizje} terminów pominięto — korepetytor ma już wtedy '
            'inną lekcję (sprawdź kalendarz ucznia i umów nowe godziny).'
        )
    messages.success(request, komunikat)
    return redirect('archiwum')


@login_required
@tylko_szef
def zakoncz_wspolprace(request, nauczyciel_id):
    """Kończy współpracę z korepetytorem: staje się nieaktywny, znika z list bieżących (dane zostają)."""
    nauczyciel = get_object_or_404(Nauczyciel, pk=nauczyciel_id)
    if request.method == 'POST':
        nauczyciel.aktywny = False
        nauczyciel.save(update_fields=['aktywny'])
        imie = f'{nauczyciel.imie} {nauczyciel.nazwisko}'
        messages.success(request, f'Zakończono współpracę z {imie} — korepetytor trafił do archiwum. Dane i godziny pozostają w bazie.')
        return redirect('archiwum')
    return render(request, 'konta/zakoncz_wspolprace.html', {'nauczyciel': nauczyciel})


@login_required
@tylko_szef
def przywroc_wspolprace(request, nauczyciel_id):
    """Przywraca korepetytora do współpracy."""
    nauczyciel = get_object_or_404(Nauczyciel, pk=nauczyciel_id)
    nauczyciel.aktywny = True
    nauczyciel.save(update_fields=['aktywny'])
    imie = f'{nauczyciel.imie} {nauczyciel.nazwisko}'
    messages.success(request, f'Wznowiono współpracę z {imie}.')
    return redirect('archiwum')


@login_required
def edytuj_uzytkownika(request, user_id):
    """Edycja konta: imię, nazwisko, email, telefon (bez hasła i roli).
    Dla korepetytora dodatkowo zarządzanie przedmiotami (dodaj / usuń / zmień stawkę).
    Szef i koordynator (edycja danych), ale koordynator nie edytuje kont adminów."""
    if not (czy_szef(request.user) or czy_koordynator(request.user)):
        raise PermissionDenied
    # koordynator nie edytuje kont adminów
    uzytkownik = get_object_or_404(Pracownik, pk=user_id)
    if not czy_szef(request.user) and uzytkownik.is_superuser:
        raise PermissionDenied
    nauczyciel = uzytkownik.nauczyciel

    # --- przedmioty korepetytora: dodawanie / aktualizacja stawki ---
    if request.method == 'POST' and nauczyciel and 'dodaj_przedmiot' in request.POST:
        przedmiot = request.POST.get('przedmiot', '')
        poziom = request.POST.get('poziom', '')
        stawka_txt = (request.POST.get('stawka', '') or '0').replace(',', '.')
        if przedmiot not in dict(PRZEDMIOTY) or poziom not in dict(POZIOMY_NAUCZYCIELA):
            messages.error(request, 'Wybierz przedmiot i poziom z listy.')
            return redirect('edytuj_uzytkownika', user_id=uzytkownik.id)
        try:
            stawka = Decimal(stawka_txt)
            if stawka < 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            messages.error(request, 'Stawka musi być liczbą (np. 100 lub 99,50).')
            return redirect('edytuj_uzytkownika', user_id=uzytkownik.id)
        _, stworzony = PrzedmiotNauczyciela.objects.update_or_create(
            nauczyciel=nauczyciel,
            przedmiot=przedmiot,
            poziom=poziom,
            defaults={'stawka': stawka},
        )
        if stworzony:
            messages.success(request, f'Dodano przedmiot {przedmiot} ({poziom}) — stawka {stawka} zł.')
        else:
            messages.success(request, f'Zaktualizowano stawkę: {przedmiot} ({poziom}) — {stawka} zł.')
        return redirect('edytuj_uzytkownika', user_id=uzytkownik.id)

    # --- przedmioty korepetytora: usuwanie ---
    if request.method == 'POST' and nauczyciel and 'usun_przedmiot' in request.POST:
        wiersz = PrzedmiotNauczyciela.objects.filter(
            pk=request.POST.get('przedmiot_id'), nauczyciel=nauczyciel,
        ).first()
        if wiersz:
            opis = f'{wiersz.przedmiot} ({wiersz.poziom})'
            wiersz.delete()
            messages.success(request, f'Usunięto przedmiot {opis}.')
        return redirect('edytuj_uzytkownika', user_id=uzytkownik.id)

    if request.method == 'POST':
        stara_nazwa = ''
        if nauczyciel:
            stara_nazwa = f'{nauczyciel.imie} {nauczyciel.nazwisko}'.strip()
        # login (nazwa użytkownika) — można zmienić, ale musi być wolny; puste pole = bez zmiany
        nowy_login = request.POST.get('username', '').strip() or uzytkownik.username
        if not nowy_login:
            messages.error(request, 'Login (nazwa użytkownika) nie może być pusty.')
            return redirect('edytuj_uzytkownika', user_id=uzytkownik.id)
        if Pracownik.objects.filter(username__iexact=nowy_login).exclude(pk=uzytkownik.pk).exists():
            messages.error(request, f'Login „{nowy_login}” jest już zajęty — wybierz inny.')
            return redirect('edytuj_uzytkownika', user_id=uzytkownik.id)
        uzytkownik.username = nowy_login
        uzytkownik.first_name = request.POST.get('first_name', '').strip()
        uzytkownik.last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()
        # e-mail opcjonalny: puste pole = zostaje obecny adres
        if email:
            uzytkownik.email = email
        uzytkownik.telefon = request.POST.get('telefon', '').strip()
        # hasło opcjonalne: puste pola = hasło zostaje bez zmian (przydatne gdy ktoś zapomniał)
        haslo1 = request.POST.get('haslo1', '')
        haslo2 = request.POST.get('haslo2', '')
        if haslo1 or haslo2:
            if haslo1 != haslo2:
                messages.error(request, 'Hasła nie są takie same — wpisz dwa razy identyczne.')
                return redirect('edytuj_uzytkownika', user_id=uzytkownik.id)
            try:
                validate_password(haslo1, uzytkownik)
            except ValidationError as e:
                messages.error(request, ' '.join(e.messages))
                return redirect('edytuj_uzytkownika', user_id=uzytkownik.id)
            uzytkownik.set_password(haslo1)
        uzytkownik.save()
        # korepetytor: synchronizujemy imię, nazwisko i e-mail na karcie nauczyciela;
        # przy zmianie imienia/nazwiska przepisujemy też nazwisko na kartach uczniów
        if nauczyciel:
            nauczyciel.imie = uzytkownik.first_name or uzytkownik.username
            nauczyciel.nazwisko = uzytkownik.last_name or uzytkownik.username
            nauczyciel.email = uzytkownik.email
            nauczyciel.save()
            nowa_nazwa = f'{nauczyciel.imie} {nauczyciel.nazwisko}'.strip()
            if stara_nazwa and stara_nazwa.lower() != nowa_nazwa.lower():
                Uczen.objects.filter(korepetytor__iexact=stara_nazwa).update(korepetytor=nowa_nazwa)
                PrzedmiotUcznia.objects.filter(korepetytor__iexact=stara_nazwa).update(korepetytor=nowa_nazwa)
        messages.success(request, f'Dane konta {uzytkownik.username} zostały zaktualizowane.')
        return redirect('uzytkownicy')
    return render(request, 'konta/edytuj_uzytkownika.html', {
        'uzytkownik': uzytkownik,
        'przedmioty': PRZEDMIOTY,
        'poziomy_nauczyciela': POZIOMY_NAUCZYCIELA,
    })


@login_required
@tylko_szef
def przelacz_konto(request, user_id):
    """Dezaktywacja konta z potwierdzeniem: GET pokazuje „czy na pewno?”,
    POST wyłącza konto (is_active=False) — trafia do Archiwum, nie można się logować.
    Włączenie z powrotem tylko z Archiwum (przywroc_konto)."""
    uzytkownik = get_object_or_404(Pracownik, pk=user_id)
    if uzytkownik.id == request.user.id:
        messages.error(request, 'Nie możesz wyłączyć konta samemu sobie.')
        return redirect('uzytkownicy')
    if request.method == 'POST':
        uzytkownik.is_active = False
        uzytkownik.save(update_fields=['is_active'])
        # konto korepetytora wyłączone -> jego karta też nieaktywna (inaczej "znika" bez śladu)
        if uzytkownik.nauczyciel_id:
            uzytkownik.nauczyciel.aktywny = False
            uzytkownik.nauczyciel.save(update_fields=['aktywny'])
        messages.info(
            request,
            f'Konto {uzytkownik.username} zostało dezaktywowane — trafiło do Archiwum '
            'i nie można się na nie zalogować. Przywrócisz je z poziomu Archiwum.',
        )
        return redirect('uzytkownicy')
    return render(request, 'konta/dezaktywuj_konto.html', {'uzytkownik': uzytkownik})


@login_required
@tylko_szef
def przywroc_konto(request, user_id):
    """Przywraca konto z Archiwum: is_active=True — konto wraca do zakładki
    Użytkownicy z wszystkimi wcześniejszymi funkcjami i może się znów logować."""
    uzytkownik = get_object_or_404(Pracownik, pk=user_id)
    uzytkownik.is_active = True
    uzytkownik.save(update_fields=['is_active'])
    # konto korepetytora przywrócone -> karta znowu aktywna
    if uzytkownik.nauczyciel_id:
        uzytkownik.nauczyciel.aktywny = True
        uzytkownik.nauczyciel.save(update_fields=['aktywny'])
    messages.success(
        request,
        f'Konto {uzytkownik.username} zostało przywrócone — wróciło do zakładki Użytkownicy '
        'i można się na nie ponownie zalogować.',
    )
    return redirect('archiwum')

@login_required
@tylko_szef
def usun_konto(request, user_id):
    """Usuwa konto NA ZAWSZE — tylko szef/admin (koordynator nic nie usuwa).
    Nie można usunąć własnego konta."""
    uzytkownik = get_object_or_404(Pracownik, pk=user_id)
    if uzytkownik.id == request.user.id:
        messages.error(request, 'Nie możesz usunąć własnego konta.')
        return redirect('archiwum')
    if not czy_szef(request.user):
        raise PermissionDenied
    if request.method == 'POST':
        login = uzytkownik.username
        nauczyciel = uzytkownik.nauczyciel
        uzytkownik.delete()
        # karta nauczyciela: usuwamy razem z kontem (przedmioty, stawki,
        # zaznaczone godziny znikną w kaskadzie) — żeby nie zostały "duchy"
        if nauczyciel:
            nauczyciel.delete()
        messages.success(request, f'Konto {login} zostało usunięte na stałe.')
        return redirect('archiwum')
    return render(request, 'konta/usun_konto.html', {'uzytkownik': uzytkownik})

@login_required
def pobierz_raport(request):
    """Raport zapłaconych płatności jako plik HTML do pobrania — tabela z kolumnami
    i siatką, jak na stronie Płatności. Szef i koordynator. Pobranie raportu też
    archiwizuje pozycje do historii (nie wracają do nieopłaconych)."""
    if not (czy_szef(request.user) or czy_koordynator(request.user)):
        raise PermissionDenied
    zaplaconi = list(Uczen.objects.filter(zaplacone=True, zakonczona=False))
    teraz = timezone.now()
    tresc = render_to_string('konta/raport_plik.html', {
        'pozycje': zaplaconi,
        'data_raportu': teraz.strftime('%d.%m.%Y %H:%M'),
        'odbiorca': '(pobranie pliku)',
    })
    # archiwizacja jak przy wysyłce mailem: pozycje do historii, nie do nieopłaconych
    for u in zaplaconi:
        u.zaplacone = False
        u.rozliczono_kiedy = teraz
        u.save(update_fields=['zaplacone', 'rozliczono_kiedy'])
    RaportPlatnosci.objects.create(
        zawartosc=tresc,
        email_odbiorcy='(pobranie pliku)',
        wyslal=request.user.get_full_name() or request.user.username,
    )
    odpowiedz = HttpResponse(tresc, content_type='text/html; charset=utf-8')
    odpowiedz['Content-Disposition'] = f'attachment; filename="raport_platnosci_{teraz:%Y-%m-%d_%H-%M}.html"'
    return odpowiedz


@login_required
def pobierz_raport_id(request, raport_id):
    """Pobiera zapisaną treść raportu płatności z historii. Nowe raporty to pliki HTML
    (tabela z siatką), stare (sprzed zmiany formatu) zostają jako zwykły .txt.
    Szef i koordynator."""
    if not (czy_szef(request.user) or czy_koordynator(request.user)):
        raise PermissionDenied
    raport = get_object_or_404(RaportPlatnosci, pk=raport_id)
    if raport.zawartosc.lstrip().startswith('<'):
        odpowiedz = HttpResponse(raport.zawartosc, content_type='text/html; charset=utf-8')
        odpowiedz['Content-Disposition'] = f'attachment; filename="raport_platnosci_{raport.id}.html"'
    else:
        odpowiedz = HttpResponse(raport.zawartosc, content_type='text/plain; charset=utf-8')
        odpowiedz['Content-Disposition'] = f'attachment; filename="raport_platnosci_{raport.id}.txt"'
    return odpowiedz


def reset_hasla(request):
    """Reset hasła z limitem prób: max 5 żądań w oknie 10 minut (ochrona przed
    spamowaniem maili) — potem blokada tylko do końca okna. Link w mailu jest
    robiony naszym generatorem (działa 3 dni nawet po zalogowaniu).
    Celowo bez @login_required — z tego widoku korzystają osoby niezalogowane."""
    from django.contrib.auth.views import PasswordResetView
    if request.method == 'POST':
        teraz = timezone.now().timestamp()
        okno = request.session.get('okno_resetu', 0)
        if not okno or teraz - okno >= OKNO_PROB_SEK:
            okno = teraz
            request.session['okno_resetu'] = okno
            request.session['proby_resetu'] = 0
        if request.session.get('proby_resetu', 0) >= 5:
            pozostalo = int((OKNO_PROB_SEK - (teraz - okno)) // 60) + 1
            messages.error(
                request,
                f'Zbyt wiele prób resetu hasła — spróbuj ponownie za ok. {pozostalo} min.',
            )
            return redirect('logowanie')
        request.session['proby_resetu'] = request.session.get('proby_resetu', 0) + 1
    return PasswordResetView.as_view(
        template_name='konta/reset_hasla.html',
        subject_template_name='konta/mail_reset_temat.txt',
        email_template_name='konta/mail_reset_tresc.txt',
        success_url='/reset-hasla/wyslane/',
        token_generator=token_generator_resetu,
    )(request)

def _link_przeterminowany(uidb64, token):
    """True, gdy link był prawidłowy, ale minęły 3 dni (PASSWORD_RESET_TIMEOUT).
    Fałszywy link zwraca False — wtedy pokazuje się zwykła strona błędu."""
    from django.conf import settings as ustawienia
    from django.utils.crypto import constant_time_compare
    from django.utils.http import base36_to_int, urlsafe_base64_decode
    try:
        uid = urlsafe_base64_decode(uidb64).decode()
        uzytkownik = Pracownik.objects.get(pk=uid)
        gen = token_generator_resetu
        if gen.check_token(uzytkownik, token):
            return False  # link wciąż ważny
        czesci = token.split('-')
        if len(czesci) != 2:
            return False
        try:
            ts = base36_to_int(czesci[0])
        except ValueError:
            return False
        # skrót musi się zgadzać — inaczej to fałszywy link, a nie przeterminowany
        for secret in [gen.secret, *gen.secret_fallbacks]:
            if constant_time_compare(
                gen._make_token_with_timestamp(uzytkownik, ts, secret), token
            ):
                break
        else:
            return False
        # dokładnie ten sam warunek czasowy co w check_token, tylko odwrócony
        return (gen._num_seconds(gen._now()) - ts) > ustawienia.PASSWORD_RESET_TIMEOUT
    except Exception:
        return False


class ResetHaslaPotwierdzWidok(PasswordResetConfirmView):
    """Jak wbudowany widok Django, ale gdy link minął (3 dni) —
    przekierowuje na osobną stronę „czas minął” zamiast pustego formularza.
    Nasz generator tokenów (bez last_login), więc link działa 3 dni mimo logowania."""

    token_generator = token_generator_resetu

    def dispatch(self, request, *args, **kwargs):
        uidb64 = kwargs.get('uidb64')
        token = kwargs.get('token')
        if uidb64 and token and _link_przeterminowany(uidb64, token):
            return redirect('reset_hasla_wygasl')
        return super().dispatch(request, *args, **kwargs)


def reset_hasla_wygasl(request):
    """Strona „czas minął” — link do ustawienia hasła był ważny 3 dni i wygasł."""
    return render(request, 'konta/reset_wygasl.html')

def _koniec_wakacji(dzien):
    """Koniec wakacji 31.08 — bieżący rok, a jeśli już minął, następny."""
    rok = dzien.year if dzien <= date(dzien.year, 8, 31) else dzien.year + 1
    return date(rok, 8, 31)


def _rozszerz_cyklicznie(nauczyciel, dzien_data, start, koniec, okres=1):
    """Kopiuje cykliczne sloty zakresu [start, koniec) z dnia dzien_data co okres tygodni
    aż do końca wakacji (31.08). Zwraca liczbę nowo utworzonych terminów."""
    ostatni = _koniec_wakacji(dzien_data)
    krok = timedelta(days=7 * okres)
    zrodlo = list(Dostepnosc.objects.filter(
        nauczyciel=nauczyciel, data_od=dzien_data, cykliczny=True,
        godzina_od__gte=start, godzina_do__lte=koniec,
    ))
    if not zrodlo:
        return 0
    docelowe = []
    d = dzien_data + krok
    while d <= ostatni:
        docelowe.append(d)
        d += krok
    if not docelowe:
        return 0
    istniejace = set(Dostepnosc.objects.filter(
        nauczyciel=nauczyciel, data_od__in=docelowe,
    ).values_list('data_od', 'godzina_od'))
    nowe = [
        Dostepnosc(
            nauczyciel=nauczyciel, data_od=d, data_do=d,
            godzina_od=s.godzina_od, godzina_do=s.godzina_do,
            cykliczny=True, cykliczny_okres=okres,
        )
        for d in docelowe for s in zrodlo
        if (d, s.godzina_od) not in istniejace
    ]
    if nowe:
        Dostepnosc.objects.bulk_create(nowe)
    return len(nowe)

def _prosi_o_koordynatora(request, nauczyciel, dzien_data, akcja_txt, lekcje_txt='', dane=None):
    """Korepetytor (nie-szef) próbował ruszyć zajęcia — blokuje akcję (403)
    i zapisuje zgłoszenie dla koordynatora/szefa w zakładce Zgłoszenia.
    dane: opcjonalny słownik strukturalny akcji (podaje go kalendarz_dane),
    żeby koordynator mógł później ZATWIERDZIĆ zgłoszenie automatycznie."""
    kto = (request.user.get_full_name() or request.user.username).strip() or request.user.username
    tresc = (f'{kto} chce: {akcja_txt} — korepetytor {nauczyciel.imie} {nauczyciel.nazwisko}, '
             f'dzień {dzien_data:%d.%m.%Y}.')
    if lekcje_txt:
        tresc += f' Zajęcia w zakresie: {lekcje_txt}.'
    Zgloszenie.objects.create(
        autor=request.user,
        kategoria='zajęcia',
        tresc=tresc,
        strona=f'/moj-tydzien/{nauczyciel.id}/?dzien={dzien_data.isoformat()}',
        dane=dane or {},
    )
    return JsonResponse({
        'blad': ('Nie możesz tego zrobić samodzielnie — w tym terminie są zajęcia. '
                 'Zgłoszenie wysłane: skontaktuj się z koordynatorem w sprawie zmiany.'),
    }, status=403)

@login_required
def kalendarz_dane(request, nauczyciel_id):
    """AJAX: przełącza godzinę lub zapisuje przerwę dnia i zwraca odświeżoną siatkę godzin."""
    nauczyciel = get_object_or_404(Nauczyciel, pk=nauczyciel_id)
    edycja = moze_edytowac_kalendarz(request.user, nauczyciel_id)
    if not edycja:
        return JsonResponse({'blad': 'Brak uprawnień do edycji.'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'blad': 'Tylko POST.'}, status=405)

    try:
        dzien_data = date.fromisoformat(request.POST.get('dzien', ''))
    except ValueError:
        return JsonResponse({'blad': 'Nieprawidłowa data.'}, status=400)

    akcja = request.POST.get('akcja')
    komunikat = ''

    if akcja == 'przelacz_godzine':
        etykieta = request.POST.get('godzina', '')
        start = datetime.strptime(etykieta, '%H:%M').time()
        koniec = (datetime.combine(date.today(), start) + timedelta(minutes=30)).time()
        slot = Dostepnosc.objects.filter(
            nauczyciel=nauczyciel,
            data_od=dzien_data,
            godzina_od=start,
            godzina_do=koniec,
        ).first()
        if slot:
            slot.delete()
            komunikat = f'Godzina {etykieta} — odznaczona tylko w tym dniu (cykl co tydzień zostaje).'
        else:
            # każda godzina od razu jako cykliczna: co tydzień do końca wakacji (31.08)
            Dostepnosc.objects.create(
                nauczyciel=nauczyciel,
                data_od=dzien_data,
                data_do=dzien_data,
                godzina_od=start,
                godzina_do=koniec,
                cykliczny=True,
                cykliczny_okres=1,
            )
            _rozszerz_cyklicznie(nauczyciel, dzien_data, start, koniec, okres=1)
            komunikat = (f'Godzina {etykieta} — dostępna co tydzień '
                         f'do {_koniec_wakacji(dzien_data):%d.%m.%Y}.')
    elif akcja == 'zapisz_przerwe':
        try:
            nowa_przerwa = int(request.POST.get('przerwa', 0))
        except (TypeError, ValueError):
            nowa_przerwa = 0
        if nowa_przerwa not in (5, 10, 15, 30):
            nowa_przerwa = 5
        PrzerwaDnia.objects.update_or_create(
            nauczyciel=nauczyciel,
            data=dzien_data,
            defaults={'minuty': nowa_przerwa},
        )
        komunikat = f'Przerwa {dzien_data:%d.%m.%Y}: {nowa_przerwa} min.'
    elif akcja == 'zaznacz_zakres':
        try:
            start = datetime.strptime(request.POST.get('od', ''), '%H:%M').time()
            koniec_zakresu = datetime.strptime(request.POST.get('do', ''), '%H:%M').time()
        except ValueError:
            return JsonResponse({'blad': 'Nieprawidłowy zakres godzin.'}, status=400)
        odznaczyc = request.POST.get('tryb') == 'odznacz'
        # zawsze co tydzień (bez opcji „nie” i „co 2 tygodnie”) — decyzja szefa
        cykliczny = not odznaczyc
        okres = 1
        kursor = datetime.combine(date(2000, 1, 1), start)
        koniec_dt = datetime.combine(date(2000, 1, 1), koniec_zakresu)
        sloty_zakresu = []
        while kursor < koniec_dt:
            sloty_zakresu.append((kursor.time(), (kursor + timedelta(minutes=30)).time()))
            kursor += timedelta(minutes=30)
        # masowo: 1 zapytanie o sloty dnia zamiast pętli update_or_create/delete po każdym slocie
        sloty_set = set(sloty_zakresu)
        istniejace_dnia = {
            (t.godzina_od, t.godzina_do): t
            for t in Dostepnosc.objects.filter(nauczyciel=nauczyciel, data_od=dzien_data)
        }
        if odznaczyc:
            pks = [t.pk for klucz, t in istniejace_dnia.items() if klucz in sloty_set]
            if pks:
                Dostepnosc.objects.filter(pk__in=pks).delete()
        else:
            # flaga 🔁 dotyczy TYLKO tego zakresu — pozostałe zakresy dnia mogą mieć inną
            do_zapisu = []
            brakujace = []
            for s, e in sloty_zakresu:
                t = istniejace_dnia.get((s, e))
                if t is not None:
                    if t.data_do != dzien_data or t.cykliczny != cykliczny or t.cykliczny_okres != okres:
                        t.data_do = dzien_data
                        t.cykliczny = cykliczny
                        t.cykliczny_okres = okres
                        do_zapisu.append(t)
                else:
                    brakujace.append(Dostepnosc(
                        nauczyciel=nauczyciel,
                        data_od=dzien_data,
                        data_do=dzien_data,
                        godzina_od=s,
                        godzina_do=e,
                        cykliczny=cykliczny,
                        cykliczny_okres=okres,
                    ))
            for t in do_zapisu:
                t.save(update_fields=['data_do', 'cykliczny', 'cykliczny_okres'])
            if brakujace:
                Dostepnosc.objects.bulk_create(brakujace)
        komunikat = (
            f'Godziny {start:%H:%M}–{koniec_zakresu:%H:%M} — odznaczono.'
            if odznaczyc else
            f'Godziny {start:%H:%M}–{koniec_zakresu:%H:%M} — oznaczono jako dostępne.'
        )
        if not odznaczyc:
            # pokaż, o której skończą się zajęcia w tym zakresie (rytm 60+przerwa od startu zakresu)
            krok_z = timedelta(minutes=GODZINA_LEKCJI + _przerwa_dnia(nauczyciel, dzien_data))
            sloty_r = _rytm_zakresu(
                datetime.combine(date(2000, 1, 1), start),
                datetime.combine(date(2000, 1, 1), koniec_zakresu),
                krok_z,
            )
            if sloty_r:
                ost = sloty_r[-1]
                koniec_zajec = ost + timedelta(minutes=GODZINA_LEKCJI)
                komunikat += (f' Ostatnia lekcja w tym zakresie: {ost:%H:%M}–{koniec_zajec:%H:%M}'
                              f' (zajęcia kończą się o {koniec_zajec:%H:%M}).')
        if cykliczny and not odznaczyc:
            # cykliczność od razu co tydzień aż do końca wakacji (31.08)
            skopiowane = _rozszerz_cyklicznie(nauczyciel, dzien_data, start, koniec_zakresu, okres=1)
            ostatni_dzien = _koniec_wakacji(dzien_data)
            komunikat += (f' Zakres powtórzony co tydzień do {ostatni_dzien:%d.%m.%Y} '
                          f'({skopiowane} terminów).')
    elif akcja == 'wklej_plan':
        try:
            zrodlo = date.fromisoformat(request.POST.get('zrodlo', ''))
        except ValueError:
            return JsonResponse({'blad': 'Nieprawidłowa data źródłowa.'}, status=400)
        if zrodlo == dzien_data:
            return JsonResponse({'blad': 'Dzień źródłowy i docelowy są takie same.'}, status=400)
        zrodlo_sloty = list(
            Dostepnosc.objects.filter(nauczyciel=nauczyciel, data_od=zrodlo)
        )
        if not zrodlo_sloty:
            return JsonResponse({'blad': 'Dzień źródłowy nie ma żadnych godzin.'}, status=400)
        Dostepnosc.objects.filter(nauczyciel=nauczyciel, data_od=dzien_data).delete()
        Dostepnosc.objects.bulk_create([
            Dostepnosc(
                nauczyciel=nauczyciel,
                data_od=dzien_data,
                data_do=dzien_data,
                godzina_od=slot.godzina_od,
                godzina_do=slot.godzina_do,
            )
            for slot in zrodlo_sloty
        ])
        zrodlo_przerwa = PrzerwaDnia.objects.filter(nauczyciel=nauczyciel, data=zrodlo).first()
        if zrodlo_przerwa:
            PrzerwaDnia.objects.update_or_create(
                nauczyciel=nauczyciel,
                data=dzien_data,
                defaults={'minuty': zrodlo_przerwa.minuty},
            )
        else:
            PrzerwaDnia.objects.filter(nauczyciel=nauczyciel, data=dzien_data).delete()
        komunikat = f'Plan dnia {zrodlo:%d.%m.%Y} wklejono na {dzien_data:%d.%m.%Y}.'
    elif akcja == 'zapisz_link':
        link = (request.POST.get('link_meet') or '').strip()
        nauczyciel.link_meet = link
        nauczyciel.save()
        komunikat = 'Link Google Meet zapisany.' if link else 'Link Google Meet usunięty.'
    elif akcja == 'zapisz_discord_id':
        discord_id = (request.POST.get('discord_id') or '').strip()
        if discord_id and not re.fullmatch(r'\d{5,32}', discord_id):
            return JsonResponse({'blad': 'ID Discorda to same cyfry (Discord → ustawienia → tryb deweloperski → PPM na sobie → „Kopiuj ID użytkownika”).'}, status=400)
        nauczyciel.discord_id = discord_id
        nauczyciel.save()
        komunikat = 'ID Discorda zapisane — będziesz dostawać powiadomienia o pierwszych lekcjach.' if discord_id else 'ID Discorda usunięte.'
    elif akcja == 'usun_zakres':
        # TYLKO ten dzień staje się wolny — cykl co tydzień zostaje na kolejne tygodnie
        try:
            start = datetime.strptime(request.POST.get('od', ''), '%H:%M').time()
            koniec_zakresu = datetime.strptime(request.POST.get('do', ''), '%H:%M').time()
        except ValueError:
            return JsonResponse({'blad': 'Nieprawidłowy zakres godzin.'}, status=400)
        nazwa_n = f'{nauczyciel.imie} {nauczyciel.nazwisko}'.strip().lower()
        lekcje_dnia = [
            timezone.localtime(l.data).strftime('%H:%M')
            for l in Lekcja.objects.filter(data__date=dzien_data, odwolana=False)
            if lekcja_jest_u_nauczyciela(l, nazwa_n)
        ]
        z_lekcjami = [g for g in lekcje_dnia if start <= datetime.strptime(g, '%H:%M').time() < koniec_zakresu]
        if z_lekcjami and not czy_szef(request.user):
            return _prosi_o_koordynatora(
                request, nauczyciel, dzien_data,
                f'usunąć zakres {start:%H:%M}–{koniec_zakresu:%H:%M} (tylko ten dzień)',
                ', '.join(z_lekcjami),
                dane={'akcja': 'usun_zakres', 'nauczyciel_id': nauczyciel.id,
                      'dzien': dzien_data.isoformat(), 'od': f'{start:%H:%M}', 'do': f'{koniec_zakresu:%H:%M}'},
            )
        usuniete = Dostepnosc.objects.filter(
            nauczyciel=nauczyciel,
            data_od=dzien_data,
            godzina_od__gte=start,
            godzina_do__lte=koniec_zakresu,
        ).delete()[0]
        komunikat = (f'Godziny {start:%H:%M}–{koniec_zakresu:%H:%M} usunięte tylko w dniu {dzien_data:%d.%m.%Y} '
                     f'({usuniete} terminów) — cykl co tydzień zostaje na kolejne tygodnie.')
        if z_lekcjami:
            lekcje_txt = ', '.join(z_lekcjami)
            komunikat += f' UWAGA: tego dnia w tym zakresie są lekcje ({lekcje_txt}) — odwołaj je osobno (✖ na lekcji).'
    elif akcja == 'usun_caly_zakres':
        # CAŁY cykl od tego dnia w przód: ten dzień + wszystkie kolejne tygodnie (do 31.08)
        try:
            start = datetime.strptime(request.POST.get('od', ''), '%H:%M').time()
            koniec_zakresu = datetime.strptime(request.POST.get('do', ''), '%H:%M').time()
        except ValueError:
            return JsonResponse({'blad': 'Nieprawidłowy zakres godzin.'}, status=400)
        nazwa_n = f'{nauczyciel.imie} {nauczyciel.nazwisko}'.strip().lower()
        z_lekcjami = [
            timezone.localtime(l.data).strftime('%d.%m %H:%M')
            for l in Lekcja.objects.filter(data__date__gte=dzien_data, odwolana=False)
            if lekcja_jest_u_nauczyciela(l, nazwa_n)
            and start <= timezone.localtime(l.data).time() < koniec_zakresu
        ]
        if z_lekcjami and not czy_szef(request.user):
            return _prosi_o_koordynatora(
                request, nauczyciel, dzien_data,
                f'usunąć CAŁY cykl {start:%H:%M}–{koniec_zakresu:%H:%M} od {dzien_data:%d.%m.%Y} w przód',
                ', '.join(z_lekcjami),
                dane={'akcja': 'usun_caly_zakres', 'nauczyciel_id': nauczyciel.id,
                      'dzien': dzien_data.isoformat(), 'od': f'{start:%H:%M}', 'do': f'{koniec_zakresu:%H:%M}'},
            )
        usuniete = Dostepnosc.objects.filter(
            nauczyciel=nauczyciel,
            data_od__gte=dzien_data,
            godzina_od__gte=start,
            godzina_do__lte=koniec_zakresu,
        ).delete()[0]
        komunikat = (f'Usunięto cały zakres {start:%H:%M}–{koniec_zakresu:%H:%M} od {dzien_data:%d.%m.%Y} w przód '
                     f'({usuniete} terminów) — ten dzień i wszystkie kolejne tygodnie.')
        if z_lekcjami:
            lekcje_txt = ', '.join(z_lekcjami)
            komunikat += f' UWAGA: w tym zakresie są zajęcia ({lekcje_txt}).'
    elif akcja == 'wyczysc_dzien':
        nazwa_n = f'{nauczyciel.imie} {nauczyciel.nazwisko}'.strip().lower()
        z_lekcjami = [
            timezone.localtime(l.data).strftime('%H:%M')
            for l in Lekcja.objects.filter(data__date=dzien_data, odwolana=False)
            if lekcja_jest_u_nauczyciela(l, nazwa_n)
        ]
        if z_lekcjami and not czy_szef(request.user):
            return _prosi_o_koordynatora(
                request, nauczyciel, dzien_data,
                'wyczyścić cały dzień (usunąć wszystkie godziny)',
                ', '.join(z_lekcjami),
                dane={'akcja': 'wyczysc_dzien', 'nauczyciel_id': nauczyciel.id, 'dzien': dzien_data.isoformat()},
            )
        usuniete = Dostepnosc.objects.filter(nauczyciel=nauczyciel, data_od=dzien_data).count()
        Dostepnosc.objects.filter(nauczyciel=nauczyciel, data_od=dzien_data).delete()
        komunikat = f'Wyczyszczono cały dzień {dzien_data:%d.%m.%Y} — usunięto {usuniete} terminów.'
    elif akcja in ('odwolaj_lekcje', 'przywroc_lekcje'):
        # odwołanie/przywrócenie JEDNEJ lekcji — cykl zajęć zostaje bez zmian
        lekcja = get_object_or_404(Lekcja, pk=request.POST.get('lekcja_id'))
        nazwa_n = f'{nauczyciel.imie} {nauczyciel.nazwisko}'.strip().lower()
        if not lekcja_jest_u_nauczyciela(lekcja, nazwa_n):
            return JsonResponse({'blad': 'To nie jest lekcja tego korepetytora.'}, status=403)
        if akcja == 'odwolaj_lekcje' and not lekcja.odwolana and not czy_szef(request.user):
            return _prosi_o_koordynatora(
                request, nauczyciel, timezone.localtime(lekcja.data).date(),
                f'odwołać zajęcia {timezone.localtime(lekcja.data):%d.%m.%Y %H:%M} (uczeń: {lekcja.uczen})',
                dane={'akcja': 'odwolaj_lekcje', 'nauczyciel_id': nauczyciel.id,
                      'dzien': timezone.localtime(lekcja.data).date().isoformat(),
                      'lekcja_id': lekcja.id, 'uczen_id': lekcja.uczen_id, 'uczen': str(lekcja.uczen)},
            )
        lekcja.odwolana = (akcja == 'odwolaj_lekcje')
        lekcja.save(update_fields=['odwolana'])
        komunikat = (f'Lekcja {timezone.localtime(lekcja.data):%d.%m.%Y %H:%M} odwołana — tylko ten termin, cykl zostaje.'
                     if lekcja.odwolana else
                     f'Lekcja {timezone.localtime(lekcja.data):%d.%m.%Y %H:%M} przywrócona.')
    elif akcja == 'przesun_lekcje':
        # przesunięcie JEDNEJ lekcji na inny dzień TEGO SAMEGO tygodnia — cykl zostaje
        lekcja = get_object_or_404(Lekcja, pk=request.POST.get('lekcja_id'))
        nazwa_n = f'{nauczyciel.imie} {nauczyciel.nazwisko}'.strip().lower()
        if not lekcja_jest_u_nauczyciela(lekcja, nazwa_n):
            return JsonResponse({'blad': 'To nie jest lekcja tego korepetytora.'}, status=403)
        if not czy_szef(request.user):
            return _prosi_o_koordynatora(
                request, nauczyciel, timezone.localtime(lekcja.data).date(),
                f'przesunąć zajęcia {timezone.localtime(lekcja.data):%d.%m.%Y %H:%M} (uczeń: {lekcja.uczen}) na inny dzień tego tygodnia',
                dane={'akcja': 'przesun_lekcje', 'nauczyciel_id': nauczyciel.id,
                      'dzien': timezone.localtime(lekcja.data).date().isoformat(),
                      'lekcja_id': lekcja.id, 'uczen_id': lekcja.uczen_id, 'uczen': str(lekcja.uczen)},
            )
        try:
            nowy_dzien = date.fromisoformat(request.POST.get('nowy_dzien', ''))
            nowa_godzina = datetime.strptime(request.POST.get('nowa_godzina', ''), '%H:%M').time()
        except ValueError:
            return JsonResponse({'blad': 'Nieprawidłowy nowy termin.'}, status=400)
        stara = timezone.localtime(lekcja.data)
        pon = stara.date() - timedelta(days=stara.date().weekday())
        if not (pon <= nowy_dzien <= pon + timedelta(days=6)):
            return JsonResponse({'blad': 'Nowy termin musi być w tym samym tygodniu co lekcja.'}, status=400)
        if nowy_dzien == stara.date() and nowa_godzina == stara.time():
            return JsonResponse({'blad': 'To już jest ten termin.'}, status=400)
        if nowa_godzina.strftime('%H:%M') not in wolne_godziny_nauczyciela(nauczyciel, nowy_dzien, pomijaj_uczen_id=lekcja.uczen_id):
            return JsonResponse({'blad': f'Korepetytor nie ma wolnej godziny {nowy_dzien:%d.%m} o {nowa_godzina:%H:%M}.'}, status=400)
        lekcja.data = timezone.make_aware(datetime.combine(nowy_dzien, nowa_godzina))
        lekcja.save(update_fields=['data'])
        komunikat = (f'Lekcja przesunięta: {stara:%d.%m.%Y %H:%M} → {nowy_dzien:%d.%m.%Y} {nowa_godzina:%H:%M} '
                     f'(tylko ten jeden termin; cykl bez zmian).')
    else:
        return JsonResponse({'blad': 'Nieznana akcja.'}, status=400)

    zaznaczone = set()
    for termin in Dostepnosc.objects.filter(nauczyciel=nauczyciel, data_od=dzien_data):
        start = datetime.combine(dzien_data, termin.godzina_od)
        koniec = datetime.combine(dzien_data, termin.godzina_do)
        while start < koniec:
            zaznaczone.add(start.strftime('%H:%M'))
            start += timedelta(minutes=30)

    wpis = PrzerwaDnia.objects.filter(nauczyciel=nauczyciel, data=dzien_data).first()
    przerwa_min = wpis.minuty if wpis else (nauczyciel.przerwa or 5)

    # plan dnia = rytm KAŻDEGO zakresu osobno (taki sam jak w formularzu ucznia)
    plan_dnia, sloty_przerwy = [], set()
    krok = timedelta(minutes=GODZINA_LEKCJI + przerwa_min)
    for s, e in _zakresy_dnia(nauczyciel, dzien_data):
        sloty_zakresu = _rytm_zakresu(s, e, krok)
        for i, t in enumerate(sloty_zakresu):
            plan_dnia.append({
                'typ': 'lekcja',
                'numer': len(plan_dnia) + 1,
                'start': t.strftime('%H:%M'),
                'koniec': (t + timedelta(minutes=GODZINA_LEKCJI)).strftime('%H:%M'),
            })
            if i + 1 < len(sloty_zakresu):
                nastepny = sloty_zakresu[i + 1]
                koniec_lekcji = t + timedelta(minutes=GODZINA_LEKCJI)
                plan_dnia.append({
                    'typ': 'przerwa',
                    'start': koniec_lekcji.strftime('%H:%M'),
                    'koniec': nastepny.strftime('%H:%M'),
                })
                kursor = koniec_lekcji.replace(minute=(koniec_lekcji.minute // 30) * 30)
                while kursor < nastepny:
                    sloty_przerwy.add(kursor.strftime('%H:%M'))
                    kursor += timedelta(minutes=30)
    plan_dnia.sort(key=lambda w: (w['start'], w['typ'] == 'przerwa'))
    zajete = set()
    nazwa_nauczyciela_dane = f'{nauczyciel.imie} {nauczyciel.nazwisko}'.strip().lower()
    for lekcja in Lekcja.objects.select_related('uczen', 'przedmiot_karta').filter(data__date=dzien_data, odwolana=False):
        if not lekcja_jest_u_nauczyciela(lekcja, nazwa_nauczyciela_dane):
            continue
        start = timezone.localtime(lekcja.data).replace(tzinfo=None)
        koniec = start + timedelta(minutes=GODZINA_LEKCJI)
        # kafelki PELNYCH GODZIN nachodzące na lekcję są zajęte (lekcja 11:50-12:50 -> kafelki 11:00 i 12:00)
        kursor = start.replace(minute=0)
        while kursor < koniec:
            zajete.add(kursor.strftime('%H:%M'))
            kursor += timedelta(hours=1)
        # przerwa po lekcji też zajmuje kafelki pełnych godzin (jeśli nie są zaznaczone jako dostępne)
        koniec_przerwy = koniec + timedelta(minutes=przerwa_min)
        kursor = koniec.replace(minute=0)
        while kursor < koniec_przerwy:
            etykieta_przerwy = kursor.strftime('%H:%M')
            if etykieta_przerwy not in zaznaczone and etykieta_przerwy not in zajete:
                sloty_przerwy.add(etykieta_przerwy)
            kursor += timedelta(hours=1)
    kafelki = []
    for h in range(6, 21):
        for m in (0,):
            etykieta = f'{h:02d}:{m:02d}'
            kafelki.append({
                'etykieta': etykieta,
                'zaznaczony': etykieta in zaznaczone,
                'przerwa': etykieta in sloty_przerwy and etykieta not in zaznaczone,
                'zajety': etykieta in zajete,
            })
    return JsonResponse({
        'komunikat': komunikat,
        'przerwa': przerwa_min,
        'kafelki': kafelki,
        'plan_dnia': plan_dnia,
    })





@login_required
def lekcje(request):
    """Zakładka lekcje: lista uczniów z liczbą lekcji i wejściem do kalendarza lekcji.
    Admin i koordynator widzą wszystkich, pracownik — swoich,
    korepetytor — uczniów przypisanych do niego (wg pola korepetytor)."""
    nauczyciel_id = getattr(request.user, 'nauczyciel_id', None)
    nauczyciel = get_object_or_404(Nauczyciel, pk=nauczyciel_id) if nauczyciel_id else None
    nazwa_nauczyciela = f'{nauczyciel.imie} {nauczyciel.nazwisko}'.strip().lower() if nauczyciel else ''
    if request.method == 'POST' and 'szybka_lekcja' in request.POST:
        if not nauczyciel:
            raise PermissionDenied
        uczen = get_object_or_404(Uczen, pk=request.POST.get('uczen_id'), zakonczona=False)
        moj_uczen = (uczen.korepetytor or '').strip().lower() == nazwa_nauczyciela or any(
            (k.korepetytor or '').strip().lower() == nazwa_nauczyciela
            for k in uczen.przedmioty_ucznia.all()
        )
        if not moj_uczen:
            messages.error(request, 'Możesz dodawać lekcje tylko swoim uczniom.')
            return redirect('lekcje')
        try:
            dzien = date.fromisoformat(request.POST.get('data', ''))
            godzina = datetime.strptime(request.POST.get('godzina', ''), '%H:%M').time()
        except (ValueError, TypeError):
            messages.error(request, 'Wybierz ucznia, datę i godzinę.')
            return redirect('lekcje')
        etykieta = f'{godzina.hour:02d}:{godzina.minute:02d}'
        start_dt = datetime.combine(dzien, godzina)
        if etykieta not in wolne_godziny_nauczyciela(nauczyciel, dzien, pomijaj_uczen_id=uczen.id):
            if kolizja_lekcji_nauczyciela(nauczyciel, dzien, start_dt, pomijaj_uczen_id=uczen.id):
                messages.error(request, f'Godzina {etykieta} jest już zajęta inną lekcją.')
            else:
                messages.error(request, f'Godzina {etykieta} jest poza Twoimi godzinami pracy — zaznacz ją w „Mój tydzień”.')
            return redirect('lekcje')
        data_lekcji = timezone.make_aware(start_dt)
        Lekcja.objects.get_or_create(uczen=uczen, data=data_lekcji, przedmiot_karta__isnull=True)
        Dostepnosc.objects.get_or_create(
            nauczyciel=nauczyciel, data_od=dzien, data_do=dzien,
            godzina_od=godzina,
            godzina_do=(datetime.combine(date(2000, 1, 1), godzina) + timedelta(minutes=GODZINA_LEKCJI)).time(),
        )
        messages.success(
            request,
            f'Zapisano lekcję: {uczen.imie_ucznia} {uczen.nazwisko_rodzica} — {dzien:%d.%m.%Y} o {etykieta} (godzina zablokowana).',
        )
        return redirect('lekcje')
    if nauczyciel:
        uczniowie_lista = [
            u for u in Uczen.objects.filter(zakonczona=False).prefetch_related('przedmioty_ucznia')
            if (u.korepetytor or '').strip().lower() == nazwa_nauczyciela
            or any(
                (k.korepetytor or '').strip().lower() == nazwa_nauczyciela
                for k in u.przedmioty_ucznia.all()
            )
        ]
    elif czy_szef(request.user) or czy_koordynator(request.user):
        uczniowie_lista = list(Uczen.objects.filter(zakonczona=False))
    else:
        uczniowie_lista = list(Uczen.objects.filter(umowil=request.user, zakonczona=False))
    fraza = (request.GET.get('szukaj') or '').strip().lower()
    if fraza:
        uczniowie_lista = [
            u for u in uczniowie_lista
            if fraza in u.imie_ucznia.lower()
            or fraza in u.nazwisko_rodzica.lower()
            or fraza in (u.korepetytor or '').lower()
            or fraza in u.przedmiot.lower()
        ]
    dzisiaj = date.today()
    liczba_lekcji = {}
    for lekcja in Lekcja.objects.filter(
        uczen__in=uczniowie_lista,
        data__year=dzisiaj.year,
        data__month=dzisiaj.month,
    ):
        liczba_lekcji[lekcja.uczen_id] = liczba_lekcji.get(lekcja.uczen_id, 0) + 1
    for u in uczniowie_lista:
        u.liczba_lekcji = liczba_lekcji.get(u.id, 0)
    uczniowie_cykliczni = [u for u in uczniowie_lista if u.zajecia_cykliczne]
    uczniowie_pierwsze = [u for u in uczniowie_lista if not u.zajecia_cykliczne]
    for u in uczniowie_lista:
        u.lekcje_terminy = _lekcje_cykliczne_tekst(u)
    return render(request, 'konta/lekcje.html', {
        'uczniowie': uczniowie_lista,
        'uczniowie_cykliczni': uczniowie_cykliczni,
        'uczniowie_pierwsze': uczniowie_pierwsze,
        'fraza': (request.GET.get('szukaj') or '').strip(),
        'nauczyciel_id': nauczyciel_id,
        'dzisiaj': date.today().isoformat(),
        'czy_szef': czy_szef(request.user),
        'jest_nauczycielem': bool(getattr(request.user, 'nauczyciel_id', None)),
    })





def _lekcje_cykliczne_tekst(uczen, limit_miesiecy=2):
    """Przyszłe lekcje ucznia (od teraz, bez odwołanych) pogrupowane po miesiącu,
    np. 'lis 2026: 26.11 17:00, 30.11 17:00; gru 2026: 07.12 17:00'.
    Pokazujemy max `limit_miesiecy` najbliższych miesięcy — zajęcia cykliczne są
    odnawiane co miesiąc komendą odnow_cykliczne (CRON)."""
    lekcje = Lekcja.objects.filter(uczen=uczen, odwolana=False, data__gte=timezone.now()).order_by('data')
    grupy = {}
    for l in lekcje:
        lok = timezone.localtime(l.data)
        grupy.setdefault((lok.year, lok.month), []).append(f'{lok.day:02d}.{lok.month:02d} {lok:%H:%M}')
    if not grupy:
        return '—'
    return '; '.join(
        f'{MIESIACE[m - 1][:3]} {r}: ' + ', '.join(terminy)
        for (r, m), terminy in sorted(grupy.items())[:limit_miesiecy]
    )


def _lekcje_cykliczne_dane(uczen):
    """Przyszłe lekcje ucznia (od teraz, bez odwołanych) jako lista słowników
    {y, m, d, g} — dane do mini-kalendarza miesięcznego w Płatnościach (JS grupuje
    je po miesiącu i można strzałkami ‹ › przesuwać miesiące jak w kalendarzu)."""
    lekcje = Lekcja.objects.filter(uczen=uczen, odwolana=False, data__gte=timezone.now()).order_by('data')
    wynik = []
    for l in lekcje:
        lok = timezone.localtime(l.data)
        wynik.append({'y': lok.year, 'm': lok.month, 'd': f'{lok.day:02d}.{lok.month:02d}', 'g': f'{lok:%H:%M}'})
    return wynik


def _lekcje_cykliczne_dane(uczen):
    """Przyszłe lekcje ucznia (od teraz, bez odwołanych) jako lista słowników
    {y, m, d, g} — dane do mini-kalendarza miesięcznego w Płatnościach (JS grupuje
    je po miesiącu i można strzałkami ‹ › przesuwać miesiące jak w kalendarzu)."""
    lekcje = Lekcja.objects.filter(uczen=uczen, odwolana=False, data__gte=timezone.now()).order_by('data')
    wynik = []
    for l in lekcje:
        lok = timezone.localtime(l.data)
        wynik.append({'y': lok.year, 'm': lok.month, 'd': f'{lok.day:02d}.{lok.month:02d}', 'g': f'{lok:%H:%M}'})
    return wynik


@login_required
def platnosci(request):
    """Zakładka płatności: lista uczniów, statusy zapłacone i wysyłka raportu.
    Szef i koordynator (edycja i raporty, bez usuwania uczniów)."""
    if not (czy_szef(request.user) or czy_koordynator(request.user)):
        raise PermissionDenied
    uczniowie_lista = Uczen.objects.filter(zaplacone=False, zakonczona=False, rozliczono_kiedy__isnull=True)
    uczniowie_cykliczni = uczniowie_lista.filter(zajecia_cykliczne=True)
    uczniowie_pojedynczy = uczniowie_lista.filter(zajecia_cykliczne=False)
    zaplaconi = Uczen.objects.filter(zaplacone=True, zakonczona=False)
    zaplaconi_cykliczni = zaplaconi.filter(zajecia_cykliczne=True)
    zaplaconi_pojedynczy = zaplaconi.filter(zajecia_cykliczne=False)
    historia = Uczen.objects.filter(zakonczona=False, rozliczono_kiedy__isnull=False).order_by('-rozliczono_kiedy')
    raporty = list(RaportPlatnosci.objects.all()[:20])
    wszyscy_uczniowie = list(Uczen.objects.filter(zakonczona=False).prefetch_related('przedmioty_ucznia'))
    email_odbiorcy = request.POST.get('email_raportu', '').strip()

    if request.method == 'POST' and 'oznacz_zaplacone' in request.POST:
        uczen = get_object_or_404(Uczen, pk=request.POST['uczen_id'])
        uczen.zaplacone = True
        uczen.save()
        messages.success(request, f'{uczen.imie_ucznia} {uczen.nazwisko_rodzica} — oznaczono jako opłacone.')
        return redirect('platnosci')

    if request.method == 'POST' and 'odznacz_zaplacone' in request.POST:
        uczen = get_object_or_404(Uczen, pk=request.POST['uczen_id'])
        uczen.zaplacone = False
        uczen.save()
        messages.info(request, f'{uczen.imie_ucznia} {uczen.nazwisko_rodzica} — cofnięto status opłacone.')
        return redirect('platnosci')

    if request.method == 'POST' and 'usun_ucznia' in request.POST:
        # usuwanie uczniów (na zawsze) — tylko szef; koordynator edytuje, ale nie usuwa
        if not czy_szef(request.user):
            raise PermissionDenied
        uczen = get_object_or_404(Uczen, pk=request.POST['uczen_id'])
        imie = f'{uczen.imie_ucznia} {uczen.nazwisko_rodzica}'
        uczen.delete()
        messages.success(request, f'{imie} — usunięto z listy.')
        return redirect('platnosci')

    if request.method == 'POST' and 'wyslij_raport' in request.POST:
        if not zaplaconi:
            messages.error(request, 'Brak zapłaconych płatności w kafelku „Zapłacone" — nie ma czego wysyłać.')
            return redirect('platnosci')
        if not email_odbiorcy:
            messages.error(request, 'Wpisz adres e-mail, na który ma pójść raport.')
            return redirect('platnosci')
        linie = []
        for u in zaplaconi:
            linie.append(
                f'{u.imie_ucznia} {u.nazwisko_rodzica} — nauczyciel: {u.korepetytor} '
                f'— wpisał: {u.kto_umowil} — 1. lekcja: {u.pierwsza_lekcja:%d.%m.%Y %H:%M}'
            )
        podsumowanie = f'\n\nRazem zapłaconych płatności: {len(zaplaconi)}.'
        tresc = 'Raport zapłaconych płatności:\n\n' + '\n'.join(linie) + podsumowanie
        try:
            send_mail(
                'Raport zapłaconych płatności — Panel pracowniczy',
                tresc,
                None,
                [email_odbiorcy],
            )
        except Exception:
            messages.warning(
                request,
                f'E-mail nie został wysłany (serwer pocztowy niedostępny) — '
                'pobierz raport przyciskiem „Pobierz raport jako plik” i zapisz go ręcznie.',
            )
            return redirect('platnosci')
        RaportPlatnosci.objects.create(
            zawartosc=tresc,
            email_odbiorcy=email_odbiorcy,
            wyslal=request.user.get_full_name() or request.user.username,
        )
        # rozliczone pozycje: znikają z kafelka „Zapłacone”, ale NIE wracają do nieopłaconych —
        # trafiają do „Historii płatności” na dole zakładki (pole rozliczono_kiedy)
        teraz = timezone.now()
        for u in zaplaconi:
            u.zaplacone = False
            u.rozliczono_kiedy = teraz
            u.save(update_fields=['zaplacone', 'rozliczono_kiedy'])
        messages.success(
            request,
            f'Raport wysłano na {email_odbiorcy}. Rozliczone pozycje trafiły do „Historii płatności” '
            'na dole zakładki — nie wrócą do nieopłaconych.',
        )
        return redirect('platnosci')
    teraz = timezone.now()
    rozliczenia = []
    for nauczyciel in Nauczyciel.objects.filter(aktywny=True).order_by('nazwisko', 'imie'):
        nazwa = f'{nauczyciel.imie} {nauczyciel.nazwisko}'
        nazwa_low = nazwa.strip().lower()
        # uczniowie, u których ten nauczyciel uczy: głównego przedmiotu LUB dodatkowego
        jego_uczniowie = [
            u for u in wszyscy_uczniowie
            if (u.korepetytor or '').strip().lower() == nazwa_low
            or any(
                (k.korepetytor or '').strip().lower() == nazwa_low
                for k in u.przedmioty_ucznia.all()
            )
        ]
        if jego_uczniowie:
            zaplacone_uczniowie = [u for u in jego_uczniowie if u.zaplacone]
            # kwota = suma stawek za kazda ODBYTA lekcje oplaconych uczniow
            # TYLKO z przedmiotow tego nauczyciela (glownego i dodatkowych):
            # liczemy lekcje do teraz, pomijamy odwolane
            kwota = 0
            liczba_odbytych = 0
            for u in zaplacone_uczniowie:
                # lekcje głównego przedmiotu (jeśli nauczyciel jest głównym korepetytorem)
                if (u.korepetytor or '').strip().lower() == nazwa_low:
                    odbyte = Lekcja.objects.filter(
                        uczen=u,
                        przedmiot_karta__isnull=True,
                        odwolana=False,
                        data__lte=teraz,
                    ).count()
                    liczba_odbytych += odbyte
                    kwota += nauczyciel.stawka_dla(u.przedmiot, u.poziom) * odbyte
                # lekcje dodatkowych przedmiotów tego nauczyciela
                for karta in u.przedmioty_ucznia.all():
                    if (karta.korepetytor or '').strip().lower() == nazwa_low:
                        odbyte = Lekcja.objects.filter(
                            uczen=u,
                            przedmiot_karta=karta,
                            odwolana=False,
                            data__lte=teraz,
                        ).count()
                        liczba_odbytych += odbyte
                        kwota += nauczyciel.stawka_dla(karta.przedmiot, karta.poziom) * odbyte
            rozliczenia.append({
                'nauczyciel': nauczyciel,
                'liczba_uczniow': len(jego_uczniowie),
                'liczba_zaplaconych': len(zaplacone_uczniowie),
                'liczba_odbytych': liczba_odbytych,
                'kwota': kwota,
            })
    # rozliczenie pracownikow (osob wpisujacych uczniow): licznik odbytych lekcji
    # uczniow, ktorych dany pracownik wpisal — tylko lekcje ODBYTE (nie odwolane, nie przyszle)
    rozliczenia_pracownikow = []
    pracownicy_z_uczniami = {}
    for u in wszyscy_uczniowie:
        klucz = u.umowil_id
        if klucz:
            pracownicy_z_uczniami.setdefault(klucz, []).append(u)
    for pracownik_id, uczniowie_prac in pracownicy_z_uczniami.items():
        pracownik = Pracownik.objects.filter(pk=pracownik_id).first()
        if not pracownik:
            continue
        # zliczamy ODBYTE lekcje wszystkich uczniow tego pracownika + ich nauczyciele
        liczba_odbytych_prac = 0
        nauczyciele_set = set()
        for u in uczniowie_prac:
            liczba_odbytych_prac += Lekcja.objects.filter(
                uczen=u,
                odwolana=False,
                data__lte=teraz,
            ).count()
            if u.korepetytor:
                nauczyciele_set.add(u.korepetytor.strip())
            for k in u.przedmioty_ucznia.all():
                if k.korepetytor:
                    nauczyciele_set.add(k.korepetytor.strip())
        rozliczenia_pracownikow.append({
            'pracownik': pracownik,
            'liczba_uczniow': len(uczniowie_prac),
            'liczba_zaplaconych': len([u for u in uczniowie_prac if u.zaplacone]),
            'liczba_odbytych': liczba_odbytych_prac,
            'nauczyciele': sorted(nauczyciele_set),
        })
    rozliczenia_pracownikow.sort(key=lambda p: p['pracownik'].username)
    for u in (list(uczniowie_cykliczni) + list(uczniowie_pojedynczy)
              + list(zaplaconi_cykliczni) + list(zaplaconi_pojedynczy) + list(historia)):
        u.lekcje_dane = _lekcje_cykliczne_dane(u)
        u.lekcje_json = json.dumps(u.lekcje_dane, ensure_ascii=False)
        u.lekcje_cykliczne_tekst = _lekcje_cykliczne_tekst(u)   # pierwszy miesiąc jako tekst (fallback bez JS)
    return render(request, 'konta/platnosci.html', {
        'uczniowie_cykliczni': uczniowie_cykliczni,
        'uczniowie_pojedynczy': uczniowie_pojedynczy,
        'zaplaconi_cykliczni': zaplaconi_cykliczni,
        'zaplaconi_pojedynczy': zaplaconi_pojedynczy,
        'zaplaconi': zaplaconi,
        'historia': historia,
        'raporty': raporty,
        'email_raportu': email_odbiorcy,
        'rozliczenia': rozliczenia,
        'rozliczenia_pracownikow': rozliczenia_pracownikow,
    })


@login_required
def uzytkownicy(request):
    """Zakładka użytkownicy: dodawanie kont i nadawanie uprawnień.
    Admin (szef) widzi wszystko i może zarządzać kontami.
    Koordynator może dodawać konta — ale bez roli admina i bez zarządzania kontami.
    Konta podzielone na kategorie: admini, koordynatorzy, osoby umawiające, korepetytorzy."""
    if not (czy_szef(request.user) or czy_koordynator(request.user)):
        raise PermissionDenied
        # w zakładce Użytkownicy tylko aktywne konta — wyłączone są w Archiwum
    lista = Pracownik.objects.filter(is_active=True).order_by('username')
    form = FormularzUzytkownika(request.POST or None, bez_admina=not czy_szef(request.user))
    if request.method == 'POST' and not form.is_valid():
        messages.error(request, 'Nie udało się dodać użytkownika — popraw błędy w formularzu (czerwona ramka u góry).')
    if request.method == 'POST' and form.is_valid():
        # koordynator nie może tworzyć konta admina
        if not czy_szef(request.user) and form.cleaned_data.get('rola') == 'admin':
            messages.error(request, 'Koordynator nie może tworzyć kont administratorów.')
            return render(request, 'konta/uzytkownicy.html', {
                'form': form,
                'uzytkownicy': lista,
                'admini': [],
                'koordynatorzy': [],
                'umawiajacy': [],
                'nauczyciele': [],
                'czy_szef': False,
                'czy_koordynator': czy_koordynator(request.user),
            })
        try:
            nowy_uzytkownik = form.zapisz()
        except Exception as e:
            zapisz_blad_maila('utworzenie_konta', e)
            messages.error(request, 'Nie udało się utworzyć konta — konto nie zostało zapisane. Spróbuj ponownie; jeśli problem wróci, zgłoś to w zakładce Zgłoszenia.')
            return redirect('uzytkownicy')
        nowy_uzytkownik.utworzyl = request.user
        nowy_uzytkownik.save(update_fields=['utworzyl'])
        messages.success(request, 'Użytkownik został dodany.')

        # mail powitalny do nowego użytkownika — z Reply-To do twórcy konta
        # + jednorazowy link do ustawienia własnego hasła (jak reset hasła)
        if nowy_uzytkownik.email:
            link_logowania = request.build_absolute_uri(reverse('logowanie'))
            # link do ustawienia hasla DOKLADNIE dla tego nowego konta
            from django.utils.encoding import force_bytes
            from django.utils.http import urlsafe_base64_encode
            uid = urlsafe_base64_encode(force_bytes(nowy_uzytkownik.pk))
            token = token_generator_resetu.make_token(nowy_uzytkownik)
            link_hasla = request.build_absolute_uri(
                reverse('reset_hasla_potwierdz', kwargs={'uidb64': uid, 'token': token})
            )
            if nowy_uzytkownik.is_superuser:
                role_tekst = 'administrator (pełny dostęp)'
            elif nowy_uzytkownik.stanowisko == 'koordynator':
                role_tekst = 'koordynator'
            elif nowy_uzytkownik.nauczyciel:
                role_tekst = 'korepetytor'
            else:
                role_tekst = 'osoba umawiająca'
            tworca = request.user
            tworca_email = tworca.email or ''
            tworca_nazwa = tworca.get_full_name() or tworca.username
            try:
                from django.core.mail import EmailMessage
                mail = EmailMessage(
                    subject=f'Witamy w zespole, {nowy_uzytkownik.first_name or nowy_uzytkownik.username}!',
                    body=(
                        f'Dzień dobry {nowy_uzytkownik.first_name or nowy_uzytkownik.username},\n\n'
                        'dziękujemy za dołączenie do naszego zespołu!\n\n'
                        f'Twoje konto w Panelu pracowniczym zostało utworzone.\n'
                        f'Login: {nowy_uzytkownik.username}\n'
                        f'Rola: {role_tekst}\n\n'
                        f'Zaloguj się tutaj:\n{link_logowania}\n\n'
                        f'USTAW SWOJE HASŁO (kliknij — link ważny 3 dni):\n{link_hasla}\n\n'
                        'Po 3 dniach link wygasa i przestaje działać — wtedy na stronie\n'
                        'logowania użyj opcji „Nie pamiętasz hasła?”, aby dostać nowy link.\n\n'
                        'Jeśli hasło podał Ci administrator, możesz go użyć przy pierwszym logowaniu\n'
                        'i zmienić w zakładce „Zmień hasło" — albo po prostu ustaw własne hasło linkiem powyżej.\n\n'
                        f'W razie pytań odpisz na tego maila — trafi do {tworca_nazwa}.\n\n'
                        'Pozdrawiamy,\nZespół'
                    ),
                    to=[nowy_uzytkownik.email],
                )
                if tworca_email:
                    mail.reply_to = [tworca_email]
                mail.send()
                messages.info(request, f'Powitalny e-mail wysłany na adres {nowy_uzytkownik.email} (odpowiedź trafi do {tworca_nazwa}).')
            except Exception as e:
                zapisz_blad_maila('mail_powitalny_pracownik', e)
        odbiorcy_szefa = list(getattr(settings, 'EMAILE_POWIADOMIEN', []))
        if odbiorcy_szefa:
            if nowy_uzytkownik.is_superuser:
                rola_szefa_tekst = 'administrator (pełny dostęp)'
            elif nowy_uzytkownik.stanowisko == 'koordynator':
                rola_szefa_tekst = 'koordynator'
            elif nowy_uzytkownik.nauczyciel:
                rola_szefa_tekst = 'korepetytor'
            else:
                rola_szefa_tekst = 'osoba umawiająca'
            tresc_szefa = (
                f'Nowe konto w Panelu pracowniczym:\n\n'
                f'Login: {nowy_uzytkownik.username}\n'
                f'Imię i nazwisko: {nowy_uzytkownik.first_name} {nowy_uzytkownik.last_name}\n'
                f'E-mail: {nowy_uzytkownik.email}\n'
                f'Rola: {rola_szefa_tekst}\n'
                f'Konto utworzył: {request.user.get_full_name() or request.user.username}\n'
            )
            try:
                send_mail(
                    'Nowe konto użytkownika — Panel pracowniczy',
                    tresc_szefa,
                    None,
                    odbiorcy_szefa,
                )
            except Exception as e:
                zapisz_blad_maila('powiadomienie_szefa_o_nowym_koncie', e)
        return redirect('uzytkownicy')
    admini = [u for u in lista if u.is_superuser]
    koordynatorzy = [u for u in lista if not u.is_superuser and not u.nauczyciel and (u.stanowisko or '').strip().lower() == 'koordynator']
    umawiajacy = [u for u in lista if not u.is_superuser and not u.nauczyciel and (u.stanowisko or '').strip().lower() != 'koordynator']
    nauczyciele_konta = [u for u in lista if u.nauczyciel]
    DNI_SKROT = ['pon', 'wt', 'śr', 'czw', 'pt', 'sob', 'nied']
    for u in nauczyciele_konta:
        n = u.nauczyciel
        # przedmioty z klasą koloru poziomu (jak legenda w Uczniowie)
        u.przedmioty_kolor = [
            {'przedmiot': p.przedmiot, 'poziom': p.poziom, 'stawka': p.stawka, 'klasa': _klasa_poziomu(p.poziom)}
            for p in n.przedmioty.all()
        ]
        # zakresy pracy wg dnia tygodnia, np. "pon 6:00–15:00, śr 8:00–12:00"
        per_dzien = {}
        for d in Dostepnosc.objects.filter(nauczyciel=n):
            klucz = d.data_od.weekday()
            od, do = per_dzien.get(klucz, (d.godzina_od, d.godzina_do))
            per_dzien[klucz] = (min(od, d.godzina_od), max(do, d.godzina_do))
        u.zakresy_tekst = ', '.join(
            f'{DNI_SKROT[k]} {od:%H:%M}–{do:%H:%M}' for k, (od, do) in sorted(per_dzien.items())
        ) or '—'
    return render(request, 'konta/uzytkownicy.html', {
        'form': form,
        'uzytkownicy': lista,
        'admini': admini,
        'koordynatorzy': koordynatorzy,
        'umawiajacy': umawiajacy,
        'nauczyciele': nauczyciele_konta,
        'czy_szef': czy_szef(request.user),
        'czy_koordynator': czy_koordynator(request.user),
    })


@login_required
@tylko_szef
def przelacz_szefa(request, user_id):
    """Włącza/wyłącza uprawnienia szefa dla użytkownika."""
    uzytkownik = get_object_or_404(Pracownik, pk=user_id)
    if uzytkownik.id == request.user.id:
        messages.error(request, 'Nie możesz odebrać uprawnień samemu sobie.')
        return redirect('uzytkownicy')
    uzytkownik.is_superuser = not uzytkownik.is_superuser
    uzytkownik.is_staff = uzytkownik.is_superuser
    uzytkownik.save()
    if uzytkownik.is_superuser:
        messages.success(request, f'{uzytkownik.username} — nadano uprawnienia admina.')
    else:
        messages.info(request, f'{uzytkownik.username} — odebrano uprawnienia admina.')
    return redirect('uzytkownicy')


@login_required
def moj_tydzien(request, nauczyciel_id=None):
    """Widok tygodnia nauczyciela w stylu Calendly: 7 dni obok siebie,
    godziny dodaje się pojedynczym kliknięciem, przerwa 5/10/15/30 min pigułkami.
    Kalendarz cudzy (z id w adresie) może otworzyć TYLKO szef — osoba umawiająca
    i koordynator dostają 403 (w Uczniowie widać tylko imię + godziny pracy)."""
    if nauczyciel_id is None:
        nauczyciel_id = getattr(request.user, 'nauczyciel_id', None)
    if not nauczyciel_id:
        raise PermissionDenied
    if not czy_szef(request.user) and getattr(request.user, 'nauczyciel_id', None) != nauczyciel_id:
        raise PermissionDenied
    nauczyciel = get_object_or_404(Nauczyciel, pk=nauczyciel_id)

    dzis = date.today()
    try:
        start = date(
            int(request.GET.get('rok') or dzis.year),
            int(request.GET.get('miesiac') or dzis.month),
            int(request.GET.get('dzien') or dzis.day),
        )
    except ValueError:
        start = dzis
    # tydzień zaczyna się w poniedziałek (jak w Calendly)
    poniedzialek = start - timedelta(days=start.weekday())
    dni_tygodnia = [poniedzialek + timedelta(days=i) for i in range(7)]

    poprzedni = poniedzialek - timedelta(days=7)
    nastepny = poniedzialek + timedelta(days=7)

    dzisiaj = date.today()
    przerwy_dni = {}
    for wpis in PrzerwaDnia.objects.filter(nauczyciel=nauczyciel, data__in=dni_tygodnia):
        przerwy_dni[wpis.data] = wpis.minuty
    nazwa_nauczyciela = f'{nauczyciel.imie} {nauczyciel.nazwisko}'.strip().lower()
    lekcje_dni = {}
    for lekcja in Lekcja.objects.select_related('uczen', 'przedmiot_karta').filter(data__gte=poniedzialek, data__lt=nastepny):
        if not lekcja_jest_u_nauczyciela(lekcja, nazwa_nauczyciela):
            continue
        dzien_lekcji = timezone.localtime(lekcja.data).date()
        lekcje_dni.setdefault(dzien_lekcji, []).append(lekcja)
    dni = []
    for dzien in dni_tygodnia:
        zakresy = []
        przerwa_dnia = przerwy_dni.get(dzien, nauczyciel.przerwa or 5)
        for s, e in _zakresy_dnia(nauczyciel, dzien):
            cyk_sloty = list(Dostepnosc.objects.filter(
                nauczyciel=nauczyciel, data_od=dzien, cykliczny=True,
                godzina_od__gte=s.time(), godzina_do__lte=e.time(),
            ))
            sloty_z = _rytm_zakresu(s, e, timedelta(minutes=GODZINA_LEKCJI + przerwa_dnia))
            ostatnia = sloty_z[-1] if sloty_z else s
            zakresy.append({
                'od': s.strftime('%H:%M'), 'do': e.strftime('%H:%M'),
                'ostatnia': ostatnia.strftime('%H:%M'),
                'koniec': (ostatnia + timedelta(minutes=GODZINA_LEKCJI)).strftime('%H:%M'),
                'cykliczny': bool(cyk_sloty),
                'okres': cyk_sloty[0].cykliczny_okres if cyk_sloty else 1,
            })
        lekcje_listy = []
        for lekcja in sorted(lekcje_dni.get(dzien, []), key=lambda l: l.data):
            lekcje_listy.append({
                'id': lekcja.id,
                'uczen': f'{lekcja.uczen.imie_ucznia} {lekcja.uczen.nazwisko_rodzica}',
                'przedmiot': (lekcja.przedmiot_karta.przedmiot if lekcja.przedmiot_karta_id else lekcja.uczen.przedmiot),
                'godzina': timezone.localtime(lekcja.data).strftime('%H:%M'),
                'koniec': (timezone.localtime(lekcja.data) + timedelta(minutes=GODZINA_LEKCJI)).strftime('%H:%M'),
                'odwolana': lekcja.odwolana,
            })
        dni.append({
            'data': dzien,
            'zakresy': zakresy,
            'lekcje': lekcje_listy,
            'przerwa': przerwy_dni.get(dzien, nauczyciel.przerwa or 5),
            'dzisiaj': dzien == dzisiaj,
        })

    godziny = [f'{h:02d}:00' for h in range(6, 22)]

    return render(request, 'konta/moj_tydzien.html', {
        'czy_szef': czy_szef(request.user),
        'nauczyciel': nauczyciel,
        'link_meet': nauczyciel.link_meet or '',
        'discord_id': nauczyciel.discord_id or '',
        'dni': dni,
        'godziny': godziny,
        'poprzedni_tydzien': poprzedni,
        'nastepny_tydzien': nastepny,
    })
@login_required
def kalendarz_zbiorczy(request):
    """Zbiorczy kalendarz MIESIĘCZNY wszystkich aktywnych nauczycieli — tylko do odczytu.
    Siatka jak zwykły kalendarz (7 kolumn, dni miesiąca); w każdym kafelku dnia
    chipy z nauczycielami: szary = wolny (godziny pracy), zielony = zajęta lekcja
    (godzina, nauczyciel, uczeń, przedmiot). Kafelki mają własny suwak (nie rozciągają się).
    Strzałki ‹ › zmieniają miesiąc; klik w numer dnia rozwija pełną listę zajęć tego dnia
    (pogrupowane po korepetytorach: godzina, uczeń, przedmiot, poziom).
    Kolory: zielony = lekcja nowa/jednorazowa, jasnoniebieski = cykliczna, szary = wolny.
    Dostęp: TYLKO szef i koordynator (nauczyciel i zwykły pracownik → 403)."""
    dzis = date.today()
    try:
        rok = int(request.GET.get('rok') or dzis.year)
        miesiac = int(request.GET.get('miesiac') or dzis.month)
        if not (1 <= miesiac <= 12):
            miesiac = dzis.month
    except ValueError:
        rok, miesiac = dzis.year, dzis.month
    pierwszy = date(rok, miesiac, 1)
    if miesiac == 12:
        pierwszy_nastepnego = date(rok + 1, 1, 1)
    else:
        pierwszy_nastepnego = date(rok, miesiac + 1, 1)
    poprzedni_rok, poprzedni_miesiac = (rok - 1, 12) if miesiac == 1 else (rok, miesiac - 1)
    nastepny_rok, nastepny_miesiac = (rok + 1, 1) if miesiac == 12 else (rok, miesiac + 1)

    nauczyciele = list(
        Nauczyciel.objects.filter(aktywny=True).prefetch_related('przedmioty').order_by('nazwisko', 'imie')
    )
    nazwy = {n.id: f'{n.imie} {n.nazwisko}'.strip().lower() for n in nauczyciele}

    zakresy_dni = {}
    for termin in Dostepnosc.objects.filter(
        data_od__gte=pierwszy, data_od__lt=pierwszy_nastepnego
    ).select_related('nauczyciel').order_by('nauczyciel_id', 'data_od', 'godzina_od'):
        klucz = (termin.nauczyciel_id, termin.data_od)
        s = datetime.combine(termin.data_od, termin.godzina_od)
        e = datetime.combine(termin.data_od, termin.godzina_do)
        lista = zakresy_dni.setdefault(klucz, [])
        if lista and s <= lista[-1][1]:
            lista[-1] = (lista[-1][0], max(lista[-1][1], e))
        else:
            lista.append((s, e))

    lekcje_dni = {}
    for lekcja in Lekcja.objects.select_related('uczen', 'przedmiot_karta').filter(
        data__gte=pierwszy, data__lt=pierwszy_nastepnego, odwolana=False
    ):
        for n in nauczyciele:
            if lekcja_jest_u_nauczyciela(lekcja, nazwy[n.id]):
                dzien = timezone.localtime(lekcja.data).date()
                lekcje_dni.setdefault((n.id, dzien), []).append(lekcja)
                break

    tygodnie = []
    for tydzien in calendar.Calendar(firstweekday=0).monthdatescalendar(rok, miesiac):
        dni = []
        for dzien in tydzien:
            wpisy = []
            for n in nauczyciele:
                lekcje = sorted(lekcje_dni.get((n.id, dzien), []), key=lambda l: l.data)
                if lekcje:
                    for lekcja in lekcje:
                        start = timezone.localtime(lekcja.data)
                        wpisy.append({
                            'typ': 'lekcja',
                            'cykliczna': lekcja.uczen.zajecia_cykliczne,
                            'godzina': start.strftime('%H:%M'),
                            'tekst': (
                                f"{start.strftime('%H:%M')}–{(start + timedelta(minutes=GODZINA_LEKCJI)).strftime('%H:%M')} "
                                f"{n.imie} {n.nazwisko} · {lekcja.uczen.imie_ucznia} {lekcja.uczen.nazwisko_rodzica} · "
                                f"{lekcja.przedmiot_karta.przedmiot if lekcja.przedmiot_karta_id else lekcja.uczen.przedmiot}"
                                f" · umówił: {lekcja.uczen.kto_umowil}"
                            ),
                        })
                else:
                    zakresy = zakresy_dni.get((n.id, dzien), [])
                    if zakresy:
                        wpisy.append({
                            'typ': 'wolny',
                            'godzina': zakresy[0][0].strftime('%H:%M'),
                            'tekst': (
                                f"{n.imie} {n.nazwisko} wolny: "
                                + ", ".join(f"{s.strftime('%H:%M')}–{e.strftime('%H:%M')}" for s, e in zakresy)
                            ),
                        })
            wpisy.sort(key=lambda w: (w['godzina'], w['tekst']))
            dni.append({
                'data': dzien,
                'w_miesiacu': dzien.month == miesiac,
                'dzisiaj': dzien == dzis,
                'wpisy': wpisy,
            })
        tygodnie.append(dni)
    # kliknięty dzień — pełna lista zajęć tego dnia pogrupowana po korepetytorach
    wybrany_dzien = None
    dzien_param = request.GET.get('dzien')
    if dzien_param:
        try:
            wybrany_dzien = date(rok, miesiac, int(dzien_param))
        except ValueError:
            wybrany_dzien = None
    szczegoly = []
    if wybrany_dzien:
        for n in nauczyciele:
            nazwa = f'{n.imie} {n.nazwisko}'.strip().lower()
            lekcje_d = sorted(lekcje_dni.get((n.id, wybrany_dzien), []), key=lambda l: l.data)
            zakresy = zakresy_dni.get((n.id, wybrany_dzien), [])
            if not lekcje_d and not zakresy:
                continue
            lekcje_listy = []
            for lekcja in lekcje_d:
                start = timezone.localtime(lekcja.data)
                karta_l = lekcja.przedmiot_karta
                lekcje_listy.append({
                    'godzina': start.strftime('%H:%M'),
                    'koniec': (start + timedelta(minutes=GODZINA_LEKCJI)).strftime('%H:%M'),
                    'uczen': f'{lekcja.uczen.imie_ucznia} {lekcja.uczen.nazwisko_rodzica}',
                    'przedmiot': karta_l.przedmiot if karta_l else lekcja.uczen.przedmiot,
                    'poziom': karta_l.poziom if karta_l else lekcja.uczen.poziom,
                    'cykliczna': lekcja.uczen.zajecia_cykliczne,
                    'umowil': lekcja.uczen.kto_umowil,
                })
            szczegoly.append({
                'nauczyciel': n,
                'lekcje': lekcje_listy,
                'zakresy': [f'{s.strftime("%H:%M")}–{e.strftime("%H:%M")}' for s, e in zakresy],
            })

    # widok dnia jako SIATKA: kolumny = korepetytorzy (imię nazwisko + przedmioty),
    # wiersze = godziny 6:00–21:00; komórka = szara (wolny), zielona (lekcja jednorazowa),
    # niebieska (lekcja cykliczna); lekcja siedzi w wierszu swojej pełnej godziny
    siatka_dnia = []
    kolumny = []
    if wybrany_dzien:
        # tylko korepetytorzy, którzy tego dnia pracują (mają zaznaczony zakres lub lekcję)
        for n in nauczyciele:
            if not zakresy_dni.get((n.id, wybrany_dzien)) and not lekcje_dni.get((n.id, wybrany_dzien)):
                continue
            przedmioty = [
                {'przedmiot': p.przedmiot, 'klasa': _klasa_poziomu(p.poziom)}
                for p in sorted(n.przedmioty.all(), key=lambda p: p.przedmiot)
            ]
            kolumny.append({'n': n, 'nazwa': f'{n.imie} {n.nazwisko}'.strip(), 'przedmioty': przedmioty})
        for h in range(6, 22):
            wiersz = {'etykieta': f'{h:02d}:00', 'kolumny': []}
            for kol in kolumny:
                n = kol['n']
                kom = {'typ': '', 'tekst': '', 'cykliczna': False}
                lekcje_h = [
                    l for l in lekcje_dni.get((n.id, wybrany_dzien), [])
                    if timezone.localtime(l.data).hour == h
                ]
                if lekcje_h:
                    l = lekcje_h[0]
                    start = timezone.localtime(l.data)
                    karta = l.przedmiot_karta
                    kom = {
                        'typ': 'lekcja',
                        'cykliczna': l.uczen.zajecia_cykliczne,
                        'tekst': (
                            f'{start:%H:%M}–{(start + timedelta(minutes=GODZINA_LEKCJI)):%H:%M} · z {l.uczen.imie_ucznia} {l.uczen.nazwisko_rodzica}'
                            f' — {karta.przedmiot if karta else l.uczen.przedmiot} ({karta.poziom if karta else l.uczen.poziom})'
                            f' · umówił: {l.uczen.kto_umowil}'
                        ),
                    }
                else:
                    h_dt = datetime.combine(wybrany_dzien, time(h, 0))
                    if any(st <= h_dt < e for st, e in zakresy_dni.get((n.id, wybrany_dzien), [])):
                        kom = {'typ': 'wolny', 'tekst': '', 'cykliczna': False}  # szara komórka zakresu, bez napisu
                wiersz['kolumny'].append(kom)
            siatka_dnia.append(wiersz)
    poprzedni_dzien = nastepny_dzien = None
    if wybrany_dzien:
        poprzedni_dzien = wybrany_dzien - timedelta(days=1)
        nastepny_dzien = wybrany_dzien + timedelta(days=1)
    return render(request, 'konta/kalendarz_zbiorczy.html', {
        'tygodnie': tygodnie,
        'wybrany_dzien': wybrany_dzien,
        'szczegoly': szczegoly,
        'siatka_dnia': siatka_dnia,
        'kolumny': kolumny,
        'poprzedni_dzien': poprzedni_dzien,
        'nastepny_dzien': nastepny_dzien,
        'dni_nazwy': ['poniedziałek', 'wtorek', 'środa', 'czwartek', 'piątek', 'sobota', 'niedziela'],
        'nazwa_miesiaca': MIESIACE[miesiac - 1],
        'rok': rok,
        'miesiac': miesiac,
        'poprzedni_rok': poprzedni_rok,
        'poprzedni_miesiac': poprzedni_miesiac,
        'nastepny_rok': nastepny_rok,
        'nastepny_miesiac': nastepny_miesiac,
    })





def odbiorcy_meet(uczen, adresat='oba'):
    """Zwraca adresy e-mail rodzica — ZAWSZE oba (główny i dodatkowy),
    pomija puste adresy i duplikaty. Adresat z formularza nie jest już wybierany."""
    adresy = []
    if uczen.email_glowny:
        adresy.append(uczen.email_glowny)
    if uczen.email_dodatkowy and uczen.email_dodatkowy not in adresy:
        adresy.append(uczen.email_dodatkowy)
    return adresy


def wyslij_link_meet_uczniowi(request, uczen):
    """Wysyła rodzicowi maila z linkiem Google Meet ucznia (jeśli jest)."""
    odbiorcy = odbiorcy_meet(uczen)
    if not uczen.link_meet or not odbiorcy:
        return
    tresc = (
        f'Dzień dobry,\n\n'
        f'link do zajęć z {uczen.korepetytor} dla {uczen.imie_ucznia} '
        f'(przedmiot: {uczen.przedmiot}):\n\n'
        f'{uczen.link_meet}\n\n'
        f'1. lekcja: {uczen.pierwsza_lekcja:%d.%m.%Y o godz. %H:%M}\n\n'
        'Link jest stały — używaj go przy każdej lekcji.\n\n'
        f'Pozdrawiamy,\n{uczen.kto_umowil}\n'
    )
    try:
        send_mail(
            f'Link do zajęć — {uczen.imie_ucznia} ({uczen.przedmiot})',
            tresc,
            None,
            odbiorcy,
        )
    except Exception as e:
        zapisz_blad_maila('link_do_zajec_rodzic', e)

def wyslij_potwierdzenie_rodzicowi(request, uczen, karta=None, lekcja=None):
    """Wysyła rodzicowi potwierdzenie umówienia: przedmiot, poziom, korepetytor,
    1. lekcja i link Meet. Dla przedmiotu DODATKOWEGO (karta) bierze dane z karty —
    w mailu jest wtedy faktycznie umówiony przedmiot (np. angielski), nie główny.
    Odpowiedź na maila trafia do osoby, która umówiła ucznia."""
    odbiorcy = odbiorcy_meet(uczen)
    if not odbiorcy:
        return
    if karta:
        przedmiot = karta.przedmiot
        poziom = karta.poziom
        korepetytor = karta.korepetytor
        link_meet = karta.link_meet or uczen.link_meet
        pierwsza = timezone.localtime(lekcja.data) if lekcja else timezone.localtime(uczen.pierwsza_lekcja)
    else:
        przedmiot = uczen.przedmiot
        poziom = uczen.poziom
        korepetytor = uczen.korepetytor
        link_meet = uczen.link_meet
        pierwsza = timezone.localtime(uczen.pierwsza_lekcja)
    tresc = (
        'Dzień dobry,\n\n'
        'Dziękuję za umówienie się na korepetycje 😊\n\n'
        f'Przedmiot: {przedmiot}\n'
        f'Poziom: {poziom}\n'
        f'Korepetytor: {korepetytor}\n'
        f'Pierwsza lekcja: {pierwsza:%d.%m.%Y o godz. %H:%M}\n'
    )
    if link_meet:
        tresc += f'\nLink do zajęć online:\n{link_meet}\n'
    else:
        tresc += '\nLink do zajęć online otrzymacie osobno, gdy będzie dostępny.\n'
    tresc += (
        '\nW razie pytań proszę o wiadomość na messengerze.\n\n'
        f'Pozdrawiam,\n{uczen.kto_umowil}\n'
    )
    from django.utils.html import escape
    if link_meet:
        link_html = f'<p>Link do zajęć online:<br><a href="{escape(link_meet)}">{escape(link_meet)}</a></p>'
    else:
        link_html = '<p>Link do zajęć online otrzymacie osobno, gdy będzie dostępny.</p>'
    tresc_html = (
        '<p>Dzień dobry,</p>'
        '<p>Dziękuję za umówienie się na korepetycje 😊</p>'
        f'<p><strong>Przedmiot: {escape(przedmiot)}</strong><br>'
        f'<strong>Poziom: {escape(poziom)}</strong><br>'
        f'<strong>Korepetytor: {escape(korepetytor)}</strong><br>'
        f'<strong>Pierwsza lekcja: {pierwsza:%d.%m.%Y o godz. %H:%M}</strong></p>'
        + link_html
        + '<p>W razie pytań proszę o wiadomość na messengerze.</p>'
        + f'<p>Pozdrawiam,<br>{escape(uczen.kto_umowil)}</p>'
    )
    tworca_email = getattr(uczen.umowil, 'email', '') or ''
    try:
        from django.core.mail import EmailMultiAlternatives
        mail = EmailMultiAlternatives(
            subject=f'Umówienie na korepetycje — {uczen.imie_ucznia} ({przedmiot})',
            body=tresc,
            to=odbiorcy,
        )
        mail.attach_alternative(tresc_html, 'text/html')
        if tworca_email:
            mail.reply_to = [tworca_email]
        mail.send()
    except Exception as e:
        zapisz_blad_maila('potwierdzenie_rodzica', e)

@login_required
def umow_lekcje_cykliczna(request, uczen_id):
    """Umawia stałe lekcje ucznia: wiele slotów w tygodniu, presety okresu,
    blokada terminów u korepetytora i e-mail z linkiem Meet do rodzica.
    Przełącznik przedmiotu: główny (bez karta) lub dodatkowy (?karta=ID)."""
    uczen = get_object_or_404(Uczen, pk=uczen_id)
    if not czy_szef(request.user) and uczen.umowil_id != request.user.id:
        raise PermissionDenied

    # aktywny przedmiot: główny lub dodatkowy (karta); przy POST bierzemy wybór
    # z formularza (radio name="karta"), a nie ze starego ?karta= w adresie
    karta = None
    if request.method == 'POST' and 'karta' in request.POST:
        karta_id = request.POST.get('karta', '')
    else:
        karta_id = request.GET.get('karta', '')
    if karta_id:
        karta = uczen.przedmioty_ucznia.filter(pk=karta_id).first()
        if not karta:
            raise PermissionDenied

    # nauczyciel i link Meet bieżącego przedmiotu
    nauczyciel = nauczyciel_z_korepetytora(karta.korepetytor if karta else uczen.korepetytor)

    def okres_dat(okres, od_dnia):
        """Zwraca (od, do) wg wybranego presetu.
        'zawsze' celowo usunięte — lekcje cykliczne zawsze mają koniec
        (miesiąc / do końca roku szkolnego)."""

        if okres == 'miesiac':
            if od_dnia.month == 12:
                do = date(od_dnia.year, 12, 31)
            else:
                nast = date(od_dnia.year, od_dnia.month + 1, 1)
                do = nast - timedelta(days=1)
            return od_dnia, do
        if od_dnia.month >= 7:
            rok = od_dnia.year + 1
        else:
            rok = od_dnia.year
        return od_dnia, date(rok, 6, 30)

    if request.method == 'POST':
        okres = request.POST.get('okres', 'miesiac')
        co_2_tygodnie = False # opcja „co 2 tygodnie” usunięta całkowicie — lekcje cykliczne zawsze co tydzień
        link_meet = request.POST.get('link_meet', '').strip()
        try:
            data_poczatkowa = date.fromisoformat(request.POST.get('data_od', '') or request.POST.get('data_poczatkowa', ''))
        except ValueError:
            data_poczatkowa = date.today()
        try:
            data_koncowa = date.fromisoformat(request.POST.get('data_do', ''))
        except ValueError:
            data_koncowa = None
        if data_koncowa and data_koncowa < data_poczatkowa:
            messages.error(request, 'Data „do” nie może być wcześniejsza niż data „od”.')
            return redirect('lekcja_cykliczna', uczen_id=uczen.id)
        if data_koncowa:
            od_dnia, do_dnia = data_poczatkowa, data_koncowa
        else:
            od_dnia, do_dnia = okres_dat(okres, data_poczatkowa)
        sloty_raw = request.POST.getlist('slot_dzien')
        godziny_raw = request.POST.getlist('slot_godzina')
        sloty = []
        for idx, dzien_txt in enumerate(sloty_raw):
            if not dzien_txt:
                continue
            godzina_txt = godziny_raw[idx] if idx < len(godziny_raw) else ''
            if not godzina_txt:
                continue
            sloty.append((int(dzien_txt), godzina_txt))
        if not sloty:
            messages.error(request, 'Dodaj min. jeden termin (dzień tygodnia i godzina).')
            return redirect('lekcja_cykliczna', uczen_id=uczen.id)

        # EDYCJA: formularz pokazuje wszystkie obecne sloty ucznia — każda przyszła
        # lekcja tego przedmiotu, której slot (dzień tygodnia + godzina) NIE jest
        # w nowej liście, zostaje ODWOŁANA (nie kasowana — zostaje w historii)
        nowe_sloty = {(dzien_tygodnia, godzina_txt) for dzien_tygodnia, godzina_txt in sloty}
        qs_stare = Lekcja.objects.filter(uczen=uczen, odwolana=False, data__gte=timezone.now())
        if karta:
            qs_stare = qs_stare.filter(przedmiot_karta=karta)
        else:
            qs_stare = qs_stare.filter(przedmiot_karta__isnull=True)
        odwolane = 0
        for stara in qs_stare:
            lok = timezone.localtime(stara.data)
            if (lok.weekday(), lok.strftime('%H:%M')) not in nowe_sloty:
                stara.odwolana = True
                stara.save(update_fields=['odwolana'])
                odwolane += 1
        utworzone = 0
        pominiete_konflikt = 0

        pominiete_poza_grafikiem = 0
        for dzien_tygodnia, godzina_txt in sloty:
            try:
                godzina = datetime.strptime(godzina_txt, '%H:%M').time()
            except ValueError:
                continue
            dzien = od_dnia
            while dzien <= do_dnia:
                if dzien.weekday() == dzien_tygodnia:
                    data_lekcji = timezone.make_aware(datetime.combine(dzien, godzina))
                    start_dt = datetime.combine(dzien, godzina)
                    if nauczyciel and godzina_txt not in wolne_godziny_nauczyciela(nauczyciel, dzien, pomijaj_uczen_id=uczen.id):
                        if kolizja_lekcji_nauczyciela(nauczyciel, dzien, start_dt, pomijaj_uczen_id=uczen.id):
                            pominiete_konflikt += 1

                        else:
                            pominiete_poza_grafikiem += 1
                    else:
                        if karta:
                            _, stworzona = Lekcja.objects.get_or_create(uczen=uczen, data=data_lekcji, przedmiot_karta=karta)
                        else:
                            _, stworzona = Lekcja.objects.get_or_create(uczen=uczen, data=data_lekcji, przedmiot_karta__isnull=True)
                        if stworzona:
                            utworzone += 1
                        if nauczyciel:
                            Dostepnosc.objects.get_or_create(
                                nauczyciel=nauczyciel,
                                data_od=dzien,
                                data_do=dzien,
                                godzina_od=godzina,
                                godzina_do=(datetime.combine(date(2000, 1, 1), godzina) + timedelta(minutes=GODZINA_LEKCJI)).time(),
                            )

                dzien += timedelta(days=1)
        if pominiete_konflikt or pominiete_poza_grafikiem:
            komunikaty = []
            if pominiete_konflikt:
                komunikaty.append(f'{pominiete_konflikt} terminów z konfliktem (nauczyciel ma już wtedy lekcję)')
            if pominiete_poza_grafikiem: komunikaty.append(f'{pominiete_poza_grafikiem} terminów poza godzinami pracy korepetytora')

            if utworzone == 0:
                messages.error(
                    request,
                    'Nie wpisałem żadnego terminu — ' + ' oraz '.join(komunikaty)
                    + '. Sprawdź dostępność korepetytora w „Mój tydzień" (dni, w których pracuje).'
                    + (f' Odwołano {odwolane} starych terminów.' if odwolane else ''),
                )
            else:
                messages.warning(
                    request,
                    f'Pominięto: ' + ' oraz '.join(komunikaty) + '. '
                                        f'Wpisano {utworzone} terminów do {do_dnia:%d.%m.%Y}.'
                    + (f' Odwołano {odwolane} starych terminów.' if odwolane else ''),
                )
        else:
            messages.success(
                request,
                f'Stałe lekcje umówione — wpisano {utworzone} terminów do {do_dnia:%d.%m.%Y}. '
                'Korepetytor ma zablokowane godziny w kalendarzu.'
                + (f' Odwołano {odwolane} starych terminów.' if odwolane else ''),
            )
        # flaga "zajęcia cykliczne" dotyczy ucznia (niezależnie od przedmiotu)
        if utworzone and not uczen.zajecia_cykliczne:
            uczen.zajecia_cykliczne = True
            uczen.save(update_fields=['zajecia_cykliczne'])
        # link Meet: dla dodatkowego przedmiotu zapisujemy na jego karcie,
        # dla głównego — na karcie ucznia (jak dotychczas)
        if karta:
            if link_meet:
                karta.link_meet = link_meet
                karta.save()
        elif link_meet:
            uczen.link_meet = link_meet
            uczen.save()
        else:
            if not uczen.pk or uczen.zajecia_cykliczne:
                uczen.save(update_fields=['zajecia_cykliczne'])
        biezacy_link = karta.link_meet if karta else uczen.link_meet
        if biezacy_link:
            messages.info(
                request,
                'Link Meet zapisany — rodzic dostanie go automatycznie w przypomnieniu '
                '60 minut przed każdą lekcją.',
            )
        return redirect('lekcje')

    # EDYCJA: formularz podstawia obecne przyszłe sloty ucznia na tym przedmiocie
    # (dzień tygodnia + godzina), żeby można było zmienić godzinę zamiast dokładać
    qs_sloty = Lekcja.objects.filter(uczen=uczen, odwolana=False, data__gte=timezone.now())
    if karta:
        qs_sloty = qs_sloty.filter(przedmiot_karta=karta)
    else:
        qs_sloty = qs_sloty.filter(przedmiot_karta__isnull=True)
    seen_sloty = set()
    istniejace_sloty = []
    for l in qs_sloty.order_by('data'):
        lok = timezone.localtime(l.data)
        klucz = (lok.weekday(), lok.strftime('%H:%M'))
        if klucz not in seen_sloty:
            seen_sloty.add(klucz)
            istniejace_sloty.append({'dzien': lok.weekday(), 'godzina': lok.strftime('%H:%M')})
    istniejace_sloty.sort(key=lambda sl: (sl['dzien'], sl['godzina']))    
    return render(request, 'konta/lekcja_cykliczna.html', {
        'uczen': uczen,
        'karta': karta,
        'przedmioty_ucznia': list(uczen.przedmioty_ucznia.all()),
        'biezacy_link_meet': (karta.link_meet if karta else uczen.link_meet) or '',
        'godziny': [f'{h:02d}:00' for h in range(6, 24)],
        'dzisiaj': date.today().isoformat(),
        'domyslny_koniec': (date.today() + timedelta(days=92)).isoformat(),
        'nauczyciel_id': nauczyciel.id if nauczyciel else '',      
        'dni_tygodnia': [
            (0, 'poniedziałek'), (1, 'wtorek'), (2, 'środa'), (3, 'czwartek'),
            (4, 'piątek'), (5, 'sobota'), (6, 'niedziela'),
        ],
        'wiersze_slotow': istniejace_sloty or [None],
    })

KATEGORIE_ZAZALEN = ['aplikacja', 'zajęcia', 'pracownik', 'pomysły']

import json as _json
from django.http import JsonResponse as _JsonResponse
from django.views.decorators.csrf import csrf_exempt as _csrf_exempt


def _ip_z_zadania(request):
    """IP klienta (pierwszy z X-Forwarded-For, gdy PA podstawia proxy)."""
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


@_csrf_exempt
def api_contact(request):
    """Formularz ze strony publicznej (strona POSTuje JSON na /api/contact).
    Wysyła maila na biuro (EMAILE_POWIADOMIEN) i/lub powiadomienie na DISCORD_WEBHOOK_URL.
    Zabezpieczenia antyspamowe: limit 10 KB body, rate-limit 5 zgłoszeń/godz. na IP,
    honeypot (pole „website”), walidacja pól. Boty dostają udawany sukces {'ok': true}."""
    import os as _os
    if request.method != 'POST':
        return _JsonResponse({'ok': False, 'error': 'Tylko POST.'}, status=405)
    if int(request.headers.get('Content-Length') or 0) > 10_240:
        return _JsonResponse({'ok': False, 'error': 'Za duże zapytanie.'}, status=413)
    try:
        dane = _json.loads(request.body.decode('utf-8') or '{}')
        if not isinstance(dane, dict):
            dane = {}
    except ValueError:
        dane = request.POST.dict()

    def _pole(nazwa, maks):
        return str(dane.get(nazwa, '') or '')[:maks].replace('\r', ' ').replace('\n', ' ').strip()

    # honeypot: wypełnione „website” = bot -> udajemy sukces, nic nie wysyłamy
    if dane.get('website'):
        return _JsonResponse({'ok': True})
    # rate-limit per IP: max 5 zgłoszeń na godzinę
    klucz = f'api_contact:{_ip_z_zadania(request)}'
    if not cache.add(klucz, 1, timeout=3600):
        try:
            cache.incr(klucz)
        except ValueError:
            cache.set(klucz, 1, timeout=3600)
    if (cache.get(klucz) or 0) > 5:
        return _JsonResponse({'ok': True})  # udajemy sukces, żeby bot nie wiedział

    name = _pole('name', 100)
    subject = _pole('subject', 200)
    level = _pole('level', 100)
    goal = _pole('goal', 500)
    phone = _pole('phone', 20)
    email = _pole('email', 254)
    slowa_imienia = name.split()
    litery_slowa = [len(re.sub(r'[^A-Za-ząćęłńóśźżĄĆĘŁŃÓŚŹŻ]', '', s)) for s in slowa_imienia]
    if len(slowa_imienia) < 2 or any(l < 2 for l in litery_slowa):
        return _JsonResponse({'ok': False, 'error': 'Podaj imię i nazwisko.'}, status=400)
    if phone and not re.fullmatch(r'[0-9+\- ]{5,20}', phone):
        return _JsonResponse({'ok': False, 'error': 'Nieprawidłowy numer telefonu.'}, status=400)
    if email:
        from django.core.validators import validate_email
        try:
            validate_email(email)
        except Exception:
            return _JsonResponse({'ok': False, 'error': 'Nieprawidłowy adres e-mail.'}, status=400)
    tresc = (
        f"Imię i nazwisko: {name}\n"
        f"Przedmiot: {subject}\n"
        f"Poziom/klasa: {level}\n"
        f"Cel nauki: {goal}\n"
        f"Telefon: {phone}\n"
        f"E-mail: {email}\n"
    )
    ok = False
    odbiorcy = list(getattr(settings, 'EMAILE_POWIADOMIEN', []))
    if odbiorcy:
        try:
            send_mail('Nowe zgłoszenie ze strony www', tresc, None, odbiorcy)
            ok = True
        except Exception as e:
            zapisz_blad_maila('api_contact', e)
    webhook = _os.environ.get('DISCORD_WEBHOOK_URL', '')
    if webhook:
        try:
            import urllib.request as _urllib
            zadanie = _urllib.Request(
                webhook,
                data=_json.dumps({'content': '**Nowe zgłoszenie ze strony www**\n' + tresc}).encode('utf-8'),
                headers={'Content-Type': 'application/json', 'User-Agent': 'NowoczesneKorepetycje-Zgloszenia/1.0'},
                method='POST',
            )
            with _urllib.urlopen(zadanie, timeout=10) as odp:
                if odp.status in (200, 204):
                    ok = True
        except Exception as e:
            zapisz_blad_maila('api_contact_webhook', e)
    if ok:
        return _JsonResponse({'ok': True})
    return _JsonResponse({'ok': False, 'error': 'Nie udało się wysłać zgłoszenia.'}, status=500)
STRONY_CMS = [
    ('home', 'Strona główna'), ('cennik', 'Cennik'), ('o-nas', 'O nas'), ('kontakt', 'Kontakt'),
    ('jak-uczymy', 'Jak uczymy'), ('zajecia', 'Zajęcia'), ('start', 'Start'),
    ('dziekujemy', 'Dziękujemy'), ('regulamin', 'Regulamin'),
    ('polityka-prywatnosci', 'Polityka prywatności'), ('polityka-cookies', 'Polityka cookies'),
    ('matematyka', 'Przedmiot: Matematyka'), ('angielski', 'Przedmiot: Angielski'),
    ('hiszpanski', 'Przedmiot: Hiszpański'), ('biologia', 'Przedmiot: Biologia'),
    ('chemia', 'Przedmiot: Chemia'), ('fizyka', 'Przedmiot: Fizyka'),
    ('polski', 'Przedmiot: Polski'), ('geografia', 'Przedmiot: Geography'),
    ('niemiecki', 'Przedmiot: Język niemiecki'), ('informatyka', 'Przedmiot: Informatyka'),
    ('filozofia', 'Przedmiot: Filozofia'),
]


def _domyslny_content():
    return {
        'copy': {'home-headline': '', 'home-intro': ''},
        'stats': {'tutors': 50, 'students': 550, 'retention': 93},
        'images': {},
        'prices': {
            'matematyka': [90, 120, 170], 'angielski': [90, 120, 170, 200], 'hiszpanski': [90, 120],
            'biologia': [90, 120], 'chemia': [90, 120], 'fizyka': [90], 'polski': [90, 120, 170],
            'geografia': [90, 120], 'niemiecki': [90, 120], 'informatyka': [90, 120],
            'filozofia': [90, 120, 170],
        },
        'subjects': [], 'pages': [], 'reviews': [],
    }


def _sloty_dostepne(slug):
    """Sloty data-nk-slot z HTML podstrony: {klucz: {text, section}} — section = najbliższy nagłówek („gdzie to jest”)."""
    import re as _re
    katalog = getattr(settings, 'NK_STRONA_DIR', '/home/NowoczesneKorepetycje/strona')
    pliki = [katalog + '/index.html'] if slug == 'home' else [f'{katalog}/{slug}/index.html']
    sloty = {}
    for p in pliki:
        try:
            html = open(p, encoding='utf-8').read()
        except Exception:
            continue
        section = ''
        for m in _re.finditer(r'<(h[1-6])[^>]*>([^<]{1,120})</h[1-6]>|data-nk-slot="([^"]+)"[^>]*>([^<]{1,300})', html):
            if m.group(1):
                section = m.group(2).strip()
            else:
                sloty[m.group(3)] = {'text': m.group(4).strip(), 'section': section}
    return sloty


def _content_ze_salda():
    import os as _os
    plik = getattr(settings, 'NK_CONTENT_JSON', '/home/NowoczesneKorepetycje/strona/content.json')
    try:
        with open(plik, encoding='utf-8') as f:
            dane = _json.load(f)
        if isinstance(dane, dict):
            return dane
    except Exception:
        pass
    return _domyslny_content()


def api_content(request):
    """Kontent CMS dla strony (cms.js woła /api/content)."""
    return _JsonResponse(_content_ze_salda())


@login_required
def cms_strony(request):
    """Stary CMS wyłączony — treścią strony zarządza się teraz w nowym panelu /cms/ (SSO)."""
    return redirect('/cms/')

@login_required
def zazalenie(request):
    if request.method == 'POST':
        kategoria = request.POST.get('kategoria', '')
        notatka = request.POST.get('notatka', '').strip()
        if kategoria in KATEGORIE_ZAZALEN and notatka:
            Zgloszenie.objects.create(
                autor=request.user,
                kategoria=kategoria,
                tresc=notatka,
                strona=request.POST.get('strona', ''),
            )
            messages.success(request, 'Zgłoszenie zapisane.')
        else:
            messages.error(request, 'Wybierz kategorię i napisz treść zgłoszenia.')
        ref = request.META.get('HTTP_REFERER') or ''
        wlasna = f'{request.scheme}://{request.get_host()}'
        return redirect(ref if ref.startswith(wlasna) else 'pulpit')
    return redirect('pulpit')
@login_required
def zgloszenia(request):
    """Zakładka Zgłoszenia — TYLKO szef/koordynator. Aktywne / zrealizowane (do końca miesiąca)."""
    if not (czy_szef(request.user) or czy_koordynator(request.user)):
        raise PermissionDenied
    if request.method == 'POST':
        zg = get_object_or_404(Zgloszenie, pk=request.POST.get('zgloszenie_id'))
        if 'zrealizuj' in request.POST:
            zg.zrealizowane = True
            zg.zrealizowane_kiedy = timezone.now()
            zg.zrealizowane_przez = request.user
            zg.save(update_fields=['zrealizowane', 'zrealizowane_kiedy', 'zrealizowane_przez'])
            messages.success(request, 'Zgłoszenie oznaczone jako zrealizowane.')
        elif 'przywroc' in request.POST:
            zg.zrealizowane = False
            zg.zrealizowane_kiedy = None
            zg.zrealizowane_przez = None
            zg.save(update_fields=['zrealizowane', 'zrealizowane_kiedy', 'zrealizowane_przez'])
            messages.success(request, 'Zgłoszenie wróciło do aktywnych.')
        return redirect('zgloszenia')
    dzis = timezone.localdate()
    granica = date(dzis.year, dzis.month, 1)
    return render(request, 'konta/zgloszenia.html', {
        'aktywne': Zgloszenie.objects.filter(zrealizowane=False).select_related('autor'),
        'zrealizowane': Zgloszenie.objects.filter(zrealizowane=True, zrealizowane_kiedy__date__gte=granica).select_related('autor', 'zrealizowane_przez'),
    })

def _zastosuj_zgloszenie(zg, post=None):
    """Stosuje do kalendarzy akcję zapisaną w zgłoszeniu kategorii 'zajęcia'
    (ta sama logika co akcje szefa w kalendarz_dane). Zwraca (ok, komunikat)."""
    dane = zg.dane or {}
    akcja = dane.get('akcja')
    nauczyciel = Nauczyciel.objects.filter(pk=dane.get('nauczyciel_id')).first()
    if not nauczyciel:
        return False, 'Brak karty korepetytora powiązanego ze zgłoszeniem.'
    nazwa_n = f'{nauczyciel.imie} {nauczyciel.nazwisko}'.strip().lower()
    try:
        dzien_data = date.fromisoformat(dane.get('dzien', ''))
    except ValueError:
        return False, 'Nieprawidłowa data w zgłoszeniu.'
    if akcja == 'odwolaj_lekcje':
        lekcja = Lekcja.objects.filter(pk=dane.get('lekcja_id')).first()
        if not lekcja or not lekcja_jest_u_nauczyciela(lekcja, nazwa_n):
            return False, 'Nie znaleziono lekcji do odwołania.'
        lekcja.odwolana = True
        lekcja.save(update_fields=['odwolana'])
        return True, (f'Odwołano zajęcia {timezone.localtime(lekcja.data):%d.%m.%Y %H:%M} '
                      f'(uczeń: {lekcja.uczen}) — widoczne we wszystkich kalendarzach.')
    if akcja == 'przesun_lekcje':
        lekcja = Lekcja.objects.filter(pk=dane.get('lekcja_id')).first()
        if not lekcja or not lekcja_jest_u_nauczyciela(lekcja, nazwa_n):
            return False, 'Nie znaleziono lekcji do przesunięcia.'
        post = post or {}
        try:
            nowy_dzien = date.fromisoformat(post.get('nowy_dzien', ''))
            nowa_godzina = datetime.strptime(post.get('nowa_godzina', ''), '%H:%M').time()
        except ValueError:
            return False, 'Wybierz nowy dzień i godzinę.'
        stara = timezone.localtime(lekcja.data)
        pon = stara.date() - timedelta(days=stara.date().weekday())
        if not (pon <= nowy_dzien <= pon + timedelta(days=6)):
            return False, 'Nowy termin musi być w tym samym tygodniu co lekcja.'
        if nowy_dzien == stara.date() and nowa_godzina == stara.time():
            return False, 'To już jest ten termin.'
        if nowa_godzina.strftime('%H:%M') not in wolne_godziny_nauczyciela(nauczyciel, nowy_dzien, pomijaj_uczen_id=lekcja.uczen_id):
            return False, f'Korepetytor nie ma wolnej godziny {nowy_dzien:%d.%m} o {nowa_godzina:%H:%M}.'
        lekcja.data = timezone.make_aware(datetime.combine(nowy_dzien, nowa_godzina))
        lekcja.save(update_fields=['data'])
        return True, (f'Przesunięto zajęcia: {stara:%d.%m.%Y %H:%M} → {nowy_dzien:%d.%m.%Y} {nowa_godzina:%H:%M} '
                      '— weszło do wszystkich kalendarzy.')
    if akcja in ('usun_zakres', 'usun_caly_zakres', 'wyczysc_dzien'):
        try:
            start = datetime.strptime(dane.get('od') or '00:00', '%H:%M').time()
            koniec = datetime.strptime(dane.get('do') or '23:59', '%H:%M').time()
        except ValueError:
            return False, 'Nieprawidłowy zakres godzin w zgłoszeniu.'
        q = Lekcja.objects.filter(odwolana=False)
        if akcja == 'usun_caly_zakres':
            q = q.filter(data__date__gte=dzien_data)
        else:
            q = q.filter(data__date=dzien_data)
        lekcje = [
            l for l in q.select_related('uczen', 'przedmiot_karta')
            if lekcja_jest_u_nauczyciela(l, nazwa_n)
            and (akcja == 'wyczysc_dzien' or start <= timezone.localtime(l.data).time() < koniec)
        ]
        # zajęcia w zakresie odwołujemy, żeby zniknęły ze wszystkich kalendarzy
        for l in lekcje:
            l.odwolana = True
            l.save(update_fields=['odwolana'])
        dostep_q = Dostepnosc.objects.filter(nauczyciel=nauczyciel)
        if akcja == 'usun_caly_zakres':
            dostep_q = dostep_q.filter(data_od__gte=dzien_data, godzina_od__gte=start, godzina_do__lte=koniec)
        elif akcja == 'usun_zakres':
            dostep_q = dostep_q.filter(data_od=dzien_data, godzina_od__gte=start, godzina_do__lte=koniec)
        else:
            dostep_q = dostep_q.filter(data_od=dzien_data)
        usuniete = dostep_q.delete()[0]
        teksty = {
            'usun_zakres': f'usunięto zakres {start:%H:%M}–{koniec:%H:%M} tylko dnia {dzien_data:%d.%m.%Y}',
            'usun_caly_zakres': f'usunięto CAŁY zakres {start:%H:%M}–{koniec:%H:%M} od {dzien_data:%d.%m.%Y} w przód',
            'wyczysc_dzien': f'wyczyszczono cały dzień {dzien_data:%d.%m.%Y}',
        }
        return True, (f'{teksty[akcja]} ({usuniete} terminów); odwołano {len(lekcje)} zajęć w tym zakresie '
                      '— weszło do wszystkich kalendarzy.')
    return False, f'Nieznana akcja zgłoszenia: {akcja}'


@login_required
def rozpatrz_zgloszenie(request, zgloszenie_id):
    """Rozpatrywanie zgłoszenia kategorii 'zajęcia' — TYLKO szef/koordynator.
    GET: szczegóły zgłoszenia + formularz (dla przesunięcia: wybór nowego dnia/godziny).
    POST 'zatwierdz': stosuje akcję do wszystkich kalendarzy i zamyka zgłoszenie.
    POST 'odrzuc': zamyka zgłoszenie bez wprowadzania zmian."""
    if not (czy_szef(request.user) or czy_koordynator(request.user)):
        raise PermissionDenied
    zg = get_object_or_404(Zgloszenie.objects.select_related('autor'), pk=zgloszenie_id)
    dane = zg.dane or {}
    nauczyciel = Nauczyciel.objects.filter(pk=dane.get('nauczyciel_id')).first()
    lekcja = None
    if dane.get('lekcja_id'):
        lekcja = Lekcja.objects.filter(pk=dane['lekcja_id']).select_related('uczen', 'przedmiot_karta').first()
    dni_tygodnia = []
    wolne_per_dzien = {}
    if lekcja and nauczyciel and dane.get('akcja') == 'przesun_lekcje':
        stara = timezone.localtime(lekcja.data)
        pon = stara.date() - timedelta(days=stara.date().weekday())
        for i in range(7):
            d = pon + timedelta(days=i)
            dni_tygodnia.append(d)
            wolne_per_dzien[d.isoformat()] = wolne_godziny_nauczyciela(nauczyciel, d, pomijaj_uczen_id=lekcja.uczen_id)
    if request.method == 'POST' and not zg.zrealizowane:
        if 'zatwierdz' in request.POST:
            ok, komunikat = _zastosuj_zgloszenie(zg, request.POST)
            if not ok:
                messages.error(request, komunikat)
                return redirect('rozpatrz_zgloszenie', zgloszenie_id=zg.id)
            zg.zrealizowane = True
            zg.zrealizowane_kiedy = timezone.now()
            zg.zrealizowane_przez = request.user
            zg.save(update_fields=['zrealizowane', 'zrealizowane_kiedy', 'zrealizowane_przez'])
            messages.success(request, f'Zatwierdzono zgłoszenie. {komunikat}')
            return redirect('zgloszenia')
        if 'odrzuc' in request.POST:
            kto = request.user.get_full_name() or request.user.username
            zg.tresc += f' [ODRZUCONE {timezone.now():%d.%m.%Y %H:%M} przez {kto}]'
            zg.zrealizowane = True
            zg.zrealizowane_kiedy = timezone.now()
            zg.zrealizowane_przez = request.user
            zg.save(update_fields=['tresc', 'zrealizowane', 'zrealizowane_kiedy', 'zrealizowane_przez'])
            messages.info(request, 'Zgłoszenie odrzucone — zmiany NIE weszły do kalendarzy.')
            return redirect('zgloszenia')
        return redirect('rozpatrz_zgloszenie', zgloszenie_id=zg.id)
    return render(request, 'konta/rozpatrz_zgloszenia.html', {
        'zg': zg,
        'dane': dane,
        'nauczyciel': nauczyciel,
        'lekcja': lekcja,
        'dni_tygodnia': dni_tygodnia,
        'wolne_json': json.dumps(wolne_per_dzien),
        'czy_szef': czy_szef(request.user),
    })

@login_required
def zmiana_hasla(request):
    """Pozwola zalogowanemu pracownikowi zmienić własne hasło."""
    form = FormularzZmianyHasla(request.user, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Hasło zostało zmienione.')
        return redirect('pulpit')
    return render(request, 'konta/zmiana_hasla.html', {'form': form})


def brak_dostepu(request, exception=None):
    """Strona 403 — brak uprawnień."""
    return render(request, 'konta/brak_dostepu.html', status=403)