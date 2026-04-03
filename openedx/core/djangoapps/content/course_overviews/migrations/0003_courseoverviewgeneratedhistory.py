import django.utils.timezone
import model_utils.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('course_overviews', '0002_add_course_catalog_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='CourseOverviewGeneratedHistory',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, verbose_name='created', editable=False)),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, verbose_name='modified', editable=False)),
                ('num_courses', models.IntegerField(null=True)),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
