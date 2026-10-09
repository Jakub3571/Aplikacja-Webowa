from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('konta', '0010_przedmioty_nauczyciela'),
    ]

    operations = [
        migrations.AddField(
            model_name='nauczyciel',
            name='aktywny',
            field=models.BooleanField(default=True, verbose_name='aktywny (współpracuje)'),
        ),
    ]