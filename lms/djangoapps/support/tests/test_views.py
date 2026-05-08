"""
Tests for support views.
"""

import itertools
import json
import re
from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.parse import quote
from uuid import UUID, uuid4

import ddt
from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.db.models import signals
from django.http import HttpResponse
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone
from edx_proctoring.api import create_exam_attempt, update_attempt_status
from edx_proctoring.models import ProctoredExam
from edx_proctoring.runtime import set_runtime_service
from edx_proctoring.statuses import ProctoredExamStudentAttemptStatus
from edx_proctoring.tests.test_services import MockLearningSequencesService, MockScheduleItemData
from edx_proctoring.tests.test_utils.utils import ProctoredExamTestCase
from oauth2_provider.models import AccessToken, RefreshToken
from opaque_keys.edx.locator import BlockUsageLocator
from organizations.tests.factories import OrganizationFactory
from pytz import UTC
from rest_framework import status
from social_django.models import UserSocialAuth

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.entitlements.tests.factories import CourseEntitlementFactory
from common.djangoapps.student.models import (
    ENROLLED_TO_ENROLLED,
    UNENROLLED_TO_ENROLLED,
    CourseEnrollment,
    CourseEnrollmentAttribute,
    ManualEnrollmentAudit,
)
from common.djangoapps.student.roles import GlobalStaff, SupportStaffRole
from common.djangoapps.student.tests.factories import (
    CourseEnrollmentAttributeFactory,
    CourseEnrollmentFactory,
    UserFactory,
)
from common.djangoapps.third_party_auth.tests.factories import SAMLProviderConfigFactory
from common.test.utils import assert_dict_contains_subset, disable_signal
from lms.djangoapps.program_enrollments.tests.factories import ProgramCourseEnrollmentFactory, ProgramEnrollmentFactory
from lms.djangoapps.support.models import CourseResetAudit
from lms.djangoapps.support.serializers import ProgramEnrollmentSerializer
from lms.djangoapps.support.tests.factories import CourseResetAuditFactory, CourseResetCourseOptInFactory
from lms.djangoapps.verify_student.models import VerificationDeadline
from lms.djangoapps.verify_student.services import IDVerificationService
from lms.djangoapps.verify_student.tests.factories import SSOVerificationFactory
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.oauth_dispatch.tests import factories
from openedx.features.content_type_gating.models import ContentTypeGatingConfig
from openedx.features.course_duration_limits.models import CourseDurationLimitConfig
from openedx.features.enterprise_support.api import enterprise_is_enabled
from openedx.features.enterprise_support.tests.factories import (
    EnterpriseCourseEnrollmentFactory,
    EnterpriseCustomerUserFactory,
)
from xmodule.modulestore.tests.django_utils import (
    TEST_DATA_SPLIT_MODULESTORE,
    ModuleStoreTestCase,
    SharedModuleStoreTestCase,
)
from xmodule.modulestore.tests.factories import CourseFactory

try:
    from consent.models import DataSharingConsent
except ImportError:  # pragma: no cover
    pass


class SupportViewTestCase(ModuleStoreTestCase):
    """
    Base class for support view tests.
    """

    USERNAME = "support"
    EMAIL = "support@example.com"
    PASSWORD = "support"

    def setUp(self):
        """Create a user and log in. """
        super().setUp()
        self.user = UserFactory(username=self.USERNAME, email=self.EMAIL, password=self.PASSWORD)
        self.course = CourseFactory.create()
        success = self.client.login(username=self.USERNAME, password=self.PASSWORD)
        assert success, 'Could not log in'


class SupportViewManageUserTests(SupportViewTestCase):
    """
    Base class for support view tests.
    """

    ZENDESK_URL = 'http://zendesk.example.com/'

    def setUp(self):
        """Make the user support staff"""
        super().setUp()
        SupportStaffRole().add_users(self.user)

    @override_settings(ZENDESK_URL=ZENDESK_URL)
    def test_get_contact_us(self):
        """
        Tests Support View contact us Page
        """
        url = reverse('support:contact_us')
        response = self.client.get(url)
        assert response.status_code == 200

    def test_get_contact_us_redirect_if_undefined_zendesk_url(self):
        """
        Tests the Support contact us Page redirects if ZENDESK_URL setting is not defined
        """
        url = reverse('support:contact_us')
        response = self.client.get(url)
        assert response.status_code == 302

    def test_get_password_assistance(self):
        """
        Tests password assistance
        """
        # Ensure that user is not logged in if they need
        # password assistance.
        self.client.logout()
        url = '/password_assistance'
        response = self.client.get(url)
        assert response.status_code == 200

    def test_get_support_form(self):
        """
        Tests Support View to return Manage User Form
        """
        url = reverse('support:manage_user')
        response = self.client.get(url)
        assert response.status_code == 200

    def test_get_form_with_user_info(self):
        """
        Tests Support View to return Manage User Form
        with user info
        """
        url = reverse('support:manage_user_detail') + self.user.username
        response = self.client.get(url)
        data = json.loads(response.content.decode('utf-8'))
        assert data['username'] == self.user.username

    def test_disable_user_account(self):
        """
        Tests Support View to disable the user account
        """
        test_user = UserFactory(
            username='foobar', email='foobar@foobar.com', password='foobar'
        )

        application = factories.ApplicationFactory(user=test_user)
        access_token = factories.AccessTokenFactory(user=test_user, application=application)
        factories.RefreshTokenFactory(
            user=test_user, application=application, access_token=access_token
        )
        assert 0 != AccessToken.objects.filter(user=test_user).count()
        assert 0 != RefreshToken.objects.filter(user=test_user).count()

        url = reverse('support:manage_user_detail') + test_user.username
        response = self.client.post(url, data={
            'username_or_email': test_user.username,
            'comment': 'Test comment'
        })
        data = json.loads(response.content.decode('utf-8'))
        assert data['success_msg'] == 'User Disabled Successfully'
        test_user = User.objects.get(username=test_user.username, email=test_user.email)
        assert test_user.has_usable_password() is False
        assert 0 == AccessToken.objects.filter(user=test_user).count()
        assert 0 == RefreshToken.objects.filter(user=test_user).count()


@ddt.ddt
class SupportViewAccessTests(SupportViewTestCase):
    """
    Tests for access control of support views.
    """

    @ddt.data(*(
        (url_name, role, has_access)
        for (url_name, (role, has_access))
        in itertools.product((
            'support:index',
            'support:certificates',
            'support:enrollment',
            'support:enrollment_list',
            'support:manage_user',
            'support:manage_user_detail',
            'support:link_program_enrollments',
        ), (
            (GlobalStaff, True),
            (SupportStaffRole, True),
            (None, False)
        ))
    ))
    @ddt.unpack
    def test_access(self, url_name, role, has_access):
        if role is not None:
            role().add_users(self.user)

        url = reverse(url_name)
        response = self.client.get(url)

        if has_access:
            assert response.status_code == 200
        else:
            assert response.status_code == 403

    @ddt.data(
        "support:index",
        "support:certificates",
        "support:enrollment",
        "support:enrollment_list",
        "support:manage_user",
        "support:manage_user_detail",
        "support:link_program_enrollments",
    )
    def test_require_login(self, url_name):
        url = reverse(url_name)

        # Log out then try to retrieve the page
        self.client.logout()
        response = self.client.get(url)

        # Expect a redirect to the login page
        redirect_url = "{login_url}?next={original_url}".format(
            login_url=reverse("signin_user"),
            original_url=quote(url),
        )
        self.assertRedirects(response, redirect_url)


class SupportViewIndexTests(SupportViewTestCase):
    """
    Tests for the support index view.
    """

    EXPECTED_URL_NAMES = [
        "support:certificates",
        "support:link_program_enrollments",
    ]

    def setUp(self):
        """Make the user support staff. """
        super().setUp()
        SupportStaffRole().add_users(self.user)

    def test_index(self):
        response = self.client.get(reverse("support:index"))
        self.assertContains(response, "Support")

        # Check that all the expected links appear on the index page.
        for url_name in self.EXPECTED_URL_NAMES:
            self.assertContains(response, reverse(url_name))


class SupportViewCertificatesTests(SupportViewTestCase):
    """
    Tests for the certificates support view.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        """Make the user support staff. """
        super().setUp()
        SupportStaffRole().add_users(self.user)

    def test_certificates_no_filter(self):
        # Check that an empty initial filter is passed to the JavaScript client correctly.
        response = self.client.get(reverse("support:certificates"))
        self.assertContains(response, "userFilter: ''")

    def test_certificates_with_user_filter(self):
        # Check that an initial filter is passed to the JavaScript client.
        url = reverse("support:certificates") + "?user=student@example.com"
        response = self.client.get(url)
        self.assertContains(response, "userFilter: 'student@example.com'")

    def test_certificates_along_with_course_filter(self):
        # Check that an initial filter is passed to the JavaScript client.
        url = reverse("support:certificates") + "?user=student@example.com&course_id=" + quote(str(self.course.id))
        response = self.client.get(url)
        self.assertContains(response, "userFilter: 'student@example.com'")
        # use replase due to escaping course id:
        # https://docs.djangoproject.com/en/dev/ref/templates/builtins/#escapejs
        self.assertContains(response, "courseFilter: '" + str(self.course.id).replace('-', '\\u002D') + "'")


@ddt.ddt
class SupportViewEnrollmentsTests(SharedModuleStoreTestCase, SupportViewTestCase):
    """Tests for the enrollment support view."""

    def setUp(self):
        super().setUp()
        SupportStaffRole().add_users(self.user)

        self.course = CourseFactory(display_name='teꜱᴛ')
        self.student = UserFactory.create(username='student', email='test@example.com', password='test')

        for mode in (
                CourseMode.AUDIT, CourseMode.PROFESSIONAL, CourseMode.CREDIT_MODE,
                CourseMode.NO_ID_PROFESSIONAL_MODE, CourseMode.VERIFIED, CourseMode.HONOR
        ):
            CourseModeFactory.create(mode_slug=mode, course_id=self.course.id)

        self.verification_deadline = VerificationDeadline(
            course_key=self.course.id,
            deadline=datetime.now(UTC) + timedelta(days=365)
        )
        self.verification_deadline.save()

        self.enrollment = CourseEnrollmentFactory.create(
            mode=CourseMode.AUDIT, user=self.student, course_id=self.course.id
        )

        self.url = reverse('support:enrollment_list', kwargs={'username_or_email': self.student.username})

    def assert_enrollment(self, mode):
        """
        Assert that the student's enrollment has the correct mode.
        """
        enrollment = CourseEnrollment.get_enrollment(self.student, self.course.id)
        assert enrollment.mode == mode

    @ddt.data('username', 'email')
    def test_get_enrollments(self, search_string_type):
        url = reverse(
            'support:enrollment_list',
            kwargs={'username_or_email': getattr(self.student, search_string_type)}
        )
        response = self.client.get(url)
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert len(data) == 1
        assert_dict_contains_subset(
            self,
            {
                'mode': CourseMode.AUDIT,
                'manual_enrollment': {},
                'user': self.student.username,
                'course_id': str(self.course.id),
                'is_active': True,
                'verified_upgrade_deadline': None,
            },
            data[0],
        )
        assert {CourseMode.VERIFIED, CourseMode.AUDIT, CourseMode.HONOR, CourseMode.NO_ID_PROFESSIONAL_MODE,
                CourseMode.PROFESSIONAL, CourseMode.CREDIT_MODE} == {mode['slug'] for mode in data[0]['course_modes']}
        assert 'enterprise_course_enrollments' not in data[0]
        assert data[0]['order_number'] == ''
        assert data[0]['source_system'] == ''

    @ddt.data(*itertools.product(['username', 'email'], [(3, 'ORD-003'), (1, 'ORD-001')]))
    @ddt.unpack
    def test_order_number_information(self, search_string_type, order_details):
        for count in range(order_details[0]):
            CourseEnrollmentAttributeFactory(
                enrollment=self.enrollment,
                namespace='order',
                name='order_number',
                value='ORD-00{}'.format(count + 1)  # noqa: UP032
            )
        url = reverse(
            'support:enrollment_list',
            kwargs={'username_or_email': getattr(self.student, search_string_type)}
        )
        response = self.client.get(url)
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert len(data) == 1
        assert data[0]['order_number'] == order_details[1]

    def test_order_source_system_information(self):
        CourseEnrollmentAttributeFactory(
            enrollment=self.enrollment,
            namespace='order',
            name='source_system',
            value='commercetools'
        )
        url = reverse(
            'support:enrollment_list',
            kwargs={'username_or_email': self.student.username}
        )
        response = self.client.get(url)
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert len(data) == 1
        assert data[0]['source_system'] == 'commercetools'

    @override_settings(FEATURES=dict(ENABLE_ENTERPRISE_INTEGRATION=True))
    @enterprise_is_enabled()
    def test_get_enrollments_enterprise_enabled(self):
        url = reverse(
            'support:enrollment_list',
            kwargs={'username_or_email': self.student.username}
        )

        enterprise_customer_user = EnterpriseCustomerUserFactory(
            user_id=self.student.id
        )
        enterprise_course_enrollment = EnterpriseCourseEnrollmentFactory(
            course_id=self.course.id,
            enterprise_customer_user=enterprise_customer_user
        )
        data_sharing_consent = DataSharingConsent(
            course_id=self.course.id,
            enterprise_customer=enterprise_customer_user.enterprise_customer,
            username=self.student.username,
            granted=True
        )
        data_sharing_consent.save()

        response = self.client.get(url)
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert len(data) == 1

        enterprise_course_enrollments_data = data[0]['enterprise_course_enrollments']
        assert len(enterprise_course_enrollments_data) == 1
        expected = {
            'course_id': str(enterprise_course_enrollment.course_id),
            'enterprise_customer_name': enterprise_customer_user.enterprise_customer.name,
            'enterprise_customer_user_id': enterprise_customer_user.id,
            'license': None,
            'saved_for_later': enterprise_course_enrollment.saved_for_later,
            'data_sharing_consent': {
                'username': self.student.username,
                'enterprise_customer_uuid': str(enterprise_customer_user.enterprise_customer_id),
                'exists': data_sharing_consent.exists,
                'consent_provided': data_sharing_consent.granted,
                'consent_required': data_sharing_consent.consent_required(),
                'course_id': str(enterprise_course_enrollment.course_id),
            }
        }
        assert enterprise_course_enrollments_data[0] == expected

    @ddt.data(
        (True, 'Self Paced'),
        (False, 'Instructor Paced')
    )
    @ddt.unpack
    def test_pacing_type(self, is_self_paced, pacing_type):
        """
        Test correct pacing type is returned in the enrollment.
        """
        # Course enrollment is made against course overview. Therefore, the self_paced
        # attr of course overview needs to be updated before getting the enrollment data.
        course_overview = CourseOverview.get_from_id(self.course.id)
        course_overview.self_paced = is_self_paced
        course_overview.save()
        response = self.client.get(self.url)
        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert len(data) == 1
        self.assertEqual(data[0]['pacing_type'], pacing_type)  # noqa: PT009

    def test_get_manual_enrollment_history(self):
        ManualEnrollmentAudit.create_manual_enrollment_audit(
            self.user,
            self.student.email,
            ENROLLED_TO_ENROLLED,
            'Financial Assistance',
            CourseEnrollment.objects.get(course_id=self.course.id, user=self.student)
        )
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert_dict_contains_subset(
            self,
            {
                'enrolled_by': self.user.email,
                'reason': 'Financial Assistance',
            },
            json.loads(response.content.decode('utf-8'))[0]['manual_enrollment'],
        )

    @disable_signal(signals, 'post_save')
    @ddt.data('username', 'email')
    def test_create_new_enrollment(self, search_string_type):
        """
        Assert that a new enrollment is created through post request endpoint.
        """
        test_user = UserFactory.create(username='newStudent', email='test2@example.com', password='test')
        assert ManualEnrollmentAudit.get_manual_enrollment_by_email(test_user.email) is None
        url = reverse(
            'support:enrollment_list',
            kwargs={'username_or_email': getattr(test_user, search_string_type)}
        )
        response = self.client.post(url, data={
            'course_id': str(self.course.id),
            'mode': CourseMode.AUDIT,
            'reason': 'Financial Assistance'
        })
        assert response.status_code == 200
        manual_enrollment = ManualEnrollmentAudit.get_manual_enrollment_by_email(test_user.email)
        assert manual_enrollment is not None
        assert manual_enrollment.reason == response.json()['reason']
        assert manual_enrollment.enrolled_email == 'test2@example.com'
        assert manual_enrollment.state_transition == UNENROLLED_TO_ENROLLED

    @disable_signal(signals, 'post_save')
    @ddt.data('username', 'email')
    def test_create_new_enrollment_invalid_mode(self, search_string_type):
        """
        Assert that a new enrollment is not created with a vulnerable/invalid enrollment mode.
        """
        test_user = UserFactory.create(username='newStudent', email='test2@example.com', password='test')
        assert ManualEnrollmentAudit.get_manual_enrollment_by_email(test_user.email) is None
        url = reverse(
            'support:enrollment_list',
            kwargs={'username_or_email': getattr(test_user, search_string_type)}
        )
        response = self.client.post(url, data={
            'course_id': str(self.course.id),
            'mode': '<script>alert("xss")</script>',
            'reason': 'Financial Assistance'
        })
        test_key_error = b'&lt;script&gt;alert(&#34;xss&#34;)&lt;/script&gt; is not a valid mode for course-v1:org'
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert test_key_error in response.content

    @disable_signal(signals, 'post_save')
    @ddt.data('username', 'email')
    def test_create_existing_enrollment(self, search_string_type):
        """
        Assert that a new enrollment is not created when an enrollment already exist for that course.
        """
        assert ManualEnrollmentAudit.get_manual_enrollment_by_email(self.student.email) is None
        url = reverse(
            'support:enrollment_list',
            kwargs={'username_or_email': getattr(self.student, search_string_type)}
        )
        response = self.client.post(url, data={
            'course_id': str(self.course.id),
            'mode': CourseMode.AUDIT,
            'reason': 'Financial Assistance'
        })
        assert response.status_code == 400
        assert ManualEnrollmentAudit.get_manual_enrollment_by_email(self.student.email) is None

    @disable_signal(signals, 'post_save')
    @ddt.data('username', 'email')
    def test_change_enrollment(self, search_string_type):
        """
        Assert changing mode for an enrollment.
        """
        assert ManualEnrollmentAudit.get_manual_enrollment_by_email(self.student.email) is None
        url = reverse(
            'support:enrollment_list',
            kwargs={'username_or_email': getattr(self.student, search_string_type)}
        )
        response = self.client.patch(url, data={
            'course_id': str(self.course.id),
            'old_mode': CourseMode.AUDIT,
            'new_mode': CourseMode.VERIFIED,
            'reason': 'Financial Assistance'
        }, content_type='application/json')
        assert response.status_code == 200
        assert ManualEnrollmentAudit.get_manual_enrollment_by_email(self.student.email) is not None
        self.assert_enrollment(CourseMode.VERIFIED)

    @disable_signal(signals, 'post_save')
    @ddt.data('username', 'email')
    def test_change_enrollment_invalid_old_mode(self, search_string_type):
        """
        Assert changing mode fails for an enrollment having a vulnerable/invalid old mode.
        """
        assert ManualEnrollmentAudit.get_manual_enrollment_by_email(self.student.email) is None
        url = reverse(
            'support:enrollment_list',
            kwargs={'username_or_email': getattr(self.student, search_string_type)}
        )
        response = self.client.patch(url, data={
            'course_id': str(self.course.id),
            'old_mode': '<script>alert("xss")</script>',
            'new_mode': CourseMode.VERIFIED,
            'reason': 'Financial Assistance'
        }, content_type='application/json')
        test_key_error = b'is not enrolled with mode &lt;script&gt;alert(&#34;xss&#34;)&lt;/script&gt;'
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert test_key_error in response.content

    @disable_signal(signals, 'post_save')
    @ddt.data('username', 'email')
    @patch("common.djangoapps.entitlements.models.get_course_uuid_for_course")
    def test_change_enrollment_mode_fullfills_entitlement(self, search_string_type, mock_get_course_uuid):
        """
        Assert that changing student's enrollment fulfills it's respective entitlement if it exists.
        """
        assert ManualEnrollmentAudit.get_manual_enrollment_by_email(self.student.email) is None
        enrollment = CourseEnrollment.get_enrollment(self.student, self.course.id)
        entitlement = CourseEntitlementFactory.create(
            user=self.user,
            mode=CourseMode.VERIFIED,
            enrollment_course_run=enrollment
        )
        mock_get_course_uuid.return_value = entitlement.course_uuid

        url = reverse(
            'support:enrollment_list',
            kwargs={'username_or_email': getattr(self.student, search_string_type)}
        )
        response = self.client.patch(url, data={
            'course_id': str(self.course.id),
            'old_mode': CourseMode.AUDIT,
            'new_mode': CourseMode.VERIFIED,
            'reason': 'Financial Assistance'
        }, content_type='application/json')
        entitlement.refresh_from_db()
        assert response.status_code == 200
        assert ManualEnrollmentAudit.get_manual_enrollment_by_email(self.student.email) is not None
        assert entitlement.enrollment_course_run is not None
        assert entitlement.is_entitlement_redeemable() is False
        self.assert_enrollment(CourseMode.VERIFIED)

    @ddt.data(
        ({}, r"The field \w+ is required."),
        ({'course_id': 'bad course key'}, 'Could not parse course key.'),
        ({
            'course_id': 'course-v1:TestX+T101+2015',
            'old_mode': CourseMode.AUDIT,
            'new_mode': CourseMode.VERIFIED,
            'reason': ''
        }, 'Could not find enrollment for user'),
        ({
            'course_id': None,
            'old_mode': CourseMode.HONOR,
            'new_mode': CourseMode.VERIFIED,
            'reason': ''
        }, r'User \w+ is not enrolled with mode ' + CourseMode.HONOR),
        ({
            'course_id': 'course-v1:TestX+T101+2015',
            'old_mode': CourseMode.AUDIT,
            'new_mode': CourseMode.CREDIT_MODE,
            'reason': 'Enrollment cannot be changed to credit mode'
        }, '')
    )
    @ddt.unpack
    def test_change_enrollment_bad_data(self, data, error_message):
        # `self` isn't available from within the DDT declaration, so
        # assign the course ID here
        if 'course_id' in data and data['course_id'] is None:
            data['course_id'] = str(self.course.id)
        response = self.client.patch(self.url, data, content_type='application/json')

        assert response.status_code == 400
        assert re.match(error_message, response.content.decode('utf-8').replace("'", '').replace('"', '')) is not None
        self.assert_enrollment(CourseMode.AUDIT)
        assert ManualEnrollmentAudit.get_manual_enrollment_by_email(self.student.email) is None

    @disable_signal(signals, 'post_save')
    @ddt.data('honor', 'audit', 'verified', 'professional', 'no-id-professional', 'credit')
    def test_update_enrollment_for_all_modes(self, new_mode):
        """ Verify support can changed the enrollment to all available modes"""
        self.assert_update_enrollment('username', new_mode)

    @disable_signal(signals, 'post_save')
    @ddt.data('honor', 'audit', 'verified', 'professional', 'no-id-professional')
    def test_update_enrollment_for_ended_course(self, new_mode):
        """ Verify support can changed the enrollment of archived course. """
        self.set_course_end_date_and_expiry()
        self.assert_update_enrollment('username', new_mode)

    @ddt.data('username', 'email')
    def test_get_enrollments_with_expired_mode(self, search_string_type):
        """ Verify that page can get the all modes with archived course. """
        self.set_course_end_date_and_expiry()
        url = reverse(
            'support:enrollment_list',
            kwargs={'username_or_email': getattr(self.student, search_string_type)}
        )
        response = self.client.get(url)
        self._assert_generated_modes(response)

    @disable_signal(signals, 'post_save')
    @ddt.data('username', 'email')
    def test_update_enrollments_with_expired_mode(self, search_string_type):
        """ Verify that enrollment can be updated to verified mode. """
        self.set_course_end_date_and_expiry()
        assert ManualEnrollmentAudit.get_manual_enrollment_by_email(self.student.email) is None
        self.assert_update_enrollment(search_string_type, CourseMode.VERIFIED)

    def _assert_generated_modes(self, response):
        """Dry method to generate course modes dict and test with response data."""
        modes = CourseMode.modes_for_course(self.course.id, include_expired=True, only_selectable=False)
        modes_data = []
        for mode in modes:
            expiry = mode.expiration_datetime.strftime('%Y-%m-%dT%H:%M:%SZ') if mode.expiration_datetime else None
            modes_data.append({
                'sku': mode.sku,
                'expiration_datetime': expiry,
                'name': mode.name,
                'currency': mode.currency,
                'bulk_sku': mode.bulk_sku,
                'min_price': mode.min_price,
                'suggested_prices': mode.suggested_prices,
                'slug': mode.slug,
                'description': mode.description
            })

        assert response.status_code == 200
        data = json.loads(response.content.decode('utf-8'))
        assert len(data) == 1

        assert modes_data == data[0]['course_modes']

        assert {CourseMode.VERIFIED, CourseMode.AUDIT, CourseMode.NO_ID_PROFESSIONAL_MODE, CourseMode.PROFESSIONAL,
                CourseMode.HONOR, CourseMode.CREDIT_MODE} == {mode['slug'] for mode in data[0]['course_modes']}

    def assert_update_enrollment(self, search_string_type, new_mode):
        """ Dry method to update the enrollment and assert response."""
        assert ManualEnrollmentAudit.get_manual_enrollment_by_email(self.student.email) is None
        url = reverse(
            'support:enrollment_list',
            kwargs={'username_or_email': getattr(self.student, search_string_type)}
        )

        with patch('lms.djangoapps.support.views.enrollments.get_credit_provider_attribute_values') as mock_method:
            credit_provider = (
                ['Arizona State University'], 'You are now eligible for credit from Arizona State University'
            )
            mock_method.return_value = credit_provider
            response = self.client.patch(url, data={
                'course_id': str(self.course.id),
                'old_mode': CourseMode.AUDIT,
                'new_mode': new_mode,
                'reason': 'Financial Assistance'
            }, content_type='application/json')

        assert response.status_code == 200
        assert ManualEnrollmentAudit.get_manual_enrollment_by_email(self.student.email) is not None
        self.assert_enrollment(new_mode)
        if new_mode == 'credit':
            enrollment_attr = CourseEnrollmentAttribute.objects.first()
            assert enrollment_attr.value == str(credit_provider[0])

    def set_course_end_date_and_expiry(self):
        """ Set the course-end date and expire its verified mode."""
        self.course.start = datetime(year=1970, month=1, day=1, tzinfo=UTC)
        self.course.end = datetime(year=1970, month=1, day=10, tzinfo=UTC)

        # change verified mode expiry.
        verified_mode = CourseMode.objects.get(
            course_id=self.course.id,
            mode_slug=CourseMode.VERIFIED
        )
        verified_mode.expiration_datetime = datetime(year=1970, month=1, day=9, tzinfo=UTC)
        verified_mode.save()


@ddt.ddt
class SupportViewLinkProgramEnrollmentsTests(SupportViewTestCase):
    """
    Tests for the link_program_enrollments support view.
    """
    patch_render = patch(
        'lms.djangoapps.support.views.program_enrollments.render_to_response',
        return_value=HttpResponse(),
        autospec=True,
    )

    def setUp(self):
        """Make the user support staff. """
        super().setUp()
        self.url = reverse("support:link_program_enrollments")
        SupportStaffRole().add_users(self.user)
        self.program_uuid = str(uuid4())
        self.text = '0001,user-0001\n0002,user-02'

    @patch_render
    def test_get(self, mocked_render):
        self.client.get(self.url)
        render_call_dict = mocked_render.call_args[0][1]
        assert render_call_dict == {
            'successes': [],
            'errors': [],
            'program_uuid': '',
            'text': ''
        }

    def test_rendering(self):
        """
        Test the view without mocking out the rendering like the rest of the tests.
        """
        response = self.client.get(self.url)
        content = str(response.content, encoding='utf-8')
        assert '"programUUID": ""' in content
        assert '"text": ""' in content

    @patch_render
    def test_invalid_uuid(self, mocked_render):
        self.client.post(self.url, data={
            'program_uuid': 'notauuid',
            'text': self.text,
        })
        msg = "Supplied program UUID 'notauuid' is not a valid UUID."
        render_call_dict = mocked_render.call_args[0][1]
        assert render_call_dict['errors'] == [msg]

    @patch_render
    @ddt.data(
        ('program_uuid', ''),
        ('', 'text'),
        ('', ''),
    )
    @ddt.unpack
    def test_missing_parameter(self, program_uuid, text, mocked_render):
        error = (
            "You must provide both a program uuid "
            "and a series of lines with the format "
            "'external_user_key,lms_username'."
        )
        self.client.post(self.url, data={
            'program_uuid': program_uuid,
            'text': text,
        })
        render_call_dict = mocked_render.call_args[0][1]
        assert render_call_dict['errors'] == [error]

    @ddt.data(
        '0001,learner-01\n0002,learner-02',  # normal
        '0001,learner-01,apple,orange\n0002,learner-02,purple',  # extra fields
        '\t0001        ,    \t  learner-01    \n   0002 , learner-02    ',  # whitespace
    )
    @patch('lms.djangoapps.support.views.utils.link_program_enrollments')
    def test_text(self, text, mocked_link):
        self.client.post(self.url, data={
            'program_uuid': self.program_uuid,
            'text': text,
        })
        mocked_link.assert_called_once()
        mocked_link.assert_called_with(
            UUID(self.program_uuid),
            {
                '0001': 'learner-01',
                '0002': 'learner-02',
            }
        )

    @patch_render
    def test_junk_text(self, mocked_render):
        text = 'alsdjflajsdflakjs'
        self.client.post(self.url, data={
            'program_uuid': self.program_uuid,
            'text': text,
        })
        msg = "All linking lines must be in the format 'external_user_key,lms_username'"
        render_call_dict = mocked_render.call_args[0][1]
        assert render_call_dict['errors'] == [msg]

    def _setup_user_from_username(self, username):
        """
        Setup a user from the passed in username.
        If username passed in is falsy, return None
        """
        created_user = None
        if username:
            created_user = UserFactory(username=username, password=self.PASSWORD)
        return created_user

    def _setup_enrollments(self, external_user_key, linked_user=None):
        """
        Create enrollments for testing linking.
        The enrollments can be create with already linked edX user.
        """
        program_enrollment = ProgramEnrollmentFactory.create(
            external_user_key=external_user_key,
            program_uuid=self.program_uuid,
            user=linked_user
        )
        course_enrollment = None
        if linked_user:
            course_enrollment = CourseEnrollmentFactory.create(
                course_id=self.course.id,
                user=linked_user,
                mode=CourseMode.MASTERS,
                is_active=True
            )
        program_course_enrollment = ProgramCourseEnrollmentFactory.create(
            program_enrollment=program_enrollment,
            course_key=self.course.id,
            course_enrollment=course_enrollment,
            status='active'
        )

        return program_enrollment, program_course_enrollment

    @ddt.data(
        ('', None),
        ('linked_user', None),
        ('linked_user', 'original_user')
    )
    @ddt.unpack
    @patch_render
    def test_linking_program_enrollment(self, username, original_username, mocked_render):
        external_user_key = '0001'
        linked_user = self._setup_user_from_username(username)
        original_user = self._setup_user_from_username(original_username)
        program_enrollment, program_course_enrollment = self._setup_enrollments(
            external_user_key,
            original_user
        )
        self.client.post(self.url, data={
            'program_uuid': self.program_uuid,
            'text': external_user_key + ',' + username
        })
        render_call_dict = mocked_render.call_args[0][1]
        if username:
            expected_success = f"('{external_user_key}', '{username}')"
            assert render_call_dict['successes'] == [expected_success]
            program_enrollment.refresh_from_db()
            assert program_enrollment.user == linked_user
            program_course_enrollment.refresh_from_db()
            assert program_course_enrollment.course_enrollment.user == linked_user
        else:
            error = "All linking lines must be in the format 'external_user_key,lms_username'"
            assert render_call_dict['errors'] == [error]


@ddt.ddt
class ProgramEnrollmentsInspectorViewTests(SupportViewTestCase):
    """
    View tests for Program Enrollments Inspector
    """
    patch_render = patch(
        'lms.djangoapps.support.views.program_enrollments.render_to_response',
        return_value=HttpResponse(),
        autospec=True,
    )

    def setUp(self):
        super().setUp()
        self.url = reverse("support:program_enrollments_inspector")
        SupportStaffRole().add_users(self.user)
        self.program_uuid = str(uuid4())
        self.external_user_key = 'abcaaa'
        # Setup three orgs and their SAML providers
        self.org_key_list = ['test_org', 'donut_org', 'tri_org']
        for org_key in self.org_key_list:
            lms_org = OrganizationFactory(
                short_name=org_key
            )
            SAMLProviderConfigFactory(
                organization=lms_org,
                slug=org_key,
                enabled=True,
            )
        self.no_saml_org_key = 'no_saml_org'
        self.no_saml_lms_org = OrganizationFactory(
            short_name=self.no_saml_org_key
        )

    def _serialize_datetime(self, dt):
        return dt.strftime('%Y-%m-%dT%H:%M:%S')

    def test_initial_rendering(self):
        response = self.client.get(self.url)
        content = str(response.content, encoding='utf-8')
        expected_organization_serialized = '"orgKeys": {}'.format(  # noqa: UP032
            json.dumps(sorted(self.org_key_list))
        )
        assert response.status_code == 200
        assert expected_organization_serialized in content
        assert '"learnerInfo": {}' in content

    def _construct_user(self, username, org_key=None, external_user_key=None):
        """
        Provided the username, create an edx account user. If the org_key is provided,
        SSO link the user with the IdP associated with org_key. Return the created user and
        expected user info object from the view
        """
        user = UserFactory(username=username)
        user_info = {
            'username': user.username,
            'email': user.email
        }
        if org_key and external_user_key:
            user_social_auth = UserSocialAuth.objects.create(
                user=user,
                uid=f'{org_key}:{external_user_key}',
                provider='tpa-saml'
            )
            user_info['sso_list'] = [{
                'uid': user_social_auth.uid
            }]
        return user, user_info

    def _construct_enrollments(self, program_uuids, course_ids, external_user_key, edx_user=None):
        """
        A helper function to setup the program enrollments for a given learner.
        If the edx user is provided, it will try to SSO the user with the enrollments
        Return the expected info object that should be created based on the model setup
        """
        program_enrollments = []
        for program_uuid in program_uuids:
            course_enrollment = None
            program_enrollment = ProgramEnrollmentFactory.create(
                external_user_key=external_user_key,
                program_uuid=program_uuid,
                user=edx_user
            )

            for course_id in course_ids:
                if edx_user:
                    course_enrollment = CourseEnrollmentFactory.create(
                        course_id=course_id,
                        user=edx_user,
                        mode=CourseMode.MASTERS,
                        is_active=True
                    )

                program_course_enrollment = ProgramCourseEnrollmentFactory.create(  # noqa: F841
                    # pylint: disable=unused-variable
                    program_enrollment=program_enrollment,
                    course_key=course_id,
                    course_enrollment=course_enrollment,
                    status='active',
                )

            program_enrollments.append(program_enrollment)

        serialized = ProgramEnrollmentSerializer(program_enrollments, many=True)
        return serialized.data

    def _construct_id_verification(self, user):
        """
        Helper function to create the SSO verified record for the user
        so that the user is ID Verified
        """
        SSOVerificationFactory(
            identity_provider_slug=self.org_key_list[0],
            user=user,
        )
        return IDVerificationService.user_status(user)

    @patch_render
    def test_search_username_well_connected_user(self, mocked_render):
        created_user, expected_user_info = self._construct_user(
            'test_user_connected',
            self.org_key_list[0],
            self.external_user_key
        )
        id_verified = self._construct_id_verification(created_user)
        expected_enrollments = self._construct_enrollments(
            [self.program_uuid],
            [self.course.id],
            self.external_user_key,
            created_user
        )
        self.client.get(self.url, data={
            'edx_user': created_user.username,
            'org_key': self.org_key_list[0]
        })
        expected_info = {
            'user': expected_user_info,
            'enrollments': expected_enrollments,
            'id_verification': id_verified
        }

        render_call_dict = mocked_render.call_args[0][1]
        assert expected_info == render_call_dict['learner_program_enrollments']

    @patch_render
    def test_search_username_user_not_connected(self, mocked_render):
        created_user, expected_user_info = self._construct_user('user_not_connected')
        self.client.get(self.url, data={
            'edx_user': created_user.email,
            'org_key': self.org_key_list[0]
        })
        expected_info = {
            'user': expected_user_info,
            'id_verification': IDVerificationService.user_status(created_user)
        }

        render_call_dict = mocked_render.call_args[0][1]
        assert expected_info == render_call_dict['learner_program_enrollments']

    @patch_render
    def test_search_username_user_no_enrollment(self, mocked_render):
        created_user, expected_user_info = self._construct_user(
            'user_connected',
            self.org_key_list[0],
            self.external_user_key
        )
        self.client.get(self.url, data={
            'edx_user': created_user.email,
            'org_key': self.org_key_list[0]
        })
        expected_info = {
            'user': expected_user_info,
            'id_verification': IDVerificationService.user_status(created_user),
        }

        render_call_dict = mocked_render.call_args[0][1]
        assert expected_info == render_call_dict['learner_program_enrollments']

    @patch_render
    def test_search_username_user_no_course_enrollment(self, mocked_render):
        created_user, expected_user_info = self._construct_user(
            'user_connected',
            self.org_key_list[0],
            self.external_user_key
        )
        expected_enrollments = self._construct_enrollments(
            [self.program_uuid],
            [],
            self.external_user_key,
            created_user,
        )
        self.client.get(self.url, data={
            'edx_user': created_user.email,
            'org_key': self.org_key_list[0]
        })
        expected_info = {
            'user': expected_user_info,
            'enrollments': expected_enrollments,
            'id_verification': IDVerificationService.user_status(created_user),
        }

        render_call_dict = mocked_render.call_args[0][1]
        assert expected_info == render_call_dict['learner_program_enrollments']

    @patch_render
    def test_search_username_user_not_connected_with_enrollments(self, mocked_render):
        created_user, expected_user_info = self._construct_user(
            'user_not_connected'
        )
        self._construct_enrollments(
            [self.program_uuid],
            [],
            self.external_user_key,
        )
        self.client.get(self.url, data={
            'edx_user': created_user.email,
            'org_key': self.org_key_list[0]
        })
        expected_info = {
            'user': expected_user_info,
            'id_verification': IDVerificationService.user_status(created_user),
        }

        render_call_dict = mocked_render.call_args[0][1]
        assert expected_info == render_call_dict['learner_program_enrollments']

    @patch_render
    def test_search_username_user_id_verified(self, mocked_render):
        created_user, expected_user_info = self._construct_user(
            'user_not_connected'
        )
        id_verified = self._construct_id_verification(created_user)
        expected_info = {
            'user': expected_user_info,
            'id_verification': id_verified
        }

        self.client.get(self.url, data={
            'edx_user': created_user.email,
            'org_key': self.org_key_list[0]
        })

        render_call_dict = mocked_render.call_args[0][1]
        assert expected_info == render_call_dict['learner_program_enrollments']

    @patch_render
    def test_search_external_key_well_connected(self, mocked_render):
        created_user, expected_user_info = self._construct_user(
            'test_user_connected',
            self.org_key_list[0],
            self.external_user_key
        )
        expected_enrollments = self._construct_enrollments(
            [self.program_uuid],
            [self.course.id],
            self.external_user_key,
            created_user
        )
        id_verified = self._construct_id_verification(created_user)
        self.client.get(self.url, data={
            'external_user_key': self.external_user_key,
            'org_key': self.org_key_list[0]
        })
        expected_info = {
            'user': expected_user_info,
            'enrollments': expected_enrollments,
            'id_verification': id_verified,
        }

        render_call_dict = mocked_render.call_args[0][1]
        assert expected_info == render_call_dict['learner_program_enrollments']

    @ddt.data(
        ('', 'test_org'),
        ('bad_key', '')
    )
    @ddt.unpack
    @patch_render
    def test_search_no_external_user_key(self, user_key, org_key, mocked_render):
        self.client.get(self.url, data={
            'external_user_key': user_key,
            'org_key': org_key,
        })

        expected_error = (
            "To perform a search, you must provide either the student's "
            "(a) edX username, "
            "(b) email address associated with their edX account, or "
            "(c) Identity-providing institution and external key!"
        )

        render_call_dict = mocked_render.call_args[0][1]
        assert {} == render_call_dict['learner_program_enrollments']
        assert expected_error == render_call_dict['error']

    @patch_render
    def test_search_external_user_not_connected(self, mocked_render):
        expected_enrollments = self._construct_enrollments(
            [self.program_uuid],
            [self.course.id],
            self.external_user_key,
        )
        self.client.get(self.url, data={
            'external_user_key': self.external_user_key,
            'org_key': self.org_key_list[0]
        })
        expected_info = {
            'user': {
                'external_user_key': self.external_user_key,
            },
            'enrollments': expected_enrollments
        }

        render_call_dict = mocked_render.call_args[0][1]
        assert expected_info == render_call_dict['learner_program_enrollments']

    @patch_render
    def test_search_external_user_not_in_system(self, mocked_render):
        external_user_key = 'not_in_system'
        self.client.get(self.url, data={
            'external_user_key': external_user_key,
            'org_key': self.org_key_list[0],
        })

        expected_error = 'No user found for external key {} for institution {}'.format(  # noqa: UP032
            external_user_key, self.org_key_list[0]
        )
        render_call_dict = mocked_render.call_args[0][1]
        assert expected_error == render_call_dict['error']

    @patch_render
    def test_search_external_user_case_insensitive(self, mocked_render):
        external_user_key = 'AbCdEf123'
        requested_external_user_key = 'aBcDeF123'

        created_user, expected_user_info = self._construct_user(
            'test_user_connected',
            self.org_key_list[0],
            external_user_key
        )

        expected_enrollments = self._construct_enrollments(
            [self.program_uuid],
            [self.course.id],
            external_user_key,
            created_user
        )
        id_verified = self._construct_id_verification(created_user)

        self.client.get(self.url, data={
            'external_user_key': requested_external_user_key,
            'org_key': self.org_key_list[0]
        })
        expected_info = {
            'user': expected_user_info,
            'enrollments': expected_enrollments,
            'id_verification': id_verified,
        }

        render_call_dict = mocked_render.call_args[0][1]
        assert expected_info == render_call_dict['learner_program_enrollments']


@ddt.ddt
class ProgramEnrollmentsInspectorAPIViewTests(SupportViewTestCase):
    """
    View tests for Program Enrollments Inspector API
    """
    _url = reverse("support:program_enrollments_inspector_details")

    def setUp(self):
        super().setUp()
        SupportStaffRole().add_users(self.user)
        self.program_uuid = str(uuid4())
        self.external_user_key = 'abcaaa'
        # Setup three orgs and their SAML providers
        self.org_key_list = ['test_org', 'donut_org', 'tri_org']
        for org_key in self.org_key_list:
            lms_org = OrganizationFactory(
                short_name=org_key
            )
            SAMLProviderConfigFactory(
                organization=lms_org,
                slug=org_key,
                enabled=True,
            )
        self.no_saml_org_key = 'no_saml_org'
        self.no_saml_lms_org = OrganizationFactory(
            short_name=self.no_saml_org_key
        )

    def _serialize_datetime(self, dt):
        return dt.strftime('%Y-%m-%dT%H:%M:%S')

    def test_default_response(self):
        response = self.client.get(self._url)
        content = json.loads(response.content.decode('utf-8'))
        assert response.status_code == 200
        assert '' == content['org_keys']

    def _construct_user(self, username, org_key=None, external_user_key=None):
        """
        Provided the username, create an edx account user. If the org_key is provided,
        SSO link the user with the IdP associated with org_key. Return the created user and
        expected user info object from the view
        """
        user = UserFactory(username=username)
        user_info = {
            'username': user.username,
            'email': user.email
        }
        if org_key and external_user_key:
            user_social_auth = UserSocialAuth.objects.create(
                user=user,
                uid=f'{org_key}:{external_user_key}',
                provider='tpa-saml'
            )
            user_info['sso_list'] = [{
                'uid': user_social_auth.uid
            }]
        return user, user_info

    def _construct_enrollments(self, program_uuids, course_ids, external_user_key, edx_user=None):
        """
        A helper function to setup the program enrollments for a given learner.
        If the edx user is provided, it will try to SSO the user with the enrollments
        Return the expected info object that should be created based on the model setup
        """
        program_enrollments = []
        for program_uuid in program_uuids:
            course_enrollment = None
            program_enrollment = ProgramEnrollmentFactory.create(
                external_user_key=external_user_key,
                program_uuid=program_uuid,
                user=edx_user
            )

            for course_id in course_ids:
                if edx_user:
                    course_enrollment = CourseEnrollmentFactory.create(
                        course_id=course_id,
                        user=edx_user,
                        mode=CourseMode.MASTERS,
                        is_active=True
                    )

                program_course_enrollment = ProgramCourseEnrollmentFactory.create(  # noqa: F841
                    # pylint: disable=unused-variable
                    program_enrollment=program_enrollment,
                    course_key=course_id,
                    course_enrollment=course_enrollment,
                    status='active',
                )

            program_enrollments.append(program_enrollment)

        serialized = ProgramEnrollmentSerializer(program_enrollments, many=True)
        return serialized.data

    def _construct_id_verification(self, user):
        """
        Helper function to create the SSO verified record for the user
        so that the user is ID Verified
        """
        SSOVerificationFactory(
            identity_provider_slug=self.org_key_list[0],
            user=user,
        )
        return IDVerificationService.user_status(user)

    def test_search_username_well_connected_user(self):
        created_user, expected_user_info = self._construct_user(
            'test_user_connected',
            self.org_key_list[0],
            self.external_user_key
        )
        id_verified = self._construct_id_verification(created_user)
        expected_enrollments = self._construct_enrollments(
            [self.program_uuid],
            [self.course.id],
            self.external_user_key,
            created_user
        )
        response = self.client.get(self._url + f'?edx_user={created_user.username}&org_key={self.org_key_list[0]}')
        response = json.loads(response.content.decode('utf-8'))
        expected_info = {
            'user': expected_user_info,
            'enrollments': expected_enrollments,
            'id_verification': id_verified
        }
        assert expected_info == response['learner_program_enrollments']

    def test_search_username_user_not_connected(self):
        created_user, expected_user_info = self._construct_user('user_not_connected')
        response = self.client.get(self._url + f'?edx_user={created_user.username}&org_key={self.org_key_list[0]}')
        response = json.loads(response.content.decode('utf-8'))
        expected_info = {
            'user': expected_user_info,
            'id_verification': IDVerificationService.user_status(created_user)
        }

        assert expected_info == response['learner_program_enrollments']

    def test_search_username_user_no_enrollment(self):
        created_user, expected_user_info = self._construct_user(
            'user_connected',
            self.org_key_list[0],
            self.external_user_key
        )
        response = self.client.get(self._url + f'?edx_user={created_user.username}&org_key={self.org_key_list[0]}')
        response = json.loads(response.content.decode('utf-8'))
        expected_info = {
            'user': expected_user_info,
            'id_verification': IDVerificationService.user_status(created_user),
        }
        assert expected_info == response['learner_program_enrollments']

    def test_search_username_user_no_course_enrollment(self):
        created_user, expected_user_info = self._construct_user(
            'user_connected',
            self.org_key_list[0],
            self.external_user_key
        )
        expected_enrollments = self._construct_enrollments(
            [self.program_uuid],
            [],
            self.external_user_key,
            created_user,
        )
        response = self.client.get(self._url + f'?edx_user={created_user.username}&org_key={self.org_key_list[0]}')
        response = json.loads(response.content.decode('utf-8'))
        expected_info = {
            'user': expected_user_info,
            'enrollments': expected_enrollments,
            'id_verification': IDVerificationService.user_status(created_user),
        }

        assert expected_info == response['learner_program_enrollments']

    def test_search_username_user_not_connected_with_enrollments(self):
        created_user, expected_user_info = self._construct_user(
            'user_not_connected'
        )
        self._construct_enrollments(
            [self.program_uuid],
            [],
            self.external_user_key,
        )
        response = self.client.get(self._url + f'?edx_user={created_user.username}&org_key={self.org_key_list[0]}')
        response = json.loads(response.content.decode('utf-8'))
        expected_info = {
            'user': expected_user_info,
            'id_verification': IDVerificationService.user_status(created_user),
        }
        assert expected_info == response['learner_program_enrollments']

    def test_search_username_user_id_verified(self):
        created_user, expected_user_info = self._construct_user(
            'user_not_connected'
        )
        id_verified = self._construct_id_verification(created_user)
        expected_info = {
            'user': expected_user_info,
            'id_verification': id_verified
        }
        response = self.client.get(self._url + f'?edx_user={created_user.username}&org_key={self.org_key_list[0]}')
        response = json.loads(response.content.decode('utf-8'))
        assert expected_info == response['learner_program_enrollments']

    @ddt.data(
        ('', 'test_org'),
        ('bad_key', '')
    )
    @ddt.unpack
    def test_search_no_external_user_key(self, user_key, org_key):
        response = self.client.get(self._url + f'?external_user_key={user_key}&org_key={org_key}')
        response = json.loads(response.content.decode('utf-8'))
        expected_error = (
            "To perform a search, you must provide either the student's "
            "(a) edX username, "
            "(b) email address associated with their edX account, or "
            "(c) Identity-providing institution and external key!"
        )

        assert {} == response['learner_program_enrollments']
        assert expected_error == response['error']

    def test_search_external_user_not_connected(self):
        expected_enrollments = self._construct_enrollments(
            [self.program_uuid],
            [self.course.id],
            self.external_user_key,
        )
        response = self.client.get(
            self._url + f'?external_user_key={self.external_user_key}&org_key={self.org_key_list[0]}'
        )
        response = json.loads(response.content.decode('utf-8'))
        expected_info = {
            'user': {
                'external_user_key': self.external_user_key,
            },
            'enrollments': expected_enrollments
        }
        assert expected_info == response['learner_program_enrollments']

    def test_search_external_user_not_in_system(self):
        external_user_key = 'not_in_system'
        response = self.client.get(
            self._url + f'?external_user_key={external_user_key}&org_key={self.org_key_list[0]}'
        )
        response = json.loads(response.content.decode('utf-8'))
        expected_error = 'No user found for external key {} for institution {}'.format(  # noqa: UP032
            external_user_key, self.org_key_list[0]
        )
        assert expected_error == response['error']

    def test_search_external_user_case_insensitive(self):
        external_user_key = 'AbCdEf123'
        requested_external_user_key = 'aBcDeF123'
        created_user, expected_user_info = self._construct_user(
            'test_user_connected',
            self.org_key_list[0],
            external_user_key
        )
        expected_enrollments = self._construct_enrollments(
            [self.program_uuid],
            [self.course.id],
            external_user_key,
            created_user
        )
        id_verified = self._construct_id_verification(created_user)
        response = self.client.get(
            self._url + f'?external_user_key={requested_external_user_key}&org_key={self.org_key_list[0]}'
        )
        response = json.loads(response.content.decode('utf-8'))
        expected_info = {
            'user': expected_user_info,
            'enrollments': expected_enrollments,
            'id_verification': id_verified,
        }
        assert expected_info == response['learner_program_enrollments']


class SsoRecordsTests(SupportViewTestCase):  # pylint: disable=missing-class-docstring

    def setUp(self):
        """Make the user support staff"""
        super().setUp()
        SupportStaffRole().add_users(self.user)
        self.student = UserFactory.create(username='student', email='test@example.com', password='test')
        self.url = reverse("support:sso_records", kwargs={'username_or_email': self.student.username})
        self.org_key_list = ['test_org']
        for org_key in self.org_key_list:
            lms_org = OrganizationFactory(
                short_name=org_key
            )
            SAMLProviderConfigFactory(
                organization=lms_org,
                slug=org_key,
                enabled=True,
            )

    def test_empty_response(self):
        response = self.client.get(self.url)
        data = json.loads(response.content.decode('utf-8'))
        assert response.status_code == 200
        assert len(data) == 0

    def test_user_does_not_exist(self):
        response = self.client.get(reverse("support:sso_records", kwargs={'username_or_email': 'wrong_username'}))
        data = json.loads(response.content.decode('utf-8'))
        assert response.status_code == 200
        assert len(data) == 0

    def test_response(self):
        user_social_auth = UserSocialAuth.objects.create(  # pylint: disable=unused-variable  # noqa: F841
            user=self.student,
            uid=self.student.email,
            provider='tpa-saml'
        )
        response = self.client.get(self.url)
        data = json.loads(response.content.decode('utf-8'))
        assert response.status_code == 200
        assert len(data) == 1
        self.assertContains(response, '"uid": "test@example.com"')

    def test_history_response(self):
        '''Tests changes in SSO history for a user'''
        user_social_auth = UserSocialAuth.objects.create(  # pylint: disable=unused-variable  # noqa: F841
            user=self.student,
            uid=self.student.email,
            provider='tpa-saml'
        )
        sso = UserSocialAuth.objects.get(user=self.student)
        sso.uid = self.student.email + ':' + sso.provider
        sso.save()
        response = self.client.get(self.url)
        data = json.loads(response.content.decode('utf-8'))
        assert response.status_code == 200
        assert len(data) == 1
        assert len(data[0].get('history')) == 2
        assert data[0].get('history')[0].get('uid') == "test@example.com:tpa-saml"
        assert data[0].get('history')[1].get('uid') == "test@example.com"


class FeatureBasedEnrollmentSupportApiViewTests(SupportViewTestCase):
    """
    Test suite for FBE Support API view.
    """

    def setUp(self):
        super().setUp()
        SupportStaffRole().add_users(self.user)

    def test_fbe_enabled_response(self):
        """
        Test the response for the api view when the gating and duration configs
        are enabled.
        """
        for course_mode in [CourseMode.AUDIT, CourseMode.VERIFIED]:
            CourseModeFactory.create(mode_slug=course_mode, course_id=self.course.id)
        ContentTypeGatingConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1))
        CourseDurationLimitConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1))

        response = self.client.get(
            reverse("support:feature_based_enrollment_details", kwargs={'course_id': str(self.course.id)})
        )
        data = json.loads(response.content.decode('utf-8'))
        gating_config = data['gating_config']
        duration_config = data['duration_config']

        assert str(self.course.id) == data['course_id']
        assert gating_config['enabled']
        assert gating_config['enabled_as_of'] == '2018-01-01 00:00:00+00:00'
        assert duration_config['enabled']
        assert duration_config['enabled_as_of'] == '2018-01-01 00:00:00+00:00'

    def test_fbe_disabled_response(self):
        """
        Test the FBE support api view response to be empty when no gating and duration
        config is present.
        """
        response = self.client.get(
            reverse("support:feature_based_enrollment_details", kwargs={'course_id': str(self.course.id)})
        )
        data = json.loads(response.content.decode('utf-8'))
        assert data == {}


@ddt.ddt
class LinkProgramEnrollmentSupportAPIViewTests(SupportViewTestCase):
    """
    Tests for the link_program_enrollments support view.
    """
    _url = reverse("support:link_program_enrollments_details")

    def setUp(self):
        """
        Make the user support staff.
        """
        super().setUp()
        SupportStaffRole().add_users(self.user)
        self.program_uuid = str(uuid4())
        self.username_pair_text = '0001,user-0001\n0002,user-02'

    def _setup_user_from_username(self, username):
        """
        Setup a user from the passed in username.
        If username passed in is falsy, return None
        """
        created_user = None
        if username:
            created_user = UserFactory(username=username, password=self.PASSWORD)
        return created_user

    def _setup_enrollments(self, external_user_key, linked_user=None):
        """
        Create enrollments for testing linking.
        The enrollments can be created with already linked edX user.
        """
        program_enrollment = ProgramEnrollmentFactory.create(
            external_user_key=external_user_key,
            program_uuid=self.program_uuid,
            user=linked_user
        )
        course_enrollment = None
        if linked_user:
            course_enrollment = CourseEnrollmentFactory.create(
                course_id=self.course.id,
                user=linked_user,
                mode=CourseMode.MASTERS,
                is_active=True
            )
        program_course_enrollment = ProgramCourseEnrollmentFactory.create(
            program_enrollment=program_enrollment,
            course_key=self.course.id,
            course_enrollment=course_enrollment,
            status='active'
        )
        return program_enrollment, program_course_enrollment

    def test_invalid_uuid(self):
        """
        Tests if enrollment linkages are refused for an invalid uuid
        """
        response = self.client.post(self._url, data={
            'program_uuid': 'notauuid',
            'username_pair_text': self.username_pair_text,
        })
        msg = "Supplied program UUID 'notauuid' is not a valid UUID."
        data = json.loads(response.content.decode('utf-8'))
        assert data['errors'] == [msg]

    @ddt.data(
        ('program_uuid', ''),
        ('', 'username_pair_text'),
        ('', '')
    )
    @ddt.unpack
    def test_missing_parameter(self, program_uuid, username_pair_text):
        """
        Tests if enrollment linkages are refused for missing parameters
        """
        error = (
            "You must provide both a program uuid "
            "and a series of lines with the format "
            "'external_user_key,lms_username'."
        )
        response = self.client.post(self._url, data={
            'program_uuid': program_uuid,
            'username_pair_text': username_pair_text
        })
        response_data = json.loads(response.content.decode('utf-8'))
        assert response_data['errors'] == [error]

    @ddt.data(
        '0001,learner-01\n0002,learner-02',  # normal
        '0001,learner-01,apple,orange\n0002,learner-02,purple',  # extra fields
        '\t0001        ,    \t  learner-01    \n   0002 , learner-02    ',  # whitespace
    )
    @patch('lms.djangoapps.support.views.utils.link_program_enrollments')
    def test_username_pair_text(self, username_pair_text, mocked_link):
        """
        Tests if enrollment linkages are created for different types of
        username_pair_text format
        """
        response = self.client.post(self._url, data={
            'program_uuid': self.program_uuid,
            'username_pair_text': username_pair_text,
        })
        response_data = json.loads(response.content.decode('utf-8'))
        mocked_link.assert_called_once()
        mocked_link.assert_called_with(
            UUID(self.program_uuid),
            {
                '0001': 'learner-01',
                '0002': 'learner-02',
            }
        )
        success = ["('0001', 'learner-01')", "('0002', 'learner-02')"]
        assert response_data['successes'] == success
        mocked_link.reset_mock()

    def test_invalid_username_pair_text(self):
        """
        Tests if enrollment linkages are refused for invalid types of
        username_pair_text format
        """
        username_pair_text = 'garbage_text'
        response = self.client.post(self._url, data={
            'program_uuid': self.program_uuid,
            'username_pair_text': username_pair_text,
        })
        msg = "All linking lines must be in the format 'external_user_key,lms_username'"
        response_data = json.loads(response.content.decode('utf-8'))
        assert response_data['errors'] == [msg]

    @ddt.data(
        ('linked_user', None),
        ('linked_user', 'original_user')
    )
    @ddt.unpack
    def test_linking_program_enrollment_with_username(self, username, original_username):
        """
        Tests if enrollment linkages are created for valid usernames
        """
        external_user_key = '0001'
        linked_user = self._setup_user_from_username(username)
        original_user = self._setup_user_from_username(original_username)
        program_enrollment, program_course_enrollment = self._setup_enrollments(
            external_user_key,
            original_user
        )
        response = self.client.post(self._url, data={
            'program_uuid': self.program_uuid,
            'username_pair_text': external_user_key + ',' + username
        })
        response_data = json.loads(response.content.decode('utf-8'))
        expected_success = f"('{external_user_key}', '{username}')"
        assert response_data['successes'] == [expected_success]
        program_enrollment.refresh_from_db()
        assert program_enrollment.user == linked_user
        program_course_enrollment.refresh_from_db()
        assert program_course_enrollment.course_enrollment.user == linked_user

    @ddt.data(
        ('', None),
    )
    @ddt.unpack
    def test_linking_program_enrollment_without_username(self, username, original_username):
        """
        Tests if enrollment linkages are refused for invalid usernames
        """
        external_user_key = '0001'
        linked_user = self._setup_user_from_username(username)  # noqa: F841
        original_user = self._setup_user_from_username(original_username)
        program_enrollment, program_course_enrollment = self._setup_enrollments(
            external_user_key,
            original_user
        )
        response = self.client.post(self._url, data={
            'program_uuid': self.program_uuid,
            'username_pair_text': external_user_key + ',' + username
        })
        response_data = json.loads(response.content.decode('utf-8'))
        error = "All linking lines must be in the format 'external_user_key,lms_username'"
        assert response_data['errors'] == [error]


class SAMLProvidersWithOrgTests(SupportViewTestCase):
    """
    Tests for the get_saml_providers API View
    """
    _url = reverse("support:get_saml_providers")

    def setUp(self):
        """
        Make the user support staff.
        """
        super().setUp()
        SupportStaffRole().add_users(self.user)

        self.org_key_list = ['test_org', 'donut_org', 'tri_org']
        for org_key in self.org_key_list:
            lms_org = OrganizationFactory(
                short_name=org_key
            )
            SAMLProviderConfigFactory(
                organization=lms_org,
                slug=org_key,
                enabled=True,
            )

    def test_returning_saml_providers(self):
        response = self.client.get(self._url)
        response_data = json.loads(response.content.decode('utf-8'))
        assert response_data == self.org_key_list


class TestOnboardingView(SupportViewTestCase, ProctoredExamTestCase):
    """
    Tests for OnboardingView
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        super().setUp()
        SupportStaffRole().add_users(self.user)

        # update default course key
        self.course_id = 'course-v1:a+b+c'

        self.proctored_exam_id = self._create_proctored_exam()
        self.onboarding_exam_id = self._create_onboarding_exam()

        self.other_user = User.objects.create(username='otheruser', password='test')
        self.other_course_content = 'block-v1:test+course+2+type@sequential+block@other_onboard'

        self.other_course = CourseFactory.create(
            org='x',
            course='y',
            run='z',
            enable_proctored_exams=True,
            proctoring_provider=settings.PROCTORING_BACKENDS['DEFAULT'],
        )

        yesterday = timezone.now() - timezone.timedelta(days=1)
        self.course_scheduled_sections = {
            BlockUsageLocator.from_string(self.content_id_onboarding): MockScheduleItemData(yesterday),
            BlockUsageLocator.from_string(self.other_course_content): MockScheduleItemData(yesterday),
        }

        set_runtime_service('learning_sequences', MockLearningSequencesService(
            list(self.course_scheduled_sections.keys()),
            self.course_scheduled_sections,
        ))

        self.onboarding_exam = ProctoredExam.objects.get(id=self.onboarding_exam_id)

    def tearDown(self):  # pylint: disable=super-method-not-called
        """
        Override deafult implementation to prevent `default` key deletion from TRACKERS in
        an inherited tearDown() method of ProctoredExamTestCase
        """
        return

    def _url(self, username):
        return reverse("support:onboarding_status", kwargs={'username_or_email': username})

    def _create_enrollment(self):
        """ Create enrollment in default course """
        # updated course key = 'course-v1:a+b+c'
        self.course = CourseFactory.create(
            org='a',
            course='b',
            run='c',
            enable_proctored_exams=True,
            proctoring_provider=settings.PROCTORING_BACKENDS['DEFAULT'],
        )
        CourseEnrollmentFactory(
            is_active=True,
            mode='verified',
            course_id=self.course.id,
            user=self.user
        )

    def test_wrong_username(self):
        """
        Test that a request with a username which does not exits returns 404
        """
        response = self.client.get(self._url(username='does_not_exist'))
        self.assertEqual(response.status_code, 404)  # noqa: PT009

        response_data = json.loads(response.content.decode('utf-8'))

        self.assertEqual(response_data['verified_in'], None)  # noqa: PT009
        self.assertEqual(response_data['current_status'], None)  # noqa: PT009

    def test_no_record(self):
        """
        Test that a request with a username which do not have any onboarding exam returns empty data
        """
        response = self.client.get(self._url(username=self.other_user.username))
        self.assertEqual(response.status_code, 200)  # noqa: PT009

        response_data = json.loads(response.content.decode('utf-8'))

        self.assertEqual(response_data['verified_in'], None)  # noqa: PT009
        self.assertEqual(response_data['current_status'], None)  # noqa: PT009

    def test_no_verified_attempts(self):
        """
        Test that if there are no verified attempts, the most recent status is returned
        """

        self._create_enrollment()

        # create first attempt
        attempt_id = create_exam_attempt(self.onboarding_exam_id, self.user.id, True)
        update_attempt_status(attempt_id, ProctoredExamStudentAttemptStatus.submitted)

        response = self.client.get(self._url(username=self.user.username))
        self.assertEqual(response.status_code, 200)  # noqa: PT009
        response_data = json.loads(response.content.decode('utf-8'))
        self.assertEqual(response_data['verified_in'], None)  # noqa: PT009
        self.assertEqual(  # noqa: PT009
            response_data['current_status']['onboarding_status'],
            ProctoredExamStudentAttemptStatus.submitted
        )

        # Create second attempt and assert that most recent attempt is returned
        create_exam_attempt(self.onboarding_exam_id, self.user.id, True)
        response = self.client.get(self._url(username=self.user.username))
        self.assertEqual(response.status_code, 200)  # noqa: PT009
        response_data = json.loads(response.content.decode('utf-8'))

        self.assertEqual(response_data['verified_in'], None)  # noqa: PT009
        self.assertEqual(  # noqa: PT009
            response_data['current_status']['onboarding_status'],
            ProctoredExamStudentAttemptStatus.created
        )

    def test_get_verified_attempt(self):
        """
        Test that if there is at least one verified attempt, the status returned is always verified
        """

        self._create_enrollment()

        # Create first attempt
        attempt_id = create_exam_attempt(self.onboarding_exam_id, self.user.id, True)
        update_attempt_status(attempt_id, ProctoredExamStudentAttemptStatus.verified)
        response = self.client.get(self._url(username=self.user.username))
        self.assertEqual(response.status_code, 200)  # noqa: PT009
        response_data = json.loads(response.content.decode('utf-8'))

        self.assertEqual(  # noqa: PT009
            response_data['verified_in']['onboarding_status'],
            ProctoredExamStudentAttemptStatus.verified
        )
        self.assertEqual(  # noqa: PT009
            response_data['current_status']['onboarding_status'],
            ProctoredExamStudentAttemptStatus.verified
        )

        # Create second attempt and assert that verified attempt is still returned
        create_exam_attempt(self.onboarding_exam_id, self.user.id, True)
        response = self.client.get(self._url(username=self.user.username))
        self.assertEqual(response.status_code, 200)  # noqa: PT009
        response_data = json.loads(response.content.decode('utf-8'))

        self.assertEqual(  # noqa: PT009
            response_data['verified_in']['onboarding_status'],
            ProctoredExamStudentAttemptStatus.verified
        )
        self.assertEqual(  # noqa: PT009
            response_data['current_status']['onboarding_status'],
            ProctoredExamStudentAttemptStatus.verified
        )

    def test_verified_in_another_course(self):
        """
        Test that, if there is at least one verified attempt in any course for a given user,
        the current status will return `other_course_approved`
        """

        # Create a submitted attempt in the current course
        attempt_id = create_exam_attempt(self.onboarding_exam_id, self.user.id, True)
        update_attempt_status(attempt_id, ProctoredExamStudentAttemptStatus.submitted)

        # Create an attempt in the other course that has been verified
        other_course_id = 'course-v1:x+y+z'
        other_course_onboarding_exam = ProctoredExam.objects.create(
            course_id=other_course_id,
            content_id=self.other_course_content,
            exam_name='Test Exam',
            external_id='123aXqe3',
            time_limit_mins=90,
            is_active=True,
            is_proctored=True,
            is_practice_exam=True,
            backend='test'
        )

        self.user_id = self.user.id
        self._create_exam_attempt(other_course_onboarding_exam.id, ProctoredExamStudentAttemptStatus.verified, True)

        # professional enrollment
        CourseEnrollmentFactory(
            is_active=True,
            mode='professional',
            course_id=self.other_course.id,
            user=self.user
        )

        # default enrollment afterwards with submitted status
        self._create_enrollment()

        response = self.client.get(self._url(username=self.user.username))
        self.assertEqual(response.status_code, 200)  # noqa: PT009
        response_data = json.loads(response.content.decode('utf-8'))

        # assert that originally verified enrollment is reflected correctly
        self.assertEqual(response_data['verified_in']['onboarding_status'], 'verified')  # noqa: PT009
        self.assertEqual(response_data['verified_in']['course_id'], other_course_id)  # noqa: PT009

        # assert that most recent enrollment (current status) has other_course_approved status
        self.assertEqual(response_data['current_status']['onboarding_status'], 'other_course_approved')  # noqa: PT009
        self.assertEqual(response_data['current_status']['course_id'], self.course_id)  # noqa: PT009


class ResetCourseViewTestBase(SupportViewTestCase):
    """
    Shared base class for course reset view tests
    """
    def _url(self, username):
        """ Helper to generate URL """
        return reverse("support:course_reset", kwargs={'username_or_email': username})

    def setUp(self):
        """
        Set permissions, create an open course and learner, enroll learner and opt into course reset
        """
        super().setUp()
        SupportStaffRole().add_users(self.user)
        self.now = datetime.now().replace(tzinfo=UTC)
        self.course = CourseFactory.create(
            start=self.now - timedelta(days=90),
            end=self.now + timedelta(days=90),
        )
        self.course_id = str(self.course.id)
        self.course_overview = CourseOverview.get_from_id(self.course.id)
        self.learner = UserFactory.create()
        self.enrollment = CourseEnrollmentFactory.create(user=self.learner, course_id=self.course.id)
        self.opt_in = CourseResetCourseOptInFactory.create(course_id=self.course.id)


@ddt.ddt
class TestResetCourseListView(ResetCourseViewTestBase):
    """ Tests for the list endpoint for course reset """

    def assert_course_ids(self, expected_course_ids, learner=None):
        """ Helper that asserts the course ids that will be returned from the listing endpoint """
        learner = learner or self.learner
        response = self.client.get(self._url(learner))
        self.assertEqual(response.status_code, 200)  # noqa: PT009

        actual_course_ids = [course['course_id'] for course in response.json()]
        self.assertEqual(expected_course_ids, actual_course_ids)  # noqa: PT009

    def test_no_enrollments(self):
        """ When a learner has no enrollments, the endpoint should return an empty list """
        no_enrollment_learner = UserFactory.create()
        self.assert_course_ids([], learner=no_enrollment_learner)

    def test_not_opted_in(self):
        """
        If a learner is enrolled in a course that is not opted into the course reset feature,
        it will not be returned by the endpoint
        """
        non_opted_in_course = CourseFactory.create()
        CourseEnrollmentFactory.create(user=self.learner, course_id=non_opted_in_course.id)
        self.assert_course_ids([self.course_id])

    def test_deactivated_opt_in(self):
        """
        If a learner is enrolled in a course that has opted in, but that opt-in is
        deactivated, it will not be returned from the endpoint
        """
        self.assert_course_ids([self.course_id])

        self.opt_in.active = False
        self.opt_in.save()

        self.assert_course_ids([])

    def test_deactivated_enrollment(self):
        """
        If a learner's enrollment in an opted in course is deactivated,
        the course will not be returned by the endpoint
        """
        self.assert_course_ids([self.course_id])

        self.enrollment.is_active = False
        self.enrollment.save()

        self.assert_course_ids([])

    def assertResponse(self, expected_response):
        """ Helper to assert the contents of the response from the listing endpoint """
        response = self.client.get(self._url(self.learner))
        self.assertEqual(response.status_code, 200)  # noqa: PT009

        actual_response = response.json()
        self.assertEqual(expected_response, actual_response)  # noqa: PT009
        return actual_response

    def test_course_not_started(self):
        """ If a course is opted in but has not started, it should not be resettable """
        self.course_overview.start = self.now + timedelta(days=10)
        self.course_overview.end = self.now + timedelta(days=11)
        self.course_overview.save()
        self.assertResponse([{
            'course_id': self.course_id,
            'display_name': self.course_overview.display_name,
            'can_reset': False,
            'comment': '',
            'status': 'Course Not Started'
        }])

    def test_course_ended(self):
        """ If a course is opted in but has ended, it should not be resettable """
        self.course_overview.start = self.now - timedelta(days=11)
        self.course_overview.end = self.now - timedelta(days=10)
        self.course_overview.save()
        self.assertResponse([
            {
                'course_id': self.course_id,
                'display_name': self.course_overview.display_name,
                'can_reset': False,
                'comment': '',
                'status': 'Course Ended'
            }
        ])

    @patch('lms.djangoapps.support.views.course_reset.user_has_passing_grade_in_course', return_value=True)
    def test_user_has_passing_grade(self, _):  # noqa: PT019
        """ If a course is opted in but the learner has a passing grade, it should not be resettable """
        self.assertResponse([{
            'course_id': self.course_id,
            'display_name': self.course_overview.display_name,
            'can_reset': False,
            'comment': '',
            'status': 'Learner Has Passing Grade'
        }])

    @patch('lms.djangoapps.support.views.course_reset.user_has_passing_grade_in_course', return_value=True)
    def test_ended_with_passing_grade(self, _):  # noqa: PT019
        """
        If a course has ended and the learner has a passing grade,
        the passing grade message should override the ended message
        """
        self.course_overview.start = self.now - timedelta(days=11)
        self.course_overview.end = self.now - timedelta(days=10)
        self.assertResponse([{
            'course_id': self.course_id,
            'display_name': self.course_overview.display_name,
            'can_reset': False,
            'comment': '',
            'status': 'Learner Has Passing Grade'
        }])

    def test_available_course(self):
        """ If a course is opted in and had nothing stopping it from being reset, it should be resettable """
        self.assertResponse([{
            'course_id': self.course_id,
            'display_name': self.course.display_name,
            'can_reset': True,
            'comment': '',
            'status': 'Available'
        }])

    @ddt.unpack
    @ddt.data(
        (CourseResetAudit.CourseResetStatus.ENQUEUED, False),
        (CourseResetAudit.CourseResetStatus.IN_PROGRESS, False),
        (CourseResetAudit.CourseResetStatus.FAILED, True),
        (CourseResetAudit.CourseResetStatus.COMPLETE, False),
    )
    def test_audit(self, audit_status, expected_can_reset):
        """
        If a course enrollment has a CourseResetAudit associated with it,
        it should not be resettable unless the audit is FAILED
        """
        audit = CourseResetAuditFactory.create(
            course=self.opt_in,
            course_enrollment=self.enrollment,
            status=audit_status,
        )
        self.assertResponse([{
            'course_id': self.course_id,
            'display_name': self.course.display_name,
            'can_reset': expected_can_reset,
            'comment': audit.comment,
            'status': audit.status_message()
        }])

    def _set_up_course(self, opt_in=True):
        """
        Make a course, enroll self.learner, and optionally opt into course reset
        """
        course = CourseFactory.create(start=self.course.start, end=self.course.end)
        CourseEnrollmentFactory.create(course_id=course.id, user=self.learner)
        if opt_in:
            CourseResetCourseOptInFactory.create(course_id=course.id)
        return course

    def test_multiple_courses(self):
        """ Test for the behavior of multiple courses """
        # Create four opted in courses and four non-opted-in courses
        opted_in_courses = [self._set_up_course(opt_in=True) for _ in range(4)]
        for _ in range(4):
            self._set_up_course(opt_in=False)

        expected_response = [{
            'course_id': self.course_id,
            'display_name': self.course.display_name,
            'can_reset': True,
            'comment': '',
            'status': 'Available'
        }]
        for course in opted_in_courses:
            expected_response.append({
                'course_id': str(course.id),
                'display_name': course.display_name,
                'comment': '',
                'can_reset': True,
                'status': 'Available'
            })

        self.assertResponse(expected_response)

    def test_multiple_audits(self):
        """
        If you have multiple audits for an enrollment (should only happen if process fails)
        the information returned should be for the most recent ONLY
        """
        daysago = lambda x: self.now - timedelta(days=x)
        CourseResetAuditFactory.create(
            course=self.opt_in,
            course_enrollment=self.enrollment,
            status=CourseResetAudit.CourseResetStatus.FAILED,
            created=daysago(3),
            modified=daysago(3),
        )
        CourseResetAuditFactory.create(
            course=self.opt_in,
            course_enrollment=self.enrollment,
            status=CourseResetAudit.CourseResetStatus.FAILED,
            created=daysago(2),
            modified=daysago(2),
        )
        most_recent_audit = CourseResetAuditFactory.create(
            course=self.opt_in,
            course_enrollment=self.enrollment,
            status=CourseResetAudit.CourseResetStatus.IN_PROGRESS,
        )

        self.assertResponse([{
            'course_id': self.course_id,
            'display_name': self.course.display_name,
            'can_reset': False,
            'comment': most_recent_audit.comment,
            'status': most_recent_audit.status_message()
        }])

    def test_multiple_failed_audits(self):
        """
        If you have multiple audits for an enrollment and the most recent was a failure,
        you should still be able to reset the course
        """
        daysago = lambda x: self.now - timedelta(days=x)
        CourseResetAuditFactory.create(
            course=self.opt_in,
            course_enrollment=self.enrollment,
            status=CourseResetAudit.CourseResetStatus.FAILED,
            created=daysago(3),
            modified=daysago(3),
        )
        CourseResetAuditFactory.create(
            course=self.opt_in,
            course_enrollment=self.enrollment,
            status=CourseResetAudit.CourseResetStatus.FAILED,
            created=daysago(2),
            modified=daysago(2),
        )
        most_recent_audit = CourseResetAuditFactory.create(
            course=self.opt_in,
            course_enrollment=self.enrollment,
            status=CourseResetAudit.CourseResetStatus.FAILED,
        )

        self.assertResponse([{
            'course_id': self.course_id,
            'display_name': self.course.display_name,
            'can_reset': True,
            'comment': most_recent_audit.comment,
            'status': most_recent_audit.status_message()
        }])


class TestResetCourseCreateView(ResetCourseViewTestBase):
    """
    Tests for POST endpoint for performing course reset
    """

    def request(self, username=None, course_id=None, comment=None):
        """ Helper to perform request """
        username = username or self.learner.username
        return self.client.post(
            self._url(username),
            data={
                "course_id": course_id if course_id else self.course_id,
                "comment": comment if comment else ""
            }
        )

    def assert_error_response(self, response, expected_status_code, expected_error_message):
        """ Helper to assert status code and error message """
        self.assertEqual(response.status_code, expected_status_code)  # noqa: PT009
        self.assertEqual(response.data['error'], expected_error_message)  # noqa: PT009

    def test_wrong_username(self):
        """ A request with a username which does not exits returns 404 """
        response = self.request(username='does_not_exist')
        self.assert_error_response(response, 404, "User does not exist")

    def test_invalid_course_id(self):
        """ A request for an invalid course id returns 400 """
        response = self.request(course_id='thisisnotacourseid')
        self.assert_error_response(response, 400, "invalid course id")

    def test_missing_course_id(self):
        """ A request without a course id returns 400 """
        response = self.client.post(self._url(self.learner.username))
        self.assert_error_response(response, 400, "Must specify course id")

    def test_course_not_opt_in(self):
        """ A request for a course which isn't opted into the feature returns 404 """
        self.opt_in.active = False
        self.opt_in.save()

        response = self.request()
        self.assert_error_response(response, 404, "Course is not eligible")

    def test_unenrolled(self):
        """ A request for a learner who isn't enrolled in the given course returns a 404 """
        self.enrollment.is_active = False
        self.enrollment.save()

        response = self.request()
        self.assert_error_response(response, 404, "Learner is not enrolled in course")

    @patch('lms.djangoapps.support.views.course_reset.can_enrollment_be_reset')
    def test_cannot_reset(self, mock_can_reset):
        """ A request for a course which isn't able to be reset returns a 404 """
        mock_status = str(uuid4())
        mock_can_reset.return_value = (False, mock_status)

        response = self.request()
        self.assert_error_response(response, 400, f"Cannot reset course: {mock_status}")

    @patch('lms.djangoapps.support.views.course_reset.reset_student_course')
    def test_learner_course_reset(self, mock_reset_student_course):
        """ Happy path test """
        comment = str(uuid4())

        # A request for a given learner and course with a comment should return a 201
        response = self.request(comment=comment)
        self.assertEqual(response.status_code, 201)  # noqa: PT009
        self.assertEqual(response.data, {  # noqa: PT009
            'course_id': self.course_id,
            'status': response.data['status'],
            'can_reset': False,
            'comment': comment,
            'display_name': self.course.display_name
        })
        # The reset task should be queued
        mock_reset_student_course.delay.assert_called_once_with(self.course_id, self.learner.email, self.user.email)
        # And an audit should be created as ENQUEUED
        self.assertEqual(  # noqa: PT009
            self.enrollment.courseresetaudit_set.first().status,
            CourseResetAudit.CourseResetStatus.ENQUEUED
        )

    @patch('lms.djangoapps.support.views.course_reset.reset_student_course')
    def test_course_reset_failed(self, mock_reset_student_course):
        """ An audit that has failed previously should be able to be run successfully """
        CourseResetAudit.objects.create(
            course=self.opt_in,
            course_enrollment=self.enrollment,
            reset_by=self.user,
            status=CourseResetAudit.CourseResetStatus.FAILED
        )
        response = self.request()
        self.assertEqual(response.status_code, 201)  # noqa: PT009
        self.assertEqual(response.data, {  # noqa: PT009
            'course_id': self.course_id,
            'status': response.data['status'],
            'can_reset': False,
            'comment': '',
            'display_name': self.course.display_name
        })
        mock_reset_student_course.delay.assert_called_once_with(self.course_id, self.learner.email, self.user.email)

    def test_course_reset_already_reset(self):
        """ A course that has an audit that hasn't failed should not be allowed to be run again """
        additional_audit = CourseResetAuditFactory.create(
            course=self.opt_in,
            course_enrollment=self.enrollment,
            status=CourseResetAudit.CourseResetStatus.ENQUEUED
        )
        response = self.request()
        self.assert_error_response(response, 400, f"Cannot reset course: {additional_audit.status_message()}")
