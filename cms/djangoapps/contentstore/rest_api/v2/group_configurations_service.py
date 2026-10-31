"""Reads of a course's group configurations."""

import logging

from openedx_authz.constants.permissions import COURSES_MANAGE_GROUP_CONFIGURATIONS
from rest_framework.exceptions import NotFound

from cms.djangoapps.contentstore.utils import get_group_configurations_context
from cms.djangoapps.contentstore.views.permissions import COURSE_NOT_FOUND_MESSAGE
from openedx.core.djangoapps.authz.constants import LegacyAuthoringPermission
from openedx.core.djangoapps.authz.decorators import user_has_course_permission
from xmodule.modulestore.django import modulestore

log = logging.getLogger(__name__)


def list_group_configurations(course_key, user):
    """
    Return the group configurations of the course ``course_key`` that Studio shows, in Studio's order.

    First the configurations Studio's group configurations page lists in its
    main section: the enrollment track configuration when the course has more
    than one track, the content type gating configuration when Studio may set
    it, then content groups and the other configurations in course order. A
    course without content groups gets an unsaved content group configuration
    there, with a new identifier for each call. The experiment configurations
    follow, in course order, when the course has content experiments turned
    on; otherwise none is returned.

    Each configuration is a dict as Studio stores it, with ``usage`` where
    Studio reports where the configuration or its groups are used, and with
    ``can_manage``: whether ``user`` may change the course's group
    configurations. That is decided once, for every configuration.

    Raises ``NotFound`` when the course cannot be loaded from the modulestore.
    """
    store = modulestore()
    with store.bulk_operations(course_key):
        course = store.get_course(course_key)
        if course is None:
            log.info("Group configurations requested for course %s, which is not in the modulestore.", course_key)
            raise NotFound(COURSE_NOT_FOUND_MESSAGE)
        context = get_group_configurations_context(course, store)
    can_manage = user_has_course_permission(
        user,
        COURSES_MANAGE_GROUP_CONFIGURATIONS.identifier,
        course_key,
        LegacyAuthoringPermission.WRITE,
    )
    configurations = [*context["all_group_configurations"], *(context["experiment_group_configurations"] or [])]
    return [{**configuration, "can_manage": can_manage} for configuration in configurations]
