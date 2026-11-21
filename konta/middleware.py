"""Middleware panelu: przeglądarka nigdy nie keszuje stron HTML."""


class BezCacheHTML:
    """Dla każdej odpowiedzi HTML ustawia nagłówki no-cache — po wdrożeniu
    użytkownik od razu widzi nową wersję strony (bez Ctrl+F5)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if 'text/html' in response.get('Content-Type', ''):
            response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response['Pragma'] = 'no-cache'
            response['Expires'] = '0'
            response['Content-Security-Policy'] = (
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
                "form-action 'self'"
            )

        return response

