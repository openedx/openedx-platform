"""
LTI Provider view functions
"""


import logging

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseBadRequest, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from common.djangoapps.edxmako.shortcuts import render_to_response
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey
from openedx_events.learning.data import LtiProviderLaunchData, LtiProviderLaunchParamsData, UserData, UserPersonalData
from openedx_events.learning.signals import LTI_PROVIDER_LAUNCH_SUCCESS

from common.djangoapps.util.views import add_p3p_header
from lms.djangoapps.lti_provider.models import LtiConsumer
from lms.djangoapps.lti_provider.outcomes import store_outcome_parameters
from lms.djangoapps.lti_provider.signature_validator import SignatureValidator
from lms.djangoapps.lti_provider.users import authenticate_lti_user
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.lib.url_utils import unquote_slashes

log = logging.getLogger("edx.lti_provider")


# LTI launch parameters that must be present for a successful launch
REQUIRED_PARAMETERS = [
    'roles', 'context_id', 'oauth_version', 'oauth_consumer_key',
    'oauth_signature', 'oauth_signature_method', 'oauth_timestamp',
    'oauth_nonce', 'user_id'
]

OPTIONAL_PARAMETERS = [
    'context_title', 'context_label', 'lis_result_sourcedid',
    'lis_outcome_service_url', 'tool_consumer_instance_guid',
    "lis_person_name_full", "lis_person_name_given", "lis_person_name_family",
    "lis_person_contact_email_primary",
]


@csrf_exempt
@add_p3p_header
def lti_launch(request, course_id, usage_id):
    """
    Endpoint for all requests to embed edX content via the LTI protocol. This
    endpoint will be called by a POST message that contains the parameters for
    an LTI launch (we support version 1.2 of the LTI specification):
        http://www.imsglobal.org/lti/ltiv1p2/ltiIMGv1p2.html

    An LTI launch is successful if:
        - The launch contains all the required parameters
        - The launch data is correctly signed using a known client key/secret
          pair
    """
    if not settings.FEATURES['ENABLE_LTI_PROVIDER']:
        log.info('LTI provider feature is disabled.')
        return HttpResponseForbidden()

    # Check the LTI parameters, and return 400 if any required parameters are
    # missing
    params = get_required_parameters(request.POST)
    if not params:
        log.info('Missing required LTI parameters in LTI request path: %s', request.path)
        return HttpResponseBadRequest()
    params.update(get_optional_parameters(request.POST))
    params.update(get_custom_parameters(request.POST))

    # Get the consumer information from either the instance GUID or the consumer
    # key
    try:
        lti_consumer = LtiConsumer.get_or_supplement(
            params.get('tool_consumer_instance_guid', None),
            params['oauth_consumer_key']
        )
    except LtiConsumer.DoesNotExist:
        log.error(
            'LTI consumer lookup failed because no matching consumer was found against '
            'consumer key: %s and instance GUID: %s for request path: %s',
            params['oauth_consumer_key'],
            params.get('tool_consumer_instance_guid', None),
            request.path
        )
        return HttpResponseForbidden()

    # Check the OAuth signature on the message
    if not SignatureValidator(lti_consumer).verify(request):
        log.error(
            'Invalid OAuth signature for LTI launch from request path: %s',
            request.path
        )
        return HttpResponseForbidden()

    # Add the course and usage keys to the parameters array
    try:
        course_key, usage_key = parse_course_and_usage_keys(course_id, usage_id)
    except InvalidKeyError:
        log.error(
            'Invalid course key %s or usage key %s from request path %s',
            course_id,
            usage_id,
            request.path
        )
        raise Http404()  # lint-amnesty, pylint: disable=raise-missing-from
    params['course_key'] = course_key
    params['usage_key'] = usage_key

    # Create an edX account if the user identified by the LTI launch doesn't have
    # one already, and log the edX account into the platform.
    try:
        user_id = params["user_id"]
        authenticate_lti_user(request, user_id, lti_consumer)
    except PermissionDenied:
        log.info(
            'LTI user authentication failed for user Id: %s from request path: %s',
            user_id,
            request.path
        )
        request.session.flush()
        context = {
            "login_link": request.build_absolute_uri(settings.LOGIN_URL),
            "allow_iframing": True,
            "disable_header": True,
            "disable_footer": True,
        }
        return render_to_response("lti_provider/user-auth-error.html", context)

    # Store any parameters required by the outcome service in order to report
    # scores back later. We know that the consumer exists, since the record was
    # used earlier to verify the oauth signature.
    store_outcome_parameters(params, request.user, lti_consumer)

    # Make a copy of params for the event signal, and remove sensitive oauth parameters.
    launch_params = params.copy()
    for key in list(launch_params.keys()):
        if key.startswith('oauth_'):
            launch_params.pop(key)

    LTI_PROVIDER_LAUNCH_SUCCESS.send_event(
        launch_data=LtiProviderLaunchData(
            user=UserData(
                pii=UserPersonalData(
                    username=request.user.username,
                    email=request.user.email,
                    name=request.user.profile.name,
                ),
                id=request.user.id,
                is_active=request.user.is_active,
            ),
            course_key=launch_params.pop("course_key"),
            usage_key=launch_params.pop("usage_key"),
            launch_params=LtiProviderLaunchParamsData(
                roles=launch_params.pop("roles"),
                context_id=launch_params.pop("context_id"),
                user_id=launch_params.pop("user_id"),
                extra_params={key: str(val) for key, val in launch_params.items()},
            ),
        )
    )

    return render_courseware(request, params['usage_key'])


def get_required_parameters(dictionary, additional_params=None):
    """
    Extract all required LTI parameters from a dictionary and verify that none
    are missing.

    :param dictionary: The dictionary that should contain all required parameters
    :param additional_params: Any expected parameters, beyond those required for
        the LTI launch.

    :return: A new dictionary containing all the required parameters from the
        original dictionary and additional parameters, or None if any expected
        parameters are missing.
    """
    params = {}
    additional_params = additional_params or []
    for key in REQUIRED_PARAMETERS + additional_params:
        if key not in dictionary:
            return None
        params[key] = dictionary[key]
    return params


def get_optional_parameters(dictionary):
    """
    Extract all optional LTI parameters from a dictionary. This method does not
    fail if any parameters are missing.

    :param dictionary: A dictionary containing zero or more optional parameters.
    :return: A new dictionary containing all optional parameters from the
        original dictionary, or an empty dictionary if no optional parameters
        were present.
    """
    return {key: dictionary[key] for key in OPTIONAL_PARAMETERS if key in dictionary}


def get_custom_parameters(params: dict[str]) -> dict[str]:
    """
    Extract all optional LTI parameters from a dictionary. This method does not
    fail if any parameters are missing.

    :param params: A dictionary containing zero or more parameters.
    :return: A new dictionary containing all optional parameters from the
        original dictionary, or an empty dictionary if no optional parameters
        were present.
    """
    custom_params = configuration_helpers.get_value("LTI_CUSTOM_PARAMS", settings.LTI_CUSTOM_PARAMS)
    if not custom_params:
        return {}
    return {key: params[key] for key in custom_params if key in params}


def render_courseware(request, usage_key):
    """
    Render the content requested for the LTI launch.
    TODO: This method depends on the current refactoring work on the
    courseware/courseware.html template. It's signature may change depending on
    the requirements for that template once the refactoring is complete.

    Return an HttpResponse object that contains the template and necessary
    context to render the courseware.
    """
    # return an HttpResponse object that contains the template and necessary context to render the courseware.
    from lms.djangoapps.courseware.views.views import render_xblock
    return render_xblock(request, str(usage_key), check_if_enrolled=False, disable_staff_debug_info=True)


def parse_course_and_usage_keys(course_id, usage_id):
    """
    Convert course and usage ID strings into key objects. Return a tuple of
    (course_key, usage_key), or throw an InvalidKeyError if the translation
    fails.
    """
    course_key = CourseKey.from_string(course_id)
    usage_id = unquote_slashes(usage_id)
    usage_key = UsageKey.from_string(usage_id).map_into_course(course_key)
    return course_key, usage_key
