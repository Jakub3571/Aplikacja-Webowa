# -*- coding: utf-8 -*-
"""Raz w tygodniu (w niedzielę) wysyła najnowszy backup bazy mailem
na adresy z EMAILE_POWIADOMIEN — kopia 'do szuflady' poza serwerem.

Uruchamiane codziennie cronem; mail leci tylko w niedziele.
"""
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.mail import EmailMessage

from konta.models import Uczen


class Command(BaseCommand):
    help = 'Wysyła najnowszy backup bazy mailem (raz w tygodniu, w niedzielę).'

    def handle(self, *args, **options):
        if date.today().weekday() != 6:  # 6 = niedziela
            self.stdout.write('Dziś nie niedziela — pomijam.')
            return

        katalog = Path(settings.BASE_DIR) / 'backupy'
        kopie = sorted(katalog.glob('backup_*.sqlite3'))
        if not kopie:
            self.stdout.write('Brak plików backupu — najpierw uruchom backup_bazy.')
            return
        plik = kopie[-1]  # najnowszy

        odbiorcy = list(getattr(settings, 'EMAILE_POWIADOMIEN', []))
        if not odbiorcy:
            self.stdout.write('Brak odbiorców (EMAILE_POWIADOMIEN puste).')
            return

        liczba_uczniow = Uczen.objects.count()
        try:
            mail = EmailMessage(
                subject=f'Backup bazy — {plik.name}',
                body=(
                    'W załączniku najnowsza kopia zapasowa bazy Panelu pracowniczego.\n'
                    f'Plik: {plik.name}\n'
                    f'Rozmiar: {plik.stat().st_size / 1024:.0f} KB\n'
                    f'Uczniów w bazie: {liczba_uczniow}\n\n'
                    'Zalecenie: zapisz załącznik na dysku (np. folder "Backupy firmy")\n'
                    'i trzymaj przynajmniej 4 ostatnie kopie.\n\n'
                    'Pozdrawiamy,\nPanel pracowniczy'
                ),
                to=odbiorcy,
            )
            mail.attach_file(str(plik))
            mail.send()
            self.stdout.write(f'Backup {plik.name} wysłany do: {odbiorcy}')
        except Exception as e:
            log = Path(settings.BASE_DIR) / 'logi_maili.txt'
            with open(log, 'a', encoding='utf-8') as f:
                f.write(f'{date.today()} | backup_mailem | {e}\n')
            self.stdout.write(f'BLAD WYSYLKI: {e}')