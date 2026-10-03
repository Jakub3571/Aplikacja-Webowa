"""Modele aplikacji konta."""
from datetime import date

from django.contrib.auth.models import AbstractUser
from django.db import models

PRZEDMIOTY = [
    ('matematyka', 'matematyka'),
    ('fizyka', 'fizyka'),
    ('angielski', 'angielski'),
    ('chemia', 'chemia'),
    ('biologia', 'biologia'),
    ('geografia', 'geografia'),
    ('polski', 'polski'),
    ('historia', 'historia'),
]


class Pracownik(AbstractUser):
    """Rozszerzenie standardowego użytkownika Django o dane pracownika."""

    stanowisko = models.CharField('stanowisko', max_length=100, blank=True)
    telefon = models.CharField('telefon', max_length=20, blank=True)

    class Meta:
        verbose_name = 'pracownik'
        verbose_name_plural = 'pracownicy'

    def __str__(self):
        return f'{self.get_full_name() or self.username}'


class Uczen(models.Model):
    """Karta ucznia z danymi lekcji, kontaktu do rodzica i notatkami."""

    pierwsza_lekcja = models.DateTimeField('data i godzina 1. lekcji')
    przedmiot = models.CharField('przedmiot', max_length=100)
    poziom = models.CharField('poziom', max_length=50)
    szkola = models.CharField('szkoła', max_length=150)
    imie_ucznia = models.CharField('imię ucznia', max_length=100)
    nazwisko_rodzica = models.CharField('nazwisko (rodzica)', max_length=100)
    rodzic = models.CharField('rodzic (imię i nazwisko)', max_length=150)
    telefon_glowny = models.CharField('telefon główny', max_length=20)
    email_glowny = models.EmailField('email główny')
    telefon_dodatkowy = models.CharField('telefon dodatkowy', max_length=20)
    email_dodatkowy = models.EmailField('email dodatkowy', blank=True)
    korepetytor = models.CharField('korepetytor', max_length=150)
    notatka_od_rodzica = models.TextField('notatka od rodzica', blank=True)
    kto_umowil = models.CharField('kto umówił', max_length=150)
    zaplacone = models.BooleanField('zapłacone', default=False)
    dodano = models.DateTimeField('data dodania', auto_now_add=True)

    class Meta:
        verbose_name = 'uczeń'
        verbose_name_plural = 'uczniowie'
        ordering = ['-pierwsza_lekcja']

    def __str__(self):
        return f'{self.imie_ucznia} {self.nazwisko_rodzica} — {self.przedmiot}'


class Nauczyciel(models.Model):
    """Karta nauczyciela/korepetytora."""

    imie = models.CharField('imię', max_length=100)
    nazwisko = models.CharField('nazwisko', max_length=100)
    przedmiot = models.CharField('przedmiot', max_length=100, choices=PRZEDMIOTY, default='')
    email = models.EmailField('email')
    dodano = models.DateTimeField('data dodania', auto_now_add=True)

    class Meta:
        verbose_name = 'nauczyciel'
        verbose_name_plural = 'nauczyciele'
        ordering = ['nazwisko']

    def __str__(self):
        return f'{self.imie} {self.nazwisko}'


class Dostepnosc(models.Model):
    """Termin, w którym nauczyciel może przyjąć ucznia."""

    nauczyciel = models.ForeignKey(
        Nauczyciel,
        on_delete=models.CASCADE,
        related_name='dostepnosci',
        verbose_name='nauczyciel',
    )
    data_od = models.DateField('data od', default=date(2026, 1, 1))
    data_do = models.DateField('data do', default=date(2026, 1, 1))
    godzina_od = models.TimeField('godzina od')
    godzina_do = models.TimeField('godzina do')

    class Meta:
        verbose_name = 'dostępność'
        verbose_name_plural = 'dostępności'
        ordering = ['data_od', 'godzina_od']

    def __str__(self):
        if self.data_od == self.data_do:
            return f'{self.nauczyciel}: {self.data_od:%d.%m.%Y} {self.godzina_od:%H:%M}–{self.godzina_do:%H:%M}'
        return f'{self.nauczyciel}: {self.data_od:%d.%m.%Y}–{self.data_do:%d.%m.%Y} {self.godzina_od:%H:%M}–{self.godzina_do:%H:%M}'


class RaportPlatnosci(models.Model):
    """Archiwum raportu płatności wysłanego e-mailem."""

    zawartosc = models.TextField('zawartość raportu')
    email_odbiorcy = models.EmailField('email odbiorcy')
    wyslal = models.CharField('wysłał', max_length=150)
    wyslano = models.DateTimeField('data wysłania', auto_now_add=True)

    class Meta:
        verbose_name = 'raport płatności'
        verbose_name_plural = 'raporty płatności'
        ordering = ['-wyslano']

    def __str__(self):
        return f'Raport z {self.wyslano:%d.%m.%Y %H:%M} — {self.email_odbiorcy}'