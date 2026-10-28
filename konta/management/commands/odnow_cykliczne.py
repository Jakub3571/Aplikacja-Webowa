# -*- coding: utf-8 -*-
"""Odnowienie zajęć cyklicznych na kolejny miesiąc.

Zasada: dla każdego aktywnego ucznia z znacznikiem zajecia_cykliczne
sprawdzamy, czy ma lekcje w bazie. Jeśli tak — na podstawie powtarzających
się slotów (dzień tygodnia + godzina z ostatnich 4 tygodni) dopisujemy te
same terminy 30 dni naprzód. Kolizje u korepetytora i przerwy dnia są
pomijane (bez nadpisywania cudzych terminów).

Każda lekcja jest tworzona tylko raz (get_or_create po uczniu i dacie),
więc komendę można bezpiecznie odpalać codziennie.
"""
from datetime import date, datetime, timedelta

from django.core.management.base import BaseCommand
from django.db.models import Max
from django.utils import timezone

from konta.models import Lekcja, Uczen
from konta.views import GODZINA_LEKCJI, godzina_w_przerwie, kolizja_lekcji_nauczyciela, nauczyciel_z_korepetytora


class Command(BaseCommand):
    help = 'Przedluza zajecia cykliczne uczniow o kolejny miesiac (na podstawie powtarzajacych sie terminow).'

    def handle(self, *args, **options):
        dzis = date.today()
        utworzone = 0
        pominiete = 0
        uczniowie_aktualizowani = 0

        uczniowie = Uczen.objects.filter(zakonczona=False, zajecia_cykliczne=True)
        for uczen in uczniowie:
            ostatnia = Lekcja.objects.filter(uczen=uczen).aggregate(Max('data'))['data__max']
            if not ostatnia:
                continue
            ostatnia_local = timezone.localtime(ostatnia)
            # horizon: 90 dni naprzód od najpóźniejszej lekcji (lekcje "na zawsze"
            # są przedłużane automatycznie, gdy tylko komenda uruchomi się z CRON-a)
            granica = ostatnia_local.date() + timedelta(days=90)
            if granica <= dzis:
                granica = dzis + timedelta(days=90)

            # wzorce slotów: (dzień tygodnia, godzina) z ostatnich 4 tygodni przed ostatnią lekcją
            poczatek_okna = ostatnia_local.date() - timedelta(days=28)
            wzorce = set()
            for lekcja in Lekcja.objects.filter(uczen=uczen, data__gte=poczatek_okna):
                start = timezone.localtime(lekcja.data)
                wzorce.add((start.weekday(), start.time()))

            nauczyciel = nauczyciel_z_korepetytora(uczen.korepetytor)
            nowy = False
            for dzien in [ostatnia_local.date() + timedelta(days=i) for i in range(1, 91)]:
                for dzien_tygodnia, godzina in wzorce:
                    if dzien.weekday() != dzien_tygodnia or dzien > granica:
                        continue
                    data_lekcji = timezone.make_aware(datetime.combine(dzien, godzina))
                    if Lekcja.objects.filter(uczen=uczen, data=data_lekcji).exists():
                        continue
                    if nauczyciel:
                        if kolizja_lekcji_nauczyciela(nauczyciel, dzien, datetime.combine(dzien, godzina)):
                            pominiete += 1
                            continue
                        if godzina_w_przerwie(nauczyciel, dzien, godzina):
                            pominiete += 1
                            continue
                    Lekcja.objects.create(uczen=uczen, data=data_lekcji)
                    nowy = True
                    utworzone += 1
            if nowy:
                uczniowie_aktualizowani += 1

        self.stdout.write(self.style.SUCCESS(
            f'Odnowienie cyklicznych: dopisano {utworzone} lekcji '
            f'({uczniowie_aktualizowani} uczniow), pominięto {pominiete} kolizji/przerw.'
        ))