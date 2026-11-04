"""Error types this version of the User API classifies for itself."""

from edx_rest_framework_extensions.errors import register_error_type
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.settings import api_settings


class AmbiguousAccountName(ValidationError):
    """
    An address answered by more than one account, naming no one of them.

    The refusal is one about what the request asked for, so it keeps the
    status of the refusals beside it; what it adds is a name for the one
    condition, which a caller can tell apart from a value it sent being
    wrong and can act on by naming an account as it is stored.
    """


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


register_error_type(RetiredAccount, "user/retired-account", "Retired Account")
register_error_type(AmbiguousAccountName, "user/ambiguous-account-name", "Ambiguous Account Name")


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


class ReadableErrorDetailMixin:
    """
    Writes the one-line message of a refused request as a sentence.

    The standard error body carries a line for a person to read beside the
    list of what each parameter or property was refused for, and where the
    refusal names them that line was produced by printing the list itself.
    Everything that puts the line in front of a person then shows the
    brackets, the quoting and the class names that write a data structure
    out: the message of the exception a generated client raises, a line in an
    integration's log, the notification a page puts on the screen. The list is
    left exactly as it was; only the line is rewritten, from the same values.
    """

    def handle_exception(self, exc):
        """Answer with the standard body, its one-line message written in words."""
        response = super().handle_exception(exc)
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
