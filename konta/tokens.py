from django.contrib.auth.tokens import PasswordResetTokenGenerator


class TokenResetuBezLastLogin(PasswordResetTokenGenerator):
    """Token resetu/ustawiania hasła, który NIE zawiera last_login w hash-u.

    Domyślny generator Django dołącza last_login do hasha — przez to każde
    udane logowanie natychmiast unieważniało link z maila („link nie działa”).
    Tutaj link działa pełne 3 dni (PASSWORD_RESET_TIMEOUT), nawet jeśli użytkownik
    zaloguje się w międzyczasie. Zmiana hasła NADAL unieważnia link (hasło jest
    w hash-u), więc link pozostaje jednorazowy."""

    def _make_hash_value(self, user, timestamp):
        email_field = user.get_email_field_name()
        email = getattr(user, email_field, "") or ""
        return f"{user.pk}{user.password}{timestamp}{email}"


token_generator_resetu = TokenResetuBezLastLogin()