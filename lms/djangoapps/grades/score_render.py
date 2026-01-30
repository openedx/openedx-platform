"""
Score rendering when submission is evaluated for external grader and has been saved successfully
"""

import logging
from functools import partial

from django.http import Http404
from edx_when.field_data import DateLookupFieldData
from opaque_keys.edx.keys import CourseKey, UsageKey
from xblock.runtime import KvsFieldData

from common.djangoapps.student.models import AnonymousUserId
from lms.djangoapps.courseware.block_render import prepare_runtime_for_user
from lms.djangoapps.courseware.field_overrides import OverrideFieldData
from lms.djangoapps.courseware.model_data import DjangoKeyValueStore, FieldDataCache
from lms.djangoapps.lms_xblock.field_data import LmsFieldData
from xmodule.modulestore.django import modulestore

log = logging.getLogger(__name__)


def load_xblock_for_external_grader(
    user_id: str,
    course_key: CourseKey,
    usage_key: UsageKey,
    course=None,
):
    """
    Load a single XBlock for external grading without user access checks.
    """

    user = AnonymousUserId.objects.get(anonymous_user_id=user_id).user

    # pylint: disable=broad-exception-caught
    try:
        block = modulestore().get_item(usage_key)
    except Exception as e:
        log.exception("Could not find block %s in modulestore: %s", usage_key, e)
        raise Http404(f"Module {usage_key} was not found") from e

    field_data_cache = FieldDataCache.cache_for_block_descendents(course_key, user, block, depth=0)

    student_kvs = DjangoKeyValueStore(field_data_cache)
    student_data = KvsFieldData(student_kvs)

    instance = get_block_for_descriptor_without_access_check(
        user=user, block=block, student_data=student_data, course_key=course_key, course=course
    )

    if instance is None:
        msg = f"Could not bind XBlock instance for usage key: {usage_key}"
        log.error(msg)
        raise Http404(msg)

    return instance


def get_block_for_descriptor_without_access_check(user, block, student_data, course_key, course=None):
    """
    Modified version of get_block_for_descriptor that skips access checks for system operations.
    """

    prepare_runtime_for_user(
        user=user,
        student_data=student_data,
        runtime=block.runtime,
        course_id=course_key,
        course=course,
        track_function=lambda event_type, event: None,
        request_token="external-grader-token",
        position=None,
        wrap_xblock_display=True,
    )

    block.bind_for_student(
        user.id,
        [
            partial(DateLookupFieldData, course_id=course_key, user=user),
            partial(OverrideFieldData.wrap, user, course),
            partial(LmsFieldData, student_data=student_data),
        ],
    )

    return block
