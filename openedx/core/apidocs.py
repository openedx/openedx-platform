"""
Open API support.
"""

from django.conf import settings
from rest_framework import serializers

# Settings for the service-wide ``/api-docs`` schema, served by drf-spectacular.
#
# These are passed as ``custom_settings`` to SpectacularAPIView rather than
# living in ``SPECTACULAR_SETTINGS``, because that global is already claimed by
# a deliberately narrow schema in each service -- the Authoring API
# (``/authoring-api/``) in CMS and the Enrollment API (``/lms-api/``) in LMS.
# Both filter the surface down via ``PREPROCESSING_HOOKS`` and trim a path
# prefix. ``/api-docs`` is the opposite: the full, untrimmed API surface, so it
# must switch that filtering off explicitly.
# Note: ``SERVE_*`` settings cannot be overridden through ``custom_settings``
# (drf-spectacular raises AttributeError); SpectacularAPIView takes dedicated
# constructor arguments for those instead.
API_DOCS_SETTINGS = {
    'TITLE': 'Open edX API',
    'DESCRIPTION': 'APIs for access to Open edX information',
    'VERSION': 'v1',
    # Document every endpoint, without the per-service filtering and prefix
    # trimming that SPECTACULAR_SETTINGS applies.
    'PREPROCESSING_HOOKS': [],
    'SCHEMA_PATH_PREFIX': None,
    'SCHEMA_PATH_PREFIX_TRIM': False,
    'SERVERS': [],
}


def get_api_docs_settings():
    """
    Build the ``/api-docs`` schema settings, adding contact details if available.

    ``API_ACCESS_MANAGER_EMAIL`` is an LMS-only setting, so it is included only
    where it is defined.
    """
    api_docs_settings = dict(API_DOCS_SETTINGS)
    contact_email = getattr(settings, 'API_ACCESS_MANAGER_EMAIL', None)
    if contact_email:
        api_docs_settings['CONTACT'] = {'email': contact_email}
    return api_docs_settings


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
