"""Error types this version of the User API classifies for itself."""

from edx_rest_framework_extensions.errors import register_error_type
from rest_framework.exceptions import MethodNotAllowed, NotAcceptable


class RefusedMethod(MethodNotAllowed):
    """A request method this API does not serve at the address it was sent to."""


class UnsatisfiableAccept(NotAcceptable):
    """An ``Accept`` header naming no representation this API can produce."""


register_error_type(RefusedMethod, "method-not-allowed", "Method Not Allowed")
register_error_type(UnsatisfiableAccept, "not-acceptable", "Not Acceptable")


class CatalogedRequestErrorsMixin:
    """
    Names the error type of a refused method and of an unanswerable ``Accept`` header.

    The shared catalog classifies neither, so both would reach the caller
    described as a server fault at a 4xx status. Raising this API's own
    subclasses of them names each error without changing how the same failure
    is classified for any other API in the process; once the shared catalog
    covers them, the subclasses can give way to it.
    """

    def handle_exception(self, exc):
        """Classify the two request failures the shared catalog leaves unnamed."""
        if isinstance(exc, MethodNotAllowed) and not isinstance(exc, RefusedMethod):
            exc = RefusedMethod(self.request.method, detail=exc.detail)
        elif isinstance(exc, NotAcceptable) and not isinstance(exc, UnsatisfiableAccept):
            exc = UnsatisfiableAccept(detail=exc.detail)
        return super().handle_exception(exc)
