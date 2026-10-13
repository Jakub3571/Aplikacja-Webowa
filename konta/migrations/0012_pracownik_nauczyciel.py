from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('konta', '0011_nauczyciel_aktywny'),
    ]

    operations = [
        migrations.AddField(
            model_name='pracownik',
            name='nauczyciel',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='konto', to='konta.nauczyciel', verbose_name='nauczyciel'),
        ),
    ]