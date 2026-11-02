"""
Open edX filters for the course_modes app.

These filters live here until they are added to openedx-filters. A plugin only needs the filter type
and ``openedx_filters.filters.PipelineStep``, so it does not import this module.

Core does not catch errors from pipeline steps. ``fail_silently`` in ``OPEN_EDX_FILTERS_CONFIG``
decides whether a failing step is skipped or fails the request.
"""

from typing import Any

from opaque_keys.edx.keys import CourseKey
from openedx_filters.tooling import OpenEdxPublicFilter


class TrackSelectionAPIRenderStarted(OpenEdxPublicFilter):
    """
    Filter used to modify the track selection API data before it is returned.

    Purpose:
        This filter is triggered when the Learning MFE loads the track selection page, just before the
        API returns the page data. Pipeline steps can add fields to the data or change it.

    Filter Type:
        org.openedx.learning.course_mode.track_selection.api.rendered.started.v1

    Trigger:
        - Repository: edx/edx-platform
        - Path: common/djangoapps/course_modes/track_selection_data.py
        - Function or Method: get_track_selection_page_data
    """

    filter_type = "org.openedx.learning.course_mode.track_selection.api.rendered.started.v1"

    @classmethod
    def run_filter(
        cls,
        user: Any,
        course_key: CourseKey,
        track_selection_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        Process the track selection data using the configured pipeline steps.

        Arguments:
            user (User): the learner.
            course_key (CourseKey): the course run.
            track_selection_data (dict): the data the API returns. It must stay JSON-serializable.

        Returns:
            dict: the track selection data, with the changes made by the pipeline steps.
        """
        data = super().run_pipeline(
            user=user,
            course_key=course_key,
            track_selection_data=track_selection_data,
        )
        return data.get("track_selection_data")
