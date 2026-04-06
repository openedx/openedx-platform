"""
Module containing API functions for the CCXCon
"""


import logging
from urllib import parse

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.http import Http404
from oauthlib.oauth2 import BackendApplicationClient
from requests_oauthlib import OAuth2Session
from rest_framework.status import HTTP_200_OK, HTTP_201_CREATED

from common.djangoapps.student.models import anonymous_id_for_user
from common.djangoapps.student.roles import CourseInstructorRole
from openedx.core.djangoapps.models.course_details import CourseDetails
from openedx.core.lib.courses import get_course_by_id

from .models import CCXCon

log = logging.getLogger(__name__)


CCXCON_COURSEXS_URL = '/api/v1/coursexs/'
CCXCON_TOKEN_URL = '/o/token/'
CCXCON_REQUEST_TIMEOUT = 30


class CCXConnServerError(Exception):
    """
    Custom exception to be raised in case there is any
    issue with the request to the server
    """


def is_valid_url(url):
    """
    Helper function used to check if a string is a valid url.

    Args:
        url (str): the url string to be validated

    Returns:
        bool: whether the url is valid or not
    """
    validate = URLValidator()
    try:
        validate(url)
        return True
    except ValidationError:
        return False


def get_oauth_client(server_token_url, client_id, client_secret):
    """
    Function that creates an oauth client and fetches a token.
    It intentionally doesn't handle errors.

    Args:
        server_token_url (str): server URL where to get an authentication token
        client_id (str): oauth client ID
        client_secret (str): oauth client secret

    Returns:
        OAuth2Session: an instance of OAuth2Session with a token
    """
    if not is_valid_url(server_token_url):
        return
    client = BackendApplicationClient(client_id=client_id)
    oauth_ccxcon = OAuth2Session(client=client)
    oauth_ccxcon.fetch_token(
        token_url=server_token_url,
        client_id=client_id,
        client_secret=client_secret,
        timeout=CCXCON_REQUEST_TIMEOUT
    )
    return oauth_ccxcon


def course_info_to_ccxcon(course_key):
    """
    Function that gathers informations about the course and
    makes a post request to a CCXCon with the data.

    Args:
        course_key (CourseLocator): the master course key
    """

    try:
        course = get_course_by_id(course_key)
    except Http404:
        log.error('Master Course with key "%s" not found', str(course_key))
        return
    if not course.enable_ccx:
        log.debug('ccx not enabled for course key "%s"', str(course_key))
        return
    if not course.ccx_connector:
        log.debug('ccx connector not defined for course key "%s"', str(course_key))
        return
    if not is_valid_url(course.ccx_connector):
        log.error(
            'ccx connector URL "%s" for course key "%s" is not a valid URL.',
            course.ccx_connector, str(course_key)
        )
        return
    # get the oauth credential for this URL
    try:
        ccxcon = CCXCon.objects.get(url=course.ccx_connector)
    except CCXCon.DoesNotExist:
        log.error('ccx connector Oauth credentials not configured for URL "%s".', course.ccx_connector)
        return

    # get an oauth client with a valid token

    oauth_ccxcon = get_oauth_client(
        server_token_url=parse.urljoin(course.ccx_connector, CCXCON_TOKEN_URL),
        client_id=ccxcon.oauth_client_id,
        client_secret=ccxcon.oauth_client_secret
    )

    # get the entire list of instructors
    course_instructors = CourseInstructorRole(course.id).users_with_role()
    # get anonymous ids for each of them
    course_instructors_ids = [anonymous_id_for_user(user, course_key) for user in course_instructors]
    # extract the course details
    course_details = CourseDetails.fetch(course_key)

    payload = {
        'course_id': str(course_key),
        'title': course.display_name,
        'author_name': None,
        'overview': course_details.overview,
        'description': course_details.short_description,
        'image_url': course_details.course_image_asset_path,
        'instructors': course_instructors_ids
    }
    headers = {'content-type': 'application/json'}

    # make the POST request
    add_course_url = parse.urljoin(course.ccx_connector, CCXCON_COURSEXS_URL)
    resp = oauth_ccxcon.post(
        url=add_course_url,
        json=payload,
        headers=headers,
        timeout=CCXCON_REQUEST_TIMEOUT
    )

    if resp.status_code >= 500:
        raise CCXConnServerError('Server returned error Status: %s, Content: %s', resp.status_code, resp.content)  # lint-amnesty, pylint: disable=raising-format-tuple
    if resp.status_code >= 400:
        log.error("Error creating course on ccxcon. Status: %s, Content: %s", resp.status_code, resp.content)
    # this API performs a POST request both for POST and PATCH, but the POST returns 201 and the PATCH returns 200
    elif resp.status_code not in (HTTP_200_OK, HTTP_201_CREATED):
        log.error('Server returned unexpected status code %s', resp.status_code)
    else:
        log.debug('Request successful. Status: %s, Content: %s', resp.status_code, resp.content)
