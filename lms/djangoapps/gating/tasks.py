"""
This file contains celery tasks related to course content gating.
"""


import logging

from celery import shared_task
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from edx_django_utils.monitoring import set_code_owner_attribute
from opaque_keys.edx.keys import CourseKey, UsageKey

from lms.djangoapps.course_blocks.api import get_course_blocks
from lms.djangoapps.gating import api as gating_api
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

log = logging.getLogger(__name__)


@shared_task
@set_code_owner_attribute
def task_evaluate_subsection_completion_milestones(course_id, block_id, user_id):
    """
    Updates users' milestones related to completion of a subsection.
     Args:
        course_id(str): Course id which triggered a completion event
        block_id(str): Id of the completed block
        user_id(int): Id of the user who completed a block
    """
    store = modulestore()
    course_key = CourseKey.from_string(course_id)
    with store.bulk_operations(course_key):
        course = store.get_course(course_key)
        if not course or not course.enable_subsection_gating:
            log.debug(
                "Gating: ignoring evaluation of completion milestone because it disabled for course [%s]", course_id
            )
        else:
            try:
                user = User.objects.get(id=user_id)
                course_structure = get_course_blocks(user, store.make_course_usage_key(course_key))
                completed_block_usage_key = UsageKey.from_string(block_id).map_into_course(course.id)
                subsection_block = _get_subsection_of_block(completed_block_usage_key, course_structure)
                subsection = course_structure[subsection_block]
                log.debug(
                    "Gating: Evaluating completion milestone for subsection [%s] and user [%s]",
                    str(subsection.location), user.id
                )
                gating_api.evaluate_prerequisite(course, subsection, user)
            except KeyError:
                log.error("Gating: Given prerequisite subsection [%s] not found in course structure", block_id)


def _get_subsection_of_block(usage_key, block_structure):
    """
    Finds subsection of a block by recursively iterating over its parents
    :param usage_key: key of the block
    :param block_structure: block structure
    :return: sequential block
    """
    parents = block_structure.get_parents(usage_key)
    if parents:
        for parent_block in parents:
            if parent_block.block_type == 'sequential':
                return parent_block
            else:
                return _get_subsection_of_block(parent_block, block_structure)
