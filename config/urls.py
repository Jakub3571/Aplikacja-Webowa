"""Adresy (URL-e) całego projektu."""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from konta import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.logowanie, name='logowanie'),
    path('wyloguj/', views.wyloguj, name='wyloguj'),
    path('reset-hasla/', views.reset_hasla, name='reset_hasla'),
    path(
        'reset-hasla/wyslane/',
        auth_views.PasswordResetDoneView.as_view(template_name='konta/reset_wyslane.html'),
        name='reset_hasla_wyslane',
    ),
    path(
        'reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='konta/reset_nowe_haslo.html',
            success_url='/reset-hasla/gotowe/',
        ),
        name='reset_hasla_potwierdz',
    ),
    path(
        'reset-hasla/gotowe/',
        auth_views.PasswordResetCompleteView.as_view(template_name='konta/reset_gotowe.html'),
        name='reset_hasla_gotowe',
    ),
    path('pulpit/', views.pulpit, name='pulpit'),
    path('uczniowie/', views.uczniowie, name='uczniowie'),
    path('nauczyciele/<int:nauczyciel_id>/kalendarz/', views.kalendarz_nauczyciela, name='kalendarz_nauczyciela'),
    path('nauczyciele/<int:nauczyciel_id>/kalendarz/dane/', views.kalendarz_dane, name='kalendarz_dane'),
    path('moja-dyspozycyjnosc/', views.kreator_dyspozycyjnosc, name='kreator_dyspozycyjnosc'),
    path('moj-tydzien/', views.moj_tydzien, name='moj_tydzien'),
    path('moj-tydzien/<int:nauczyciel_id>/', views.moj_tydzien, name='moj_tydzien_nauczyciela'),
    path('terminy/<int:termin_id>/usun/', views.usun_termin, name='usun_termin'),
    path('lekcje/', views.lekcje, name='lekcje'),
    path('uczniowie/<int:uczen_id>/lekcja-cykliczna/', views.umow_lekcje_cykliczna, name='lekcja_cykliczna'),
    path('lekcje/<int:uczen_id>/kalendarz/', views.kalendarz_lekcji, name='kalendarz_lekcji'),
    path('platnosci/', views.platnosci, name='platnosci'),
    path('zmiana-hasla/', views.zmiana_hasla, name='zmiana_hasla'),
    path('uzytkownicy/', views.uzytkownicy, name='uzytkownicy'),
    path('uzytkownicy/<int:user_id>/przelacz-szefa/', views.przelacz_szefa, name='przelacz_szefa'),
    path('uczniowie/<int:uczen_id>/edytuj/', views.edytuj_ucznia, name='edytuj_ucznia'),
    path('uczniowie/<int:uczen_id>/usun/', views.usun_ucznia, name='usun_ucznia'),
    path('uczniowie/<int:uczen_id>/zakoncz/', views.zakoncz_ucznia, name='zakoncz_ucznia'),
    path('uczniowie/<int:uczen_id>/przywroc/', views.przywroc_ucznia, name='przywroc_ucznia'),
    path('korepetytorzy/dane/', views.korepetytorzy_dane, name='korepetytorzy_dane'),
    path('archiwum/', views.archiwum_uczniow, name='archiwum'),
    path('raport-platnosci/', views.pobierz_raport, name='pobierz_raport'),
    path('nauczyciele/<int:nauczyciel_id>/zakoncz-wspolprace/', views.zakoncz_wspolprace, name='zakoncz_wspolprace'),
    path('nauczyciele/<int:nauczyciel_id>/przywroc-wspolprace/', views.przywroc_wspolprace, name='przywroc_wspolprace'),
    path('uzytkownicy/<int:user_id>/edytuj/', views.edytuj_uzytkownika, name='edytuj_uzytkownika'),
    path('uzytkownicy/<int:user_id>/konto/', views.przelacz_konto, name='przelacz_konto'),
]

handler403 = 'konta.views.brak_dostepu'