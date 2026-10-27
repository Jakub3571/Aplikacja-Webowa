from django.db import migrations


def przepisz_stary_poziom(apps, schema_editor):
    """Stary zapis 'srednia rozszerzenie + studia' przepisujemy na 'srednia rozszerzenie'."""
    Uczen = apps.get_model('konta', 'Uczen')
    PrzedmiotNauczyciela = apps.get_model('konta', 'PrzedmiotNauczyciela')
    Uczen.objects.filter(poziom='srednia rozszerzenie + studia').update(poziom='srednia rozszerzenie')
    PrzedmiotNauczyciela.objects.filter(poziom='srednia rozszerzenie + studia').update(poziom='srednia rozszerzenie')


def cofnij(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('konta', '0021_uczen_zajecia_cykliczne'),
    ]

    operations = [
        migrations.RunPython(przepisz_stary_poziom, cofnij),
    ]