# lint-amnesty, pylint: disable=django-not-configured
"""
Utility functions related to databases.
"""


import random

# TransactionManagementError used below actually *does* derive from the standard "Exception" class.
# lint-amnesty, pylint: disable=bad-option-value, nonstandard-exception
from contextlib import contextmanager

from django.db import DEFAULT_DB_ALIAS, transaction  # lint-amnesty, pylint: disable=unused-import

from openedx.core.lib.cache_utils import get_cache

OUTER_ATOMIC_CACHE_NAME = 'db.outer_atomic'

MYSQL_MAX_INT = (2 ** 31) - 1


@contextmanager
def enable_named_outer_atomic(*names):
    """
    Enable outer_atomics with names.

    Can be used either as a decorator or a context manager.
    See docstring of outer_atomic for details.

    Arguments:
        names (variable-lenght argument list): Names of outer_atomics.
    """
    if len(names) == 0:
        raise ValueError("At least one name must be specified.")

    cache = get_cache(OUTER_ATOMIC_CACHE_NAME)

    for name in names:
        cache[name] = True
    try:
        yield
    finally:
        for name in names:
            del cache[name]


class OuterAtomic(transaction.Atomic):
    """
    Atomic which cannot be nested in another atomic.

    This is useful if you want to ensure that a commit happens at
    the end of the wrapped block.
    """
    ALLOW_NESTED = False

    def __init__(self, using, savepoint, name=None, durable=False):
        self.name = name
        self.durable = durable
        super().__init__(using, savepoint, durable)   # pylint: disable=too-many-function-args

    def __enter__(self):

        connection = transaction.get_connection(self.using)

        cache = get_cache(OUTER_ATOMIC_CACHE_NAME)

        # By default it is enabled.
        enable = True
        # If name is set it is only enabled if requested by calling enable_named_outer_atomic().
        if self.name:
            enable = cache.get(self.name, False)

        if enable:
            # TestCase setup nests tests in two atomics - one for the test class and one for the individual test.
            # The outermost atomic starts a transaction - so does not have a savepoint.
            # The inner atomic starts a savepoint around the test.
            # So, for tests only, there should be exactly one savepoint_id and two atomic_for_testcase_calls.
            # atomic_for_testcase_calls below is added in a monkey-patch for tests only.
            if self.ALLOW_NESTED and (self.atomic_for_testcase_calls - len(connection.savepoint_ids)) < 1:  # lint-amnesty, pylint: disable=no-member
                raise transaction.TransactionManagementError('Cannot be inside an atomic block.')

            # Otherwise, this shouldn't be nested in any atomic block.
            if not self.ALLOW_NESTED and connection.in_atomic_block:
                raise transaction.TransactionManagementError('Cannot be inside an atomic block.')

        super().__enter__()


def outer_atomic(using=None, savepoint=True, name=None, durable=False):
    """
    A variant of Django's atomic() which cannot be nested inside another atomic.

    With the upgrade to Django 1.8, all views by default are wrapped
    in an atomic block. Because of this, a commit to the database can
    only happen once the view returns. This is because nested atomic
    blocks use savepoints and the transaction only gets committed when
    the outermost atomic block returns. See the official Django docs
    for details: https://docs.djangoproject.com/en/1.8/topics/db/transactions/

    However, in some cases, we need to be able to commit to the
    database in the middle of a view. The only way to do this
    is to disable automatic transaction management for the view by
    adding @transaction.non_atomic_requests to it and then using
    atomic() inside it in relevant places. To help ensure that queries
    inside a piece of code are committed, you can wrap it in
    outer_atomic(). outer_atomic() will ensure that it is not nested
    inside another atomic block.

    If we need to do this to prevent IntegrityErrors, a named outer_atomic
    should be used. You can create a named outer_atomic by passing a name.
    A named outer_atomic only checks that it is not nested under an atomic
    only if it is nested under enable_named_outer_atomic(name=<name>). This way
    only the view which is causing IntegrityErrors needs to have its
    automatic transaction management disabled and other callers are not
    affected.

    Arguments:
        using (str): the name of the database.
        name (str): the name to give to this outer_atomic instance.

    Raises:
        TransactionManagementError: if already inside an atomic block.
    """
    if callable(using):
        return OuterAtomic(DEFAULT_DB_ALIAS, savepoint, durable)(using)
    # Decorator: @outer_atomic(...) or context manager: with outer_atomic(...): ...
    else:
        return OuterAtomic(using, savepoint, name, durable)


def generate_int_id(minimum=0, maximum=MYSQL_MAX_INT, used_ids=None):
    """
    Return a unique integer in the range [minimum, maximum], inclusive.
    """
    if used_ids is None:
        used_ids = []

    cid = random.randint(minimum, maximum)

    while cid in used_ids:
        cid = random.randint(minimum, maximum)

    return cid
