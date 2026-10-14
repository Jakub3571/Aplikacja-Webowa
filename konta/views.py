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

from .forms import (
    FormularzDyspozycyjnosc,
    FormularzLogowania,
    FormularzNauczyciela,
    FormularzUcznia,
    FormularzUzytkownika,
    FormularzZmianyHasla,
    FormularzLekcjiCyklicznej,
)
from .models import Dostepnosc, Lekcja, Nauczyciel, Pracownik, RaportPlatnosci, Uczen

MIESIACE = [
    'styczeń', 'luty', 'marzec', 'kwiecień', 'maj', 'czerwiec',
    'lipiec', 'sierpień', 'wrzesień', 'październik', 'listopad', 'grudzień',
]

DNI_TYGODNIA = ['Pn', 'Wt', 'Śr', 'Cz', 'Pt', 'So', 'Nd']


def logowanie(request):
    """Wyświetla formularz logowania i loguje pracownika po poprawnych danych."""
    if request.user.is_authenticated:
        return redirect('pulpit')
    form = FormularzLogowania(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.get_user()
        login(request, user)
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
            nauczyciel = request.user.nauczyciel
            if not nauczyciel.dyspozycyjnosc_uzupełniona:
                return redirect('kreator_dyspozycyjnosc')
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
def nauczyciele(request):
    """Zakładka nauczyciele: admin widzi listę, konto nauczyciela — tylko swój kalendarz,
    zwykły pracownik — listę nauczycieli z kalendarzem tylko do podglądu."""
    if not czy_szef(request.user):
        nauczyciel_id = getattr(request.user, 'nauczyciel_id', None)
        if nauczyciel_id:
            return redirect('kalendarz_nauczyciela', nauczyciel_id=nauczyciel_id)
        # zwykły pracownik: lista nauczycieli do podglądu (bez formularza i historii)
        nauczyciele_lista = Nauczyciel.objects.prefetch_related('przedmioty').filter(aktywny=True)
        return render(request, 'konta/nauczyciele.html', {
            'nauczyciele': nauczyciele_lista,
            'podglad': True,
        })
    nauczyciele_lista = Nauczyciel.objects.prefetch_related('przedmioty').filter(aktywny=True)
    historia_nauczycieli = Nauczyciel.objects.prefetch_related('przedmioty').filter(aktywny=False)
    form = FormularzNauczyciela(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        form.zapisz()
        messages.success(request, 'Nauczyciel został dodany do systemu.')
        return redirect('nauczyciele')

    return render(request, 'konta/nauczyciele.html', {
        'form': form,
        'nauczyciele': nauczyciele_lista,
        'historia_nauczycieli': historia_nauczycieli,
    })
@tylko_szef
@login_required
def przelacz_umowe(request, nauczyciel_id):
    """Przełącza status umowy nauczyciela (ma/nie ma)."""
    nauczyciel = get_object_or_404(Nauczyciel, pk=nauczyciel_id)
    nauczyciel.ma_umowe = not nauczyciel.ma_umowe
    nauczyciel.save()
    if nauczyciel.ma_umowe:
        messages.success(request, f'{nauczyciel.imie} {nauczyciel.nazwisko} — umowa podpisana.')
    else:
        messages.info(request, f'{nauczyciel.imie} {nauczyciel.nazwisko} — umowa wycofana.')
    return redirect('nauczyciele')

@tylko_szef
@login_required
def archiwizuj_nauczyciela(request, nauczyciel_id):
    """Archiwizuje nauczyciela: znika z listy, ale zostaje w historii."""
    nauczyciel = get_object_or_404(Nauczyciel, pk=nauczyciel_id)
    nauczyciel.aktywny = False
    nauczyciel.save()
    imie = f'{nauczyciel.imie} {nauczyciel.nazwisko}'
    messages.success(request, f'{imie} — zakończono współpracę. Nauczyciel trafił do historii.')
    return redirect('nauczyciele')


@tylko_szef
@login_required
def przywroc_nauczyciela(request, nauczyciel_id):
    """Przywraca nauczyciela z historii na aktywną listę."""
    nauczyciel = get_object_or_404(Nauczyciel, pk=nauczyciel_id)
    nauczyciel.aktywny = True
    nauczyciel.save()
    imie = f'{nauczyciel.imie} {nauczyciel.nazwisko}'
    messages.success(request, f'{imie} — wznowiono współpracę. Nauczyciel wrócił na listę.')
    return redirect('nauczyciele')


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

    kafelki = []
    for h in range(24):
        for m in (0, 30):
            etykieta = f'{h:02d}:{m:02d}'
            kafelki.append({'etykieta': etykieta, 'zaznaczony': etykieta in zaznaczone})

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

    return render(request, 'konta/kalendarz.html', {
        'nauczyciel': nauczyciel,
        'tygodnie': tygodnie,
        'dni_tygodnia': DNI_TYGODNIA,
        'nazwa_miesiaca': MIESIACE[miesiac - 1],
        'rok': rok,
        'miesiac': miesiac,
        'poprzedni_miesiac': poprzedni_miesiac,
        'poprzedni_rok': poprzedni_rok,
        'nastepny_miesiac': nastepny_miesiac,
        'nastepny_rok': nastepny_rok,
        'dostepnosci': dostepnosci_miesiaca.order_by('data_od', 'godzina_od'),
        'wybrany_dzien': wybrany_dzien,
        'godziny_na_dzien': godziny_na_dzien,
        'kafelki': kafelki,
        'edycja': edycja,
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
        uczniowie_lista = list(Uczen.objects.all())
    else:
        uczniowie_lista = list(Uczen.objects.filter(umowil=request.user))
    liczba_lekcji = {}
    for lekcja in Lekcja.objects.filter(uczen__in=uczniowie_lista):
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

    kafelki = []
    for h in range(24):
        for m in (0, 30):
            etykieta = f'{h:02d}:{m:02d}'
            kafelki.append({'etykieta': etykieta, 'zaznaczony': etykieta in zaznaczone})

    if request.method == 'POST' and 'przelacz_godzine' in request.POST and wybrany_dzien:
        etykieta = request.POST['godzina']
        start_czas = datetime.strptime(etykieta, '%H:%M').time()
        data_lekcji = timezone.make_aware(datetime.combine(wybrany_dzien, start_czas))
        istniejaca = Lekcja.objects.filter(uczen=uczen, data=data_lekcji).first()
        if istniejaca:
            istniejaca.delete()
            messages.success(request, f'Lekcja {etykieta} {wybrany_dzien:%d.%m.%Y} została usunięta.')
        else:
            Lekcja.objects.create(uczen=uczen, data=data_lekcji)
            messages.success(request, f'Lekcja {etykieta} {wybrany_dzien:%d.%m.%Y} została dodana.')
        return redirect(
            reverse('kalendarz_lekcji', args=[uczen.id])
            + f'?rok={rok}&miesiac={miesiac}&dzien={wybrany_dzien.day}'
        )

    liczba_lekcji = Lekcja.objects.filter(uczen=uczen).count()

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
    uczniowie_lista = Uczen.objects.filter(zaplacone=False)
    zaplaconi = Uczen.objects.filter(zaplacone=True)
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
        send_mail(
            'Raport zapłaconych płatności — Panel pracowniczy',
            tresc,
            None,
            [email_odbiorcy],
        )
        RaportPlatnosci.objects.create(
            zawartosc=tresc,
            email_odbiorcy=email_odbiorcy,
            wyslal=request.user.get_full_name() or request.user.username,
        )
        Uczen.objects.filter(zaplacone=True).delete()
        messages.success(request, f'Raport wysłano na {email_odbiorcy}. Zapłacone pozycje usunięto z listy.')
        return redirect('platnosci')

    rozliczenia = []
    for nauczyciel in Nauczyciel.objects.filter(aktywny=True).order_by('nazwisko', 'imie'):
        nazwa = f'{nauczyciel.imie} {nauczyciel.nazwisko}'
        jego_uczniowie = [u for u in uczniowie_lista if (u.korepetytor or '').strip() == nazwa]
        zaplacone_lekcje = [u for u in jego_uczniowie if u.zaplacone]
        if jego_uczniowie:
            rozliczenia.append({
                'nauczyciel': nauczyciel,
                'liczba_uczniow': len(jego_uczniowie),
                'liczba_zaplaconych': len(zaplacone_lekcje),
                'kwota': nauczyciel.stawka * len(zaplacone_lekcje),
            })

    return render(request, 'konta/platnosci.html', {
        'uczniowie': uczniowie_lista,
        'zaplaconi': zaplaconi,
        'email_raportu': email_odbiorcy,
        'rozliczenia': rozliczenia,
    })

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
        nauczyciel = None
        if ' ' in uczen.korepetytor:
            imie, nazwisko = uczen.korepetytor.split(' ', 1)
            nauczyciel = Nauczyciel.objects.filter(imie__iexact=imie, nazwisko__iexact=nazwisko).first()
        utworzone = 0
        for dzien_tygodnia, godzina_txt in sloty:
            godzina = datetime.strptime(godzina_txt, '%H:%M').time()
            dzien = od_dnia
            while dzien <= do_dnia:
                if dzien.weekday() == dzien_tygodnia:
                    data_lekcji = timezone.make_aware(datetime.combine(dzien, godzina))
                    Lekcja.objects.get_or_create(uczen=uczen, data=data_lekcji)
                    if nauczyciel:
                        Dostepnosc.objects.get_or_create(
                            nauczyciel=nauczyciel,
                            data_od=dzien,
                            data_do=dzien,
                            godzina_od=godzina,
                            godzina_do=(datetime.combine(date(2000, 1, 1), godzina) + timedelta(minutes=60)).time(),
                        )
                    utworzone += 1
                dzien += timedelta(days=1)
        if link_meet:
            uczen.link_meet = link_meet
            uczen.save()
            wyslij_zaproszenie_meet(uczen)
        messages.success(
            request,
            f'Stałe lekcje umówione — wpisano {utworzone} terminów do {do_dnia:%d.%m.%Y}. '
            'Korepetytor ma zablokowane godziny w kalendarzu.',
        )
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