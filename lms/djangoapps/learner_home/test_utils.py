"""
Various utilities used for creating test data
"""

import datetime
from random import choice, getrandbits, randint
from time import time
from uuid import uuid4

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from xmodule.modulestore.tests.factories import CourseFactory


def random_string():
    """Test util for generating a random string"""
    return str(uuid4())


def random_bool():
    """Test util for generating a random boolean"""
    return bool(getrandbits(1))


def random_date(allow_null=False):
    """Test util for generating a random date, optionally blank"""

    # If null allowed, return null half the time
    if allow_null and random_bool():
        return None

    d = randint(1, int(time()))
    return datetime.datetime.fromtimestamp(d, tz=datetime.timezone.utc)  # noqa: UP017


def random_url(allow_null=False):
    """Test util for generating a random URL, optionally blank"""

    # If null allowed, return null half the time
    if allow_null and random_bool():
        return None

    random_uuid = uuid4()
    return choice([f"{random_uuid}.example.com", f"example.com/{random_uuid}"])


def random_grade():
    """Return a random grade (0-100) with 2 decimal places of padding"""
    return randint(0, 10000) / 100


def decimal_to_grade_format(decimal):
    """Util for matching serialized grade format, pads a decimal to 2 places"""
    return "{:.2f}".format(decimal)  # noqa: UP032


def datetime_to_django_format(datetime_obj):
    """Util for matching serialized Django datetime format for comparison"""
    if datetime_obj:
        return datetime_obj.strftime("%Y-%m-%dT%H:%M:%SZ")


def create_test_enrollment(user, course_mode=CourseMode.AUDIT, advertised_start=None):
    """Create a test user, course, course overview, and enrollment. Return the enrollment."""
    course = CourseFactory(
        self_paced=True,
        advertised_start=advertised_start,
    )

    CourseModeFactory(
        course_id=course.id,
        mode_slug=course_mode,
    )

    course_overview = CourseOverviewFactory(id=course.id)

    # extra info for exercising serializers
    course_overview.certificate_available_date = random_date()

    test_enrollment = CourseEnrollmentFactory(
        course_id=course.id, mode=course_mode, user_id=user.id
    )

    test_enrollment.course_overview.marketing_url = random_url()
    test_enrollment.course_overview.end = random_date()

    return test_enrollment
