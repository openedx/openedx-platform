"""
Open edX filters for the course home metadata API.

These filters live here until they are added to openedx-filters. A plugin only needs the filter type
and ``openedx_filters.filters.PipelineStep``, so it does not import this module.

Core does not catch errors from pipeline steps. ``fail_silently`` in ``OPEN_EDX_FILTERS_CONFIG``
decides whether a failing step is skipped or fails the request.
"""

from typing import Any

from opaque_keys.edx.keys import CourseKey
from openedx_filters.tooling import OpenEdxPublicFilter


class CourseHomeMetadataAPIRenderStarted(OpenEdxPublicFilter):
    """
    Filter used to modify the course home metadata API data before it is returned.

    Purpose:
        This filter is triggered when the Learning MFE loads the course home metadata, just before the
        API returns it. Pipeline steps can add fields to the data or change it. Every course home and
        courseware page loads this data, so pipeline steps must be fast.

    Filter Type:
        org.openedx.learning.course_home.metadata.api.rendered.started.v1

    Trigger:
        - Repository: edx/edx-platform
        - Path: lms/djangoapps/course_home_api/course_metadata/views.py
        - Function or Method: CourseHomeMetadataView.get
    """

    filter_type = "org.openedx.learning.course_home.metadata.api.rendered.started.v1"

    @classmethod
    def run_filter(
        cls,
        user: Any,
        course_key: CourseKey,
        course_home_metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        Process the course home metadata using the configured pipeline steps.

        Arguments:
            user (User): the learner, or the learner a staff user masquerades as.
            course_key (CourseKey): the course run.
            course_home_metadata (dict): the serialized data the API returns. It must stay JSON-serializable.

        Returns:
            dict: the course home metadata, with the changes made by the pipeline steps.
        """
        data = super().run_pipeline(
            user=user,
            course_key=course_key,
            course_home_metadata=course_home_metadata,
        )
        return data.get("course_home_metadata")
