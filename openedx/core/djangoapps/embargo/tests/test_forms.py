"""
Unit tests for embargo app admin forms.
"""


# Explicitly import the cache from ConfigurationModel so we can reset it after each test
from config_models.models import cache
from django.test import TestCase
from opaque_keys.edx.locator import CourseLocator

from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

from ..forms import IPFilterForm, RestrictedCourseForm
from ..models import IPFilter


class RestrictedCourseFormTest(ModuleStoreTestCase):
    """Test the course form properly validates course IDs"""

    def test_save_valid_data(self):
        course = CourseFactory.create()
        data = {
            'course_key': str(course.id),
            'enroll_msg_key': 'default',
            'access_msg_key': 'default'
        }
        form = RestrictedCourseForm(data=data)
        assert form.is_valid()

    def test_invalid_course_key(self):
        # Invalid format for the course key
        form = RestrictedCourseForm(data={'course_key': 'not/valid'})
        self._assert_course_field_error(form)

    def test_course_not_found(self):
        course_key = CourseLocator(org='test', course='test', run='test')
        form = RestrictedCourseForm(data={'course_key': course_key})
        self._assert_course_field_error(form)

    def _assert_course_field_error(self, form):
        """
        Validation shouldn't work.
        """
        assert not form.is_valid()

        msg = 'COURSE NOT FOUND'
        assert msg in form._errors['course_key'][0]  # pylint: disable=protected-access

        with self.assertRaisesRegex(  # noqa: PT027
            ValueError, "The RestrictedCourse could not be created because the data didn't validate."
        ):
            form.save()


class IPFilterFormTest(TestCase):
    """Test form for adding [black|white]list IP addresses"""

    def tearDown(self):
        super().tearDown()
        # Explicitly clear ConfigurationModel's cache so tests have a clear cache
        # and don't interfere with each other
        cache.clear()

    def test_add_valid_ips(self):
        # test adding valid ip addresses
        # should be able to do both ipv4 and ipv6
        # spacing should not matter
        form_data = {
            'whitelist': '127.0.0.1, 2003:dead:beef:4dad:23:46:bb:101, 1.1.0.1/32, 1.0.0.0/24',
            'blacklist': '  18.244.1.5  ,  2002:c0a8:101::42, 18.36.22.1, 1.0.0.0/16'
        }
        form = IPFilterForm(data=form_data)
        assert form.is_valid()
        form.save()
        whitelist = IPFilter.current().whitelist_ips
        blacklist = IPFilter.current().blacklist_ips
        for addr in '127.0.0.1, 2003:dead:beef:4dad:23:46:bb:101'.split(','):
            assert addr.strip() in whitelist
        for addr in '18.244.1.5, 2002:c0a8:101::42, 18.36.22.1'.split(','):
            assert addr.strip() in blacklist

        # Network tests
        # ips not in whitelist network
        for addr in ['1.1.0.2', '1.0.1.0']:
            assert addr.strip() not in whitelist
        # ips in whitelist network
        for addr in ['1.1.0.1', '1.0.0.100']:
            assert addr.strip() in whitelist
        # ips not in blacklist network
        for addr in ['2.0.0.0', '1.1.0.0']:
            assert addr.strip() not in blacklist
        # ips in blacklist network
        for addr in ['1.0.100.0', '1.0.0.10']:
            assert addr.strip() in blacklist

        # Test clearing by adding an empty list is OK too
        form_data = {
            'whitelist': '',
            'blacklist': ''
        }
        form = IPFilterForm(data=form_data)
        assert form.is_valid()
        form.save()
        assert len(IPFilter.current().whitelist) == 0
        assert len(IPFilter.current().blacklist) == 0

    def test_add_invalid_ips(self):
        # test adding invalid ip addresses
        form_data = {
            'whitelist': '.0.0.1, :dead:beef:::, 1.0.0.0/55',
            'blacklist': '  18.244.*  ,  999999:c0a8:101::42, 1.0.0.0/'
        }
        form = IPFilterForm(data=form_data)
        assert not form.is_valid()

        wmsg = "Invalid IP Address(es): ['.0.0.1', ':dead:beef:::', '1.0.0.0/55']"\
               " Please fix the error(s) and try again."
        assert wmsg == form._errors['whitelist'][0]  # pylint: disable=protected-access

        bmsg = "Invalid IP Address(es): ['18.244.*', '999999:c0a8:101::42', '1.0.0.0/']"\
               " Please fix the error(s) and try again."
        assert bmsg == form._errors['blacklist'][0]  # pylint: disable=protected-access

        with self.assertRaisesRegex(ValueError, "The IPFilter could not be created because the data didn't validate."):  # noqa: PT027  # pylint: disable=line-too-long
            form.save()
