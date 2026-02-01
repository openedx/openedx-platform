"""
Utility methods related to course
"""


import logging
from urllib.parse import urlencode

from django.conf import settings
from opaque_keys.edx.keys import CourseKey, UsageKey

from lms.djangoapps.branding.toggles import use_catalog_mfe
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx_filters.learning.filters import CourseAboutPageURLRequested

log = logging.getLogger(__name__)

COURSE_SHARING_UTM_PARAMETERS = {
    'facebook': {
        'utm_medium': 'social',
        'utm_campaign': 'social-sharing-db',
        'utm_source': 'facebook',
    },
    'twitter': {
        'utm_medium': 'social',
        'utm_campaign': 'social-sharing-db',
        'utm_source': 'twitter',
    },
}


def get_encoded_course_sharing_utm_params():
    """
    Returns encoded Course Sharing UTM Parameters.
    """
    return {
        utm_source: urlencode(utm_params)
        for utm_source, utm_params in COURSE_SHARING_UTM_PARAMETERS.items()
    }


def get_link_for_about_page(course):
    """
    Arguments:
        course: This can be either a course overview object or a course block.

    Returns the course sharing url, this can be one of course's social sharing url, marketing url, or
    lms course about url.
    """
    is_social_sharing_enabled = configuration_helpers.get_value(
        'SOCIAL_SHARING_SETTINGS',
        getattr(settings, 'SOCIAL_SHARING_SETTINGS', {})
    ).get('CUSTOM_COURSE_URLS')

    if use_catalog_mfe():
        about_base_url = configuration_helpers.get_value(
            'CATALOG_MICROFRONTEND_URL',
            settings.CATALOG_MICROFRONTEND_URL,
        )
    else:
        about_base_url = configuration_helpers.get_value('LMS_ROOT_URL', settings.LMS_ROOT_URL)

    if is_social_sharing_enabled and course.social_sharing_url:
        course_about_url = course.social_sharing_url
    elif settings.FEATURES.get('ENABLE_MKTG_SITE') and getattr(course, 'marketing_url', None):
        course_about_url = course.marketing_url
    else:
        course_about_url = f'{about_base_url}/courses/{course.id}/about'

    ## .. filter_implemented_name: CourseAboutPageURLRequested
    ## .. filter_type: org.openedx.learning.course_about.page.url.requested.v1
    course_about_url, _ = CourseAboutPageURLRequested.run_filter(
        url=course_about_url,
        org=course.id.org,
    )

    return course_about_url


def has_certificates_enabled(course):
    """
    Arguments:
        course: This can be either a course overview object or a course block.
    Returns a boolean if the course has enabled certificates
    """
    if not settings.FEATURES.get('CERTIFICATES_HTML_VIEW', False):
        return False
    return course.cert_html_view_enabled


def course_location_from_key(course_key: CourseKey) -> UsageKey:
    """Creates a usage key for the toplevel course item, handling differences between mongo and newer keys"""
    if getattr(course_key, 'deprecated', False):
        block_id = course_key.run
    else:
        block_id = 'course'
    return course_key.make_usage_key('course', block_id)
