"""Permission classes for version 2 of the User API."""

from rest_framework.exceptions import NotFound
from rest_framework.permissions import BasePermission


class IsActiveUser(BasePermission):
    """
    Admits only a caller whose account is still active.

    Neither the authenticated check nor the administrator check reads
    ``is_active``, so a view whose answer turns on the state of the caller's
    own account declares this class alongside them. edx-drf-extensions ships
    no equivalent class, so this one stands in for it.
    """

    def has_permission(self, request, view):
        """Return whether the caller's account is still active."""
        return bool(request.user and request.user.is_active)


#: Query parameters that pick an account out by something about its owner
#: that the owner has not published.
ACCOUNT_CONTACT_SELECTORS = ("email", "lms_user_id")


class CanSelectAccountsByContact(BasePermission):
    """
    Admits a caller selecting accounts by an owner's address or identifier while their account is active.

    An account selected by the email address or the numeric identifier of its
    owner is reached without its owner having published either, which is a
    privilege, so it is refused to a caller whose own account is no longer
    active. A request that selects accounts no such way is admitted whatever
    state the caller's account is in, because a user reaches their own account
    before they have confirmed its address.
    """

    def has_permission(self, request, view):
        """Return whether this request selects no account by contact, or comes from an active account."""
        if all(request.query_params.get(name) is None for name in ACCOUNT_CONTACT_SELECTORS):
            return True
        return bool(request.user and request.user.is_active)


class IsAddressedUser(BasePermission):
    """
    Admits a caller whose username is exactly the one the address carries.

    The handlers behind this class select a record by the username in the
    address, so that is the value compared here, character for character, and
    the address is the only place a username is read from. Where usernames are
    stored case-sensitively, two names differing in case alone are two
    different names, so the comparison is exact.
    """

    def has_permission(self, request, view):
        """Return whether the address names this caller, and nobody else."""
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return getattr(view, "kwargs", {}).get("username") == user.username


class IsStaffOrAbsent(BasePermission):
    """
    Admits a platform administrator and reports the address as absent to anyone else.

    The endpoints this guards answer questions about other people's accounts.
    Refusing a caller who may not ask them with a refusal would confirm that
    the address exists and what it is for, so they are told instead that there
    is nothing at it.
    """

    def has_permission(self, request, view):
        """Admit an administrator, and report the address as absent to every other caller."""
        if request.user and request.user.is_staff:
            return True
        raise NotFound
