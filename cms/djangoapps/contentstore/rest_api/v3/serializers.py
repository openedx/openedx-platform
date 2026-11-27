"""
Serializers for the v3 contentstore API.
"""

from cms.djangoapps.contentstore.rest_api.v1.serializers import StudioHomeSerializer


class StudioHomeContextSerializer(StudioHomeSerializer):
    """
    The Studio home page settings, without the course and library lists.

    The v3 home is built with ``get_home_context(request, True)``, which leaves
    those lists empty; they are served by ``/api/authoring/v4/courses/`` and the
    library APIs instead.
    """
    archived_courses = None
    courses = None
    in_process_course_actions = None
    libraries = None
