"""Adresy (URL-e) całego projektu."""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from konta import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.logowanie, name='logowanie'),
    path('wyloguj/', views.wyloguj, name='wyloguj'),
    path(
        'reset-hasla/',
        auth_views.PasswordResetView.as_view(
            template_name='konta/reset_hasla.html',
            subject_template_name='konta/mail_reset_temat.txt',
            email_template_name='konta/mail_reset_tresc.txt',
            success_url='/reset-hasla/wyslane/',
        ),
        name='reset_hasla',
    ),
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
    path('nauczyciele/', views.nauczyciele, name='nauczyciele'),
    path('nauczyciele/<int:nauczyciel_id>/archiwum/', views.archiwizuj_nauczyciela, name='archiwizuj_nauczyciela'),
    path('nauczyciele/<int:nauczyciel_id>/przywroc/', views.przywroc_nauczyciela, name='przywroc_nauczyciela'),
    path('nauczyciele/<int:nauczyciel_id>/umowa/', views.przelacz_umowe, name='przelacz_umowe'),
    path('nauczyciele/<int:nauczyciel_id>/kalendarz/', views.kalendarz_nauczyciela, name='kalendarz_nauczyciela'),
    path('terminy/<int:termin_id>/usun/', views.usun_termin, name='usun_termin'),
    path('lekcje/', views.lekcje, name='lekcje'),
    path('lekcje/<int:uczen_id>/kalendarz/', views.kalendarz_lekcji, name='kalendarz_lekcji'),
    path('platnosci/', views.platnosci, name='platnosci'),
    path('zmiana-hasla/', views.zmiana_hasla, name='zmiana_hasla'),
    path('uzytkownicy/', views.uzytkownicy, name='uzytkownicy'),
    path('uzytkownicy/<int:user_id>/przelacz-szefa/', views.przelacz_szefa, name='przelacz_szefa'),
]

handler403 = 'konta.views.brak_dostepu'