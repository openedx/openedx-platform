"""
Decorators for courseware views.
"""
import functools

from django.shortcuts import redirect
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from openedx_filters.learning.filters import CoursewareViewStarted


def courseware_view_hooks(view_func):
    """
    Decorator that calls the CoursewareViewStarted filter before rendering a courseware view.

    If any pipeline step raises ``CoursewareViewStarted.RedirectToUrl``, the user is
    redirected to that URL. Otherwise, the original view is rendered normally.

    Usage::

        @courseware_view_hooks
        def my_view(request, course_id, ...):
            ...

    Works with both function-based views and ``method_decorator``-wrapped class-based views.
    The wrapped view must accept ``course_id`` as its first argument after ``request``, which
    binds whether callers pass it positionally or as a URL keyword argument.
    """
    @functools.wraps(view_func)
    def _wrapper(request, course_id, *args, **kwargs):
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError:
            # Skip bad request which contains a malformed course_id; let the view logic raise an error.
            return view_func(request, course_id, *args, **kwargs)

        try:
            view_name = getattr(view_func, '__name__', '')
            CoursewareViewStarted.run_filter(course_key=course_key, view_name=view_name)
        except CoursewareViewStarted.RedirectToUrl as exc:
            # One of the pipeline steps wants us to block view execution and redirect to a specific URL.
            return redirect(exc.redirect_to)

        return view_func(request, course_id, *args, **kwargs)

    return _wrapper
