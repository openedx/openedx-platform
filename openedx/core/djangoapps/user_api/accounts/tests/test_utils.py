""" Unit tests for custom UserProfile properties. """


import ddt
from completion import models
from completion.test_utils import CompletionWaffleTestMixin
from django.test import TestCase
from django.test.utils import override_settings

from openedx.core.djangoapps.user_api.accounts.utils import retrieve_last_sitewide_block_completed
from openedx.core.djangolib.testing.utils import skip_unless_lms
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory  # lint-amnesty, pylint: disable=wrong-import-order

from ..utils import format_social_link, validate_social_link, get_named_storage
from django.conf import settings
from django.core.files.storage import default_storage


@ddt.ddt
class UserAccountSettingsTest(TestCase):
    """Unit tests for setting Social Media Links."""

    def setUp(self):  # lint-amnesty, pylint: disable=useless-super-delegation
        super().setUp()

    def validate_social_link(self, social_platform, link):
        """
        Helper method that returns True if the social link is valid, False if
        the input link fails validation and will throw an error.
        """
        try:
            validate_social_link(social_platform, link)
        except ValueError:
            return False
        return True

    @ddt.data(
        ('facebook', 'www.facebook.com/edX', 'https://www.facebook.com/edX', True),
        ('facebook', 'facebook.com/edX/', 'https://www.facebook.com/edX', True),
        ('facebook', 'HTTP://facebook.com/edX/', 'https://www.facebook.com/edX', True),
        ('facebook', 'www.evilwebsite.com/123', None, False),
        ('x', 'https://www.x.com/edX/', 'https://www.x.com/edX', True),
        ('x', 'https://www.x.com/edX/123s', None, False),
        ('x', 'x.com/edX', 'https://www.x.com/edX', True),
        ('x', 'x.com/edX?foo=bar', 'https://www.x.com/edX?foo=bar', True),
        ('x', 'x.com/test.user', 'https://www.x.com/test.user', True),
        ('linkedin', 'www.linkedin.com/harryrein', None, False),
        ('linkedin', 'www.linkedin.com/in/harryrein-1234', 'https://www.linkedin.com/in/harryrein-1234', True),
        ('linkedin', 'www.evilwebsite.com/123?www.linkedin.com/edX', None, False),
        ('linkedin', '', '', True),
        ('linkedin', None, None, False),
    )
    @ddt.unpack
    @skip_unless_lms
    def test_social_link_input(self, platform_name, link_input, formatted_link_expected, is_valid_expected):
        """
        Verify that social links are correctly validated and formatted.
        """
        assert is_valid_expected == self.validate_social_link(platform_name, link_input)

        assert formatted_link_expected == format_social_link(platform_name, link_input)


@ddt.ddt
class CompletionUtilsTestCase(SharedModuleStoreTestCase, CompletionWaffleTestMixin, TestCase):
    """
    Test completion utility functions
    """
    def setUp(self):
        """
        Creates a test course that can be used for non-destructive tests
        """
        super().setUp()
        self.override_waffle_switch(True)
        self.engaged_user = UserFactory.create()
        self.cruft_user = UserFactory.create()
        self.course = self.create_test_course()
        self.submit_faux_completions()

    def create_test_course(self):
        """
        Create, populate test course.
        """
        course = CourseFactory.create()
        with self.store.bulk_operations(course.id):
            self.chapter = BlockFactory.create(category='chapter', parent=course)
            self.sequential = BlockFactory.create(category='sequential', parent=self.chapter)
            self.vertical1 = BlockFactory.create(category='vertical', parent=self.sequential)
            self.vertical2 = BlockFactory.create(category='vertical', parent=self.sequential)

        if hasattr(self, 'user_one'):
            CourseEnrollment.enroll(self.engaged_user, course.id)
        if hasattr(self, 'user_two'):
            CourseEnrollment.enroll(self.cruft_user, course.id)
        return course

    def submit_faux_completions(self):
        """
        Submit completions (only for user_one)
        """
        for block in self.sequential.get_children():
            models.BlockCompletion.objects.submit_completion(
                user=self.engaged_user,
                block_key=block.location,
                completion=1.0
            )

    @override_settings(LMS_ROOT_URL='test_url:9999')
    def test_retrieve_last_sitewide_block_completed(self):
        """
        Test that the method returns a URL for the "last completed" block
        when sending a user object
        """
        block_url = retrieve_last_sitewide_block_completed(
            self.engaged_user
        )
        empty_block_url = retrieve_last_sitewide_block_completed(
            self.cruft_user
        )
        assert block_url ==\
               'test_url:9999/courses/course-v1:{org}+{course}+{run}/jump_to/'\
               'block-v1:{org}+{course}+{run}+type@vertical+block@{vertical_id}'.format(
                   org=self.course.location.course_key.org,
                   course=self.course.location.course_key.course,
                   run=self.course.location.course_key.run,
                   vertical_id=self.vertical2.location.block_id
               )

        assert empty_block_url is None


@ddt.ddt
class TestGetNamedStorage(TestCase):
    """
    Tests for the get_named_storage utility.
    """

    @ddt.data(
        # STORAGES dict config for avatars
        (
            'avatars',
            'AVATAR_BACKEND',
            {'avatars': {
                'BACKEND': 'django.core.files.storage.FileSystemStorage',
                'OPTIONS': {'location': '/tmp/avatars'}
            }},
            None,  # No legacy config
            '/tmp/avatars'
        ),
        # Legacy config for avatars
        (
            'avatars',
            'AVATAR_BACKEND',
            {},  # Empty STORAGES dict
            {
                'class': 'django.core.files.storage.FileSystemStorage',
                'options': {'location': '/tmp/legacy_avatars'}
            },
            '/tmp/legacy_avatars'
        ),
        # STORAGES dict config for certificates
        (
            'certificates',
            'CERTIFICATE_BACKEND',
            {'certificates': {
                'BACKEND': 'django.core.files.storage.FileSystemStorage',
                'OPTIONS': {'location': '/tmp/certificates'}
            }},
            None,
            '/tmp/certificates'
        ),
        # ✅ Legacy config for certificates
        (
            'certificates',
            'CERTIFICATE_BACKEND',
            {},  # Empty STORAGES dict
            {
                'class': 'django.core.files.storage.FileSystemStorage',
                'options': {'location': '/tmp/legacy_certificates'}
            },
            '/tmp/legacy_certificates'
        ),
    )
    @ddt.unpack
    def test_get_named_storage(
        self,
        storage_name,
        legacy_setting_name,
        storages_config,
        legacy_config,
        expected_location
    ):
        """
        Test get_named_storage with both STORAGES dict and legacy config.
        """
        # Apply dynamic settings
        with override_settings(STORAGES=storages_config or {}):
            if legacy_config:
                setattr(settings, legacy_setting_name, legacy_config)
            elif hasattr(settings, legacy_setting_name):
                delattr(settings, legacy_setting_name)

            storage = get_named_storage(storage_name, legacy_setting_name=legacy_setting_name)
            self.assertEqual(storage.location, expected_location)

    def test_fallback_to_default_storage(self):
        """
        Test fallback to default_storage when neither STORAGES dict nor legacy config is defined.
        """
        with override_settings(STORAGES={}):
            for legacy_setting in ['AVATAR_BACKEND', 'CERTIFICATE_BACKEND', 'PROFILE_IMAGE_BACKEND']:
                if hasattr(settings, legacy_setting):
                    delattr(settings, legacy_setting)

            for storage_name, legacy_setting_name in [
                ('avatars', 'AVATAR_BACKEND'),
                ('certificates', 'CERTIFICATE_BACKEND'),
                ('profile_image', 'PROFILE_IMAGE_BACKEND'),
            ]:
                storage = get_named_storage(storage_name, legacy_setting_name=legacy_setting_name)
                self.assertEqual(storage, default_storage)
