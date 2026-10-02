"""Pagination for the Grades API v2 list endpoints."""

from edx_rest_framework_extensions.paginators import DefaultPagination


class GradePagination(DefaultPagination):
    """
    The shared page-number paginator, with its response body described to the schema generator.

    The shared class builds the seven-field body but does not describe it, so
    without this override the published schema lists four of the seven fields
    callers actually receive. Remove the override once the shared class
    describes its own body.
    """

    def get_paginated_response_schema(self, schema):
        """Describe the paginated response body this paginator returns."""
        return {
            'type': 'object',
            'required': ['count', 'num_pages', 'current_page', 'start', 'next', 'previous', 'results'],
            'properties': {
                'count': {
                    'type': 'integer',
                    'description': 'Total number of results across every page.',
                },
                'num_pages': {
                    'type': 'integer',
                    'description': 'Total number of pages.',
                },
                'current_page': {
                    'type': 'integer',
                    'description': 'Number of the page in this response, counting from one.',
                },
                'start': {
                    'type': 'integer',
                    'description': 'Index of the first result on this page, counting from zero.',
                },
                'next': {
                    'type': 'string',
                    'format': 'uri',
                    'nullable': True,
                    'description': 'URL of the next page, or null on the last page.',
                },
                'previous': {
                    'type': 'string',
                    'format': 'uri',
                    'nullable': True,
                    'description': 'URL of the previous page, or null on the first page.',
                },
                'results': schema,
            },
        }
