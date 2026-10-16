import calendar
from datetime import date, datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import PermissionDenied
def moze_edytowac_kalendarz(user, nauczyciel_id):
    """Kalendarz edytuje: admin/szef albo nauczyciel-właściciel konta."""
    if czy_szef(user):
        return True
    return getattr(user, 'nauczyciel_id', None) == nauczyciel_id
def czy_szef(u):
    
    """Prawdziwy szef: superuser LUB konto o nazwie 'Admin'."""
    return bool(u.is_authenticated and (u.is_superuser or u.username.strip().lower() == 'admin'))

def tylko_szef(widok):
    """Puszcza dalej tylko superusera (szefa); reszta dostaje stronę „brak dostępu"."""
    def sprawdz(u):
        if czy_szef(u):
            return True
        raise PermissionDenied
    return user_passes_test(sprawdz, login_url='logowanie')(widok)
from django.core.mail import send_mail
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.http import HttpResponse, JsonResponse

from .forms import (
    FormularzDyspozycyjnosc,
    FormularzLogowania,
    FormularzUcznia,
    FormularzUzytkownika,
    FormularzZmianyHasla,
    FormularzLekcjiCyklicznej,
)
from .models import Dostepnosc, Lekcja, Nauczyciel, Pracownik, PrzerwaDnia, RaportPlatnosci, Uczen

MIESIACE = [
    'styczeń', 'luty', 'marzec', 'kwiecień', 'maj', 'czerwiec',
    'lipiec', 'sierpień', 'wrzesień', 'październik', 'listopad', 'grudzień',
]

DNI_TYGODNIA = ['Pn', 'Wt', 'Śr', 'Cz', 'Pt', 'So', 'Nd']

GODZINA_LEKCJI = 60


def nauczyciel_z_korepetytora(korepetytor):
    """Znajduje kartę nauczyciela po polu 'korepetytor' ucznia (np. 'Anna Kowalska')."""
    if not korepetytor or ' ' not in korepetytor:
        return None
    imie, nazwisko = korepetytor.split(' ', 1)
    return Nauczyciel.objects.filter(imie__iexact=imie.strip(), nazwisko__iexact=nazwisko.strip()).first()


def inne_lekcje_nauczyciela(nauczyciel, uczen, dzien, godzina_start):
    """Lekcje innych uczniów tego nauczyciela nachodzące na slot (60 min od godzina_start)."""
    if not nauczyciel:
        return []
    start_slotu = datetime.combine(dzien, godzina_start)
    koniec_slotu = start_slotu + timedelta(minutes=GODZINA_LEKCJI)
    nazwa_nauczyciela = f'{nauczyciel.imie} {nauczyciel.nazwisko}'.strip().lower()
    kolidujace = []
    for lekcja in Lekcja.objects.select_related('uczen').filter(data__date=dzien):
        if lekcja.uczen_id == uczen.id:
            continue
        if (lekcja.uczen.korepetytor or '').strip().lower() != nazwa_nauczyciela:
            continue
        start_lekcji = timezone.localtime(lekcja.data).replace(tzinfo=None)
        koniec_lekcji = start_lekcji + timedelta(minutes=GODZINA_LEKCJI)
        if start_slotu < koniec_lekcji and start_lekcji < koniec_slotu:
            kolidujace.append(lekcja)
    return kolidujace

def godzina_w_przerwie(nauczyciel, dzien, godzina_start, uczen_id=None):
    """Sprawdza, czy slot (60 min) wypada w luce przerwy dnia nauczyciela.
    Przerwa to luka między blokami zajęć wyliczana wg planu dnia."""
    if not nauczyciel:
        return False
    wpis = PrzerwaDnia.objects.filter(nauczyciel=nauczyciel, data=dzien).first()
    if not wpis or not wpis.minuty:
        return False

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
            przerwy.append((start - timedelta(minutes=wpis.minuty), start))
        przesuniecie += wpis.minuty

    start_slotu = datetime.combine(date(1900, 1, 1), godzina_start)
    for poczatek, koniec in przerwy:
        if poczatek <= start_slotu < koniec:
            return True
    return False

MAX_PROB_LOGOWANIA = 5


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

@login_required
def wyloguj(request):
    """Wylogowuje pracownika i wraca na stronę logowania."""
    logout(request)
    messages.info(request, 'Zostałeś wylogowany. Do zobaczenia!')
    return redirect('logowanie')


@login_required
def pulpit(request):
    """Strona startowa: szef widzi pulpit, pracownik od razu formularz ucznia."""
    if not czy_szef(request.user):
        if getattr(request.user, 'nauczyciel_id', None):
            # nauczyciel: od razu swoj kalendarz (dni/godziny zaznacza sam w kalendarzu)
            return redirect('kalendarz_nauczyciela', nauczyciel_id=request.user.nauczyciel_id)
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
            'godzina_od': wpis['godzina_od'] if wpis else None,
            'godzina_do': wpis['godzina_do'] if wpis else None,
        })
    return wynik


def wyslij_powiadomienie_o_uczniu(request, uczen):
    """Wysyła e-mail z powiadomieniem, że do systemu wpisano nowego ucznia."""
    odbiorcy = getattr(settings, 'EMAILE_POWIADOMIEN', [])
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
            list(odbiorcy),
        )
    except Exception:
        pass


@login_required
def uczniowie(request):
    """Zakładka uczniowie: lista, formularz dodawania i dostępność nauczycieli.
    Zwykły pracownik widzi tylko uczniów, których sam umówił.
    Konto nauczyciela nie ma tu wstępu (widzi tylko swój kalendarz)."""
    if getattr(request.user, 'nauczyciel_id', None):
        raise PermissionDenied
    if czy_szef(request.user):         
        uczniowie_lista = Uczen.objects.all()
    else:
        uczniowie_lista = Uczen.objects.filter(umowil=request.user)
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

    if request.method == 'POST' and form.is_valid():
        uczen = form.save(commit=False)
        uczen.pierwsza_lekcja = form.cleaned_data['pierwsza_lekcja']
        uczen.kto_umowil = request.user.get_full_name() or request.user.username
        uczen.umowil = request.user
        uczen.save()
        messages.success(request, 'Uczeń został dodany do systemu.')
        wyslij_powiadomienie_o_uczniu(request, uczen)
        return redirect('uczniowie')

    return render(request, 'konta/uczniowie.html', {
        'form': form,
        'uczniowie': uczniowie_lista,
        'nadchodzace_dostepnosci': dostepnosci_nauczycieli(),
    })

@login_required
def edytuj_ucznia(request, uczen_id):
    """Edycja danych ucznia: admin może edytować każdego, pracownik — tylko swoich."""
    uczen = get_object_or_404(Uczen, pk=uczen_id)
    if not czy_szef(request.user) and uczen.umowil_id != request.user.id:
        raise PermissionDenied

    if request.method == 'POST':
        form = FormularzUcznia(
            request.POST,
            instance=uczen,
            przedmiot=request.POST.get('przedmiot'),
            poziom=request.POST.get('poziom'),
        )
        # obecny korepetytor musi zostac na liscie, nawet jesli nauczyciel jest juz nieaktywny
        if uczen.korepetytor and uczen.korepetytor not in [w for w, _ in form.fields['korepetytor'].choices]:
            form.fields['korepetytor'].choices += [(uczen.korepetytor, uczen.korepetytor)]
        if form.is_valid():
            zapisany = form.save(commit=False)
            zapisany.pierwsza_lekcja = form.cleaned_data.get('pierwsza_lekcja') or uczen.pierwsza_lekcja
            zapisany.save()
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

    return render(request, 'konta/edytuj_ucznia.html', {'form': form, 'uczen': uczen})


@login_required
def usun_ucznia(request, uczen_id):
    """Usuwa ucznia wraz z jego lekcjami i zwalnia zablokowane godziny u korepetytora."""
    uczen = get_object_or_404(Uczen, pk=uczen_id)
    if not czy_szef(request.user) and uczen.umowil_id != request.user.id:
        raise PermissionDenied
    if request.method == 'POST':
        nauczyciel = nauczyciel_z_korepetytora(uczen.korepetytor)
        if nauczyciel:
            for lekcja in Lekcja.objects.filter(uczen=uczen):
                start = timezone.localtime(lekcja.data)
                Dostepnosc.objects.filter(
                    nauczyciel=nauczyciel,
                    data_od=start.date(),
                    godzina_od=start.time(),
                ).delete()
        imie = f'{uczen.imie_ucznia} {uczen.nazwisko_rodzica}'
        uczen.delete()
        messages.success(request, f'Uczeń {imie} został usunięty wraz z lekcjami.')
        return redirect('lekcje')
    return render(request, 'konta/usun_ucznia.html', {'uczen': uczen})


@tylko_szef
@login_required
def edytuj_uzytkownika(request, user_id):
    """Edycja konta: imię, nazwisko, email, telefon (bez hasla i roli)."""
    uzytkownik = get_object_or_404(Pracownik, pk=user_id)
    if request.method == 'POST':
        uzytkownik.first_name = request.POST.get('first_name', '').strip()
        uzytkownik.last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()
        if not email:
            messages.error(request, 'Adres e-mail jest wymagany.')
            return render(request, 'konta/edytuj_uzytkownika.html', {'uzytkownik': uzytkownik})
        uzytkownik.email = email
        uzytkownik.telefon = request.POST.get('telefon', '').strip()
        uzytkownik.save()
        messages.success(request, f'Dane konta {uzytkownik.username} zostały zaktualizowane.')
        return redirect('uzytkownicy')
    return render(request, 'konta/edytuj_uzytkownika.html', {'uzytkownik': uzytkownik})


@tylko_szef
@login_required
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
def archiwum_uczniow(request):
    """Zakładka archiwum: uczniowie ze zakończoną współpracą, z możliwością przywrócenia."""
    if getattr(request.user, 'nauczyciel_id', None):
        raise PermissionDenied
    if czy_szef(request.user):
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
    return render(request, 'konta/archiwum.html', {
        'uczniowie': uczniowie_lista,
        'fraza': (request.GET.get('szukaj') or '').strip(),
    })


@login_required
def zakoncz_ucznia(request, uczen_id):
    """Archiwizuje ucznia: znika z list bieżących, ale zostaje w archiwum (dane i lekcje nietknięte)."""
    uczen = get_object_or_404(Uczen, pk=uczen_id)
    if not czy_szef(request.user) and uczen.umowil_id != request.user.id:
        raise PermissionDenied
    if request.method == 'POST':
        nauczyciel = nauczyciel_z_korepetytora(uczen.korepetytor)
        if nauczyciel:
            for lekcja in Lekcja.objects.filter(uczen=uczen):
                start = timezone.localtime(lekcja.data)
                Dostepnosc.objects.filter(
                    nauczyciel=nauczyciel,
                    data_od=start.date(),
                    godzina_od=start.time(),
                ).delete()
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
    messages.success(request, f'Uczeń {imie} przywrócony z archiwum.')
    return redirect('archiwum')

@tylko_szef
@login_required
def pobierz_raport(request):
    """Raport zapłaconych płatności jako plik tekstowy do pobrania (zamiast e-maila)."""
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

@login_required
def kalendarz_nauczyciela(request, nauczyciel_id):
    """Kalendarz miesiąca z klikalnymi kafelkami godzin (co 30 minut)."""
    nauczyciel = get_object_or_404(Nauczyciel, pk=nauczyciel_id)
    edycja = moze_edytowac_kalendarz(request.user, nauczyciel_id)
    if not czy_szef(request.user) and not edycja:
        # konto nauczyciela: widzi TYLKO swój kalendarz
        if getattr(request.user, 'nauczyciel_id', None):
            raise PermissionDenied
        # zwykły pracownik: kalendarz tylko do podglądu
    nauczyciel = get_object_or_404(Nauczyciel, pk=nauczyciel_id)
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

    dostepnosci_miesiaca = Dostepnosc.objects.filter(
        nauczyciel=nauczyciel,
        data_od__year=rok,
        data_od__month=miesiac,
    )
    dni_z_godzinami = set(d.data_od for d in dostepnosci_miesiaca)

    kalendarz_miesiaca = calendar.Calendar(firstweekday=0).monthdatescalendar(rok, miesiac)
    tygodnie = []
    for tydzien in kalendarz_miesiaca:
        dni = []
        for dzien_miesiaca in tydzien:
            dni.append({
                'data': dzien_miesiaca,
                'w_tym_miesiacu': dzien_miesiaca.month == miesiac,
                'dostepny': dzien_miesiaca in dni_z_godzinami,
                'wybrany': wybrany_dzien == dzien_miesiaca,
            })
        tygodnie.append(dni)

    poprzedni_miesiac = miesiac - 1 if miesiac > 1 else 12
    poprzedni_rok = rok if miesiac > 1 else rok - 1
    nastepny_miesiac = miesiac + 1 if miesiac < 12 else 1
    nastepny_rok = rok if miesiac < 12 else rok + 1

    godziny_na_dzien = []
    if wybrany_dzien:
        godziny_na_dzien = Dostepnosc.objects.filter(
            nauczyciel=nauczyciel,
            data_od=wybrany_dzien,
        ).order_by('godzina_od')

    zaznaczone = set()
    if wybrany_dzien:
        for termin in godziny_na_dzien:
            start = datetime.combine(wybrany_dzien, termin.godzina_od)
            koniec = datetime.combine(wybrany_dzien, termin.godzina_do)
            while start < koniec:
                zaznaczone.add(start.strftime('%H:%M'))
                start += timedelta(minutes=30)

    przerwa_dnia = 0
    if wybrany_dzien:
        wpis = PrzerwaDnia.objects.filter(nauczyciel=nauczyciel, data=wybrany_dzien).first()
        if wpis:
            przerwa_dnia = wpis.minuty

    def policz_plan(zaznaczone_godziny, przerwa_min):
        plan = []
        sloty_przerwy = set()
        if not zaznaczone_godziny:
            return plan, sloty_przerwy
        sloty = sorted(zaznaczone_godziny)
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
                plan.append({
                    'typ': 'przerwa',
                    'start': poczatek_przerwy.strftime('%H:%M'),
                    'koniec': start.strftime('%H:%M'),
                })
                kursor = poczatek_przerwy.replace(minute=(poczatek_przerwy.minute // 30) * 30)
                while kursor < start:
                    sloty_przerwy.add(kursor.strftime('%H:%M'))
                    kursor += timedelta(minutes=30)
            plan.append({
                'typ': 'lekcja',
                'numer': numer,
                'start': start.strftime('%H:%M'),
                'koniec': koniec.strftime('%H:%M'),
            })
            przesuniecie += przerwa_min
        return plan, sloty_przerwy

    plan_dnia, sloty_przerwy = policz_plan(zaznaczone, przerwa_dnia)

    kafelki = []
    for h in range(6, 21):
        for m in (0, 30):
            etykieta = f'{h:02d}:{m:02d}'
            kafelki.append({
                'etykieta': etykieta,
                'zaznaczony': etykieta in zaznaczone,
                'przerwa': etykieta in sloty_przerwy and etykieta not in zaznaczone,
            })

    if request.method == 'POST' and 'zapisz_przerwe' in request.POST:
        if not edycja:
            messages.error(request, 'Ten kalendarz jest tylko do odczytu — nie masz uprawnień do edycji.')
            return redirect('kalendarz_nauczyciela', nauczyciel_id=nauczyciel.id)
        try:
            nowa_przerwa = int(request.POST.get('przerwa', 0))
        except (TypeError, ValueError):
            nowa_przerwa = 0
        if nowa_przerwa not in (0, 5, 10, 15, 30):
            nowa_przerwa = 0
        if wybrany_dzien:
            PrzerwaDnia.objects.update_or_create(
                nauczyciel=nauczyciel,
                data=wybrany_dzien,
                defaults={'minuty': nowa_przerwa},
            )
            if nowa_przerwa:
                messages.success(request, f'Przerwa {wybrany_dzien:%d.%m.%Y}: {nowa_przerwa} min.')
            else:
                messages.success(request, f'Przerwa {wybrany_dzien:%d.%m.%Y}: brak.')
        cel = reverse('kalendarz_nauczyciela', args=[nauczyciel.id])
        if wybrany_dzien:
            cel += f'?rok={rok}&miesiac={miesiac}&dzien={wybrany_dzien.day}'
        return redirect(cel)

    if request.method == 'POST' and 'przelacz_godzine' in request.POST and wybrany_dzien:
        if not edycja:
            messages.error(request, 'Ten kalendarz jest tylko do odczytu — nie masz uprawnień do edycji.')
            return redirect('kalendarz_nauczyciela', nauczyciel_id=nauczyciel.id)
        etykieta = request.POST['godzina']
        start = datetime.strptime(etykieta, '%H:%M').time()
        koniec = (datetime.combine(date.today(), start) + timedelta(minutes=30)).time()
        slot = Dostepnosc.objects.filter(
            nauczyciel=nauczyciel,
            data_od=wybrany_dzien,
            godzina_od=start,
            godzina_do=koniec,
        ).first()
        if slot:
            slot.delete()
            messages.success(request, f'Godzina {etykieta} — odznaczono.')
        else:
            Dostepnosc.objects.create(
                nauczyciel=nauczyciel,
                data_od=wybrany_dzien,
                data_do=wybrany_dzien,
                godzina_od=start,
                godzina_do=koniec,
            )
            messages.success(request, f'Godzina {etykieta} — oznaczono jako dostępna.')
        return redirect(
            reverse('kalendarz_nauczyciela', args=[nauczyciel.id])
            + f'?rok={rok}&miesiac={miesiac}&dzien={wybrany_dzien.day}'
        )

    return render(request, 'konta/lekcje.html', {
        'uczniowie': uczniowie_lista,
        'fraza': (request.GET.get('szukaj') or '').strip(),
    })


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
        if nowa_przerwa not in (0, 5, 10, 15, 30):
            nowa_przerwa = 0
        PrzerwaDnia.objects.update_or_create(
            nauczyciel=nauczyciel,
            data=dzien_data,
            defaults={'minuty': nowa_przerwa},
        )
        komunikat = f'Przerwa {dzien_data:%d.%m.%Y}: {nowa_przerwa} min.' if nowa_przerwa else f'Przerwa {dzien_data:%d.%m.%Y}: brak.'
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
    przerwa_min = wpis.minuty if wpis else 0

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

    kafelki = []
    for h in range(6, 21):
        for m in (0, 30):
            etykieta = f'{h:02d}:{m:02d}'
            kafelki.append({
                'etykieta': etykieta,
                'zaznaczony': etykieta in zaznaczone,
                'przerwa': etykieta in sloty_przerwy and etykieta not in zaznaczone,
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
        reverse('kalendarz_nauczyciela', args=[nauczyciel_id])
        + f'?rok={rok}&miesiac={miesiac}&dzien={dzien}'
    )

@login_required
def lekcje(request):
    """Zakładka lekcje: lista uczniów z liczbą lekcji i wejściem do kalendarza lekcji.
    Admin widzi wszystkich uczniów, zwykły pracownik — tylko swoich.
    Konto nauczyciela nie ma tu wstępu (widzi tylko swój kalendarz)."""
    if getattr(request.user, 'nauczyciel_id', None):
        raise PermissionDenied
    if czy_szef(request.user):
        uczniowie_lista = list(Uczen.objects.filter(zakonczona=False))
    else:
        uczniowie_lista = list(Uczen.objects.filter(umowil=request.user, zakonczona=False))
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
    return render(request, 'konta/lekcje.html', {
        'uczniowie': uczniowie_lista,
    })

@login_required
def kalendarz_lekcji(request, uczen_id):
    """Kalendarz lekcji ucznia: miesiąc, dni i kafelki godzin do wpisywania lekcji."""
    uczen = get_object_or_404(Uczen, pk=uczen_id)
    if not czy_szef(request.user) and uczen.umowil_id != request.user.id:
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

    lekcje_miesiaca = Lekcja.objects.filter(
        uczen=uczen,
        data__year=rok,
        data__month=miesiac,
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

    zaznaczone = set()
    if wybrany_dzien:
        for lekcja in Lekcja.objects.filter(uczen=uczen, data__date=wybrany_dzien):
            zaznaczone.add(timezone.localtime(lekcja.data).strftime('%H:%M'))

    nauczyciel = nauczyciel_z_korepetytora(uczen.korepetytor)

    zajete_przez_innych = set()
    if wybrany_dzien and nauczyciel:
        nazwa_nauczyciela = f'{nauczyciel.imie} {nauczyciel.nazwisko}'.strip().lower()
        for lekcja in Lekcja.objects.select_related('uczen').filter(data__date=wybrany_dzien):
            if lekcja.uczen_id == uczen.id:
                continue
            if (lekcja.uczen.korepetytor or '').strip().lower() != nazwa_nauczyciela:
                continue
            start_lekcji = timezone.localtime(lekcja.data).replace(tzinfo=None)
            for minuty in range(0, GODZINA_LEKCJI, 30):
                zajete_przez_innych.add((start_lekcji + timedelta(minutes=minuty)).strftime('%H:%M'))

    kafelki = []
    for h in range(6, 21):
        for m in (0, 30):
            etykieta = f'{h:02d}:{m:02d}'
            kafelki.append({
                'etykieta': etykieta,
                'zaznaczony': etykieta in zaznaczone,
                'zajety': etykieta in zajete_przez_innych,
            })

    if request.method == 'POST' and 'przelacz_godzine' in request.POST and wybrany_dzien:
        etykieta = request.POST['godzina']
        start_czas = datetime.strptime(etykieta, '%H:%M').time()
        data_lekcji = timezone.make_aware(datetime.combine(wybrany_dzien, start_czas))
        istniejaca = Lekcja.objects.filter(uczen=uczen, data=data_lekcji).first()
        if istniejaca:
            istniejaca.delete()
            if nauczyciel:
                Dostepnosc.objects.filter(
                    nauczyciel=nauczyciel,
                    data_od=wybrany_dzien,
                    godzina_od=start_czas,
                ).delete()
            messages.success(request, f'Lekcja {etykieta} {wybrany_dzien:%d.%m.%Y} została usunięta.')
        else:
            kolidujace = inne_lekcje_nauczyciela(nauczyciel, uczen, wybrany_dzien, start_czas)
            if kolidujace:
                imie = kolidujace[0].uczen.imie_ucznia
                messages.error(
                    request,
                    f'Nie można dodać lekcji {etykieta} — {nauczyciel.imie} {nauczyciel.nazwisko} '
                    f'ma już wtedy lekcję (uczeń {imie}). Wybierz inną godzinę.',
                )
            elif godzina_w_przerwie(nauczyciel, wybrany_dzien, start_czas):
                messages.error(
                    request,
                    f'Nie można dodać lekcji {etykieta} — ta godzina wypada w przerwie dnia '
                    f'{nauczyciel.imie} {nauczyciel.nazwisko}. Wybierz inną godzinę.',
                )
            else:
                Lekcja.objects.create(uczen=uczen, data=data_lekcji)
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
            + f'?rok={rok}&miesiac={miesiac}&dzien={wybrany_dzien.day}'
        )

    liczba_lekcji = Lekcja.objects.filter(
        uczen=uczen,
        data__year=dzisiaj.year,
        data__month=dzisiaj.month,
    ).count()

    return render(request, 'konta/kalendarz_lekcji.html', {
        'uczen': uczen,
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
        'liczba_lekcji': liczba_lekcji,
    })

@tylko_szef
@login_required
def platnosci(request):
    """Zakładka płatności: lista uczniów, statusy zapłacone i wysyłka raportu."""
    uczniowie_lista = Uczen.objects.filter(zaplacone=False, zakonczona=False)
    zaplaconi = Uczen.objects.filter(zaplacone=True, zakonczona=False)
    wszyscy_uczniowie = list(Uczen.objects.filter(zakonczona=False))
    email_odbiorcy = request.POST.get('email_raportu', '').strip()

    if request.method == 'POST' and 'oznacz_zaplacone' in request.POST:
        uczen = get_object_or_404(Uczen, pk=request.POST['uczen_id'])
        uczen.zaplacone = True
        uczen.save()
        messages.success(request, f'{uczen.imie_ucznia} {uczen.nazwisko_rodzica} — oznaczono jako zapłacone.')
        return redirect('platnosci')

    if request.method == 'POST' and 'odznacz_zaplacone' in request.POST:
        uczen = get_object_or_404(Uczen, pk=request.POST['uczen_id'])
        uczen.zaplacone = False
        uczen.save()
        messages.info(request, f'{uczen.imie_ucznia} {uczen.nazwisko_rodzica} — cofnięto status zapłacone.')
        return redirect('platnosci')

    if request.method == 'POST' and 'usun_ucznia' in request.POST:
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
        Uczen.objects.filter(zaplacone=True).delete()
        messages.success(request, f'Raport wysłano na {email_odbiorcy}. Zapłacone pozycje usunięto z listy.')
        return redirect('platnosci')

@tylko_szef
@login_required
def uzytkownicy(request):
    """Zakładka użytkownicy: dodawanie kont i nadawanie uprawnień (tylko szef)."""
    lista = Pracownik.objects.all().order_by('username')
    form = FormularzUzytkownika(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.zapisz()
        messages.success(request, 'Użytkownik został dodany.')
        return redirect('uzytkownicy')
    return render(request, 'konta/uzytkownicy.html', {
        'form': form,
        'uzytkownicy': lista,
        'nauczyciele': [u for u in lista if u.nauczyciel],
    })


@tylko_szef
@login_required
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
def kreator_dyspozycyjnosc(request):
    """Kreator dyspozycyjności nauczyciela — widok dla konta nauczyciela."""
    nauczyciel_id = getattr(request.user, 'nauczyciel_id', None)
    if not nauczyciel_id:
        raise PermissionDenied
    nauczyciel = get_object_or_404(Nauczyciel, pk=nauczyciel_id)
    form = FormularzDyspozycyjnosc(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        dane = form.cleaned_data
        godzina_od = datetime.strptime(dane['godzina_od'], '%H:%M').time()
        godzina_do = datetime.strptime(dane['godzina_do'], '%H:%M').time()
        przerwa = int(dane['przerwa'])
        wybrane_dni = {int(d) for d in dane['dni']}
        krok = timedelta(minutes=60 + przerwa)
        start = datetime.combine(date(2000, 1, 1), godzina_od)
        koniec = datetime.combine(date(2000, 1, 1), godzina_do)
        utworzone = 0
        dzien = dane['od_kiedy']
        while dzien <= dane['do_kiedy']:
            if dzien.weekday() in wybrane_dni:
                slot = start
                while slot < koniec:
                    Dostepnosc.objects.get_or_create(
                        nauczyciel=nauczyciel,
                        data_od=dzien,
                        data_do=dzien,
                        godzina_od=slot.time(),
                        godzina_do=(slot + timedelta(minutes=30)).time(),
                    )
                    utworzone += 1
                    slot += krok
            dzien += timedelta(days=1)
        nauczyciel.przerwa = przerwa
        nauczyciel.dyspozycyjnosc_uzupełniona = True
        nauczyciel.save()
        messages.success(
            request,
            f'Dyspozycyjność zapisana — w kalendarzu pojawiło się {utworzone} terminów. '
            'Możesz teraz wejść w swój kalendarz i doprecyzować godziny.',
        )
        return redirect('kalendarz_nauczyciela', nauczyciel_id=nauczyciel.id)
    return render(request, 'konta/kreator_dyspozycyjnosc.html', {'form': form})
def wyslij_zaproszenie_meet(uczen):
    """Wysyła e-mail do rodzica z linkiem do lekcji cyklicznej na Google Meet."""
    if not uczen.link_meet or not uczen.email_glowny:
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
            [uczen.email_glowny],
        )
    except Exception:
        pass

@login_required
def umow_lekcje_cykliczna(request, uczen_id):
    """Umawia stałe lekcje ucznia: wiele slotów w tygodniu, presets okresu,
    blokada terminów u korepetytora i e-mail z linkiem Meet do rodzica."""
    uczen = get_object_or_404(Uczen, pk=uczen_id)
    if not czy_szef(request.user) and uczen.umowil_id != request.user.id:
        raise PermissionDenied

    def okres_dat(okres, od_dnia):
        """Zwraca (od, do) wg wybranego presetu."""
        if okres == 'miesiac':
            if od_dnia.month == 12:
                do = date(od_dnia.year, 12, 31)
            else:
                nast = date(od_dnia.year, od_dnia.month + 1, 1)
                do = nast - timedelta(days=1)
            return od_dnia, do
        if od_dnia.month >= 9:
            rok = od_dnia.year + 1
        else:
            rok = od_dnia.year
        return od_dnia, date(rok, 6, 30)

    if request.method == 'POST':
        okres = request.POST.get('okres', 'miesiac')
        link_meet = request.POST.get('link_meet', '').strip()
        od_dnia, do_dnia = okres_dat(okres, date.today())
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
        nauczyciel = nauczyciel_z_korepetytora(uczen.korepetytor)
        utworzone = 0
        pominiete_konflikt = 0
        pominiete_przerwa = 0
        for dzien_tygodnia, godzina_txt in sloty:
            godzina = datetime.strptime(godzina_txt, '%H:%M').time()
            dzien = od_dnia
            while dzien <= do_dnia:
                if dzien.weekday() == dzien_tygodnia:
                    data_lekcji = timezone.make_aware(datetime.combine(dzien, godzina))
                    kolidujace = inne_lekcje_nauczyciela(nauczyciel, uczen, dzien, godzina)
                    w_przerwie = godzina_w_przerwie(nauczyciel, dzien, godzina)
                    if kolidujace:
                        pominiete_konflikt += 1
                    elif w_przerwie:
                        pominiete_przerwa += 1
                    else:
                        Lekcja.objects.get_or_create(uczen=uczen, data=data_lekcji)
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
        if pominiete_konflikt or pominiete_przerwa:
            komunikaty = []
            if pominiete_konflikt:
                komunikaty.append(f'{pominiete_konflikt} terminów z konfliktem (nauczyciel ma już wtedy lekcję)')
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
        if link_meet:
            uczen.link_meet = link_meet
            uczen.save()
            wyslij_zaproszenie_meet(uczen)
        return redirect('kalendarz_lekcji', uczen_id=uczen.id)
    return render(request, 'konta/lekcja_cykliczna.html', {
        'uczen': uczen,
        'godziny': [f'{h:02d}:{m:02d}' for h in range(6, 24) for m in (0, 30)],
    })

@login_required
def zmiana_hasla(request):
    """Pozwala zalogowanemu pracownikowi zmienić własne hasło."""
    form = FormularzZmianyHasla(request.user, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Hasło zostało zmienione.')
        return redirect('pulpit')
    return render(request, 'konta/zmiana_hasla.html', {'form': form})


def brak_dostepu(request, exception=None):
    """Strona wyświetlana, gdy pracownik nie ma uprawnień (błąd 403)."""
    return render(request, 'konta/brak_dostepu.html', status=403)