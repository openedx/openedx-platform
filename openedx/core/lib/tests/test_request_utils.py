"""Tests for request_utils module."""

import unittest
from unittest.mock import Mock, call, patch

import ddt
import pytest
from django.conf import settings
from django.core.exceptions import SuspiciousOperation
from django.test.client import RequestFactory
from django.test.utils import override_settings
from edx_django_utils.cache import RequestCache

from openedx.core.lib.request_utils import (
    IgnoredErrorMiddleware,
    _get_ignored_error_settings_dict,
    clear_cached_ignored_error_settings,
    course_id_from_url,
    get_request_or_stub,
    ignored_error_exception_handler,
    safe_get_host,
)


class RequestUtilTestCase(unittest.TestCase):
    """
    Tests for request_utils module.
    """
    def setUp(self):
        super().setUp()
        self.old_site_name = settings.SITE_NAME
        self.old_allowed_hosts = settings.ALLOWED_HOSTS

    def tearDown(self):
        super().tearDown()
        settings.SITE_NAME = self.old_site_name
        settings.ALLOWED_HOSTS = self.old_allowed_hosts

    def test_get_request_or_stub(self):
        """
        Outside the context of the request, we should still get a request
        that allows us to build an absolute URI.
        """
        stub = get_request_or_stub()
        expected_url = f"http://{settings.SITE_NAME}/foobar"
        assert stub.build_absolute_uri('foobar') == expected_url

    def test_safe_get_host(self):
        """ Tests that the safe_get_host function returns the desired host """
        settings.SITE_NAME = 'siteName.com'
        factory = RequestFactory()
        request = factory.request()
        request.META['HTTP_HOST'] = 'www.userProvidedHost.com'
        # If ALLOWED_HOSTS is not set properly, safe_get_host should return SITE_NAME
        settings.ALLOWED_HOSTS = None
        assert safe_get_host(request) == 'siteName.com'
        settings.ALLOWED_HOSTS = ["*"]
        assert safe_get_host(request) == 'siteName.com'
        settings.ALLOWED_HOSTS = ["foo.com", "*"]
        assert safe_get_host(request) == 'siteName.com'

        # If ALLOWED_HOSTS is set properly, and the host is valid, we just return the user-provided host
        settings.ALLOWED_HOSTS = [request.META['HTTP_HOST']]
        assert safe_get_host(request) == request.META['HTTP_HOST']

        # If ALLOWED_HOSTS is set properly but the host is invalid, we should get a SuspiciousOperation
        settings.ALLOWED_HOSTS = ["the_valid_website.com"]
        with pytest.raises(SuspiciousOperation):
            safe_get_host(request)

    def test_course_id_from_url(self):
        """ Test course_id_from_url(). """

        assert course_id_from_url('/login') is None
        assert course_id_from_url('/courses/edX/maths/') is None
        assert course_id_from_url('/api/courses/v1/blocks/edX/maths/2020') is None
        assert course_id_from_url('/api/courses/v1/blocks/course-v1:incidental+courseid+formatting') is None
        assert course_id_from_url('/api/courses/v41/notcourses/course-v1:incidental+courseid+formatting') is None

        course_id = course_id_from_url('/courses/course-v1:edX+maths+2020')
        self.assertCourseIdFieldsMatch(course_id=course_id, org="edX", course='maths', run='2020')

        course_id = course_id_from_url('/course/course-v1:edX+maths+2020')
        self.assertCourseIdFieldsMatch(course_id=course_id, org="edX", course='maths', run='2020')

        course_id = course_id_from_url('/courses/edX/maths/2020')
        self.assertCourseIdFieldsMatch(course_id=course_id, org='edX', course='maths', run='2020')

        course_id = course_id_from_url('/course/edX/maths/2020')
        self.assertCourseIdFieldsMatch(course_id=course_id, org="edX", course='maths', run='2020')

        course_id = course_id_from_url('/api/courses/v1/courses/course-v1:edX+maths+2020')
        self.assertCourseIdFieldsMatch(course_id=course_id, org='edX', course='maths', run='2020')

        course_id = course_id_from_url('/api/courses/v1/courses/edX/maths/2020')
        self.assertCourseIdFieldsMatch(course_id=course_id, org='edX', course='maths', run='2020')

    def assertCourseIdFieldsMatch(self, course_id, org, course, run):
        """ Asserts that the passed-in course id matches the specified fields"""
        assert course_id.org == org
        assert course_id.course == course
        assert course_id.run == run


class TestGetIgnoredErrorSettingsDict(unittest.TestCase):
    """
    Tests for processing issues in _get_ignored_error_settings_dict()

    Note: Although this is a private method, we have broken out the testing of
      special cases so they don't have to be tested with the middleware and drf
      custom error handler.
    """
    def setUp(self):
        super().setUp()
        clear_cached_ignored_error_settings()

    def test_get_with_no_setting(self):
        ignored_error_settings_dict = _get_ignored_error_settings_dict()
        assert ignored_error_settings_dict == {}

    @override_settings(IGNORED_ERRORS=[])
    def test_get_with_empty_list_setting(self):
        ignored_error_settings_dict = _get_ignored_error_settings_dict()
        assert ignored_error_settings_dict == {}

    @patch('openedx.core.lib.request_utils.log')
    @override_settings(IGNORED_ERRORS=[{}])
    def test_get_with_missing_module_and_class(self, mock_logger):
        ignored_error_settings_dict = _get_ignored_error_settings_dict()
        mock_logger.error.assert_called_once_with(
            "Skipping IGNORED_ERRORS[%d] setting. 'MODULE_AND_CLASS' set to [%s] and should be module.Class, like "
            "'rest_framework.exceptions.PermissionDenied'.",
            0,
            None,
        )
        assert ignored_error_settings_dict == {}

    @patch('openedx.core.lib.request_utils.log')
    @override_settings(IGNORED_ERRORS=[
        {
            'MODULE_AND_CLASS': 'colon.separator.warning:Class',
            'REASON_IGNORED': 'Because',
        }
    ])
    def test_get_with_colon_in_class_and_module(self, mock_logger):
        ignored_error_settings_dict = _get_ignored_error_settings_dict()
        mock_logger.warning.assert_called_once_with(
            "Replacing ':' with '.' in IGNORED_ERRORS[%d]['MODULE_AND_CLASS'], which was set to %s. Note that "
            "monitoring and logging will not include the ':'.",
            0,
            'colon.separator.warning:Class',
        )
        assert 'colon.separator.warning.Class' in ignored_error_settings_dict

    @patch('openedx.core.lib.request_utils.log')
    @override_settings(IGNORED_ERRORS=[
        {
            'MODULE_AND_CLASS': 'valid.module.DuplicateClass',
            'REASON_IGNORED': 'Because'
        },
        {
            'MODULE_AND_CLASS': 'valid.module.DuplicateClass',
            'REASON_IGNORED': 'Because overridden'
        },
    ])
    def test_get_with_duplicate_class_and_module(self, mock_logger):
        ignored_error_settings_dict = _get_ignored_error_settings_dict()
        mock_logger.warning.assert_called_once_with(
            "IGNORED_ERRORS[%d] setting is overriding an earlier setting. 'MODULE_AND_CLASS' [%s] is defined "
            "multiple times.",
            1,
            'valid.module.DuplicateClass',
        )
        assert 'valid.module.DuplicateClass' in ignored_error_settings_dict
        assert ignored_error_settings_dict['valid.module.DuplicateClass']['reason_ignored'] == 'Because overridden'

    @patch('openedx.core.lib.request_utils.log')
    @override_settings(IGNORED_ERRORS=[{'MODULE_AND_CLASS': 'valid.module.and.class.ButMissingReason'}])
    def test_get_with_missing_reason(self, mock_logger):
        ignored_error_settings_dict = _get_ignored_error_settings_dict()
        mock_logger.error.assert_called_once_with(
            "Skipping IGNORED_ERRORS[%d] setting. 'REASON_IGNORED' is required to document why %s is an ignored "
            "error.",
            0, 'valid.module.and.class.ButMissingReason'
        )
        assert ignored_error_settings_dict == {}

    @patch('openedx.core.lib.request_utils.log')
    @override_settings(IGNORED_ERRORS=['not-a-dict'])
    def test_get_with_invalid_dict(self, mock_logger):
        ignored_error_settings_dict = _get_ignored_error_settings_dict()
        mock_logger.exception.assert_called_once_with(
            'Error processing setting IGNORED_ERRORS. AttributeError("\'str\' object has no attribute \'get\'")'
        )
        assert ignored_error_settings_dict == {}

    @override_settings(IGNORED_ERRORS=[{
        'MODULE_AND_CLASS': 'test.module.TestClass',
        'REASON_IGNORED': 'Because'
    }])
    def test_get_with_defaults(self):
        ignored_error_settings_dict = _get_ignored_error_settings_dict()
        assert ignored_error_settings_dict == {
            'test.module.TestClass': {
                'log_error': False,
                'log_stack_trace': False,
                'reason_ignored': 'Because'
            }
        }


class CustomError1(Exception):
    pass


class CustomError2(Exception):
    pass


@ddt.ddt
class TestIgnoredErrorMiddleware(unittest.TestCase):
    """
    Tests for IgnoredErrorMiddleware
    """
    def setUp(self):
        super().setUp()
        RequestCache.clear_all_namespaces()
        clear_cached_ignored_error_settings()
        self.mock_request = RequestFactory().get('/test')
        self.mock_exception = CustomError1('Test failure')

    def test_get_response(self):
        expected_response = Mock()

        middleware = IgnoredErrorMiddleware(lambda _: expected_response)
        response = middleware(self.mock_request)

        assert response == expected_response

    @patch('openedx.core.lib.request_utils.set_custom_attribute')
    @patch('openedx.core.lib.request_utils.log')
    def test_process_exception_no_ignored_errors(self, mock_logger, mock_set_custom_attribute):
        IgnoredErrorMiddleware('mock-response').process_exception(self.mock_request, self.mock_exception)

        mock_logger.info.assert_not_called()
        mock_set_custom_attribute.assert_not_called()

    @patch('openedx.core.lib.request_utils.set_custom_attribute')
    @patch('openedx.core.lib.request_utils.log')
    @ddt.data(None, [])
    def test_process_exception_with_empty_ignored_errors(
        self, ignored_errors_setting, mock_logger, mock_set_custom_attribute,
    ):
        with override_settings(IGNORED_ERRORS=ignored_errors_setting):
            IgnoredErrorMiddleware('mock-response').process_exception(self.mock_request, self.mock_exception)

        mock_logger.info.assert_not_called()
        mock_set_custom_attribute.assert_not_called()

    @override_settings(IGNORED_ERRORS=[{
        'MODULE_AND_CLASS': 'test.module.TestException',
        'REASON_IGNORED': 'Because',
    }])
    @patch('openedx.core.lib.request_utils.set_custom_attribute')
    @patch('openedx.core.lib.request_utils.log')
    def test_process_exception_not_matching_ignored_errors(self, mock_logger, mock_set_custom_attribute):
        IgnoredErrorMiddleware('mock-response').process_exception(self.mock_request, self.mock_exception)

        mock_logger.info.assert_not_called()
        mock_set_custom_attribute.assert_called_once_with('checked_error_ignored_from', 'middleware')

    @override_settings(IGNORED_ERRORS=[
        {
            'MODULE_AND_CLASS': 'test.module.TestException',
            'REASON_IGNORED': 'Because',
        },
        {
            'MODULE_AND_CLASS': 'openedx.core.lib.tests.test_request_utils.CustomError1',
            'REASON_IGNORED': 'Because',
        }
    ])
    @patch('openedx.core.lib.request_utils.set_custom_attribute')
    @patch('openedx.core.lib.request_utils.log')
    def test_process_exception_ignored_error_with_defaults(self, mock_logger, mock_set_custom_attribute):
        IgnoredErrorMiddleware('mock-response').process_exception(self.mock_request, self.mock_exception)

        mock_logger.info.assert_not_called()
        mock_set_custom_attribute.assert_has_calls(
            [
                call('checked_error_ignored_from', 'middleware'),
                call('error_ignored_class', 'openedx.core.lib.tests.test_request_utils.CustomError1'),
                call('error_ignored_message', 'Test failure'),
            ],
            any_order=True
        )

    @override_settings(IGNORED_ERRORS=[
        {
            'MODULE_AND_CLASS': 'openedx.core.lib.tests.test_request_utils.CustomError1',
            'REASON_IGNORED': 'Because',
        },
        {
            'MODULE_AND_CLASS': 'openedx.core.lib.tests.test_request_utils.CustomError2',
            'REASON_IGNORED': 'Because',
        }
    ])
    @patch('openedx.core.lib.request_utils.set_custom_attribute')
    @ddt.data(True, False)
    def test_process_exception_called_multiple_times(self, use_same_exception, mock_set_custom_attribute):
        mock_first_exception = self.mock_exception
        mock_second_exception = mock_first_exception if use_same_exception else CustomError2("Oops")

        IgnoredErrorMiddleware('mock-response').process_exception(self.mock_request, mock_first_exception)
        IgnoredErrorMiddleware('mock-response').process_exception(self.mock_request, mock_second_exception)

        expected_calls = [
            call('checked_error_ignored_from', 'middleware'),
            call('error_ignored_class', 'openedx.core.lib.tests.test_request_utils.CustomError1'),
            call('error_ignored_message', 'Test failure'),
            call('checked_error_ignored_from', 'middleware'),
        ]
        if use_same_exception:
            expected_calls += [call('checked_error_ignored_from', 'multiple')]
        else:
            expected_calls += [
                call('unexpected_multiple_exceptions', 'openedx.core.lib.tests.test_request_utils.CustomError1'),
            ]
        mock_set_custom_attribute.assert_has_calls(expected_calls)
        assert mock_set_custom_attribute.call_count == len(expected_calls)

    @override_settings(IGNORED_ERRORS=[{
        'MODULE_AND_CLASS': 'Exception',
        'REASON_IGNORED': 'Because',
    }])
    @patch('openedx.core.lib.request_utils.set_custom_attribute')
    def test_process_exception_with_plain_exception(self, mock_set_custom_attribute):
        mock_exception = Exception("Oops")
        IgnoredErrorMiddleware('mock-response').process_exception(self.mock_request, mock_exception)

        mock_set_custom_attribute.assert_has_calls([
            call('error_ignored_class', 'Exception'),
            call('error_ignored_message', 'Oops'),
        ])

    @patch('openedx.core.lib.request_utils.set_custom_attribute')
    @patch('openedx.core.lib.request_utils.log')
    @ddt.data(
        (False, True),  # skip logging
        (True, False),  # log without stacktrace
        (True, True),   # log with stacktrace
    )
    @ddt.unpack
    def test_process_exception_ignored_error_with_overrides(
        self, log_error, log_stack_trace, mock_logger, mock_set_custom_attribute,
    ):
        expected_class = 'openedx.core.lib.tests.test_request_utils.CustomError1'
        expected_message = 'Test failure'

        with override_settings(IGNORED_ERRORS=[{
            'MODULE_AND_CLASS': expected_class,
            'LOG_ERROR': log_error,
            'LOG_STACK_TRACE': log_stack_trace,
            'REASON_IGNORED': 'Because',
        }]):
            IgnoredErrorMiddleware('mock-response').process_exception(self.mock_request, self.mock_exception)

        if log_error:
            exc_info = self.mock_exception if log_stack_trace else None
            mock_logger.info.assert_called_once_with(
                'Ignored error %s: %s: seen for path %s', expected_class, expected_message, '/test', exc_info=exc_info
            )
        else:
            mock_logger.info.assert_not_called()
        mock_set_custom_attribute.assert_has_calls(
            [
                call('checked_error_ignored_from', 'middleware'),
            ],
            any_order=True
        )


@ddt.ddt
class TestIgnoredErrorExceptionHandler(unittest.TestCase):
    """
    Tests for ignored_error_exception_handler.

    Note: Only smoke tests the handler to not duplicate all testing in TestIgnoredErrorMiddleware.
    """
    def setUp(self):
        super().setUp()
        RequestCache.clear_all_namespaces()
        clear_cached_ignored_error_settings()
        self.mock_request = RequestFactory().get('/test')
        self.mock_exception = CustomError1('Test failure')

    @override_settings(IGNORED_ERRORS=[{
        'MODULE_AND_CLASS': 'openedx.core.lib.tests.test_request_utils.CustomError1',
        'LOG_ERROR': True,
        'REASON_IGNORED': 'Because',
    }])
    @patch('openedx.core.lib.request_utils.set_custom_attribute')
    @patch('openedx.core.lib.request_utils.log')
    @ddt.data(True, False)
    def test_handler_with_ignored_error(
        self, use_valid_context, mock_logger, mock_set_custom_attribute
    ):
        if use_valid_context:
            mock_context = {'request': self.mock_request}
            expected_request_path = '/test'
        else:
            mock_context = None
            expected_request_path = 'request-path-unknown'
        ignored_error_exception_handler(self.mock_exception, mock_context)

        expected_class = 'openedx.core.lib.tests.test_request_utils.CustomError1'
        expected_message = 'Test failure'
        mock_logger.info.assert_called_once_with(
            'Ignored error %s: %s: seen for path %s',
            expected_class,
            expected_message,
            expected_request_path,
            exc_info=None,
        )
        mock_set_custom_attribute.assert_has_calls(
            [
                call('checked_error_ignored_from', 'drf'),
                call('error_ignored_class', expected_class),
                call('error_ignored_message', expected_message),
            ],
            any_order=True
        )
