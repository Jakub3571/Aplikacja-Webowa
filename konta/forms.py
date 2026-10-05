"""Formularze: logowanie, zmiana hasła, dodawanie ucznia i nauczyciela."""
from datetime import datetime, time

from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm

from .models import PRZEDMIOTY, Nauczyciel, Uczen

WYBIERZ_PRZEDMIOT = [('', '— wybierz przedmiot —')] + list(PRZEDMIOTY)

POZIOMY = [
    ('', '— wybierz poziom —'),
    ('szkoła średnia - podstawa', 'szkoła średnia - podstawa'),
    ('szkoła średnia - rozszerzenie', 'szkoła średnia - rozszerzenie'),
    ('studia', 'studia'),
]

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

    def __init__(self, *args, przedmiot=None, **kwargs):
        super().__init__(*args, **kwargs)
        nauczyciele = Nauczyciel.objects.all()
        if przedmiot:
            nauczyciele = nauczyciele.filter(przedmiot=przedmiot)
        else:
            nauczyciele = Nauczyciel.objects.none()
        self.fields['korepetytor'] = forms.ChoiceField(
            label='korepetytor',
            choices=[('', '— najpierw wybierz przedmiot —')]
            + [(f'{n.imie} {n.nazwisko}', f'{n.imie} {n.nazwisko}') for n in nauczyciele],
            help_text='Nauczyciel prowadzący lekcje — lista zależna od wybranego przedmiotu',
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


class FormularzNauczyciela(forms.ModelForm):
    """Formularz dodawania nauczyciela."""

    przedmiot = forms.ChoiceField(
        label='przedmiot',
        choices=WYBIERZ_PRZEDMIOT,
    )
    stawka = forms.DecimalField(
        label='stawka',
        min_value=0,
        widget=forms.NumberInput(attrs={'min': '0', 'step': '0.01'}),
    )

    class Meta:
        model = Nauczyciel
        fields = ['imie', 'nazwisko', 'przedmiot', 'email', 'stawka']
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