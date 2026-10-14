"""Pagination for version 2 of the User API."""

from edx_rest_framework_extensions.paginators import DefaultPagination


class UserApiPagination(DefaultPagination):
    """
    Page-number pagination whose published schema matches the body it returns.

    ``DefaultPagination`` answers with ``count``, ``num_pages``,
    ``current_page``, ``start``, ``next``, ``previous`` and ``results`` but
    inherits the four-member schema of DRF's ``PageNumberPagination``, so a
    generated client would not know about the three page-position members.
    This subclass only restates the response schema; the body is unchanged.
    """

    def get_paginated_response_schema(self, schema):
        """Describe the full envelope the paginator returns."""
        return {
            "type": "object",
            "required": ["count", "num_pages", "current_page", "start", "results"],
            "properties": {
                "count": {
                    "type": "integer",
                    "example": 123,
                    "description": "Total number of items across every page.",
                },
                "num_pages": {
                    "type": "integer",
                    "example": 13,
                    "description": "Total number of pages.",
                },
                "current_page": {
                    "type": "integer",
                    "example": 2,
                    "description": "Number of the page in this response, counting from one.",
                },
                "start": {
                    "type": "integer",
                    "example": 10,
                    "description": "Index of the first item of this page within the full result set.",
                },
                "next": {
                    "type": "string",
                    "nullable": True,
                    "format": "uri",
                    "description": "URL of the next page, or null on the last page.",
                },
                "previous": {
                    "type": "string",
                    "nullable": True,
                    "format": "uri",
                    "description": "URL of the previous page, or null on the first page.",
                },
                "results": schema,
            },
        }
