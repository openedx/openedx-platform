"""
Tests for course_modes views.
"""

import decimal
from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import ddt
import freezegun
import httpretty
from django.conf import settings
from django.test import override_settings
from django.urls import reverse

from common.djangoapps.course_modes.models import CourseMode, Mode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from common.djangoapps.util.testing import UrlResetMixin
from common.djangoapps.util.tests.mixins.discovery import CourseCatalogServiceMockMixin
from lms.djangoapps.commerce.tests import test_utils as ecomm_test_utils
from lms.djangoapps.commerce.tests.mocks import mock_payment_processors
from lms.djangoapps.verify_student.services import IDVerificationService
from openedx.core.djangoapps.catalog.tests.mixins import CatalogIntegrationMixin
from openedx.core.djangoapps.embargo.test_utils import restrict_course
from openedx.core.djangoapps.theming.tests.test_util import with_comprehensive_theme
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

# Name of the method to mock for Content Type Gating.
GATING_METHOD_NAME = 'openedx.features.content_type_gating.models.ContentTypeGatingConfig.enabled_for_enrollment'

# Name of the method to mock for Course Duration Limits.
CDL_METHOD_NAME = 'openedx.features.course_duration_limits.models.CourseDurationLimitConfig.enabled_for_enrollment'


@ddt.ddt
@skip_unless_lms
class CourseModeViewTest(CatalogIntegrationMixin, UrlResetMixin, ModuleStoreTestCase, CourseCatalogServiceMockMixin):
    """
    Course Mode View tests
    """
    URLCONF_MODULES = ['common.djangoapps.course_modes.urls']

    @patch.dict(settings.FEATURES, {'MODE_CREATION_FOR_TESTING': True})
    def setUp(self):
        super().setUp()
        now = datetime.now(ZoneInfo("UTC"))
        day = timedelta(days=1)
        tomorrow = now + day
        yesterday = now - day
        # Create course that has not started yet and course that started
        self.course = CourseFactory.create(start=tomorrow)
        self.course_that_started = CourseFactory.create(start=yesterday)
        self.user = UserFactory.create(username="Bob", email="bob@example.com", password="edx")
        self.client.login(username=self.user.username, password="edx")

    @skip_unless_lms
    @httpretty.activate
    @ddt.data(
        # is_active?, enrollment_mode, redirect?, has_started
        (True, 'verified', True, False),
        (True, 'honor', False, False),
        (True, 'audit', False, False),
        (True, 'verified', True, True),
        (True, 'honor', False, True),
        (True, 'audit', False, True),
        (False, 'verified', False, False),
        (False, 'honor', False, False),
        (False, 'audit', False, False),
        (False, None, False, False),
    )
    @ddt.unpack
    def test_redirect_to_dashboard(self, is_active, enrollment_mode, redirect, has_started):
        # Configure whether course has started
        # If it has go to course home instead of dashboard
        course = self.course_that_started if has_started else self.course
        # Create the course modes
        for mode in ('audit', 'honor', 'verified'):
            CourseModeFactory.create(mode_slug=mode, course_id=course.id)

        # Enroll the user in the test course
        if enrollment_mode is not None:
            CourseEnrollmentFactory(
                is_active=is_active,
                mode=enrollment_mode,
                course_id=course.id,
                user=self.user
            )

        # Configure whether we're upgrading or not
        url = reverse('course_modes_choose', args=[str(course.id)])
        response = self.client.get(url)

        # Check whether we were correctly redirected
        if redirect:
            if has_started:
                mfe_url = f'http://learning-mfe/course/{course.id}/home'
                self.assertRedirects(response, mfe_url, fetch_redirect_response=False)
            else:
                self.assertRedirects(response, reverse('dashboard'))
        else:
            assert response.status_code == 200

    def test_no_id_redirect(self):
        # Create the course modes
        CourseModeFactory.create(mode_slug=CourseMode.NO_ID_PROFESSIONAL_MODE, course_id=self.course.id, min_price=100)

        # Enroll the user in the test course
        CourseEnrollmentFactory(
            is_active=False,
            mode=CourseMode.NO_ID_PROFESSIONAL_MODE,
            course_id=self.course.id,
            user=self.user
        )

        # Configure whether we're upgrading or not
        url = reverse('course_modes_choose', args=[str(self.course.id)])
        response = self.client.get(url)

        start_flow_url = IDVerificationService.get_verify_location(course_id=self.course.id)
        # Check whether we were correctly redirected
        self.assertRedirects(response, start_flow_url, fetch_redirect_response=False)

    def test_no_id_redirect_otto(self):
        # Create the course modes
        prof_course = CourseFactory.create()
        CourseModeFactory(mode_slug=CourseMode.NO_ID_PROFESSIONAL_MODE, course_id=prof_course.id,
                          min_price=100, sku='TEST', bulk_sku="BULKTEST")
        ecomm_test_utils.update_commerce_config(enabled=True)
        # Enroll the user in the test course
        CourseEnrollmentFactory(
            is_active=False,
            mode=CourseMode.NO_ID_PROFESSIONAL_MODE,
            course_id=prof_course.id,
            user=self.user
        )
        # Configure whether we're upgrading or not
        url = reverse('course_modes_choose', args=[str(prof_course.id)])
        response = self.client.get(url)
        self.assertRedirects(response, '/test_basket/add/?sku=TEST', fetch_redirect_response=False)
        ecomm_test_utils.update_commerce_config(enabled=False)

    @httpretty.activate
    @ddt.data(
        '',
        '1,,2',
        '1, ,2',
        '1, 2, 3'
    )
    def test_suggested_prices(self, price_list):

        # Create the course modes
        for mode in ('audit', 'honor'):
            CourseModeFactory.create(mode_slug=mode, course_id=self.course.id)

        CourseModeFactory.create(
            mode_slug='verified',
            course_id=self.course.id,
            suggested_prices=price_list
        )

        # Enroll the user in the test course to emulate
        # automatic enrollment
        CourseEnrollmentFactory(
            is_active=True,
            course_id=self.course.id,
            user=self.user
        )

        # Verify that the prices render correctly
        response = self.client.get(
            reverse('course_modes_choose', args=[str(self.course.id)]),
            follow=False,
        )

        assert response.status_code == 200
        # TODO: Fix it so that response.templates works w/ mako templates, and then assert
        # that the right template rendered

    @httpretty.activate
    @patch('common.djangoapps.course_modes.views.enterprise_customer_for_request')
    @patch('common.djangoapps.course_modes.views.get_course_final_price')
    @ddt.data(
        (1.0, True),
        (50.0, False),
        (0.0, True),
        (None, False),
    )
    @ddt.unpack
    def test_display_after_discounted_price(
        self,
        discounted_price,
        is_enterprise_enabled,
        mock_get_course_final_price,
        mock_enterprise_customer_for_request
    ):
        verified_mode = CourseModeFactory.create(mode_slug='verified', course_id=self.course.id, sku='dummy')
        CourseEnrollmentFactory(
            is_active=True,
            course_id=self.course.id,
            user=self.user
        )

        mock_enterprise_customer_for_request.return_value = {'name': 'dummy'} if is_enterprise_enabled else {}
        mock_get_course_final_price.return_value = discounted_price
        url = reverse('course_modes_choose', args=[self.course.id])
        response = self.client.get(url)

        if is_enterprise_enabled:
            self.assertContains(response, discounted_price)
        self.assertContains(response, verified_mode.min_price)

    @ddt.data('professional', 'no-id-professional')
    def test_professional_enrollment(self, mode):
        # The only course mode is professional ed
        CourseModeFactory.create(mode_slug=mode, course_id=self.course.id, min_price=1)

        # Go to the "choose your track" page
        choose_track_url = reverse('course_modes_choose', args=[str(self.course.id)])
        response = self.client.get(choose_track_url)

        # Since the only available track is professional ed, expect that
        # we're redirected immediately to the start of the payment flow.
        start_flow_url = IDVerificationService.get_verify_location(course_id=self.course.id)
        self.assertRedirects(response, start_flow_url, fetch_redirect_response=False)

        # Now enroll in the course
        CourseEnrollmentFactory(
            user=self.user,
            is_active=True,
            mode=mode,
            course_id=str(self.course.id),
        )

        # Expect that this time we're redirected to the dashboard (since we're already registered)
        response = self.client.get(choose_track_url)
        self.assertRedirects(response, reverse('dashboard'))

    # Mapping of course modes to the POST parameters sent
    # when the user chooses that mode.
    POST_PARAMS_FOR_COURSE_MODE = {
        'audit': {'audit_mode': True},
        'honor': {'honor_mode': True},
        'verified': {'verified_mode': True, 'contribution': '1.23'},
        'unsupported': {'unsupported_mode': True},
    }

    @ddt.data(
        ('audit', 'dashboard'),
        ('honor', 'dashboard'),
        ('verified', 'start-flow'),
    )
    @ddt.unpack
    def test_choose_mode_redirect(self, course_mode, expected_redirect):
        # Create the course modes
        for mode in ('audit', 'honor', 'verified'):
            min_price = 0 if mode in ["honor", "audit"] else 1
            CourseModeFactory.create(mode_slug=mode, course_id=self.course.id, min_price=min_price)

        # Choose the mode (POST request)
        choose_track_url = reverse('course_modes_choose', args=[str(self.course.id)])
        response = self.client.post(choose_track_url, self.POST_PARAMS_FOR_COURSE_MODE[course_mode])

        # Verify the redirect
        if expected_redirect == 'dashboard':
            redirect_url = reverse('dashboard')
        elif expected_redirect == 'start-flow':
            redirect_url = IDVerificationService.get_verify_location(course_id=self.course.id)
        else:
            self.fail("Must provide a valid redirect URL name")

        with mock_payment_processors(expect_called=None):
            self.assertRedirects(response, redirect_url, fetch_redirect_response=False,)

    def test_choose_mode_audit_enroll_on_post(self):
        audit_mode = 'audit'
        # Create the course modes
        for mode in (audit_mode, 'verified'):
            min_price = 0 if mode in [audit_mode] else 1
            CourseModeFactory.create(mode_slug=mode, course_id=self.course.id, min_price=min_price)

        # Assert learner is not enrolled in Audit track pre-POST
        mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert mode is None
        assert is_active is None

        # Choose the audit mode (POST request)
        choose_track_url = reverse('course_modes_choose', args=[str(self.course.id)])
        self.client.post(choose_track_url, self.POST_PARAMS_FOR_COURSE_MODE[audit_mode])

        # Assert learner is enrolled in Audit track post-POST
        mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert mode == audit_mode
        assert is_active

        # Unenroll learner from Audit track and confirm the enrollment record is now 'inactive'
        CourseEnrollment.unenroll(self.user, self.course.id)
        mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert mode == audit_mode
        assert not is_active

        # Choose the audit mode again
        self.client.post(choose_track_url, self.POST_PARAMS_FOR_COURSE_MODE[audit_mode])

        # Assert learner is again enrolled in Audit track post-POST-POST
        mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert mode == audit_mode
        assert is_active

    def test_remember_donation_for_course(self):
        # Create the course modes
        CourseModeFactory.create(mode_slug='honor', course_id=self.course.id)
        CourseModeFactory.create(mode_slug='verified', course_id=self.course.id, min_price=1)

        # Choose the mode (POST request)
        choose_track_url = reverse('course_modes_choose', args=[str(self.course.id)])
        self.client.post(choose_track_url, self.POST_PARAMS_FOR_COURSE_MODE['verified'])

        # Expect that the contribution amount is stored in the user's session
        assert 'donation_for_course' in self.client.session
        assert str(self.course.id) in self.client.session['donation_for_course']

        actual_amount = self.client.session['donation_for_course'][str(self.course.id)]
        expected_amount = decimal.Decimal(self.POST_PARAMS_FOR_COURSE_MODE['verified']['contribution'])
        assert actual_amount == expected_amount

    def test_successful_default_enrollment(self):
        # Create the course modes
        for mode in (CourseMode.DEFAULT_MODE_SLUG, 'verified'):
            CourseModeFactory.create(mode_slug=mode, course_id=self.course.id)

        # Enroll the user in the default mode (honor) to emulate
        # automatic enrollment
        params = {
            'enrollment_action': 'enroll',
            'course_id': str(self.course.id)
        }
        self.client.post(reverse('change_enrollment'), params)

        # Explicitly select the honor mode (POST request)
        choose_track_url = reverse('course_modes_choose', args=[str(self.course.id)])
        self.client.post(choose_track_url, self.POST_PARAMS_FOR_COURSE_MODE[CourseMode.DEFAULT_MODE_SLUG])

        # Verify that the user's enrollment remains unchanged
        mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
        assert mode == CourseMode.DEFAULT_MODE_SLUG
        assert is_active is True

    @skip_unless_lms
    def test_default_mode_creation(self):
        # Hit the mode creation endpoint with no querystring params, to create an honor mode
        url = reverse('create_mode', args=[str(self.course.id)])
        response = self.client.get(url)

        assert response.status_code == 200

        expected_mode = [Mode('honor', 'Honor Code Certificate', 0, '', 'usd', None, None, None, None, None, None)]
        course_mode = CourseMode.modes_for_course(self.course.id)

        assert course_mode == expected_mode

    @skip_unless_lms
    @ddt.data(
        ('verified', 'Verified Certificate', 10, '10,20,30', 'usd'),
        ('professional', 'Professional Education', 100, '100,200', 'usd'),
    )
    @ddt.unpack
    def test_verified_mode_creation(self, mode_slug, mode_display_name, min_price, suggested_prices, currency):
        parameters = {}
        parameters['mode_slug'] = mode_slug
        parameters['mode_display_name'] = mode_display_name
        parameters['min_price'] = min_price
        parameters['suggested_prices'] = suggested_prices
        parameters['currency'] = currency

        url = reverse('create_mode', args=[str(self.course.id)])
        response = self.client.get(url, parameters)

        assert response.status_code == 200

        expected_mode = [
            Mode(
                mode_slug,
                mode_display_name,
                min_price,
                suggested_prices,
                currency,
                None,
                None,
                None,
                None,
                None,
                None
            )
        ]
        course_mode = CourseMode.modes_for_course(self.course.id)

        assert course_mode == expected_mode

    @skip_unless_lms
    def test_multiple_mode_creation(self):
        # Create an honor mode
        base_url = reverse('create_mode', args=[str(self.course.id)])
        self.client.get(base_url)

        # Excluding the currency parameter implicitly tests the mode creation endpoint's ability to
        # use default values when parameters are partially missing.
        parameters = {}
        parameters['mode_slug'] = 'verified'
        parameters['mode_display_name'] = 'Verified Certificate'
        parameters['min_price'] = 10
        parameters['suggested_prices'] = '10,20'

        # Create a verified mode
        url = reverse('create_mode', args=[str(self.course.id)])
        self.client.get(url, parameters)

        honor_mode = Mode('honor', 'Honor Code Certificate', 0, '', 'usd', None, None, None, None, None, None)
        verified_mode = Mode('verified', 'Verified Certificate', 10, '10,20', 'usd', None, None, None, None, None, None)
        expected_modes = [honor_mode, verified_mode]
        course_modes = CourseMode.modes_for_course(self.course.id)

        assert course_modes == expected_modes

    @skip_unless_lms
    @with_comprehensive_theme("edx.org")
    @httpretty.activate
    def test_hide_nav(self):
        # Create the course modes
        for mode in ["honor", "verified"]:
            CourseModeFactory.create(mode_slug=mode, course_id=self.course.id)

        # Load the track selection page
        url = reverse('course_modes_choose', args=[str(self.course.id)])
        response = self.client.get(url)

        # Verify that the header navigation links are hidden for the edx.org version
        self.assertNotContains(response, "How it Works")
        self.assertNotContains(response, "Find courses")
        self.assertNotContains(response, "Schools & Partners")

    @skip_unless_lms
    def test_course_closed(self):
        with freezegun.freeze_time('2015-01-02'):
            for mode in ["honor", "verified"]:
                CourseModeFactory(mode_slug=mode, course_id=self.course.id)

            self.course.enrollment_end = datetime(2015, 1, 1)
            modulestore().update_item(self.course, self.user.id)

            url = reverse('course_modes_choose', args=[str(self.course.id)])
            response = self.client.get(url)
            # URL-encoded version of 1/1/15, 12:00 AM
            redirect_url = reverse('dashboard') + '?course_closed=1%2F1%2F15%2C+12%3A00%E2%80%AFAM'
            self.assertRedirects(response, redirect_url)

    @ddt.data(
        (False, {'audit_mode': True}, 'Enrollment is closed', 302),
        (False, {'verified_mode': True, 'contribution': '1.23'}, 'Enrollment is closed', 302),
        (True, {'verified_mode': True, 'contribution': 'abc'}, 'Invalid amount selected', 200),
        (True, {'verified_mode': True, 'contribution': '0.1'}, 'No selected price or selected price is too low.', 200),
        (True, {'unsupported_mode': True}, 'Enrollment mode not supported', 200),
    )
    @ddt.unpack
    @patch('django.contrib.auth.models.PermissionsMixin.has_perm')
    def test_errors(self, has_perm, post_params, error_msg, status_code, mock_has_perm):
        """
        Test the error template is rendered on different types of errors.
        When the chosen CourseMode is 'honor' or 'audit' via POST,
        it redirects to dashboard, but if there's an error in the process,
        it shows the error template.
        If the user does not have permission to enroll, GET is called with error message,
        but it also redirects to dashboard.
        """
        # Create course modes
        for mode in ('audit', 'honor', 'verified'):
            CourseModeFactory.create(mode_slug=mode, course_id=self.course.id)

        mock_has_perm.return_value = has_perm
        url = reverse('course_modes_choose', args=[str(self.course.id)])

        # Choose mode (POST request)
        response = self.client.post(url, post_params)
        self.assertEqual(response.status_code, status_code)

        if has_perm:
            self.assertContains(response, error_msg)
            self.assertContains(response, 'Sorry, we were unable to enroll you')

            # Check for CTA button on error page
            marketing_root = settings.MKTG_URLS.get('ROOT')
            search_courses_url = urljoin(marketing_root, '/search?tab=course')
            self.assertContains(response, search_courses_url)
            self.assertContains(response, '<span>Explore all courses</span>')
        else:
            self.assertTrue(CourseEnrollment.is_enrollment_closed(self.user, self.course))

    def _assert_fbe_page(self, response, min_price=None, **_):
        """
        Assert fbe.html was rendered.
        """
        self.assertContains(response, "Choose a path for your course in")

        # Check if it displays the upgrade price for verified track and "Free" for audit track
        self.assertContains(response, min_price)
        self.assertContains(response, "Free")

        # Check for specific HTML elements
        self.assertContains(response, '<span class="award-icon">')
        self.assertContains(response, '<span class="popover-icon">')
        self.assertContains(response, '<span class="note-icon">')
        self.assertContains(response, '<div class="grid-options">')

        # Check for upgrade button ID
        self.assertContains(response, 'track_selection_upgrade')

        # Check for audit button ID
        self.assertContains(response, 'track_selection_audit')

        # Check for happy path messaging - verified
        self.assertContains(response, '<li class="collapsible-item">')
        self.assertContains(response, 'access to all course activities')
        self.assertContains(response, 'Full access')

        # Check for informational links - verified
        marketing_root = settings.MKTG_URLS.get('ROOT')
        marketing_url = urljoin(marketing_root, 'verified-certificate')
        self.assertContains(response, marketing_url)
        support_root = settings.SUPPORT_SITE_LINK
        article_params = ('hc/en-us/articles/360013426573-'
                          'What-are-the-differences-between-audit-free-and-verified-paid-courses-')
        support_url = urljoin(support_root, article_params)
        self.assertContains(response, support_url)

        # Check for happy path messaging - audit
        self.assertContains(response, "discussion forums and non-graded assignments")
        self.assertContains(response, "Get temporary access")
        self.assertContains(response, "Access expires and all progress will be lost")

    def _assert_unfbe_page(self, response, min_price=None, **_):
        """
        Assert track_selection.html and unfbe.html were rendered.
        """
        # Check for string unique to track_selection.html.
        self.assertContains(response, "| Upgrade Now")
        # This string only occurs in lms/templates/course_modes/track_selection.html
        # and related theme and translation files.

        # Check min_price was correctly passed in.
        self.assertContains(response, min_price)

        # Check for the HTML element for courses with more than one mode
        self.assertContains(response, '<div class="grid-options">')

    @override_settings(MKTG_URLS={'ROOT': 'https://www.example.edx.org'})
    @ddt.data(
        # gated_content_on, course_duration_limits_on, expected_page_assertion_function
        (True, True, _assert_fbe_page),
        (True, False, _assert_unfbe_page),
        (False, True, _assert_unfbe_page),
        (False, False, _assert_unfbe_page),
    )
    @ddt.unpack
    def test_track_selection_types(
            self,
            gated_content_on,
            course_duration_limits_on,
            expected_page_assertion_function
    ):
        """
        Feature-based enrollment (FBE) is when gated content and course duration
        limits are enabled when a user is auditing a course.

        When prompted to perform track selection (choosing between the audit and
        verified course modes), the learner may view 3 different pages:
            1. fbe.html - full FBE
            2. unfbe.html - partial or no FBE

        This test checks that the right template is rendered.

        """
        # Create audit/honor course modes
        for mode in ('audit', 'honor'):
            CourseModeFactory.create(mode_slug=mode, course_id=self.course_that_started.id)

        # Create verified course mode:
        verified_mode = CourseModeFactory.create(
            mode_slug='verified',
            course_id=self.course_that_started.id,
            min_price=149,
        )

        # Enroll the test user in the audit mode:
        CourseEnrollmentFactory(
            is_active=True,
            course_id=self.course_that_started.id,
            user=self.user
        )

        with patch(GATING_METHOD_NAME, return_value=gated_content_on):
            with patch(CDL_METHOD_NAME, return_value=course_duration_limits_on):
                url = reverse('course_modes_choose', args=[str(self.course_that_started.id)])
                response = self.client.get(url)
                expected_page_assertion_function(self, response, min_price=verified_mode.min_price)

    def test_verified_mode_only(self):
        # Create only the verified mode and enroll the user
        CourseModeFactory.create(
            mode_slug='verified',
            course_id=self.course_that_started.id,
            min_price=149,
        )
        CourseEnrollmentFactory(
            is_active=True,
            course_id=self.course_that_started.id,
            user=self.user
        )

        with patch(GATING_METHOD_NAME, return_value=True):
            with patch(CDL_METHOD_NAME, return_value=True):
                url = reverse('course_modes_choose', args=[str(self.course_that_started.id)])
                response = self.client.get(url)
                # Check that only the verified option is rendered
                self.assertNotContains(response, "Choose a path for your course in")
                self.assertContains(response, "Earn a certificate")
                self.assertNotContains(response, "Access this course")
                self.assertContains(response, '<div class="grid-single">')
                self.assertNotContains(response, '<div class="grid-options">')


@skip_unless_lms
class TrackSelectionEmbargoTest(UrlResetMixin, ModuleStoreTestCase):
    """Test embargo restrictions on the track selection page. """

    URLCONF_MODULES = ['openedx.core.djangoapps.embargo']

    @patch.dict(settings.FEATURES, {'EMBARGO': True})
    def setUp(self):
        super().setUp()

        # Create a course and course modes
        self.course = CourseFactory.create()
        CourseModeFactory.create(mode_slug='honor', course_id=self.course.id)
        CourseModeFactory.create(mode_slug='verified', course_id=self.course.id, min_price=10)

        # Create a user and log in
        self.user = UserFactory.create(username="Bob", email="bob@example.com", password="edx")
        self.client.login(username=self.user.username, password="edx")

        # Construct the URL for the track selection page
        self.url = reverse('course_modes_choose', args=[str(self.course.id)])

    @patch.dict(settings.FEATURES, {'EMBARGO': True})
    def test_embargo_restrict(self):
        with restrict_course(self.course.id) as redirect_url:
            response = self.client.get(self.url)
            self.assertRedirects(response, redirect_url)

    @httpretty.activate
    def test_embargo_allow(self):
        response = self.client.get(self.url)
        assert response.status_code == 200
