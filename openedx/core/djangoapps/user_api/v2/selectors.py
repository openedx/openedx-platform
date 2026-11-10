"""Selections over the user records version 2 of the User API answers from."""

import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user

from common.djangoapps.student.models import get_potentially_retired_user_by_username
from openedx.core.djangoapps.user_api.models import (
    UserRetirementPartnerReportingStatus,
    UserRetirementStatus,
)


def accounts_holding_emails(emails):
    """Return the accounts holding any of these email addresses, by name."""
    return User.objects.filter(email__in=emails).order_by("username")


def accounts_named(name):
    """
    Return the accounts this name addresses.

    The database settles which stored names an address answers to, and it need
    not be only the one written exactly as the address writes it, so a name
    can bring back more than one account.
    """
    return list(User.objects.filter(username=name))


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


def usernames_holding_email(email):
    """
    Return the names of the accounts holding this address.

    Nothing keeps one email address to one account, so an address can be held
    by more than one of them. Every account holding it is named, and nothing
    is named for an address no account holds.
    """
    return list(accounts_holding_emails([email]).values_list("username", flat=True))


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

    One address can have been held and left more than once, so more than one
    retirement of it can sit inside the period. The most recent is the one
    reported, being the only one still open to being undone. This is the one
    place where the answer differs from the superseded version, which reads
    the same window with a lookup that takes a single record and so fails
    outright on the second.
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


def retirements():
    """Return every retirement, with the account and the steps a retirement record reports."""
    return UserRetirementStatus.objects.select_related(
        "user", "user__profile", "current_state", "last_state",
    )


def retirement_of_the_account_named(records, username):
    """
    Return the retirement of the account this name addresses, or ``None``.

    A retired account is stored under a hashed name, so the name a caller
    holds may be either the one the account was registered under or the one it
    was moved to; both address the same account and so the same retirement.
    The record is taken from the selection given rather than from every record
    held, so a record outside it is reported as absent.
    """
    try:
        user = get_potentially_retired_user_by_username(username)
    except User.DoesNotExist:
        return None
    return records.filter(user=user).first()


def _grouped_by_requested_name(rows, usernames):
    """
    Return these rows under the name each was requested with, every row kept.

    Stored names are compared without regard to case in some deployments,
    which once let a learner register a retired name again by changing its
    capitalisation, so the rows the database offers are compared again here
    character for character. The name a row keeps of the account it is for is
    indexed but not unique, so a name may carry more than one row; every one
    of them is returned, because a caller that acted on one of several would
    act on a record it did not ask about. A name no row was requested under is
    absent from the result.
    """
    asked_for = set(usernames)
    grouped = {}
    for row in rows:
        if row.original_username in asked_for:
            grouped.setdefault(row.original_username, []).append(row)
    return grouped


def retirements_requested_as(records, usernames):
    """Return the records of this selection requested under exactly these names, grouped by name."""
    return _grouped_by_requested_name(
        records.filter(original_username__in=usernames), usernames,
    )


def unclaimed_partner_reports():
    """
    Return the partner reports no caller has taken for processing yet.

    The organizations of each are read from the user's enrolments, which are
    fetched with the rows so that the cost of the answer does not grow with
    the number of rows in it.
    """
    return (
        UserRetirementPartnerReportingStatus.objects
        .filter(is_being_processed=False)
        .select_related("user")
        .prefetch_related("user__courseenrollment_set")
        .order_by("id")
    )


def partner_reports():
    """Return every partner report entry, whether or not it has been taken for processing."""
    return UserRetirementPartnerReportingStatus.objects.all()


def partner_reports_requested_as(entries, usernames):
    """Return the entries of this selection registered under exactly these names, grouped by name."""
    return _grouped_by_requested_name(
        entries.filter(original_username__in=usernames), usernames,
    )
