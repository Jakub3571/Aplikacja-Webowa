from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('konta', '0014_uczen_umowil_link_meet'),
    ]

    operations = [
        migrations.AddField(
            model_name='przedmiotnauczyciela',
            name='stawka',
            field=models.DecimalField(decimal_places=2, default=0, help_text='Individualna stawka za ten przedmiot i poziom.', max_digits=6, verbose_name='stawka za godzinę (zł)'),
        ),
    ]