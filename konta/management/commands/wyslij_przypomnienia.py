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
              # przypomnienie idzie na oba maile rodzica (główny + dodatkowy)
                adresy = [a for a in (uczen.email_glowny, uczen.email_dodatkowy) if a]
                if not adresy:
                  continue
                # przedmiot i link Meet: z karty przedmiotu dodatkowego, gdy lekcja do niej należy
                if lekcja.przedmiot_karta_id:
                    karta = lekcja.przedmiot_karta
                    przedmiot_info = karta.przedmiot
                    korepetytor_info = karta.korepetytor
                    link_info = karta.link_meet or uczen.link_meet
                else:
                    przedmiot_info = uczen.przedmiot
                    korepetytor_info = uczen.korepetytor
                    link_info = uczen.link_meet
                tresc = (
                    'Dzień dobry,\n\n'
                    f'przypomnienie o zajęciach {przedmiot_info} dla {uczen.imie_ucznia}:\n\n'
                    f'{start:%d.%m.%Y o godz. %H:%M}\n'
                    f'Korepetytor: {korepetytor_info}\n'
                )
                if link_info:
                    tresc += f'\nLink do zajęć: {link_info}\n'
                tresc += '\nDo zobaczenia na zajęciach!\n'
                try:
                    send_mail(
                        f'Przypomnienie: zajęcia {przedmiot_info} — {start:%d.%m.%Y %H:%M}',
                        tresc,
                        None,
                        adresy,
                    )
                except Exception as e:
                    from pathlib import Path
                    from django.conf import settings
                    import datetime
                    plik = Path(settings.BASE_DIR) / 'logi_maili.txt'
                    with open(plik, 'a', encoding='utf-8') as f:
                        f.write(
                            f'{datetime.datetime.now():%Y-%m-%d %H:%M:%S} | '
                            f'przypomnienie_o_lekcji | {e}\n'
                        )
                    continue
                PrzypomnienieLekcji.objects.create(lekcja=lekcja)
                wyslane += 1
        self.stdout.write(f'Przypomnienia wyslane: {wyslane}')