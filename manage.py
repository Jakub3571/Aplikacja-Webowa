#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    # Na serwerze (PythonAnywhere) sekrety są w ~/.env_aplikacji — wczytujemy je
    # tutaj, żeby komendy zarządzające (przypomnienia, backupy z crona) miały ten
    # sam env co aplikacja webowa (WSGI). Lokalnie pliku nie ma, więc bez zmian.
    _env = os.path.expanduser('~/.env_aplikacji')
    if os.path.isfile(_env):
        for _l in open(_env):
            _l = _l.strip()
            if '=' in _l and not _l.startswith('#'):
                _k, _v = _l.split('=', 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()