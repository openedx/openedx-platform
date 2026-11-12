"""
Tests for the legacy (pre-AuthZ) checks of the Instructor Dashboard.
"""

from django.test import TestCase
from opaque_keys.edx.locator import CourseLocator

from common.djangoapps.student.tests.factories import InstructorFactory, UserFactory
from lms.djangoapps.instructor.authz.legacy import is_django_staff
from openedx.core.djangolib.testing.utils import skip_unless_lms

COURSE_KEY = CourseLocator("edX", "Demo", "2026")


@skip_unless_lms
class IsDjangoStaffTest(TestCase):
    """
    ``is_django_staff`` must behave as DRF's ``IsAdminUser``.
    """

    def test_django_staff_is_allowed(self):
        """A user with the Django ``is_staff`` flag is allowed."""
        assert is_django_staff(UserFactory(is_staff=True), COURSE_KEY)

    def test_regular_user_is_denied(self):
        """A user without the ``is_staff`` flag is denied."""
        assert not is_django_staff(UserFactory(), COURSE_KEY)

    def test_course_instructor_without_is_staff_is_denied(self):
        """Course-level roles do not grant access, only the Django ``is_staff`` flag does."""
        instructor = InstructorFactory(course_key=COURSE_KEY)
        assert not is_django_staff(instructor, COURSE_KEY)

    def test_missing_user_is_denied(self):
        """A missing user is denied instead of raising."""
        assert not is_django_staff(None, COURSE_KEY)
