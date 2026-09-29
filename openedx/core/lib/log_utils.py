"""
Helper functions for logging.
"""

import logging

from django.conf import settings

log = logging.getLogger(__name__)


def get_username_or_pii_safe_user_id_for_log(user):
    """
    Return the identifier to use for ``user`` in a log message.

    Returns ``user.id`` when the ``SQUELCH_PII_IN_LOGS`` setting is enabled, and
    ``user.username`` otherwise.

    A numeric user id is not PII and can always be logged directly without this
    function. Use this function only where the username is preferred in logs for
    deployments that allow PII in logs.

    For an ``AnonymousUser`` this returns ``None`` (squelched) or ``''`` (its username).

    Arguments:
        user (User): the user to identify in the log message.

    Returns:
        int or str: the user id or the username.
    """
    if getattr(settings, 'SQUELCH_PII_IN_LOGS', False):
        return user.id
    return user.username


def get_email_or_pii_safe_user_id_for_log(user):
    """
    Return the identifier to use for ``user`` in a log message.

    Returns ``user.id`` when the ``SQUELCH_PII_IN_LOGS`` setting is enabled, and
    ``user.email`` otherwise.

    A numeric user id is not PII and can always be logged directly without this
    function. Use this function only where the email is preferred in logs for
    deployments that allow PII in logs.

    For an ``AnonymousUser``, which has no email, this returns ``None`` (squelched)
    or ``''`` rather than raising from inside a log call.

    Arguments:
        user (User): the user to identify in the log message.

    Returns:
        int or str: the user id or the email.
    """
    if getattr(settings, 'SQUELCH_PII_IN_LOGS', False):
        return user.id
    return getattr(user, 'email', '')


def audit_log(name, **kwargs):
    """
    DRY helper used to emit an INFO-level log message.

    Messages logged with this function are used to construct an audit trail. Log messages
    should be emitted immediately after the event they correspond to has occurred and, if
    applicable, after the database has been updated. These log messages use a verbose
    key-value pair syntax to make it easier to extract fields when parsing the application's
    logs.

    This function is variadic, accepting a variable number of keyword arguments.

    Arguments:
        name (str): The name of the message to log. For example, 'payment_received'.

    Keyword Arguments:
        Indefinite. Keyword arguments are strung together as comma-separated key-value
        pairs ordered alphabetically by key in the resulting log message.

    Returns:
        None
    """
    # Joins sorted keyword argument keys and values with an "=", wraps each value
    # in quotes, and separates each pair with a comma and a space.
    payload = ', '.join([f'{k}="{v}"' for k, v in sorted(kwargs.items())])
    message = f'{name}: {payload}'

    log.info(message)
