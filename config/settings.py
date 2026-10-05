"""
Główna konfiguracja projektu Django "firma_panel".
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# --- Konfiguracja środowiska: dev (lokalnie) vs produkcja (serwer) ---
# Na serwerze ustaw zmienną środowiskową DJANGO_DEBUG=0, aby włączyć tryb
# produkcyjny. Lokalnie (bez zmiennej) zostaje tryb deweloperski.
import os

DEBUG = os.environ.get('DJANGO_DEBUG', '1') == '1'

# SECRET_KEY: na produkcji czytany ze zmiennej środowiskowej DJANGO_SECRET_KEY
# (nowy, losowy klucz wygenerowany dla serwera). Lokalnie zostaje klucz dev.
SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-klucz-deweloperski-do-testow-lokalnych'
)

# ALLOWED_HOSTS: na produkcji podaj adres strony w zmiennej DJANGO_ALLOWED_HOSTS,
# np. "mojastrona.pl,www.mojastrona.pl".
if DEBUG:
    ALLOWED_HOSTS = ['localhost', '127.0.0.1', 'testserver']
else:
    ALLOWED_HOSTS = [
        h.strip() for h in os.environ.get('DJANGO_ALLOWED_HOSTS', '').split(',') if h.strip()
    ]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'konta',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}
# --- Bezpieczeństwo na produkcji (HTTPS) ---
# Aktywne tylko gdy DJANGO_DEBUG=0: wymusza HTTPS i bezpieczne ciasteczka.
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 3600
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'pl'
TIME_ZONE = 'Europe/Warsaw'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']

AUTH_USER_MODEL = 'konta.Pracownik'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Dokąd przekierowuje po udanym logowaniu i skąd wymusza logowanie
LOGIN_REDIRECT_URL = 'pulpit'
LOGIN_URL = 'logowanie'
LOGOUT_REDIRECT_URL = 'logowanie'

# Poczta: na czas developmentu maile wypisywane są w terminalu (backend konsolowy).
# Na produkcji zmień EMAIL_BACKEND na 'django.core.mail.backends.smtp.EmailBackend'
# i uzupełnij EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD.
# Na produkcji wystarczy ustawić zmienną środowiskową DJANGO_EMAIL=smtp
# oraz EMAIL_HOST_USER / EMAIL_HOST_PASSWORD (np. Gmail z hasłem aplikacji).
if os.environ.get('DJANGO_EMAIL') == 'smtp':
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
    EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
    EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
    EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
    EMAIL_USE_TLS = True
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
DEFAULT_FROM_EMAIL = os.environ.get('DJANGO_EMAIL_FROM', 'panel@example.com')

# Adresy, na które idzie powiadomienie o nowym uczniu (można wpisać kilka).
EMAILE_POWIADOMIEN = [
    'biuro@example.com',
]