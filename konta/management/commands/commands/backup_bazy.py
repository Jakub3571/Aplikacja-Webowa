from datetime import date

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Tworzy kopię zapasową bazy danych do katalogu backupy/ (data w nazwie pliku).'

    def handle(self, *args, **options):
        from django.conf import settings
        import shutil
        from pathlib import Path

        sciezka_bazy = Path(settings.BASE_DIR) / 'db.sqlite3'
        katalog_backupow = Path(settings.BASE_DIR) / 'backupy'
        katalog_backupow.mkdir(exist_ok=True)

        if not sciezka_bazy.exists():
            self.stdout.write(self.style.ERROR('Nie znaleziono pliku bazy (db.sqlite3).'))
            return

        nazwa = f'backup_{date.today():%Y-%m-%d}.sqlite3'
        cel = katalog_backupow / nazwa
        shutil.copy2(sciezka_bazy, cel)
        self.stdout.write(self.style.SUCCESS(f'Kopia zapasowa zapisana: {cel}'))