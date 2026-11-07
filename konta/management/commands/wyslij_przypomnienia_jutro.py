# -*- coding: utf-8 -*-
"""Zbiorcze przypomnienie „jutro” — jedna wiadomość na dzień przed zajęciami.

- RODZICE (oba maile ucznia): lista jutrzejszych lekcji ucznia w stylu
  „jutro o 8:00 z Janem — matematyka, 10:15 z Anną — fizyka” + link Meet,
- KOREPETYTOR (jego e-mail): lista jego jutrzejszych lekcji z nazwiskami uczniów.

Działa dla wszystkich lekcji z tabeli Lekcja (pierwsze i cykliczne).
Każda osoba dostaje digest tylko raz dziennie (znacznik PrzypomnienieDzienne).
CRON: codziennie rano, np. 8:00."""
from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.utils import timezone

from konta.models import Lekcja, Nauczyciel, PrzypomnienieDzienne, Uczen
from konta.views import GODZINA_LEKCJI, lekcja_jest_u_nauczyciela, zapisz_blad_maila


class Command(BaseCommand):
    help = 'Wysyła zbiorcze przypomnienie „jutro” rodzicom i korepetytorom (raz dziennie na osobę).'

    def handle(self, *args, **options):
        jutro = timezone.localtime(timezone.now()).date() + timedelta(days=1)
        start = timezone.make_aware(datetime.combine(jutro, time.min))
        koniec = start + timedelta(days=1)
        lekcje = list(
            Lekcja.objects.select_related('uczen', 'przedmiot_karta').filter(
                data__gte=start, data__lt=koniec, odwolana=False
            ).order_by('data')
        )
        if not lekcje:
            self.stdout.write(f'Brak zajęć na {jutro:%d.%m.%Y} — nic nie wysyłam.')
            return

        wyslane = 0

        # --- digest dla rodziców (grupowanie po uczniu) ---
        per_uczen = {}
        for lekcja in lekcje:
            per_uczen.setdefault(lekcja.uczen_id, []).append(lekcja)
        for uczen_id, lista in per_uczen.items():
            uczen = lista[0].uczen
            if uczen.zakonczona:
                continue
            adresy = [a for a in (uczen.email_glowny, uczen.email_dodatkowy) if a]
            if not adresy:
                continue
            if PrzypomnienieDzienne.objects.filter(typ='uczen', ref_id=uczen.id, data=jutro).exists():
                continue
            wiersze = []
            linki = []
            for lekcja in lista:
                start_l = timezone.localtime(lekcja.data)
                if lekcja.przedmiot_karta_id:
                    karta = lekcja.przedmiot_karta
                    przedmiot = karta.przedmiot
                    kto = karta.korepetytor
                    if karta.link_meet and karta.link_meet not in linki:
                        linki.append(karta.link_meet)
                else:
                    przedmiot = uczen.przedmiot
                    kto = uczen.korepetytor
                if uczen.link_meet and uczen.link_meet not in linki:
                    linki.append(uczen.link_meet)
                imie_korepetytora = (kto or '').split(' ')[0] or kto
                wiersze.append(
                    f'{start_l:%H:%M} — {przedmiot} z {imie_korepetytora}'
                )
            tresc = (
                'Dzień dobry,\n\n'
                f'jutro ({jutro:%d.%m.%Y}) {uczen.imie_ucznia} ma zajęcia:\n\n'
                + '\n'.join(wiersze) + '\n'
            )
            if linki:
                tresc += '\nLink do zajęć online:\n' + '\n'.join(linki) + '\n'
            tresc += '\nDo zobaczenia!\n'
            try:
                send_mail(
                    f'Jutro zajęcia {uczen.imie_ucznia} — {jutro:%d.%m.%Y} ({len(lista)} lekcji)',
                    tresc,
                    None,
                    adresy,
                )
            except Exception as e:
                zapisz_blad_maila('przypomnienie_jutro_rodzic', e)
                continue
            PrzypomnienieDzienne.objects.create(typ='uczen', ref_id=uczen.id, data=jutro)
            wyslane += 1

        # --- digest dla korepetytorów (grupowanie po nauczycielu) ---
        nauczyciele = list(Nauczyciel.objects.filter(aktywny=True))
        for n in nauczyciele:
            if not n.email:
                continue
            if PrzypomnienieDzienne.objects.filter(typ='nauczyciel', ref_id=n.id, data=jutro).exists():
                continue
            nazwa = f'{n.imie} {n.nazwisko}'.strip().lower()
            moje = [l for l in lekcje if lekcja_jest_u_nauczyciela(l, nazwa)]
            if not moje:
                continue
            wiersze = []
            for lekcja in moje:
                start_l = timezone.localtime(lekcja.data)
                koniec_l = start_l + timedelta(minutes=GODZINA_LEKCJI)
                przedmiot = (
                    lekcja.przedmiot_karta.przedmiot if lekcja.przedmiot_karta_id else lekcja.uczen.przedmiot
                )
                wiersze.append(
                    f'{start_l:%H:%M}–{koniec_l:%H:%M} — {lekcja.uczen.imie_ucznia} {lekcja.uczen.nazwisko_rodzica} ({przedmiot})'
                )
            tresc = (
                f'Dzień dobry {n.imie},\n\n'
                f'jutro ({jutro:%d.%m.%Y}) masz zajęcia:\n\n'
                + '\n'.join(wiersze) + '\n\n'
                'Do zobaczenia!\n'
            )
            try:
                send_mail(
                    f'Jutro Twoje zajęcia — {jutro:%d.%m.%Y} ({len(moje)} lekcji)',
                    tresc,
                    None,
                    [n.email],
                )
            except Exception as e:
                zapisz_blad_maila('przypomnienie_jutro_nauczyciel', e)
                continue
            PrzypomnienieDzienne.objects.create(typ='nauczyciel', ref_id=n.id, data=jutro)
            wyslane += 1

        self.stdout.write(f'Przypomnienia „jutro” wysłane: {wyslane} (dzień {jutro:%d.%m.%Y})')