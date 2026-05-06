"""
Tests for the LTI provider views
"""
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from django.test.client import RequestFactory
from django.test.utils import override_settings
from django.urls import reverse
from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator
from openedx_events.learning.data import LtiProviderLaunchData, LtiProviderLaunchParamsData, UserData, UserPersonalData

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.courseware.testutils import RenderXBlockTestMixin
from lms.djangoapps.lti_provider import models, views
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # pylint: disable=wrong-import-order
)

LTI_DEFAULT_PARAMS = {
    'roles': 'Instructor,urn:lti:instrole:ims/lis/Administrator',
    'context_id': 'lti_launch_context_id',
    'oauth_version': '1.0',
    'oauth_consumer_key': 'consumer_key',
    'oauth_signature': 'OAuth Signature',
    'oauth_signature_method': 'HMAC-SHA1',
    'oauth_timestamp': 'OAuth Timestamp',
    'oauth_nonce': 'OAuth Nonce',
    'user_id': 'LTI_User',
}

LTI_OPTIONAL_PARAMS = {
    'context_title': 'context title',
    'context_label': 'context label',
    'lis_result_sourcedid': 'result sourcedid',
    'lis_outcome_service_url': 'outcome service URL',
    'tool_consumer_instance_guid': 'consumer instance guid'
}

COURSE_KEY = CourseLocator(org='some_org', course='some_course', run='some_run')
USAGE_KEY = BlockUsageLocator(course_key=COURSE_KEY, block_type='problem', block_id='block_id')

COURSE_PARAMS = {
    'course_key': COURSE_KEY,
    'usage_key': USAGE_KEY
}


ALL_PARAMS = dict(list(LTI_DEFAULT_PARAMS.items()) + list(COURSE_PARAMS.items()))


def build_launch_request(extra_post_data=None, param_to_delete=None):
    """
    Helper method to create a new request object for the LTI launch.
    """
    if extra_post_data is None:
        extra_post_data = {}
    post_data = dict(list(LTI_DEFAULT_PARAMS.items()) + list(extra_post_data.items()))
    if param_to_delete:
        del post_data[param_to_delete]
    request = RequestFactory().post('/', data=post_data)
    request.user = UserFactory.create()
    request.session = MagicMock()
    return request


class LtiTestMixin:
    """
    Mixin for LTI tests
    """
    @patch.dict('django.conf.settings.FEATURES', {'ENABLE_LTI_PROVIDER': True})
    def setUp(self):
        super().setUp()
        # Always accept the OAuth signature
        self.mock_verify = MagicMock(return_value=True)
        patcher = patch('lms.djangoapps.lti_provider.signature_validator.SignatureValidator.verify', self.mock_verify)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.consumer = models.LtiConsumer(
            consumer_name='consumer',
            consumer_key=LTI_DEFAULT_PARAMS['oauth_consumer_key'],
            consumer_secret='secret'
        )
        self.consumer.save()

        self.auto_link_consumer = models.LtiConsumer(
            consumer_name='auto-link-consumer',
            consumer_key='consumer_key_2',
            consumer_secret='secret_2',
            require_user_account=True
        )
        self.auto_link_consumer.save()


class LtiLaunchTest(LtiTestMixin, TestCase):
    """
    Tests for the lti_launch view
    """
    @patch('lms.djangoapps.lti_provider.views.LTI_PROVIDER_LAUNCH_SUCCESS.send_event')
    @patch('lms.djangoapps.lti_provider.views.render_courseware')
    @patch('lms.djangoapps.lti_provider.views.authenticate_lti_user')
    def test_valid_launch(self, _authenticate, render, lti_launch_success_send_event):  # noqa: PT019
        """
        Verifies that the LTI launch succeeds when passed a valid request.
        """
        request = build_launch_request()
        views.lti_launch(request, str(COURSE_KEY), str(USAGE_KEY))
        render.assert_called_with(request, USAGE_KEY)
        lti_launch_success_send_event.assert_called_with(
            launch_data=LtiProviderLaunchData(
                user=UserData(
                    id=request.user.id,
                    is_active=request.user.is_active,
                    pii=UserPersonalData(
                        username=request.user.username,
                        email=request.user.email,
                        name=request.user.profile.name,
                    )
                ),
                course_key=COURSE_KEY,
                usage_key=USAGE_KEY,
                launch_params=LtiProviderLaunchParamsData(
                    roles='Instructor,urn:lti:instrole:ims/lis/Administrator',
                    context_id='lti_launch_context_id',
                    user_id='LTI_User', extra_params={}
                )
            )
        )

    @patch('lms.djangoapps.lti_provider.views.render_courseware')
    @patch('lms.djangoapps.lti_provider.views.store_outcome_parameters')
    @patch('lms.djangoapps.lti_provider.views.authenticate_lti_user')
    def test_valid_launch_with_optional_params(self, _authenticate, store_params, _render):  # noqa: PT019
        """
        Verifies that the LTI launch succeeds when passed a valid request.
        """
        request = build_launch_request(extra_post_data=LTI_OPTIONAL_PARAMS)
        views.lti_launch(request, str(COURSE_KEY), str(USAGE_KEY))
        store_params.assert_called_with(
            dict(list(ALL_PARAMS.items()) + list(LTI_OPTIONAL_PARAMS.items())),
            request.user,
            self.consumer
        )

    @patch('lms.djangoapps.lti_provider.views.render_courseware')
    @patch('lms.djangoapps.lti_provider.views.store_outcome_parameters')
    @patch('lms.djangoapps.lti_provider.views.authenticate_lti_user')
    @override_settings(LTI_CUSTOM_PARAMS=["extra_param1", "extra_param2"])
    def test_valid_launch_with_extra_params(self, _authenticate, store_params, _render):  # noqa: PT019
        """
        Verifies that the LTI launch succeeds when passed a valid request.
        """
        extra_params = {'extra_param1': 'extra_value1', 'extra_param2': "extra_value2"}
        request = build_launch_request(extra_post_data=LTI_OPTIONAL_PARAMS | extra_params)
        views.lti_launch(request, str(COURSE_KEY), str(USAGE_KEY))
        store_params.assert_called_with(
            dict(list(ALL_PARAMS.items()) + list(LTI_OPTIONAL_PARAMS.items()) + list(extra_params.items())),
            request.user,
            self.consumer
        )

    @patch('lms.djangoapps.courseware.views.views.render_xblock')
    @patch('lms.djangoapps.lti_provider.views.authenticate_lti_user')
    def test_render_xblock_params(self, _authenticate, render):  # noqa: PT019
        """
        Verifies that the LTI renders an XBlock without:
        1. Checking the enrollment.
        2. Displaying staff debug info.
        """
        request = build_launch_request()
        views.lti_launch(request, str(COURSE_KEY), str(USAGE_KEY))
        render.assert_called_with(request, str(USAGE_KEY), check_if_enrolled=False, disable_staff_debug_info=True)

    @patch('lms.djangoapps.lti_provider.views.render_courseware')
    @patch('lms.djangoapps.lti_provider.views.store_outcome_parameters')
    @patch('lms.djangoapps.lti_provider.views.authenticate_lti_user')
    def test_outcome_service_registered(self, _authenticate, store_params, _render):  # noqa: PT019
        """
        Verifies that the LTI launch succeeds when passed a valid request.
        """
        request = build_launch_request()
        views.lti_launch(
            request,
            str(COURSE_PARAMS['course_key']),
            str(COURSE_PARAMS['usage_key'])
        )
        store_params.assert_called_with(ALL_PARAMS, request.user, self.consumer)

    def launch_with_missing_parameter(self, missing_param):
        """
        Helper method to remove a parameter from the LTI launch and call the view
        """
        request = build_launch_request(param_to_delete=missing_param)
        return views.lti_launch(request, None, None)

    def test_launch_with_missing_parameters(self):
        """
        Runs through all required LTI parameters and verifies that the lti_launch
        view returns Bad Request if any of them are missing.
        """
        for missing_param in views.REQUIRED_PARAMETERS:
            response = self.launch_with_missing_parameter(missing_param)
            assert response.status_code == 400, (('Launch should fail when parameter ' + missing_param) + ' is missing')

    def test_launch_with_disabled_feature_flag(self):
        """
        Verifies that the LTI launch will fail if the ENABLE_LTI_PROVIDER flag
        is not set
        """
        with patch.dict('django.conf.settings.FEATURES', {'ENABLE_LTI_PROVIDER': False}):
            request = build_launch_request()
            response = views.lti_launch(request, None, None)
            assert response.status_code == 403

    def test_forbidden_if_signature_fails(self):
        """
        Verifies that the view returns Forbidden if the LTI OAuth signature is
        incorrect.
        """
        self.mock_verify.return_value = False

        request = build_launch_request()
        response = views.lti_launch(request, None, None)
        assert response.status_code == 403
        assert response.status_code == 403

    @patch('lms.djangoapps.lti_provider.views.render_courseware')
    def test_lti_consumer_record_supplemented_with_guid(self, _render):  # noqa: PT019
        self.mock_verify.return_value = False

        request = build_launch_request(LTI_OPTIONAL_PARAMS)
        with self.assertNumQueries(3):
            views.lti_launch(request, None, None)
        consumer = models.LtiConsumer.objects.get(
            consumer_key=LTI_DEFAULT_PARAMS['oauth_consumer_key']
        )
        assert consumer.instance_guid == 'consumer instance guid'

    @patch('lms.djangoapps.lti_provider.views.render_to_response')
    def test_unauthenticated_user_shown_error_when_require_user_account_is_enabled(self, render_error):
        """
        Verify that an error page is shown instead of LTI Content for an unauthenticated user,
        when the `require_user_account` flag is enabled for the LTI Consumer.
        """
        request = build_launch_request({'oauth_consumer_key': 'consumer_key_2'})
        request.user = AnonymousUser()

        views.lti_launch(request, str(COURSE_KEY), str(USAGE_KEY))

        render_error.assert_called()
        assert render_error.call_args[0][0] == "lti_provider/user-auth-error.html"

    @patch('lms.djangoapps.lti_provider.views.render_to_response')
    def test_auth_error_shown_when_lis_email_is_different_from_user_email(self, render_error):
        """
        When the `require_user_account` flag is enabled for the LTI Consumer, verify that
        an error page is shown instead of LTI Content if the authenticated user's email
        doesn't match the `lis_person_contact_email_primary` value from LTI Launch.
        """
        # lis email different from logged in user
        request = build_launch_request({
            'oauth_consumer_key': 'consumer_key_2',
            'lis_person_contact_email_primary': 'random_email@test.com'
        })

        views.lti_launch(request, str(COURSE_KEY), str(USAGE_KEY))
        render_error.assert_called()


class LtiLaunchTestRender(LtiTestMixin, RenderXBlockTestMixin, ModuleStoreTestCase):
    """
    Tests for the rendering returned by lti_launch view.
    This class overrides the get_response method, which is used by
    the tests defined in RenderXBlockTestMixin.
    """

    def get_response(self, usage_key, url_encoded_params=None):
        """
        Overridable method to get the response from the endpoint that is being tested.
        """
        lti_launch_url = reverse(
            'lti_provider_launch',
            kwargs={
                'course_id': str(self.course.id),
                'usage_id': str(usage_key)
            }
        )
        if url_encoded_params:
            lti_launch_url += '?' + url_encoded_params

        return self.client.post(lti_launch_url, data=LTI_DEFAULT_PARAMS)

    # The following test methods override the base tests for verifying access
    # by unenrolled and unauthenticated students, since there is a discrepancy
    # of access rules between the 2 endpoints (LTI and xBlock_render).
    # TODO fix this access discrepancy to the same underlying data.

    def test_unenrolled_student(self):
        """
        Override since LTI allows access to unenrolled students.
        """
        self.setup_course()
        self.setup_user(admin=False, enroll=False, login=True)
        self.verify_response()

    def test_unauthenticated(self):
        """
        Override since LTI allows access to unauthenticated users.
        """
        self.setup_course()
        self.setup_user(admin=False, enroll=True, login=False)
        self.verify_response()
