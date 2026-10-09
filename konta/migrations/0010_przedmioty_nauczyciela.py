import django.db.models.deletion
from django.db import migrations, models


def przenies_przedmioty(apps, schema_editor):
    """Kopiuje stare przedmioty nauczycieli do nowej tabeli (jako poziom 'podstawa')."""
    Nauczyciel = apps.get_model('konta', 'Nauczyciel')
    PrzedmiotNauczyciela = apps.get_model('konta', 'PrzedmiotNauczyciela')
    for nauczyciel in Nauczyciel.objects.exclude(przedmiot='').exclude(przedmiot__isnull=True):
        PrzedmiotNauczyciela.objects.get_or_create(
            nauczyciel=nauczyciel,
            przedmiot=nauczyciel.przedmiot,
            poziom='podstawa',
        )


class Migration(migrations.Migration):

    dependencies = [
        ('konta', '0009_lekcja'),
    ]

    operations = [
        migrations.CreateModel(
            name='PrzedmiotNauczyciela',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('przedmiot', models.CharField(choices=[('matematyka', 'matematyka'), ('fizyka', 'fizyka'), ('angielski', 'angielski'), ('chemia', 'chemia'), ('biologia', 'biologia'), ('geografia', 'geografia'), ('polski', 'polski'), ('historia', 'historia')], max_length=100, verbose_name='przedmiot')),
                ('poziom', models.CharField(choices=[('podstawa', 'podstawa'), ('rozszerzenie', 'rozszerzenie')], max_length=50, verbose_name='poziom')),
                ('nauczyciel', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='przedmioty', to='konta.nauczyciel', verbose_name='nauczyciel')),
            ],
            options={
                'verbose_name': 'przedmiot nauczyciela',
                'verbose_name_plural': 'przedmioty nauczycieli',
                'ordering': ['przedmiot', 'poziom'],
                'constraints': [models.UniqueConstraint(fields=('nauczyciel', 'przedmiot', 'poziom'), name='unikalny_przedmiot_poziom_nauczyciela')],
            },
        ),
        migrations.RunPython(przenies_przedmioty, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='nauczyciel',
            name='przedmiot',
        ),
    ]