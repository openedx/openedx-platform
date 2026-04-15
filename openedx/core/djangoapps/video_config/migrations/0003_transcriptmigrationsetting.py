import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('video_config', '0002_coursevideotranscriptenabledflag_videotranscriptenabledflag'),
    ]

    operations = [
        migrations.CreateModel(
            name='TranscriptMigrationSetting',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('change_date', models.DateTimeField(auto_now_add=True, verbose_name='Change date')),
                ('enabled', models.BooleanField(default=False, verbose_name='Enabled')),
                ('force_update', models.BooleanField(default=False, help_text='Flag to force migrate transcripts for the requested courses, overwrite if already present.')),
                ('commit', models.BooleanField(default=False, help_text='Dry-run or commit.')),
                ('all_courses', models.BooleanField(default=False, help_text='Process all courses.')),
                ('course_ids', models.TextField(help_text='Whitespace-separated list of course keys for which to migrate transcripts.')),
                ('changed_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, editable=False, to=settings.AUTH_USER_MODEL, null=True, verbose_name='Changed by')),
            ],
            options={
                'ordering': ('-change_date',),
                'abstract': False,
            },
        ),
    ]
