import django.utils.timezone
import model_utils.fields
from django.db import migrations, models
from opaque_keys.edx.django.models import UsageKeyField

import openedx.core.djangoapps.content.block_structure.models


class Migration(migrations.Migration):

    dependencies = [
        ('block_structure', '0001_config'),
    ]

    operations = [
        migrations.CreateModel(
            name='BlockStructureModel',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, verbose_name='created', editable=False)),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, verbose_name='modified', editable=False)),
                ('data_usage_key', UsageKeyField(unique=True, max_length=255, verbose_name='Identifier of the data being collected.')),
                ('data_version', models.CharField(max_length=255, null=True, verbose_name='Version of the data at the time of collection.', blank=True)),
                ('data_edit_timestamp', models.DateTimeField(null=True, verbose_name='Edit timestamp of the data at the time of collection.', blank=True)),
                ('transformers_schema_version', models.CharField(max_length=255, verbose_name='Representation of the schema version of the transformers used during collection.')),
                ('block_structure_schema_version', models.CharField(max_length=255, verbose_name='Version of the block structure schema at the time of collection.')),
                ('data', models.FileField(max_length=500, upload_to=openedx.core.djangoapps.content.block_structure.models._path_name)),
            ],
            options={
                'db_table': 'block_structure',
            },
        ),
    ]
