from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('konta', '0019_rename_dyspozycyjnosc_uzupelniona_nauczyciel_dyspozycyjnosc_uzupelniona_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='lekcja',
            name='odwolana',
            field=models.BooleanField(default=False, verbose_name='zajęcia odwołane'),
        ),
    ]