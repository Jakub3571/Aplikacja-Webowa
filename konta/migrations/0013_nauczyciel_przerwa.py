from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('konta', '0012_pracownik_nauczyciel'),
    ]

    operations = [
        migrations.AddField(
            model_name='nauczyciel',
            name='przerwa',
            field=models.PositiveIntegerField(default=0, help_text='Ile minut przerwy nauczyciel potrzebuje między zajęciami.', verbose_name='przerwa między zajęciami (minuty)'),
        ),
        migrations.AddField(
            model_name='nauczyciel',
            name='dyspozycyjnosc_uzupełniona',
            field=models.BooleanField(default=False, verbose_name='dyspozycyjność uzupełniona'),
        ),
    ]