"""
API for initiating and tracking requests for credit from a provider.
"""


import datetime
import logging
import uuid

from zoneinfo import ZoneInfo
from django.db import transaction
from edx_proctoring.api import get_last_exam_completion_date

from openedx.core.djangoapps.credit.exceptions import (
    CreditProviderNotConfigured,
    CreditRequestNotFound,
    InvalidCreditStatus,
    RequestAlreadyCompleted,
    UserIsNotEligible
)
from openedx.core.djangoapps.credit.models import (
    CreditEligibility,
    CreditProvider,
    CreditRequest,
    CreditRequirementStatus
)
from openedx.core.djangoapps.credit.signature import get_shared_secret_key, signature
from common.djangoapps.student.models import CourseEnrollment, User
from common.djangoapps.util.date_utils import to_timestamp
from common.djangoapps.util.json_request import JsonResponse

# TODO: Cleanup this mess! ECOM-2908

log = logging.getLogger(__name__)


def get_credit_providers(providers_list=None):
    """Retrieve all available credit providers or filter on given providers_list.

    Arguments:
        providers_list (list of strings or None): contains list of ids of credit providers
        or None.

    Returns:
        list of credit providers represented as dictionaries
    Response Values:
        >>> get_credit_providers(['hogwarts'])
        [
            {
                "id": "hogwarts",
                "name": "Hogwarts School of Witchcraft and Wizardry",
                "url": "https://credit.example.com/",
                "status_url": "https://credit.example.com/status/",
                "description: "A new model for the Witchcraft and Wizardry School System.",
                "enable_integration": false,
                "fulfillment_instructions": "
                <p>In order to fulfill credit, Hogwarts School of Witchcraft and Wizardry requires learners to:</p>
                <ul>
                <li>Sample instruction abc</li>
                <li>Sample instruction xyz</li>
                </ul>",
            },
            ...
        ]
    """
    return CreditProvider.get_credit_providers(providers_list=providers_list)


def get_credit_provider_info(request, provider_id):  # pylint: disable=unused-argument
    """Retrieve the 'CreditProvider' model data against provided
     credit provider.

    Args:
        provider_id (str): The identifier for the credit provider

    Returns: 'CreditProvider' data dictionary

    Example Usage:
        >>> get_credit_provider_info("hogwarts")
        {
            "provider_id": "hogwarts",
            "display_name": "Hogwarts School of Witchcraft and Wizardry",
            "provider_url": "https://credit.example.com/",
            "provider_status_url": "https://credit.example.com/status/",
            "provider_description: "A new model for the Witchcraft and Wizardry School System.",
            "enable_integration": False,
            "fulfillment_instructions": "
                <p>In order to fulfill credit, Hogwarts School of Witchcraft and Wizardry requires learners to:</p>
                <ul>
                <li>Sample instruction abc</li>
                <li>Sample instruction xyz</li>
                </ul>",
            "thumbnail_url": "https://credit.example.com/logo.png"
        }

    """
    credit_provider = CreditProvider.get_credit_provider(provider_id=provider_id)
    credit_provider_data = {}
    if credit_provider:
        credit_provider_data = {
            "provider_id": credit_provider.provider_id,
            "display_name": credit_provider.display_name,
            "provider_url": credit_provider.provider_url,
            "provider_status_url": credit_provider.provider_status_url,
            "provider_description": credit_provider.provider_description,
            "enable_integration": credit_provider.enable_integration,
            "fulfillment_instructions": credit_provider.fulfillment_instructions,
            "thumbnail_url": credit_provider.thumbnail_url
        }

    return JsonResponse(credit_provider_data)


def check_keys_exist(shared_secret_key, provider_id):
    """
    Verify that a key is available for single or multiple key support scenarios.

    Raise CreditProviderNotConfigured if no key available.
    """
    # Accounts for old way of storing provider key
    if shared_secret_key is None:  # lint-amnesty, pylint: disable=no-else-raise
        msg = 'Credit provider with ID "{provider_id}" does not have a secret key configured.'.format(
            provider_id=provider_id
        )
        log.error(msg)
        raise CreditProviderNotConfigured(msg)

    # Accounts for new way of storing provider key
    elif isinstance(shared_secret_key, list) and not any(shared_secret_key):
        msg = 'Could not retrieve secret key for credit provider [{}]. ' \
              'Unable to validate requests from provider.'.format(provider_id)
        log.error(msg)
        raise CreditProviderNotConfigured(msg)


@transaction.atomic
def create_credit_request(course_key, provider_id, username):
    """
    Initiate a request for credit from a credit provider.

    This will return the parameters that the user's browser will need to POST
    to the credit provider.  It does NOT calculate the signature.

    Only users who are eligible for credit (have satisfied all credit requirements) are allowed to make requests.

    A provider can be configured either with *integration enabled* or not.
    If automatic integration is disabled, this method will simply return
    a URL to the credit provider and method set to "GET", so the student can
    visit the URL and request credit directly.  No database record will be created
    to track these requests.

    If automatic integration *is* enabled, then this will also return the parameters
    that the user's browser will need to POST to the credit provider.
    These parameters will be digitally signed using a secret key shared with the credit provider.

    A database record will be created to track the request with a 32-character UUID.
    The returned dictionary can be used by the user's browser to send a POST request to the credit provider.

    If a pending request already exists, this function should return a request description with the same UUID.
    (Other parameters, such as the user's full name may be different than the original request).

    If a completed request (either accepted or rejected) already exists, this function will
    raise an exception.  Users are not allowed to make additional requests once a request
    has been completed.

    Arguments:
        course_key (CourseKey): The identifier for the course.
        provider_id (str): The identifier of the credit provider.
        username (str): The user initiating the request.

    Returns: dict

    Raises:
        UserIsNotEligible: The user has not satisfied eligibility requirements for credit.
        CreditProviderNotConfigured: The credit provider has not been configured for this course.
        RequestAlreadyCompleted: The user has already submitted a request and received a response
            from the credit provider.

    Example Usage:
        >>> create_credit_request(course.id, "hogwarts", "ron")
        {
            "url": "https://credit.example.com/request",
            "method": "POST",
            "parameters": {
                "request_uuid": "557168d0f7664fe59097106c67c3f847",
                "timestamp": 1434631630,
                "course_org": "HogwartsX",
                "course_num": "Potions101",
                "course_run": "1T2015",
                "final_grade": "0.95",
                "user_username": "ron",
                "user_email": "ron@example.com",
                "user_full_name": "Ron Weasley",
                "user_mailing_address": "",
                "user_country": "US",
                "signature": "cRCNjkE4IzY+erIjRwOQCpRILgOvXx4q2qvx141BCqI="
            }
        }

    """
    try:
        user_eligibility = CreditEligibility.objects.select_related('course').get(
            username=username,
            course__course_key=course_key
        )
        credit_course = user_eligibility.course
        credit_provider = CreditProvider.objects.get(provider_id=provider_id)
    except CreditEligibility.DoesNotExist:
        log.warning(
            'User "%s" tried to initiate a request for credit in course "%s", '
            'but the user is not eligible for credit',
            username, course_key
        )
        raise UserIsNotEligible  # lint-amnesty, pylint: disable=raise-missing-from
    except CreditProvider.DoesNotExist:
        log.error('Credit provider with ID "%s" has not been configured.', provider_id)
        raise CreditProviderNotConfigured  # lint-amnesty, pylint: disable=raise-missing-from

    # Check if we've enabled automatic integration with the credit
    # provider.  If not, we'll show the user a link to a URL
    # where the user can request credit directly from the provider.
    # Note that we do NOT track these requests in our database,
    # since the state would always be "pending" (we never hear back).
    if not credit_provider.enable_integration:
        return {
            "url": credit_provider.provider_url,
            "method": "GET",
            "parameters": {}
        }
    else:
        # If automatic credit integration is enabled, then try
        # to retrieve the shared signature *before* creating the request.
        # That way, if there's a misconfiguration, we won't have requests
        # in our system that we know weren't sent to the provider.
        shared_secret_key = get_shared_secret_key(credit_provider.provider_id)
        check_keys_exist(shared_secret_key, credit_provider.provider_id)

        if isinstance(shared_secret_key, list):
            # if keys exist, and keys are stored as a list
            # then we know at least 1 is available for [0]
            shared_secret_key = [key for key in shared_secret_key if key][0]

    # Initiate a new request if one has not already been created
    credit_request, created = CreditRequest.objects.get_or_create(
        course=credit_course,
        provider=credit_provider,
        username=username,
    )

    # Check whether we've already gotten a response for a request,
    # If so, we're not allowed to issue any further requests.
    # Skip checking the status if we know that we just created this record.
    if not created and credit_request.status != "pending":
        log.warning(
            (
                'Cannot initiate credit request because the request with UUID "%s" '
                'exists with status "%s"'
            ), credit_request.uuid, credit_request.status
        )
        raise RequestAlreadyCompleted

    if created:
        credit_request.uuid = uuid.uuid4().hex

    # Retrieve user account and profile info
    user = User.objects.select_related('profile').get(username=username)

    # Retrieve the final grade from the eligibility table
    try:
        final_grade = CreditRequirementStatus.objects.get(
            username=username,
            requirement__namespace="grade",
            requirement__name="grade",
            requirement__course__course_key=course_key,
            status="satisfied"
        ).reason["final_grade"]

        # NOTE (CCB): Limiting the grade to seven characters is a hack for ASU.
        if len(str(final_grade)) > 7:
            final_grade = f'{final_grade:.5f}'
        else:
            final_grade = str(final_grade)

    except (CreditRequirementStatus.DoesNotExist, TypeError, KeyError):
        msg = 'Could not retrieve final grade from the credit eligibility table for ' \
              'user [{user_id}] in course [{course_key}].'.format(user_id=user.id, course_key=course_key)
        log.exception(msg)
        raise UserIsNotEligible(msg)  # lint-amnesty, pylint: disable=raise-missing-from

    # Getting the students's enrollment date
    course_enrollment = CourseEnrollment.get_enrollment(user, course_key)
    enrollment_date = course_enrollment.created if course_enrollment else ""

    # Getting the student's course completion date
    completion_date = get_last_exam_completion_date(course_key, username)

    parameters = {
        "request_uuid": credit_request.uuid,
        "timestamp": to_timestamp(datetime.datetime.now(ZoneInfo("UTC"))),
        "course_org": course_key.org,
        "course_num": course_key.course,
        "course_run": course_key.run,
        "enrollment_timestamp": to_timestamp(enrollment_date) if enrollment_date else "",
        "course_completion_timestamp": to_timestamp(completion_date) if completion_date else "",
        "final_grade": final_grade,
        "user_username": user.username,
        "user_email": user.email,
        "user_full_name": user.profile.name,
        "user_mailing_address": "",
        "user_country": (
            user.profile.country.code
            if user.profile.country.code is not None
            else ""
        ),
    }

    credit_request.parameters = parameters
    credit_request.save()

    if created:
        log.info('Created new request for credit with UUID "%s"', credit_request.uuid)
    else:
        log.info(
            'Updated request for credit with UUID "%s" so the user can re-issue the request',
            credit_request.uuid
        )

    # Sign the parameters using a secret key we share with the credit provider.
    parameters["signature"] = signature(parameters, shared_secret_key)

    return {
        "url": credit_provider.provider_url,
        "method": "POST",
        "parameters": parameters
    }


def update_credit_request_status(request_uuid, provider_id, status):
    """
    Update the status of a credit request.

    Approve or reject a request for a student to receive credit in a course
    from a particular credit provider.

    This function does NOT check that the status update is authorized.
    The caller needs to handle authentication and authorization (checking the signature
    of the message received from the credit provider)

    The function is idempotent; if the request has already been updated to the status,
    the function does nothing.

    Arguments:
        request_uuid (str): The unique identifier for the credit request.
        provider_id (str): Identifier for the credit provider.
        status (str): Either "approved" or "rejected"

    Returns: None

    Raises:
        CreditRequestNotFound: No request exists that is associated with the given provider.
        InvalidCreditStatus: The status is not either "approved" or "rejected".

    """
    if status not in [CreditRequest.REQUEST_STATUS_APPROVED, CreditRequest.REQUEST_STATUS_REJECTED]:
        raise InvalidCreditStatus

    try:
        request = CreditRequest.objects.get(uuid=request_uuid, provider__provider_id=provider_id)
        old_status = request.status
        request.status = status
        request.save()

        log.info(
            'Updated request with UUID "%s" from status "%s" to "%s" for provider with ID "%s".',
            request_uuid, old_status, status, provider_id
        )
    except CreditRequest.DoesNotExist:
        msg = (
            'Credit provider with ID "{provider_id}" attempted to '
            'update request with UUID "{request_uuid}", but no request '
            'with this UUID is associated with the provider.'
        ).format(provider_id=provider_id, request_uuid=request_uuid)
        log.warning(msg)
        raise CreditRequestNotFound(msg)  # lint-amnesty, pylint: disable=raise-missing-from


def get_credit_requests_for_user(username):
    """
    Retrieve the status of a credit request.

    Returns either "pending", "approved", or "rejected"

    Arguments:
        username (unicode): The username of the user who initiated the requests.

    Returns: list

    Example Usage:
    >>> get_credit_request_status_for_user("bob")
    [
        {
            "uuid": "557168d0f7664fe59097106c67c3f847",
            "timestamp": 1434631630,
            "course_key": "course-v1:HogwartsX+Potions101+1T2015",
            "provider": {
                "id": "HogwartsX",
                "display_name": "Hogwarts School of Witchcraft and Wizardry",
            },
            "status": "pending"  # or "approved" or "rejected"
        }
    ]

    """
    return CreditRequest.credit_requests_for_user(username)


def get_credit_request_status(username, course_key):
    """Get the credit request status.

    This function returns the status of credit request of user for given course.
    It returns the latest request status for the any credit provider.
    The valid status are 'pending', 'approved' or 'rejected'.

    Args:
        username(str): The username of user
        course_key(CourseKey): The course locator key

    Returns:
        A dictionary of credit request user has made if any

    """
    credit_request = CreditRequest.get_user_request_status(username, course_key)
    return {
        "uuid": credit_request.uuid,
        "timestamp": credit_request.modified,
        "course_key": credit_request.course.course_key,
        "provider": {
            "id": credit_request.provider.provider_id,
            "display_name": credit_request.provider.display_name
        },
        "status": credit_request.status
    } if credit_request else {}
