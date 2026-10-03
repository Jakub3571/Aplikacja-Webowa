import calendar
from datetime import date, datetime, timedelta

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import (
    FormularzLogowania,
    FormularzNauczyciela,
    FormularzUcznia,
    FormularzZmianyHasla,
)
from .models import Dostepnosc, Nauczyciel, RaportPlatnosci, Uczen

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
    """Zakładka startowa panelu."""
    return render(request, 'konta/pulpit.html')


@login_required
def uczniowie(request):
    """Zakładka uczniowie: lista, formularz dodawania i dostępność nauczycieli."""
    uczniowie_lista = Uczen.objects.all()
    wybrany_przedmiot = request.POST.get('przedmiot') if request.method == 'POST' else None

    if request.method == 'POST' and 'wybierz_przedmiot' in request.POST:
        form = FormularzUcznia(
            initial={'przedmiot': wybrany_przedmiot},
            przedmiot=wybrany_przedmiot,
        )
        return render(request, 'konta/uczniowie.html', {
            'form': form,
            'uczniowie': uczniowie_lista,
            'nadchodzace_dostepnosci': Dostepnosc.objects
                .filter(data_do__gte=date.today())
                .select_related('nauczyciel')
                .order_by('data_od', 'godzina_od'),
        })

    form = FormularzUcznia(request.POST or None, przedmiot=wybrany_przedmiot)

    if request.method == 'POST' and form.is_valid():
        uczen = form.save(commit=False)
        uczen.kto_umowil = request.user.get_full_name() or request.user.username
        uczen.save()
        messages.success(request, 'Uczeń został dodany do systemu.')
        return redirect('uczniowie')

    dzisiaj = date.today()
    nadchodzace = (
        Dostepnosc.objects
        .filter(data_do__gte=dzisiaj)
        .select_related('nauczyciel')
        .order_by('data_od', 'godzina_od')
    )

    return render(request, 'konta/uczniowie.html', {
        'form': form,
        'uczniowie': uczniowie_lista,
        'nadchodzace_dostepnosci': nadchodzace,
    })


@login_required
def nauczyciele(request):
    """Zakładka nauczyciele: lista i formularz dodawania nauczyciela."""
    nauczyciele_lista = Nauczyciel.objects.all()
    form = FormularzNauczyciela(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Nauczyciel został dodany do systemu.')
        return redirect('nauczyciele')

    return render(request, 'konta/nauczyciele.html', {
        'form': form,
        'nauczyciele': nauczyciele_lista,
    })


@login_required
def usun_nauczyciela(request, nauczyciel_id):
    """Usuwa nauczyciela z listy i wraca do zakładki Nauczyciele."""
    nauczyciel = get_object_or_404(Nauczyciel, pk=nauczyciel_id)
    imie = f'{nauczyciel.imie} {nauczyciel.nazwisko}'
    nauczyciel.delete()
    messages.success(request, f'{imie} — usunięto z listy nauczycieli.')
    return redirect('nauczyciele')


@login_required
def kalendarz_nauczyciela(request, nauczyciel_id):
    """Kalendarz miesiąca z klikalnymi kafelkami godzin (co 30 minut)."""
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
    })


@login_required
def usun_termin(request, termin_id):
    """Usuwa wpisaną godzinę pracy i wraca do tego samego dnia kalendarza."""
    termin = get_object_or_404(Dostepnosc, pk=termin_id)
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
def platnosci(request):
    """Zakładka płatności: lista uczniów, statusy zapłacone i wysyłka raportu."""
    uczniowie_lista = Uczen.objects.all()
    wszyscy_zaplacono = not uczniowie_lista or all(u.zaplacone for u in uczniowie_lista)
    email_odbiorcy = request.POST.get('email_raportu', '').strip()

    if request.method == 'POST' and 'oznacz_zaplacone' in request.POST:
        uczen = get_object_or_404(Uczen, pk=request.POST['uczen_id'])
        uczen.zaplacone = True
        uczen.save()
        messages.success(request, f'{uczen.imie_ucznia} {uczen.nazwisko_rodzica} — oznaczono jako zapłacone.')
        return redirect('platnosci')

    if request.method == 'POST' and 'usun_ucznia' in request.POST:
        uczen = get_object_or_404(Uczen, pk=request.POST['uczen_id'])
        imie = f'{uczen.imie_ucznia} {uczen.nazwisko_rodzica}'
        uczen.delete()
        messages.success(request, f'{imie} — usunięto z listy.')
        return redirect('platnosci')

    if request.method == 'POST' and 'wyslij_raport' in request.POST:
        if not wszyscy_zaplacono:
            messages.error(request, 'Nie można wysłać raportu — nie wszyscy uczniowie mają status „zapłacone".')
            return redirect('platnosci')
        if not email_odbiorcy:
            messages.error(request, 'Wpisz adres e-mail, na który ma pójść raport.')
            return redirect('platnosci')

        linie = []
        for u in uczniowie_lista:
            linie.append(
                f'{u.imie_ucznia} {u.nazwisko_rodzica} — nauczyciel: {u.korepetytor} '
                f'— wpisał: {u.kto_umowil} — 1. lekcja: {u.pierwsza_lekcja:%d.%m.%Y %H:%M}'
            )
        tresc = 'Raport płatności:\n\n' + '\n'.join(linie)
        send_mail(
            'Raport płatności — Panel pracowniczy',
            tresc,
            None,
            [email_odbiorcy],
        )
        RaportPlatnosci.objects.create(
            zawartosc=tresc,
            email_odbiorcy=email_odbiorcy,
            wyslal=request.user.get_full_name() or request.user.username,
        )
        uczniowie_lista.delete()
        messages.success(request, f'Raport wysłano na {email_odbiorcy}. Lista została wyczyszczona.')
        return redirect('platnosci')

    return render(request, 'konta/platnosci.html', {
        'uczniowie': uczniowie_lista,
        'wszyscy_zaplacono': wszyscy_zaplacono,
        'email_raportu': email_odbiorcy,
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