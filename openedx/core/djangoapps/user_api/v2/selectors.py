"""Selections over the user records version 2 of the User API answers from."""

import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user

from openedx.core.djangoapps.user_api.models import UserRetirementStatus


def accounts_holding_emails(emails):
    """Return the accounts holding any of these email addresses, by name."""
    return User.objects.filter(email__in=emails).order_by("username")


def usernames_among(names, ordering):
    """
    Return the names these accounts are stored under, in the order asked for.

    The accounts an address or a query string names are the only ones the
    answer carries, and a name no account holds matches nothing. The order is
    settled here because it decides which rows fall on which page, and the
    database compares and sorts the stored names: an account can therefore
    come back under a spelling other than the one asked for.
    """
    return (
        User.objects
        .filter(username__in=names)
        .order_by(ordering)
        .values_list("username", flat=True)
    )


def username_holding_email(email):
    """Return the name of the account holding this address, or ``None`` when no account holds it."""
    try:
        return User.objects.get(email=email).username
    except User.DoesNotExist:
        return None


def username_holding_id(identifier):
    """Return the name of the account holding this numeric identifier, or ``None``."""
    try:
        return User.objects.get(id=identifier).username
    except User.DoesNotExist:
        return None


def cancellable_retirement_for_email(email):
    """
    Return the retirement of the account that held this address, while it can still be cancelled.

    A retirement is cancellable for as long as the platform's cooling-off
    period runs, so a retirement older than that is not reported here: the
    address is gone either way, and only a retirement inside the period can be
    undone by asking the platform to undo it.
    """
    now = datetime.datetime.now(ZoneInfo("UTC"))
    return (
        UserRetirementStatus.objects
        .filter(
            created__gt=now - datetime.timedelta(days=settings.COOL_OFF_DAYS),
            created__lt=now,
            original_email=email,
        )
        .order_by("-created")
        .first()
    )
