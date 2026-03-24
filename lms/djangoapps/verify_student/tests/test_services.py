"""
Tests for the service classes in verify_student.
"""

import itertools
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import quote

import ddt
from django.conf import settings
from django.test import TestCase, override_settings
from django.utils.timezone import now
from django.utils.translation import gettext as _
from freezegun import freeze_time
from openedx_filters import PipelineStep
from pytz import utc

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.verify_student.models import (
    ManualVerification,
    SoftwareSecurePhotoVerification,
    SSOVerification,
    VerificationAttempt
)
from lms.djangoapps.verify_student.services import IDVerificationService
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from xmodule.modulestore.tests.django_utils import \
    ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

FAKE_SETTINGS = {
    "DAYS_GOOD_FOR": 365,
}


class TestIdvPageUrlRequestedPipelineStep(PipelineStep):
    """ Utility function to test a configured pipeline step """
    TEST_URL = 'example.com/verify'

    def run_filter(self, url):  # pylint: disable=arguments-differ
        return {
            "url": self.TEST_URL
        }


@patch.dict(settings.VERIFY_STUDENT, FAKE_SETTINGS)
@ddt.ddt
class TestIDVerificationService(ModuleStoreTestCase):
    """
    Tests for IDVerificationService.
    """

    @ddt.data(
        SoftwareSecurePhotoVerification, VerificationAttempt
    )
    def test_user_is_verified(self, verification_model):
        """
        Test to make sure we correctly answer whether a user has been verified.
        """
        user = UserFactory.create()
        attempt = verification_model(user=user)
        attempt.save()

        # If it's any of these, they're not verified...
        for status in ["created", "ready", "denied", "submitted", "must_retry"]:
            attempt.status = status
            attempt.save()
            assert not IDVerificationService.user_is_verified(user), status

        attempt.status = "approved"
        if verification_model == VerificationAttempt:
            attempt.expiration_datetime = now() + timedelta(days=19)
        else:
            attempt.expiration_date = now() + timedelta(days=19)
        attempt.save()

        assert IDVerificationService.user_is_verified(user), attempt.status

    @ddt.data(
        SoftwareSecurePhotoVerification, VerificationAttempt
    )
    def test_user_has_valid_or_pending(self, verification_model):
        """
        Determine whether we have to prompt this user to verify, or if they've
        already at least initiated a verification submission.
        """
        user = UserFactory.create()
        attempt = verification_model(user=user)

        # If it's any of these statuses, they don't have anything outstanding
        for status in ["created", "ready", "denied"]:
            attempt.status = status
            attempt.save()
            assert not IDVerificationService.user_has_valid_or_pending(user), status

        # Any of these, and we are. Note the benefit of the doubt we're giving
        # -- must_retry, and submitted both count until we hear otherwise
        for status in ["submitted", "must_retry", "approved"]:
            attempt.status = status
            if verification_model == VerificationAttempt:
                attempt.expiration_datetime = now() + timedelta(days=19)
            else:
                attempt.expiration_date = now() + timedelta(days=19)
            attempt.save()
            assert IDVerificationService.user_has_valid_or_pending(user), status

    @ddt.unpack
    @ddt.data(
        {'enrollment_mode': 'honor', 'status': None, 'output': 'N/A'},
        {'enrollment_mode': 'audit', 'status': None, 'output': 'N/A'},
        {'enrollment_mode': 'verified', 'status': False, 'output': 'Not ID Verified'},
        {'enrollment_mode': 'verified', 'status': True, 'output': 'ID Verified'},
    )
    def test_verification_status_for_user(self, enrollment_mode, status, output):
        """
        Verify verification_status_for_user returns correct status.
        """
        user = UserFactory.create()
        CourseFactory.create()

        with patch(
            'lms.djangoapps.verify_student.services.IDVerificationService.user_is_verified'
        ) as mock_verification:
            mock_verification.return_value = status

            status = IDVerificationService.verification_status_for_user(user, enrollment_mode)
            assert status == output

    def test_get_verified_user_ids(self):
        """
        Tests for getting users that are verified.
        """
        user_a = UserFactory.create()
        user_b = UserFactory.create()
        user_c = UserFactory.create()
        user_d = UserFactory.create()
        user_unverified = UserFactory.create()
        user_denied = UserFactory.create()
        user_denied_b = UserFactory.create()

        SoftwareSecurePhotoVerification.objects.create(user=user_a, status='approved')
        ManualVerification.objects.create(user=user_b, status='approved')
        SSOVerification.objects.create(user=user_c, status='approved')
        VerificationAttempt.objects.create(user=user_d, status='approved')
        SSOVerification.objects.create(user=user_denied, status='denied')
        VerificationAttempt.objects.create(user=user_denied_b, status='denied')

        verified_user_ids = set(IDVerificationService.get_verified_user_ids([
            user_a, user_b, user_c, user_d, user_unverified, user_denied
        ]))
        expected_user_ids = {user_a.id, user_b.id, user_c.id, user_d.id}

        assert expected_user_ids == verified_user_ids

    def test_get_verify_location_no_course_key(self):
        """
        Test for the path to the IDV flow with no course key given
        """
        path = IDVerificationService.get_verify_location()
        expected_path = f'{settings.ACCOUNT_MICROFRONTEND_URL}/id-verification'
        assert path == expected_path

    def test_get_verify_location_from_course_id(self):
        """
        Test for the path to the IDV flow with a course ID
        """
        course = CourseFactory.create(org='Robot', number='999', display_name='Test Course')
        path = IDVerificationService.get_verify_location(course.id)
        expected_path = f'{settings.ACCOUNT_MICROFRONTEND_URL}/id-verification'
        assert path == (expected_path + '?course_id=course-v1%3ARobot%2B999%2BTest_Course')

    def test_get_verify_location_from_string(self):
        """
        Test for the path to the IDV flow with a course key string
        """
        path = IDVerificationService.get_verify_location('course-v1:edX+DemoX+Demo_Course')
        expected_path = f'{settings.ACCOUNT_MICROFRONTEND_URL}/id-verification'
        assert path == (expected_path + '?course_id=course-v1%3AedX%2BDemoX%2BDemo_Course')

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.idv.page.url.requested.v1": {
                "pipeline": [
                    "lms.djangoapps.verify_student.tests.test_services.TestIdvPageUrlRequestedPipelineStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_get_verify_location_with_filter_step(self):
        """
        Test IDV flow location can be customized with an openedx filter
        """
        url = IDVerificationService.get_verify_location()
        assert url == TestIdvPageUrlRequestedPipelineStep.TEST_URL

        url = IDVerificationService.get_verify_location('course-v1:edX+DemoX+Demo_Course')
        assert url == TestIdvPageUrlRequestedPipelineStep.TEST_URL

    def test_get_expiration_datetime(self):
        """
        Test that the latest expiration datetime is returned if there are multiple records
        """
        user_a = UserFactory.create()

        SSOVerification.objects.create(
            user=user_a, status='approved', expiration_date=datetime(2021, 11, 12, 0, 0, tzinfo=timezone.utc)
        )
        newer_record = SSOVerification.objects.create(
            user=user_a, status='approved', expiration_date=datetime(2022, 1, 12, 0, 0, tzinfo=timezone.utc)
        )

        expiration_datetime = IDVerificationService.get_expiration_datetime(user_a, ['approved'])
        assert expiration_datetime == newer_record.expiration_datetime

    def test_get_expiration_datetime_mixed_models(self):
        """
        Test that the latest expiration datetime is returned if there are both instances of
        IDVerification models and VerificationAttempt models
        """
        user = UserFactory.create()

        SoftwareSecurePhotoVerification.objects.create(
            user=user, status='approved', expiration_date=datetime(2021, 11, 12, 0, 0, tzinfo=timezone.utc)
        )
        newest = VerificationAttempt.objects.create(
            user=user, status='approved', expiration_datetime=datetime(2022, 1, 12, 0, 0, tzinfo=timezone.utc)
        )

        expiration_datetime = IDVerificationService.get_expiration_datetime(user, ['approved'])
        assert expiration_datetime == newest.expiration_datetime

    def _get_verify_url_with_config(
        self,
        *,
        get_value_side_effect,
        course_id=None,
        settings_account_mfe=None,
    ):
        """
        Build the IDV URL using a patched configuration helper and optional settings override.
        Returns the final URL after the (stubbed) filter runs.
        """
        settings_ctx = (
            override_settings(ACCOUNT_MICROFRONTEND_URL=settings_account_mfe)
            if settings_account_mfe is not None
            else nullcontext()
        )

        with settings_ctx:
            with patch(
                "lms.djangoapps.verify_student.services.configuration_helpers.get_value",
                side_effect=get_value_side_effect,
            ):
                with patch(
                    "lms.djangoapps.verify_student.services.IDVPageURLRequested.run_filter",
                    side_effect=lambda url: url,
                ):
                    return IDVerificationService.get_verify_location(course_id)

    def test_get_verify_location_uses_site_config_value(self):
        """
        If site config defines ACCOUNT_MICROFRONTEND_URL (with trailing slash),
        the base should come from it and the slash should be stripped.
        """
        siteconf_url = "https://accounts.siteconf.example/"  # trailing slash on purpose

        url = self._get_verify_url_with_config(
            get_value_side_effect=lambda key, default=None, *a, **k:
                siteconf_url if key == "ACCOUNT_MICROFRONTEND_URL" else default
        )

        assert url == "https://accounts.siteconf.example/id-verification"

    def test_get_verify_location_uses_site_config_value_with_course_id(self):
        """
        Same as above, but with a course_id. Ensure proper quoting and base from site config.
        """
        course = CourseFactory.create(org='Robot', number='999', display_name='Test Course')
        siteconf_url = "https://accounts.siteconf.example/"

        url = self._get_verify_url_with_config(
            get_value_side_effect=lambda key, default=None, *a, **k:
                siteconf_url if key == "ACCOUNT_MICROFRONTEND_URL" else default,
            course_id=course.id,
        )

        expected = f"https://accounts.siteconf.example/id-verification?course_id={quote(str(course.id), safe='')}"
        assert url == expected

    def test_get_verify_location_falls_back_to_settings(self):
        """
        If site config does not override, fall back to settings.ACCOUNT_MICROFRONTEND_URL
        (and strip any trailing slash).
        """
        fallback = "https://accounts.settings.example/"  # trailing slash on purpose

        url = self._get_verify_url_with_config(
            get_value_side_effect=lambda key, default=None, *a, **k: default,
            settings_account_mfe=fallback,
        )

        assert url == "https://accounts.settings.example/id-verification"

    def test_get_verify_location_when_site_config_empty(self):
        """
        If site config explicitly returns an empty string, we should still produce a valid
        absolute path under the LMS domain.
        """
        url = self._get_verify_url_with_config(
            get_value_side_effect=lambda key, default=None, *a, **k: ""
        )

        assert url == "/id-verification"


@patch.dict(settings.VERIFY_STUDENT, FAKE_SETTINGS)
@ddt.ddt
class TestIDVerificationServiceUserStatus(TestCase):
    """
    Tests for the IDVerificationService.user_status() function.
    because the status is dependent on recency of
    verifications and in order to control the recency,
    we just put everything inside of a frozen time
    """
    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()

    def test_no_verification(self):
        with freeze_time('2014-12-12'):
            status = IDVerificationService.user_status(self.user)
            expected_status = {'status': 'none', 'error': '', 'should_display': True, 'verification_expiry': '',
                               'status_date': ''}
            self.assertDictEqual(status, expected_status)

    def test_approved_software_secure_verification(self):
        with freeze_time('2015-01-02'):
            # test for when photo verification has been created
            SoftwareSecurePhotoVerification.objects.create(user=self.user, status='approved')
            status = IDVerificationService.user_status(self.user)
            expected_status = {'status': 'approved', 'error': '', 'should_display': True, 'verification_expiry': '',
                               'status_date': datetime.now(utc)}
            self.assertDictEqual(status, expected_status)

    def test_denied_software_secure_verification(self):
        with freeze_time('2015-2-02'):
            # create denied photo verification for the user, make sure the denial
            # is handled properly
            SoftwareSecurePhotoVerification.objects.create(
                user=self.user, status='denied', error_msg='[{"photoIdReasons": ["Not provided"]}]'
            )
            status = IDVerificationService.user_status(self.user)
            expected_status = {
                'status': 'must_reverify', 'error': ['id_image_missing'],
                'should_display': True, 'verification_expiry': '', 'status_date': '',
            }
            self.assertDictEqual(status, expected_status)

    def test_approved_verification_attempt_verification(self):
        with freeze_time('2015-01-02'):
            # test for when Verification Attempt verification has been created
            VerificationAttempt.objects.create(user=self.user, status='approved')
            status = IDVerificationService.user_status(self.user)
            expected_status = {'status': 'approved', 'error': '', 'should_display': True, 'verification_expiry': '',
                               'status_date': datetime.now(utc)}
            self.assertDictEqual(status, expected_status)

    def test_denied_verification_attempt_verification(self):
        with freeze_time('2015-2-02'):
            # create denied photo verification for the user, make sure the denial
            # is handled properly
            VerificationAttempt.objects.create(
                user=self.user, status='denied'
            )
            status = IDVerificationService.user_status(self.user)
            expected_status = {
                'status': 'must_reverify', 'error': '',
                'should_display': True, 'verification_expiry': '', 'status_date': '',
            }
            self.assertDictEqual(status, expected_status)

    def test_approved_sso_verification(self):
        with freeze_time('2015-03-02'):
            # test for when sso verification has been created
            SSOVerification.objects.create(user=self.user, status='approved')
            status = IDVerificationService.user_status(self.user)
            expected_status = {'status': 'approved', 'error': '', 'should_display': False, 'verification_expiry': '',
                               'status_date': datetime.now(utc)}
            self.assertDictEqual(status, expected_status)

    def test_denied_sso_verification(self):
        with freeze_time('2015-04-02'):
            # create denied sso verification for the user, make sure the denial
            # is handled properly
            SSOVerification.objects.create(user=self.user, status='denied')
            status = IDVerificationService.user_status(self.user)
            expected_status = {
                'status': 'must_reverify', 'error': '', 'should_display': False,
                'verification_expiry': '', 'status_date': ''
            }
            self.assertDictEqual(status, expected_status)

    def test_manual_verification(self):
        with freeze_time('2015-05-02'):
            # test for when manual verification has been created
            ManualVerification.objects.create(user=self.user, status='approved')
            status = IDVerificationService.user_status(self.user)
            expected_status = {'status': 'approved', 'error': '', 'should_display': False, 'verification_expiry': '',
                               'status_date': datetime.now(utc)}
            self.assertDictEqual(status, expected_status)

    @ddt.idata(itertools.product(
        [SoftwareSecurePhotoVerification, VerificationAttempt],
        ['submitted', 'denied', 'approved', 'created', 'ready', 'must_retry'])
    )
    def test_expiring_software_secure_verification(self, value):
        verification_model, new_status = value
        with freeze_time('2015-07-11') as frozen_datetime:
            # create approved photo verification for the user
            verification_model.objects.create(user=self.user, status='approved')
            expiring_datetime = datetime.now(utc)
            frozen_datetime.move_to('2015-07-14')
            # create another according to status passed in.
            verification_model.objects.create(user=self.user, status=new_status)
            status_date = expiring_datetime
            if new_status == 'approved':
                status_date = datetime.now(utc)
            expected_status = {'status': 'approved', 'error': '', 'should_display': True, 'verification_expiry': '',
                               'status_date': status_date}
            status = IDVerificationService.user_status(self.user)
            self.assertDictEqual(status, expected_status)

    @ddt.data(SoftwareSecurePhotoVerification, VerificationAttempt)
    def test_expired_verification(self, verification_model):
        with freeze_time('2015-07-11') as frozen_datetime:
            key = 'expiration_datetime' if verification_model == VerificationAttempt else 'expiration_date'

            # create approved verification for the user
            verification_model.objects.create(
                user=self.user,
                status='approved',
                **{key: now() + timedelta(days=settings.VERIFY_STUDENT["DAYS_GOOD_FOR"])},
            )
            frozen_datetime.move_to('2016-07-11')
            expected_status = {
                'status': 'expired',
                'error': _("Your {platform_name} verification has expired.").format(
                    platform_name=configuration_helpers.get_value('platform_name', settings.PLATFORM_NAME)
                ),
                'should_display': True,
                'verification_expiry': '',
                'status_date': ''
            }
            status = IDVerificationService.user_status(self.user)
            self.assertDictEqual(status, expected_status)

    @ddt.idata(itertools.product(
        [SoftwareSecurePhotoVerification, VerificationAttempt],
        ['submitted', 'denied', 'approved', 'created', 'ready', 'must_retry'])
    )
    def test_reverify_after_expired(self, value):
        verification_model, new_status = value
        with freeze_time('2015-07-11') as frozen_datetime:
            key = 'expiration_datetime' if verification_model == VerificationAttempt else 'expiration_date'

            # create approved verification for the user
            verification_model.objects.create(
                user=self.user,
                status='approved',
                **{key: now() + timedelta(days=settings.VERIFY_STUDENT["DAYS_GOOD_FOR"])},
            )

            frozen_datetime.move_to('2016-07-12')
            # create another according to status passed in.
            verification_model.objects.create(
                user=self.user,
                status=new_status,
                **{key: now() + timedelta(days=settings.VERIFY_STUDENT["DAYS_GOOD_FOR"])},
            )

            check_status = new_status
            status_date = ''
            if new_status in ('submitted', 'must_retry'):
                check_status = 'pending'
            elif new_status in ('created', 'ready'):
                check_status = 'none'
            elif new_status == 'denied':
                check_status = 'must_reverify'
            else:
                status_date = now()

            expected_status = {'status': check_status, 'error': '', 'should_display': True, 'verification_expiry': '',
                               'status_date': status_date}
            status = IDVerificationService.user_status(self.user)
            self.assertDictEqual(status, expected_status)

    @ddt.data(
        SSOVerification,
        ManualVerification,
        VerificationAttempt
    )
    def test_override_verification(self, verification_model):
        with freeze_time('2015-07-11') as frozen_datetime:
            # create approved photo verification for the user
            SoftwareSecurePhotoVerification.objects.create(
                user=self.user,
                status='approved',
                expiration_date=now() + timedelta(days=settings.VERIFY_STUDENT["DAYS_GOOD_FOR"])
            )
            frozen_datetime.move_to('2015-07-14')

            key = 'expiration_datetime' if verification_model == VerificationAttempt else 'expiration_date'
            verification_model.objects.create(
                user=self.user,
                status='approved',
                **{key: now() + timedelta(days=settings.VERIFY_STUDENT["DAYS_GOOD_FOR"])},
            )
            expected_status = {
                'status': 'approved', 'error': '',
                'should_display': verification_model == VerificationAttempt,
                'verification_expiry': '', 'status_date': now()
            }
            status = IDVerificationService.user_status(self.user)
            self.assertDictEqual(status, expected_status)

    @ddt.data(
        SSOVerification,
        ManualVerification,
        VerificationAttempt
    )
    def test_denied_after_approved_verification(self, verification_model):
        with freeze_time('2015-07-11') as frozen_datetime:
            # create approved photo verification for the user
            SoftwareSecurePhotoVerification.objects.create(
                user=self.user,
                status='approved',
                expiration_date=now() + timedelta(days=settings.VERIFY_STUDENT["DAYS_GOOD_FOR"])
            )
            expected_date = now()
            frozen_datetime.move_to('2015-07-14')

            key = 'expiration_datetime' if verification_model == VerificationAttempt else 'expiration_date'
            verification_model.objects.create(
                user=self.user,
                status='denied',
                **{key: now() + timedelta(days=settings.VERIFY_STUDENT["DAYS_GOOD_FOR"])},
            )
            expected_status = {
                'status': 'approved', 'error': '', 'should_display': True,
                'verification_expiry': '', 'status_date': expected_date
            }
            status = IDVerificationService.user_status(self.user)
            self.assertDictEqual(status, expected_status)
