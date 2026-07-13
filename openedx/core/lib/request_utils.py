""" Utility functions related to HTTP requests """

import logging
import re
from urllib.parse import urlparse

import crum
from django.conf import settings
from django.test.client import RequestFactory
from edx_django_utils.cache import RequestCache
from edx_django_utils.monitoring import set_custom_attribute
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework.views import exception_handler

from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers

# accommodates course api urls, excluding any course api routes that do not fall under v*/courses, such as v1/blocks.
COURSE_REGEX = re.compile(fr'^(.*?/course(s)?/)(?!v[0-9]+/[^/]+){settings.COURSE_ID_PATTERN}')

log = logging.getLogger(__name__)


def get_request_or_stub():
    """
    Return the current request or a stub request.

    If called outside the context of a request, construct a fake
    request that can be used to build an absolute URI.

    This is useful in cases where we need to pass in a request object
    but don't have an active request (for example, in tests, celery tasks, and XBlocks).
    """
    request = crum.get_current_request()

    if request is None:

        # The settings SITE_NAME may contain a port number, so we need to
        # parse the full URL.
        full_url = f"http://{settings.SITE_NAME}"
        parsed_url = urlparse(full_url)

        # Construct the fake request.  This can be used to construct absolute
        # URIs to other paths.
        return RequestFactory(
            SERVER_NAME=parsed_url.hostname,
            SERVER_PORT=parsed_url.port or 80,
        ).get("/")

    else:
        return request


def safe_get_host(request):
    """
    Get the host name for this request, as safely as possible.

    If ALLOWED_HOSTS is properly set, this calls request.get_host;
    otherwise, this returns whatever settings.SITE_NAME is set to.

    This ensures we will never accept an untrusted value of get_host()
    """
    if isinstance(settings.ALLOWED_HOSTS, (list, tuple)) and '*' not in settings.ALLOWED_HOSTS:
        return request.get_host()
    else:
        return configuration_helpers.get_value('site_domain', settings.SITE_NAME)


def course_id_from_url(url: str | None) -> CourseKey | None:
    """
    Extracts and parses the course_id from the given `url` and returns a CourseKey.
    """
    if not url:
        return None

    match = COURSE_REGEX.match(url)

    if match is None:
        return None

    course_id = match.group('course_id')

    if course_id is None:
        return None

    try:
        return CourseKey.from_string(course_id)
    except InvalidKeyError:
        return None


def ignored_error_exception_handler(exc, context):
    """
    Replacement for DRF's default exception handler to enable observing ignored errors.

    In addition to the default behaviour, add logging and monitoring for ignored errors.
    """
    # Call REST framework's default exception handler first to get the standard error response.
    response = exception_handler(exc, context)

    try:
        request = context['request'] if 'request' in context else None
    except TypeError:  # when context is not iterable
        request = None

    _log_and_monitor_ignored_errors(request, exc, 'drf')
    return response


class IgnoredErrorMiddleware:
    """
    Middleware to add logging and monitoring for ignored errors.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        return response

    def process_exception(self, request, exception):
        """
        Add logging and monitoring of ignored errors.
        """
        _log_and_monitor_ignored_errors(request, exception, 'middleware')


# .. setting_name: IGNORED_ERRORS
# .. setting_default: None
# .. setting_description: Used to configure logging and monitoring for ignored errors.
#     This setting is configured of a list of dicts. See setting and toggle annotations for
#     ``IGNORED_ERRORS[N]['XXX']`` for details of each item in the dict.
#     If this setting is a non-empty list, all uncaught errors processed will get a ``checked_error_ignored_from``
#     attribute, whether they are ignored or not. See IGNORED_ERRORS[N]['MODULE_AND_CLASS'] annotation
#     for details of monitoring added if the error is to be ignored.
# .. setting_warning: We use Django Middleware and a DRF custom error handler to find uncaught errors. Some errors may
#     slip through the cracks, like ValidationError. Any error where ``checked_error_ignored_from IS NULL`` is
#     an error that was not processed.

# .. setting_name: IGNORED_ERRORS[N]['MODULE_AND_CLASS']
# .. setting_default: None
# .. setting_description: Required error module and class name that is ignored. For example,
#     ``rest_framework.exceptions.PermissionDenied``. If the current error matches the module and class
#      defined here, the middleware will add the custom attributes ``error_ignored_class`` and ``error_ignored_message``
#.     to help diagnose issues with ignored errors, since this data is not otherwise available.
#      For an example of ignoring errors in New Relic, see:
#      https://docs.newrelic.com/docs/agents/manage-apm-agents/agent-data/manage-errors-apm-collect-ignore-or-mark-expected/#ignore  pylint: disable=line-too-long,useless-suppression
#      To query for ignored errors, you would use ``error_ignored_class IS NOT NULL``.
# .. setting_warning: At this time, an error that matches won't actually be ignored. These settings should be set to match
#     the ignored error configuration found elsewhere, like in New Relic. When monitoring, no errors should ever have the attribute
#     ``error_ignored_class``. Only Transactions should have this custom attribute. If found for an error, it means we
#     are stating an error should be ignored when it is not actually configured as such, or the (New Relic) configuration is not
#     working.

# .. toggle_name: IGNORED_ERRORS[N]['LOG_ERROR']
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: If True, the error will be logged with a message like: "Ignored error ...".
# .. toggle_use_cases: opt_in
# .. toggle_creation_date: 2021-03-11

# .. toggle_name: IGNORED_ERRORS[N]['LOG_STACK_TRACE']
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: If True, the stacktrace will be included with the logging message.
# .. toggle_warning: Requires ``LOG_ERROR`` to be set to True, otherwise this value will be ignored.
# .. toggle_use_cases: opt_in
# .. toggle_creation_date: 2021-03-11

# .. setting_name: IGNORED_ERRORS[N]['REASON_IGNORED']
# .. setting_default: None
# .. setting_description: Required string explaining why the error is ignored for documentation
#     purposes.


# Warning: do not access this directly, but instead use _get_ignored_error_settings_dict.
# IGNORED_ERRORS Django setting is processed and stored as a dict keyed by ERROR_MODULE_AND_CLASS.
_IGNORED_ERROR_SETTINGS_DICT = None


def _get_ignored_error_settings_dict():
    """
    Returns a dict of dicts of ignored error settings used for logging and monitoring.

    The contents of the IGNORED_ERRORS Django Setting list is processed for efficient lookup by module.Class.

    Returns:
         (dict): dict of dicts, mapping module-and-class name to settings for proper handling of ignored errors.
           Keys of the inner dicts use the lowercase version of the related Django Setting (e.g. 'REASON_IGNORED' =>
           'reason_ignored').

    Example return value::

        {
            'rest_framework.exceptions:PermissionDenied': {
                'log_error': True,
                'log_stack_trace': True,
                'reason_ignored': 'In most cases, signifies a user was trying to do something they cannot do. '
                   'However, an overabundance could indicate a bug, which could be monitored for.'
            }
            ...
        }

    """
    global _IGNORED_ERROR_SETTINGS_DICT

    # Return cached processed mappings if already processed
    if _IGNORED_ERROR_SETTINGS_DICT is not None:
        return _IGNORED_ERROR_SETTINGS_DICT

    ignored_errors = getattr(settings, 'IGNORED_ERRORS', None)
    if ignored_errors is None:
        _IGNORED_ERROR_SETTINGS_DICT = {}
        return _IGNORED_ERROR_SETTINGS_DICT

    # Use temporary variable to build mappings to avoid multi-threading issue with a partially
    # processed map.  Worst case, it is processed more than once at start-up.
    ignored_error_settings_dict = {}

    try:
        for index, ignored_error in enumerate(ignored_errors):
            module_and_class = ignored_error.get('MODULE_AND_CLASS')
            processed_ignored_error = {
                'log_error': ignored_error.get('LOG_ERROR', False),
                'log_stack_trace': ignored_error.get('LOG_STACK_TRACE', False),
                'reason_ignored': ignored_error.get('REASON_IGNORED'),
            }

            # validate configuration
            if not isinstance(module_and_class, str):
                log.error(
                    "Skipping IGNORED_ERRORS[%d] setting. 'MODULE_AND_CLASS' set to [%s] and should be module.Class, "
                    "like 'rest_framework.exceptions.PermissionDenied'.",
                    index, module_and_class
                )
                continue
            if ':' in module_and_class:
                log.warning(
                    "Replacing ':' with '.' in IGNORED_ERRORS[%d]['MODULE_AND_CLASS'], which was set to %s. Note that "
                    "monitoring and logging will not include the ':'.",
                    index, module_and_class
                )
                module_and_class = module_and_class.replace(":", ".")
            if module_and_class in ignored_error_settings_dict:
                log.warning(
                    "IGNORED_ERRORS[%d] setting is overriding an earlier setting. 'MODULE_AND_CLASS' [%s] is defined "
                    "multiple times.",
                    index, module_and_class
                )
            if not processed_ignored_error['reason_ignored']:
                log.error(
                    "Skipping IGNORED_ERRORS[%d] setting. 'REASON_IGNORED' is required to document why %s is an "
                    "ignored error.",
                    index, module_and_class
                )
                continue
            ignored_error_settings_dict[module_and_class] = processed_ignored_error
    except Exception as e:  # pylint: disable=broad-except
        set_custom_attribute('ignored_errors_setting_misconfigured', repr(e))
        log.exception(f'Error processing setting IGNORED_ERRORS. {repr(e)}')

    _IGNORED_ERROR_SETTINGS_DICT = ignored_error_settings_dict
    return _IGNORED_ERROR_SETTINGS_DICT


def clear_cached_ignored_error_settings():
    """
    Clears the cached ignored error settings. Useful for testing.
    """
    global _IGNORED_ERROR_SETTINGS_DICT
    _IGNORED_ERROR_SETTINGS_DICT = None


def _log_and_monitor_ignored_errors(request, exception, caller):
    """
    Adds logging and monitoring for ignored errors as needed.

    Arguments:
        request: The request
        exception: The exception
        caller: Either 'middleware' or 'drf`
    """
    ignored_error_settings_dict = _get_ignored_error_settings_dict()
    if not ignored_error_settings_dict:
        return

    # 'module.Class', for example, 'django.core.exceptions.PermissionDenied'
    # Note: `Exception` itself doesn't have a module.
    exception_module = getattr(exception, '__module__', '')
    separator = '.' if exception_module else ''
    module_and_class = f'{exception_module}{separator}{exception.__class__.__name__}'

    # Set checked_error_ignored_from custom attribute to potentially help find issues where errors are never processed.
    set_custom_attribute('checked_error_ignored_from', caller)

    # check if we already added logging/monitoring from a different caller
    request_cache = RequestCache('openedx.core.lib.request_utils')
    cached_handled_exception = request_cache.get_cached_response('handled_exception')
    if cached_handled_exception.is_found:
        cached_module_and_class = cached_handled_exception.value
        # exception was already processed by a different caller
        if cached_handled_exception.value == module_and_class:
            set_custom_attribute('checked_error_ignored_from', 'multiple')
            return

        # We have confirmed using monitoring that it is very rare that middleware and drf handle different uncaught exceptions.
        # We will leave this attribute in place, but it is not worth investing in a workaround.
        set_custom_attribute('unexpected_multiple_exceptions', cached_module_and_class)
        log.warning(
            "Unexpected scenario where different exceptions are handled by _log_and_monitor_ignored_errors. "
            "See 'unexpected_multiple_exceptions' custom attribute. Skipping exception for %s.",
            module_and_class,
        )
        return
    request_cache.set('handled_exception', module_and_class)

    if module_and_class not in ignored_error_settings_dict:
        return

    exception_message = str(exception)

    # Additional error details are needed for ignored errors, because they are otherwise
    # not available by our monitoring system, because they have been ignored.
    set_custom_attribute('error_ignored_class', module_and_class)
    set_custom_attribute('error_ignored_message', exception_message)

    ignored_error_settings = ignored_error_settings_dict[module_and_class]

    if ignored_error_settings['log_error']:
        exc_info = exception if ignored_error_settings['log_stack_trace'] else None
        request_path = getattr(request, 'path', 'request-path-unknown')
        log.info(
            'Ignored error %s: %s: seen for path %s',
            module_and_class,
            exception_message,
            request_path,
            exc_info=exc_info,
        )
