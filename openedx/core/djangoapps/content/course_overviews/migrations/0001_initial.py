import django.utils.timezone
import model_utils.fields
from django.db import migrations, models
from opaque_keys.edx.django.models import CourseKeyField, UsageKeyField


class Migration(migrations.Migration):

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='CourseOverview',
            fields=[
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, verbose_name='created', editable=False)),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, verbose_name='modified', editable=False)),
                ('version', models.IntegerField()),
                ('id', CourseKeyField(max_length=255, serialize=False, primary_key=True, db_index=True)),
                ('_location', UsageKeyField(max_length=255)),
                ('display_name', models.TextField(null=True)),
                ('display_number_with_default', models.TextField()),
                ('display_org_with_default', models.TextField()),
                ('start', models.DateTimeField(null=True)),
                ('end', models.DateTimeField(null=True)),
                ('advertised_start', models.TextField(null=True)),
                ('course_image_url', models.TextField()),
                ('facebook_url', models.TextField(null=True)),
                ('social_sharing_url', models.TextField(null=True)),
                ('end_of_course_survey_url', models.TextField(null=True)),
                ('certificates_display_behavior', models.TextField(null=True)),
                ('certificates_show_before_end', models.BooleanField(default=False)),
                ('cert_html_view_enabled', models.BooleanField(default=False)),
                ('has_any_active_web_certificate', models.BooleanField(default=False)),
                ('cert_name_short', models.TextField()),
                ('cert_name_long', models.TextField()),
                ('lowest_passing_grade', models.DecimalField(null=True, max_digits=5, decimal_places=2)),
                ('days_early_for_beta', models.FloatField(null=True)),
                ('mobile_available', models.BooleanField(default=False)),
                ('visible_to_staff_only', models.BooleanField(default=False)),
                ('_pre_requisite_courses_json', models.TextField()),
                ('enrollment_start', models.DateTimeField(null=True)),
                ('enrollment_end', models.DateTimeField(null=True)),
                ('enrollment_domain', models.TextField(null=True)),
                ('invitation_only', models.BooleanField(default=False)),
                ('max_student_enrollments_allowed', models.IntegerField(null=True)),
            ],
        ),
        migrations.CreateModel(
            name='CourseOverviewTab',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('tab_id', models.CharField(max_length=50)),
                ('course_overview', models.ForeignKey(related_name='tabs', to='course_overviews.CourseOverview', on_delete=models.CASCADE)),
            ],
        ),
    ]
