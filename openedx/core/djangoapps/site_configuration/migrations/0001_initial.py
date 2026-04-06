import django.utils.timezone
import jsonfield.fields
import model_utils.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sites', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='SiteConfiguration',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('values', jsonfield.fields.JSONField(blank=True)),
                ('site', models.OneToOneField(related_name='configuration', to='sites.Site', on_delete=models.CASCADE)),
            ],
        ),
        migrations.CreateModel(
            name='SiteConfigurationHistory',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, verbose_name='created', editable=False)),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, verbose_name='modified', editable=False)),
                ('values', jsonfield.fields.JSONField(blank=True)),
                ('site', models.ForeignKey(related_name='configuration_histories', to='sites.Site', on_delete=models.CASCADE)),
            ],
            options={
                'ordering': ('-modified', '-created'),
                'abstract': False,
                'get_latest_by': 'modified',
            },
        ),
    ]
