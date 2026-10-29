"""Pagination for version 2 of the User API."""

import logging

from edx_rest_framework_extensions.paginators import DefaultPagination
from rest_framework.exceptions import ValidationError

log = logging.getLogger(__name__)


class UserApiPagination(DefaultPagination):
    """
    Page-number pagination whose published schema matches the body it returns,
    and which refuses a number of rows no page of it can carry.

    ``DefaultPagination`` answers with ``count``, ``num_pages``,
    ``current_page``, ``start``, ``next``, ``previous`` and ``results`` but
    inherits the four-member schema of DRF's ``PageNumberPagination``, so a
    generated client would not know about the three page-position members.
    The response schema below only restates them; the body is unchanged.
    """

    def get_page_size(self, request):
        """
        Return how many rows a page carries, refusing a number no page can have.

        The number asked for is otherwise dropped where it cannot be honoured
        and the default page answered in its place, which a caller reads as
        the page it asked for; worse, the links on that page carry the value
        back, so a client that follows them keeps sending a number that is
        never read. A number above what a page carries is still answered with
        as many rows as it carries, because asking for more rows than a page
        holds is asking for a full one.
        """
        asked = request.query_params.get(self.page_size_query_param)
        if asked is None or asked == "":
            return self.page_size
        try:
            rows = int(asked)
        except ValueError:
            rows = 0
        if rows < 1:
            log.info("Page size refused at %s: %r", request.path, asked)
            raise ValidationError({
                self.page_size_query_param: [
                    "Enter a whole number of rows of at least 1. A larger number than "
                    f"{self.max_page_size} returns {self.max_page_size} rows.",
                ],
            })
        return min(rows, self.max_page_size)

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
