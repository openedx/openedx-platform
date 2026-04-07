"""
Tests for EmbargoMiddleware with CountryAccessRules
"""


from unittest.mock import patch

import ddt
from config_models.models import cache as config_cache
from django.conf import settings
from django.core.cache import cache as django_cache
from django.urls import reverse

from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.util.testing import UrlResetMixin
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

from ..models import IPFilter, RestrictedCourse
from ..test_utils import restrict_course


@ddt.ddt
@skip_unless_lms
class EmbargoMiddlewareAccessTests(UrlResetMixin, ModuleStoreTestCase):
    """Tests of embargo middleware country access rules.

    There are detailed unit tests for the rule logic in
    `test_api.py`; here, we're mainly testing the integration
    with middleware

    """
    USERNAME = 'fred'
    PASSWORD = 'secret'

    URLCONF_MODULES = ['openedx.core.djangoapps.embargo']

    @patch.dict(settings.FEATURES, {'EMBARGO': True})
    def setUp(self):
        super().setUp()
        self.user = UserFactory(username=self.USERNAME, password=self.PASSWORD)
        self.course = CourseFactory.create()
        self.client.login(username=self.USERNAME, password=self.PASSWORD)

        self.courseware_url = reverse('about_course', kwargs={'course_id': str(self.course.id)})
        self.non_courseware_url = reverse('dashboard')

        # Clear the cache to avoid interference between tests
        django_cache.clear()
        config_cache.clear()

    @patch.dict(settings.FEATURES, {'EMBARGO': True})
    @ddt.data(True, False)
    def test_blocked(self, disable_access_check):
        with restrict_course(self.course.id, access_point='courseware', disable_access_check=disable_access_check) as redirect_url:  # pylint: disable=line-too-long
            response = self.client.get(self.courseware_url)
            if disable_access_check:
                assert response.status_code == 200
            else:
                self.assertRedirects(response, redirect_url)

    @patch.dict(settings.FEATURES, {'EMBARGO': True})
    def test_allowed(self):
        # Add the course to the list of restricted courses
        # but don't create any access rules
        RestrictedCourse.objects.create(course_key=self.course.id)

        # Expect that we can access courseware
        response = self.client.get(self.courseware_url)
        assert response.status_code == 200

    @patch.dict(settings.FEATURES, {'EMBARGO': True})
    def test_non_courseware_url(self):
        with restrict_course(self.course.id):
            response = self.client.get(self.non_courseware_url)
            assert response.status_code == 200

    @patch.dict(settings.FEATURES, {'EMBARGO': True})
    @ddt.data(
        # request ip chain, blacklist, whitelist, is_enabled, allow_access
        (['192.178.2.3'], [], [], True, True),  # confirm that test setup & no config allows users by default
        (['173.194.123.35'], ['173.194.123.35'], [], True, False),
        (['173.194.123.35'], ['173.194.0.0/16'], [], True, False),
        (['173.194.123.35'], ['127.0.0.0/32', '173.194.0.0/16'], [], True, False),
        (['173.195.10.20'], ['173.194.0.0/16'], [], True, True),
        (['173.194.123.35'], ['173.194.0.0/16'], ['173.194.0.0/16'], True, False),  # blacklist checked before whitelist
        (['173.194.123.35', '192.178.2.3'], ['173.194.123.35'], [], True, False),  # earlier ip can still be blocked
        (['173.194.123.35'], ['173.194.123.35'], [], False, True),  # blacklist disabled
    )
    @ddt.unpack
    def test_ip_blacklist_rules(self, request_ips, blacklist, whitelist, is_enabled, allow_access):
        # Ensure that IP blocking works for anonymous users
        self.client.logout()

        # Set up the IP rules
        IPFilter.objects.create(
            blacklist=", ".join(blacklist),
            whitelist=", ".join(whitelist),
            enabled=is_enabled
        )

        # Check that access is enforced
        response = self.client.get(
            self.courseware_url,
            HTTP_X_FORWARDED_FOR=','.join(request_ips),
            REMOTE_ADDR=request_ips[-1],
        )

        if allow_access:
            assert response.status_code == 200
        else:
            redirect_url = reverse(
                'embargo:blocked_message',
                kwargs={
                    'access_point': 'courseware',
                    'message_key': 'embargo'
                }
            )
            self.assertRedirects(response, redirect_url)

    @patch.dict(settings.FEATURES, {'EMBARGO': True})
    @ddt.data(
        # request ip chain, blacklist, whitelist, is_enabled, allow_access
        (['192.178.2.3'], [], [], True, False),  # confirm that test setup & no config blocks users by default
        (['173.194.123.35', '192.178.2.3'], [], ['173.194.123.35'], True, False),  # whitelist only looks at last ip
        (['192.178.2.3', '173.194.123.35'], [], ['173.194.0.0/16'], True, True),
        (['192.178.2.3'], [], ['173.194.0.0/16'], True, False),
        (['173.194.123.35'], [], ['173.194.123.35'], False, False),  # whitelist disabled
    )
    @ddt.unpack
    def test_ip_whitelist_rules(self, request_ips, blacklist, whitelist, is_enabled, allow_access):
        # Ensure that IP blocking works for anonymous users
        self.client.logout()

        # Set up the IP rules
        IPFilter.objects.create(
            blacklist=", ".join(blacklist),
            whitelist=", ".join(whitelist),
            enabled=is_enabled
        )

        # Check that access is enforced (restrict course by default, so that allow-list logic is actually tested)
        with restrict_course(self.course.id):
            response = self.client.get(
                self.courseware_url,
                HTTP_X_FORWARDED_FOR=','.join(request_ips),
                REMOTE_ADDR=request_ips[-1],
            )

        if allow_access:
            assert response.status_code == 200
        else:
            redirect_url = reverse(
                'embargo:blocked_message',
                kwargs={
                    'access_point': 'courseware',
                    'message_key': 'default'
                }
            )
            self.assertRedirects(response, redirect_url)

    @patch.dict(settings.FEATURES, {'EMBARGO': True})
    @ddt.data(
        ('courseware', 'default'),
        ('courseware', 'embargo'),
        ('enrollment', 'default'),
        ('enrollment', 'embargo')
    )
    @ddt.unpack
    def test_always_allow_access_to_embargo_messages(self, access_point, msg_key):
        # Blacklist an IP address
        IPFilter.objects.create(
            blacklist="192.168.10.20",
            enabled=True
        )

        url = reverse(
            'embargo:blocked_message',
            kwargs={
                'access_point': access_point,
                'message_key': msg_key
            }
        )
        response = self.client.get(
            url,
            HTTP_X_FORWARDED_FOR="192.168.10.20",
            REMOTE_ADDR="192.168.10.20"
        )
        assert response.status_code == 200
