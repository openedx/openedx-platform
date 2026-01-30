"""
Tests courseware views.py
"""

from contextlib import contextmanager
import html
import itertools
import json
import re
from datetime import datetime, timedelta
from unittest.mock import MagicMock, PropertyMock, create_autospec, patch
from urllib.parse import quote, urlencode
from uuid import uuid4

import ddt
from completion.test_utils import CompletionWaffleTestMixin
from crum import set_current_request
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.http.request import QueryDict
from django.test import override_settings, RequestFactory, TestCase
from django.test.client import Client
from django.urls import reverse, reverse_lazy
from edx_django_utils.cache.utils import RequestCache
from edx_toggles.toggles.testutils import override_waffle_flag, override_waffle_switch
from freezegun import freeze_time
from opaque_keys.edx.keys import CourseKey, UsageKey
from pytz import UTC
from openedx.core.djangoapps.waffle_utils.models import WaffleFlagCourseOverrideModel
from rest_framework import status
from rest_framework.test import APIClient
from web_fragments.fragment import Fragment
from xblock.core import XBlock
from xblock.fields import Scope, String
from xblock.scorable import ShowCorrectness
from xmodule.capa.tests.response_xml_factory import MultipleChoiceResponseXMLFactory
from xmodule.data import CertificatesDisplayBehaviors
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import CourseUserType, ModuleStoreTestCase, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory, check_mongo_calls

import lms.djangoapps.courseware.views.views as views
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import (
    TEST_PASSWORD,
    AdminFactory,
    CourseEnrollmentFactory,
    GlobalStaffFactory,
    RequestFactoryNoCsrf,
    UserFactory
)
from common.djangoapps.util.tests.test_date_utils import fake_pgettext, fake_ugettext
from common.djangoapps.util.url import reload_django_url_config
from common.djangoapps.util.views import ensure_valid_course_key
from lms.djangoapps.certificates import api as certs_api
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.tests.factories import (
    CertificateAllowlistFactory,
    CertificateInvalidationFactory,
    GeneratedCertificateFactory
)
from lms.djangoapps.commerce.models import CommerceConfiguration
from lms.djangoapps.commerce.utils import EcommerceService
from lms.djangoapps.courseware.access_utils import check_course_open_for_learner
from lms.djangoapps.courseware.model_data import FieldDataCache, set_score
from lms.djangoapps.courseware.block_render import get_block, handle_xblock_callback
from lms.djangoapps.courseware.tests.helpers import MasqueradeMixin, get_expiration_banner_text
from lms.djangoapps.courseware.testutils import RenderXBlockTestMixin
from lms.djangoapps.courseware.toggles import (
    COURSEWARE_MICROFRONTEND_SEARCH_ENABLED,
    COURSEWARE_OPTIMIZED_RENDER_XBLOCK,
)
from completion.waffle import ENABLE_COMPLETION_TRACKING_SWITCH
from lms.djangoapps.courseware.user_state_client import DjangoXBlockUserStateClient
from lms.djangoapps.courseware.views.views import (
    BasePublicVideoXBlockView,
    PublicVideoXBlockView,
    PublicVideoXBlockEmbedView,
)
from lms.djangoapps.instructor.access import allow_access
from lms.djangoapps.verify_student.services import IDVerificationService
from openedx.core.djangoapps.catalog.tests.factories import CourseFactory as CatalogCourseFactory
from openedx.core.djangoapps.catalog.tests.factories import CourseRunFactory, ProgramFactory
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.credit.api import set_credit_requirements
from openedx.core.djangoapps.credit.models import CreditCourse, CreditProvider
from openedx.core.djangoapps.waffle_utils.testutils import WAFFLE_TABLES
from openedx.core.djangolib.testing.utils import get_mock_request
from openedx.core.djangoapps.video_config.toggles import PUBLIC_VIDEO_SHARE
from openedx.core.lib.url_utils import quote_slashes
from openedx.features.content_type_gating.models import ContentTypeGatingConfig
from openedx.features.course_duration_limits.models import CourseDurationLimitConfig
from openedx.features.course_experience.tests.views.helpers import add_course_mode
from openedx.features.course_experience.url_helpers import (
    get_learning_mfe_home_url,
    make_learning_mfe_courseware_url
)
from openedx.features.enterprise_support.tests.factories import (
    EnterpriseCourseEnrollmentFactory,
    EnterpriseCustomerUserFactory,
    EnterpriseCustomerFactory
)
from openedx.features.enterprise_support.tests.mixins.enterprise import EnterpriseTestConsentRequired
from openedx.features.enterprise_support.api import add_enterprise_customer_to_session
from enterprise.api.v1.serializers import EnterpriseCustomerSerializer

QUERY_COUNT_TABLE_IGNORELIST = WAFFLE_TABLES

FEATURES_WITH_DISABLE_HONOR_CERTIFICATE = settings.FEATURES.copy()
FEATURES_WITH_DISABLE_HONOR_CERTIFICATE['DISABLE_HONOR_CERTIFICATES'] = True


@ddt.ddt
class TestJumpTo(ModuleStoreTestCase):
    """
    Check the jumpto link for a course.
    """
    @ddt.data(
        (False, ModuleStoreEnum.Type.split),
        (True, ModuleStoreEnum.Type.split),
    )
    @ddt.unpack
    def test_jump_to_invalid_location(self, preview_mode, store_type):
        """Confirm that invalid locations redirect back to a general course URL"""
        with self.store.default_store(store_type):
            course = CourseFactory.create()
            location = course.id.make_usage_key(None, 'NoSuchPlace')

        expected_redirect_url = f'http://learning-mfe/course/{course.id}'
        jumpto_url = (
            f'/courses/{course.id}/jump_to/{location}?preview=1'
        ) if preview_mode else (
            f'/courses/{course.id}/jump_to/{location}'
        )

        # This is fragile, but unfortunately the problem is that within the LMS we
        # can't use the reverse calls from the CMS
        response = self.client.get(jumpto_url)
        assert response.status_code == 302
        assert response.url == expected_redirect_url

    def test_jump_to_preview_from_sequence(self):
        with self.store.default_store(ModuleStoreEnum.Type.split):
            course = CourseFactory.create()
            chapter = BlockFactory.create(category='chapter', parent_location=course.location)
            sequence = BlockFactory.create(category='sequential', parent_location=chapter.location)
        jumpto_url = f'/courses/{course.id}/jump_to/{sequence.location}?preview=1'
        expected_redirect_url = (
            f'http://learning-mfe/preview/course/{course.id}/{sequence.location}'
        )
        response = self.client.get(jumpto_url)
        assert response.status_code == 302
        assert response.url == expected_redirect_url

    def test_jump_to_mfe_from_sequence(self):
        course = CourseFactory.create()
        chapter = BlockFactory.create(category='chapter', parent_location=course.location)
        sequence = BlockFactory.create(category='sequential', parent_location=chapter.location)
        expected_redirect_url = (
            f'http://learning-mfe/course/{course.id}/{sequence.location}'
        )
        jumpto_url = f'/courses/{course.id}/jump_to/{sequence.location}'
        response = self.client.get(jumpto_url)
        assert response.status_code == 302
        assert response.url == expected_redirect_url

    def test_jump_to_preview_from_block(self):
        with self.store.default_store(ModuleStoreEnum.Type.split):
            course = CourseFactory.create()
            chapter = BlockFactory.create(category='chapter', parent_location=course.location)
            sequence = BlockFactory.create(category='sequential', parent_location=chapter.location)
            vertical1 = BlockFactory.create(category='vertical', parent_location=sequence.location)
            vertical2 = BlockFactory.create(category='vertical', parent_location=sequence.location)
            block1 = BlockFactory.create(category='html', parent_location=vertical1.location)
            block2 = BlockFactory.create(category='html', parent_location=vertical2.location)

        jumpto_url = f'/courses/{course.id}/jump_to/{block1.location}?preview=1'
        expected_redirect_url = (
            f'http://learning-mfe/preview/course/{course.id}/{sequence.location}/{vertical1.location}'
        )
        response = self.client.get(jumpto_url)
        assert response.status_code == 302
        assert response.url == expected_redirect_url

        jumpto_url = f'/courses/{course.id}/jump_to/{block2.location}?preview=1'
        expected_redirect_url = (
            f'http://learning-mfe/preview/course/{course.id}/{sequence.location}/{vertical2.location}'
        )
        response = self.client.get(jumpto_url)
        assert response.status_code == 302
        assert response.url == expected_redirect_url

    def test_jump_to_mfe_from_block(self):
        course = CourseFactory.create()
        chapter = BlockFactory.create(category='chapter', parent_location=course.location)
        sequence = BlockFactory.create(category='sequential', parent_location=chapter.location)
        vertical1 = BlockFactory.create(category='vertical', parent_location=sequence.location)
        vertical2 = BlockFactory.create(category='vertical', parent_location=sequence.location)
        block1 = BlockFactory.create(category='html', parent_location=vertical1.location)
        block2 = BlockFactory.create(category='html', parent_location=vertical2.location)

        expected_redirect_url = (
            f'http://learning-mfe/course/{course.id}/{sequence.location}/{vertical1.location}'
        )
        jumpto_url = f'/courses/{course.id}/jump_to/{block1.location}'
        response = self.client.get(jumpto_url)
        assert response.status_code == 302
        assert response.url == expected_redirect_url

        expected_redirect_url = (
            f'http://learning-mfe/course/{course.id}/{sequence.location}/{vertical2.location}'
        )
        jumpto_url = f'/courses/{course.id}/jump_to/{block2.location}'
        response = self.client.get(jumpto_url)
        assert response.status_code == 302
        assert response.url == expected_redirect_url

    @ddt.data(
        (False, ModuleStoreEnum.Type.split),
        (True, ModuleStoreEnum.Type.split),
    )
    @ddt.unpack
    def test_jump_to_id_invalid_location(self, preview_mode, store_type):
        with self.store.default_store(store_type):
            course = CourseFactory.create()
        jumpto_url = (
            f'/courses/{course.id}/jump_to/NoSuchPlace?preview=1'
        ) if preview_mode else (
            f'/courses/{course.id}/jump_to/NoSuchPlace'
        )
        response = self.client.get(jumpto_url)
        assert response.status_code == 404


class BaseViewsTestCase(ModuleStoreTestCase, MasqueradeMixin):
    """Base class for courseware tests"""
    CREATE_USER = False

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(display_name='teꜱᴛ course', run="Testing_course")
        with self.store.bulk_operations(self.course.id):
            self.chapter = BlockFactory.create(
                category='chapter',
                parent_location=self.course.location,
                display_name="Chapter 1",
            )
            self.section = BlockFactory.create(
                category='sequential',
                parent_location=self.chapter.location,
                due=datetime(2013, 9, 18, 11, 30, 00),
                display_name='Sequential 1',
                format='Homework'
            )
            self.vertical = BlockFactory.create(
                category='vertical',
                parent_location=self.section.location,
                display_name='Vertical 1',
            )
            self.problem = BlockFactory.create(
                category='problem',
                parent_location=self.vertical.location,
                display_name='Problem 1',
            )

            self.section2 = BlockFactory.create(
                category='sequential',
                parent_location=self.chapter.location,
                display_name='Sequential 2',
            )
            self.vertical2 = BlockFactory.create(
                category='vertical',
                parent_location=self.section2.location,
                display_name='Vertical 2',
            )
            self.problem2 = BlockFactory.create(
                category='problem',
                parent_location=self.vertical2.location,
                display_name='Problem 2',
            )

        self.course_key = self.course.id
        # Set profile country to Åland Islands to check Unicode characters does not raise error
        self.user = UserFactory(username='dummy', profile__country='AX')
        self.date = datetime(2013, 1, 22, tzinfo=UTC)
        self.enrollment = CourseEnrollment.enroll(self.user, self.course_key)
        self.enrollment.created = self.date
        self.enrollment.save()
        chapter = 'Overview'
        self.chapter_url = '{}/{}/{}'.format('/courses', self.course_key, chapter)

        self.org = "ꜱᴛᴀʀᴋ ɪɴᴅᴜꜱᴛʀɪᴇꜱ"
        self.org_html = "<p>'+Stark/Industries+'</p>"

        assert self.client.login(username=self.user.username, password=TEST_PASSWORD)

        # refresh the course from the modulestore so that it has children
        self.course = modulestore().get_course(self.course.id)

    def _create_global_staff_user(self):
        """
        Create global staff user and log them in
        """
        self.global_staff = GlobalStaffFactory.create()  # pylint: disable=attribute-defined-outside-init
        assert self.client.login(username=self.global_staff.username, password=TEST_PASSWORD)


@ddt.ddt
class CoursewareIndexTestCase(BaseViewsTestCase):
    """
    Tests for the courseware index view, used for instructor previews.
    """

    def setUp(self):
        super().setUp()
        self._create_global_staff_user()  # this view needs staff permission

    def test_course_redirect(self):
        lms_url = reverse(
            'courseware',
            kwargs={
                'course_id': str(self.course_key),
            }
        )

        mfe_url = make_learning_mfe_courseware_url(self.course.id)

        response = self.client.get(lms_url)
        assert response.url == mfe_url

    def test_section_redirect(self):
        lms_url = reverse(
            'courseware_section',
            kwargs={
                'course_id': str(self.course_key),
                'section': str(self.chapter.location.block_id),
            }
        )

        mfe_url = make_learning_mfe_courseware_url(self.course.id)

        response = self.client.get(lms_url)
        assert response.url == mfe_url

    def test_subsection_redirect(self):
        lms_url = reverse(
            'courseware_subsection',
            kwargs={
                'course_id': str(self.course_key),
                'section': str(self.chapter.location.block_id),
                'subsection': str(self.section2.location.block_id),
            }
        )

        mfe_url = make_learning_mfe_courseware_url(self.course.id, self.section2.location)

        response = self.client.get(lms_url)
        assert response.url == mfe_url


@ddt.ddt
class ViewsTestCase(BaseViewsTestCase):
    """
    Tests for views.py methods.
    """
    YESTERDAY = 'yesterday'
    DATES = {
        YESTERDAY: datetime.now(UTC) - timedelta(days=1),
        None: None,
    }

    def test_mfe_link_from_about_page(self):
        """
        Verify course about page links to the MFE.
        """
        with self.store.default_store(ModuleStoreEnum.Type.split):
            course = CourseFactory.create()
        CourseEnrollment.enroll(self.user, course.id)

        response = self.client.get(reverse('about_course', args=[str(course.id)]))
        self.assertContains(response, get_learning_mfe_home_url(course_key=course.id, url_fragment='home'))

    def _create_url_for_enroll_staff(self):
        """
        creates the courseware url and enroll staff url
        """
        # create the _next parameter
        courseware_url = make_learning_mfe_courseware_url(self.course.id, self.chapter.location, self.section.location)
        courseware_url = quote(courseware_url, safe=':/')
        # create the url for enroll_staff view
        enroll_url = "{enroll_url}?next={courseware_url}".format(
            enroll_url=reverse('enroll_staff', kwargs={'course_id': str(self.course.id)}),
            courseware_url=courseware_url
        )
        return courseware_url, enroll_url

    @ddt.data(
        ({'enroll': "Enroll"}, True),
        ({'dont_enroll': "Don't enroll"}, False))
    @ddt.unpack
    def test_enroll_staff_redirection(self, data, enrollment):
        """
        Verify unenrolled staff is redirected to correct url.
        """
        self._create_global_staff_user()
        courseware_url, enroll_url = self._create_url_for_enroll_staff()
        response = self.client.post(enroll_url, data=data)

        # we were redirected to our current location
        if enrollment:
            self.assertRedirects(response, courseware_url, fetch_redirect_response=False)
        else:
            self.assertRedirects(response, f'/courses/{str(self.course_key)}/about')

    def test_enroll_staff_with_invalid_data(self):
        """
        If we try to post with an invalid data pattern, then we'll redirected to
        course about page.
        """
        self._create_global_staff_user()
        __, enroll_url = self._create_url_for_enroll_staff()
        response = self.client.post(enroll_url, data={'test': "test"})
        assert response.status_code == 302
        self.assertRedirects(response, f'/courses/{str(self.course_key)}/about')

    def assert_enrollment_link_present(self, is_anonymous):
        """
        Prepare ecommerce checkout data and assert if the ecommerce link is contained in the response.
        Arguments:
            is_anonymous(bool): Tell the method to use an anonymous user or the logged in one.
            _id(bool): Tell the method to either expect an id in the href or not.
        """
        sku = 'TEST123'
        configuration = CommerceConfiguration.objects.create(checkout_on_ecommerce_service=True)
        course = CourseFactory.create()
        CourseModeFactory(mode_slug=CourseMode.PROFESSIONAL, course_id=course.id, sku=sku, min_price=1)

        if is_anonymous:
            self.client.logout()
        else:
            assert self.client.login(username=self.user.username, password=TEST_PASSWORD)

        # Construct the link according the following scenarios and verify its presence in the response:
        #      (1) shopping cart is enabled and the user is not logged in
        #      (2) shopping cart is enabled and the user is logged in
        href = '<a href="{uri_stem}?sku={sku}" class="add-to-cart">'.format(
            uri_stem=configuration.basket_checkout_page,
            sku=sku,
        )

        # Generate the course about page content
        response = self.client.get(reverse('about_course', args=[str(course.id)]))
        self.assertContains(response, href)

    @ddt.data(True, False)
    def test_ecommerce_checkout(self, is_anonymous):
        if not is_anonymous:
            self.assert_enrollment_link_present(is_anonymous=is_anonymous)
        else:
            assert EcommerceService().is_enabled(AnonymousUser()) is False

    def test_user_groups(self):
        # deprecated function
        mock_user = MagicMock()
        type(mock_user).is_authenticated = PropertyMock(return_value=False)
        assert views.user_groups(mock_user) == []

    def test_invalid_course_id(self):
        response = self.client.get('/courses/MITx/3.091X/')
        assert response.status_code == 404

    def test_incomplete_course_id(self):
        response = self.client.get('/courses/MITx/')
        assert response.status_code == 404

    def test_jump_to_invalid(self):
        # TODO add a test for invalid location
        # TODO add a test for no data *
        response = self.client.get(reverse('jump_to', args=['foo/bar/baz', 'baz']))
        assert response.status_code == 404

    def verify_end_date(self, course_id, expected_end_text=None):
        """
        Visits the about page for `course_id` and tests that both the text "Classes End", as well
        as the specified `expected_end_text`, is present on the page.
        If `expected_end_text` is None, verifies that the about page *does not* contain the text
        "Classes End".
        """
        result = self.client.get(reverse('about_course', args=[str(course_id)]))
        if expected_end_text is not None:
            self.assertContains(result, "Classes End")
            self.assertContains(result, expected_end_text)
        else:
            self.assertNotContains(result, "Classes End")

    def test_submission_history_accepts_valid_ids(self):
        # log into a staff account
        admin = AdminFactory()

        assert self.client.login(username=admin.username, password=TEST_PASSWORD)

        url = reverse('submission_history', kwargs={
            'course_id': str(self.course_key),
            'learner_identifier': 'dummy',
            'location': str(self.problem.location),
        })
        response = self.client.get(url)
        # Tests that we do not get an "Invalid x" response when passing correct arguments to view
        self.assertNotContains(response, 'Invalid')

    def test_submission_history_xss(self):
        # log into a staff account
        admin = AdminFactory()

        assert self.client.login(username=admin.username, password=TEST_PASSWORD)

        # try it with an existing user and a malicious location
        url = reverse('submission_history', kwargs={
            'course_id': str(self.course_key),
            'learner_identifier': 'dummy',
            'location': '<script>alert("hello");</script>'
        })
        response = self.client.get(url)
        self.assertNotContains(response, '<script>')

        # try it with a malicious user and a non-existent location
        url = reverse('submission_history', kwargs={
            'course_id': str(self.course_key),
            'learner_identifier': '<script>alert("hello");</script>',
            'location': 'dummy'
        })
        response = self.client.get(url)
        self.assertNotContains(response, '<script>')

    def test_submission_history_contents(self):
        # log into a staff account
        admin = AdminFactory.create()

        assert self.client.login(username=admin.username, password=TEST_PASSWORD)

        usage_key = self.course_key.make_usage_key('problem', 'test-history')
        state_client = DjangoXBlockUserStateClient(admin)

        # store state via the UserStateClient
        state_client.set(
            username=admin.username,
            block_key=usage_key,
            state={'field_a': 'x', 'field_b': 'y'}
        )

        set_score(admin.id, usage_key, 0, 3)

        # Typically an http request has its own thread and RequestCache which is used to ensure that only
        # one StudentModuleHistory record is created per request. However, since tests are run locally
        # the score updates share the same thread. If the RequestCache is not cleared in between the two
        # activities, then there will only be one StudentModuleHistory record that reflects the final state
        # rather than one per action. Clearing the cache here allows the second action below to generate
        # its own history record. The assertions below are then able to check that both states are recorded
        # in the history.
        RequestCache('studentmodulehistory').clear()

        state_client.set(
            username=admin.username,
            block_key=usage_key,
            state={'field_a': 'a', 'field_b': 'b'}
        )
        set_score(admin.id, usage_key, 3, 3)

        url = reverse('submission_history', kwargs={
            'course_id': str(self.course_key),
            'learner_identifier': admin.email,
            'location': str(usage_key),
        })
        response = self.client.get(url)
        response_content = html.unescape(response.content.decode('utf-8'))

        # We have update the state 2 times. We'll check that the identifying content from each is
        # displayed (but not the order), and also the indexes assigned in the output
        # #1 - #2

        assert '#1' in response_content
        assert json.dumps({'field_a': 'a', 'field_b': 'b'}, sort_keys=True, indent=2) in response_content
        assert 'Score: 0.0 / 3.0' in response_content
        assert json.dumps({'field_a': 'x', 'field_b': 'y'}, sort_keys=True, indent=2) in response_content
        assert 'Score: 3.0 / 3.0' in response_content
        assert '#2' in response_content
        assert '#3' not in response_content

    @ddt.data(('America/New_York', -5),  # UTC - 5
              ('Asia/Pyongyang', 9),  # UTC + 9
              ('Europe/London', 0),  # UTC
              ('Canada/Yukon', -8),  # UTC - 8
              ('Europe/Moscow', 4))  # UTC + 3 + 1 for daylight savings
    @ddt.unpack
    def test_submission_history_timezone(self, timezone, hour_diff):
        with freeze_time('2012-01-01'):
            with (override_settings(TIME_ZONE=timezone)):  # lint-amnesty, pylint: disable=superfluous-parens
                course = CourseFactory.create()
                course_key = course.id
                client = Client()
                admin = AdminFactory.create()
                assert client.login(username=admin.username, password=TEST_PASSWORD)
                state_client = DjangoXBlockUserStateClient(admin)
                usage_key = course_key.make_usage_key('problem', 'test-history')
                state_client.set(
                    username=admin.username,
                    block_key=usage_key,
                    state={'field_a': 'x', 'field_b': 'y'}
                )
                url = reverse('submission_history', kwargs={
                    'course_id': str(course_key),
                    'learner_identifier': admin.username,
                    'location': str(usage_key),
                })
                response = client.get(url)
                expected_time = datetime.now() + timedelta(hours=hour_diff)
                expected_tz = expected_time.strftime('%Z')
                self.assertContains(response, expected_tz)
                self.assertContains(response, str(expected_time))

    def _email_opt_in_checkbox(self, response, org_name_string=None):
        """Check if the email opt-in checkbox appears in the response content."""
        checkbox_html = '<input id="email-opt-in" type="checkbox" name="opt-in" class="email-opt-in" value="true" checked>'  # lint-amnesty, pylint: disable=line-too-long
        if org_name_string:
            # Verify that the email opt-in checkbox appears, and that the expected
            # organization name is displayed.
            self.assertContains(response, checkbox_html, html=True)
            self.assertContains(response, org_name_string)
        else:
            # Verify that the email opt-in checkbox does not appear
            self.assertNotContains(response, checkbox_html, html=True)

    def test_financial_assistance_page(self):
        url = reverse('financial_assistance')
        response = self.client.get(url)
        # This is a static page, so just assert that it is returned correctly
        assert response.status_code == 200
        self.assertContains(response, 'Financial Assistance Application')

    @patch('lms.djangoapps.courseware.views.views._use_new_financial_assistance_flow', return_value=True)
    @patch('lms.djangoapps.courseware.views.views.is_eligible_for_financial_aid', return_value=(False, 'error reason'))
    def test_new_financial_assistance_page_course_ineligible(self, *args):
        """
        Test to verify the financial_assistance view against an ineligible course returns an error page.
        """
        url = reverse('financial_assistance_v2', args=['course-v1:test+TestX+Test_Course'])
        response = self.client.get(url)
        assert response.status_code == 200
        self.assertContains(response, 'This course is not eligible for Financial Assistance for the following reason:')

    @ddt.data(([CourseMode.AUDIT, CourseMode.VERIFIED], CourseMode.AUDIT, True, YESTERDAY),
              ([CourseMode.AUDIT, CourseMode.VERIFIED], CourseMode.VERIFIED, True, None),
              ([CourseMode.AUDIT, CourseMode.VERIFIED], CourseMode.AUDIT, False, None),
              ([CourseMode.AUDIT], CourseMode.AUDIT, False, None))
    @ddt.unpack
    def test_financial_assistance_form_course_exclusion(
            self, course_modes, enrollment_mode, eligible_for_aid, expiration):
        """Verify that learner cannot get the financial aid for the courses having one of the
        following attributes:
        1. User is enrolled in the verified mode.
        2. Course is expired.
        3. Course does not provide financial assistance.
        4. Course does not have verified mode.
        """
        # Create course
        course = CourseFactory.create()

        # Create Course Modes
        for mode in course_modes:
            CourseModeFactory.create(mode_slug=mode, course_id=course.id, expiration_datetime=self.DATES[expiration])

        # Enroll user in the course
        CourseEnrollmentFactory(course_id=course.id, user=self.user, mode=enrollment_mode)
        # load course into course overview
        CourseOverview.get_from_id(course.id)

        # add whether course is eligible for financial aid or not
        course_overview = CourseOverview.objects.get(id=course.id)
        course_overview.eligible_for_financial_aid = eligible_for_aid
        course_overview.save()

        url = reverse('financial_assistance_form')
        response = self.client.get(url)
        assert response.status_code == 200

        self.assertNotContains(response, str(course.id))

    def test_financial_assistance_form(self):
        """Verify that learner can get the financial aid for the course in which
        he/she is enrolled in audit mode whereas the course provide verified mode.
        """
        # Create course
        course = CourseFactory.create().id

        # Create Course Modes
        CourseModeFactory.create(mode_slug=CourseMode.AUDIT, course_id=course)
        CourseModeFactory.create(mode_slug=CourseMode.VERIFIED, course_id=course)

        # Enroll user in the course
        CourseEnrollmentFactory(course_id=course, user=self.user, mode=CourseMode.AUDIT)
        # load course into course overview
        CourseOverview.get_from_id(course)

        url = reverse('financial_assistance_form')
        response = self.client.get(url)
        assert response.status_code == 200

        self.assertContains(response, str(course))

    def _submit_financial_assistance_form(self, data, submit_url='submit_financial_assistance_request',
                                          referrer_url=None):
        """Submit a financial assistance request."""
        url = reverse(submit_url)
        return self.client.post(url, json.dumps(data), content_type='application/json', HTTP_REFERER=referrer_url)

    @override_settings(ZENDESK_CUSTOM_FIELDS={'course_id': 'custom_123'})
    @patch.object(views, 'create_zendesk_ticket', return_value=200)
    def test_submit_financial_assistance_request(self, mock_create_zendesk_ticket):
        username = self.user.username
        course = str(self.course_key)
        legal_name = 'Jesse Pinkman'
        country = 'United States'
        data = {
            'username': username,
            'course': course,
            'name': legal_name,
            'email': self.user.email,
            'country': country,
            'certify-economic-hardship': False,
            'certify-complete-certificate': False,
            'certify-honor-code': False,
        }
        response = self._submit_financial_assistance_form(data)
        assert response.status_code == 204

        __, __, ticket_subject, __ = mock_create_zendesk_ticket.call_args[0]
        mocked_kwargs = mock_create_zendesk_ticket.call_args[1]
        group_name = mocked_kwargs['group']
        custom_fields = mocked_kwargs['custom_fields']
        additional_info = mocked_kwargs['additional_info']

        private_comment = '\n'.join(list(additional_info.values()))
        for info in (country, username, legal_name, course):
            assert info in private_comment

        assert additional_info['Paying for the course would cause economic hardship'] == 'No'
        assert additional_info['Certify work diligently to receive a certificate'] == 'No'
        assert additional_info['Certify abide by the honor code'] == 'No'

        assert ticket_subject == f'Financial assistance request for learner {username} in course {self.course.display_name}'  # pylint: disable=line-too-long
        self.assertEqual([{'id': 'custom_123', 'value': course}], custom_fields)
        assert 'Client IP' in additional_info
        assert group_name == 'Financial Assistance'

    @patch.object(views, 'create_zendesk_ticket', return_value=500)
    def test_zendesk_submission_failed(self, _mock_create_zendesk_ticket):
        response = self._submit_financial_assistance_form({
            'username': self.user.username,
            'course': str(self.course.id),
            'name': '',
            'email': '',
            'country': '',
            'certify-economic-hardship': False,
            'certify-complete-certificate': False,
            'certify-honor-code': False,
        })
        assert response.status_code == 500

    @patch.object(
        views, 'create_financial_assistance_application', return_value=HttpResponse(status=status.HTTP_204_NO_CONTENT)
    )
    @ddt.data(
        ('/financial-assistance/course-v1:test+TestX+Test_Course/apply/', status.HTTP_204_NO_CONTENT),
        ('/financial-assistance/course-v1:test+TestX+Test_Course/apply/', status.HTTP_403_FORBIDDEN),
        ('/financial-assistance/course-v1:invalid+ErrorX+Invalid_Course/apply/', status.HTTP_400_BAD_REQUEST)
    )
    @ddt.unpack
    def test_submit_financial_assistance_request_v2(self, referrer_url, expected_status, *args):
        # We expect a 403 if the user account is not active
        if expected_status == status.HTTP_403_FORBIDDEN:
            self.user.is_active = False
            self.user.save()
        form_data = {
            'username': self.user.username,
            'course': 'course-v1:test+TestX+Test_Course',
            'certify-economic-hardship': False,
            'certify-complete-certificate': False,
            'certify-honor-code': False,
        }
        response = self._submit_financial_assistance_form(
            form_data,
            submit_url='submit_financial_assistance_request_v2',
            referrer_url=referrer_url
        )
        assert response.status_code == expected_status

    @ddt.data(
        ({}, 400),
        ({'username': 'wwhite'}, 403),
        ({'username': 'dummy', 'course': 'bad course ID'}, 400)
    )
    @ddt.unpack
    def test_submit_financial_assistance_errors(self, data, response_status):
        response = self._submit_financial_assistance_form(data)
        assert response.status_code == response_status

    def test_financial_assistance_login_required(self):
        for url in (
                reverse('financial_assistance'),
                reverse('financial_assistance_form'),
                reverse('submit_financial_assistance_request')
        ):
            self.client.logout()
            response = self.client.get(url)
            self.assertRedirects(response, reverse('signin_user') + '?next=' + url)


# Patching 'lms.djangoapps.courseware.views.views.get_programs' would be ideal,
# but for some unknown reason that patch doesn't seem to be applied.
@patch('openedx.core.djangoapps.catalog.utils.cache')
class TestProgramMarketingView(SharedModuleStoreTestCase):
    """Unit tests for the program marketing page."""
    program_uuid = str(uuid4())
    url = reverse_lazy('program_marketing_view', kwargs={'program_uuid': program_uuid})

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        modulestore_course = CourseFactory()
        course_run = CourseRunFactory(key=str(modulestore_course.id))  # lint-amnesty, pylint: disable=no-member
        course = CatalogCourseFactory(course_runs=[course_run])

        cls.data = ProgramFactory(
            courses=[course],
            is_program_eligible_for_one_click_purchase=False,
            uuid=cls.program_uuid,
        )

    def test_404_if_no_data(self, mock_cache):
        """
        Verify that the page 404s if no program data is found.
        """
        mock_cache.get.return_value = None

        response = self.client.get(self.url)
        assert response.status_code == 404

    def test_200(self, mock_cache):
        """
        Verify the view returns a 200.
        """
        mock_cache.get.return_value = self.data

        response = self.client.get(self.url)
        assert response.status_code == 200


# setting TIME_ZONE_DISPLAYED_FOR_DEADLINES explicitly
@override_settings(TIME_ZONE_DISPLAYED_FOR_DEADLINES="UTC")
class BaseDueDateTests(ModuleStoreTestCase):
    """
    Base class that verifies that due dates are rendered correctly on a page
    """
    __test__ = False

    def get_response(self, course):
        """Return the rendered text for the page to be verified"""
        raise NotImplementedError

    def set_up_course(self, **course_kwargs):
        """
        Create a stock course with a specific due date.
        :param course_kwargs: All kwargs are passed to through to the :class:`CourseFactory`
        """
        course = CourseFactory.create(**course_kwargs)
        with self.store.bulk_operations(course.id):
            chapter = BlockFactory.create(category='chapter', parent_location=course.location)
            section = BlockFactory.create(
                category='sequential',
                parent_location=chapter.location,
                due=datetime(2013, 9, 18, 11, 30, 00),
                format='homework'
            )
            vertical = BlockFactory.create(category='vertical', parent_location=section.location)
            BlockFactory.create(category='problem', parent_location=vertical.location)

        course = modulestore().get_course(course.id)
        assert course.get_children()[0].get_children()[0].due is not None
        CourseEnrollmentFactory(user=self.user, course_id=course.id)
        CourseOverview.load_from_module_store(course.id)
        return course

    def setUp(self):
        super().setUp()
        assert self.client.login(username=self.user.username, password=self.user_password)

        self.time_with_tz = "2013-09-18 11:30:00+00:00"

    def test_backwards_compatibility(self):
        # The test course being used has show_timezone = False in the policy file
        # (and no due_date_display_format set). This is to test our backwards compatibility--
        # in course_block's init method, the date_display_format will be set accordingly to
        # remove the timezone.
        course = self.set_up_course(due_date_display_format=None, show_timezone=False)
        response = self.get_response(course)
        self.assertContains(response, self.time_with_tz)
        # Test that show_timezone has been cleared (which means you get the default value of True).
        assert course.show_timezone

    def test_defaults(self):
        course = self.set_up_course()
        response = self.get_response(course)
        self.assertContains(response, self.time_with_tz)

    def test_format_none(self):
        # Same for setting the due date to None
        course = self.set_up_course(due_date_display_format=None)
        response = self.get_response(course)
        self.assertContains(response, self.time_with_tz)

    def test_format_date(self):
        # due date with no time
        course = self.set_up_course(due_date_display_format="%b %d %y")
        response = self.get_response(course)
        self.assertContains(response, self.time_with_tz)

    def test_format_invalid(self):
        # improperly formatted due_date_display_format falls through to default
        # (value of show_timezone does not matter-- setting to False to make that clear).
        course = self.set_up_course(due_date_display_format="%%%", show_timezone=False)
        response = self.get_response(course)
        self.assertNotContains(response, "%%%")
        self.assertContains(response, self.time_with_tz)


class TestProgressDueDate(BaseDueDateTests):
    """
    Test that the progress page displays due dates correctly
    """
    __test__ = True

    def get_response(self, course):
        """ Returns the HTML for the progress page """
        return self.client.get(reverse('progress', args=[str(course.id)]))


class StartDateTests(ModuleStoreTestCase):
    """
    Test that start dates are properly localized and displayed on the student
    dashboard.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()

    def set_up_course(self):
        """
        Create a stock course with a specific due date.
        :param course_kwargs: All kwargs are passed to through to the :class:`CourseFactory`
        """
        course = CourseFactory.create(start=datetime(2013, 9, 16, 7, 17, 28))
        course = modulestore().get_course(course.id)
        return course

    def get_about_response(self, course_key):
        """
        Get the text of the /about page for the course.
        """
        return self.client.get(reverse('about_course', args=[str(course_key)]))

    @patch('common.djangoapps.util.date_utils.pgettext', fake_pgettext(translations={
        ("abbreviated month name", "Sep"): "SEPTEMBER",
    }))
    @patch('common.djangoapps.util.date_utils.gettext', fake_ugettext(translations={
        "SHORT_DATE_FORMAT": "%Y-%b-%d",
    }))
    def test_format_localized_in_studio_course(self):
        course = self.set_up_course()
        response = self.get_about_response(course.id)
        # The start date is set in the set_up_course function above.
        # This should return in the format '%Y-%m-%dT%H:%M:%S%z'
        self.assertContains(response, "2013-09-16T07:17:28+0000")


class ProgressPageBaseTests(ModuleStoreTestCase):
    """
    Base class for progress page tests.
    """

    ENABLED_CACHES = ['default', 'mongo_modulestore_inheritance', 'loc_cache']
    ENABLED_SIGNALS = ['course_published']

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        assert self.client.login(username=self.user.username, password=TEST_PASSWORD)

        self.setup_course()

    def create_course(self, **options):
        """Create the test course."""
        self.course = CourseFactory.create(  # pylint: disable=attribute-defined-outside-init
            start=datetime(2013, 9, 16, 7, 17, 28),
            end=datetime.now(),
            certificate_available_date=datetime.now(UTC),
            certificates_display_behavior=CertificatesDisplayBehaviors.END_WITH_DATE,
            **options,
        )

    def setup_course(self, **course_options):
        """Create the test course and content, and enroll the user."""
        self.create_course(**course_options, grading_policy={'GRADE_CUTOFFS': {'çü†øƒƒ': 0.75, 'Pass': 0.5}})
        with self.store.bulk_operations(self.course.id):
            self.chapter = BlockFactory.create(category='chapter', parent_location=self.course.location)
            self.section = BlockFactory.create(category='sequential', parent_location=self.chapter.location)
            self.vertical = BlockFactory.create(category='vertical', parent_location=self.section.location)

        CourseEnrollmentFactory(user=self.user, course_id=self.course.id, mode=CourseMode.HONOR)

    def _get_progress_page(self, expected_status_code=200):
        """
        Gets the progress page for the currently logged-in user.
        """
        resp = self.client.get(
            reverse('progress', args=[str(self.course.id)])
        )
        assert resp.status_code == expected_status_code
        return resp

    def _get_student_progress_page(self, expected_status_code=200):
        """
        Gets the progress page for the user in the course.
        """
        resp = self.client.get(
            reverse('student_progress', args=[str(self.course.id), self.user.id])
        )
        assert resp.status_code == expected_status_code
        return resp


# pylint: disable=protected-access
@patch('lms.djangoapps.certificates.api.get_active_web_certificate', PropertyMock(return_value=True))
@ddt.ddt
class ProgressPageTests(ProgressPageBaseTests):
    """
    Tests that verify that the progress page works correctly.
    """
    @ddt.data('"><script>alert(1)</script>', '<script>alert(1)</script>', '</script><script>alert(1)</script>')
    def test_progress_page_xss_prevent(self, malicious_code):
        """
        Test that XSS attack is prevented
        """
        resp = self._get_student_progress_page()
        # Test that malicious code does not appear in html
        self.assertNotContains(resp, malicious_code)

    def test_pure_ungraded_xblock(self):
        BlockFactory.create(category='acid', parent_location=self.vertical.location)
        self._get_progress_page()

    def test_student_progress_with_valid_and_invalid_id(self):
        """
         Check that invalid 'student_id' raises Http404.
        """

        # Create new course
        # Enroll student into course
        self.course = CourseFactory.create()  # lint-amnesty, pylint: disable=attribute-defined-outside-init
        CourseEnrollmentFactory(user=self.user, course_id=self.course.id, mode=CourseMode.HONOR)

        # Invalid Student Ids (Integer and Non-int)
        invalid_student_ids = [
            991021,
            'azU3N_8$',
        ]
        for invalid_id in invalid_student_ids:

            resp = self.client.get(
                reverse('student_progress', args=[str(self.course.id), invalid_id])
            )
            assert resp.status_code == 404

        # Assert that valid 'student_id' returns 200 status
        self._get_student_progress_page()

    def test_unenrolled_student_progress_for_credit_course(self):
        """
         Test that student progress page does not break while checking for an unenrolled student.
         Scenario: When instructor checks the progress of a student who is not enrolled in credit course.
         It should return 200 response.
        """
        # Create a new course, a user which will not be enrolled in course, admin user for staff access
        course = CourseFactory.create(default_store=ModuleStoreEnum.Type.split)
        admin = AdminFactory.create()
        assert self.client.login(username=admin.username, password=TEST_PASSWORD)

        # Create and enable Credit course
        CreditCourse.objects.create(course_key=course.id, enabled=True)

        # Configure a credit provider for the course
        CreditProvider.objects.create(
            provider_id="ASU",
            enable_integration=True,
            provider_url="https://credit.example.com/request"
        )

        requirements = [{
            "namespace": "grade",
            "name": "grade",
            "display_name": "Grade",
            "criteria": {"min_grade": 0.52},
        }]
        # Add a single credit requirement (final grade)
        set_credit_requirements(course.id, requirements)

        self._get_student_progress_page()

    def test_non_ascii_grade_cutoffs(self):
        self._get_progress_page()

    def test_generate_cert_config(self):

        resp = self._get_progress_page()
        self.assertNotContains(resp, 'Request Certificate')

        # Enable the feature, but do not enable it for this course
        certs_api.set_certificate_generation_config(enabled=True)

        resp = self._get_progress_page()
        self.assertNotContains(resp, 'Request Certificate')

        # Enable certificate generation for this course
        certs_api.set_cert_generation_enabled(self.course.id, True)

        resp = self._get_progress_page()
        self.assertNotContains(resp, 'Request Certificate')

    @patch.dict('django.conf.settings.FEATURES', {'CERTIFICATES_HTML_VIEW': True})
    def test_view_certificate_for_unverified_student(self):
        """
        If user has already generated a certificate, it should be visible in case of user being
        unverified too.
        """
        GeneratedCertificateFactory.create(
            user=self.user,
            course_id=self.course.id,
            status=CertificateStatuses.downloadable,
            mode='verified'
        )

        # Enable the feature, but do not enable it for this course
        certs_api.set_certificate_generation_config(enabled=True)

        # Enable certificate generation for this course
        certs_api.set_cert_generation_enabled(self.course.id, True)
        CourseEnrollment.enroll(self.user, self.course.id, mode="verified")

        # Check that the user is unverified
        assert not IDVerificationService.user_is_verified(self.user)
        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_create:
            course_grade = mock_create.return_value
            course_grade.passed = True
            course_grade.summary = {'grade': 'Pass', 'percent': 0.75, 'section_breakdown': [],
                                    'grade_breakdown': {}}
            resp = self._get_progress_page()
            self.assertNotContains(resp, "Certificate unavailable")
            self.assertContains(resp, "Your certificate is available")

    @patch.dict('django.conf.settings.FEATURES', {'CERTIFICATES_HTML_VIEW': True})
    def test_view_certificate_link(self):
        """
        If certificate web view is enabled then certificate web view button should appear for user who certificate is
        available/generated
        """
        certificate = GeneratedCertificateFactory.create(
            user=self.user,
            course_id=self.course.id,
            status=CertificateStatuses.downloadable,
            mode='honor'
        )

        # Enable the feature, but do not enable it for this course
        certs_api.set_certificate_generation_config(enabled=True)

        # Enable certificate generation for this course
        certs_api.set_cert_generation_enabled(self.course.id, True)

        # Course certificate configurations
        certificates = [
            {
                'id': 1,
                'name': 'Name 1',
                'description': 'Description 1',
                'course_title': 'course_title_1',
                'signatories': [],
                'version': 1,
                'is_active': True
            }
        ]

        self.course.certificates = {'certificates': certificates}
        self.course.cert_html_view_enabled = True
        self.course.save()
        self.update_course(self.course, self.user.id)

        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_create:
            course_grade = mock_create.return_value
            course_grade.passed = True
            course_grade.summary = {'grade': 'Pass', 'percent': 0.75, 'section_breakdown': [], 'grade_breakdown': {}}

            resp = self._get_progress_page()

            self.assertContains(resp, "View Certificate")

            self.assertContains(resp, "earned a certificate for this course")
            cert_url = certs_api.get_certificate_url(course_id=self.course.id, uuid=certificate.verify_uuid)
            self.assertContains(resp, cert_url)

            # when course certificate is not active
            certificates[0]['is_active'] = False
            self.update_course(self.course, self.user.id)

            resp = self._get_progress_page()
            self.assertNotContains(resp, "View my Certificate")
            self.assertNotContains(resp, "You can now View my Certificate")
            self.assertContains(resp, "Your certificate is available")
            self.assertContains(resp, "earned a certificate for this course.")

    @ddt.data(
        (True, 54),
        (False, 54),
    )
    @ddt.unpack
    def test_progress_queries_paced_courses(self, self_paced, query_count):
        """Test that query counts remain the same for self-paced and instructor-paced courses."""
        # TODO: decrease query count as part of REVO-28
        ContentTypeGatingConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1))
        self.setup_course(self_paced=self_paced)
        with self.assertNumQueries(query_count, table_ignorelist=QUERY_COUNT_TABLE_IGNORELIST), check_mongo_calls(2):
            self._get_progress_page()

    def test_progress_queries(self):
        ContentTypeGatingConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1))
        self.setup_course()
        with self.assertNumQueries(
            54, table_ignorelist=QUERY_COUNT_TABLE_IGNORELIST
        ), check_mongo_calls(2):
            self._get_progress_page()

        for _ in range(2):
            with self.assertNumQueries(
                39, table_ignorelist=QUERY_COUNT_TABLE_IGNORELIST
            ), check_mongo_calls(2):
                self._get_progress_page()

    @patch.dict(settings.FEATURES, {'ENABLE_CERTIFICATES_IDV_REQUIREMENT': True})
    @ddt.data(
        *itertools.product(
            (
                CourseMode.AUDIT,
                CourseMode.HONOR,
                CourseMode.VERIFIED,
                CourseMode.PROFESSIONAL,
                CourseMode.NO_ID_PROFESSIONAL_MODE,
                CourseMode.CREDIT_MODE
            ),
            (True, False)
        )
    )
    @ddt.unpack
    def test_show_certificate_request_button(self, course_mode, user_verified):
        """Verify that the Request Certificate is not displayed in audit mode."""
        certs_api.set_certificate_generation_config(enabled=True)
        certs_api.set_cert_generation_enabled(self.course.id, True)
        CourseEnrollment.enroll(self.user, self.course.id, mode=course_mode)
        with patch(
            'lms.djangoapps.verify_student.services.IDVerificationService.user_is_verified'
        ) as user_verify:
            user_verify.return_value = user_verified
            with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_create:
                course_grade = mock_create.return_value
                course_grade.passed = True
                course_grade.summary = {
                    'grade': 'Pass', 'percent': 0.75, 'section_breakdown': [], 'grade_breakdown': {}
                }

                resp = self._get_progress_page()

                cert_button_hidden = course_mode is CourseMode.AUDIT or \
                    course_mode in CourseMode.VERIFIED_MODES and not user_verified

                assert cert_button_hidden == ('Request Certificate' not in resp.content.decode('utf-8'))

    @patch.dict('django.conf.settings.FEATURES', {'CERTIFICATES_HTML_VIEW': True})
    def test_page_with_invalidated_certificate_with_html_view(self):
        """
        Verify that for html certs if certificate is marked as invalidated than
        re-generate button should not appear on progress page.
        """
        generated_certificate = self.generate_certificate("honor")

        # Course certificate configurations
        certificates = [
            {
                'id': 1,
                'name': 'dummy',
                'description': 'dummy description',
                'course_title': 'dummy title',
                'signatories': [],
                'version': 1,
                'is_active': True
            }
        ]
        self.course.certificates = {'certificates': certificates}
        self.course.cert_html_view_enabled = True
        self.course.save()
        self.update_course(self.course, self.user.id)

        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_create:
            course_grade = mock_create.return_value
            course_grade.passed = True
            course_grade.summary = {
                'grade': 'Pass', 'percent': 0.75, 'section_breakdown': [], 'grade_breakdown': {}
            }

            resp = self._get_progress_page()
            self.assertContains(resp, "View Certificate")
            self.assert_invalidate_certificate(generated_certificate)

    @patch.dict('django.conf.settings.FEATURES', {'CERTIFICATES_HTML_VIEW': True})
    def test_page_with_allowlisted_certificate_with_html_view(self):
        """
        Verify that view certificate appears for an allowlisted user
        """
        generated_certificate = self.generate_certificate("honor")

        # Course certificate configurations
        certificates = [
            {
                'id': 1,
                'name': 'dummy',
                'description': 'dummy description',
                'course_title': 'dummy title',
                'signatories': [],
                'version': 1,
                'is_active': True
            }
        ]
        self.course.certificates = {'certificates': certificates}
        self.course.cert_html_view_enabled = True
        self.course.save()
        self.update_course(self.course, self.user.id)
        CertificateAllowlistFactory.create(
            user=self.user,
            course_id=self.course.id
        )

        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_create:
            course_grade = mock_create.return_value
            course_grade.passed = False
            course_grade.summary = {
                'grade': 'Fail', 'percent': 0.75, 'section_breakdown': [], 'grade_breakdown': {}
            }

            resp = self._get_progress_page()
            self.assertContains(resp, "View Certificate")
            self.assert_invalidate_certificate(generated_certificate)

    @ddt.data(
        *itertools.product(
            (
                CourseMode.AUDIT,
                CourseMode.HONOR,
                CourseMode.VERIFIED,
                CourseMode.PROFESSIONAL,
                CourseMode.NO_ID_PROFESSIONAL_MODE,
                CourseMode.CREDIT_MODE
            )
        )
    )
    @ddt.unpack
    def test_progress_with_course_duration_limits(self, course_mode):
        """
        Verify that expired banner message appears on progress page, if learner is enrolled
        in audit mode.
        """
        CourseDurationLimitConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1))
        user = UserFactory.create()
        assert self.client.login(username=user.username, password=TEST_PASSWORD)
        add_course_mode(self.course, mode_slug=CourseMode.AUDIT)
        add_course_mode(self.course)
        CourseEnrollmentFactory(user=user, course_id=self.course.id, mode=course_mode)

        response = self._get_progress_page()
        bannerText = get_expiration_banner_text(user, self.course)

        if course_mode == CourseMode.AUDIT:
            self.assertContains(response, bannerText, html=True)
        else:
            self.assertNotContains(response, bannerText, html=True)

    @ddt.data(
        *itertools.product(
            (
                CourseMode.AUDIT,
                CourseMode.HONOR,
                CourseMode.VERIFIED,
                CourseMode.PROFESSIONAL,
                CourseMode.NO_ID_PROFESSIONAL_MODE,
                CourseMode.CREDIT_MODE
            )
        )
    )
    @ddt.unpack
    def test_progress_without_course_duration_limits(self, course_mode):
        """
        Verify that expired banner message never appears on progress page, regardless
        of course_mode
        """
        CourseDurationLimitConfig.objects.create(enabled=False)
        user = UserFactory.create()
        assert self.client.login(username=user.username, password=TEST_PASSWORD)
        CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug=course_mode
        )
        CourseEnrollmentFactory(user=user, course_id=self.course.id, mode=course_mode)

        response = self._get_progress_page()
        bannerText = get_expiration_banner_text(user, self.course)
        self.assertNotContains(response, bannerText, html=True)

    @patch('lms.djangoapps.courseware.views.views.is_course_passed', PropertyMock(return_value=True))
    @override_settings(FEATURES=FEATURES_WITH_DISABLE_HONOR_CERTIFICATE)
    @ddt.data(CourseMode.AUDIT, CourseMode.HONOR)
    def test_message_for_ineligible_mode(self, course_mode):
        """ Verify that message appears on progress page, if learner is enrolled
         in an ineligible mode.
        """
        user = UserFactory.create()
        assert self.client.login(username=user.username, password=TEST_PASSWORD)
        CourseEnrollmentFactory(user=user, course_id=self.course.id, mode=course_mode)

        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_create:
            course_grade = mock_create.return_value
            course_grade.passed = True
            course_grade.summary = {'grade': 'Pass', 'percent': 0.75, 'section_breakdown': [], 'grade_breakdown': {}}

            response = self._get_progress_page()

            expected_message = ('You are enrolled in the {mode} track for this course. '
                                'The {mode} track does not include a certificate.').format(mode=course_mode)
            self.assertContains(response, expected_message)

    def test_invalidated_cert_data(self):
        """
        Verify that invalidated cert data is returned if cert is invalidated.
        """
        generated_certificate = self.generate_certificate("honor")

        CertificateInvalidationFactory.create(
            generated_certificate=generated_certificate,
            invalidated_by=self.user
        )
        # Invalidate user certificate
        generated_certificate.invalidate()
        response = views.get_cert_data(self.user, self.course, CourseMode.HONOR, MagicMock(passed=True))
        assert response.cert_status == 'invalidated'
        assert response.title == 'Your certificate has been invalidated'

    @override_settings(FEATURES=FEATURES_WITH_DISABLE_HONOR_CERTIFICATE)
    def test_downloadable_get_cert_data(self):
        """
        Verify that downloadable cert data is returned if cert is downloadable even
        when DISABLE_HONOR_CERTIFICATES feature flag is turned ON.
        """
        self.generate_certificate("honor")
        response = views.get_cert_data(
            self.user, self.course, CourseMode.HONOR, MagicMock(passed=True)
        )

        assert response.cert_status == 'downloadable'
        assert response.title == 'Your certificate is available'

    def test_generating_get_cert_data(self):
        """
        Verify that generating cert data is returned if cert is generating.
        """
        self.generate_certificate("honor")
        with patch('lms.djangoapps.certificates.api.certificate_downloadable_status',
                   return_value=self.mock_certificate_downloadable_status(is_generating=True)):
            response = views.get_cert_data(self.user, self.course, CourseMode.HONOR, MagicMock(passed=True))

        assert response.cert_status == 'generating'
        assert response.title == "We're working on it..."

    def test_unverified_get_cert_data(self):
        """
        Verify that unverified cert data is returned if cert is unverified.
        """
        self.generate_certificate("honor")
        with patch('lms.djangoapps.certificates.api.certificate_downloadable_status',
                   return_value=self.mock_certificate_downloadable_status(is_unverified=True)):
            response = views.get_cert_data(self.user, self.course, CourseMode.HONOR, MagicMock(passed=True))

        assert response.cert_status == 'unverified'
        assert response.title == 'Certificate unavailable'

    def test_request_get_cert_data(self):
        """
        Verify that requested cert data is returned if cert is to be requested.
        """
        self.generate_certificate("honor")
        with patch('lms.djangoapps.certificates.api.certificate_downloadable_status',
                   return_value=self.mock_certificate_downloadable_status()):
            response = views.get_cert_data(self.user, self.course, CourseMode.HONOR, MagicMock(passed=True))

        assert response.cert_status == 'requesting'
        assert response.title == 'Congratulations, you qualified for a certificate!'

    def test_earned_but_not_available_get_cert_data(self):
        """
        Verify that earned but not available cert data is returned if cert has been earned, but isn't available.
        """
        self.generate_certificate("verified")
        with patch('lms.djangoapps.certificates.api.certificate_downloadable_status',
                   return_value=self.mock_certificate_downloadable_status(earned_but_not_available=True)):
            response = views.get_cert_data(self.user, self.course, CourseMode.VERIFIED, MagicMock(passed=True))

        assert response.cert_status == 'earned_but_not_available'
        assert response.title == 'Your certificate will be available soon!'

    @ddt.data(True, False)
    def test_no_certs_generated_and_not_verified(self, enable_cert_idv_requirement):
        """
        Verify if the learner is not ID Verified, and the certs are not yet generated,
        but the learner is eligible, the get_cert_data would return cert status Unverified
        """
        certs_api.set_certificate_generation_config(enabled=True)
        certs_api.set_cert_generation_enabled(self.course.id, True)
        with patch.dict(settings.FEATURES, ENABLE_CERTIFICATES_IDV_REQUIREMENT=enable_cert_idv_requirement):
            with patch(
                'lms.djangoapps.certificates.api.certificate_downloadable_status',
                return_value=self.mock_certificate_downloadable_status()
            ):
                response = views.get_cert_data(self.user, self.course, CourseMode.VERIFIED, MagicMock(passed=True))

        if not enable_cert_idv_requirement:
            assert response.cert_status == 'requesting'
            assert response.title == 'Congratulations, you qualified for a certificate!'
        else:
            assert response.cert_status == 'unverified'
            assert response.title == 'Certificate unavailable'

    def assert_invalidate_certificate(self, certificate):
        """ Dry method to mark certificate as invalid. And assert the response. """
        CertificateInvalidationFactory.create(
            generated_certificate=certificate,
            invalidated_by=self.user
        )
        # Invalidate user certificate
        certificate.invalidate()
        resp = self._get_progress_page()

        self.assertNotContains(resp, 'Request Certificate')
        self.assertContains(resp, 'Your certificate has been invalidated')
        self.assertContains(resp, 'Please contact your course team if you have any questions.')
        self.assertNotContains(resp, 'View my Certificate')

    def generate_certificate(self, mode):
        """ Dry method to generate certificate. """

        generated_certificate = GeneratedCertificateFactory.create(
            user=self.user,
            course_id=self.course.id,
            status=CertificateStatuses.downloadable,
            mode=mode
        )
        certs_api.set_certificate_generation_config(enabled=True)
        certs_api.set_cert_generation_enabled(self.course.id, True)
        return generated_certificate

    def mock_certificate_downloadable_status(
            self, is_downloadable=False, is_generating=False, is_unverified=False, uuid=None,
            earned_but_not_available=None,
    ):
        """Dry method to mock certificate downloadable status response."""
        return {
            'is_downloadable': is_downloadable,
            'is_generating': is_generating,
            'is_unverified': is_unverified,
            'uuid': uuid,
            'earned_but_not_available': earned_but_not_available,
        }


@ddt.ddt
class ProgressPageShowCorrectnessTests(ProgressPageBaseTests):
    """
    Tests that verify that the progress page works correctly when displaying subsections where correctness is hidden.
    """
    # Constants used in the test data
    NOW = datetime.now(UTC)
    DAY_DELTA = timedelta(days=1)
    YESTERDAY = 'yesterday'
    TODAY = 'today'
    TOMORROW = 'tomorrow'
    GRADER_TYPE = 'Homework'
    DATES = {
        YESTERDAY: NOW - DAY_DELTA,
        TODAY: NOW,
        TOMORROW: NOW + DAY_DELTA,
        None: None,
    }

    def setUp(self):
        super().setUp()
        self.staff_user = UserFactory.create(is_staff=True)

    def setup_course(self, show_correctness='', due_date=None, graded=False, **course_options):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Set up course with a subsection with the given show_correctness, due_date, and graded settings.
        """
        # Use a simple grading policy
        course_options['grading_policy'] = {
            "GRADER": [{
                "type": self.GRADER_TYPE,
                "min_count": 2,
                "drop_count": 0,
                "short_label": "HW",
                "weight": 1.0
            }],
            "GRADE_CUTOFFS": {
                'A': .9,
                'B': .33
            }
        }
        self.create_course(**course_options)

        metadata = dict(
            show_correctness=show_correctness,
        )
        if due_date is not None:
            metadata['due'] = due_date
        if graded:
            metadata['graded'] = True
            metadata['format'] = self.GRADER_TYPE

        with self.store.bulk_operations(self.course.id):
            self.chapter = BlockFactory.create(category='chapter', parent_location=self.course.location,
                                               display_name="Section 1")
            self.section = BlockFactory.create(category='sequential', parent_location=self.chapter.location,
                                               display_name="Subsection 1", metadata=metadata)
            self.vertical = BlockFactory.create(category='vertical', parent_location=self.section.location)

        CourseEnrollmentFactory(user=self.user, course_id=self.course.id, mode=CourseMode.HONOR)

    def add_problem(self):
        """
        Add a problem to the subsection
        """
        problem_xml = MultipleChoiceResponseXMLFactory().build_xml(
            question_text='The correct answer is Choice 1',
            choices=[True, False],
            choice_names=['choice_0', 'choice_1']
        )
        self.problem = BlockFactory.create(category='problem', parent_location=self.vertical.location,  # lint-amnesty, pylint: disable=attribute-defined-outside-init
                                           data=problem_xml, display_name='Problem 1')
        # Re-fetch the course from the database
        self.course = self.store.get_course(self.course.id)  # lint-amnesty, pylint: disable=attribute-defined-outside-init

    def answer_problem(self, value=1, max_value=1):
        """
        Submit the given score to the problem on behalf of the user
        """
        # Get the block for the problem, as viewed by the user
        field_data_cache = FieldDataCache.cache_for_block_descendents(
            self.course.id,
            self.user,
            self.course,
            depth=2
        )
        self.addCleanup(set_current_request, None)
        block = get_block(
            self.user,
            get_mock_request(self.user),
            self.problem.scope_ids.usage_id,
            field_data_cache
        )

        # Submit the given score/max_score to the problem xmodule
        grade_dict = {'value': value, 'max_value': max_value, 'user_id': self.user.id}
        block.runtime.publish(self.problem, 'grade', grade_dict)

    def assert_progress_page_show_grades(self, response, show_correctness, due_date, graded,
                                         show_grades, score, max_score, avg):  # lint-amnesty, pylint: disable=unused-argument
        """
        Ensures that grades and scores are shown or not shown on the progress page as required.
        """

        expected_score = f"<dd>{score}/{max_score}</dd>"
        percent = score / float(max_score)

        # Test individual problem scores
        if show_grades:
            # If grades are shown, we should be able to see the current problem scores.
            self.assertContains(response, expected_score)

            if graded:
                expected_summary_text = "Problem Scores:"
            else:
                expected_summary_text = "Practice Scores:"

        else:
            # If grades are hidden, we should not be able to see the current problem scores.
            self.assertNotContains(response, expected_score)

            if graded:
                expected_summary_text = "Problem scores are hidden"
            else:
                expected_summary_text = "Practice scores are hidden"

            if show_correctness == ShowCorrectness.PAST_DUE and due_date:
                expected_summary_text += ' until the due date.'
            else:
                expected_summary_text += '.'

        # Ensure that expected text is present
        self.assertContains(response, expected_summary_text)

        # Test overall sequential score
        if graded and max_score > 0:
            percentageString = f"{percent:.0%}" if max_score > 0 else ""
            template = '<span> ({0:.3n}/{1:.3n}) {2}</span>'
            expected_grade_summary = template.format(float(score),
                                                     float(max_score),
                                                     percentageString)

            if show_grades:
                self.assertContains(response, expected_grade_summary)
            else:
                self.assertNotContains(response, expected_grade_summary)

    @ddt.data(
        ('', None, False),
        ('', None, True),
        (ShowCorrectness.ALWAYS, None, False),
        (ShowCorrectness.ALWAYS, None, True),
        (ShowCorrectness.ALWAYS, YESTERDAY, False),
        (ShowCorrectness.ALWAYS, YESTERDAY, True),
        (ShowCorrectness.ALWAYS, TODAY, False),
        (ShowCorrectness.ALWAYS, TODAY, True),
        (ShowCorrectness.ALWAYS, TOMORROW, False),
        (ShowCorrectness.ALWAYS, TOMORROW, True),
        (ShowCorrectness.NEVER, None, False),
        (ShowCorrectness.NEVER, None, True),
        (ShowCorrectness.NEVER, YESTERDAY, False),
        (ShowCorrectness.NEVER, YESTERDAY, True),
        (ShowCorrectness.NEVER, TODAY, False),
        (ShowCorrectness.NEVER, TODAY, True),
        (ShowCorrectness.NEVER, TOMORROW, False),
        (ShowCorrectness.NEVER, TOMORROW, True),
        (ShowCorrectness.PAST_DUE, None, False),
        (ShowCorrectness.PAST_DUE, None, True),
        (ShowCorrectness.PAST_DUE, YESTERDAY, False),
        (ShowCorrectness.PAST_DUE, YESTERDAY, True),
        (ShowCorrectness.PAST_DUE, TODAY, False),
        (ShowCorrectness.PAST_DUE, TODAY, True),
        (ShowCorrectness.PAST_DUE, TOMORROW, False),
        (ShowCorrectness.PAST_DUE, TOMORROW, True),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, None, False),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, None, True),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, YESTERDAY, False),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, YESTERDAY, True),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, TODAY, False),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, TODAY, True),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, TOMORROW, False),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, TOMORROW, True),
    )
    @ddt.unpack
    def test_progress_page_no_problem_scores(self, show_correctness, due_date_name, graded):
        """
        Test that "no problem scores are present" for a course with no problems,
        regardless of the various show correctness settings.
        """
        self.setup_course(show_correctness=show_correctness, due_date=self.DATES[due_date_name], graded=graded)
        resp = self._get_progress_page()

        # Test that no problem scores are present
        self.assertContains(resp, 'No problem scores in this section')

    @ddt.data(
        ('', None, False, True),
        ('', None, True, True),
        (ShowCorrectness.ALWAYS, None, False, True),
        (ShowCorrectness.ALWAYS, None, True, True),
        (ShowCorrectness.ALWAYS, YESTERDAY, False, True),
        (ShowCorrectness.ALWAYS, YESTERDAY, True, True),
        (ShowCorrectness.ALWAYS, TODAY, False, True),
        (ShowCorrectness.ALWAYS, TODAY, True, True),
        (ShowCorrectness.ALWAYS, TOMORROW, False, True),
        (ShowCorrectness.ALWAYS, TOMORROW, True, True),
        (ShowCorrectness.NEVER, None, False, False),
        (ShowCorrectness.NEVER, None, True, False),
        (ShowCorrectness.NEVER, YESTERDAY, False, False),
        (ShowCorrectness.NEVER, YESTERDAY, True, False),
        (ShowCorrectness.NEVER, TODAY, False, False),
        (ShowCorrectness.NEVER, TODAY, True, False),
        (ShowCorrectness.NEVER, TOMORROW, False, False),
        (ShowCorrectness.NEVER, TOMORROW, True, False),
        (ShowCorrectness.PAST_DUE, None, False, True),
        (ShowCorrectness.PAST_DUE, None, True, True),
        (ShowCorrectness.PAST_DUE, YESTERDAY, False, True),
        (ShowCorrectness.PAST_DUE, YESTERDAY, True, True),
        (ShowCorrectness.PAST_DUE, TODAY, False, True),
        (ShowCorrectness.PAST_DUE, TODAY, True, True),
        (ShowCorrectness.PAST_DUE, TOMORROW, False, False),
        (ShowCorrectness.PAST_DUE, TOMORROW, True, False),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, None, False, False),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, None, True, False),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, YESTERDAY, False, False),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, YESTERDAY, True, False),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, TODAY, False, False),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, TODAY, True, False),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, TOMORROW, False, False),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, TOMORROW, True, False),
    )
    @ddt.unpack
    def test_progress_page_hide_scores_from_learner(self, show_correctness, due_date_name, graded, show_grades):
        """
        Test that problem scores are hidden on progress page when correctness is not available to the learner, and that
        they are visible when it is.
        """
        due_date = self.DATES[due_date_name]
        self.setup_course(show_correctness=show_correctness, due_date=due_date, graded=graded)
        self.add_problem()

        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        resp = self._get_progress_page()

        # Ensure that expected text is present
        self.assert_progress_page_show_grades(resp, show_correctness, due_date, graded, show_grades, 0, 1, 0)

        # Submit answers to the problem, and re-fetch the progress page
        self.answer_problem()

        resp = self._get_progress_page()

        # Test that the expected text is still present.
        self.assert_progress_page_show_grades(resp, show_correctness, due_date, graded, show_grades, 1, 1, .5)

    @ddt.data(
        ('', None, False, True),
        ('', None, True, True),
        (ShowCorrectness.ALWAYS, None, False, True),
        (ShowCorrectness.ALWAYS, None, True, True),
        (ShowCorrectness.ALWAYS, YESTERDAY, False, True),
        (ShowCorrectness.ALWAYS, YESTERDAY, True, True),
        (ShowCorrectness.ALWAYS, TODAY, False, True),
        (ShowCorrectness.ALWAYS, TODAY, True, True),
        (ShowCorrectness.ALWAYS, TOMORROW, False, True),
        (ShowCorrectness.ALWAYS, TOMORROW, True, True),
        (ShowCorrectness.NEVER, None, False, False),
        (ShowCorrectness.NEVER, None, True, False),
        (ShowCorrectness.NEVER, YESTERDAY, False, False),
        (ShowCorrectness.NEVER, YESTERDAY, True, False),
        (ShowCorrectness.NEVER, TODAY, False, False),
        (ShowCorrectness.NEVER, TODAY, True, False),
        (ShowCorrectness.NEVER, TOMORROW, False, False),
        (ShowCorrectness.NEVER, TOMORROW, True, False),
        (ShowCorrectness.PAST_DUE, None, False, True),
        (ShowCorrectness.PAST_DUE, None, True, True),
        (ShowCorrectness.PAST_DUE, YESTERDAY, False, True),
        (ShowCorrectness.PAST_DUE, YESTERDAY, True, True),
        (ShowCorrectness.PAST_DUE, TODAY, False, True),
        (ShowCorrectness.PAST_DUE, TODAY, True, True),
        (ShowCorrectness.PAST_DUE, TOMORROW, False, True),
        (ShowCorrectness.PAST_DUE, TOMORROW, True, True),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, None, False, False),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, None, True, False),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, YESTERDAY, False, False),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, YESTERDAY, True, False),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, TODAY, False, False),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, TODAY, True, False),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, TOMORROW, False, False),
        (ShowCorrectness.NEVER_BUT_INCLUDE_GRADE, TOMORROW, True, False),
    )
    @ddt.unpack
    def test_progress_page_hide_scores_from_staff(self, show_correctness, due_date_name, graded, show_grades):
        """
        Test that problem scores are hidden from staff viewing a learner's progress page only if show_correctness is
        never or never_but_include_grade.
        """
        due_date = self.DATES[due_date_name]
        self.setup_course(show_correctness=show_correctness, due_date=due_date, graded=graded)
        self.add_problem()

        # Login as a course staff user to view the student progress page.
        self.client.login(username=self.staff_user.username, password=TEST_PASSWORD)

        resp = self._get_student_progress_page()

        # Ensure that expected text is present
        self.assert_progress_page_show_grades(resp, show_correctness, due_date, graded, show_grades, 0, 1, 0)

        # Submit answers to the problem, and re-fetch the progress page
        self.answer_problem()
        resp = self._get_student_progress_page()

        # Test that the expected text is still present.
        self.assert_progress_page_show_grades(resp, show_correctness, due_date, graded, show_grades, 1, 1, .5)


class VerifyCourseKeyDecoratorTests(TestCase):
    """
    Tests for the ensure_valid_course_key decorator.
    """

    def setUp(self):
        super().setUp()

        self.request = RequestFactoryNoCsrf().get("foo")
        self.valid_course_id = "edX/test/1"
        self.invalid_course_id = "edX/"

    def test_decorator_with_valid_course_id(self):
        mocked_view = create_autospec(views.course_about)
        view_function = ensure_valid_course_key(mocked_view)
        view_function(self.request, course_id=self.valid_course_id)
        assert mocked_view.called

    def test_decorator_with_invalid_course_id(self):
        mocked_view = create_autospec(views.course_about)
        view_function = ensure_valid_course_key(mocked_view)
        self.assertRaises(Http404, view_function, self.request, course_id=self.invalid_course_id)
        assert not mocked_view.called


class GenerateUserCertTests(ModuleStoreTestCase):
    """
    Tests for the view function Generated User Certs
    """

    def setUp(self):
        super().setUp()

        self.student = UserFactory()
        self.course = CourseFactory.create(
            org='edx',
            number='verified',
            end=datetime.now(),
            display_name='Verified Course',
            grading_policy={'GRADE_CUTOFFS': {'cutoff': 0.75, 'Pass': 0.5}},
            self_paced=True,
        )
        self.enrollment = CourseEnrollment.enroll(self.student, self.course.id, mode='honor')
        assert self.client.login(username=self.student, password=TEST_PASSWORD)
        self.url = reverse('generate_user_cert', kwargs={'course_id': str(self.course.id)})

    def test_user_with_out_passing_grades(self):
        # If user has no grading then json will return failed message and badrequest code
        resp = self.client.post(self.url)
        self.assertContains(
            resp,
            "Your certificate will be available when you pass the course.",
            status_code=HttpResponseBadRequest.status_code,
        )

    @patch('lms.djangoapps.courseware.views.views.is_course_passed', return_value=True)
    @override_settings(CERT_QUEUE='certificates')
    def test_user_with_passing_grade(self, mock_is_course_passed):  # lint-amnesty, pylint: disable=unused-argument
        # If user has above passing grading then json will return cert generating message and
        # status valid code
        with patch('xmodule.capa.xqueue_interface.XQueueInterface.send_to_queue') as mock_send_to_queue:
            mock_send_to_queue.return_value = (0, "Successfully queued")

            resp = self.client.post(self.url)
            assert resp.status_code == 200

    def test_user_with_passing_existing_generating_cert(self):
        # If user has passing grade but also has existing generating cert
        # then json will return cert generating message with bad request code
        GeneratedCertificateFactory.create(
            user=self.student,
            course_id=self.course.id,
            status=CertificateStatuses.generating,
            mode='verified'
        )
        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_create:
            course_grade = mock_create.return_value
            course_grade.passed = True
            course_grade.summary = {'grade': 'Pass', 'percent': 0.75}

            resp = self.client.post(self.url)
            self.assertContains(resp, "Certificate is being created.", status_code=HttpResponseBadRequest.status_code)

    @override_settings(CERT_QUEUE='certificates')
    def test_user_with_passing_existing_downloadable_cert(self):
        # If user has already downloadable certificate
        # then json will return cert generating message with bad request code

        GeneratedCertificateFactory.create(
            user=self.student,
            course_id=self.course.id,
            status=CertificateStatuses.downloadable,
            mode='verified'
        )

        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_create:
            course_grade = mock_create.return_value
            course_grade.passed = True
            course_grade.summay = {'grade': 'Pass', 'percent': 0.75}

            resp = self.client.post(self.url)
            self.assertContains(
                resp,
                "Certificate has already been created.",
                status_code=HttpResponseBadRequest.status_code,
            )

    def test_user_with_non_existing_course(self):
        # If try to access a course with valid key pattern then it will return
        # bad request code with course is not valid message
        resp = self.client.post('/courses/def/abc/in_valid/generate_user_cert')
        self.assertContains(resp, "Course is not valid", status_code=HttpResponseBadRequest.status_code)

    def test_user_with_invalid_course_id(self):
        # If try to access a course with invalid key pattern then 404 will return
        resp = self.client.post('/courses/def/generate_user_cert')
        assert resp.status_code == 404

    def test_user_without_login_return_error(self):
        # If user try to access without login should see a bad request status code with message
        self.client.logout()
        resp = self.client.post(self.url)
        self.assertContains(
            resp,
            "You must be signed in to {platform_name} to create a certificate.".format(
                platform_name=settings.PLATFORM_NAME
            ),
            status_code=HttpResponseBadRequest.status_code,
        )

    def test_certificates_with_passing_grade(self):
        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_read_grade:
            course_grade = mock_read_grade.return_value
            course_grade.passed = True

            with patch(
                'lms.djangoapps.certificates.api.generate_certificate_task',
                return_value=None
            ) as mock_cert_task:
                resp = self.client.post(self.url)
                mock_cert_task.assert_called_with(self.student, self.course.id, 'self')
                assert resp.status_code == 200

    def test_certificates_not_passing(self):
        """
        Test course certificates when the user is not passing the course
        """
        with patch(
            'lms.djangoapps.certificates.api.generate_certificate_task',
            return_value=None
        ) as mock_cert_task:
            resp = self.client.post(self.url)
            mock_cert_task.assert_called_with(self.student, self.course.id, 'self')
            self.assertContains(
                resp,
                "Your certificate will be available when you pass the course.",
                status_code=HttpResponseBadRequest.status_code,
            )

    def test_certificates_with_existing_downloadable_cert(self):
        """
        Test course certificates when the user is passing the course and already has a cert
        """
        GeneratedCertificateFactory.create(
            user=self.student,
            course_id=self.course.id,
            status=CertificateStatuses.downloadable,
            mode='verified'
        )

        with patch('lms.djangoapps.grades.course_grade_factory.CourseGradeFactory.read') as mock_read_grade:
            course_grade = mock_read_grade.return_value
            course_grade.passed = True

            with patch(
                'lms.djangoapps.certificates.api.generate_certificate_task',
                return_value=None
            ) as mock_cert_task:
                resp = self.client.post(self.url)
                mock_cert_task.assert_called_with(self.student, self.course.id, 'self')
                self.assertContains(
                    resp,
                    "Certificate has already been created.",
                    status_code=HttpResponseBadRequest.status_code,
                )


class ActivateIDCheckerBlock(XBlock):
    """
    XBlock for checking for an activate_block_id entry in the render context.
    """
    # We don't need actual children to test this.
    has_children = False

    def student_view(self, context):
        """
        A student view that displays the activate_block_id context variable.
        """
        result = Fragment()
        if 'activate_block_id' in context:
            result.add_content("Activate Block ID: {block_id}</p>".format(block_id=context['activate_block_id']))
        return result


class ViewCheckerBlock(XBlock):
    """
    XBlock for testing user state in views.
    """
    has_children = True
    state = String(scope=Scope.user_state)
    position = 0

    def student_view(self, context):  # pylint: disable=unused-argument
        """
        A student_view that asserts that the ``state`` field for this block
        matches the block's usage_id.
        """
        msg = f"{self.state} != {self.scope_ids.usage_id}"
        assert self.state == str(self.scope_ids.usage_id), msg
        fragments = self.runtime.render_children(self)
        result = Fragment(
            content="<p>ViewCheckerPassed: {}</p>\n{}".format(
                str(self.scope_ids.usage_id),
                "\n".join(fragment.content for fragment in fragments),
            )
        )
        return result


@ddt.ddt
class TestIndexView(ModuleStoreTestCase):
    """
    Tests of the courseware.views.index view.
    """

    @patch('lms.djangoapps.courseware.views.views.CourseTabView.course_open_for_learner_enrollment')
    @patch('openedx.core.djangoapps.util.user_messages.PageLevelMessages.register_warning_message')
    def test_courseware_messages_differentiate_for_anonymous_users(
            self, patch_register_warning_message, patch_course_open_for_learner_enrollment
    ):
        """
        Tests that the anonymous user case for the
        register_user_access_warning_messages returns different
        messaging based on the possibility of enrollment
        """
        course = CourseFactory()

        user = self.create_user_for_course(course, CourseUserType.ANONYMOUS)
        request = RequestFactory().get('/')
        request.user = user

        patch_course_open_for_learner_enrollment.return_value = False
        views.CourseTabView.register_user_access_warning_messages(request, course)
        open_for_enrollment_message = patch_register_warning_message.mock_calls[0][1][1]

        patch_register_warning_message.reset_mock()

        patch_course_open_for_learner_enrollment.return_value = True
        views.CourseTabView.register_user_access_warning_messages(request, course)
        closed_to_enrollment_message = patch_register_warning_message.mock_calls[0][1][1]

        assert open_for_enrollment_message != closed_to_enrollment_message

    @patch('openedx.core.djangoapps.util.user_messages.PageLevelMessages.register_warning_message')
    def test_courseware_messages_masters_only(self, patch_register_warning_message):
        with patch(
                'lms.djangoapps.courseware.views.views.CourseTabView.course_open_for_learner_enrollment'
        ) as patch_course_open_for_learner_enrollment:
            course = CourseFactory()

            user = self.create_user_for_course(course, CourseUserType.UNENROLLED)
            request = RequestFactory().get('/')
            request.user = user

            button_html = '<button class="enroll-btn btn-link">Enroll now</button>'

            patch_course_open_for_learner_enrollment.return_value = False
            views.CourseTabView.register_user_access_warning_messages(request, course)
            # pull message out of the calls to the mock so that
            # we can make finer grained assertions than mock provides
            message = patch_register_warning_message.mock_calls[0][1][1]
            assert button_html not in message

            patch_register_warning_message.reset_mock()

            patch_course_open_for_learner_enrollment.return_value = True
            views.CourseTabView.register_user_access_warning_messages(request, course)
            # pull message out of the calls to the mock so that
            # we can make finer grained assertions than mock provides
            message = patch_register_warning_message.mock_calls[0][1][1]
            assert button_html in message

    @ddt.data(
        [True, True, True, False, ],
        [False, True, True, False, ],
        [True, False, True, False, ],
        [True, True, False, False, ],
        [False, False, True, False, ],
        [True, False, False, True, ],
        [False, True, False, False, ],
        [False, False, False, False, ],
    )
    @ddt.unpack
    def test_should_show_enroll_button(self, course_open_for_self_enrollment,
                                       invitation_only, is_masters_only, expected_should_show_enroll_button):
        with patch('lms.djangoapps.courseware.views.views.course_open_for_self_enrollment') \
                as patch_course_open_for_self_enrollment, \
                patch('common.djangoapps.course_modes.models.CourseMode.is_masters_only') \
                as patch_is_masters_only:
            course = CourseFactory()

            patch_course_open_for_self_enrollment.return_value = course_open_for_self_enrollment
            patch_is_masters_only.return_value = is_masters_only
            course.invitation_only = invitation_only

            assert views.CourseTabView.course_open_for_learner_enrollment(course) == expected_should_show_enroll_button


@ddt.ddt
class TestRenderXBlock(RenderXBlockTestMixin, ModuleStoreTestCase, CompletionWaffleTestMixin):
    """
    Tests for the courseware.render_xblock endpoint.
    This class overrides the get_response method, which is used by
    the tests defined in RenderXBlockTestMixin.
    """

    def setUp(self):
        reload_django_url_config()
        super().setUp()

    def test_render_xblock_with_invalid_usage_key(self):
        """
        Test XBlockRendering with invalid usage key
        """
        response = self.get_response(usage_key='some_invalid_usage_key')
        self.assertContains(response, 'Page not found', status_code=404)

    def get_response(self, usage_key, url_encoded_params=None):
        """
        Overridable method to get the response from the endpoint that is being tested.
        """
        url = reverse('render_xblock', kwargs={'usage_key_string': str(usage_key)})
        if url_encoded_params:
            url += '?' + url_encoded_params
        return self.client.get(url)

    def test_render_xblock_with_completion_service_disabled(self):
        """
        Test that render_xblock does not set up the CompletionOnViewService.
        """
        self.setup_course(ModuleStoreEnum.Type.split)
        self.setup_user(admin=True, enroll=True, login=True)

        response = self.get_response(usage_key=self.html_block.location)
        assert response.status_code == 200
        self.assertContains(response, 'data-enable-completion-on-view-service="false"')
        self.assertNotContains(response, 'data-mark-completed-on-view-after-delay')

    def test_render_xblock_with_completion_service_enabled(self):
        """
        Test that render_xblock sets up the CompletionOnViewService for relevant xblocks.
        """
        self.override_waffle_switch(True)

        self.setup_course(ModuleStoreEnum.Type.split)
        self.setup_user(admin=False, enroll=True, login=True)

        response = self.get_response(usage_key=self.html_block.location)
        assert response.status_code == 200
        self.assertContains(response, 'data-enable-completion-on-view-service="true"')
        self.assertContains(response, 'data-mark-completed-on-view-after-delay')

        request = RequestFactoryNoCsrf().post(
            '/',
            data=json.dumps({"completion": 1}),
            content_type='application/json',
        )
        request.user = self.user
        response = handle_xblock_callback(
            request,
            str(self.course.id),
            quote_slashes(str(self.html_block.location)),
            'publish_completion',
        )
        assert response.status_code == 200
        assert json.loads(response.content.decode('utf-8')) == {'result': 'ok'}

        response = self.get_response(usage_key=self.html_block.location)
        assert response.status_code == 200
        self.assertContains(response, 'data-enable-completion-on-view-service="false"')
        self.assertNotContains(response, 'data-mark-completed-on-view-after-delay')

        response = self.get_response(usage_key=self.problem_block.location)
        assert response.status_code == 200
        self.assertContains(response, 'data-enable-completion-on-view-service="false"')
        self.assertNotContains(response, 'data-mark-completed-on-view-after-delay')

    def test_rendering_descendant_of_gated_sequence(self):
        """
        Test that we redirect instead of rendering what should be gated content,
        for things that are gated at the sequence level.
        """
        with self.store.default_store(ModuleStoreEnum.Type.split):
            # pylint:disable=attribute-defined-outside-init
            self.course = CourseFactory.create(**self.course_options())
            self.chapter = BlockFactory.create(parent=self.course, category='chapter')
            self.sequence = BlockFactory.create(
                parent=self.chapter,
                category='sequential',
                display_name='Sequence',
                is_time_limited=True,
            )
            self.vertical_block = BlockFactory.create(
                parent=self.sequence,
                category='vertical',
                display_name="Vertical",
            )
            self.html_block = BlockFactory.create(
                parent=self.vertical_block,
                category='html',
                data="<p>Test HTML Content<p>"
            )
            self.problem_block = BlockFactory.create(
                parent=self.vertical_block,
                category='problem',
                display_name='Problem'
            )
        CourseOverview.load_from_module_store(self.course.id)
        self.setup_user(admin=False, enroll=True, login=True)

        # Problem and Vertical response should both redirect to the Sequential
        # (where useful messaging would be).
        seq_url = reverse('render_xblock', kwargs={'usage_key_string': str(self.sequence.location)})
        for block in [self.problem_block, self.vertical_block]:
            response = self.get_response(usage_key=block.location)
            assert response.status_code == 302
            assert response.get('Location') == seq_url

        # The Sequence itself 200s (or we risk infinite redirect loops).
        assert self.get_response(usage_key=self.sequence.location).status_code == 200

    def test_rendering_descendant_of_gated_sequence_with_masquerade(self):
        """
        Test that if we are masquerading as a specific student, we do not redirect if content is gated
        """
        with self.store.default_store(ModuleStoreEnum.Type.split):
            # pylint:disable=attribute-defined-outside-init
            self.course = CourseFactory.create(**self.course_options())
            self.chapter = BlockFactory.create(parent=self.course, category='chapter')
            self.sequence = BlockFactory.create(
                parent=self.chapter,
                category='sequential',
                display_name='Sequence',
                is_time_limited=True,
            )
            self.vertical_block = BlockFactory.create(
                parent=self.sequence,
                category='vertical',
                display_name="Vertical",
            )
            self.html_block = BlockFactory.create(
                parent=self.vertical_block,
                category='html',
                data="<p>Test HTML Content<p>"
            )
            self.problem_block = BlockFactory.create(
                parent=self.vertical_block,
                category='problem',
                display_name='Problem'
            )
        CourseOverview.load_from_module_store(self.course.id)
        self.setup_user(admin=True, enroll=True, login=True)

        student = UserFactory()
        CourseEnrollment.enroll(student, self.course.id)
        self.update_masquerade(role='student', username=student.username)

        # Problem and Vertical response should both render successfully
        for block in [self.problem_block, self.vertical_block]:
            response = self.get_response(usage_key=block.location)
            assert response.status_code == 200

    def test_render_xblock_with_course_duration_limits(self):
        """
        Verify that expired banner message appears on xblock page, if learner is enrolled
        in audit mode.
        """
        self.setup_course(ModuleStoreEnum.Type.split)
        self.setup_user(admin=False, login=True)

        CourseDurationLimitConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1))
        add_course_mode(self.course, mode_slug=CourseMode.AUDIT)
        add_course_mode(self.course)
        CourseEnrollmentFactory(user=self.user, course_id=self.course.id, mode=CourseMode.AUDIT)

        response = self.get_response(usage_key=self.vertical_block.location)
        assert response.status_code == 200

        banner_text = get_expiration_banner_text(self.user, self.course)
        self.assertContains(response, banner_text, html=True)

    @patch('lms.djangoapps.courseware.views.views.is_request_from_mobile_app')
    def test_render_xblock_with_course_duration_limits_in_mobile_browser(self, mock_is_request_from_mobile_app):
        """
        Verify that expired banner message doesn't appear on xblock page in a mobile browser, if learner is enrolled
        in audit mode.
        """
        mock_is_request_from_mobile_app.return_value = True
        self.setup_course(ModuleStoreEnum.Type.split)
        self.setup_user(admin=False, login=True)

        CourseDurationLimitConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1))
        add_course_mode(self.course, mode_slug=CourseMode.AUDIT)
        add_course_mode(self.course)
        CourseEnrollmentFactory(user=self.user, course_id=self.course.id, mode=CourseMode.AUDIT)

        response = self.get_response(usage_key=self.vertical_block.location)
        assert response.status_code == 200

        banner_text = get_expiration_banner_text(self.user, self.course)
        self.assertNotContains(response, banner_text, html=True)

    @ddt.data(
        ('valid-jwt-for-exam-sequence', 200),
        ('valid-jwt-for-incorrect-sequence', 403),
        ('invalid-jwt', 403),
    )
    @override_settings(
        PROCTORING_BACKENDS={
            'DEFAULT': 'null',
            'null': {},
            'lti_external': {}
        }
    )
    @ddt.unpack
    @patch.dict('django.conf.settings.FEATURES', {'ENABLE_PROCTORED_EXAMS': True})
    @patch('lms.djangoapps.courseware.views.views.unpack_jwt')
    def test_render_descendant_of_exam_gated_by_access_token(self, exam_access_token,
                                                             expected_response, _mock_unpack_jwt):
        """
        Verify blocks inside an exam that requires token access are gated by
        a valid exam access JWT issued for that exam sequence.
        """
        with self.store.default_store(ModuleStoreEnum.Type.split):
            # pylint:disable=attribute-defined-outside-init
            self.course = CourseFactory.create(proctoring_provider='lti_external', **self.course_options())
            self.chapter = BlockFactory.create(parent=self.course, category='chapter')
            self.sequence = BlockFactory.create(
                parent=self.chapter,
                category='sequential',
                display_name='Sequence',
                is_time_limited=True,
            )
            self.vertical_block = BlockFactory.create(
                parent=self.sequence,
                category='vertical',
                display_name="Vertical",
            )
            self.problem_block = BlockFactory.create(
                parent=self.vertical_block,
                category='problem',
                display_name='Problem'
            )
            self.other_sequence = BlockFactory.create(
                parent=self.chapter,
                category='sequential',
                display_name='Sequence 2',
            )
        CourseOverview.load_from_module_store(self.course.id)
        self.setup_user(admin=False, enroll=True, login=True)

        def _mock_unpack_jwt_fn(token, user_id):
            if token == 'valid-jwt-for-exam-sequence':
                return {'content_id': str(self.sequence.location)}
            elif token == 'valid-jwt-for-incorrect-sequence':
                return {'content_id': str(self.other_sequence.location)}
            else:
                raise Exception('invalid JWT')

        _mock_unpack_jwt.side_effect = _mock_unpack_jwt_fn

        # Problem and Vertical response should be gated on access token
        for block in [self.problem_block, self.vertical_block]:
            response = self.get_response(
                usage_key=block.location, url_encoded_params=f'exam_access={exam_access_token}')
            assert response.status_code == expected_response

        # The Sequence itself should also be gated
        response = self.get_response(
            usage_key=self.sequence.location, url_encoded_params=f'exam_access={exam_access_token}')
        assert response.status_code == expected_response


class TestBasePublicVideoXBlock(ModuleStoreTestCase):
    """
    Tests for public video xblock.
    """

    def setup_course(self, enable_waffle=True):
        """
        Helper method to create the course.
        """
        # pylint:disable=attribute-defined-outside-init

        with self.store.default_store(self.store.default_modulestore.get_modulestore_type()):
            self.course = CourseFactory.create(**{'start': datetime.now() - timedelta(days=1)})
            chapter = BlockFactory.create(parent=self.course, category='chapter')
            vertical_block = BlockFactory.create(
                parent_location=chapter.location,
                category='vertical',
                display_name="Vertical"
            )
            self.html_block = BlockFactory.create(  # pylint: disable=attribute-defined-outside-init
                parent=vertical_block,
                category='html',
                data="<p>Test HTML Content<p>"
            )
            self.video_block_public = BlockFactory.create(  # pylint: disable=attribute-defined-outside-init
                parent=vertical_block,
                category='video',
                display_name='Video with public access',
                metadata={'public_access': True}
            )
            self.video_block_not_public = BlockFactory.create(  # pylint: disable=attribute-defined-outside-init
                parent=vertical_block,
                category='video',
                display_name='Video with private access'
            )
        WaffleFlagCourseOverrideModel.objects.create(
            waffle_flag=PUBLIC_VIDEO_SHARE.name,
            course_id=self.course.id,
            enabled=enable_waffle,
        )
        CourseOverview.load_from_module_store(self.course.id)


@ddt.ddt
class TestRenderPublicVideoXBlock(TestBasePublicVideoXBlock):
    """
    Tests for the courseware.render_public_video_xblock endpoint.
    """

    def get_response(self, usage_key, is_embed):
        """
        Overridable method to get the response from the endpoint that is being tested.
        """
        view_name = 'render_public_video_xblock'
        if is_embed:
            view_name += '_embed'
        url = reverse(view_name, kwargs={'usage_key_string': str(usage_key)})
        return self.client.get(url)

    @ddt.data(True, False)
    def test_render_xblock_with_invalid_usage_key(self, is_embed):
        """
        Verify that endpoint returns expected response with invalid usage key
        """
        response = self.get_response(usage_key='some_invalid_usage_key', is_embed=is_embed)
        self.assertContains(response, 'Page not found', status_code=404)

    @ddt.data(True, False)
    def test_render_xblock_with_non_video_usage_key(self, is_embed):
        """
        Verify that endpoint returns expected response if usage key block type is not `video`
        """
        self.setup_course()
        response = self.get_response(usage_key=self.html_block.location, is_embed=is_embed)
        self.assertContains(response, 'Page not found', status_code=404)

    @ddt.unpack
    @ddt.data(
        (True, True, 200),
        (True, False, 404),
        (False, True, 404),
        (False, False, 404),
    )
    def test_access(self, is_waffle_enabled, is_public_video, expected_status_code):
        """ Tests for access control """
        self.setup_course(enable_waffle=is_waffle_enabled)
        target_video = self.video_block_public if is_public_video else self.video_block_not_public

        response = self.get_response(usage_key=target_video.location, is_embed=False)
        embed_response = self.get_response(usage_key=target_video.location, is_embed=True)

        self.assertEqual(expected_status_code, response.status_code)
        self.assertEqual(expected_status_code, embed_response.status_code)


class TestRenderXBlockSelfPaced(TestRenderXBlock):  # lint-amnesty, pylint: disable=test-inherits-tests
    """
    Test rendering XBlocks for a self-paced course. Relies on the query
    count assertions in the tests defined by RenderXBlockMixin.
    """

    def setUp(self):  # lint-amnesty, pylint: disable=useless-super-delegation
        super().setUp()

    def course_options(self):
        options = super().course_options()
        options['self_paced'] = True
        return options


class EnterpriseConsentTestCase(EnterpriseTestConsentRequired, ModuleStoreTestCase):
    """
    Ensure that the Enterprise Data Consent redirects are in place only when consent is required.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        assert self.client.login(username=self.user.username, password=TEST_PASSWORD)
        self.course = CourseFactory.create()
        CourseOverview.load_from_module_store(self.course.id)
        CourseEnrollmentFactory(user=self.user, course_id=self.course.id)

    @patch('openedx.features.enterprise_support.api.enterprise_customer_for_request')
    def test_consent_required(self, mock_enterprise_customer_for_request):
        """
        Test that enterprise data sharing consent is required when enabled for the various courseware views.
        """
        # ENT-924: Temporary solution to replace sensitive SSO usernames.
        mock_enterprise_customer_for_request.return_value = None

        course_id = str(self.course.id)
        for url in (
                reverse("progress", kwargs=dict(course_id=course_id)),
                reverse("student_progress", kwargs=dict(course_id=course_id, student_id=str(self.user.id))),
        ):
            self.verify_consent_required(self.client, url)  # lint-amnesty, pylint: disable=no-value-for-parameter


@ddt.ddt
class AccessUtilsTestCase(ModuleStoreTestCase):
    """
    Test access utilities
    """
    @ddt.data(
        {
            'start_date_modifier': 1,  # course starts in future
            'setup_enterprise_enrollment': False,
            'expected_has_access': False,
            'expected_error_code': 'course_not_started',
        },
        {
            'start_date_modifier': -1,  # course already started
            'setup_enterprise_enrollment': False,
            'expected_has_access': True,
            'expected_error_code': None,
        },
        {
            'start_date_modifier': 1,  # course starts in future
            'setup_enterprise_enrollment': True,
            'expected_has_access': False,
            'expected_error_code': 'course_not_started_enterprise_learner',
        },
        {
            'start_date_modifier': -1,  # course already started
            'setup_enterprise_enrollment': True,
            'expected_has_access': True,
            'expected_error_code': None,
        },
    )
    @ddt.unpack
    @patch.dict('django.conf.settings.FEATURES', {'DISABLE_START_DATES': False, 'ENABLE_ENTERPRISE_INTEGRATION': True})
    def test_is_course_open_for_learner(
        self,
        start_date_modifier,
        setup_enterprise_enrollment,
        expected_has_access,
        expected_error_code,
    ):
        """
        Test is_course_open_for_learner().

        When setup_enterprise_enrollment == True, make an enterprise-subsidized enrollment, setting up one of each:
        * CourseEnrollment
        * EnterpriseCustomer
        * EnterpriseCustomerUser
        * EnterpriseCourseEnrollment
        * A mock request session to pre-cache the enterprise customer data.
        """
        staff_user = AdminFactory()
        start_date = datetime.now(UTC) + timedelta(days=start_date_modifier)
        course = CourseFactory.create(start=start_date)
        request = RequestFactory().get('/')
        request.user = staff_user
        request.session = {}
        if setup_enterprise_enrollment:
            course_enrollment = CourseEnrollmentFactory(mode=CourseMode.VERIFIED, user=staff_user, course_id=course.id)
            enterprise_customer = EnterpriseCustomerFactory(enable_learner_portal=True)
            add_enterprise_customer_to_session(request, EnterpriseCustomerSerializer(enterprise_customer).data)
            enterprise_customer_user = EnterpriseCustomerUserFactory(
                user_id=staff_user.id,
                enterprise_customer=enterprise_customer,
            )
            EnterpriseCourseEnrollmentFactory(enterprise_customer_user=enterprise_customer_user, course_id=course.id)
        set_current_request(request)

        access_response = check_course_open_for_learner(staff_user, course)
        assert bool(access_response) == expected_has_access
        assert access_response.error_code == expected_error_code


class DatesTabTestCase(TestCase):
    """
    Ensure that the legacy dates view redirects appropriately (it no longer exists).
    """

    def test_legacy_redirect(self):
        """
        Verify that the legacy dates page redirects to the MFE correctly.
        """
        response = self.client.get('/courses/course-v1:Org+Course+Run/dates?foo=b$r')
        assert response.status_code == 302
        assert response.get('Location') == 'http://learning-mfe/course/course-v1:Org+Course+Run/dates?foo=b%24r'


class MFEUrlTests(TestCase):
    """
    Test url utility method
    """
    @override_settings(LEARNING_MICROFRONTEND_URL='https://learningmfe.openedx.org')
    def test_url_generation(self):
        course_key = CourseKey.from_string("course-v1:OpenEdX+MFE+2020")
        section_key = UsageKey.from_string("block-v1:OpenEdX+MFE+2020+type@sequential+block@Introduction")
        unit_id = "block-v1:OpenEdX+MFE+2020+type@vertical+block@Getting_To_Know_You"
        assert make_learning_mfe_courseware_url(course_key) == (
            'https://learningmfe.openedx.org'
            '/course/course-v1:OpenEdX+MFE+2020'
        )
        assert make_learning_mfe_courseware_url(course_key, params=QueryDict('foo=b$r')) == (
            'https://learningmfe.openedx.org'
            '/course/course-v1:OpenEdX+MFE+2020'
            '?foo=b%24r'
        )
        assert make_learning_mfe_courseware_url(course_key, section_key, '') == (
            'https://learningmfe.openedx.org'
            '/course/course-v1:OpenEdX+MFE+2020'
            '/block-v1:OpenEdX+MFE+2020+type@sequential+block@Introduction'
        )
        assert make_learning_mfe_courseware_url(course_key, section_key, unit_id) == (
            'https://learningmfe.openedx.org'
            '/course/course-v1:OpenEdX+MFE+2020'
            '/block-v1:OpenEdX+MFE+2020+type@sequential+block@Introduction'
            '/block-v1:OpenEdX+MFE+2020+type@vertical+block@Getting_To_Know_You'
        )


class ContentOptimizationTestCase(ModuleStoreTestCase):
    """
    Test our ability to make browser optimizations based on XBlock content.
    """

    def setUp(self):
        super().setUp()
        self.math_html_usage_keys = []

        with self.store.default_store(ModuleStoreEnum.Type.split):
            self.course = CourseFactory.create(display_name='teꜱᴛ course', run="Testing_course")
            with self.store.bulk_operations(self.course.id):
                chapter = BlockFactory.create(
                    category='chapter',
                    parent_location=self.course.location,
                    display_name="Chapter 1",
                )
                section = BlockFactory.create(
                    category='sequential',
                    parent_location=chapter.location,
                    due=datetime(2013, 9, 18, 11, 30, 00),
                    display_name='Sequential 1',
                    format='Homework'
                )
                self.math_vertical = BlockFactory.create(
                    category='vertical',
                    parent_location=section.location,
                    display_name='Vertical with Mathjax HTML',
                )
                self.no_math_vertical = BlockFactory.create(
                    category='vertical',
                    parent_location=section.location,
                    display_name='Vertical with No Mathjax HTML',
                )
                MATHJAX_TAG_PAIRS = [
                    (r"\(", r"\)"),
                    (r"\[", r"\]"),
                    ("[mathjaxinline]", "[/mathjaxinline]"),
                    ("[mathjax]", "[/mathjax]"),
                ]
                for (i, (start_tag, end_tag)) in enumerate(MATHJAX_TAG_PAIRS):
                    math_html_block = BlockFactory.create(
                        category='html',
                        parent_location=self.math_vertical.location,
                        display_name=f"HTML With Mathjax {i}",
                        data=f"<p>Hello Math! {start_tag}x^2 + y^2{end_tag}</p>",
                    )
                    self.math_html_usage_keys.append(math_html_block.location)

                self.html_without_mathjax = BlockFactory.create(
                    category='html',
                    parent_location=self.no_math_vertical.location,
                    display_name="HTML Without Mathjax",
                    data="<p>I talk about mathjax, but I have no actual Math!</p>",
                )

        self.course_key = self.course.id
        self.user = UserFactory(username='staff_user', profile__country='AX', is_staff=True)
        self.date = datetime(2013, 1, 22, tzinfo=UTC)
        self.enrollment = CourseEnrollment.enroll(self.user, self.course_key)
        self.enrollment.created = self.date
        self.enrollment.save()

    @override_waffle_flag(COURSEWARE_OPTIMIZED_RENDER_XBLOCK, True)
    def test_mathjax_detection(self):
        self.client.login(username=self.user.username, password=TEST_PASSWORD)

        # Check the HTML blocks with Math
        for usage_key in self.math_html_usage_keys:
            url = reverse("render_xblock", kwargs={'usage_key_string': str(usage_key)})
            response = self.client.get(url)
            assert response.status_code == 200
            assert b"MathJax.Hub.Config" in response.content

        # Check the one without Math...
        url = reverse("render_xblock", kwargs={
            'usage_key_string': str(self.html_without_mathjax.location)
        })
        response = self.client.get(url)
        assert response.status_code == 200
        assert b"MathJax.Hub.Config" not in response.content

        # The containing vertical should still return MathJax (for now)
        url = reverse("render_xblock", kwargs={
            'usage_key_string': str(self.no_math_vertical.location)
        })
        response = self.client.get(url)
        assert response.status_code == 200
        assert b"MathJax.Hub.Config" in response.content

    @override_waffle_flag(COURSEWARE_OPTIMIZED_RENDER_XBLOCK, False)
    def test_mathjax_detection_disabled(self):
        """Check that we can disable optimizations."""
        self.client.login(username=self.user.username, password=TEST_PASSWORD)
        url = reverse("render_xblock", kwargs={
            'usage_key_string': str(self.html_without_mathjax.location)
        })
        response = self.client.get(url)
        assert response.status_code == 200
        assert b"MathJax.Hub.Config" in response.content


@ddt.ddt
class TestCourseWideResources(ModuleStoreTestCase):
    """
    Tests that custom course-wide resources are rendered in course pages
    """

    @ddt.data(
        ('progress', 'course_id', False, False),
        ('instructor_dashboard', 'course_id', True, False),
        ('forum_form_discussion', 'course_id', False, False),
        ('render_xblock', 'usage_key_string', False, True),
    )
    @ddt.unpack
    def test_course_wide_resources(self, url_name, param, is_instructor, is_rendered):
        """
        Tests that the <script> and <link> tags are created for course-wide custom resources.
        Also, test that the order which the resources are added match the given order.
        """
        user = UserFactory()

        js = ['/test.js', 'https://testcdn.com/js/lib.min.js', '//testcdn.com/js/lib2.js']
        css = ['https://testcdn.com/css/lib.min.css', '//testcdn.com/css/lib2.css', '/test.css']

        course = CourseFactory.create(course_wide_js=js, course_wide_css=css)
        chapter = BlockFactory.create(parent_location=course.location, category='chapter')
        sequence = BlockFactory.create(parent_location=chapter.location, category='sequential', display_name='Sequence')

        CourseOverview.load_from_module_store(course.id)
        CourseEnrollmentFactory(user=user, course_id=course.id)
        if is_instructor:
            allow_access(course, user, 'instructor')
        assert self.client.login(username=user.username, password=TEST_PASSWORD)

        kwargs = None
        if param == 'course_id':
            kwargs = {'course_id': str(course.id)}
        elif param == 'usage_key_string':
            kwargs = {'usage_key_string': str(sequence.location)}
        response = self.client.get(reverse(url_name, kwargs=kwargs))

        content = response.content.decode('utf-8')
        js_match = [re.search(f'<script .*src=[\'"]{j}[\'"].*>', content) for j in js]
        css_match = [re.search(f'<link .*href=[\'"]{c}[\'"].*>', content) for c in css]

        # custom resources are included
        if is_rendered:
            assert None not in js_match
            assert None not in css_match

            # custom resources are added in order
            for i in range(len(js) - 1):
                assert js_match[i].start() < js_match[i + 1].start()
            for i in range(len(css) - 1):
                assert css_match[i].start() < css_match[i + 1].start()
        else:
            assert js_match == [None, None, None]
            assert css_match == [None, None, None]


@ddt.ddt
class TestBasePublicVideoXBlockView(TestBasePublicVideoXBlock):
    """Test Base Public Video XBlock View tests"""
    base_block = BasePublicVideoXBlockView(request=MagicMock())

    @ddt.data(
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    )
    @ddt.unpack
    @patch('lms.djangoapps.courseware.views.views.get_block_by_usage_id')
    def test_get_course_and_video_block(self, is_waffle_enabled, is_public_video, mock_get_block_by_usage_id):
        """
        Test that get_course_and_video_block returns course and video block.
        """

        self.setup_course(enable_waffle=is_waffle_enabled)
        target_video = self.video_block_public if is_public_video else self.video_block_not_public

        mock_get_block_by_usage_id.return_value = (target_video, None)

        # get 404 unless waffle is enabled and video is public
        if is_public_video and is_waffle_enabled:
            course, video_block = self.base_block.get_course_and_video_block(str(target_video.location))
            assert course.id == self.course.id
            assert video_block.location == target_video.location
        else:
            with self.assertRaisesRegex(Http404, "Video not found"):
                course, video_block = self.base_block.get_course_and_video_block(str(target_video.location))


@ddt.ddt
class TestPublicVideoXBlockView(TestBasePublicVideoXBlock):
    """Test Public Video XBlock View"""
    request = RequestFactory().get('/?utm_source=edx.org&utm_medium=referral&utm_campaign=video')
    request.user = AnonymousUser()
    base_block = PublicVideoXBlockView(request=request)
    default_utm_params = {'utm_source': 'edx.org', 'utm_medium': 'referral', 'utm_campaign': 'video'}

    @contextmanager
    def mock_get_learn_more_url(self, **kwargs):
        """ Helper for mocking get_learn_more_button_url """
        with patch.object(
            PublicVideoXBlockView,
            'get_learn_more_button_url',
            **kwargs
        ) as mock_get_url:
            yield mock_get_url

    @contextmanager
    def mock_get_catalog_course_data(self, **kwargs):
        """ Helper for mocking get_catalog_course_data """
        with patch.object(
            PublicVideoXBlockView,
            'get_catalog_course_data',
            **kwargs
        ) as mock_get_data:
            yield mock_get_data

    def test_get_template_and_context(self):
        """
        Get template and context.
        """
        self.setup_course(enable_waffle=True)
        fragment = MagicMock()
        with patch.object(self.video_block_public, "render", return_value=fragment):
            with self.mock_get_learn_more_url():
                with self.mock_get_catalog_course_data():
                    template, context = self.base_block.get_template_and_context(self.course, self.video_block_public)
        assert template == 'public_video.html'
        assert context['fragment'] == fragment
        assert context['course'] == self.course

    @ddt.unpack
    @ddt.data(
        (None, None, {}),
        ('uuid', None, {}),
        ('uuid', {}, {'org_logo': None, 'marketing_url': None}),
    )
    def test_get_catalog_course_data(self, mock_get_uuid, mock_get_data, expected_response):
        self.setup_course()
        with patch('lms.djangoapps.courseware.views.views.get_course_uuid_for_course', return_value=mock_get_uuid):
            with patch('lms.djangoapps.courseware.views.views.get_course_data', return_value=mock_get_data):
                assert self.base_block.get_catalog_course_data(self.course) == expected_response

    @ddt.unpack
    @ddt.data(
        ({}, None),
        ({'marketing_url': 'www.somesite.com/this'}, 'www.somesite.com/this'),
        ({'marketing_url': 'www.somesite.com/this?utm_source=jansen'}, 'www.somesite.com/this'),
    )
    def test_get_catalog_course_marketing_url(self, input_data, expected_url):
        url = self.base_block._get_catalog_course_marketing_url(input_data)
        assert url == expected_url

    @ddt.unpack
    @ddt.data(
        ({}, None),
        ({'owners': []}, None),
        ({'owners': [{}]}, None),
        ({'owners': [{'logo_image_url': 'somesite.org/image'}]}, 'somesite.org/image'),
        ({'owners': [{'logo_image_url': 'firsturl'}, {'logo_image_url': 'secondurl'}]}, 'firsturl'),
    )
    def test_get_catalog_course_owner_logo(self, input_data, expected_url):
        url = self.base_block._get_catalog_course_owner_logo(input_data)
        assert url == expected_url

    @ddt.data("poster", None)
    def test_get_social_sharing_metadata(self, poster_url):
        """
        Test that get_social_sharing_metadata returns correct metadata.
        """
        self.setup_course(enable_waffle=True)
        # can't mock something that doesn't exist
        self.video_block_public._post = MagicMock(return_value=poster_url)

        metadata = self.base_block.get_social_sharing_metadata(self.course, self.video_block_public)
        assert metadata["video_title"] == self.video_block_public.display_name_with_default
        assert metadata["video_description"] == f"Watch a video from the course {self.course.display_name} on edX.org"
        assert metadata["video_thumbnail"] == "" if poster_url is None else poster_url

    def test_get_utm_params(self):
        """
        Test that get_utm_params returns correct utm params.
        """
        utm_params = self.base_block.get_utm_params()
        assert utm_params == {
            'utm_source': 'edx.org',
            'utm_medium': 'referral',
            'utm_campaign': 'video',
        }

    def test_build_url(self):
        """
        Test that build_url returns correct url.
        """
        base_url = 'http://test.server'
        params = {
            'param1': 'value1',
            'param2': 'value2',
        }
        utm_params = {
            "utm_source": "edx.org",
        }
        url = self.base_block.build_url(base_url, params, utm_params)
        assert url == 'http://test.server?param1=value1&param2=value2&utm_source=edx.org'

    def assert_url_with_params(self, url, base_url, params):
        if params:
            assert url == base_url + '?' + urlencode(params)
        else:
            assert url == base_url

    @ddt.data({}, {'marketing_url': 'some_url'})
    def test_get_learn_more_button_url(self, catalog_course_info):
        """
        If we have a marketing url from the catalog service, use that. Otherwise
        use the courseware about_course
        """
        self.setup_course()
        url = self.base_block.get_learn_more_button_url(self.course, catalog_course_info, self.default_utm_params)
        if 'marketing_url' in catalog_course_info:
            expected_url = catalog_course_info['marketing_url']
        else:
            expected_url = reverse('about_course', kwargs={'course_id': str(self.course.id)})
        self.assert_url_with_params(url, expected_url, self.default_utm_params)

    def test_get_public_video_cta_button_urls(self):
        """
        Test that get_public_video_cta_button_urls returns correct urls.
        """
        catalog_course_info = {'marketing_url': 'some_url'}
        self.setup_course()
        learn_more_url, enroll_url, go_to_course_url = \
            self.base_block.get_public_video_cta_button_urls(self.course, catalog_course_info)
        assert go_to_course_url == \
            get_learning_mfe_home_url(course_key=self.course.id, url_fragment='home')

        assert learn_more_url == \
            self.base_block.get_learn_more_button_url(self.course, catalog_course_info,
                                                      self.default_utm_params)
        assert enroll_url == self.base_block.build_url(
            reverse('register_user'),
            {
                'course_id': str(self.course.id),
                'enrollment_action': 'enroll',
                'email_opt_in': False,
            },
            self.default_utm_params,
        )

    @ddt.data(True, False)
    def test_get_is_enrolled_in_course(self, mock_registered_for_course):
        """
        Test that is_enrolled_in_course returns correct value.
        """
        with patch('lms.djangoapps.courseware.views.views.registered_for_course',
                   return_value=mock_registered_for_course):
            self.setup_course()
            assert views.registered_for_course(self.course, self.user) == mock_registered_for_course
            assert self.base_block.get_is_enrolled_in_course(self.course) == mock_registered_for_course


class TestPublicVideoXBlockEmbedView(TestBasePublicVideoXBlock):
    """Test Public Video XBlock Embed View"""
    base_block = PublicVideoXBlockEmbedView()

    def test_get_template_and_context(self):
        """
        Get template and context.
        """
        self.setup_course(enable_waffle=True)
        fragment = MagicMock()
        with patch.object(self.video_block_public, "render", return_value=fragment):
            template, context = self.base_block.get_template_and_context(self.course, self.video_block_public)
            assert template == 'public_video_share_embed.html'
            assert context['fragment'] == fragment
            assert context['course'] == self.course


@ddt.ddt
@override_waffle_flag(COURSEWARE_MICROFRONTEND_SEARCH_ENABLED, active=True)
class TestCoursewareMFESearchAPI(SharedModuleStoreTestCase):
    """
    Tests the endpoint to fetch the Courseware Search waffle flag enabled status.
    """

    def setUp(self):
        super().setUp()

        self.course = CourseFactory.create()

        self.client = APIClient()
        self.apiUrl = reverse('courseware_search_enabled_view', kwargs={'course_id': str(self.course.id)})

    @ddt.data(
        (CourseMode.AUDIT, False),
        (CourseMode.VERIFIED, True),
        (CourseMode.MASTERS, True),
    )
    @ddt.unpack
    @patch.dict('django.conf.settings.FEATURES', {'ENABLE_COURSEWARE_SEARCH_VERIFIED_ENROLLMENT_REQUIRED': True})
    def test_courseware_mfe_search_verified_only(self, mode, expected_enabled):
        """
        Only verified enrollees may use Courseware Search if ENABLE_COURSEWARE_SEARCH_VERIFIED_ENROLLMENT_REQUIRED
        is enabled.
        """
        user = UserFactory()
        CourseEnrollmentFactory.create(user=user, course_id=self.course.id, mode=mode)

        self.client.login(username=user.username, password=TEST_PASSWORD)
        response = self.client.get(self.apiUrl, content_type='application/json')
        body = json.loads(response.content.decode('utf-8'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(body, {'enabled': expected_enabled})

    @patch.dict('django.conf.settings.FEATURES', {'ENABLE_COURSEWARE_SEARCH_VERIFIED_ENROLLMENT_REQUIRED': True})
    def test_courseware_mfe_search_staff_access(self):
        """
        Staff users may use Courseware Search regardless of their enrollment status.
        """
        user_staff = UserFactory(is_staff=True)  # not enrolled

        self.client.login(username=user_staff.username, password=TEST_PASSWORD)
        response = self.client.get(self.apiUrl, content_type='application/json')
        body = json.loads(response.content.decode('utf-8'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(body, {'enabled': True})

    @override_waffle_flag(COURSEWARE_MICROFRONTEND_SEARCH_ENABLED, active=False)
    def test_is_mfe_search_waffle_disabled(self):
        """
        Courseware search is only available when the waffle flag is enabled, if no inclusion date is provided.
        """
        user_admin = UserFactory(is_staff=True, is_superuser=True)
        CourseEnrollmentFactory.create(user=user_admin, course_id=self.course.id, mode=CourseMode.VERIFIED)
        self.client.login(username=user_admin.username, password=TEST_PASSWORD)
        response = self.client.get(self.apiUrl, content_type='application/json')
        body = json.loads(response.content.decode('utf-8'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(body, {'enabled': False})

    @patch.dict('django.conf.settings.FEATURES', {'COURSEWARE_SEARCH_INCLUSION_DATE': '2020'})
    @override_waffle_flag(COURSEWARE_MICROFRONTEND_SEARCH_ENABLED, active=False)
    @ddt.data(
        (datetime(2013, 9, 18, 11, 30, 00), False),
        (None, False),
        (datetime(2024, 9, 18, 11, 30, 00), True),
    )
    @ddt.unpack
    def test_inclusion_date_greater_than_course_start(self, start_date, expected_enabled):
        course_with_start = CourseFactory.create(start=start_date)
        api_url = reverse('courseware_search_enabled_view', kwargs={'course_id': str(course_with_start.id)})

        user_staff = UserFactory(is_staff=True)

        self.client.login(username=user_staff.username, password=TEST_PASSWORD)
        response = self.client.get(api_url, content_type='application/json')
        body = json.loads(response.content.decode('utf-8'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(body, {'enabled': expected_enabled})


class TestCoursewareMFENavigationSidebarTogglesAPI(SharedModuleStoreTestCase):
    """
    Tests the endpoint to fetch the Courseware Navigation Sidebar waffle flags status.
    """

    def setUp(self):
        super().setUp()

        self.course = CourseFactory.create()

        self.client = APIClient()
        self.apiUrl = reverse('courseware_navigation_sidebar_toggles_view', kwargs={'course_id': str(self.course.id)})

    @override_waffle_switch(ENABLE_COMPLETION_TRACKING_SWITCH, active=False)
    def test_courseware_mfe_navigation_sidebar_completion_track_disabled(self):
        """
        Getter to check if completion tracking is disabled.
        """
        response = self.client.get(self.apiUrl, content_type='application/json')
        body = json.loads(response.content.decode('utf-8'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            body,
            {
                "enable_completion_tracking": False,
            },
        )

    @override_waffle_switch(ENABLE_COMPLETION_TRACKING_SWITCH, active=True)
    def test_courseware_mfe_navigation_sidebar_completion_track_enabled(self):
        """
        Getter to check if completion tracking is enabled.
        """
        response = self.client.get(self.apiUrl, content_type='application/json')
        body = json.loads(response.content.decode('utf-8'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            body,
            {
                "enable_completion_tracking": True,
            },
        )


@ddt.ddt
class CourseAboutViewTests(ModuleStoreTestCase):
    """
    Tests for the CourseAboutView.
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()

    @ddt.data(
        (True, True),
        (False, False),
    )
    @ddt.unpack
    def test_course_about_redirect_to_mfe(self, catalog_mfe_enabled, expected_redirect):
        """
        Test that the CourseAboutView redirects to the MFE when appropriate.
        """
        new_settings = {
            "ENABLE_CATALOG_MICROFRONTEND": catalog_mfe_enabled,
            "CATALOG_MICROFRONTEND_URL": "http://example.com/catalog",
        }
        with override_settings(**new_settings):
            response = self.client.get(reverse('about_course', args=[str(self.course.id)]))
            if expected_redirect:
                assert response.status_code == 301
                assert response.url == "http://example.com/catalog/courses/{}/about".format(self.course.id)
            else:
                assert response.status_code == 200
