"""
Helpers for marking superseded cohort API versions.
"""


class DeprecatedAPIViewMixin:
    """
    Advertises a view as superseded by a newer version of the same resource.

    Adds the ``Deprecation`` and ``Link`` response headers described in RFC 8594
    so that remaining callers can be identified from access logs before the
    superseded routes are removed.

    Subclasses set ``successor_path`` to the path that replaces this one.
    """

    successor_path = None

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Deprecation"] = "true"
        if self.successor_path:
            response["Link"] = f'<{self.successor_path}>; rel="successor-version"'
        return response
