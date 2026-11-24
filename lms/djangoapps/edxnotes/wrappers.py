"""
XBlock wrappers related to edXNotes.
"""


import json

from django.conf import settings

from common.djangoapps.edxmako.shortcuts import render_to_string
from xmodule.modulestore.django import modulestore
from xmodule.x_module import STUDENT_VIEW

ANNOTATABLE_BLOCK_TYPES = {"html"}


def wrap_with_edxnotes(user, course, block, view, frag, context):  # pylint: disable=unused-argument
    """
    Make the student view of an HTML block annotatable by wrapping it in the edXNotes frontend.

    WARNING: This is a temporary hack. Do not use it as a pattern for new features.

    The LMS should not special-case particular block types when rendering, nor should it inject
    feature-specific templates and JavaScript into a block's output. This wrapper does both. It
    exists only to keep edXNotes working until edXNotes is either replaced or deprecated:
    https://github.com/openedx/edx-notes-api/issues/633

    Arguments:
        user (User): the user who is viewing the block.
        course (CourseBlock | None): the block's course, if the caller has already loaded it.
        block, view, frag, context: the standard XBlock wrapper arguments.
    """
    # Import is placed here to avoid model import at project startup.
    from .helpers import generate_uid, get_edxnotes_id_token, get_public_endpoint, get_token_url, is_feature_enabled

    if not settings.ENABLE_EDXNOTES:
        return frag
    if view != STUDENT_VIEW or block.scope_ids.block_type not in ANNOTATABLE_BLOCK_TYPES:
        return frag

    course = course or modulestore().get_course(block.scope_ids.usage_id.context_key)
    if not (course and is_feature_enabled(course, user)):
        return frag

    frag.content = render_to_string("edxnotes_wrapper.html", {
        "content": frag.content,
        "uid": generate_uid(),
        "edxnotes_visibility": json.dumps(
            getattr(block, "edxnotes_visibility", course.edxnotes_visibility)
        ),
        "params": {
            # Use camelCase to name keys.
            "usageId": block.scope_ids.usage_id,
            "courseId": course.id,
            "token": get_edxnotes_id_token(user),
            "tokenUrl": get_token_url(course.id),
            "endpoint": get_public_endpoint(),
            "debug": settings.DEBUG,
            "eventStringLimit": settings.TRACK_MAX_EVENT / 6,
        },
    })
    return frag
