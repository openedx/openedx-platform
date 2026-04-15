from django.conf import settings
from django.db import migrations, models
from opaque_keys.edx.django.models import CourseKeyField, UsageKeyField

from openedx.core.lib.hash_utils import create_hash256, short_token


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='GradedAssignment',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('course_key', CourseKeyField(max_length=255, db_index=True)),
                ('usage_key', UsageKeyField(max_length=255, db_index=True)),
                ('lis_result_sourcedid', models.CharField(max_length=255, db_index=True)),
                ('version_number', models.IntegerField(default=0)),
            ],
        ),
        migrations.CreateModel(
            name='LtiConsumer',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('consumer_name', models.CharField(unique=True, max_length=255)),
                ('consumer_key', models.CharField(default=short_token, unique=True, max_length=32, db_index=True)),
                ('consumer_secret', models.CharField(default=create_hash256, unique=True, max_length=32)),
                ('instance_guid', models.CharField(max_length=255, unique=True, null=True, blank=True)),
            ],
        ),
        migrations.CreateModel(
            name='LtiUser',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('lti_user_id', models.CharField(max_length=255)),
                ('edx_user', models.OneToOneField(to=settings.AUTH_USER_MODEL, on_delete=models.CASCADE)),
                ('lti_consumer', models.ForeignKey(to='lti_provider.LtiConsumer', on_delete=models.CASCADE)),
            ],
        ),
        migrations.CreateModel(
            name='OutcomeService',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('lis_outcome_service_url', models.CharField(unique=True, max_length=255)),
                ('lti_consumer', models.ForeignKey(to='lti_provider.LtiConsumer', on_delete=models.CASCADE)),
            ],
        ),
        migrations.AddField(
            model_name='gradedassignment',
            name='outcome_service',
            field=models.ForeignKey(to='lti_provider.OutcomeService', on_delete=models.CASCADE),
        ),
        migrations.AddField(
            model_name='gradedassignment',
            name='user',
            field=models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=models.CASCADE),
        ),
        migrations.AlterUniqueTogether(
            name='ltiuser',
            unique_together={('lti_consumer', 'lti_user_id')},
        ),
        migrations.AlterUniqueTogether(
            name='gradedassignment',
            unique_together={('outcome_service', 'lis_result_sourcedid')},
        ),
    ]
