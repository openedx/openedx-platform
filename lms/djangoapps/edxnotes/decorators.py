"""
Decorators related to edXNotes.
"""


import json

from django.conf import settings
from xblock.exceptions import NoSuchServiceError

from common.djangoapps.edxmako.shortcuts import render_to_string


def edxnotes(cls):
    """
    Decorator that makes components annotatable.
    """
    original_get_html = cls.get_html

    def get_html(self, *args, **kwargs):
        """
        Returns raw html for the component.
        """
        # Import is placed here to avoid model import at project startup.
        from .helpers import (
            generate_uid,
            get_ccx_master_course_key,
            get_ccx_master_usage_key,
            get_edxnotes_id_token,
            get_public_endpoint,
            get_token_url,
            is_feature_enabled,
        )

        if not settings.ENABLE_EDXNOTES:
            return original_get_html(self, *args, **kwargs)

        runtime = getattr(self, 'block', self).runtime
        if not hasattr(runtime, 'modulestore'):
            return original_get_html(self, *args, **kwargs)

        is_studio = getattr(self.runtime, "is_author_mode", False)
        course = getattr(self, 'block', self).runtime.modulestore.get_course(self.scope_ids.usage_id.context_key)

        # Must be disabled when:
        # - in Studio
        # - Harvard Annotation Tool is enabled for the course
        # - the feature flag or `edxnotes` setting of the course is set to False
        # - the user is not authenticated
        try:
            user = self.runtime.service(self, 'user').get_user_by_anonymous_id()
        except NoSuchServiceError:
            user = None

        if is_studio or not is_feature_enabled(course, user):
            return original_get_html(self, *args, **kwargs)
        else:
            # Notes are recorded against the master course (and its own block ids),
            # not the CCX, so a note taken in any CCX section shows up in every CCX
            # section derived from the same master course. `tokenUrl` deliberately
            # keeps the real (CCX) course id: that URL is routed/access-checked
            # against the course actually being viewed, not the note's data key.
            notes_course_id = get_ccx_master_course_key(course.id)
            notes_usage_id = get_ccx_master_usage_key(self.scope_ids.usage_id)

            return render_to_string("edxnotes_wrapper.html", {
                "content": original_get_html(self, *args, **kwargs),
                "uid": generate_uid(),
                "edxnotes_visibility": json.dumps(
                    getattr(self, 'edxnotes_visibility', course.edxnotes_visibility)
                ),
                "params": {
                    # Use camelCase to name keys.
                    "usageId": notes_usage_id,
                    "courseId": notes_course_id,
                    "token": get_edxnotes_id_token(user),
                    "tokenUrl": get_token_url(course.id),
                    "endpoint": get_public_endpoint(),
                    "debug": settings.DEBUG,
                    "eventStringLimit": settings.TRACK_MAX_EVENT / 6,
                },
            })

    cls.get_html = get_html
    return cls
