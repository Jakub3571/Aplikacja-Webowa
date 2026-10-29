"""Formularze: logowanie, zmiana hasła, dodawanie ucznia i nauczyciela."""

from datetime import datetime, time

from django.utils import timezone

from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import (
    POZIOMY_NAUCZYCIELA,
    PRZEDMIOTY,
    Nauczyciel,
    Pracownik,
    PrzedmiotNauczyciela,
    Uczen,
)

WYBIERZ_PRZEDMIOT = [('', '— wybierz przedmiot —')] + list(PRZEDMIOTY)

POZIOMY = [
    ('', '— wybierz poziom —'),
    ('podstawowka', 'podstawówka'),
    ('srednia podstawa', 'średnia podstawa'),
    ('srednia rozszerzenie', 'średnia rozszerzenie'),
    ('studia', 'studia'),
]

# Mapowanie poziomu ucznia na poziom nauczyciela: 1:1 — te same klucze.
# 'podstawówka' (z ogonkiem) też rozpoznawana — bezpiecznik dla starszych danych.
# Poziomy 'srednia rozszerzenie' i 'studia' są teraz osobne; starszy zapis

POZIOM_UCZEN_NA_NAUCZYCIELA = {
    'podstawowka': 'podstawowka',
    'podstawówka': 'podstawowka',
    'srednia podstawa': 'srednia podstawa',
    'srednia rozszerzenie': 'srednia rozszerzenie',
    'studia': 'studia',
}

# Mapowanie poziomu ucznia na poziom nauczyciela: 1:1 — te same klucze.
# 'podstawówka' (z ogonkiem) też rozpoznawana — bezpiecznik dla starszych danych.


GODZINY_LEKCJI = [
    (f'{h:02d}:{m:02d}', f'{h:02d}:{m:02d}') for h in range(6, 21) for m in range(0, 60, 5)
]


class FormularzLogowania(AuthenticationForm):
    """Logowanie z polskimi komunikatami."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({
            'placeholder': 'np. j.kowalski',
            'autofocus': True,
        })
        self.fields['password'].widget.attrs.update({
            'placeholder': 'Twoje hasło',
        })

    error_messages = {
        'invalid_login': 'Nieprawidłowa nazwa użytkownika lub hasło.',
        'inactive': 'To konto jest nieaktywne. Skontaktuj się z administratorem.',
    }


class FormularzZmianyHasla(PasswordChangeForm):
    """Zmiana hasła przez zalogowanego pracownika."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        nowe = self.fields['new_password2']
        nowe.help_text = 'Powtórz nowe hasło, żeby wykluczyć literówkę.'


class FormularzUcznia(forms.ModelForm):
    """Formularz dodawania ucznia — pola obowiązkowe z podpowiedziami."""



    auto_wyslij_meet = forms.BooleanField(
        label='wyślij link Meet automatycznie po dodaniu',
        required=False,
        initial=True,
        help_text='Odznacz, jeśli link ma być wysłany później ręcznie.',
    )

    przedmiot = forms.ChoiceField(
        label='przedmiot',
        choices=WYBIERZ_PRZEDMIOT,
    )
    poziom = forms.ChoiceField(
        label='poziom',
        choices=POZIOMY,
    )
    data_pierwszej_lekcji = forms.DateField(
        label='data 1. lekcji',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    godzina_pierwszej_lekcji = forms.ChoiceField(
        label='godzina 1. lekcji',
        choices=GODZINY_LEKCJI,
        help_text='Godziny pojawiają się po wybraniu daty, przedmiotu i poziomu — to wolne terminy korepetytorów.',
    )

    class Meta:
        model = Uczen
        fields = [
            'przedmiot', 'poziom',
            'imie_ucznia', 'nazwisko_rodzica', 'rodzic',
            'telefon_glowny', 'email_glowny',
            'telefon_dodatkowy', 'email_dodatkowy',
            'korepetytor', 'notatka_od_rodzica',
        ]
        widgets = {
            'notatka_od_rodzica': forms.Textarea(attrs={'rows': 4}),
        }
        help_texts = {
            'imie_ucznia': 'Samo imię ucznia',
            'nazwisko_rodzica': 'Nazwisko rodzica (uczeń nosi nazwisko rodzica)',
            'rodzic': 'Imię i nazwisko rodzica do kontaktu',
            'telefon_glowny': 'Główny numer do kontaktu z rodzicem',
            'email_glowny': 'Główny adres e-mail do kontaktu',
            'telefon_dodatkowy': 'Zapasowy numer telefonu',
            'email_dodatkowy': 'Zapasowy adres e-mail',
            'notatka_od_rodzica': 'Dodatkowe informacje od rodzica',
        }
        error_messages = {
            'email_glowny': {'invalid': 'Wpisz poprawny adres e-mail.'},
            'email_dodatkowy': {'invalid': 'Wpisz poprawny adres e-mail.'},
        }

    def __init__(self, *args, przedmiot=None, poziom=None, **kwargs):
        super().__init__(*args, **kwargs)
        # przy GET: godzina to sam placeholder (JS wypełni po wyborze daty/przedmiotu/poziomu);
        # przy POST: pełna siatka 5-min — dostępność i tak pilnuje widok (nauczyciel_wolny_o)
        if not self.is_bound:
            self.fields['godzina_pierwszej_lekcji'].choices = [('', '— najpierw data, przedmiot i poziom —')]
        nauczyciele = Nauczyciel.objects.none()
        if przedmiot:
            wpisane_przedmioty = PrzedmiotNauczyciela.objects.filter(przedmiot=przedmiot)
            if poziom:
                # nowy poziom używany 1:1; stare wartości przetłumaczone słownikiem
                poziom_nauczyciela = POZIOM_UCZEN_NA_NAUCZYCIELA.get(poziom, poziom)
                wpisane_przedmioty = wpisane_przedmioty.filter(poziom=poziom_nauczyciela)
            nauczyciele = (
                Nauczyciel.objects
                .filter(przedmioty__in=wpisane_przedmioty, aktywny=True)
                .distinct()
                .order_by('nazwisko', 'imie')
            )
        self.fields['korepetytor'] = forms.ChoiceField(
            label='korepetytor',
            choices=[('', '— najpierw wybierz przedmiot i poziom —')]
            + [
                (
                    f'{n.imie} {n.nazwisko}',
                    f'{n.imie} {n.nazwisko} ({n.przedmioty_tekst()})',
                )
                for n in nauczyciele
            ],
            help_text='Lista korepetytorów pojawia się po wybraniu daty, przedmiotu, poziomu i godziny — tylko dostępni o wybranej godzinie.',
        )
        for nazwa, pole in self.fields.items():
            pole.required = True
            pole.widget.attrs['required'] = True
        # pola "na zapas" i notatka są opcjonalne — bez nich też można dodać ucznia
        for opcjonalne in ('telefon_dodatkowy', 'email_dodatkowy', 'notatka_od_rodzica'):
            if opcjonalne in self.fields:
                self.fields[opcjonalne].required = False
                self.fields[opcjonalne].widget.attrs.pop('required', None)
        # przełącznik automatycznej wysyłki ma zostać opcjonalny (odznaczony = nie wysyłaj)
        self.fields['auto_wyslij_meet'].required = False
        self.fields['auto_wyslij_meet'].widget.attrs.pop('required', None)

    def clean(self):
        dane = super().clean()
        data = dane.get('data_pierwszej_lekcji')
        godzina = dane.get('godzina_pierwszej_lekcji')
        if data and godzina:
            godziny, minuty = godzina.split(':')
            czas = time(int(godziny), int(minuty))
            dane['pierwsza_lekcja'] = timezone.make_aware(datetime.combine(data, czas))
        return dane

class FormularzUzytkownika(forms.ModelForm):
    """Formularz dodawania nowego użytkownika panelu — rola wybierana na górze.
    Dla nauczyciela: wiersze przedmiot + poziom + stawka."""

    rola = forms.ChoiceField(
        label='rola',
        choices=[
            ('pracownik', 'osoba umawiająca'),
            ('koordynator', 'koordynator (edycja, bez usuwania)'),
            ('nauczyciel', 'korepetytor'),
            ('admin', 'admin (pełny dostęp)'),
        ],
        initial='pracownik',
        help_text='Wybierz, kim jest ta osoba — formularz dostosuje się automatycznie.',
    )
    haslo1 = forms.CharField(
        label='hasło',
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        help_text='Minimum 8 znaków. Nie może być samymi cyframi.',
    )
    haslo2 = forms.CharField(
        label='powtórz hasło',
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        help_text='Powtórz to samo hasło, żeby wykluczyć literówkę.',
    )

    def __init__(self, *args, bez_admina=False, **kwargs):
        super().__init__(*args, **kwargs)
        # koordynator nie widzi roli admina na liście
        if bez_admina:
            self.fields['rola'].choices = [
                ('pracownik', 'osoba umawiająca'),
                ('koordynator', 'koordynator (edycja, bez usuwania)'),
                ('nauczyciel', 'korepetytor'),
            ]
        # dla nauczyciela: budujemy wiersze przedmiot+poziom+stawka
        self.wiersze_przedmiotow = []
        if self.is_bound and hasattr(self.data, 'getlist'):
            przedmioty = self.data.getlist('w_przedmiot')
            poziomy = self.data.getlist('w_poziom')
            stawki = self.data.getlist('w_stawka')
            for idx, przedmiot in enumerate(przedmioty):
                if not przedmiot:
                    continue
                poziom = poziomy[idx] if idx < len(poziomy) else 'podstawówka'
                stawka = stawki[idx] if idx < len(stawki) else '0'
                self.wiersze_przedmiotow.append({
                    'przedmiot': przedmiot,
                    'poziom': poziom,
                    'stawka': stawka,
                })
        if not self.wiersze_przedmiotow:
            self.wiersze_przedmiotow = [{'przedmiot': '', 'poziom': 'podstawówka', 'stawka': ''}]
        self.fields_przedmioty = list(PRZEDMIOTY)
        self.fields['email'].required = False

    class Meta:
        model = Pracownik
        fields = ['username', 'first_name', 'last_name', 'email', 'telefon']
        help_texts = {
            'username': 'Nazwa do logowania, np. j.kowalski',
            'email': 'Podaj prawdziwy adres e-mail — wysyłamy na niego link Meet, przypomnienia i reset hasła. Puste = tymczasowy login@konta.local.',
        }

    def clean(self):
        dane = super().clean()
        haslo1 = dane.get('haslo1')
        haslo2 = dane.get('haslo2')
        if haslo1 and haslo2 and haslo1 != haslo2:
            raise forms.ValidationError('Hasła nie są identyczne.')
        if haslo1:
            try:
                validate_password(haslo1)
            except ValidationError as e:
                raise forms.ValidationError(list(e.messages))


        # e-mail: jeśli podano — zostaje; jeśli nie — automatyczny login@konta.local
        # (reset hasła i powiadomienia działają tylko z prawdziwym adresem)
        if not dane.get('email'):
            dane['email'] = f"{dane.get('username', '')}@konta.local"
        # nauczyciel: walidacja wierszy przedmiotow
        if dane.get('rola') == 'nauczyciel':
            pelne = [w for w in self.wiersze_przedmiotow if w['przedmiot']]
            if not pelne:
                raise forms.ValidationError('Nauczyciel musi mieć min. jeden przedmiot.')
            pary = set()
            for w in pelne:
                para = (w['przedmiot'], w['poziom'])
                if para in pary:
                    raise forms.ValidationError(
                        f'Przedmiot {w["przedmiot"]} na poziomie {w["poziom"]} jest wpisany dwa razy.'
                    )
                pary.add(para)
                try:
                    stawka = float(w['stawka'].replace(',', '.') or '0')
                    if stawka < 0:
                        raise ValueError
                except ValueError:
                    raise forms.ValidationError(
                        f'Stawka dla {w["przedmiot"]} ({w["poziom"]}) musi być liczbą.'
                    )
        return dane

    def zapisz(self):
        """Tworzy użytkownika z hasłem, rolą, a dla nauczyciela — przedmiotami ze stawkami."""
        user = self.save(commit=False)
        rola = self.cleaned_data['rola']
        user.set_password(self.cleaned_data['haslo1'])
        user.is_superuser = rola == 'admin'
        user.is_staff = rola == 'admin'
        # rola koordynatora zapisana na koncie (pole stanowisko) — dzieki temu
        # dziala niezaleznie od loginu, a koordynator nie moze byc adminem
        user.stanowisko = 'koordynator' if rola == 'koordynator' else ''
        user.save()
        if rola == 'nauczyciel':
            nauczyciel = Nauczyciel.objects.create(
                imie=user.first_name or user.username,
                nazwisko=user.last_name or user.username,
                email=user.email,
            )
            user.nauczyciel = nauczyciel
            user.save()
            for w in self.wiersze_przedmiotow:
                if not w['przedmiot']:
                    continue
                stawka = w['stawka'].replace(',', '.') or '0'
                PrzedmiotNauczyciela.objects.update_or_create(
                    nauczyciel=nauczyciel,
                    przedmiot=w['przedmiot'],
                    poziom=w['poziom'],
                    defaults={'stawka': stawka},
                )
        return user
class FormularzLekcjiCyklicznej(forms.Form):
    """Umawianie lekcji cyklicznej ucznia: dzień tygodnia, godzina i okres."""

    dzien_tygodnia = forms.ChoiceField(
        label='dzień tygodnia',
        choices=[
            (0, 'poniedziałek'), (1, 'wtorek'), (2, 'środa'), (3, 'czwartek'),
            (4, 'piątek'), (5, 'sobota'), (6, 'niedziela'),
        ],
    )
    godzina = forms.ChoiceField(
        label='godzina lekcji',
        choices=[(f'{h:02d}:{m:02d}', f'{h:02d}:{m:02d}') for h in range(6, 24) for m in (0, 30)],
    )
    od_kiedy = forms.DateField(
        label='od dnia',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    do_kiedy = forms.DateField(
        label='do dnia (np. koniec roku szkolnego)',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    link_meet = forms.CharField(
        label='link Google Meet',
        help_text='Stały link do spotkań Google Meet (meet.google.com/...). Wpisz raz — będzie ten sam przy każdej lekcji.',
    )
class FormularzDyspozycyjnosc(forms.Form):
    """Kreator dyspozycyjności nauczyciela: dzień, przedział godzin i przerwa."""

    dni = forms.MultipleChoiceField(
        label='dni tygodnia',
        choices=[
            (0, 'poniedziałek'), (1, 'wtorek'), (2, 'środa'), (3, 'czwartek'),
            (4, 'piątek'), (5, 'sobota'), (6, 'niedziela'),
        ],
        widget=forms.CheckboxSelectMultiple,
        help_text='Zaznacz dni, w które możesz prowadzić zajęcia.',
    )
    godzina_od = forms.ChoiceField(
        label='zajęcia od',
        choices=[(f'{h:02d}:00', f'{h:02d}:00') for h in range(6, 24)],
        initial='16:00',
    )
    godzina_do = forms.ChoiceField(
        label='zajęcia do',
        choices=[(f'{h:02d}:00', f'{h:02d}:00') for h in range(7, 25)],
        initial='20:00',
    )
    przerwa = forms.ChoiceField(
        label='przerwa między zajęciami',
        choices=[(5, '5 minut'), (10, '10 minut'), (15, '15 minut'),
                 (20, '20 minut'), (30, '30 minut')],
        initial=10,
        help_text='Np. przy 10 min przerwy i godzinach 16–20: 16:00, 17:10, 18:20, 19:30.',
    )
    od_kiedy = forms.DateField(
        label='obowiązuje od dnia',
        widget=forms.DateInput(attrs={'type': 'date'}),
        help_text='Dyspozycyjność zostanie wpisana w kalendarz od tego dnia.',
    )
    do_kiedy = forms.DateField(
        label='obowiązuje do dnia',
        widget=forms.DateInput(attrs={'type': 'date'}),
        help_text='Do tego dnia (włącznie) można zapisywać zajęcia.',
    )

    def clean(self):
        dane = super().clean()
        od = dane.get('godzina_od')
        do_ = dane.get('godzina_do')
        if od and do_ and od >= do_:
            raise forms.ValidationError('Godzina „od” musi być wcześniejsza niż „do”.')
        return dane

class FormularzLekcjiCyklicznej(forms.Form):
    """Umawianie lekcji cyklicznej ucznia: dzień tygodnia, godzina i okres."""

    dzien_tygodnia = forms.ChoiceField(
        label='dzień tygodnia',
        choices=[
            (0, 'poniedziałek'), (1, 'wtorek'), (2, 'środa'), (3, 'czwartek'),
            (4, 'piątek'), (5, 'sobota'), (6, 'niedziela'),
        ],
    )
    godzina = forms.ChoiceField(
        label='godzina lekcji',
        choices=[(f'{h:02d}:{m:02d}', f'{h:02d}:{m:02d}') for h in range(6, 24) for m in (0, 30)],
    )
    od_kiedy = forms.DateField(
        label='od dnia',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    do_kiedy = forms.DateField(
        label='do dnia (np. koniec roku szkolnego)',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    link_meet = forms.CharField(
        label='link Google Meet',
        help_text='Stały link do spotkań Google Meet (meet.google.com/...). Wpisz raz — będzie ten sam przy każdej lekcji.',
    )
class FormularzLekcjiCyklicznej(forms.Form):
    """Umawianie lekcji cyklicznej ucznia: dzień tygodnia, godzina i okres."""

    dzien_tygodnia = forms.ChoiceField(
        label='dzień tygodnia',
        choices=[
            (0, 'poniedziałek'), (1, 'wtorek'), (2, 'środa'), (3, 'czwartek'),
            (4, 'piątek'), (5, 'sobota'), (6, 'niedziela'),
        ],
    )
    godzina = forms.ChoiceField(
        label='godzina lekcji',
        choices=[(f'{h:02d}:{m:02d}', f'{h:02d}:{m:02d}') for h in range(6, 24) for m in (0, 30)],
    )
    od_kiedy = forms.DateField(
        label='od dnia',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    do_kiedy = forms.DateField(
        label='do dnia (np. koniec roku szkolnego)',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    link_meet = forms.CharField(
        label='link Google Meet',
        help_text='Stały link do spotkań Google Meet (meet.google.com/...). Wpisz raz — będzie ten sam przy każdej lekcji.',
    )


class WierszPrzedmiotu(forms.Form):
    """Jeden wiersz formularza: przedmiot + poziom nauczania."""

    przedmiot = forms.ChoiceField(label='przedmiot', choices=WYBIERZ_PRZEDMIOT)
    poziom = forms.ChoiceField(label='poziom', choices=POZIOMY_NAUCZYCIELA)