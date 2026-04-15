"""
Utilities for the REST API views.
"""
from functools import wraps

from django.http import HttpResponseBadRequest


def validate_request_with_serializer(view_func):
    """
    A decorator to validate request data using the view's serializer.

    Usage:
    @validate_request_with_serializer
    def my_view_function(self, request, ...):
        ...
    """
    @wraps(view_func)
    def _wrapped_view(instance, request, *args, **kwargs):
        serializer = instance.serializer_class(data=request.data)
        if not serializer.is_valid():
            return HttpResponseBadRequest(reason=serializer.errors)
        return view_func(instance, request, *args, **kwargs)
    return _wrapped_view
