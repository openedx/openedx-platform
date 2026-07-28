"""
Unit tests for user account utility functions.

Includes tests for social links, social-auth PII redaction, completion, etc.
"""

from contextlib import contextmanager

import ddt
from completion import models
from completion.test_utils import CompletionWaffleTestMixin
from django.db import connection
from django.db.models.signals import pre_delete
from django.test import TestCase
from django.test.utils import CaptureQueriesContext, override_settings
from django.utils import timezone
from social_django.models import UserSocialAuth

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.user_api.accounts.signals import redact_social_auth_pii_before_deletion
from openedx.core.djangoapps.user_api.accounts.utils import (
    REDACTED_SOCIAL_AUTH_UID_PREFIX,
    redact_and_delete_historical_social_auth,
    redact_and_delete_social_auth,
    retrieve_last_sitewide_block_completed,
)
from openedx.core.djangolib.testing.utils import assert_redact_before_delete, skip_unless_lms
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import (  # pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
)

from ..utils import format_social_link, validate_social_link


# Use a context manager to guarantee signal reconnection between tests.
@contextmanager
def disconnected_social_auth_redaction_signal():
    """
    Temporarily disconnect the fallback signal so tests exercise the helper path.
    """
    pre_delete.disconnect(redact_social_auth_pii_before_deletion, sender=UserSocialAuth)
    try:
        yield
    finally:
        pre_delete.connect(redact_social_auth_pii_before_deletion, sender=UserSocialAuth)


@ddt.ddt
class UserAccountSettingsTest(TestCase):
    """Unit tests for setting Social Media Links."""

    def setUp(self):  # pylint: disable=useless-super-delegation
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
               'block-v1:{org}+{course}+{run}+type@vertical+block@{vertical_id}'.format(  # noqa: UP032
                   org=self.course.location.course_key.org,
                   course=self.course.location.course_key.course,
                   run=self.course.location.course_key.run,
                   vertical_id=self.vertical2.location.block_id
               )

        assert empty_block_url is None


@ddt.ddt
@skip_unless_lms
class RedactAndDeleteSocialAuthTest(TestCase):
    """
    Tests for the redact_and_delete_social_auth utility function.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(username='testuser', email='testuser@example.com')

    def create_social_auth(self, provider='google-oauth2', uid='user@example.com', extra_data=None):
        """
        Helper method to create UserSocialAuth instances for testing.
        """
        extra_data = extra_data or {
            'email': f'{provider}@example.com',
            'name': f'{provider.capitalize()} User',
            'id': '123456789',
        }
        return UserSocialAuth.objects.create(
            user=self.user,
            provider=provider,
            uid=uid,
            extra_data=extra_data,
        )

    @ddt.data(
        [
            (
                'google-oauth2',
                'google@example.com',
                {'email': 'google@example.com', 'name': 'Google User'},
            ),
        ],
        [
            (
                'google-oauth2',
                'google@example.com',
                {'email': 'google@example.com', 'name': 'Google User'},
            ),
            (
                'tpa-saml',
                'saml@example.com',
                {'email': 'saml@example.com', 'name': 'SAML User', 'uid': 'saml-uid'},
            ),
        ],
    )
    def test_redact_and_delete_redacts_sso_records(self, social_auth_records):
        """
        Test that redact_and_delete_social_auth redacts and deletes all SSO records for a user.
        """
        social_auth_ids = [
            self.create_social_auth(provider=provider, uid=uid, extra_data=extra_data).pk
            for provider, uid, extra_data in social_auth_records
        ]

        with disconnected_social_auth_redaction_signal(), CaptureQueriesContext(connection) as ctx:
            redact_and_delete_social_auth(self.user.id)

        assert_redact_before_delete(
            [query['sql'] for query in ctx],
            table=UserSocialAuth._meta.db_table,
            expected_redacted_value_list=[REDACTED_SOCIAL_AUTH_UID_PREFIX],
        )
        assert not UserSocialAuth.objects.filter(id__in=social_auth_ids).exists()


@skip_unless_lms
class RedactAndDeleteHistoricalSocialAuthTest(TestCase):
    """
    Tests for the redact_and_delete_historical_social_auth utility function.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(username='testuser', email='testuser@example.com')
        self.historical_social_auth_model = UserSocialAuth.history.model

    def _create_historical_record(
        self,
        provider='google-oauth2',
        uid='user@example.com',
        extra_data=None,
        source_id=1,
        user=None,
    ):
        """
        Create a HistoricalUserSocialAuth record directly for test setup.
        """
        if extra_data is None:
            extra_data = {'email': uid, 'name': 'Test User'}
        user = user or self.user
        self.historical_social_auth_model.objects.create(
            user=user,
            id=source_id,
            provider=provider,
            uid=uid,
            extra_data=extra_data,
            created=timezone.now(),
            modified=timezone.now(),
            history_date=timezone.now(),
            history_type='+',
        )

    def test_historical_social_auth_redact_before_delete(self):
        """
        Ensure HistoricalUserSocialAuth records are properly redacted and deleted for retirement.

        The fields uid (email format) and extra_data must be redacted before delete.
        """
        self._create_historical_record(provider='google-oauth2', uid='google@example.com', source_id=1)
        self._create_historical_record(provider='tpa-saml', uid='saml@example.com', source_id=2)

        other_user = UserFactory.create(username='otheruser', email='other@example.com')
        self._create_historical_record(
            provider='google-oauth2',
            uid='other@example.com',
            extra_data={},
            source_id=3,
            user=other_user,
        )

        with CaptureQueriesContext(connection) as ctx:
            redact_and_delete_historical_social_auth(self.user.id)

        assert_redact_before_delete(
            [query['sql'] for query in ctx],
            table=self.historical_social_auth_model._meta.db_table,
            expected_redacted_value_list=[REDACTED_SOCIAL_AUTH_UID_PREFIX],
        )
        assert not self.historical_social_auth_model.objects.filter(user=self.user).exists()
        assert self.historical_social_auth_model.objects.filter(user=other_user).exists()
