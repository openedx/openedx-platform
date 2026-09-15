"""
Django Rest Framework view mixins.
"""

from django.core.exceptions import ValidationError
from django.http import Http404
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from rest_framework import status
from rest_framework.mixins import CreateModelMixin
from rest_framework.response import Response

# StandardizedErrorMixin is re-exported for existing import sites; new code
# should import it from edx_rest_framework_extensions.mixins directly.
__all__ = ["PutAsCreateMixin", "StandardizedErrorMixin"]


class PutAsCreateMixin(CreateModelMixin):
    """
    Backwards compatibility with Django Rest Framework v2, which allowed
    creation of a new resource using PUT.
    """

    def update(self, request, *args, **kwargs):
        """
        Create/update course modes for a course.
        """
        # First, try to update the existing instance
        try:
            try:
                return super().update(request, *args, **kwargs)
            except Http404:
                # If no instance exists yet, create it.
                # This is backwards-compatible with the behavior of DRF v2.
                return super().create(request, *args, **kwargs)

        # Backwards compatibility with DRF v2 behavior, which would catch model-level
        # validation errors and return a 400
        except ValidationError as err:
            return Response(err.messages, status=status.HTTP_400_BAD_REQUEST)
