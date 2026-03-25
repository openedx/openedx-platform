"""
Open API support.
"""

from django.conf import settings  # noqa: I001
from edx_api_doc_tools import make_api_info
from rest_framework import serializers

api_info = make_api_info(
    title="Open edX API",
    version="v1",
    description="APIs for access to Open edX information",
    #terms_of_service="https://www.google.com/policies/terms/",         # TODO: Do we have these?
    email=settings.API_ACCESS_MANAGER_EMAIL,
    #license=openapi.License(name="BSD License"),                       # TODO: What does this mean?
)


def cursor_paginate_serializer(inner_serializer_class):
    """
    Create a cursor-paginated version of a serializer.

    This is hacky workaround for an edx-api-doc-tools issue described here:
    https://github.com/openedx/api-doc-tools/issues/32

    It assumes we are using cursor-style pagination and assumes a specific
    schema for the pages. It should be removed once we address the underlying issue.

    Arguments:
        inner_serializer_class (type): A subclass of ``Serializer``.

    Returns: type
        A subclass of ``Serializer`` to model the schema of a page of a cursor-paginated
        endpoint.
    """
    class PageOfInnerSerializer(serializers.Serializer):
        """
        A serializer for a page of a cursor-paginated list of ``inner_serializer_class``.
        """
        # pylint: disable=abstract-method
        previous = serializers.URLField(
            required=False,
            help_text="Link to the previous page or results, or null if this is the first.",
        )
        next = serializers.URLField(
            required=False,
            help_text="Link to the next page of results, or null if this is the last.",
        )
        results = serializers.ListField(
            child=inner_serializer_class(),
            help_text="The list of result objects on this page.",
        )

    PageOfInnerSerializer.__name__ = f'PageOf{inner_serializer_class.__name__}'
    return PageOfInnerSerializer
