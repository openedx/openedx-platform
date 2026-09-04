"""
Serializers for the CCX Coach API v2.
"""

import logging
from urllib.parse import urlparse

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

log = logging.getLogger(__name__)

# CCX Coach navigation tabs, in display order. Each entry is
# `(tab_id, title, sort_order)`. `tab_id` values must match the route
# segments registered by the Instructor Dashboard MFE `ccxCoachConfig` so
# the MFE can resolve each tab to a page. `sort_order` uses gaps of 10 to
# match the Instructor Dashboard convention, leaving room for FE plugin tabs.
CCX_COACH_TABS = (
    ('enrollments', _('Enrollment'), 10),
    ('schedule', _('Schedule'), 20),
    ('student_grades', _('Student Grades'), 30),
    ('grading_policy', _('Grading Policy'), 40),
)


def build_ccx_coach_tab_url(ccx_course_key, tab_id):
    """
    Build a CCX Coach MFE tab URL from `CCX_COACH_MICROFRONTEND_URL`.

    Mirrors the Instructor Dashboard `_build_tab_url` helper: only the path
    component of the configured base URL is used, yielding an internal link of
    the form `/ccx-coach/{ccx_course_id}/{tab_id}` that the MFE router
    resolves. Logs a warning and falls back to a relative URL when the setting
    is unset.

    Arguments:
        ccx_course_key (CCXLocator): the CCX course key.
        tab_id (str): the tab route segment.

    Returns:
        str: the tab URL.
    """
    base_url = getattr(settings, 'CCX_COACH_MICROFRONTEND_URL', None)
    if base_url is None:
        log.warning('CCX_COACH_MICROFRONTEND_URL is not configured.')
        base_part = ''
    else:
        base_part = urlparse(base_url).path

    parts = [base_part.rstrip('/'), str(ccx_course_key).strip('/'), tab_id]
    return '/'.join(parts)


class CCXCoachMetadataSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serialize CCX Coach course metadata for the MFE.

    Expects a context dict with:

    * `master_course_key` (CourseKey): the master course key.
    * `ccx_course_key` (CCXLocator | None): the coach's CCX course key, or
      `None` when no CCX exists yet.

    Produces `{course_id, ccx_course_id, tabs}`. When no CCX exists,
    `ccx_course_id` is an empty string and `tabs` is an empty list, matching
    the legacy coach dashboard behavior (the MFE then shows its create/empty
    state).
    """

    course_id = serializers.SerializerMethodField()
    ccx_course_id = serializers.SerializerMethodField()
    tabs = serializers.SerializerMethodField()

    def get_course_id(self, data):
        """Master course id as a string."""
        return str(data['master_course_key'])

    def get_ccx_course_id(self, data):
        """CCX course id as a string, or empty string when no CCX exists."""
        ccx_course_key = data.get('ccx_course_key')
        return str(ccx_course_key) if ccx_course_key else ''

    def get_tabs(self, data):
        """The CCX Coach tabs, or an empty list when no CCX exists."""
        ccx_course_key = data.get('ccx_course_key')
        if not ccx_course_key:
            return []
        return [
            {
                'tab_id': tab_id,
                'title': str(title),
                'url': build_ccx_coach_tab_url(ccx_course_key, tab_id),
                'sort_order': sort_order,
            }
            for tab_id, title, sort_order in CCX_COACH_TABS
        ]


class CreateCCXRequestSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Validate the `create_ccx` POST body: `{ "name": str }`."""

    name = serializers.CharField(max_length=255, allow_blank=False, trim_whitespace=True)
