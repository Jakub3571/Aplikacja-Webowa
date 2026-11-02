# -*- coding: utf-8 -*-
"""Ostatniego dnia miesiąca wysyła przypomnienie o przygotowaniu raportu płatności.

Komenda jest uruchamiana codziennie (cron), ale mail leci tylko wtedy,
gdy następny dzień jest pierwszym dniem miesiąca (czyli dziś jest ostatni).
"""
from datetime import date, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.utils import timezone

from konta.models import Uczen


class Command(BaseCommand):
    help = 'Ostatniego dnia miesiąca wysyła e-mail z przypomnieniem o raporcie płatności.'

    def handle(self, *args, **options):
        dzis = date.today()
        jutro = dzis + timedelta(days=1)
        if jutro.day != 1:
            # nie jest to ostatni dzień miesiąca — cisza
            self.stdout.write('Dziś nie jest ostatnim dniem miesiąca — pomijam.')
            return

        odbiorcy = list(getattr(settings, 'EMAILE_POWIADOMIEN', []))
        if not odbiorcy:
            self.stdout.write('Brak odbiorców (EMAILE_POWIADOMIEN jest puste).')
            return

        # krótkie podsumowanie dla biura: co czeka na rozliczenie
        do_rozliczenia = Uczen.objects.filter(zaplacone=True, zakonczona=False).count()
        nowi_w_miesiacu = Uczen.objects.filter(
            dodano__year=dzis.year,
            dodano__month=dzis.month,
        ).count()

        tresc = (
            'Dzień dobry,\n\n'
            f'dziś ostatni dzień miesiąca ({dzis:%d.%m.%Y}) — czas na raport płatności.\n\n'
            'Podsumowanie z aplikacji:\n'
            f'— pozycje oznaczone jako opłacone (czekają na wysłanie raportu): {do_rozliczenia}\n'
            f'— nowych uczniów dodanych w tym miesiącu: {nowi_w_miesiacu}\n\n'
            'Co zrobić:\n'
            '1. Zaloguj się do Panelu pracowniczego → zakładka „Płatności".\n'
            '2. Sprawdź pozycje „Zapłacone" i rozliczenia korepetytorów.\n'
            '3. Wyślij/pobierz raport i wypłać należności.\n\n'
            'Pozdrawiamy,\nPanel pracowniczy'
        )
        try:
            send_mail(
                f'[{dzis:%B %Y}] Przypomnienie: raport płatności na koniec miesiąca',
                tresc,
                None,
                odbiorcy,
            )
            self.stdout.write(f'Przypomnienie o raporcie wysłano do: {odbiorcy}')
        except Exception as e:
            from pathlib import Path
            import datetime
            plik = Path(settings.BASE_DIR) / 'logi_maili.txt'
            with open(plik, 'a', encoding='utf-8') as f:
                f.write(
                    f'{datetime.datetime.now():%Y-%m-%d %H:%M:%S} | '
                    f'przypomnienie_raport | {e}\n'
                )
            self.stdout.write(f'BLAD WYSYLKI: {e}')