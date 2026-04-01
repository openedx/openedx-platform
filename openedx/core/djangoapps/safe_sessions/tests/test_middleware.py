"""
Unit tests for SafeSessionMiddleware
"""
import uuid
from unittest.mock import call, patch, MagicMock

import ddt
from crum import set_current_request
from django.conf import settings
from django.contrib.auth import SESSION_KEY
from django.contrib.auth.models import AnonymousUser, User  # lint-amnesty, pylint: disable=imported-auth-user
from django.http import HttpResponse, HttpResponseRedirect, SimpleCookie
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from edx_django_utils.cache import RequestCache
from edx_rest_framework_extensions.auth.jwt import cookies as jwt_cookies

from common.djangoapps.student.models import PendingEmailChange
from openedx.core.djangolib.testing.utils import get_mock_request, CacheIsolationTestCase, skip_unless_lms
from openedx.core.djangoapps.user_authn.tests.utils import setup_login_oauth_client
from openedx.core.djangoapps.user_authn.cookies import ALL_LOGGED_IN_COOKIE_NAMES
from common.djangoapps.student.tests.factories import UserFactory

from ..middleware import (
    EmailChangeMiddleware,
    SafeCookieData,
    SafeSessionMiddleware,
    mark_user_change_as_expected,
    obscure_token,
    track_request_user_changes
)
from .test_utils import TestSafeSessionsLogMixin


class TestSafeSessionProcessRequest(TestSafeSessionsLogMixin, TestCase):
    """
    Test class for SafeSessionMiddleware.process_request
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.addCleanup(set_current_request, None)
        self.request = get_mock_request()
        self.TEST_PASSWORD = 'Password1234'

    def assert_response(self, safe_cookie_data=None, success=True):
        """
        Calls SafeSessionMiddleware.process_request and verifies
        the response.

        Arguments:
            safe_cookie_data - If provided, it is serialized and
              stored in the request's cookies.
            success - If True, verifies a successful response.
              Else, verifies a failed response with an HTTP redirect.
        """
        if safe_cookie_data:
            self.request.COOKIES[settings.SESSION_COOKIE_NAME] = str(safe_cookie_data)
        response = SafeSessionMiddleware(get_response=lambda request: None).process_request(self.request)
        if success:
            assert response is None
            assert getattr(self.request, 'need_to_delete_cookie', None) is None
        else:
            assert response.status_code == HttpResponseRedirect.status_code
            assert self.request.need_to_delete_cookie

    def assert_no_session(self):
        """
        Asserts that a session object is *not* set on the request.
        """
        assert getattr(self.request, 'session', None) is None

    def assert_no_user_in_session(self):
        """
        Asserts that a user object is *not* set on the request's session.
        """
        assert self.request.session.get(SESSION_KEY) is None

    def assert_user_in_session(self):
        """
        Asserts that a user object *is* set on the request's session.
        """
        assert SafeSessionMiddleware.get_user_id_from_session(self.request) == self.user.id

    @patch("openedx.core.djangoapps.safe_sessions.middleware.LOG_REQUEST_USER_CHANGES", False)
    @patch("openedx.core.djangoapps.safe_sessions.middleware.track_request_user_changes")
    def test_success(self, mock_log_request_user_changes):
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)
        session_id = self.client.session.session_key
        safe_cookie_data = SafeCookieData.create(session_id, self.user.id)

        # pre-verify steps 3, 4, 5
        assert getattr(self.request, 'session', None) is None
        assert getattr(self.request, 'safe_cookie_verified_user_id', None) is None

        # verify step 1: safe cookie data is parsed
        self.assert_response(safe_cookie_data)
        self.assert_user_in_session()

        # verify step 2: cookie value is replaced with parsed session_id
        assert self.request.COOKIES[settings.SESSION_COOKIE_NAME] == session_id

        # verify step 3: session set in request
        assert self.request.session is not None

        # verify steps 4, 5: user_id stored for later verification
        assert self.request.safe_cookie_verified_user_id == self.user.id

        # verify extra request_user_logging not called.
        assert not mock_log_request_user_changes.called

    @patch("openedx.core.djangoapps.safe_sessions.middleware.LOG_REQUEST_USER_CHANGES", True)
    @patch("openedx.core.djangoapps.safe_sessions.middleware.track_request_user_changes")
    def test_log_request_user_on(self, mock_log_request_user_changes):
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)
        session_id = self.client.session.session_key
        safe_cookie_data = SafeCookieData.create(session_id, self.user.id)

        self.assert_response(safe_cookie_data)
        assert mock_log_request_user_changes.called

    def test_success_no_cookies(self):
        self.assert_response()
        self.assert_no_user_in_session()

    def test_success_no_session(self):
        safe_cookie_data = SafeCookieData.create('no_such_session_id', self.user.id)
        self.assert_response(safe_cookie_data)
        self.assert_no_user_in_session()

    def test_success_no_session_and_user(self):
        safe_cookie_data = SafeCookieData.create('no_such_session_id', 'no_such_user')
        self.assert_response(safe_cookie_data)
        self.assert_no_user_in_session()

    def test_parse_error_at_step_1(self):
        self.request.META['HTTP_ACCEPT'] = 'text/html'
        with self.assert_parse_error():
            self.assert_response('not-a-safe-cookie', success=False)
        self.assert_no_session()

    def test_invalid_user_at_step_4(self):
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)
        safe_cookie_data = SafeCookieData.create(self.client.session.session_key, 'no_such_user')
        self.request.META['HTTP_ACCEPT'] = 'text/html'
        with self.assert_incorrect_user_logged():
            self.assert_response(safe_cookie_data, success=False)
        self.assert_user_in_session()


@ddt.ddt
class TestSafeSessionProcessResponse(TestSafeSessionsLogMixin, TestCase):
    """
    Test class for SafeSessionMiddleware.process_response
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.addCleanup(set_current_request, None)
        self.request = get_mock_request()
        self.request.session = {}
        self.client.response = HttpResponse()
        self.client.response.cookies = SimpleCookie()

    def assert_response(self, set_request_user=False, set_session_cookie=False):
        """
        Calls SafeSessionMiddleware.process_response and verifies
        the response.

        Arguments:
            set_request_user - If True, the user is set on the request
                object.
            set_session_cookie - If True, a session_id is set in the
                session cookie in the response.
        """
        if set_request_user:
            self.request.user = self.user
            SafeSessionMiddleware.set_user_id_in_session(self.request, self.user)
        if set_session_cookie:
            self.client.response.cookies[settings.SESSION_COOKIE_NAME] = "some_session_id"

        response = SafeSessionMiddleware(get_response=lambda request: None).process_response(
            self.request, self.client.response
        )
        assert response.status_code == 200

    def assert_response_with_delete_cookie(
            self,
            expect_delete_called=True,
            set_request_user=False,
            set_session_cookie=False,
    ):
        """
        Calls SafeSessionMiddleware.process_response and verifies
        the response, while expecting the cookie to be deleted if
        expect_delete_called is True.

        See assert_response for information on the other
        parameters.
        """
        with patch('django.http.HttpResponse.delete_cookie') as mock_delete_cookie:
            self.assert_response(set_request_user=set_request_user, set_session_cookie=set_session_cookie)
            assert {'sessionid', 'edx-jwt-cookie-header-payload'} \
                <= {call.args[0] for call in mock_delete_cookie.call_args_list}

    def test_success(self):
        with self.assert_not_logged():
            self.assert_response(set_request_user=True, set_session_cookie=True)

    # error case is tested in TestSafeSessionMiddleware since it requires more setup
    def test_confirm_user_at_step_2(self):
        self.request.safe_cookie_verified_user_id = self.user.id
        with self.assert_not_logged():
            self.assert_response(set_request_user=True, set_session_cookie=True)

    def test_anonymous_user(self):
        self.request.safe_cookie_verified_user_id = self.user.id
        self.request.safe_cookie_verified_session_id = '1'
        self.request.user = AnonymousUser()
        self.request.session[SESSION_KEY] = self.user.id
        with self.assert_no_warning_logged():
            self.assert_response(set_request_user=False, set_session_cookie=True)

    def test_update_cookie_data_at_step_3(self):
        self.assert_response(set_request_user=True, set_session_cookie=True)

        serialized_cookie_data = self.client.response.cookies[settings.SESSION_COOKIE_NAME].value
        safe_cookie_data = SafeCookieData.parse(serialized_cookie_data)
        assert safe_cookie_data.version == SafeCookieData.CURRENT_VERSION
        assert safe_cookie_data.session_id == 'some_session_id'
        assert safe_cookie_data.verify(self.user.id)

    def test_update_cookie_data_at_step_3_with_sha256(self):
        """ first encode cookie with default algo sha1 and then check with sha256"""
        self.assert_response(set_request_user=True, set_session_cookie=True)
        serialized_cookie_data = self.client.response.cookies[settings.SESSION_COOKIE_NAME].value
        safe_cookie_data = SafeCookieData.parse(serialized_cookie_data)
        assert safe_cookie_data.version == SafeCookieData.CURRENT_VERSION
        assert safe_cookie_data.session_id == 'some_session_id'
        assert safe_cookie_data.verify(self.user.id)

    def test_cant_update_cookie_at_step_3_error(self):
        self.client.response.cookies[settings.SESSION_COOKIE_NAME] = None
        with self.assert_invalid_session_id():
            self.assert_response_with_delete_cookie(set_request_user=True)

    @ddt.data(True, False)
    def test_deletion_of_cookies_at_step_4(self, set_request_user):
        self.request.need_to_delete_cookie = True
        self.assert_response_with_delete_cookie(set_session_cookie=True, set_request_user=set_request_user)

    def test_deletion_of_no_cookies_at_step_4(self):
        self.request.need_to_delete_cookie = True
        # delete_cookies is called even if there are no cookies set
        self.assert_response_with_delete_cookie()


@ddt.ddt
class TestSafeSessionMiddleware(TestSafeSessionsLogMixin, CacheIsolationTestCase):
    """
    Test class for SafeSessionMiddleware, testing both
    process_request and process_response.
    """

    def setUp(self):
        super().setUp()
        self.TEST_PASSWORD = 'Password1234'
        self.user = UserFactory.create(password=self.TEST_PASSWORD)
        self.addCleanup(set_current_request, None)
        self.request = get_mock_request()
        self.client.response = HttpResponse()
        self.client.response.cookies = SimpleCookie()

    def cookies_from_request_to_response(self):
        """
        Transfers the cookies from the request object to the response
        object.
        """
        if self.request.COOKIES.get(settings.SESSION_COOKIE_NAME):
            self.client.response.cookies[settings.SESSION_COOKIE_NAME] = self.request.COOKIES[
                settings.SESSION_COOKIE_NAME
            ]

    def set_up_for_success(self):
        """
        Set up request for success path -- everything up until process_response().
        """
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

        session_id = self.client.session.session_key
        safe_cookie_data = SafeCookieData.create(session_id, self.user.id)
        self.request.COOKIES[settings.SESSION_COOKIE_NAME] = str(safe_cookie_data)

        with self.assert_not_logged():
            response = SafeSessionMiddleware(get_response=lambda request: None).process_request(self.request)
            # Note: setting the user here is later than it really happens, but it enables a
            #   semi-accurate user change tracking. The only issue is that it changes from
            #   None to user, rather than being logged as the first time request.user is set,
            #   as actually happens in Production.
            self.request.user = self.user
        assert response is None

        assert self.request.safe_cookie_verified_user_id == self.user.id
        self.cookies_from_request_to_response()

    def verify_success(self):
        """
        Verifies success path.
        """
        self.set_up_for_success()

        with self.assert_not_logged():
            response = SafeSessionMiddleware(get_response=lambda request: None).process_response(
                self.request, self.client.response
            )
        assert response.status_code == 200

    def test_success(self):
        self.verify_success()

    def test_success_from_mobile_web_view(self):
        self.request.path = '/xblock/block-v1:org+course+run+type@html+block@block_id'
        self.verify_success()

    @override_settings(MOBILE_APP_USER_AGENT_REGEXES=[r'Open edX Mobile App'])
    def test_success_from_mobile_app(self):
        self.request.META = {'HTTP_USER_AGENT': 'Open edX Mobile App Version 2.1'}
        self.verify_success()

    def verify_error(self, expected_response_status):
        """
        Verifies error path.
        """
        self.request.COOKIES[settings.SESSION_COOKIE_NAME] = 'not-a-safe-cookie'
        self.request.session = self.client.session

        with self.assert_parse_error():
            request_response = SafeSessionMiddleware(get_response=lambda request: None).process_request(
                self.request
            )
            assert request_response.status_code == expected_response_status

        assert self.request.need_to_delete_cookie
        self.cookies_from_request_to_response()

        with patch('django.http.HttpResponse.delete_cookie') as mock_delete_cookie:
            SafeSessionMiddleware(get_response=lambda request: None).process_response(
                self.request, self.client.response
            )
            assert {'sessionid', 'edx-jwt-cookie-header-payload'} \
                <= {call.args[0] for call in mock_delete_cookie.call_args_list}

    def test_error(self):
        self.request.META['HTTP_ACCEPT'] = 'text/html'
        self.verify_error(302)

    @ddt.data(['text/html', 302], ['', 401])
    @ddt.unpack
    def test_error_with_http_accept(self, http_accept, expected_response):
        self.request.META['HTTP_ACCEPT'] = http_accept
        self.verify_error(expected_response)

    @override_settings(MOBILE_APP_USER_AGENT_REGEXES=[r'Open edX Mobile App'])
    def test_error_from_mobile_app(self):
        self.request.META = {'HTTP_USER_AGENT': 'Open edX Mobile App Version 2.1'}
        self.verify_error(401)

    @override_settings(ENFORCE_SAFE_SESSIONS=False)
    def test_warn_on_user_change_before_response(self):
        """
        Verifies that when enforcement disabled, warnings are emitted and custom attributes set if
        the user changes unexpectedly between request and response.
        """
        self.set_up_for_success()

        # But then user changes unexpectedly
        self.request.user = UserFactory.create()

        with self.assert_logged_for_request_user_mismatch(self.user.id, self.request.user.id, 'warning', '/', False):
            with patch('openedx.core.djangoapps.safe_sessions.middleware.set_custom_attribute') as mock_attr:
                response = SafeSessionMiddleware(get_response=lambda request: None).process_response(
                    self.request, self.client.response
                )
        assert response.status_code == 200
        set_attr_call_args = [call.args for call in mock_attr.call_args_list]
        assert ("safe_sessions.user_mismatch", "request-response-mismatch") in set_attr_call_args

    def test_enforce_on_user_change_before_response(self):
        """
        Copy of test_warn_on_user_change_before_response but with enforcement enabled (default).
        The differences should be the status code and the session deletion.
        """
        self.set_up_for_success()

        assert SafeSessionMiddleware.get_user_id_from_session(self.request) is not None

        # But then user changes unexpectedly
        self.request.user = UserFactory.create()

        with self.assert_logged_for_request_user_mismatch(self.user.id, self.request.user.id, 'warning', '/', False):
            with patch('openedx.core.djangoapps.safe_sessions.middleware.set_custom_attribute') as mock_attr:
                response = SafeSessionMiddleware(get_response=lambda request: None).process_response(
                    self.request, self.client.response
                )
        assert response.status_code == 401
        assert SafeSessionMiddleware.get_user_id_from_session(self.request) is None  # session cleared
        set_attr_call_args = [call.args for call in mock_attr.call_args_list]
        assert ("safe_sessions.user_mismatch", "request-response-mismatch") in set_attr_call_args

    @override_settings(ENFORCE_SAFE_SESSIONS=False)
    def test_warn_on_user_change_from_session(self):
        """
        Verifies that warnings are emitted and custom attributes set if
        the user in the request does not match the user in the session.
        """
        self.set_up_for_success()
        different_user = UserFactory()
        SafeSessionMiddleware.set_user_id_in_session(self.request, different_user)
        with self.assert_logged_for_session_user_mismatch(self.user.id, different_user.id, self.request.path,
                                                          False):
            with patch('openedx.core.djangoapps.safe_sessions.middleware.set_custom_attribute') as mock_attr:
                response = SafeSessionMiddleware(get_response=lambda request: None).process_response(
                    self.request, self.client.response
                )
        assert response.status_code == 200
        set_attr_call_args = [call.args for call in mock_attr.call_args_list]
        assert ("safe_sessions.user_mismatch", "request-session-mismatch") in set_attr_call_args

    @override_settings(ENFORCE_SAFE_SESSIONS=False)
    def test_warn_on_user_change_in_both(self):
        """
        Verifies that warnings are emitted and custom attributes set if
        the user in the initial request does not match the user at response time or the user in the session.
        """
        self.set_up_for_success()
        different_user = UserFactory()
        SafeSessionMiddleware.set_user_id_in_session(self.request, different_user)
        self.request.user = UserFactory.create()
        with self.assert_logged_for_both_mismatch(self.user.id, different_user.id,
                                                  self.request.user.id, self.request.path, False):
            with patch('openedx.core.djangoapps.safe_sessions.middleware.set_custom_attribute') as mock_attr:
                response = SafeSessionMiddleware(get_response=lambda request: None).process_response(
                    self.request, self.client.response
                )
        assert response.status_code == 200
        set_attr_call_args = [call.args for call in mock_attr.call_args_list]
        assert ("safe_sessions.user_mismatch", "request-response-and-session-mismatch") in set_attr_call_args

    @patch("openedx.core.djangoapps.safe_sessions.middleware.LOG_REQUEST_USER_CHANGES", True)
    @patch("openedx.core.djangoapps.safe_sessions.middleware.set_custom_attribute")
    def test_warn_with_verbose_logging(self, mock_set_custom_attribute):
        self.set_up_for_success()
        self.request.user = UserFactory.create()
        with self.assert_logged('SafeCookieData: Changing request user. ', log_level='warning'):
            SafeSessionMiddleware(get_response=lambda request: None).process_response(
                self.request, self.client.response
            )
        mock_set_custom_attribute.assert_has_calls([call('safe_sessions.user_id_list', '1,2')])

    @patch("openedx.core.djangoapps.safe_sessions.middleware.LOG_REQUEST_USER_CHANGES", False)
    def test_warn_without_verbose_logging(self):
        self.set_up_for_success()
        self.request.user = UserFactory.create()
        with self.assert_regex_not_logged('SafeCookieData: Changing request user. ', log_level='warning'):
            SafeSessionMiddleware(get_response=lambda request: None).process_response(
                self.request, self.client.response
            )

    @override_settings(LOG_REQUEST_USER_CHANGE_HEADERS=True)
    @patch("openedx.core.djangoapps.safe_sessions.middleware.LOG_REQUEST_USER_CHANGES", True)
    @patch("openedx.core.djangoapps.safe_sessions.middleware.cache")
    def test_user_change_with_header_logging(self, mock_cache):
        self.set_up_for_success()
        self.request.user = UserFactory.create()
        with self.assert_logged('SafeCookieData: Changing request user. ', log_level='warning'):
            SafeSessionMiddleware(get_response=lambda request: None).process_response(
                self.request, self.client.response
            )
        # Note: Since the test cache is not retaining its values for some reason, we'll
        #   simply assert that the cache is set (here) and checked (below).
        mock_cache.set_many.assert_called_with(
            {
                'safe_sessions.middleware.recent_user_change_detected_1': True,
                'safe_sessions.middleware.recent_user_change_detected_2': True
            }, 300
        )

        # send successful request; request header should be logged for earlier mismatched user id
        self.set_up_for_success()
        SafeSessionMiddleware(get_response=lambda request: None).process_response(self.request, self.client.response)
        # Note: The test cache is not returning True because it is not retaining its values
        #   for some reason. Rather than asserting that we log the header appropriately, we'll
        #   simply verify that we are checking the cache.
        mock_cache.get.assert_called_with('safe_sessions.middleware.recent_user_change_detected_1', False)

    @override_settings(LOG_REQUEST_USER_CHANGE_HEADERS=True)
    @patch("openedx.core.djangoapps.safe_sessions.middleware.LOG_REQUEST_USER_CHANGES", True)
    def test_no_request_user_with_header_logging(self):
        """
        Test header logging enabled with request not containing a user object.

        Notes:
        * In Production, failures happened for some requests that did not have
            request.user set, so we test this case here.
        * An attempt at creating a unit test for an anonymous user started
            failing due to a missing request.session, which never happens in
            Production, so this case assumes a working session object.
        """
        self.request.session = MagicMock()
        del self.request.user
        with self.assert_not_logged():
            SafeSessionMiddleware(get_response=lambda request: None).process_response(
                self.request, self.client.response
            )

    def test_no_warn_on_expected_user_change(self):
        """
        Verifies that no warnings is emitted when the user change is expected.
        This might happen on a login, for example.
        """
        self.set_up_for_success()

        # User changes...
        new_user = UserFactory.create()
        self.request.user = new_user
        # ...but so does session, and view sets a flag to say it's OK.
        mark_user_change_as_expected(new_user.id)

        with self.assert_no_warning_logged():
            with patch('openedx.core.djangoapps.safe_sessions.middleware.set_custom_attribute') as mock_attr:
                response = SafeSessionMiddleware(get_response=lambda request: None).process_response(
                    self.request, self.client.response
                )
        assert response.status_code == 200
        assert 'safe_sessions.user_mismatch' not in [call.args[0] for call in mock_attr.call_args_list]


class TestTokenObscuring(TestCase):
    """
    Test the ability to obscure session IDs.
    """

    def test_obscure_none(self):
        """Doesn't break on None input."""
        assert obscure_token(None) is None

    def test_obscure_vary(self):
        """
        Verifies that input and SECRET_KEY both change output.

        The "expected" values here are computed and arbitrary; if the
        algorithm is updated, the expected values should also be
        updated. (Changing the algorithm will invalidate any stored
        data, but the docstring explicitly warns not to store these
        outputs anyhow.)
        """
        with override_settings(SECRET_KEY="FIRST SECRET"):
            # Same input twice, same output twice
            assert obscure_token('abcdef-123456') == '2d4260b0'
            assert obscure_token('abcdef-123456') == '2d4260b0'
            # Different input, different output
            assert obscure_token('zxcvbnm-000111') == '87978f29'

        # Different key, different output
        with override_settings(SECRET_KEY="SECOND SECRET"):
            assert obscure_token('abcdef-123456') == '7325d529'


@ddt.ddt
class TestTrackRequestUserChanges(TestCase):
    """
    Test the function that instruments a request object.

    Ensure that we are logging changes to the 'user' attribute and
    that the correct messages are written.
    """

    def test_initial_user_setting_tracking(self):
        request = get_mock_request()
        del request.user
        track_request_user_changes(request)
        request.user = UserFactory.create()

        assert "Setting for the first time" in request.debug_user_changes[0]

    def test_user_change_logging(self):
        request = get_mock_request()
        original_user = UserFactory.create()
        new_user = UserFactory.create()

        request.user = original_user
        track_request_user_changes(request)

        # Verify that we don't log if set to same as current value.
        request.user = original_user
        assert len(request.debug_user_changes) == 0

        # Verify logging on change.
        request.user = new_user
        assert len(request.debug_user_changes) == 1
        assert f"Changing request user. Originally {original_user.id!r}" in request.debug_user_changes[0]
        assert f"will become {new_user.id!r}" in request.debug_user_changes[0]

        # Verify change back logged.
        request.user = original_user
        assert len(request.debug_user_changes) == 2
        expected_msg = f"Originally {original_user.id!r}, now {new_user.id!r} and will become {original_user.id!r}"
        assert expected_msg in request.debug_user_changes[1]

    def test_user_change_with_no_ids(self):
        request = get_mock_request()
        del request.user

        track_request_user_changes(request)
        request.user = object()
        assert "Setting for the first time, but user has no id" in request.debug_user_changes[0]

        request.user = object()
        assert len(request.debug_user_changes) == 2
        assert "Changing request user but user has no id." in request.debug_user_changes[1]


@skip_unless_lms
class TestEmailChangeMiddleware(TestSafeSessionsLogMixin, TestCase):
    """
    Test class for EmailChangeMiddleware
    """

    def setUp(self):
        super().setUp()
        self.EMAIL = 'test@example.com'
        self.PASSWORD = 'Password1234'
        self.user = UserFactory.create(email=self.EMAIL, password=self.PASSWORD)
        self.addCleanup(set_current_request, None)
        self.request = get_mock_request(self.user)
        self.request.session = {}
        self.client.response = HttpResponse()
        self.client.response.cookies = SimpleCookie()
        self.addCleanup(RequestCache.clear_all_namespaces)

        self.login_url = reverse("user_api_login_session", kwargs={'api_version': 'v2'})
        self.register_url = reverse("user_api_registration_v2")
        self.dashboard_url = reverse('dashboard')

    @override_settings(ENFORCE_SESSION_EMAIL_MATCH=False)
    @patch('openedx.core.djangoapps.safe_sessions.middleware._mark_cookie_for_deletion')
    def test_process_request_user_not_authenticated_with_toggle_disabled(self, mock_mark_cookie_for_deletion):
        """
        Calls EmailChangeMiddleware.process_request when no user is authenticated
        and ENFORCE_SESSION_EMAIL_MATCH toggle is disabled.
        Verifies that session and cookies are not affected.
        """
        # Unauthenticated User
        self.request.user = AnonymousUser()

        # Call process_request without authenticating a user
        EmailChangeMiddleware(get_response=lambda request: None).process_request(self.request)

        # Assert that session and cookies are not affected
        # Assert that _mark_cookie_for_deletion not called
        mock_mark_cookie_for_deletion.assert_not_called()

    @override_settings(ENFORCE_SESSION_EMAIL_MATCH=True)
    @patch('openedx.core.djangoapps.safe_sessions.middleware._mark_cookie_for_deletion')
    def test_process_request_user_not_authenticated_with_toggle_enabled(self, mock_mark_cookie_for_deletion):
        """
        Calls EmailChangeMiddleware.process_request when no user is authenticated
        and ENFORCE_SESSION_EMAIL_MATCH toggle is enabled.
        Verifies that session and cookies are not affected.
        """
        # Unauthenticated User
        self.request.user = AnonymousUser()

        # Call process_request without authenticating a user
        EmailChangeMiddleware(get_response=lambda request: None).process_request(self.request)

        # Assert that session and cookies are not affected
        # Assert that _mark_cookie_for_deletion not called
        mock_mark_cookie_for_deletion.assert_not_called()

    @override_settings(ENFORCE_SESSION_EMAIL_MATCH=True)
    @patch('openedx.core.djangoapps.safe_sessions.middleware._mark_cookie_for_deletion')
    @patch("openedx.core.djangoapps.safe_sessions.middleware.set_custom_attribute")
    def test_process_request_emails_match_with_toggle_enabled(
        self, mock_set_custom_attribute, mock_mark_cookie_for_deletion
    ):
        """
        Calls EmailChangeMiddleware.process_request when user is authenticated,
        ENFORCE_SESSION_EMAIL_MATCH is enabled and user session and request email also match.
        Verifies that session and cookies are not affected.
        """
        # Log in the user
        self.client.login(email=self.user.email, password=self.PASSWORD)
        self.request.session = self.client.session
        self.client.response.set_cookie(settings.SESSION_COOKIE_NAME, 'authenticated')  # Add some logged-in cookie

        # Registering email change (store user's email in session for later comparison by
        # process_request function of middleware)
        EmailChangeMiddleware.register_email_change(request=self.request, email=self.user.email)

        # Ensure email is set in the session
        self.assertEqual(self.request.session.get('email'), self.user.email)
        # Ensure session cookie exist
        self.assertEqual(len(self.client.response.cookies), 1)

        # No email change occurred in any browser

        # Call process_request
        EmailChangeMiddleware(get_response=lambda request: None).process_request(self.request)

        # Verify that session_email_mismatch and is_enforce_session_email_match_enabled
        # custom attributes are set
        mock_set_custom_attribute.assert_has_calls([call('session_email_mismatch', False)])
        mock_set_custom_attribute.assert_has_calls([call('is_enforce_session_email_match_enabled', True)])

        # Assert that the session and cookies are not affected
        self.assertEqual(self.request.session.get('email'), self.user.email)
        self.assertEqual(len(self.client.response.cookies), 1)
        self.assertEqual(self.client.response.cookies[settings.SESSION_COOKIE_NAME].value, 'authenticated')

        # Assert that _mark_cookie_for_deletion not called
        mock_mark_cookie_for_deletion.assert_not_called()

    @override_settings(ENFORCE_SESSION_EMAIL_MATCH=False)
    @patch('openedx.core.djangoapps.safe_sessions.middleware._mark_cookie_for_deletion')
    @patch("openedx.core.djangoapps.safe_sessions.middleware.set_custom_attribute")
    def test_process_request_emails_match_with_toggle_disabled(
        self, mock_set_custom_attribute, mock_mark_cookie_for_deletion
    ):
        """
        Calls EmailChangeMiddleware.process_request when user is authenticated,
        ENFORCE_SESSION_EMAIL_MATCH is disabled and user session and request email match.
        Verifies that session and cookies are not affected.
        """
        # Log in the user
        self.client.login(email=self.user.email, password=self.PASSWORD)
        self.request.session = self.client.session
        self.client.response.set_cookie(settings.SESSION_COOKIE_NAME, 'authenticated')  # Add some logged-in cookie

        # Registering email change (store user's email in session for later comparison by
        # process_request function of middleware)
        EmailChangeMiddleware.register_email_change(request=self.request, email=self.user.email)

        # Ensure email is set in the session
        self.assertEqual(self.request.session.get('email'), self.user.email)
        # Ensure session cookie exist
        self.assertEqual(len(self.client.response.cookies), 1)

        # No email change occurred in any browser

        # Call process_request
        EmailChangeMiddleware(get_response=lambda request: None).process_request(self.request)

        # Verify that session_email_mismatch and is_enforce_session_email_match_enabled
        # custom attributes are set
        mock_set_custom_attribute.assert_has_calls([call('session_email_mismatch', False)])
        mock_set_custom_attribute.assert_has_calls([call('is_enforce_session_email_match_enabled', False)])

        # Assert that the session and cookies are not affected
        self.assertEqual(self.request.session.get('email'), self.user.email)
        self.assertEqual(len(self.client.response.cookies), 1)
        self.assertEqual(self.client.response.cookies[settings.SESSION_COOKIE_NAME].value, 'authenticated')

        # Assert that _mark_cookie_for_deletion not called
        mock_mark_cookie_for_deletion.assert_not_called()

    @override_settings(ENFORCE_SESSION_EMAIL_MATCH=True)
    @patch('openedx.core.djangoapps.safe_sessions.middleware._mark_cookie_for_deletion')
    @patch("openedx.core.djangoapps.safe_sessions.middleware.set_custom_attribute")
    def test_process_request_emails_mismatch_with_toggle_enabled(
        self, mock_set_custom_attribute, mock_mark_cookie_for_deletion
    ):
        """
        Calls EmailChangeMiddleware.process_request when user is authenticated,
        ENFORCE_SESSION_EMAIL_MATCH is enabled and user session and request
        email mismatch. (Email was changed in some other browser)
        Verifies that session is flushed and cookies are marked for deletion.
        """
        # Log in the user
        self.client.login(email=self.user.email, password=self.PASSWORD)
        self.request.session = self.client.session
        self.client.response.set_cookie(settings.SESSION_COOKIE_NAME, 'authenticated')  # Add some logged-in cookie

        # Registering email change (store user's email in session for later comparison by
        # process_request function of middleware)
        EmailChangeMiddleware.register_email_change(request=self.request, email=self.user.email)

        # Ensure email is set in the session
        self.assertEqual(self.request.session.get('email'), self.user.email)
        # Ensure session cookie exist
        self.assertEqual(len(self.client.response.cookies), 1)

        # simulating email changed in some other browser
        self.user.email = 'new_email@test.com'
        self.user.save()

        # Call process_request
        EmailChangeMiddleware(get_response=lambda request: None).process_request(self.request)

        # Verify that session_email_mismatch and is_enforce_session_email_match_enabled
        # custom attributes are set
        mock_set_custom_attribute.assert_has_calls([call('session_email_mismatch', True)])
        mock_set_custom_attribute.assert_has_calls([call('is_enforce_session_email_match_enabled', True)])

        # Assert that the session is flushed and cookies marked for deletion
        mock_mark_cookie_for_deletion.assert_called()
        assert self.request.session.get(SESSION_KEY) is None
        assert self.request.user == AnonymousUser()

    @override_settings(ENFORCE_SESSION_EMAIL_MATCH=False)
    @patch('openedx.core.djangoapps.safe_sessions.middleware._mark_cookie_for_deletion')
    @patch("openedx.core.djangoapps.safe_sessions.middleware.set_custom_attribute")
    def test_process_request_emails_mismatch_with_toggle_disabled(
        self, mock_set_custom_attribute, mock_mark_cookie_for_deletion
    ):
        """
        Calls EmailChangeMiddleware.process_request when user is authenticated,
        ENFORCE_SESSION_EMAIL_MATCH is disabled and user session and request
        email mismatch. (Email was changed in some other browser)
        Verifies that session and cookies are not affected.
        """
        # Log in the user
        self.client.login(email=self.user.email, password=self.PASSWORD)
        self.request.session = self.client.session
        self.client.response.set_cookie(settings.SESSION_COOKIE_NAME, 'authenticated')  # Add some logged-in cookie

        # Registering email change (store user's email in session for later comparison by
        # process_request function of middleware)
        EmailChangeMiddleware.register_email_change(request=self.request, email=self.user.email)

        # Ensure email is set in the session
        self.assertEqual(self.request.session.get('email'), self.user.email)
        # Ensure session cookie exist
        self.assertEqual(len(self.client.response.cookies), 1)

        # simulating email changed in some other browser
        self.user.email = 'new_email@test.com'
        self.user.save()

        # Call process_request
        EmailChangeMiddleware(get_response=lambda request: None).process_request(self.request)

        # Verify that session_email_mismatch and is_enforce_session_email_match_enabled
        # custom attributes are set
        mock_set_custom_attribute.assert_has_calls([call('session_email_mismatch', True)])
        mock_set_custom_attribute.assert_has_calls([call('is_enforce_session_email_match_enabled', False)])

        # Assert that the session and cookies are not affected
        self.assertNotEqual(self.request.session.get('email'), self.user.email)
        self.assertEqual(len(self.client.response.cookies), 1)
        self.assertEqual(self.client.response.cookies[settings.SESSION_COOKIE_NAME].value, 'authenticated')

        # Assert that _mark_cookie_for_deletion not called
        mock_mark_cookie_for_deletion.assert_not_called()

    @override_settings(ENFORCE_SESSION_EMAIL_MATCH=True)
    @patch('openedx.core.djangoapps.safe_sessions.middleware._mark_cookie_for_deletion')
    def test_process_request_no_email_change_history_with_toggle_enabled(
        self, mock_mark_cookie_for_deletion
    ):
        """
        Calls EmailChangeMiddleware.process_request when there is no previous
        history of an email change and ENFORCE_SESSION_EMAIL_MATCH is enabled
        Verifies that existing sessions are not affected.
        Test that sessions predating this code are not affected.
        """
        # Log in the user (Simulating user logged-in before this code and email was not set in session)
        self.client.login(email=self.user.email, password=self.PASSWORD)
        self.request.session = self.client.session
        self.client.response.set_cookie(settings.SESSION_COOKIE_NAME, 'authenticated')  # Add some logged-in cookie

        # Ensure there is no email in the session denoting no previous history of email change
        self.assertEqual(self.request.session.get('email'), None)

        # Ensure session cookie exist
        self.assertEqual(len(self.client.response.cookies), 1)

        # simulating email changed in some other browser
        self.user.email = 'new_email@test.com'
        self.user.save()

        # Call process_request
        EmailChangeMiddleware(get_response=lambda request: None).process_request(self.request)

        # Assert that the session and cookies are not affected
        self.assertEqual(len(self.client.response.cookies), 1)
        self.assertEqual(self.client.response.cookies[settings.SESSION_COOKIE_NAME].value, 'authenticated')

        # Assert that _mark_cookie_for_deletion not called
        mock_mark_cookie_for_deletion.assert_not_called()

    @override_settings(ENFORCE_SESSION_EMAIL_MATCH=False)
    @patch('openedx.core.djangoapps.safe_sessions.middleware._mark_cookie_for_deletion')
    def test_process_request_no_email_change_history_with_toggle_disabled(
        self, mock_mark_cookie_for_deletion
    ):
        """
        Calls EmailChangeMiddleware.process_request when there is no previous
        history of an email change and ENFORCE_SESSION_EMAIL_MATCH is disabled
        Verifies that existing sessions are not affected.
        Test that sessions predating this code are not affected.
        """
        # Log in the user (Simulating user logged-in before this code and email was not set in session)
        self.client.login(email=self.user.email, password=self.PASSWORD)
        self.request.session = self.client.session
        self.client.response.set_cookie(settings.SESSION_COOKIE_NAME, 'authenticated')  # Add some logged-in cookie

        # Ensure there is no email in the session denoting no previous history of email change
        self.assertEqual(self.request.session.get('email'), None)

        # Ensure session cookie exist
        self.assertEqual(len(self.client.response.cookies), 1)

        # simulating email changed in some other browser
        self.user.email = 'new_email@test.com'
        self.user.save()

        # Call process_request
        EmailChangeMiddleware(get_response=lambda request: None).process_request(self.request)

        # Assert that the session and cookies are not affected
        self.assertEqual(len(self.client.response.cookies), 1)
        self.assertEqual(self.client.response.cookies[settings.SESSION_COOKIE_NAME].value, 'authenticated')

        # Assert that _mark_cookie_for_deletion not called
        mock_mark_cookie_for_deletion.assert_not_called()

    @patch("openedx.core.djangoapps.safe_sessions.middleware.set_logged_in_cookies")
    def test_process_response_user_not_authenticated(self, mock_set_logged_in_cookies):
        """
        Calls EmailChangeMiddleware.process_response when user is not authenticated.
        Verify that the logged-in cookies are not updated
        """
        # return value of mock
        mock_set_logged_in_cookies.return_value = self.client.response

        # Unauthenticated User
        self.request.user = AnonymousUser()

        # Call process_response without authenticating a user
        response = EmailChangeMiddleware(get_response=lambda request: None).process_response(
            self.request, self.client.response
        )

        assert response.status_code == 200
        # Assert that cookies are not updated
        # Assert that mock_set_logged_in_cookies not called
        mock_set_logged_in_cookies.assert_not_called()

    @patch("openedx.core.djangoapps.safe_sessions.middleware.set_logged_in_cookies")
    def test_process_response_user_authenticated_but_email_change_not_requested(self, mock_set_logged_in_cookies):
        """
        Calls EmailChangeMiddleware.process_response when user is authenticated but email
        change was not requested.
        Verify that the logged-in cookies are not updated
        """
        # return value of mock
        mock_set_logged_in_cookies.return_value = self.client.response

        # Log in the user
        self.client.login(email=self.user.email, password=self.PASSWORD)
        self.request.session = self.client.session
        self.client.response.set_cookie(settings.SESSION_COOKIE_NAME, 'authenticated')  # Add some logged-in cookie

        # No call to register_email_change to indicate email was not changed

        # Call process_response
        response = EmailChangeMiddleware(get_response=lambda request: None).process_response(
            self.request, self.client.response
        )

        assert response.status_code == 200
        # Assert that cookies are not updated
        # Assert that mock_set_logged_in_cookies not called
        mock_set_logged_in_cookies.assert_not_called()

    @patch("openedx.core.djangoapps.safe_sessions.middleware.set_logged_in_cookies")
    def test_process_response_user_authenticated_and_email_change_requested(self, mock_set_logged_in_cookies):
        """
        Calls EmailChangeMiddleware.process_response when user is authenticated and email
        change was requested.
        Verify that the logged-in cookies are updated
        """
        # return value of mock
        mock_set_logged_in_cookies.return_value = self.client.response

        # Log in the user
        self.client.login(email=self.user.email, password=self.PASSWORD)
        self.request.session = self.client.session
        self.client.response.set_cookie(settings.SESSION_COOKIE_NAME, 'authenticated')  # Add some logged-in cookie

        # Registering email change (setting a variable `email_change_requested` to indicate email was changed)
        # so that process_response can update cookies
        EmailChangeMiddleware.register_email_change(request=self.request, email=self.user.email)

        # Call process_response
        response = EmailChangeMiddleware(get_response=lambda request: None).process_response(
            self.request, self.client.response
        )

        assert response.status_code == 200
        # Assert that cookies are updated
        # Assert that mock_set_logged_in_cookies is called
        mock_set_logged_in_cookies.assert_called()

    def test_process_response_no_email_in_session(self):
        """
        Calls EmailChangeMiddleware.process_response when user is authenticated and
        user's email was not stored in user's session.
        Verify that the user's email is stored in session
        """
        # Log in the user
        self.client.login(email=self.user.email, password=self.PASSWORD)
        self.request.session = self.client.session
        self.client.response.set_cookie(settings.SESSION_COOKIE_NAME, 'authenticated')  # Add some logged-in cookie

        # Ensure there is no email in the session
        self.assertEqual(self.request.session.get('email'), None)

        # Call process_response
        response = EmailChangeMiddleware(get_response=lambda request: None).process_response(
            self.request, self.client.response
        )

        assert response.status_code == 200
        # Verify that email is set in the session
        self.assertEqual(self.request.session.get('email'), self.user.email)

    @patch.dict("django.conf.settings.FEATURES", {"DISABLE_SET_JWT_COOKIES_FOR_TESTS": False})
    def test_user_remain_authenticated_on_email_change_in_other_browser_with_toggle_disabled(self):
        """
        Integration Test: test that a user remains authenticated upon email change
        in other browser when ENFORCE_SESSION_EMAIL_MATCH toggle is disabled
        Verify that the session and cookies are not affected in current browser and
        user remains authenticated
        """
        setup_login_oauth_client()

        # Login the user with 'test@example.com` email and test password in current browser
        response = self.client.post(self.login_url, {
            "email_or_username": self.EMAIL,
            "password": self.PASSWORD,
        })
        # Verify that the user is logged in successfully in current browser
        assert response.status_code == 200
        # Verify that the logged-in cookies are set in current browser
        self._assert_logged_in_cookies_present(response)

        # Verify that the authenticated user can access the dashboard in current browser
        response = self.client.get(self.dashboard_url)
        assert response.status_code == 200

        # simulating email changed in some other browser (Email is changed in DB)
        self.user.email = 'new_email@test.com'
        self.user.save()

        # Verify that the user remains authenticated in current browser and can access the dashboard
        response = self.client.get(self.dashboard_url)
        assert response.status_code == 200

    @patch.dict("django.conf.settings.FEATURES", {"DISABLE_SET_JWT_COOKIES_FOR_TESTS": False})
    @override_settings(ENFORCE_SESSION_EMAIL_MATCH=True)
    def test_cookies_are_updated_with_new_email_on_email_change_with_toggle_enabled(self):
        """
        Integration Test: test that cookies are updated with new email upon email change
        in current browser regardless of toggle setting
        Verify that the cookies are updated in current browser and
        user remains authenticated
        """
        setup_login_oauth_client()

        # Login the user with 'test@example.com` email and test password in current browser
        login_response = self.client.post(self.login_url, {
            "email_or_username": self.EMAIL,
            "password": self.PASSWORD,
        })
        # Verify that the user is logged in successfully in current browser
        assert login_response.status_code == 200
        # Verify that the logged-in cookies are set in current browser
        self._assert_logged_in_cookies_present(login_response)

        # Verify that the authenticated user can access the dashboard in current browser
        response = self.client.get(self.dashboard_url)
        assert response.status_code == 200

        # simulating email change in current browser
        activation_key = uuid.uuid4().hex
        PendingEmailChange.objects.update_or_create(
            user=self.user,
            defaults={
                'new_email': 'new_email@test.com',
                'activation_key': activation_key,
            }
        )
        email_change_response = self.client.get(
            reverse('confirm_email_change', kwargs={'key': activation_key}),
        )

        # Verify that email change is successful
        assert email_change_response.status_code == 200
        self._assert_logged_in_cookies_present(email_change_response)

        # Verify that jwt cookies are updated with new email and
        # not equal to old logged-in cookies in current browser
        self.assertNotEqual(
            login_response.cookies[jwt_cookies.jwt_cookie_header_payload_name()].value,
            email_change_response.cookies[jwt_cookies.jwt_cookie_header_payload_name()].value
        )
        self.assertNotEqual(
            login_response.cookies[jwt_cookies.jwt_cookie_signature_name()].value,
            email_change_response.cookies[jwt_cookies.jwt_cookie_signature_name()].value
        )

    @patch.dict("django.conf.settings.FEATURES", {"DISABLE_SET_JWT_COOKIES_FOR_TESTS": False})
    @override_settings(ENFORCE_SESSION_EMAIL_MATCH=False)
    def test_cookies_are_updated_with_new_email_on_email_change_with_toggle_disabled(self):
        """
        Integration Test: test that cookies are updated with new email upon email change
        in current browser regardless of toggle setting
        Verify that the cookies are updated in current browser and
        user remains authenticated
        """
        setup_login_oauth_client()

        # Login the user with 'test@example.com` email and test password in current browser
        login_response = self.client.post(self.login_url, {
            "email_or_username": self.EMAIL,
            "password": self.PASSWORD,
        })
        # Verify that the user is logged in successfully in current browser
        assert login_response.status_code == 200
        # Verify that the logged-in cookies are set in current browser
        self._assert_logged_in_cookies_present(login_response)

        # Verify that the authenticated user can access the dashboard in current browser
        response = self.client.get(self.dashboard_url)
        assert response.status_code == 200

        # simulating email change in current browser
        activation_key = uuid.uuid4().hex
        PendingEmailChange.objects.update_or_create(
            user=self.user,
            defaults={
                'new_email': 'new_email@test.com',
                'activation_key': activation_key,
            }
        )
        email_change_response = self.client.get(
            reverse('confirm_email_change', kwargs={'key': activation_key}),
        )

        # Verify that email change is successful
        assert email_change_response.status_code == 200
        self._assert_logged_in_cookies_present(email_change_response)

        # Verify that jwt cookies are updated with new email and
        # not equal to old logged-in cookies in current browser
        self.assertNotEqual(
            login_response.cookies[jwt_cookies.jwt_cookie_header_payload_name()].value,
            email_change_response.cookies[jwt_cookies.jwt_cookie_header_payload_name()].value
        )
        self.assertNotEqual(
            login_response.cookies[jwt_cookies.jwt_cookie_signature_name()].value,
            email_change_response.cookies[jwt_cookies.jwt_cookie_signature_name()].value
        )

    @patch.dict("django.conf.settings.FEATURES", {"DISABLE_SET_JWT_COOKIES_FOR_TESTS": False})
    @override_settings(ENFORCE_SESSION_EMAIL_MATCH=True)
    def test_logged_in_user_unauthenticated_on_email_change_in_other_browser(self):
        """
        Integration Test: Test that a user logged-in in one browser gets unauthenticated
        when the email is changed in some other browser and the request and session emails mismatch.
        Verify that the session is invalidated and cookies are deleted in current browser
        and user gets unauthenticated.
        """
        setup_login_oauth_client()

        # Login the user with 'test@example.com` email and test password in current browser
        response = self.client.post(self.login_url, {
            "email_or_username": self.EMAIL,
            "password": self.PASSWORD,
        })
        # Verify that the user is logged in successfully in current browser
        assert response.status_code == 200
        # Verify that the logged-in cookies are set in current browser
        self._assert_logged_in_cookies_present(response)

        # Verify that the authenticated user can access the dashboard in current browser
        response = self.client.get(self.dashboard_url)
        assert response.status_code == 200

        # simulating email changed in some other browser (Email is changed in DB)
        self.user.email = 'new_email@test.com'
        self.user.save()

        # Verify that the user gets unauthenticated in current browser and cannot access the dashboard
        response = self.client.get(self.dashboard_url)
        assert response.status_code == 302
        self._assert_logged_in_cookies_not_present(response)

    @patch.dict("django.conf.settings.FEATURES", {"DISABLE_SET_JWT_COOKIES_FOR_TESTS": False})
    @override_settings(ENFORCE_SESSION_EMAIL_MATCH=True)
    def test_logged_in_user_remains_authenticated_on_email_change_in_same_browser(self):
        """
        Integration Test: test that a user logged-in in some browser remains authenticated
        when the email is changed in same browser.
        Verify that the session and cookies are updated in current browser and
        user remains authenticated
        """
        setup_login_oauth_client()

        # Login the user with 'test@example.com` email and test password in current browser
        response = self.client.post(self.login_url, {
            "email_or_username": self.EMAIL,
            "password": self.PASSWORD,
        })
        # Verify that the user is logged in successfully in current browser
        assert response.status_code == 200
        # Verify that the logged-in cookies are set in current browser
        self._assert_logged_in_cookies_present(response)

        # Verify that the authenticated user can access the dashboard in current browser
        response = self.client.get(self.dashboard_url)
        assert response.status_code == 200

        # simulating email change in current browser
        activation_key = uuid.uuid4().hex
        PendingEmailChange.objects.update_or_create(
            user=self.user,
            defaults={
                'new_email': 'new_email@test.com',
                'activation_key': activation_key,
            }
        )
        email_change_response = self.client.get(
            reverse('confirm_email_change', kwargs={'key': activation_key}),
        )

        # Verify that email change is successful and all logged-in
        # cookies are set in current browser
        assert email_change_response.status_code == 200
        self._assert_logged_in_cookies_present(email_change_response)

        # Verify that the user remains authenticated in current browser and can access the dashboard
        response = self.client.get(self.dashboard_url)
        assert response.status_code == 200

    @patch.dict("django.conf.settings.FEATURES", {"DISABLE_SET_JWT_COOKIES_FOR_TESTS": False})
    @override_settings(ENFORCE_SESSION_EMAIL_MATCH=True)
    def test_registered_user_unauthenticated_on_email_change_in_other_browser(self):
        """
        Integration Test: Test that a user registered in one browser gets unauthenticated
        when the email is changed in some other browser and the request and session emails mismatch.
        Verify that the session is invalidated and cookies are deleted in current browser
        and user gets unauthenticated
        """
        setup_login_oauth_client()

        # Register the user with 'john_doe@example.com` email and test password in current browser
        response = self.client.post(self.register_url, {
            "email": 'john_doe@example.com',
            "name": 'John Doe',
            "username": 'john_doe',
            "password": 'password',
            "honor_code": "true",
        })
        # Verify that the user is logged in successfully in current browser
        assert response.status_code == 200
        # Verify that the logged-in cookies are set in current browser
        self._assert_logged_in_cookies_present(response)

        # Verify that the authenticated user can access the dashboard in current browser
        response = self.client.get(self.dashboard_url)
        assert response.status_code == 200

        # simulating email changed in some other browser (Email is changed in DB)
        registered_user = User.objects.get(email='john_doe@example.com')
        registered_user.email = 'new_email@test.com'
        registered_user.save()

        # Verify that the user get unauthenticated in current browser and cannot access the dashboard
        response = self.client.get(self.dashboard_url)
        assert response.status_code == 302
        self._assert_logged_in_cookies_not_present(response)

    @patch.dict("django.conf.settings.FEATURES", {"DISABLE_SET_JWT_COOKIES_FOR_TESTS": False})
    @override_settings(ENFORCE_SESSION_EMAIL_MATCH=True)
    def test_registered_user_remain_authenticated_on_email_change_in_same_browser(self):
        """
        Integration Test: test that a user registered in one browser remains
        authenticated in current browser when the email is changed in same browser.
        Verify that the session and cookies updated and user remains
        authenticated in current browser
        """
        setup_login_oauth_client()

        # Register the user with 'john_doe@example.com` email and test password in current browser
        response = self.client.post(self.register_url, {
            "email": 'john_doe@example.com',
            "name": 'John Doe',
            "username": 'john_doe',
            "password": 'password',
            "honor_code": "true",
        })
        # Verify that the user is logged in successfully in current browser
        assert response.status_code == 200
        # Verify that the logged-in cookies are set in current browser
        self._assert_logged_in_cookies_present(response)

        # Verify that the authenticated user can access the dashboard in current browser
        response = self.client.get(self.dashboard_url)
        assert response.status_code == 200

        # getting newly created user
        registered_user = User.objects.get(email='john_doe@example.com')

        # simulating email change in current browser
        activation_key = uuid.uuid4().hex
        PendingEmailChange.objects.update_or_create(
            user=registered_user,
            defaults={
                'new_email': 'new_email@test.com',
                'activation_key': activation_key,
            }
        )
        email_change_response = self.client.get(
            reverse('confirm_email_change', kwargs={'key': activation_key}),
        )

        # Verify that email change is successful and all logged-in
        # cookies are updated with new email in current browser
        assert email_change_response.status_code == 200
        self._assert_logged_in_cookies_present(email_change_response)

        # Verify that the user remains authenticated in current browser and can access the dashboard
        response = self.client.get(self.dashboard_url)
        assert response.status_code == 200

    def _assert_logged_in_cookies_present(self, response):
        """
        Helper function to verify that all logged-in cookies are available
        and have valid values (not empty strings)
        """
        all_cookies = ALL_LOGGED_IN_COOKIE_NAMES + (settings.SESSION_COOKIE_NAME,)

        for cookie in all_cookies:
            # Check if the cookie is present in response.cookies.keys()
            self.assertIn(cookie, response.cookies.keys())

            # Assert that the value is not an empty string
            self.assertNotEqual(response.cookies[cookie].value, "")

    def _assert_logged_in_cookies_not_present(self, response):
        """
        Helper function to verify that all logged-in cookies are cleared
        and have empty values
        """
        all_cookies = ALL_LOGGED_IN_COOKIE_NAMES + (settings.SESSION_COOKIE_NAME,)

        for cookie in all_cookies:
            # Check if the cookie is present in response.cookies.keys()
            self.assertIn(cookie, response.cookies.keys())

            # Assert that the value is not an empty string
            self.assertEqual(response.cookies[cookie].value, "")
