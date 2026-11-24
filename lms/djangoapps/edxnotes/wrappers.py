"""
XBlock wrappers related to edXNotes.
"""


from __future__ import annotations

import json
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib.auth.models import AnonymousUser, User  # pylint: disable=imported-auth-user
from web_fragments.fragment import Fragment
from xblock.core import XBlock

from common.djangoapps.edxmako.shortcuts import render_to_string
from xmodule.modulestore.django import modulestore
from xmodule.x_module import STUDENT_VIEW

if TYPE_CHECKING:
    from xmodule.course_block import CourseBlock


def wrap_with_edxnotes(  # pylint: disable=unused-argument
    user: User | AnonymousUser,
    course: CourseBlock | None,
    block: XBlock,
    view: str,
    frag: Fragment,
    context: dict | None,
) -> Fragment:
    """
    Make the student view of an HTML block annotatable by wrapping it in the edXNotes frontend.

    WARNING: This is a temporary hack. Do not use it as a pattern for new features.

    The LMS should not special-case particular block types when rendering, nor should we be building
    frontend features on openedx-platform django templates. This wrapper does both. It
    exists only to keep edXNotes working until edXNotes is either replaced or deprecated:
    https://github.com/openedx/edx-notes-api/issues/633

    Arguments:
        user: the user who is viewing the block.
        course: the block's course, if the caller has already loaded it.
        block, view, frag, context: the standard XBlock wrapper arguments.
    """
    # Import is placed here to avoid model import at project startup.
    from .helpers import generate_uid, get_edxnotes_id_token, get_public_endpoint, get_token_url, is_feature_enabled

    annotatable_block_types = {"html"}

    if not settings.ENABLE_EDXNOTES:
        return frag
    if view != STUDENT_VIEW or block.scope_ids.block_type not in annotatable_block_types:
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
