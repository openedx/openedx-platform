""" Tests for setting and displaying the site status message. """

import ddt  # noqa: I001
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from opaque_keys.edx.locations import CourseLocator

from openedx.core.djangolib.testing.utils import skip_unless_lms

# Status is not in CMS' INSTALLED_APPS so these imports will error during test collection
if settings.ROOT_URLCONF == 'lms.urls':
    from .models import CourseMessage, GlobalStatusMessage
    from .status import get_site_status_msg


@ddt.ddt
class TestStatus(TestCase):
    """Test that the get_site_status_msg function does the right thing"""

    def setUp(self):
        super().setUp()
        # Clear the cache between test runs.
        cache.clear()
        self.course_key = CourseLocator(org='TestOrg', course='TestCourse', run='TestRun')

    @skip_unless_lms
    @ddt.data(
        ("Test global message", "Test course message"),
        (" Ŧɇsŧ sŧȺŧᵾs", "Ṫëṡẗ ċöüṛṡë ṡẗäẗüṡ "),
        ("", "Ṫëṡẗ ċöüṛṡë ṡẗäẗüṡ "),
        (" Ŧɇsŧ sŧȺŧᵾs", ""),
    )
    @ddt.unpack
    def test_get_site_status_msg(self, test_global_message, test_course_message):
        """Test status messages in a variety of situations."""

        # When we don't have any data set.
        assert get_site_status_msg(None) is None
        assert get_site_status_msg(self.course_key) is None

        msg = GlobalStatusMessage.objects.create(message=test_global_message, enabled=True)
        msg.save()

        assert get_site_status_msg(None) == test_global_message

        course_msg = CourseMessage.objects.create(
            global_message=msg, message=test_course_message, course_key=self.course_key
        )
        course_msg.save()
        assert get_site_status_msg(self.course_key) == f'{test_global_message} <br /> {test_course_message}'

        msg = GlobalStatusMessage.objects.create(message="", enabled=False)
        msg.save()

        assert get_site_status_msg(None) is None
        assert get_site_status_msg(self.course_key) is None
