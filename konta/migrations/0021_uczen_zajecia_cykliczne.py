from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('konta', '0020_lekcja_odwolana'),
    ]

    operations = [
        migrations.AddField(
            model_name='uczen',
            name='zajecia_cykliczne',
            field=models.BooleanField(default=False, verbose_name='zajęcia cykliczne'),
        ),
    ]