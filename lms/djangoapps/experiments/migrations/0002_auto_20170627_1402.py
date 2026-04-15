import django.utils.timezone
import model_utils.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('experiments', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExperimentKeyValue',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, verbose_name='created', editable=False)),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, verbose_name='modified', editable=False)),
                ('experiment_id', models.PositiveSmallIntegerField(verbose_name='Experiment ID', db_index=True)),
                ('key', models.CharField(max_length=255)),
                ('value', models.TextField()),
            ],
            options={
                'verbose_name': 'Experiment Data',
                'verbose_name_plural': 'Experiment Data',
            },
        ),
        migrations.AlterUniqueTogether(
            name='experimentkeyvalue',
            unique_together={('experiment_id', 'key')},
        ),
    ]
