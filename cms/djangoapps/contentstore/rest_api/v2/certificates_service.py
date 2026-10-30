"""Service layer for the v2 course certificate settings endpoint."""

import logging

from openedx_authz.constants.permissions import COURSES_MANAGE_CERTIFICATES
from rest_framework.exceptions import NotFound

from cms.djangoapps.contentstore.utils import get_certificates_context
from cms.djangoapps.contentstore.views.permissions import COURSE_NOT_FOUND_MESSAGE
from openedx.core.djangoapps.authz.constants import LegacyAuthoringPermission
from openedx.core.djangoapps.authz.decorators import user_has_course_permission
from xmodule.modulestore.django import modulestore

log = logging.getLogger(__name__)


def can_manage_certificates(user, course_key):
    """
    Return whether ``user`` may change the certificate configuration of the course.

    Informational only: it reports a capability and is not an access check.
    """
    return user_has_course_permission(
        user,
        COURSES_MANAGE_CERTIFICATES.identifier,
        course_key,
        LegacyAuthoringPermission.WRITE,
    )


def get_certificate_settings(course_key, user):
    """
    Return the certificate configuration of the course ``course_key`` as seen by ``user``.

    Raises ``NotFound`` when the course does not exist or cannot be loaded from
    the modulestore. Nothing is written.
    """
    store = modulestore()
    with store.bulk_operations(course_key):
        course = store.get_course(course_key)
        if course is None:
            log.info("Certificate settings requested for course %s, which is not in the modulestore.", course_key)
            raise NotFound(COURSE_NOT_FOUND_MESSAGE)
        certificate_settings = get_certificates_context(course, user)
    certificate_settings["can_manage"] = can_manage_certificates(user, course_key)
    return certificate_settings
