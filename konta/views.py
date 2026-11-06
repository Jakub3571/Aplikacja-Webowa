import calendar
from datetime import date, datetime, time, timedelta
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.views import PasswordResetConfirmView
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import (
    FormularzDyspozycyjnosc,
    FormularzLogowania,
    FormularzPrzedmiotuUcznia,
    FormularzUcznia,
    FormularzUzytkownika,
    FormularzZmianyHasla,
    FormularzLekcjiCyklicznej,
)
from .models import Dostepnosc, Lekcja, Nauczyciel, Pracownik, PrzedmiotUcznia, PrzerwaDnia, PrzypomnienieLekcji, RaportPlatnosci, Uczen

MIESIACE = [
    'styczeń', 'luty', 'marzec', 'kwiecień', 'maj', 'czerwiec',
    'lipiec', 'sierpień', 'wrzesień', 'październik', 'listopad', 'grudzień',
]

DNI_TYGODNIA = ['Pn', 'Wt', 'Śr', 'Cz', 'Pt', 'So', 'Nd']

GODZINA_LEKCJI = 60

MAX_PROB_LOGOWANIA = 5


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


def kolizja_lekcji_nauczyciela(nauczyciel, dzien, start_lekcji, przerwa_min=None):
    """Czy lekcja (60 min od start_lekcji) koliduje z istniejącą lekcją nauczyciela.

    Lekcja efektywnie zajmuje [start, koniec + przerwa) — dwie lekcje kolidują,
    gdy te przedziały zachodzą na siebie, czyli między lekcjami musi zmieścić się
    PEŁNA przerwa. Uwzględnia wszystkie przedmioty nauczyciela, także lekcje
    TEGO SAMEGO ucznia — dlatego po lekcji 18:00 nie da się ustawić 18:30.
    """
    if not nauczyciel:
        return False
    if przerwa_min is None:
        przerwa_min = nauczyciel.przerwa or 5
    nazwa_nauczyciela = f'{nauczyciel.imie} {nauczyciel.nazwisko}'.strip().lower()
    moj_koniec = start_lekcji + timedelta(minutes=GODZINA_LEKCJI + przerwa_min)
    for lekcja in Lekcja.objects.select_related('uczen', 'przedmiot_karta').filter(data__date=dzien, odwolana=False):
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
    for termin in Dostepnosc.objects.filter(nauczyciel=nauczyciel, data_od=dzien).order_by('godzina_od'):
        s = datetime.combine(dzien, termin.godzina_od)
        e = datetime.combine(dzien, termin.godzina_do)
        if zakresy and s <= zakresy[-1][1]:
            zakresy[-1] = (zakresy[-1][0], max(zakresy[-1][1], e))
        else:
            zakresy.append((s, e))
    return zakresy


def nauczyciel_wolny_o(nauczyciel, dzien, start_lekcji):
    """Czy nauczyciel może przyjąć lekcję 60 min od start_lekcji — dokładnie te same
    godziny, które zwraca wolne_godziny_nauczyciela (ta sama siatka, kolizje i przerwy).
    Dzięki temu lista rozwijana i walidacja formularza zawsze pokazują to samo."""
    if not nauczyciel:
        return False
    return start_lekcji.strftime('%H:%M') in wolne_godziny_nauczyciela(nauczyciel, dzien)


def wolne_godziny_nauczyciela(nauczyciel, dzien):
    """Godziny startu lekcji dostępne u nauczyciela w danym dniu.

    Siatka rytmu: od początku każdego zaznaczonego przedziału co (60 min lekcji
    + przerwa nauczyciela), o ile lekcja mieści się w przedziale — np. przedział
    11:00–19:00 przy przerwie 5 min daje 11:00, 12:05, 13:10, ...; osobne
    przedziały dają własne początki (6:05, 7:15, ...).
    Dodatkowo łańcuchy wokół zajętych lekcji: przed lekcją można wstawić slot
    kończący się z przerwą tuż przed nią (np. 12:55 przed 14:00), a po lekcji
    pierwszy slot to koniec + przerwa (np. 15:05 po 14:00), dalej rytmem.
    Wszystko przechodzi jeszcze przez kolizje (lekcja zajmuje [start, koniec +
    przerwa)) i okno przerwy dnia. Używane wszędzie, gdzie wybiera się godzinę:
    Krok 4 formularza ucznia, kreator cykliczny, dodawanie przedmiotu — i przez
    walidację (nauczyciel_wolny_o)."""
    wynik = []
    if not nauczyciel:
        return wynik
    przerwa = nauczyciel.przerwa or 5
    krok = timedelta(minutes=GODZINA_LEKCJI + przerwa)
    nazwa_nauczyciela = f'{nauczyciel.imie} {nauczyciel.nazwisko}'.strip().lower()
    lekcje_dnia = [
        timezone.localtime(l.data).replace(tzinfo=None)
        for l in Lekcja.objects.select_related('uczen', 'przedmiot_karta').filter(data__date=dzien, odwolana=False)
        if lekcja_jest_u_nauczyciela(l, nazwa_nauczyciela)
    ]
    for s, e in _zakresy_dnia(nauczyciel, dzien):
        kandydaci = set()
        t = s
        while t + timedelta(minutes=GODZINA_LEKCJI) <= e:
            kandydaci.add(t)
            t += krok
        for l_start in lekcje_dnia:
            l_koniec = l_start + timedelta(minutes=GODZINA_LEKCJI)
            przed = l_start - krok
            if s <= przed and przed + timedelta(minutes=GODZINA_LEKCJI) <= e:
                kandydaci.add(przed)
            t = l_koniec + timedelta(minutes=przerwa)
            while t + timedelta(minutes=GODZINA_LEKCJI) <= e:
                kandydaci.add(t)
                t += krok
        for start in sorted(kandydaci):
            if kolizja_lekcji_nauczyciela(nauczyciel, dzien, start, przerwa_min=przerwa):
                continue
            if godzina_w_przerwie(nauczyciel, dzien, start.time()):
                continue
            wynik.append(start.strftime('%H:%M'))
    return sorted(set(wynik))


def godzina_w_przerwie(nauczyciel, dzien, godzina_start, uczen_id=None):
    """Sprawdza, czy slot (60 min) wypada w luce przerwy dnia nauczyciela.
    Przerwa to luka między blokami zajęć wyliczana wg planu dnia."""
    if not nauczyciel:
        return False
    wpis = PrzerwaDnia.objects.filter(nauczyciel=nauczyciel, data=dzien).first()
    if not wpis or not wpis.minuty:
        return False
    minuty = wpis.minuty

    zaznaczone = set()
    for termin in Dostepnosc.objects.filter(nauczyciel=nauczyciel, data_od=dzien):
        start = datetime.combine(dzien, termin.godzina_od)
        koniec = datetime.combine(dzien, termin.godzina_do)
        while start < koniec:
            zaznaczone.add(start.strftime('%H:%M'))
            start += timedelta(minutes=30)
    if not zaznaczone:
        return False

    sloty = sorted(zaznaczone)
    bloki = []
    i = 0
    while i < len(sloty):
        start_bloku = datetime.strptime(sloty[i], '%H:%M')
        dlugosc = 30
        while (
            i + 1 < len(sloty)
            and (start_bloku + timedelta(minutes=dlugosc)).strftime('%H:%M') == sloty[i + 1]
            and dlugosc < 60
        ):
            dlugosc += 30
            i += 1
        bloki.append((start_bloku, dlugosc))
        i += 1

    przesuniecie = 0
    przerwy = []
    for numer, (start_bloku, dlugosc) in enumerate(bloki, start=1):
        start = start_bloku + timedelta(minutes=przesuniecie)
        if numer > 1:
            przerwy.append((start - timedelta(minutes=minuty), start))
        przesuniecie += wpis.minuty

    start_slotu = datetime.combine(date(1900, 1, 1), godzina_start)
    for poczatek, koniec in przerwy:
        if poczatek <= start_slotu < koniec:
            return True
    return False


def logowanie(request):
    """Wyświetla formularz logowania i loguje pracownika po poprawnych danych.
    Po 5 nieudanych próbach z rzędu blokuje logowanie do wygaśnięcia sesji
    (ochrona przed zgadywaniem haseł)."""
    if request.user.is_authenticated:
        return redirect('pulpit')
    if request.method == 'POST':
        if request.session.get('proby_logowania', 0) >= MAX_PROB_LOGOWANIA:
            messages.error(
                request,
                'Zbyt wiele nieudanych prób logowania — odczekaj ok. 10 minut i spróbuj ponownie.',
            )
            return render(request, 'konta/logowanie.html', {'form': FormularzLogowania(request)})
        request.session['proby_logowania'] = request.session.get('proby_logowania', 0) + 1
    form = FormularzLogowania(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.get_user()
        login(request, user)
        request.session['proby_logowania'] = 0
        messages.success(request, f'Witaj, {user.get_full_name() or user.username}!')
        return redirect('pulpit')
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


def dostepnosci_nauczycieli():
    """Lista wszystkich nauczycieli z pogrupowanymi ich dostępnościami."""
    dzis = date.today()
    terminy = (
        Dostepnosc.objects
        .filter(data_do__gte=dzis)
        .select_related('nauczyciel')
        .order_by('data_od', 'godzina_od')
    )
    zgrupowane = {}
    for termin in terminy:
        klucz = termin.nauczyciel_id
        if klucz not in zgrupowane:
            zgrupowane[klucz] = {
                'nauczyciel': termin.nauczyciel,
                'dni': set(),
                'godzina_od': termin.godzina_od,
                'godzina_do': termin.godzina_do,
            }
        wpis = zgrupowane[klucz]
        wpis['dni'].add(termin.data_od)
        # dni tygodnia dla czytelności (np. pon, wt, śr)
        wpis.setdefault('dni_tygodnia', set()).add(termin.data_od.weekday())
        if termin.godzina_od < wpis['godzina_od']:
            wpis['godzina_od'] = termin.godzina_od
        if termin.godzina_do > wpis['godzina_do']:
            wpis['godzina_do'] = termin.godzina_do
    wynik = []
    for nauczyciel in (
        Nauczyciel.objects
        .prefetch_related('przedmioty')
        .filter(aktywny=True)
        .order_by('nazwisko', 'imie')
    ):
        wpis = zgrupowane.get(nauczyciel.id)
        wynik.append({
            'nauczyciel': nauczyciel,
            'dni': sorted(wpis['dni']) if wpis else [],
            'dni_tygodnia': sorted(wpis['dni_tygodnia']) if wpis and 'dni_tygodnia' in wpis else [],
            'godzina_od': wpis['godzina_od'] if wpis else None,
            'godzina_do': wpis['godzina_do'] if wpis else None,
        })
    return wynik


@login_required
def korepetytorzy_dane(request):
    """AJAX: zwraca listę aktywnych korepetytorów dla przedmiotu i poziomu.
    Gdy podano datę i godzinę 1. lekcji — tylko tych, którzy zaznaczyli ten termin w kalendarzu."""
    if getattr(request.user, 'nauczyciel_id', None):
        return JsonResponse({'blad': 'Brak uprawnień.'}, status=403)
    przedmiot = request.GET.get('przedmiot', '')
    poziom = request.GET.get('poziom', '')
    data_txt = request.GET.get('data', '')
    godzina_txt = request.GET.get('godzina', '')
    # tryb wolnych godzin JEDNEGO nauczyciela w konkretnym dniu (kreator lekcji cyklicznej)
    nauczyciel_id = request.GET.get('nauczyciel', '')
    if nauczyciel_id and data_txt:
        try:
            dzien_tmp = date.fromisoformat(data_txt)
        except ValueError:
            dzien_tmp = None
        if dzien_tmp:
            nt = Nauczyciel.objects.filter(pk=nauczyciel_id, aktywny=True).first()
            if nt:
                return JsonResponse({
                    'godziny': [{'godzina': h} for h in wolne_godziny_nauczyciela(nt, dzien_tmp)]
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
        korepetytorzy.append({'nazwa': f'{n.imie} {n.nazwisko}', 'id': n.id})
    return JsonResponse({'korepetytorzy': korepetytorzy})
@login_required
def uczniowie(request):
    """Zakładka uczniowie: lista, formularz dodawania i dostępność nauczycieli.
    Zwykły pracownik widzi tylko uczniów, których sam umówił.
    Konto nauczyciela nie ma tu wstępu (widzi tylko swój kalendarz)."""
    if getattr(request.user, 'nauczyciel_id', None):
        raise PermissionDenied
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
            'nadchodzace_dostepnosci': dostepnosci_nauczycieli(),
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
            elif godzina_w_przerwie(nauczyciel, start_dt.date(), start_dt.time()):
                powod = 'wypada w przerwie dnia korepetytora'
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
                'nadchodzace_dostepnosci': dostepnosci_nauczycieli(),
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
        wyslij_potwierdzenie_rodzicowi(request, uczen)
        messages.info(
            request,
            'Do rodzica wysłano potwierdzenie zapisu: termin 1. lekcji'
            + (' i link Meet.' if uczen.link_meet else ' (link Meet poleci osobno, gdy nauczyciel go zapisze).'),
        )
        return redirect('uczniowie')

    return render(request, 'konta/uczniowie.html', {
        'form': form,
        'uczniowie': uczniowie_lista,
        'nadchodzace_dostepnosci': dostepnosci_nauczycieli(),
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
                if nauczyciel:
                    data_aware = timezone.make_aware(start_lekcji)
                    Lekcja.objects.get_or_create(uczen=uczen, data=data_aware, przedmiot_karta=karta)
                    Dostepnosc.objects.get_or_create(
                        nauczyciel=nauczyciel,
                        data_od=data_lekcji,
                        data_do=data_lekcji,
                        godzina_od=czas,
                        godzina_do=(datetime.combine(date(2000, 1, 1), czas) + timedelta(minutes=GODZINA_LEKCJI)).time(),
                    )
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

    korepetytorzy_lista = list(Nauczyciel.objects.order_by('nazwisko', 'imie'))
    pracownicy_lista = [
        u for u in Pracownik.objects.order_by('username')
        if not u.nauczyciel
        and (u.stanowisko or '').strip().lower() != 'koordynator'
    ]
    koordynatorzy_lista = [
        u for u in Pracownik.objects.order_by('username')
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
    """Archiwizuje ucznia: znika z list bieżących, ale zostaje w archiwum (dane i lekcje nietknięte)."""
    uczen = get_object_or_404(Uczen, pk=uczen_id)
    if not czy_szef(request.user) and uczen.umowil_id != request.user.id:
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
    Szef i koordynator (edycja danych), ale bez zmiany roli/uprawnień."""
    if not (czy_szef(request.user) or czy_koordynator(request.user)):
        raise PermissionDenied
    # koordynator nie edytuje kont adminów
    uzytkownik = get_object_or_404(Pracownik, pk=user_id)
    if not czy_szef(request.user) and uzytkownik.is_superuser:
        raise PermissionDenied
    if request.method == 'POST':
        uzytkownik.first_name = request.POST.get('first_name', '').strip()
        uzytkownik.last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()
        # e-mail opcjonalny: puste pole = zostaje obecny adres
        if email:
            uzytkownik.email = email
        uzytkownik.telefon = request.POST.get('telefon', '').strip()
        uzytkownik.save()
        # korepetytor: synchronizujemy e-mail na karcie nauczyciela
        if uzytkownik.nauczyciel:
            uzytkownik.nauczyciel.email = uzytkownik.email
            uzytkownik.nauczyciel.save()
        messages.success(request, f'Dane konta {uzytkownik.username} zostały zaktualizowane.')
        return redirect('uzytkownicy')
    return render(request, 'konta/edytuj_uzytkownika.html', {'uzytkownik': uzytkownik})


@login_required
@tylko_szef
def przelacz_konto(request, user_id):
    """Włącza/wyłącza konto: wyłączone konto nie może się logować (dane zostają w bazie)."""
    uzytkownik = get_object_or_404(Pracownik, pk=user_id)
    if uzytkownik.id == request.user.id:
        messages.error(request, 'Nie możesz wyłączyć konta samemu sobie.')
        return redirect('uzytkownicy')
    uzytkownik.is_active = not uzytkownik.is_active
    uzytkownik.save()
    if uzytkownik.is_active:
        messages.success(request, f'Konto {uzytkownik.username} zostało włączone.')
    else:
        messages.info(request, f'Konto {uzytkownik.username} zostało wyłączone — nie może się logować.')
    return redirect('uzytkownicy')

@login_required
@tylko_szef
def usun_konto(request, user_id):
    """Usuwa konto pracownika NA ZAWSZE — wraz z kartą nauczyciela (jeśli jest).
    Dostępne tylko dla szefa/admina. Nie można usunąć własnego konta."""
    uzytkownik = get_object_or_404(Pracownik, pk=user_id)
    if uzytkownik.id == request.user.id:
        messages.error(request, 'Nie możesz usunąć własnego konta.')
        return redirect('uzytkownicy')
    if request.method == 'POST':
        login = uzytkownik.username
        nauczyciel = uzytkownik.nauczyciel
        uzytkownik.delete()
        # karta nauczyciela: usuwamy razem z kontem (przedmioty, stawki,
        # zaznaczone godziny znikną w kaskadzie) — żeby nie zostały "duchy"
        if nauczyciel:
            nauczyciel.delete()
        messages.success(request, f'Konto {login} zostało usunięte na stałe.')
        return redirect('uzytkownicy')
    return render(request, 'konta/usun_konto.html', {'uzytkownik': uzytkownik})

@login_required
def pobierz_raport(request):
    """Raport zapłaconych płatności jako plik tekstowy do pobrania (zamiast e-maila).
    Szef i koordynator."""
    if not (czy_szef(request.user) or czy_koordynator(request.user)):
        raise PermissionDenied
    zaplaconi = Uczen.objects.filter(zaplacone=True, zakonczona=False)
    linie = ['Raport zapłaconych płatności', '=' * 40, '']
    for u in zaplaconi:
        linie.append(
            f'{u.imie_ucznia} {u.nazwisko_rodzica} — korepetytor: {u.korepetytor} '
            f'— przedmiot: {u.przedmiot} — wpisał: {u.kto_umowil} '
            f'— 1. lekcja: {u.pierwsza_lekcja:%d.%m.%Y %H:%M}'
        )
    linie.append('')
    linie.append(f'Razem zapłaconych płatności: {len(zaplaconi)}.')
    tresc = '\n'.join(linie)
    odpowiedz = HttpResponse(tresc, content_type='text/plain; charset=utf-8')
    odpowiedz['Content-Disposition'] = 'attachment; filename="raport_platnosci.txt"'
    return odpowiedz


def reset_hasla(request):
    """Reset hasła z limitem prób: max 5 żądań na sesję (ochrona przed spamowaniem maili).
    Celowo bez @login_required — z tego widoku korzystają osoby niezalogowane."""
    from django.contrib.auth.views import PasswordResetView
    if request.method == 'POST':
        if request.session.get('proby_resetu', 0) >= 5:
            messages.error(
                request,
                'Zbyt wiele prób resetu hasła — odczekaj ok. 10 minut i spróbuj ponownie.',
            )
            return redirect('logowanie')
        request.session['proby_resetu'] = request.session.get('proby_resetu', 0) + 1
    return PasswordResetView.as_view(
        template_name='konta/reset_hasla.html',
        subject_template_name='konta/mail_reset_temat.txt',
        email_template_name='konta/mail_reset_tresc.txt',
        success_url='/reset-hasla/wyslane/',
    )(request)

def _link_przeterminowany(uidb64, token):
    """True, gdy link był prawidłowy, ale minęły 3 dni (PASSWORD_RESET_TIMEOUT).
    Fałszywy link zwraca False — wtedy pokazuje się zwykła strona błędu."""
    from django.conf import settings as ustawienia
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.crypto import constant_time_compare
    from django.utils.http import base36_to_int, urlsafe_base64_decode
    try:
        uid = urlsafe_base64_decode(uidb64).decode()
        uzytkownik = Pracownik.objects.get(pk=uid)
        gen = default_token_generator
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
    przekierowuje na osobną stronę „czas minął” zamiast pustego formularza."""

    def dispatch(self, request, *args, **kwargs):
        uidb64 = kwargs.get('uidb64')
        token = kwargs.get('token')
        if uidb64 and token and _link_przeterminowany(uidb64, token):
            return redirect('reset_hasla_wygasl')
        return super().dispatch(request, *args, **kwargs)


def reset_hasla_wygasl(request):
    """Strona „czas minął” — link do ustawienia hasła był ważny 3 dni i wygasł."""
    return render(request, 'konta/reset_wygasl.html')

@login_required
def kalendarz_nauczyciela(request, nauczyciel_id):
    """Stary widok podgladu — przekierowanie do widoku tygodnia (styl Calendly).
    Admin/pracownik widzi dokladnie ten sam kalendarz co nauczyciel."""
    nauczyciel_zalogowany = getattr(request.user, 'nauczyciel_id', None)
    if nauczyciel_zalogowany and nauczyciel_zalogowany != nauczyciel_id:
        raise PermissionDenied
    return redirect('moj_tydzien_nauczyciela', nauczyciel_id=nauczyciel_id)


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
            komunikat = f'Godzina {etykieta} — odznaczono.'
        else:
            Dostepnosc.objects.create(
                nauczyciel=nauczyciel,
                data_od=dzien_data,
                data_do=dzien_data,
                godzina_od=start,
                godzina_do=koniec,
            )
            komunikat = f'Godzina {etykieta} — oznaczono jako dostępna.'
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
        kursor = datetime.combine(date(2000, 1, 1), start)
        koniec_dt = datetime.combine(date(2000, 1, 1), koniec_zakresu)
        while kursor < koniec_dt:
            slot_start = kursor.time()
            slot_koniec = (kursor + timedelta(minutes=30)).time()
            if odznaczyc:
                Dostepnosc.objects.filter(
                    nauczyciel=nauczyciel,
                    data_od=dzien_data,
                    godzina_od=slot_start,
                    godzina_do=slot_koniec,
                ).delete()
            else:
                Dostepnosc.objects.get_or_create(
                    nauczyciel=nauczyciel,
                    data_od=dzien_data,
                    data_do=dzien_data,
                    godzina_od=slot_start,
                    godzina_do=slot_koniec,
                )
            kursor += timedelta(minutes=30)
        komunikat = (
            f'Godziny {start:%H:%M}–{koniec_zakresu:%H:%M} — odznaczono.'
            if odznaczyc else
            f'Godziny {start:%H:%M}–{koniec_zakresu:%H:%M} — oznaczono jako dostępne.'
        )
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
        for slot in zrodlo_sloty:
            Dostepnosc.objects.create(
                nauczyciel=nauczyciel,
                data_od=dzien_data,
                data_do=dzien_data,
                godzina_od=slot.godzina_od,
                godzina_do=slot.godzina_do,
            )
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
    elif akcja == 'usun_zakres':
        try:
            start = datetime.strptime(request.POST.get('od', ''), '%H:%M').time()
            koniec_zakresu = datetime.strptime(request.POST.get('do', ''), '%H:%M').time()
        except ValueError:
            return JsonResponse({'blad': 'Nieprawidłowy zakres godzin.'}, status=400)
        Dostepnosc.objects.filter(
            nauczyciel=nauczyciel,
            data_od=dzien_data,
            godzina_od__gte=start,
            godzina_do__lte=koniec_zakresu,
        ).delete()
        komunikat = f'Godziny {start:%H:%M}–{koniec_zakresu:%H:%M} usunięto.'
    elif akcja == 'wyczysc_dzien':
        usuniete = Dostepnosc.objects.filter(nauczyciel=nauczyciel, data_od=dzien_data).count()
        Dostepnosc.objects.filter(nauczyciel=nauczyciel, data_od=dzien_data).delete()
        komunikat = f'Wyczyszczono cały dzień {dzien_data:%d.%m.%Y} — usunięto {usuniete} terminów.'
    elif akcja == 'powtorz_tygodniowo':
        zrodlo_sloty = list(
            Dostepnosc.objects.filter(nauczyciel=nauczyciel, data_od=dzien_data)
        )
        if not zrodlo_sloty:
            return JsonResponse({'blad': 'Ten dzień nie ma żadnych godzin — najpierw wpisz plan.'}, status=400)
        zrodlo_przerwa = PrzerwaDnia.objects.filter(nauczyciel=nauczyciel, data=dzien_data).first()
        # koniec roku szkolnego: 30 czerwca (styczeń–czerwiec = bieżący rok, lipiec–grudzień = następny rok)
        if dzien_data.month >= 7:
            ostatni_dzien = date(dzien_data.year + 1, 6, 30)
        else:
            ostatni_dzien = date(dzien_data.year, 6, 30)
        docelowy = dzien_data + timedelta(days=7)
        utworzone = 0
        while docelowy <= ostatni_dzien:
            for slot in zrodlo_sloty:
                Dostepnosc.objects.get_or_create(
                    nauczyciel=nauczyciel,
                    data_od=docelowy,
                    data_do=docelowy,
                    godzina_od=slot.godzina_od,
                    godzina_do=slot.godzina_do,
                )
                utworzone += 1
            if zrodlo_przerwa:
                PrzerwaDnia.objects.update_or_create(
                    nauczyciel=nauczyciel,
                    data=docelowy,
                    defaults={'minuty': zrodlo_przerwa.minuty},
                )
            docelowy += timedelta(days=7)
        komunikat = f'Plan powtórzono co tydzień do {ostatni_dzien:%d.%m.%Y} ({utworzone} terminów).'
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

    plan_dnia, sloty_przerwy = [], set()
    sloty = sorted(zaznaczone)
    if sloty:
        bloki = []
        i = 0
        while i < len(sloty):
            start_bloku = datetime.strptime(sloty[i], '%H:%M')
            dlugosc = 30
            while (
                i + 1 < len(sloty)
                and (start_bloku + timedelta(minutes=dlugosc)).strftime('%H:%M') == sloty[i + 1]
                and dlugosc < 60
            ):
                dlugosc += 30
                i += 1
            bloki.append((start_bloku, dlugosc))
            i += 1
        przesuniecie = 0
        for numer, (start_bloku, dlugosc) in enumerate(bloki, start=1):
            start = start_bloku + timedelta(minutes=przesuniecie)
            koniec = start + timedelta(minutes=dlugosc)
            if numer > 1 and przerwa_min:
                poczatek_przerwy = start - timedelta(minutes=przerwa_min)
                plan_dnia.append({
                    'typ': 'przerwa',
                    'start': poczatek_przerwy.strftime('%H:%M'),
                    'koniec': start.strftime('%H:%M'),
                })
                kursor = poczatek_przerwy.replace(minute=(poczatek_przerwy.minute // 30) * 30)
                while kursor < start:
                    sloty_przerwy.add(kursor.strftime('%H:%M'))
                    kursor += timedelta(minutes=30)
            plan_dnia.append({
                'typ': 'lekcja',
                'numer': numer,
                'start': start.strftime('%H:%M'),
                'koniec': koniec.strftime('%H:%M'),
            })
            przesuniecie += przerwa_min
    zajete = set()
    nazwa_nauczyciela_dane = f'{nauczyciel.imie} {nauczyciel.nazwisko}'.strip().lower()
    for lekcja in Lekcja.objects.select_related('uczen', 'przedmiot_karta').filter(data__date=dzien_data, odwolana=False):
        if not lekcja_jest_u_nauczyciela(lekcja, nazwa_nauczyciela_dane):
            continue
        start = timezone.localtime(lekcja.data).replace(tzinfo=None)
        for minuty in range(0, GODZINA_LEKCJI, 30):
            zajete.add((start + timedelta(minutes=minuty)).strftime('%H:%M'))
    kafelki = []
    for h in range(6, 21):
        for m in (0, 30):
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
def usun_termin(request, termin_id):
    """Usuwa wpisaną godzinę pracy i wraca do tego samego dnia kalendarza."""
    termin = get_object_or_404(Dostepnosc, pk=termin_id)
    if not czy_szef(request.user):
        if getattr(request.user, 'nauczyciel_id', None) != termin.nauczyciel_id:
            raise PermissionDenied
    nauczyciel_id = termin.nauczyciel.id
    rok = request.GET.get('rok') or date.today().year
    miesiac = request.GET.get('miesiac') or date.today().month
    dzien = termin.data_od.day
    termin.delete()
    messages.success(request, 'Godziny zostały usunięte.')
    return redirect(
        reverse('moj_tydzien_nauczyciela', args=[nauczyciel_id])
        + f'?rok={rok}&miesiac={miesiac}&dzien={dzien}'
    )


@login_required
def lekcje(request):
    """Zakładka lekcje: lista uczniów z liczbą lekcji i wejściem do kalendarza lekcji.
    Admin i koordynator widzą wszystkich, pracownik — swoich,
    korepetytor — uczniów przypisanych do niego (wg pola korepetytor)."""
    nauczyciel_id = getattr(request.user, 'nauczyciel_id', None)
    if nauczyciel_id:
        nauczyciel = get_object_or_404(Nauczyciel, pk=nauczyciel_id)
        nazwa_nauczyciela = f'{nauczyciel.imie} {nauczyciel.nazwisko}'.strip().lower()
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
    return render(request, 'konta/lekcje.html', {
        'uczniowie': uczniowie_lista,
        'uczniowie_cykliczni': uczniowie_cykliczni,
        'uczniowie_pierwsze': uczniowie_pierwsze,
        'fraza': (request.GET.get('szukaj') or '').strip(),
        'czy_szef': czy_szef(request.user),
        'jest_nauczycielem': bool(getattr(request.user, 'nauczyciel_id', None)),
    })


@login_required
def kalendarz_lekcji(request, uczen_id):
    """Kalendarz lekcji ucznia: miesiąc, dni i kafelki godzin do wpisywania lekcji.
    Przełącznik przedmiotów: główny przedmiot (bez ?karta=) lub dodatkowy (?karta=ID).
    Korepetytor widzi kalendarze swoich uczniów (główny przedmiot wg pola korepetytor,
    dodatkowe — wg kart przedmiotów)."""
    uczen = get_object_or_404(Uczen, pk=uczen_id)

    # aktywny przedmiot: brak ?karta= -> główny; ?karta=ID -> dodatkowy przedmiot
    karta = None
    karta_id = request.GET.get('karta') or request.POST.get('karta')
    if karta_id:
        karta = uczen.przedmioty_ucznia.filter(pk=karta_id).first()
        if not karta:
            raise PermissionDenied

    nazwa_glowna = f'{uczen.korepetytor}'.strip().lower()
    nazwy_dodatkowe = [(k.korepetytor or '').strip().lower() for k in uczen.przedmioty_ucznia.all()]

    nauczyciel_id = getattr(request.user, 'nauczyciel_id', None)
    if nauczyciel_id:
        nauczyciel = get_object_or_404(Nauczyciel, pk=nauczyciel_id)
        nazwa_nauczyciela = f'{nauczyciel.imie} {nauczyciel.nazwisko}'.strip().lower()
        if nazwa_nauczyciela != nazwa_glowna and nazwa_nauczyciela not in nazwy_dodatkowe:
            raise PermissionDenied
    elif not czy_szef(request.user) and not czy_koordynator(request.user) and uczen.umowil_id != request.user.id:
        raise PermissionDenied

    dzisiaj = date.today()

    rok = request.GET.get('rok')
    miesiac = request.GET.get('miesiac')
    dzien = request.GET.get('dzien')
    try:
        rok = int(rok) if rok else dzisiaj.year
        miesiac = int(miesiac) if miesiac else dzisiaj.month
        if not (1 <= miesiac <= 12):
            miesiac = dzisiaj.month
    except ValueError:
        rok, miesiac = dzisiaj.year, dzisiaj.month

    wybrany_dzien = None
    if dzien:
        try:
            wybrany_dzien = date(rok, miesiac, int(dzien))
        except ValueError:
            wybrany_dzien = None

    # pomocniczy filtr lekcji bieżącego przedmiotu
    if karta:
        filtr_karta = {'przedmiot_karta': karta}
        filtr_karta_get = {'przedmiot_karta': karta}
    else:
        filtr_karta = {'przedmiot_karta__isnull': True}
        filtr_karta_get = {'przedmiot_karta__isnull': True}

    lekcje_miesiaca = Lekcja.objects.filter(
        uczen=uczen,
        data__year=rok,
        data__month=miesiac,
        **filtr_karta_get,
    )
    dni_z_lekcjami = set(timezone.localtime(l.data).date() for l in lekcje_miesiaca)

    kalendarz_miesiaca = calendar.Calendar(firstweekday=0).monthdatescalendar(rok, miesiac)
    tygodnie = []
    for tydzien in kalendarz_miesiaca:
        dni = []
        for dzien_miesiaca in tydzien:
            dni.append({
                'data': dzien_miesiaca,
                'w_tym_miesiacu': dzien_miesiaca.month == miesiac,
                'ma_lekcje': dzien_miesiaca in dni_z_lekcjami,
                'wybrany': wybrany_dzien == dzien_miesiaca,
            })
        tygodnie.append(dni)

    poprzedni_miesiac = miesiac - 1 if miesiac > 1 else 12
    poprzedni_rok = rok if miesiac > 1 else rok - 1
    nastepny_miesiac = miesiac + 1 if miesiac < 12 else 1
    nastepny_rok = rok if miesiac < 12 else rok + 1

    # nauczyciel bieżącego przedmiotu (głównego lub z karty dodatkowej)
    nauczyciel = nauczyciel_z_korepetytora(karta.korepetytor if karta else uczen.korepetytor)

    zaznaczone = set()
    odwolane = set()
    if wybrany_dzien:
        for lekcja in Lekcja.objects.filter(uczen=uczen, data__date=wybrany_dzien, **filtr_karta_get):
            etykieta = timezone.localtime(lekcja.data).strftime('%H:%M')
            zaznaczone.add(etykieta)
            if lekcja.odwolana:
                odwolane.add(etykieta)

    zajete_przez_innych = set()
    if wybrany_dzien and nauczyciel:
        nazwa_nauczyciela_dane = f'{nauczyciel.imie} {nauczyciel.nazwisko}'.strip().lower()
        for lekcja in Lekcja.objects.select_related('uczen', 'przedmiot_karta').filter(data__date=wybrany_dzien, odwolana=False):
            if lekcja.uczen_id == uczen.id:
                continue
            if not lekcja_jest_u_nauczyciela(lekcja, nazwa_nauczyciela_dane):
                continue
            start_lekcji = timezone.localtime(lekcja.data).replace(tzinfo=None)
            for minuty in range(0, GODZINA_LEKCJI, 30):
                zajete_przez_innych.add((start_lekcji + timedelta(minutes=minuty)).strftime('%H:%M'))

    karta_param = f'&karta={karta.id}' if karta else ''

    if request.method == 'POST' and 'odwolaj_lekcje' in request.POST and wybrany_dzien:
        lekcja_id = request.POST.get('lekcja_id')
        lekcja = Lekcja.objects.filter(pk=lekcja_id, uczen=uczen, data__date=wybrany_dzien, **filtr_karta).first()
        if lekcja:
            lekcja.odwolana = True
            lekcja.save(update_fields=['odwolana'])
            if nauczyciel:
                start_czas = timezone.localtime(lekcja.data).time()
                Dostepnosc.objects.filter(
                    nauczyciel=nauczyciel,
                    data_od=wybrany_dzien,
                    godzina_od=start_czas,
                ).delete()
            messages.success(
                request,
                f'Zajęcia {lekcja.data:%H:%M} {wybrany_dzien:%d.%m.%Y} zostały odwołane.',
            )
        return redirect(
            reverse('kalendarz_lekcji', args=[uczen.id])
            + f'?rok={rok}&miesiac={miesiac}&dzien={wybrany_dzien.day}{karta_param}'
        )

    if request.method == 'POST' and 'przywroc_lekcje' in request.POST and wybrany_dzien:
        lekcja_id = request.POST.get('lekcja_id')
        lekcja = Lekcja.objects.filter(pk=lekcja_id, uczen=uczen, data__date=wybrany_dzien, **filtr_karta).first()
        if lekcja:
            start_czas = timezone.localtime(lekcja.data).time()
            start_dt = datetime.combine(wybrany_dzien, start_czas)
            if nauczyciel and kolizja_lekcji_nauczyciela(nauczyciel, wybrany_dzien, start_dt):
                messages.error(
                    request,
                    f'Nie można przywrócić zajęć {lekcja.data:%H:%M} — korepetytor ma już wtedy lekcję '
                    f'(także tego ucznia). Sprawdź kalendarz i wybierz inny termin.',
                )
            else:
                lekcja.odwolana = False
                lekcja.save(update_fields=['odwolana'])
                if nauczyciel:
                    Dostepnosc.objects.get_or_create(
                        nauczyciel=nauczyciel,
                        data_od=wybrany_dzien,
                        data_do=wybrany_dzien,
                        godzina_od=start_czas,
                        godzina_do=(
                            datetime.combine(date(2000, 1, 1), start_czas)
                            + timedelta(minutes=GODZINA_LEKCJI)
                        ).time(),
                    )
                messages.success(
                    request,
                    f'Zajęcia {lekcja.data:%H:%M} {wybrany_dzien:%d.%m.%Y} zostały przywrócone.',
                )
        return redirect(
            reverse('kalendarz_lekcji', args=[uczen.id])
            + f'?rok={rok}&miesiac={miesiac}&dzien={wybrany_dzien.day}{karta_param}'
        )

    kafelki = []
    for h in range(6, 21):
        for m in (0, 30):
            etykieta = f'{h:02d}:{m:02d}'
            kafelki.append({
                'etykieta': etykieta,
                'zaznaczony': etykieta in zaznaczone,
                'odwolany': etykieta in odwolane,
                'zajety': etykieta in zajete_przez_innych,
            })

    if request.method == 'POST' and 'przelacz_godzine' in request.POST and wybrany_dzien:
        etykieta = request.POST.get('godzina', '')
        if not etykieta:
            messages.error(request, 'Nie wybrano godziny — kliknij kafelek z godziną.')
            return redirect(
                reverse('kalendarz_lekcji', args=[uczen.id])
                + f'?rok={rok}&miesiac={miesiac}&dzien={wybrany_dzien.day}{karta_param}'
            )
        start_czas = datetime.strptime(etykieta, '%H:%M').time()
        data_lekcji = timezone.make_aware(datetime.combine(wybrany_dzien, start_czas))
        if karta:
            istniejaca = Lekcja.objects.filter(uczen=uczen, data=data_lekcji, przedmiot_karta=karta).first()
        else:
            istniejaca = Lekcja.objects.filter(uczen=uczen, data=data_lekcji, przedmiot_karta__isnull=True).first()
        if istniejaca and istniejaca.odwolana:
            messages.error(
                request,
                f'Te zajęcia ({etykieta} {wybrany_dzien:%d.%m.%Y}) są odwołane — użyj przycisku '
                f'„Przywróć”, żeby odbyły się normalnie.',
            )
        elif istniejaca:
            istniejaca.delete()
            if nauczyciel:
                Dostepnosc.objects.filter(
                    nauczyciel=nauczyciel,
                    data_od=wybrany_dzien,
                    godzina_od=start_czas,
                ).delete()
            messages.success(request, f'Lekcja {etykieta} {wybrany_dzien:%d.%m.%Y} została usunięta.')
        else:
            start_dt = datetime.combine(wybrany_dzien, start_czas)
            if nauczyciel and etykieta not in wolne_godziny_nauczyciela(nauczyciel, wybrany_dzien):
                if kolizja_lekcji_nauczyciela(nauczyciel, wybrany_dzien, start_dt):
                    messages.error(
                        request,
                        f'Nie można dodać lekcji {etykieta} — {nauczyciel.imie} {nauczyciel.nazwisko} '
                        f'ma już wtedy lekcję (także tego ucznia). Wybierz inną godzinę.',
                    )
                elif godzina_w_przerwie(nauczyciel, wybrany_dzien, start_czas):
                    messages.error(
                        request,
                        f'Nie można dodać lekcji {etykieta} — ta godzina wypada w przerwie dnia '
                        f'{nauczyciel.imie} {nauczyciel.nazwisko}. Wybierz inną godzinę.',
                    )
                else:
                    messages.error(
                        request,
                        f'Nie można dodać lekcji {etykieta} — poza godzinami pracy korepetytora '
                        f'lub niezgodnie z jego rytmem zajęć. Wybierz godzinę z listy wolnych terminów.',
                    )
            else:
                Lekcja.objects.create(uczen=uczen, przedmiot_karta=karta, data=data_lekcji)
                if nauczyciel:
                    Dostepnosc.objects.get_or_create(
                        nauczyciel=nauczyciel,
                        data_od=wybrany_dzien,
                        data_do=wybrany_dzien,
                        godzina_od=start_czas,
                        godzina_do=(datetime.combine(date(2000, 1, 1), start_czas) + timedelta(minutes=GODZINA_LEKCJI)).time(),
                    )
                messages.success(request, f'Lekcja {etykieta} {wybrany_dzien:%d.%m.%Y} została dodana.')
        return redirect(
            reverse('kalendarz_lekcji', args=[uczen.id])
            + f'?rok={rok}&miesiac={miesiac}&dzien={wybrany_dzien.day}{karta_param}'
        )
    lekcje_dnia = []
    if wybrany_dzien:
        for lekcja in Lekcja.objects.filter(uczen=uczen, data__date=wybrany_dzien, **filtr_karta).order_by('data'):
            lekcje_dnia.append({
                'id': lekcja.id,
                'godzina': timezone.localtime(lekcja.data).strftime('%H:%M'),
                'odwolana': lekcja.odwolana,
            })
    liczba_lekcji = Lekcja.objects.filter(
        uczen=uczen,
        data__year=dzisiaj.year,
        data__month=dzisiaj.month,
        **filtr_karta,
    ).count()

    return render(request, 'konta/kalendarz_lekcji.html', {
        'uczen': uczen,
        'karta': karta,
        'przedmioty_ucznia': list(uczen.przedmioty_ucznia.all()),
        'tygodnie': tygodnie,
        'dni_tygodnia': DNI_TYGODNIA,
        'nazwa_miesiaca': MIESIACE[miesiac - 1],
        'rok': rok,
        'miesiac': miesiac,
        'poprzedni_miesiac': poprzedni_miesiac,
        'poprzedni_rok': poprzedni_rok,
        'nastepny_miesiac': nastepny_miesiac,
        'nastepny_rok': nastepny_rok,
        'wybrany_dzien': wybrany_dzien,
        'kafelki': kafelki,
        'lekcje_dnia': lekcje_dnia,
        'liczba_lekcji': liczba_lekcji,
    })


@login_required
def platnosci(request):
    """Zakładka płatności: lista uczniów, statusy zapłacone i wysyłka raportu.
    Szef i koordynator (edycja i raporty, bez usuwania uczniów)."""
    if not (czy_szef(request.user) or czy_koordynator(request.user)):
        raise PermissionDenied
    uczniowie_lista = Uczen.objects.filter(zaplacone=False, zakonczona=False)
    zaplaconi = Uczen.objects.filter(zaplacone=True, zakonczona=False)
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
        Uczen.objects.filter(zaplacone=True).update(zaplacone=False)
        messages.success(
            request,
            f'Raport wysłano na {email_odbiorcy}. Rozliczone pozycje wyczyszczono z listy — '
            'uczniowie zostają w aplikacji (bez zmiany zajęć).',
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
    return render(request, 'konta/platnosci.html', {
        'uczniowie': uczniowie_lista,
        'zaplaconi': zaplaconi,
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
    lista = Pracownik.objects.all().order_by('username')
    form = FormularzUzytkownika(request.POST or None, bez_admina=not czy_szef(request.user))
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
        nowy_uzytkownik = form.zapisz()
        messages.success(request, 'Użytkownik został dodany.')

        # mail powitalny do nowego użytkownika — z Reply-To do twórcy konta
        # + jednorazowy link do ustawienia własnego hasła (jak reset hasła)
        if nowy_uzytkownik.email:
            link_logowania = request.build_absolute_uri(reverse('logowanie'))
            # link do ustawienia hasla DOKLADNIE dla tego nowego konta
            from django.contrib.auth.tokens import default_token_generator
            from django.utils.encoding import force_bytes
            from django.utils.http import urlsafe_base64_encode
            uid = urlsafe_base64_encode(force_bytes(nowy_uzytkownik.pk))
            token = default_token_generator.make_token(nowy_uzytkownik)
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


def moj_tydzien(request, nauczyciel_id=None):
    """Widok tygodnia nauczyciela w stylu Calendly: 7 dni obok siebie,
    godziny dodaje się pojedynczym kliknięciem, przerwa 5/10/15/30 min pigułkami.
    Admin/pracownik może otworzyć kalendarz dowolnego nauczyciela podając jego id
    (link z zakładki Użytkownicy/Uczniowie) — widzi wtedy dokładnie to samo co nauczyciel."""
    if nauczyciel_id is None:
        nauczyciel_id = getattr(request.user, 'nauczyciel_id', None)
    if not nauczyciel_id:
        raise PermissionDenied
    if getattr(request.user, 'nauczyciel_id', None) and request.user.nauczyciel_id != nauczyciel_id:
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
    for lekcja in Lekcja.objects.select_related('uczen', 'przedmiot_karta').filter(data__gte=poniedzialek, data__lt=nastepny, odwolana=False):
        if not lekcja_jest_u_nauczyciela(lekcja, nazwa_nauczyciela):
            continue
        dzien_lekcji = timezone.localtime(lekcja.data).date()
        lekcje_dni.setdefault(dzien_lekcji, []).append(lekcja)
    dni = []
    for dzien in dni_tygodnia:
        zakresy = [
            {'od': s.strftime('%H:%M'), 'do': e.strftime('%H:%M')}
            for s, e in _zakresy_dnia(nauczyciel, dzien)
        ]
        lekcje_listy = []
        for lekcja in sorted(lekcje_dni.get(dzien, []), key=lambda l: l.data):
            lekcje_listy.append({
                'uczen': f'{lekcja.uczen.imie_ucznia} {lekcja.uczen.nazwisko_rodzica}',
                'przedmiot': (lekcja.przedmiot_karta.przedmiot if lekcja.przedmiot_karta_id else lekcja.uczen.przedmiot),
                'godzina': timezone.localtime(lekcja.data).strftime('%H:%M'),
                'koniec': (timezone.localtime(lekcja.data) + timedelta(minutes=GODZINA_LEKCJI)).strftime('%H:%M'),
            })
        dni.append({
            'data': dzien,
            'zakresy': zakresy,
            'lekcje': lekcje_listy,
            'przerwa': przerwy_dni.get(dzien, nauczyciel.przerwa or 5),
            'dzisiaj': dzien == dzisiaj,
        })

    godziny = [f'{h:02d}:{m:02d}' for h in range(6, 22) for m in (0, 30)]

    return render(request, 'konta/moj_tydzien.html', {
        'nauczyciel': nauczyciel,
        'link_meet': nauczyciel.link_meet or '',
        'dni': dni,
        'godziny': godziny,
        'poprzedni_tydzien': poprzedni,
        'nastepny_tydzien': nastepny,
    })


@login_required
def kreator_dyspozycyjnosc(request):
    """Stary kreator formularzowy — zastąpiony widokiem tygodnia (styl Calendly)."""
    return redirect('moj_tydzien')


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

def wyslij_potwierdzenie_rodzicowi(request, uczen):
    """Wysyła rodzicowi potwierdzenie zapisu: przedmiot, korepetytor,
    data i godzina 1. lekcji oraz link Meet (jeśli nauczyciel go ma).
    Odpowiedź na maila trafia do osoby, która umówiła ucznia."""
    odbiorcy = odbiorcy_meet(uczen)
    if not odbiorcy:
        return
    tresc = (
        'Dzień dobry,\n\n'
        f'potwierdzamy zapis {uczen.imie_ucznia} na korepetycje.\n\n'
        f'Przedmiot: {uczen.przedmiot} (poziom: {uczen.poziom})\n'
        f'Korepetytor: {uczen.korepetytor}\n'
        f'Pierwsza lekcja: {uczen.pierwsza_lekcja:%d.%m.%Y o godz. %H:%M}\n'
    )
    if uczen.link_meet:
        tresc += (
            '\nLink do zajęć (stały — ten sam przy każdej lekcji):\n'
            f'{uczen.link_meet}\n'
        )
    else:
        tresc += '\nLink do zajęć online otrzymacie osobno, gdy będzie dostępny.\n'
    tresc += (
        '\nW razie pytań prosimy o odpowiedź na tego maila.\n\n'
        f'Pozdrawiamy,\n{uczen.kto_umowil}\n'
    )
    tworca_email = getattr(uczen.umowil, 'email', '') or ''
    try:
        from django.core.mail import EmailMessage
        mail = EmailMessage(
            subject=(
                f'Zapis na korepetycje — {uczen.imie_ucznia} ({uczen.przedmiot}), '
                f'1. lekcja {uczen.pierwsza_lekcja:%d.%m.%Y %H:%M}'
            ),
            body=tresc,
            to=odbiorcy,
        )
        if tworca_email:
            mail.reply_to = [tworca_email]
        mail.send()
    except Exception as e:
        zapisz_blad_maila('potwierdzenie_rodzica', e)

def wyslij_zaproszenie_meet(uczen, adresat='glowny'):
    """Wysyła e-mail do rodzica z linkiem do lekcji cyklicznej na Google Meet."""
    odbiorcy = odbiorcy_meet(uczen, adresat)
    if not uczen.link_meet or not odbiorcy:
        return
    tresc = (
        'Dzień dobry,\n\n'
        f'umawiamy cykliczne zajęcia dla {uczen.imie_ucznia} '
        f'(przedmiot: {uczen.przedmiot}, korepetytor: {uczen.korepetytor}).\n\n'
        'Zapraszamy na spotkania przez Google Meet — ponieważ zajęcia są cykliczne,\n'
        'ten link jest STAŁY i będzie obowiązywał przy każdej lekcji do końca roku szkolnego:\n\n'
        f'{uczen.link_meet}\n\n'
        'Prosimy zapisać go — to samo zaproszenie będzie używane zawsze.\n\n'
        'Pozdrawiamy,\n'
        f'{uczen.kto_umowil}\n'
    )
    try:
        send_mail(
            f'Zaproszenie na zajęcia — {uczen.imie_ucznia} (stały link Google Meet)',
            tresc,
            None,
            odbiorcy,
        )
    except Exception as e:
        zapisz_blad_maila('zaproszenie_meet_rodzic', e)

@login_required
def umow_lekcje_cykliczna(request, uczen_id):
    """Umawia stałe lekcje ucznia: wiele slotów w tygodniu, presety okresu,
    blokada terminów u korepetytora i e-mail z linkiem Meet do rodzica.
    Przełącznik przedmiotu: główny (bez karta) lub dodatkowy (?karta=ID)."""
    uczen = get_object_or_404(Uczen, pk=uczen_id)
    if not czy_szef(request.user) and uczen.umowil_id != request.user.id:
        raise PermissionDenied

    # aktywny przedmiot: główny lub dodatkowy (karta)
    karta = None
    karta_id = request.GET.get('karta') or request.POST.get('karta')
    if karta_id:
        karta = uczen.przedmioty_ucznia.filter(pk=karta_id).first()
        if not karta:
            raise PermissionDenied

    # nauczyciel i link Meet bieżącego przedmiotu
    nauczyciel = nauczyciel_z_korepetytora(karta.korepetytor if karta else uczen.korepetytor)

    def okres_dat(okres, od_dnia):
        """Zwraca (od, do) wg wybranego presetu.
        'zawsze' celowo usunięte — lekcje cykliczne zawsze mają koniec
        (miesiąc / co 2 tygodnie / do końca roku szkolnego)."""

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
        co_2_tygodnie = request.POST.get('co_2_tygodnie') == 'on'
        link_meet = request.POST.get('link_meet', '').strip()
        try:
            data_poczatkowa = date.fromisoformat(request.POST.get('data_poczatkowa', ''))
        except ValueError:
            data_poczatkowa = date.today()
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
        utworzone = 0
        pominiete_konflikt = 0
        pominiete_przerwa = 0
        pominiete_poza_grafikiem = 0
        for dzien_tygodnia, godzina_txt in sloty:
            godzina = datetime.strptime(godzina_txt, '%H:%M').time()
            dzien = od_dnia
            while dzien <= do_dnia:
                if dzien.weekday() == dzien_tygodnia and (
                    not co_2_tygodnie or (dzien - od_dnia).days % 14 == 0
                    ):
                    data_lekcji = timezone.make_aware(datetime.combine(dzien, godzina))
                    start_dt = datetime.combine(dzien, godzina)
                    if nauczyciel and godzina_txt not in wolne_godziny_nauczyciela(nauczyciel, dzien):
                        if kolizja_lekcji_nauczyciela(nauczyciel, dzien, start_dt):
                            pominiete_konflikt += 1
                        elif godzina_w_przerwie(nauczyciel, dzien, godzina):
                            pominiete_przerwa += 1
                        else:
                            pominiete_poza_grafikiem += 1
                    else:
                        if karta:
                            Lekcja.objects.get_or_create(uczen=uczen, data=data_lekcji, przedmiot_karta=karta)
                        else:
                            Lekcja.objects.get_or_create(uczen=uczen, data=data_lekcji, przedmiot_karta__isnull=True)
                        if nauczyciel:
                            Dostepnosc.objects.get_or_create(
                                nauczyciel=nauczyciel,
                                data_od=dzien,
                                data_do=dzien,
                                godzina_od=godzina,
                                godzina_do=(datetime.combine(date(2000, 1, 1), godzina) + timedelta(minutes=GODZINA_LEKCJI)).time(),
                            )
                        utworzone += 1
                dzien += timedelta(days=1)
        if pominiete_konflikt or pominiete_przerwa or pominiete_poza_grafikiem:
            komunikaty = []
            if pominiete_konflikt:
                komunikaty.append(f'{pominiete_konflikt} terminów z konfliktem (nauczyciel ma już wtedy lekcję)')
            if pominiete_poza_grafikiem: komunikaty.append(f'{pominiete_poza_grafikiem} terminów poza godzinami pracy korepetytora')
            if pominiete_przerwa:
                komunikaty.append(f'{pominiete_przerwa} terminów w przerwie dnia nauczyciela')
            messages.warning(
                request,
                f'Pominięto: ' + ' oraz '.join(komunikaty) + '. '
                f'Wpisano {utworzone} terminów do {do_dnia:%d.%m.%Y}.',
            )
        else:
            messages.success(
                request,
                f'Stałe lekcje umówione — wpisano {utworzone} terminów do {do_dnia:%d.%m.%Y}. '
                'Korepetytor ma zablokowane godziny w kalendarzu.',
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
        karta_param = f'?karta={karta.id}' if karta else ''
        return redirect(f"{reverse('kalendarz_lekcji', args=[uczen.id])}{karta_param}")
    return render(request, 'konta/lekcja_cykliczna.html', {
        'uczen': uczen,
        'karta': karta,
        'przedmioty_ucznia': list(uczen.przedmioty_ucznia.all()),
        'biezacy_link_meet': (karta.link_meet if karta else uczen.link_meet) or '',
        'godziny': [f'{h:02d}:{m:02d}' for h in range(6, 24) for m in range(0, 60, 5)],
        'dzisiaj': date.today().isoformat(),
        'nauczyciel_id': nauczyciel.id if nauczyciel else '',
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