"""
Helper functions for logging.
"""

import logging

from django.conf import settings

log = logging.getLogger(__name__)

ANONYMOUS_USER_FOR_LOG = '<AnonymousUser>'
REDACTED_FOR_LOG = '[REDACTED]'


def get_standalone_pii_or_redacted_for_log(value):
    """
    Return ``value`` as a string for a log message, or ``'[REDACTED]'`` when the
    ``SQUELCH_PII_IN_LOGS`` setting is enabled.

    Use this for standalone PII, when there is no access to a ``User`` object whose id
    could be logged instead: for example, an email or username that failed a lookup,
    or a username that is still being generated. When a ``User`` object is available,
    use ``get_username_or_pii_safe_user_id_for_log`` or
    ``get_email_or_pii_safe_user_id_for_log`` instead.

    Arguments:
        value: the PII value to log, such as an email address or username.

    Returns:
        str: ``'[REDACTED]'`` or the value as a string.
    """
    if getattr(settings, 'SQUELCH_PII_IN_LOGS', False):
        return REDACTED_FOR_LOG
    return str(value)


def get_username_or_pii_safe_user_id_for_log(user):
    """
    Return the identifier to use for ``user`` in a log message.

    Returns the user id when the ``SQUELCH_PII_IN_LOGS`` setting is enabled, and
    the username otherwise. The result is always a string, and an
    ``AnonymousUser`` is returned as ``'<AnonymousUser>'``.

    A numeric user id is not PII and can always be logged directly without this
    function. Use this function only where the username is preferred in logs for
    deployments that allow PII in logs.

    Arguments:
        user (User): the user to identify in the log message.

    Returns:
        str: the user id or the username.
    """
    if getattr(user, 'is_anonymous', False) is True:
        return ANONYMOUS_USER_FOR_LOG
    if getattr(settings, 'SQUELCH_PII_IN_LOGS', False):
        return str(user.id)
    return str(user.username)


def get_email_or_pii_safe_user_id_for_log(user):
    """
    Return the identifier to use for ``user`` in a log message.

    Returns the user id when the ``SQUELCH_PII_IN_LOGS`` setting is enabled, and
    the email otherwise. The result is always a string, and an
    ``AnonymousUser`` is returned as ``'<AnonymousUser>'``.

    A numeric user id is not PII and can always be logged directly without this
    function. Use this function only where the email is preferred in logs for
    deployments that allow PII in logs.

    Arguments:
        user (User): the user to identify in the log message.

    Returns:
        str: the user id or the email.
    """
    if getattr(user, 'is_anonymous', False) is True:
        return ANONYMOUS_USER_FOR_LOG
    if getattr(settings, 'SQUELCH_PII_IN_LOGS', False):
        return str(user.id)
    return str(user.email)


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
