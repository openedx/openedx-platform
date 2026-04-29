"""
Common utilities for Contentstore APIs.
"""


from contextlib import contextmanager

from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from rest_framework.generics import GenericAPIView

from common.djangoapps.student.auth import has_course_author_access
from openedx.core.djangoapps.util.forms import to_bool
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, view_auth_classes
from openedx.core.lib.cache_utils import request_cached
from xmodule.library_content_block import LegacyLibraryContentBlock
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order


@view_auth_classes()
class BaseCourseView(DeveloperErrorViewMixin, GenericAPIView):
    """
    A base class for contentstore course api views.
    """
    @contextmanager
    def get_course(self, request, course_key):
        """
        Context manager that yields a course, given a request and course_key.
        """
        store = modulestore()
        with store.bulk_operations(course_key):
            course = store.get_course(course_key, depth=self._required_course_depth(request))
            yield course

    @staticmethod
    def _required_course_depth(request):
        """
        Returns how far deep we need to go into the course tree to
        get all of the information required.  Will use entire tree if the request's
        `all` param is truthy, otherwise goes to depth of 2 (subsections).
        """
        all_requested = get_bool_param(request, 'all', False)
        if all_requested:
            return None
        return 2

    @classmethod
    @request_cached()
    def _get_visible_subsections(cls, course):
        """
        Returns a list of all visible subsections for a course.
        """
        _, visible_sections = cls._get_sections(course)
        visible_subsections = []
        for section in visible_sections:
            visible_subsections.extend(cls._get_visible_children(section))
        return visible_subsections

    @classmethod
    @request_cached()
    def _get_sections(cls, course):
        """
        Returns all sections in the course.
        """
        return cls._get_all_children(course)

    @classmethod
    def _get_all_children(cls, parent):
        """
        Returns all child nodes of the given parent.
        """
        store = modulestore()
        children = [store.get_item(child_usage_key) for child_usage_key in cls._get_children(parent)]
        visible_children = [
            c for c in children
            if not c.visible_to_staff_only and not c.hide_from_toc
        ]
        return children, visible_children

    @classmethod
    def _get_visible_children(cls, parent):
        """
        Returns only the visible children of the given parent.
        """
        _, visible_chidren = cls._get_all_children(parent)
        return visible_chidren

    @classmethod
    def _get_children(cls, parent):
        """
        Returns the value of the 'children' attribute of a node.
        """
        if not hasattr(parent, 'children'):
            return []
        else:
            return parent.children


def get_bool_param(request, param_name, default):
    """
    Given a request, parameter name, and default value, returns
    either a boolean value or the default.
    """
    param_value = request.query_params.get(param_name, None)
    bool_value = to_bool(param_value)
    if bool_value is None:
        return default
    else:
        return bool_value


def course_author_access_required(view):
    """
    Ensure the user making the API request has course author access to the given course.

    This decorator parses the course_id parameter, checks course access, and passes
    the parsed course_key to the view as a parameter. It will raise a
    403 error if the user does not have author access.

    Usage::
        @course_author_access_required
        def my_view(request, course_key):
            # Some functionality...
    """
    def _wrapper_view(self, request, course_id, *args, **kwargs):
        """
        Checks for course author access for the given course by the requesting user.
        Calls the view function if has access, otherwise raises a 403.
        """
        course_key = CourseKey.from_string(course_id)
        if not has_course_author_access(request.user, course_key):
            raise DeveloperErrorViewMixin.api_error(
                status_code=status.HTTP_403_FORBIDDEN,
                developer_message='The requesting user does not have course author permissions.',
                error_code='user_permissions',
            )
        return view(self, request, course_key, *args, **kwargs)
    return _wrapper_view


def get_ready_to_migrate_legacy_library_content_blocks(course_key: CourseKey) -> list[LegacyLibraryContentBlock]:
    """
    Get the ready to migrate legacy library content blocks for a course.

    Args:
        course_key (CourseKey): The key of the course

    Returns:
        List[XBlock]: A list of XBlock objects that are marked as ready
    """
    store = modulestore()
    blocks = store.get_items(course_key, qualifiers={'category': 'library_content'})
    ready_to_migrate_blocks = [block for block in blocks if block.is_ready_to_migrate_to_v2]
    return ready_to_migrate_blocks
