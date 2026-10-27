# -*- coding: utf-8 -*-
"""Wysyła przypomnienia o lekcjach.

Zasada: przypomnienie leci 60 minut przed startem lekcji.
Jeśli lekcja została dodana później niż na 60 minut przed startem
(np. 20 minut przed), przypomnienie leci natychmiast po dodaniu.
Każda lekcja dostaje przypomnienie tylko raz — zapisujemy znacznik.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.utils import timezone

from konta.models import Lekcja, PrzypomnienieLekcji


class Command(BaseCommand):
    help = 'Wysyla przypomnienia o lekcjach (60 min przed startem; natychmiast dla dodanych krotko przed).'

    def handle(self, *args, **options):
        teraz = timezone.now()
        wyslane = 0
        okno = timedelta(minutes=60)
        for lekcja in Lekcja.objects.select_related('uczen').filter(data__gte=teraz, odwolana=False):
            if PrzypomnienieLekcji.objects.filter(lekcja=lekcja).exists():
                continue
            start = timezone.localtime(lekcja.data)
            if start - teraz <= okno:
                uczen = lekcja.uczen
                # pomijamy uczniów z archiwum (zakończona współpraca) i bez adresu
                if uczen.zakonczona:
                    PrzypomnienieLekcji.objects.create(lekcja=lekcja)
                    continue
                if not uczen.email_glowny:
                    continue
                tresc = (
                    'Dzień dobry,\n\n'
                    f'przypomnienie o zajęciach {uczen.przedmiot} dla {uczen.imie_ucznia}:\n\n'
                    f'{start:%d.%m.%Y o godz. %H:%M}\n'
                    f'Korepetytor: {uczen.korepetytor}\n'
                )
                if uczen.link_meet:
                    tresc += f'\nLink do zajęć: {uczen.link_meet}\n'
                tresc += '\nDo zobaczenia na zajęciach!\n'
                try:
                    send_mail(
                        f'Przypomnienie: zajęcia {uczen.przedmiot} — {start:%d.%m.%Y %H:%M}',
                        tresc,
                        None,
                        [uczen.email_glowny],
                    )
                except Exception:
                    continue
                PrzypomnienieLekcji.objects.create(lekcja=lekcja)
                wyslane += 1
        self.stdout.write(f'Przypomnienia wyslane: {wyslane}')