"""Rejestracja modeli w panelu administracyjnym Django."""

from django.contrib import admin

from .models import Dostepnosc, Nauczyciel, Pracownik, PrzedmiotNauczyciela, Uczen


@admin.register(Pracownik)
class PracownikAdmin(admin.ModelAdmin):
    """Ustawienia widoku pracowników w panelu admina."""

    list_display = ('username', 'first_name', 'last_name', 'email', 'stanowisko', 'is_active')
    search_fields = ('username', 'first_name', 'last_name', 'stanowisko')
    list_filter = ('is_active', 'stanowisko')


class PrzedmiotNauczycielaInline(admin.TabularInline):
    """Przedmioty nauczyciela widoczne bezpośrednio w jego karcie."""

    model = PrzedmiotNauczyciela
    extra = 1


class DostepnoscInline(admin.TabularInline):
    """Terminy dostępności widoczne bezpośrednio w karcie nauczyciela."""

    model = Dostepnosc
    extra = 1


@admin.register(Nauczyciel)
class NauczycielAdmin(admin.ModelAdmin):
    """Ustawienia widoku nauczycieli w panelu admina."""

    list_display = ('imie', 'nazwisko', 'email', 'aktywny')
    search_fields = ('imie', 'nazwisko', 'email')
    list_filter = ('aktywny',)
    inlines = [PrzedmiotNauczycielaInline, DostepnoscInline]


@admin.register(Dostepnosc)
class DostepnoscAdmin(admin.ModelAdmin):
    """Ustawienia widoku dostępności w panelu admina."""

    list_display = ('nauczyciel', 'data_od', 'data_do', 'godzina_od', 'godzina_do')
    list_filter = ('nauczyciel', 'data_od')


@admin.register(Uczen)
class UczenAdmin(admin.ModelAdmin):
    """Ustawienia widoku uczniów w panelu admina."""

    list_display = ('imie_ucznia', 'nazwisko_rodzica', 'przedmiot', 'poziom', 'korepetytor', 'pierwsza_lekcja')
    search_fields = ('imie_ucznia', 'nazwisko_rodzica', 'rodzic', 'przedmiot')
    list_filter = ('przedmiot', 'poziom', 'korepetytor')