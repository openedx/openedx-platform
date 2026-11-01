"""Error types this version of the User API classifies for itself."""

from edx_rest_framework_extensions.errors import Conflict, register_error_type
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.settings import api_settings


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


class _NamesTheRecordsAtFault:
    """
    Carries the records a refusal is about, each with what was wrong with it.

    A caller acting on several records at once learns which of them to look
    at, rather than being told only that the request as a whole was refused.
    Nothing is changed when any one record is refused.
    """

    def __init__(self, detail=None, errors=None):
        super().__init__(detail)
        if errors:
            self.extension_members = {"errors": errors}


class RetirementStateConflict(_NamesTheRecordsAtFault, Conflict):
    """
    An operation asked of a retirement whose state does not admit it.

    Which operation a retirement admits depends on how far through the
    pipeline it is, and the order of those steps is configured by the
    operator rather than fixed here.
    """


class AccountDeletionUnavailable(PermissionDenied):
    """
    A request to close an account where this deployment does not offer that.

    Whether an account's owner may close it is a deployment's own choice, and
    a site it serves may answer differently from the deployment as a whole, so
    a caller refused here may be served at another site of the same
    deployment. Nothing about the request itself is at fault.
    """


class PasswordNotConfirmed(PermissionDenied):
    """
    A request that has to be confirmed with the caller's own password was not.

    The value sent is not the password of the account the request was made
    from. The request is refused whole; a caller that holds the password sends
    it again.
    """


class AccountTemporarilyLocked(PermissionDenied):
    """
    A request from an account closed to password attempts for a while.

    Several attempts have been made with a password the account does not
    hold, which closes it to further ones until the period the operator
    configured has passed. Nothing the caller sends is accepted until then.
    """


class AmbiguousAccountName(_NamesTheRecordsAtFault, ValidationError):
    """
    An address or a name answered by more than one account, naming no one of them.

    The name an account was registered under is kept on the records of this
    area without being unique to one of them, so a request naming such a name
    reaches several records at once and acting on any of them would act on an
    account the caller did not ask about. Nothing is done. The refusal is one
    about what the request asked for, so it keeps the status of the refusals
    beside it; what it adds is a name for this one condition, which a caller
    can tell apart from a value it sent being wrong and can act on by naming
    an account as it is stored.
    """


register_error_type(RetiredAccount, "user/retired-account", "Retired Account")
register_error_type(AmbiguousAccountName, "user/ambiguous-account-name", "Ambiguous Account Name")
register_error_type(RetirementStateConflict, "user/retirement-state-conflict", "Retirement State Conflict")
register_error_type(
    AccountDeletionUnavailable, "user/account-deletion-unavailable", "Account Deletion Unavailable",
)
register_error_type(PasswordNotConfirmed, "user/password-not-confirmed", "Password Not Confirmed")
register_error_type(
    AccountTemporarilyLocked, "user/account-temporarily-locked", "Account Temporarily Locked",
)


def refusal_sentence(refusals):
    """
    Return one sentence naming what each parameter or property was refused for.

    A refusal that names nothing in particular is spelled on its own, so the
    sentence reads the same whether or not the thing refused has a name.
    """
    return " ".join(
        reason if name == api_settings.NON_FIELD_ERRORS_KEY else f"{name}: {reason}"
        for name, reasons in refusals.items()
        for reason in reasons
    )


def _holds_a_structure(value):
    """Whether a refusal's value is a mapping, or a list holding anything but messages."""
    return isinstance(value, dict) or (
        isinstance(value, list) and any(isinstance(item, (dict, list)) for item in value)
    )


def _refusals_by_item(detail, name):
    """
    Yield each message of a refusal with the name of the thing it is about.

    An item of a list is named after its position, counted from 0, and a
    property of an object after the property: ``social_links[0]``,
    ``social_links[0].social_link``. A refusal of an object as a whole keeps the
    object's name.
    """
    if isinstance(detail, dict):
        for key, inner in detail.items():
            if isinstance(key, int):
                yield from _refusals_by_item(inner, f"{name}[{key}]")
            elif key == api_settings.NON_FIELD_ERRORS_KEY:
                yield from _refusals_by_item(inner, name)
            else:
                yield from _refusals_by_item(inner, f"{name}.{key}")
    elif _holds_a_structure(detail):
        for index, inner in enumerate(detail):
            yield from _refusals_by_item(inner, f"{name}[{index}]")
    else:
        yield from ((name, message) for message in (detail if isinstance(detail, list) else [detail]))


def _named_by_item(detail):
    """
    Return a refusal with each refused item of a list listed under a name of its own.

    Nothing changes for a refusal naming only parameters or properties; one
    that names nothing in particular is never nested.
    """
    if not isinstance(detail, dict) or not any(_holds_a_structure(value) for value in detail.values()):
        return detail
    named = {}
    for field, refusal in detail.items():
        for name, message in _refusals_by_item(refusal, field):
            named.setdefault(name, []).append(message)
    return named


def _came_with_the_records_at_fault(exc):
    """
    Whether this refusal arrived carrying the records at fault, and a line of its own.

    Such a refusal was raised with both: a sentence about the request as a
    whole, and the record-by-record list that goes beside it. A refusal that
    arrived with a line alone has its list derived from that line, and the
    line written back from the list says the same thing.
    """
    return isinstance(getattr(exc, "extension_members", {}).get("errors"), dict)


class ReadableErrorDetailMixin:
    """
    Writes the one-line message of a refused request as a sentence.

    The standard error body carries a line for a person to read beside the
    list of what each parameter or property was refused for, and where the
    refusal names them that line was produced by printing the list itself.
    Everything that puts the line in front of a person then shows the
    brackets, the quoting and the class names that write a data structure
    out: the message of the exception a generated client raises, a line in an
    integration's log, the notification a page puts on the screen. The line is
    rewritten from the values in the list.

    A list a body carries is refused item by item, which the standard body
    would print, positions and class names included, as one message under the
    list's name. Each refused item is listed under a name of its own instead
    -- ``social_links[0].social_link`` -- so that the list and the line both say in words
    which item was refused and why.

    A refusal that names the records at fault wrote that line itself, as a
    sentence about the request as a whole -- that a set of records was left
    alone, and nothing deleted -- which the list of records cannot be read to
    say. That line is kept, and the records stay in the list beside it.
    """

    def handle_exception(self, exc):
        """Answer with the standard body, its one-line message written in words."""
        if isinstance(exc, ValidationError):
            exc.detail = _named_by_item(exc.detail)
        response = super().handle_exception(exc)
        if _came_with_the_records_at_fault(exc):
            return response
        body = getattr(response, "data", None)
        if isinstance(body, dict) and isinstance(body.get("errors"), dict):
            sentence = refusal_sentence(body["errors"])
            if sentence:
                body["detail"] = sentence
        return response


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
