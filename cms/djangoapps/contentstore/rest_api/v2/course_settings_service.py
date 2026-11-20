"""Service layer for the v2 course settings endpoint."""

import logging

from django.conf import settings
from django.http import HttpRequest
from rest_framework.exceptions import NotFound
from rest_framework.request import Request

from cms.djangoapps.contentstore.utils import get_course_settings
from cms.djangoapps.contentstore.views.course import are_content_experiments_enabled
from cms.djangoapps.contentstore.views.permissions import COURSE_NOT_FOUND_MESSAGE
from lms.djangoapps.certificates.api import can_show_certificate_available_date_field
from xmodule.modulestore.django import modulestore

log = logging.getLogger(__name__)

# Members returned only by the full view.
FULL_VIEW_FIELDS = ("credit_requirements", "language_options", "possible_pre_requisite_courses")


def _request_without_query(request):
    """
    Return a request by the caller of ``request`` that carries no query parameters.

    The courses that may be prerequisites are listed by the Studio course
    listing, which searches, filters and orders courses by the query parameters
    of the request it is given, and refuses some malformed ones.
    """
    bare = Request(HttpRequest())
    bare.user = request.user
    return bare


def get_course_settings_data(request, course_key, full=False):
    """
    Return the Studio settings of the course ``course_key`` as the caller of ``request`` sees them.

    The members in ``FULL_VIEW_FIELDS`` are left out unless ``full`` is true;
    even then the prerequisite courses are present only while prerequisite
    courses are on, and the credit requirements only for a credit course while
    credit eligibility is on. The query parameters of ``request`` are not read.

    Raises ``NotFound`` when the modulestore has no course ``course_key``.
    """
    store = modulestore()
    with store.bulk_operations(course_key):
        course = store.get_course(course_key)
        if course is None:
            log.info("Settings requested for course %s, which is not in the modulestore.", course_key)
            raise NotFound(COURSE_NOT_FOUND_MESSAGE)
        data = get_course_settings(_request_without_query(request), course_key, course)
        data.update({
            "can_show_certificate_available_date_field": can_show_certificate_available_date_field(course),
            "content_experiments_enabled": are_content_experiments_enabled(course),
            "course_display_name": course.display_name,
            "course_display_name_with_default": course.display_name_with_default,
            "licensing_enabled": settings.LICENSING,
            "platform_name": settings.PLATFORM_NAME,
        })
    if not full:
        # The shared settings helper builds these members either way; leaving them out saves payload, not work.
        for name in FULL_VIEW_FIELDS:
            data.pop(name, None)
    return data
