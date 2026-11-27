# -*- coding: utf-8 -*-
"""Poprawki starych testów w konta/test_logowanie.py pod widok miesięczny
„Umówieni uczniowie” (etap 2). Uruchom w folderze projektu:
    .venv\\Scripts\\activate
    python fix_tests_etap2.py
Skrypt jest idempotentny — jak zmiana już jest, pomija ją.
"""
import sys
from pathlib import Path

REPO = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
p = Path(REPO) / 'konta' / 'test_logowanie.py'
s = p.read_text(encoding='utf-8')

zamiany = []

zamiany.append((
"""        self.client.force_login(self.pracownik)
        r = self.client.get('/lekcje/')
        self.assertContains(r, 'Kasia')
        self.assertNotContains(r, 'Ola')""",
"""        self.client.force_login(self.pracownik)
        r = self.client.get('/lekcje/?rok=2026&miesiac=10')
        self.assertContains(r, 'Kasia')
        self.assertNotContains(r, 'Ola')"""))

zamiany.append((
"""    def test_admin_widzi_wszystkich(self):
        self.client.force_login(self.admin)
        r = self.client.get('/lekcje/')
        self.assertContains(r, 'Kasia')""",
"""    def test_admin_widzi_wszystkich(self):
        self.client.force_login(self.admin)
        r = self.client.get('/lekcje/?rok=2026&miesiac=10')
        self.assertContains(r, 'Kasia')"""))

zamiany.append((
"""        self.client.force_login(self.pracownik)
        r = self.client.get('/lekcje/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Kasia')
        # stary kalendarz lekcji ucznia usunięty — adres daje teraz 404""",
"""        self.client.force_login(self.pracownik)
        r = self.client.get('/lekcje/?rok=2026&miesiac=10')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Kasia')
        # stary kalendarz lekcji ucznia usunięty — adres daje teraz 404"""))

zamiany.append((
'''    def test_lekcje_pokazuja_terminy_cyklicznych(self):
        # w zakładce Lekcje przy zajęciach cyklicznych widać najbliższe terminy (kiedy lekcja)
        self.uczen.zajecia_cykliczne = True
        self.uczen.save(update_fields=['zajecia_cykliczne'])
        d = date.today() + timedelta(days=3)
        Lekcja.objects.create(uczen=self.uczen, data=timezone.make_aware(datetime.combine(d, time(17, 0))))
        self.client.force_login(self.pracownik)
        r = self.client.get('/lekcje/')
        self.assertContains(r, 'Zajęcia cykliczne')
        self.assertContains(r, f'{d.day:02d}.{d.month:02d} 17:00')''',
'''    def test_lekcje_pokazuja_nowego_ucznia_w_sekcji_nowi(self):
        # zakładka „Umówieni uczniowie”: nowy uczeń (przyszła 1. lekcja) w sekcji „Nowi uczniowie”;
        # zajęcia cykliczne są całkowicie usunięte z tego widoku
        self.uczen.pierwsza_lekcja = timezone.make_aware(datetime.combine(date.today() + timedelta(days=3), time(17, 0)))
        self.uczen.zajecia_cykliczne = True
        self.uczen.save()
        Lekcja.objects.create(uczen=self.uczen, data=self.uczen.pierwsza_lekcja)
        self.client.force_login(self.pracownik)
        r = self.client.get('/lekcje/')
        self.assertContains(r, 'Umówieni uczniowie')
        self.assertContains(r, 'Nowi uczniowie')
        self.assertContains(r, 'Kasia')
        self.assertNotContains(r, 'Zajęcia cykliczne')'''))

zamiany.append((
"""        r = self.client.get('/lekcje/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Kasia Nowak')""",
"""        r = self.client.get('/lekcje/?rok=2026&miesiac=10')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Kasia Nowak')"""))

zamiany.append((
"""        self.client.force_login(self.umawiajacy)
        r = self.client.get('/lekcje/')
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'Zakończ współpracę')
        self.assertContains(r, 'Edytuj dane ucznia')
        self.client.force_login(self.szef)
        r = self.client.get('/lekcje/')
        self.assertContains(r, 'Zakończ współpracę')""",
"""        self.client.force_login(self.umawiajacy)
        r = self.client.get('/lekcje/?rok=2026&miesiac=10')
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'Zakończ współpracę')
        self.assertContains(r, 'Edytuj dane ucznia')
        self.client.force_login(self.szef)
        r = self.client.get('/lekcje/?rok=2026&miesiac=10')
        self.assertContains(r, 'Zakończ współpracę')"""))

zrobione = 0
for stary, nowy in zamiany:
    if nowy in s:
        continue  # już zastosowane
    assert s.count(stary) == 1, f'niejednoznaczny lub brak wzorca: {stary[:60]!r} (wystąpień: {s.count(stary)})'
    s = s.replace(stary, nowy)
    zrobione += 1

p.write_text(s, encoding='utf-8')
import ast
ast.parse(s)
print(f'OK: zastosowano {zrobione} poprawek testów (składnia OK)')