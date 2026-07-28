""" Factories for course reset models """
import factory
from factory.django import DjangoModelFactory

from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.support.models import CourseResetAudit, CourseResetCourseOptIn


class CourseResetCourseOptInFactory(DjangoModelFactory):  # pylint: disable=missing-class-docstring
    class Meta:
        model = CourseResetCourseOptIn

    course_id = None
    active = True


class CourseResetAuditFactory(DjangoModelFactory):  # pylint: disable=missing-class-docstring
    class Meta:
        model = CourseResetAudit

    course = factory.SubFactory(CourseResetCourseOptInFactory)
    course_enrollment = factory.SubFactory(CourseEnrollmentFactory)
    reset_by = factory.SubFactory(UserFactory)
    status = CourseResetAudit.CourseResetStatus.ENQUEUED
    comment = factory.Sequence(lambda i: f'comment {i}')
    completed_at = None
