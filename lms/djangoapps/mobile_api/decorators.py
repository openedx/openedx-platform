"""
Decorators for Mobile APIs.
"""


import functools

from django.http import Http404
from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from rest_framework.response import Response

from lms.djangoapps.course_goals.models import UserActivity
from lms.djangoapps.courseware.courses import get_course_with_access
from lms.djangoapps.courseware.courseware_access_exception import CoursewareAccessException
from lms.djangoapps.courseware.exceptions import CourseAccessRedirect
from openedx.core.lib.api.view_utils import view_auth_classes
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order


def mobile_course_access(depth=0):
    """
    Method decorator for a mobile API endpoint that verifies the user has access to the course in a mobile context.
    """
    def _decorator(func):
        """Outer method decorator."""

        @functools.wraps(func)
        def _wrapper(self, request, *args, **kwargs):
            """
            Expects kwargs to contain 'course_id'.
            Passes the course block to the given decorated function.
            Raises 404 if access to course is disallowed.
            """
            course_id = CourseKey.from_string(kwargs.pop('course_id'))
            with modulestore().bulk_operations(course_id):
                try:
                    course = get_course_with_access(
                        request.user,
                        'load_mobile',
                        course_id,
                        depth=depth,
                        check_if_enrolled=True,
                    )
                    # Record user activity for tracking progress towards a user's course goals (for mobile app)
                    UserActivity.record_user_activity(
                        request.user, course_id, request=request, only_if_mobile_app=True
                    )
                except CoursewareAccessException as error:
                    return Response(data=error.to_json(), status=status.HTTP_404_NOT_FOUND)
                except CourseAccessRedirect as error:
                    # If the redirect contains information about the triggering AccessError,
                    # return the information contained in the AccessError.
                    if error.access_error is not None:
                        return Response(data=error.access_error.to_json(), status=status.HTTP_404_NOT_FOUND)
                    # Raise a 404 if the user does not have course access
                    raise Http404  # pylint: disable=raise-missing-from  # noqa: B904
                return func(self, request, course=course, *args, **kwargs)  # noqa: B026

        return _wrapper
    return _decorator


def mobile_view(is_user=False):
    """
    Function and class decorator that abstracts the authentication and permission checks for mobile api views.
    """
    return view_auth_classes(is_user)
