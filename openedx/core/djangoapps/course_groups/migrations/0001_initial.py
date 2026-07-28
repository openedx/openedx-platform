from django.conf import settings
from django.db import migrations, models
from opaque_keys.edx.django.models import CourseKeyField


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CohortMembership',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('course_id', CourseKeyField(max_length=255)),
            ],
        ),
        migrations.CreateModel(
            name='CourseCohort',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('assignment_type', models.CharField(default='manual', max_length=20, choices=[('random', 'Random'), ('manual', 'Manual')])),
            ],
        ),
        migrations.CreateModel(
            name='CourseCohortsSettings',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_cohorted', models.BooleanField(default=False)),
                ('course_id', CourseKeyField(help_text='Which course are these settings associated with?', unique=True, max_length=255, db_index=True)),
                ('_cohorted_discussions', models.TextField(null=True, db_column='cohorted_discussions', blank=True)),
                ('always_cohort_inline_discussions', models.BooleanField(default=True)),
            ],
        ),
        migrations.CreateModel(
            name='CourseUserGroup',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('name', models.CharField(help_text='What is the name of this group?  Must be unique within a course.', max_length=255)),
                ('course_id', CourseKeyField(help_text='Which course is this group associated with?', max_length=255, db_index=True)),
                ('group_type', models.CharField(max_length=20, choices=[('cohort', 'Cohort')])),
                ('users', models.ManyToManyField(help_text='Who is in this group?', related_name='course_groups', to=settings.AUTH_USER_MODEL, db_index=True)),
            ],
        ),
        migrations.CreateModel(
            name='CourseUserGroupPartitionGroup',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('partition_id', models.IntegerField(help_text='contains the id of a cohorted partition in this course')),
                ('group_id', models.IntegerField(help_text='contains the id of a specific group within the cohorted partition')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('course_user_group', models.OneToOneField(to='course_groups.CourseUserGroup', on_delete=models.CASCADE)),
            ],
        ),
        migrations.AddField(
            model_name='coursecohort',
            name='course_user_group',
            field=models.OneToOneField(related_name='cohort', to='course_groups.CourseUserGroup', on_delete=models.CASCADE),
        ),
        migrations.AddField(
            model_name='cohortmembership',
            name='course_user_group',
            field=models.ForeignKey(to='course_groups.CourseUserGroup', on_delete=models.CASCADE),
        ),
        migrations.AddField(
            model_name='cohortmembership',
            name='user',
            field=models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=models.CASCADE),
        ),
        migrations.AlterUniqueTogether(
            name='courseusergroup',
            unique_together={('name', 'course_id')},
        ),
        migrations.AlterUniqueTogether(
            name='cohortmembership',
            unique_together={('user', 'course_id')},
        ),
    ]
