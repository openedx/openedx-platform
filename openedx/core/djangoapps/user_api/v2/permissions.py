"""Permission classes for version 2 of the User API."""

from rest_framework.permissions import BasePermission


class IsActiveUser(BasePermission):
    """
    Admits only a caller whose account is still active.

    The authenticated and administrator checks read ``is_authenticated`` and
    ``is_staff``, and neither reads ``is_active``. A token stays valid until it
    expires and is accepted without the account being re-read for it, so an
    account deactivated after a token was issued keeps every access that token
    carries unless the account's state is checked here. edx-drf-extensions
    ships no equivalent class, so this one stands in for it.
    """

    def has_permission(self, request, view):
        """Return whether the caller's account is still active."""
        return bool(request.user and request.user.is_active)
