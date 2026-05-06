from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Migration to remove default Mode and to move comments to Help Text
    """

    dependencies = [
        ('entitlements', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='courseentitlement',
            name='course_uuid',
            field=models.UUIDField(help_text='UUID for the Course, not the Course Run'),
        ),
        migrations.AlterField(
            model_name='courseentitlement',
            name='enrollment_course_run',
            field=models.ForeignKey(to='student.CourseEnrollment', help_text='The current Course enrollment for this entitlement. If NULL the Learner has not enrolled.', null=True, on_delete=models.CASCADE),  # pylint: disable=line-too-long
        ),
        migrations.AlterField(
            model_name='courseentitlement',
            name='expired_at',
            field=models.DateTimeField(help_text='The date that an entitlement expired, if NULL the entitlement has not expired.', null=True),  # pylint: disable=line-too-long
        ),
        migrations.AlterField(
            model_name='courseentitlement',
            name='mode',
            field=models.CharField(help_text='The mode of the Course that will be applied on enroll.', max_length=100),
        ),
    ]
