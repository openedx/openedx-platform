"""
Django Rest Framework view mixins.
"""

from django.core.exceptions import ValidationError
from django.http import Http404
from rest_framework import status
from rest_framework.mixins import CreateModelMixin
from rest_framework.response import Response

from openedx.core.lib.api.exceptions import standardized_error_exception_handler


class StandardizedErrorMixin:
    """
    Opt-in mixin that routes DRF exceptions on this view through the ADR 0029
    standardized error-response handler (see
    ``openedx.core.lib.api.exceptions.standardized_error_exception_handler``).

    DRF's :class:`rest_framework.views.APIView` calls ``self.get_exception_handler``
    inside ``handle_exception``; overriding that method here lets the view
    return the standardized envelope while other endpoints continue to use
    whichever handler the project-wide ``EXCEPTION_HANDLER`` setting points at.

    Usage::

        class MyViewSet(StandardizedErrorMixin, viewsets.ViewSet):
            ...
    """

    def get_exception_handler(self):
        return standardized_error_exception_handler


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
