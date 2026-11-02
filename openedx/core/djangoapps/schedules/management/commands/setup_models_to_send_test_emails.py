"""
A managment command that can be used to set up Schedules with various configurations for testing.
"""


import datetime
from textwrap import dedent

import factory
from zoneinfo import ZoneInfo
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.schedules.models import ScheduleExperience
from openedx.core.djangoapps.schedules.tests.factories import (
    ScheduleConfigFactory,
    ScheduleExperienceFactory,
    ScheduleFactory
)
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import XMODULE_FACTORY_LOCK, CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


class ThreeDayNudgeSchedule(ScheduleFactory):
    """
    A ScheduleFactory that creates a Schedule set up for a 3-day nudge email.
    """
    start_date = factory.Faker('date_time_between', start_date='-3d', end_date='-3d', tzinfo=ZoneInfo("UTC"))


class TenDayNudgeSchedule(ScheduleFactory):
    """
    A ScheduleFactory that creates a Schedule set up for a 10-day nudge email.
    """
    start_date = factory.Faker('date_time_between', start_date='-10d', end_date='-10d', tzinfo=ZoneInfo("UTC"))


class UpgradeReminderSchedule(ScheduleFactory):
    """
    A ScheduleFactory that creates a Schedule set up for a 2-days-remaining upgrade reminder.
    """
    start_date = factory.Faker('past_datetime', tzinfo=ZoneInfo("UTC"))
    upgrade_deadline = factory.Faker('date_time_between', start_date='+2d', end_date='+2d', tzinfo=ZoneInfo("UTC"))


class ContentHighlightSchedule(ScheduleFactory):
    """
    A ScheduleFactory that creates a Schedule set up for a course highlights email.
    """
    start_date = factory.Faker('date_time_between', start_date='-7d', end_date='-7d', tzinfo=ZoneInfo("UTC"))
    experience = factory.RelatedFactory(ScheduleExperienceFactory, 'schedule', experience_type=ScheduleExperience.EXPERIENCES.course_updates)  # lint-amnesty, pylint: disable=line-too-long


class Command(BaseCommand):
    """
    A management command that generates schedule objects for all expected schedule email types, so that it is easy to
    generate test emails of all available types.
    """
    help = dedent(__doc__).strip()

    def handle(self, *args, **options):
        courses = modulestore().get_courses()

        # Find the largest auto-generated course, and pick the next sequence id to generate the next
        # course with.
        max_org_sequence_id = max(int(course.org[4:]) for course in courses if course.org.startswith('org.'))

        XMODULE_FACTORY_LOCK.enable()
        CourseFactory.reset_sequence(max_org_sequence_id + 1, force=True)
        course = CourseFactory.create(
            start=datetime.datetime.today() - datetime.timedelta(days=30),
            end=datetime.datetime.today() + datetime.timedelta(days=30),
            number=factory.Sequence('schedules_test_course_{}'.format),
            display_name=factory.Sequence('Schedules Test Course {}'.format),
        )
        XMODULE_FACTORY_LOCK.disable()
        course_overview = CourseOverview.load_from_module_store(course.id)
        ThreeDayNudgeSchedule.create(enrollment__course=course_overview)
        TenDayNudgeSchedule.create(enrollment__course=course_overview)
        UpgradeReminderSchedule.create(enrollment__course=course_overview)
        ContentHighlightSchedule.create(enrollment__course=course_overview)

        ScheduleConfigFactory.create(site=Site.objects.get(name='example.com'))
