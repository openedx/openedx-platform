"""Error types this version of the User API classifies for itself."""

from edx_rest_framework_extensions.errors import register_error_type
from rest_framework.exceptions import (
    MethodNotAllowed,
    NotAcceptable,
    NotFound,
    ParseError,
    UnsupportedMediaType,
)


class RefusedMethod(MethodNotAllowed):
    """A request method this API does not serve at the address it was sent to."""


class UnsatisfiableAccept(NotAcceptable):
    """An ``Accept`` header naming no representation this API can produce."""


class UnreadableRepresentation(UnsupportedMediaType):
    """A request body in a media type this API cannot read."""


class MalformedBody(ParseError):
    """A request body that is not well formed in the media type it was sent as."""


class RetiredAccount(NotFound):
    """
    An account that has been retired, addressed by something it used to hold.

    The account is gone, so the answer is the same as for an address that was
    never held; what is left of it is the retirement record, which the members
    below name so that a caller able to reverse a retirement can find it.
    """

    def __init__(self, detail=None, can_cancel_retirement=False, retirement_id=None):
        super().__init__(detail)
        self.extension_members = {
            "can_cancel_retirement": can_cancel_retirement,
            "retirement_id": retirement_id,
        }


register_error_type(RefusedMethod, "method-not-allowed", "Method Not Allowed")
register_error_type(UnsatisfiableAccept, "not-acceptable", "Not Acceptable")
register_error_type(UnreadableRepresentation, "unsupported-media-type", "Unsupported Media Type")
register_error_type(MalformedBody, "malformed-body", "Malformed Request Body")
register_error_type(RetiredAccount, "user/retired-account", "Retired Account")


class CatalogedRequestErrorsMixin:
    """
    Names the error type of a refused method, an unanswerable ``Accept``
    header, a body in a media type this API cannot read and a body that is not
    well formed in the media type it was sent as.

    The shared catalog classifies none of them, so each would reach the caller
    described as a server fault at a 4xx status. Raising this API's own
    subclasses of them names each error without changing how the same failure
    is classified for any other API in the process; once the shared catalog
    covers them, the subclasses can give way to it.
    """

    def handle_exception(self, exc):
        """Classify the request failures the shared catalog leaves unnamed."""
        if isinstance(exc, MethodNotAllowed) and not isinstance(exc, RefusedMethod):
            exc = RefusedMethod(self.request.method, detail=exc.detail)
        elif isinstance(exc, NotAcceptable) and not isinstance(exc, UnsatisfiableAccept):
            exc = UnsatisfiableAccept(detail=exc.detail)
        elif isinstance(exc, UnsupportedMediaType) and not isinstance(exc, UnreadableRepresentation):
            exc = UnreadableRepresentation(self.request.content_type, detail=exc.detail)
        elif isinstance(exc, ParseError) and not isinstance(exc, MalformedBody):
            exc = MalformedBody(detail=exc.detail)
        return super().handle_exception(exc)


class ErrorExtensionsMixin:
    """
    Adds the members an exception declares to the error body it produces.

    The shared envelope describes a failure with a fixed set of members, so a
    failure that has more to say about the resource -- what can still be done
    about it, which record holds the rest -- has nowhere to put it. An error
    body may carry members beside the standard ones, and the ones an exception
    declares are put there.
    """

    def handle_exception(self, exc):
        """Answer with the standard body, extended by the members the exception declares."""
        response = super().handle_exception(exc)
        members = getattr(exc, "extension_members", None)
        if members and isinstance(getattr(response, "data", None), dict):
            response.data.update(members)
        return response
