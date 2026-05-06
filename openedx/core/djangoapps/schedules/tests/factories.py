"""
Factories for schedules tests
"""


from zoneinfo import ZoneInfo

import factory

from common.djangoapps.student.tests.factories import CourseEnrollmentFactory
from openedx.core.djangoapps.schedules import models
from openedx.core.djangoapps.site_configuration.tests.factories import SiteFactory


class ScheduleExperienceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.ScheduleExperience

    experience_type = models.ScheduleExperience.EXPERIENCES.default


class ScheduleFactory(factory.django.DjangoModelFactory):  # pylint: disable=missing-class-docstring
    class Meta:
        model = models.Schedule

    start_date = factory.Faker('future_datetime', tzinfo=ZoneInfo("UTC"))
    upgrade_deadline = factory.Faker('future_datetime', tzinfo=ZoneInfo("UTC"))
    enrollment = factory.SubFactory(CourseEnrollmentFactory)
    experience = factory.RelatedFactory(ScheduleExperienceFactory, 'schedule')


class ScheduleConfigFactory(factory.django.DjangoModelFactory):  # pylint: disable=missing-class-docstring
    class Meta:
        model = models.ScheduleConfig

    site = factory.SubFactory(SiteFactory)
    enqueue_recurring_nudge = True
    deliver_recurring_nudge = True
    enqueue_upgrade_reminder = True
    deliver_upgrade_reminder = True
    enqueue_course_update = True
    deliver_course_update = True
