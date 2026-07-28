import django.utils.timezone
import model_utils.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('course_overviews', '0005_delete_courseoverviewgeneratedhistory'),
    ]

    operations = [
        migrations.CreateModel(
            name='CourseOverviewImageSet',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, verbose_name='created', editable=False)),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, verbose_name='modified', editable=False)),
                ('small_url', models.TextField(default='', blank=True)),
                ('large_url', models.TextField(default='', blank=True)),
                ('course_overview', models.OneToOneField(related_name='image_set', to='course_overviews.CourseOverview', on_delete=models.CASCADE)),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
