import django.utils.timezone
import jsonfield.fields
import model_utils.fields
from django.conf import settings
from django.db import migrations, models
from opaque_keys.edx.django.models import CourseKeyField, UsageKeyField


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Bookmark',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, verbose_name='created', editable=False)),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, verbose_name='modified', editable=False)),
                ('course_key', CourseKeyField(max_length=255, db_index=True)),
                ('usage_key', UsageKeyField(max_length=255, db_index=True)),
                ('_path', jsonfield.fields.JSONField(help_text='Path in course tree to the block', db_column='path')),
                ('user', models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=models.CASCADE)),
            ],
        ),
        migrations.CreateModel(
            name='XBlockCache',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, verbose_name='created', editable=False)),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, verbose_name='modified', editable=False)),
                ('course_key', CourseKeyField(max_length=255, db_index=True)),
                ('usage_key', UsageKeyField(unique=True, max_length=255, db_index=True)),
                ('display_name', models.CharField(default='', max_length=255)),
                ('_paths', jsonfield.fields.JSONField(default=[], help_text='All paths in course tree to the corresponding block.', db_column='paths')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.AddField(
            model_name='bookmark',
            name='xblock_cache',
            field=models.ForeignKey(to='bookmarks.XBlockCache', on_delete=models.CASCADE),
        ),
        migrations.AlterUniqueTogether(
            name='bookmark',
            unique_together={('user', 'usage_key')},
        ),
    ]
