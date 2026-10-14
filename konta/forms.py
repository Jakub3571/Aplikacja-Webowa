"""Formularze: logowanie, zmiana hasła, dodawanie ucznia i nauczyciela."""

from datetime import datetime, time

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
    ('szkoła średnia - podstawa', 'szkoła średnia - podstawa'),
    ('szkoła średnia - rozszerzenie', 'szkoła średnia - rozszerzenie'),
    ('studia', 'studia'),
]

# Mapowanie poziomu ucznia na poziom nauczyciela: poziom "szkoła średnia - podstawa"
# wymaga nauczyciela z poziomem "podstawa", a "rozszerzenie" oraz "studia" — "rozszerzenie".
POZIOM_UCZEN_NA_NAUCZYCIELA = {
    'szkoła średnia - podstawa': 'podstawa',
    'szkoła średnia - rozszerzenie': 'rozszerzenie',
    'studia': 'rozszerzenie',
}

GODZINY_LEKCJI = [
    (f'{h:02d}:{m:02d}', f'{h:02d}:{m:02d}') for h in range(24) for m in (0, 30)
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
        nauczyciele = Nauczyciel.objects.none()
        if przedmiot:
            wpisane_przedmioty = PrzedmiotNauczyciela.objects.filter(przedmiot=przedmiot)
            if poziom:
                poziom_nauczyciela = POZIOM_UCZEN_NA_NAUCZYCIELA.get(poziom)
                if poziom_nauczyciela:
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
            help_text='Nauczyciel prowadzący lekcje — lista zależna od wybranego przedmiotu i poziomu',
        )
        for nazwa, pole in self.fields.items():
            pole.required = True
            pole.widget.attrs['required'] = True

    def clean(self):
        dane = super().clean()
        data = dane.get('data_pierwszej_lekcji')
        godzina = dane.get('godzina_pierwszej_lekcji')
        if data and godzina:
            godziny, minuty = godzina.split(':')
            czas = time(int(godziny), int(minuty))
            dane['pierwsza_lekcja'] = datetime.combine(data, czas)
        return dane

class FormularzUzytkownika(forms.ModelForm):
    """Formularz dodawania nowego użytkownika panelu — rola wybierana na górze.
    Dla nauczyciela: wiersze przedmiot + poziom + stawka."""

    rola = forms.ChoiceField(
        label='rola',
        choices=[
            ('pracownik', 'pracownik biura'),
            ('nauczyciel', 'nauczyciel'),
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # dla nauczyciela: budujemy wiersze przedmiot+poziom+stawka
        self.wiersze_przedmiotow = []
        if self.is_bound and hasattr(self.data, 'getlist'):
            przedmioty = self.data.getlist('w_przedmiot')
            poziomy = self.data.getlist('w_poziom')
            stawki = self.data.getlist('w_stawka')
            for idx, przedmiot in enumerate(przedmioty):
                if not przedmiot:
                    continue
                poziom = poziomy[idx] if idx < len(poziomy) else 'podstawa'
                stawka = stawki[idx] if idx < len(stawki) else '0'
                self.wiersze_przedmiotow.append({
                    'przedmiot': przedmiot,
                    'poziom': poziom,
                    'stawka': stawka,
                })
        if not self.wiersze_przedmiotow:
            self.wiersze_przedmiotow = [{'przedmiot': '', 'poziom': 'podstawa', 'stawka': ''}]
        self.fields_przedmioty = list(PRZEDMIOTY)

    class Meta:
        model = Pracownik
        fields = ['username', 'first_name', 'last_name', 'email', 'telefon']
        help_texts = {
            'username': 'Nazwa do logowania, np. j.kowalski',
            'email': 'Adres e-mail',
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


class FormularzNauczyciela(forms.ModelForm):
    """Formularz dodawania nauczyciela z wieloma przedmiotami i poziomami."""

    stawka = forms.DecimalField(
        label='stawka',
        min_value=0,
        widget=forms.NumberInput(attrs={'min': '0', 'step': '0.01'}),
    )

    class Meta:
        model = Nauczyciel
        fields = ['imie', 'nazwisko', 'email', 'stawka']
        help_texts = {
            'imie': 'Imię nauczyciela',
            'nazwisko': 'Nazwisko nauczyciela',
            'email': 'Adres e-mail do kontaktu',
        }
        error_messages = {
            'email': {'invalid': 'Wpisz poprawny adres e-mail.'},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nazwa, pole in self.fields.items():
            pole.required = True
            pole.widget.attrs['required'] = True
        dane = None
        if self.is_bound:
            if hasattr(self.data, 'getlist'):
                przedmioty = self.data.getlist('przedmioty')
                poziomy = self.data.getlist('poziomy')
            else:
                przedmioty = self.data.get('przedmioty') or []
                poziomy = self.data.get('poziomy') or []
                if isinstance(przedmioty, str):
                    przedmioty = [przedmioty]
                if isinstance(poziomy, str):
                    poziomy = [poziomy]
            dane = [
                {'przedmiot': p, 'poziom': poziomy[i] if i < len(poziomy) else 'podstawa'}
                for i, p in enumerate(przedmioty)
                if p
            ]
        self.wiersze = [
            WierszPrzedmiotu(dane or {'przedmiot': '', 'poziom': 'podstawa'})
        ] if not dane else [WierszPrzedmiotu(d) for d in dane]

    def clean(self):
        super().clean()
        bledy = []
        for i, wiersz in enumerate(self.wiersze, start=1):
            if not wiersz.is_valid() or wiersz.cleaned_data.get('przedmiot') == '':
                bledy.append(f'Wiersz {i}: wybierz przedmiot i poziom.')
        pary = set()
        for wiersz in self.wiersze:
            if wiersz.is_valid() and wiersz.cleaned_data.get('przedmiot'):
                para = (wiersz.cleaned_data['przedmiot'], wiersz.cleaned_data['poziom'])
                if para in pary:
                    bledy.append('Ten sam przedmiot i poziom jest wpisany dwa razy.')
                pary.add(para)
        if bledy:
            raise forms.ValidationError(bledy)
        return self.cleaned_data

    def zapisz(self):
        """Zapisuje nauczyciela i wszystkie jego przedmioty z poziomami."""
        nauczyciel = self.save()
        for wiersz in self.wiersze:
            if wiersz.is_valid() and wiersz.cleaned_data.get('przedmiot'):
                PrzedmiotNauczyciela.objects.get_or_create(
                    nauczyciel=nauczyciel,
                    przedmiot=wiersz.cleaned_data['przedmiot'],
                    poziom=wiersz.cleaned_data['poziom'],
                )
        return nauczyciel