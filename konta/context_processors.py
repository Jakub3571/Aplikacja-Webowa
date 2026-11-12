from .models import Zgloszenie


def zgloszenia_licznik(request):
    """Liczba aktywnych zgłoszeń do czerwonego badge'a w nagłówku — tylko szef/koordynator."""
    u = getattr(request, 'user', None)
    if not (u and u.is_authenticated):
        return {'ZGŁOSZENIA_AKTYWNE': None}
    czy_szef = u.is_superuser or (u.username or '').strip().lower() == 'admin'
    czy_koord = (u.stanowisko or '').strip().lower() == 'koordynator'
    if czy_szef or czy_koord:
        return {'ZGŁOSZENIA_AKTYWNE': Zgloszenie.objects.filter(zrealizowane=False).count()}
    return {'ZGŁOSZENIA_AKTYWNE': None}