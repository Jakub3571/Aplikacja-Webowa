from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('konta', '0013_nauczyciel_przerwa'),
    ]

    operations = [
        migrations.AddField(
            model_name='uczen',
            name='umowil',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='umowieni_uczniowie', to='konta.pracownik', verbose_name='pracownik, który umówił'),
        ),
        migrations.AddField(
            model_name='uczen',
            name='link_meet',
            field=models.CharField(blank=True, max_length=200, verbose_name='stały link Google Meet'),
        ),
    ]
    