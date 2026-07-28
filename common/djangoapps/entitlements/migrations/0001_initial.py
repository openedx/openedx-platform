import uuid

import django.utils.timezone
import model_utils.fields
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):  # pylint: disable=missing-class-docstring

    dependencies = [
        ('student', '0001_squashed_0031_auto_20200317_1122'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CourseEntitlement',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, verbose_name='created', editable=False)),  # pylint: disable=line-too-long
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, verbose_name='modified', editable=False)),  # pylint: disable=line-too-long
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False)),
                ('course_uuid', models.UUIDField()),
                ('expired_at', models.DateTimeField(null=True)),
                ('mode', models.CharField(default='audit', max_length=100)),
                ('order_number', models.CharField(max_length=128, null=True)),
                ('enrollment_course_run', models.ForeignKey(to='student.CourseEnrollment', null=True, on_delete=models.CASCADE)),  # pylint: disable=line-too-long
                ('user', models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=models.CASCADE)),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
