"""Utility functions that have to do with the courseware."""


import datetime
import hashlib
import logging
from bs4 import BeautifulSoup

from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from edx_rest_api_client.client import OAuthAPIClient
from oauth2_provider.models import Application
from pytz import utc  # lint-amnesty, pylint: disable=wrong-import-order
from rest_framework import status
from xmodule.partitions.partitions import \
    ENROLLMENT_TRACK_PARTITION_ID  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.partitions.partitions_service import PartitionService  # lint-amnesty, pylint: disable=wrong-import-order

from common.djangoapps.course_modes.models import CourseMode
from lms.djangoapps.commerce.utils import EcommerceService
from lms.djangoapps.courseware.config import ENABLE_NEW_FINANCIAL_ASSISTANCE_FLOW
from lms.djangoapps.courseware.constants import (
    UNEXPECTED_ERROR_APPLICATION_STATUS,
    UNEXPECTED_ERROR_CREATE_APPLICATION,
    UNEXPECTED_ERROR_IS_ELIGIBLE
)
from lms.djangoapps.courseware.models import FinancialAssistanceConfiguration
from openedx.core.djangoapps.waffle_utils.models import WaffleFlagCourseOverrideModel

log = logging.getLogger(__name__)


def verified_upgrade_deadline_link(user, course=None, course_id=None):
    """
    Format the correct verified upgrade link for the specified ``user``
    in a course.

    One of ``course`` or ``course_id`` must be supplied. If both are specified,
    ``course`` will take priority.

    Arguments:
        user (:class:`~django.contrib.auth.models.User`): The user to display
            the link for.
        course (:class:`.CourseOverview`): The course to render a link for.
        course_id (:class:`.CourseKey`): The course_id of the course to render for.

    Returns:
        The formatted link that will allow the user to upgrade to verified
        in this course.
    """
    if course is not None:
        course_id = course.id
    return EcommerceService().upgrade_url(user, course_id)


def is_mode_upsellable(user, enrollment, course=None):
    """
    Return whether the user is enrolled in a mode that can be upselled to another mode,
    usually audit upselled to verified.
    The partition code allows this function to more accurately return results for masquerading users.

    Arguments:
        user (:class:`.AuthUser`): The user from the request.user property
        enrollment (:class:`.CourseEnrollment`): The enrollment under consideration.
        course (:class:`.ModulestoreCourse`): Optional passed in modulestore course.
            If provided, it is expected to correspond to `enrollment.course.id`.
            If not provided, the course will be loaded from the modulestore.
            We use the course to retrieve user partitions when calculating whether
            the upgrade link will be shown.
    """
    partition_service = PartitionService(enrollment.course.id, course=course)
    enrollment_track_partition = partition_service.get_user_partition(ENROLLMENT_TRACK_PARTITION_ID)
    group = partition_service.get_group(user, enrollment_track_partition)
    current_mode = None
    if group:
        try:
            current_mode = [
                mode.get('slug') for mode in settings.COURSE_ENROLLMENT_MODES.values() if mode['id'] == group.id
            ].pop()
        except IndexError:
            pass
    upsellable_mode = not current_mode or current_mode in CourseMode.UPSELL_TO_VERIFIED_MODES
    return upsellable_mode


def can_show_verified_upgrade(user, enrollment, course=None):
    """
    Return whether this user can be shown upgrade message.

    Arguments:
        user (:class:`.AuthUser`): The user from the request.user property
        enrollment (:class:`.CourseEnrollment`): The enrollment under consideration.
            If None, then the enrollment is not considered to be upgradeable.
        course (:class:`.ModulestoreCourse`): Optional passed in modulestore course.
            If provided, it is expected to correspond to `enrollment.course.id`.
            If not provided, the course will be loaded from the modulestore.
            We use the course to retrieve user partitions when calculating whether
            the upgrade link will be shown.
    """
    if enrollment is None:
        return False  # this got accidentally flipped in 2017 (commit 8468357), but leaving alone to not switch again

    if not is_mode_upsellable(user, enrollment, course):
        return False

    upgrade_deadline = enrollment.upgrade_deadline

    if upgrade_deadline is None:
        return False

    if datetime.datetime.now(utc).date() > upgrade_deadline.date():
        return False

    # Show the summary if user enrollment is in which allow user to upsell
    return enrollment.is_active and enrollment.mode in CourseMode.UPSELL_TO_VERIFIED_MODES


def _request_financial_assistance(method, url, params=None, data=None):
    """
    An internal function containing common functionality among financial assistance utility function to call
    edx-financial-assistance backend with appropriate method, url, params and data.
    """
    financial_assistance_configuration = FinancialAssistanceConfiguration.current()
    if financial_assistance_configuration.enabled:
        oauth_application = Application.objects.get(
            user=financial_assistance_configuration.get_service_user(),
            authorization_grant_type=Application.GRANT_CLIENT_CREDENTIALS
        )
        client = OAuthAPIClient(
            settings.LMS_ROOT_URL,
            oauth_application.client_id,
            oauth_application.client_secret
        )
        return client.request(
            method, f"{financial_assistance_configuration.api_base_url}{url}", params=params, data=data
        )
    else:
        return False, 'Financial Assistance configuration is not enabled'


def is_eligible_for_financial_aid(course_id):
    """
    Sends a get request to edx-financial-assistance to retrieve financial assistance eligibility criteria for a course.

    Returns either True if course is eligible for financial aid or vice versa.
    Also returns the reason why the course isn't eligible.
    In case of a bad request, returns an error message.
    """
    response = _request_financial_assistance('GET', f"{settings.IS_ELIGIBLE_FOR_FINANCIAL_ASSISTANCE_URL}{course_id}/")
    if response.status_code == status.HTTP_200_OK:
        return response.json().get('is_eligible'), response.json().get('reason')
    elif response.status_code == status.HTTP_400_BAD_REQUEST:
        return False, response.json().get('message')
    else:
        log.error('%s %s', UNEXPECTED_ERROR_IS_ELIGIBLE, str(response.content))
        return False, UNEXPECTED_ERROR_IS_ELIGIBLE


def get_financial_assistance_application_status(user_id, course_id):
    """
    Given the course_id, sends a get request to edx-financial-assistance to retrieve
    financial assistance application(s) status for the logged-in user.
    """
    request_params = {
        'course_id': course_id,
        'lms_user_id': user_id
    }
    response = _request_financial_assistance(
        'GET', f"{settings.FINANCIAL_ASSISTANCE_APPLICATION_STATUS_URL}", params=request_params
    )
    if response.status_code == status.HTTP_200_OK:
        return True, response.json()
    elif response.status_code in (status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND):
        return False, response.json().get('message')
    else:
        log.error('%s %s', UNEXPECTED_ERROR_APPLICATION_STATUS, response.content)
        return False, UNEXPECTED_ERROR_APPLICATION_STATUS


def create_financial_assistance_application(form_data):
    """
    Sends a post request to edx-financial-assistance to create a new application for financial assistance application.
    The incoming form_data must have data as given in the example below:
    {
        "lms_user_id": <user_id>,
        "course_id": <course_run_id>,
        "certify_economic_hardship": <Boolean>,
        "certify_complete_certificate": <Boolean>,
        "certify_honor_code": <Boolean>,
    }
    """
    response = _request_financial_assistance(
        'POST', f"{settings.CREATE_FINANCIAL_ASSISTANCE_APPLICATION_URL}/", data=form_data
    )
    if response.status_code == status.HTTP_200_OK:
        return HttpResponse(status=status.HTTP_204_NO_CONTENT)
    elif response.status_code == status.HTTP_400_BAD_REQUEST:
        log.error(response.json().get('message'))
        return HttpResponseBadRequest(response.content)
    else:
        log.error('%s %s', UNEXPECTED_ERROR_CREATE_APPLICATION, response.content)
        return HttpResponse(status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def get_course_hash_value(course_key):
    """
    Returns a hash value for the given course key.
    If course key is None, function returns an out of bound value which will
    never satisfy the fa_backend_enabled_courses_percentage condition
    """
    out_of_bound_value = 100
    if course_key:
        m = hashlib.md5(str(course_key).encode())
        return int(m.hexdigest(), base=16) % 100

    return out_of_bound_value


def _use_new_financial_assistance_flow(course_id):
    """
    Returns if the course_id can be used in the new financial assistance flow.
    """
    is_financial_assistance_enabled_for_course = WaffleFlagCourseOverrideModel.override_value(
        ENABLE_NEW_FINANCIAL_ASSISTANCE_FLOW.name, course_id
    )
    financial_assistance_configuration = FinancialAssistanceConfiguration.current()
    if financial_assistance_configuration.enabled and (
            is_financial_assistance_enabled_for_course == WaffleFlagCourseOverrideModel.ALL_CHOICES.on or
            get_course_hash_value(course_id) <= financial_assistance_configuration.fa_backend_enabled_courses_percentage
    ):
        return True
    return False


def is_empty_html(html_content):
    """
    Check if HTML content is effectively empty.
    """
    if not html_content:
        return True

    soup = BeautifulSoup(html_content, 'html.parser')
    text = soup.get_text(strip=True)

    return not text
