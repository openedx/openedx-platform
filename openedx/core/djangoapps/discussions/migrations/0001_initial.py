import django.db.models.deletion
import django.utils.timezone
import jsonfield.encoder
import jsonfield.fields
import model_utils.fields
import opaque_keys.edx.django.models
import simple_history.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('lti_consumer', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='HistoricalDiscussionsConfiguration',
            fields=[
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, editable=False, verbose_name='created')),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, editable=False, verbose_name='modified')),
                ('context_key', opaque_keys.edx.django.models.LearningContextKeyField(db_index=True, max_length=255, verbose_name='Learning Context Key')),
                ('enabled', models.BooleanField(default=True, help_text='If disabled, the discussions in the associated learning context/course will be disabled.')),
                ('plugin_configuration', jsonfield.fields.JSONField(blank=True, default={}, dump_kwargs={'cls': jsonfield.encoder.JSONEncoder, 'separators': (',', ':')}, help_text='The plugin configuration data for this context/provider.', load_kwargs={})),
                ('provider_type', models.CharField(help_text="The discussion tool/provider's id", max_length=100, verbose_name='Discussion provider')),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField()),
                ('history_change_reason', models.CharField(max_length=100, null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
                ('history_user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('lti_configuration', models.ForeignKey(blank=True, db_constraint=False, help_text='The LTI configuration data for this context/provider.', null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='lti_consumer.LtiConfiguration')),
            ],
            options={
                'verbose_name': 'historical discussions configuration',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': 'history_date',
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name='DiscussionsConfiguration',
            fields=[
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, editable=False, verbose_name='created')),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, editable=False, verbose_name='modified')),
                ('context_key', opaque_keys.edx.django.models.LearningContextKeyField(db_index=True, max_length=255, primary_key=True, serialize=False, unique=True, verbose_name='Learning Context Key')),
                ('enabled', models.BooleanField(default=True, help_text='If disabled, the discussions in the associated learning context/course will be disabled.')),
                ('plugin_configuration', jsonfield.fields.JSONField(blank=True, default={}, dump_kwargs={'cls': jsonfield.encoder.JSONEncoder, 'separators': (',', ':')}, help_text='The plugin configuration data for this context/provider.', load_kwargs={})),
                ('provider_type', models.CharField(help_text="The discussion tool/provider's id", max_length=100, verbose_name='Discussion provider')),
                ('lti_configuration', models.ForeignKey(blank=True, help_text='The LTI configuration data for this context/provider.', null=True, on_delete=django.db.models.deletion.SET_NULL, to='lti_consumer.LtiConfiguration')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
