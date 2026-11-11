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
    'konta.middleware.BezCacheHTML',
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
                'konta.views.rola_context',
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
STATIC_ROOT = BASE_DIR / 'staticfiles'
def _wersja_statycznych():
    """Wersja statyków = najnowszy mtime plików w static/ i templates/ — zmienia się
    przy każdej edycji pliku (także bez commita), więc przeglądarka sama pobiera nowy CSS/JS."""
    najnowszy = 0
    for katalog in ('static', 'templates'):
        sciezka = os.path.join(BASE_DIR, katalog)
        for root, _dirs, files in os.walk(sciezka):
            for nazwa in files:
                try:
                    najnowszy = max(najnowszy, int(os.path.getmtime(os.path.join(root, nazwa))))
                except OSError:
                    pass
    if najnowszy:
        return str(najnowszy)
    try:
        import subprocess
        return subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], cwd=BASE_DIR, stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return '1'


STATIC_VERSION = os.environ.get('STATIC_VERSION') or _wersja_statycznych()

AUTH_USER_MODEL = 'konta.Pracownik'
# Adresy, z których wolno przesyłać formularze i zapytania AJAX (CSRF).
# Bez tego Django 4.x odrzuca POST-y z JavaScriptu (kalendarz) błędem 403.
CSRF_TRUSTED_ORIGINS = [
    'https://www.nowoczesnekorepetycje.pl',
]
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Dokąd przekierowuje po udanym logowaniu i skąd wymusza logowanie
LOGIN_REDIRECT_URL = 'pulpit'
LOGIN_URL = 'logowanie'
# Link do ustawienia/resetu hasła (z maila powitalnego i „nie pamiętam hasła”)
# jest ważny 3 dni. Po tym czasie pokazujemy osobną stronę „czas minął”
# (patrz ResetHaslaPotwierdzWidok w konta/views.py).
PASSWORD_RESET_TIMEOUT = 3 * 24 * 60 * 60  # 3 dni w sekundach

# --- Bezpieczeństwo na produkcji (HTTPS) ---
# Aktywne tylko gdy DJANGO_DEBUG=0.
# SECURE_SSL_REDIRECT — wymusza przejście z http:// na https://.
# SECURE_HSTS_* — informuje przeglądarki, że strona jest zawsze po HTTPS.
# CSRF_COOKIE_SECURE — ciasteczko CSRF wysyłane tylko po HTTPS.
# SESSION_COOKIE_SECURE — to samo dla ciasteczka sesji (klucz logowania).
# CSRF_COOKIE_HTTPONLY — JavaScript nie ma dostępu do tokena CSRF.
# SECURE_CONTENT_TYPE_NOSNIFF — przeglądarka nie zgaduje typu zawartości.
# X_FRAME_OPTIONS — strona nie może być osadzona w cudzej ramce (clickjacking).
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_PRELOAD = True
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    CSRF_COOKIE_HTTPONLY = False
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'

# Poczta: na czas developmentu maile wypisywane są w terminalu (backend konsolowy).
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
# Puste na czas odzyskiwania skrzynki biurowej.
# Gdy konto wróci (lub założymy nowe/płatne), wpisz tu adresy odbiorców powiadomień.
EMAILE_POWIADOMIEN = ['korepetycjenowoczesne@gmail.com']