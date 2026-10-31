"""Pagination shared by the contentstore REST API views."""

from edx_rest_framework_extensions.paginators import DefaultPagination

ENVELOPE_KEYS = ("count", "num_pages", "current_page", "start", "next", "previous", "results")


class DocumentedDefaultPagination(DefaultPagination):
    """
    The standard page envelope, with a response schema describing all seven of its keys.

    ``DefaultPagination`` returns ``count``, ``num_pages``, ``current_page``,
    ``start``, ``next``, ``previous`` and ``results``, but inherits a response
    schema that documents only four of them, so generated clients would drop
    ``num_pages``, ``current_page`` and ``start``. Pages, page sizes and the
    body are exactly ``DefaultPagination``'s; only the published schema
    differs. Every member carries an example, so the schema generator can wrap
    a view's example item in a whole page. Once ``DefaultPagination``
    documents all seven keys, use it directly instead.
    """

    def get_paginated_response_schema(self, schema):
        """Describe a page whose ``results`` are items described by ``schema``."""
        return {
            "type": "object",
            "required": list(ENVELOPE_KEYS),
            "properties": {
                "count": {"type": "integer", "description": "Total number of items.", "example": 25},
                "num_pages": {"type": "integer", "description": "Total number of pages.", "example": 3},
                "current_page": {
                    "type": "integer",
                    "description": "Number of this page, starting at 1.",
                    "example": 2,
                },
                "start": {
                    "type": "integer",
                    "description": "Zero-based index of the first item on this page.",
                    "example": 10,
                },
                "next": {
                    "type": "string",
                    "format": "uri",
                    "nullable": True,
                    "description": "Address of the next page, or null on the last page.",
                    "example": "https://example.com/items/?page=3",
                },
                "previous": {
                    "type": "string",
                    "format": "uri",
                    "nullable": True,
                    "description": "Address of the previous page, or null on the first page.",
                    "example": "https://example.com/items/?page=1",
                },
                "results": schema,
            },
        }
