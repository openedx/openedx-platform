import django.db.models.deletion
import django_countries.fields
from django.conf import settings
from django.db import migrations, models
from opaque_keys.edx.django.models import CourseKeyField


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Country',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('country', django_countries.fields.CountryField(help_text='Two character ISO country code.', unique=True, max_length=2, db_index=True)),
            ],
            options={
                'ordering': ['country'],
            },
        ),
        migrations.CreateModel(
            name='CountryAccessRule',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('rule_type', models.CharField(default='blacklist', help_text='Whether to include or exclude the given course. If whitelist countries are specified, then ONLY users from whitelisted countries will be able to access the course.  If blacklist countries are specified, then users from blacklisted countries will NOT be able to access the course.', max_length=255, choices=[('whitelist', 'Whitelist (allow only these countries)'), ('blacklist', 'Blacklist (block these countries)')])),
                ('country', models.ForeignKey(help_text='The country to which this rule applies.', to='embargo.Country', on_delete=models.CASCADE)),
            ],
        ),
        migrations.CreateModel(
            name='CourseAccessRuleHistory',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('timestamp', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('course_key', CourseKeyField(max_length=255, db_index=True)),
                ('snapshot', models.TextField(null=True, blank=True)),
            ],
            options={
                'get_latest_by': 'timestamp',
            },
        ),
        migrations.CreateModel(
            name='EmbargoedCourse',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('course_id', CourseKeyField(unique=True, max_length=255, db_index=True)),
                ('embargoed', models.BooleanField(default=False)),
            ],
        ),
        migrations.CreateModel(
            name='EmbargoedState',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('change_date', models.DateTimeField(auto_now_add=True, verbose_name='Change date')),
                ('enabled', models.BooleanField(default=False, verbose_name='Enabled')),
                ('embargoed_countries', models.TextField(help_text='A comma-separated list of country codes that fall under U.S. embargo restrictions', blank=True)),
                ('changed_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, editable=False, to=settings.AUTH_USER_MODEL, null=True, verbose_name='Changed by')),
            ],
            options={
                'ordering': ('-change_date',),
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='IPFilter',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('change_date', models.DateTimeField(auto_now_add=True, verbose_name='Change date')),
                ('enabled', models.BooleanField(default=False, verbose_name='Enabled')),
                ('whitelist', models.TextField(help_text='A comma-separated list of IP addresses that should not fall under embargo restrictions.', blank=True)),
                ('blacklist', models.TextField(help_text='A comma-separated list of IP addresses that should fall under embargo restrictions.', blank=True)),
                ('changed_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, editable=False, to=settings.AUTH_USER_MODEL, null=True, verbose_name='Changed by')),
            ],
            options={
                'ordering': ('-change_date',),
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='RestrictedCourse',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('course_key', CourseKeyField(help_text='The course key for the restricted course.', unique=True, max_length=255, db_index=True)),
                ('enroll_msg_key', models.CharField(default='default', help_text='The message to show when a user is blocked from enrollment.', max_length=255, choices=[('default', 'Default'), ('embargo', 'Embargo')])),
                ('access_msg_key', models.CharField(default='default', help_text='The message to show when a user is blocked from accessing a course.', max_length=255, choices=[('default', 'Default'), ('embargo', 'Embargo')])),
                ('disable_access_check', models.BooleanField(default=False, help_text='Allow users who enrolled in an allowed country to access restricted courses from excluded countries.')),
            ],
        ),
        migrations.AddField(
            model_name='countryaccessrule',
            name='restricted_course',
            field=models.ForeignKey(help_text='The course to which this rule applies.', to='embargo.RestrictedCourse', on_delete=models.CASCADE),
        ),
        migrations.AlterUniqueTogether(
            name='countryaccessrule',
            unique_together={('restricted_course', 'country')},
        ),
    ]
