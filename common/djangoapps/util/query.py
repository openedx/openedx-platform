""" Utility functions related to database queries """


from django.conf import settings

_READ_REPLICA_DB_ALIAS = "read_replica"


def use_read_replica_if_available(queryset):
    """
    If there is a database called 'read_replica',
    use that database for the queryset / manager.

    Example usage:
        queryset = use_read_replica_if_available(SomeModel.objects.filter(...))

    Arguments:
        queryset (QuerySet)

    Returns: QuerySet
    """
    return (
        queryset.using(_READ_REPLICA_DB_ALIAS)
        if _READ_REPLICA_DB_ALIAS in settings.DATABASES
        else queryset
    )


def read_replica_or_default():
    """
    If there is a database called "read_replica",
    return "read_replica", otherwise return "default".

    This function is similiar to `use_read_replica_if_available`,
    but is be more syntactically convenient for method call chaining.
    Also, it always falls back to "default",
    no matter what the queryset was using before.

    Example usage:
        queryset = SomeModel.objects.filter(...).using(read_replica_or_default())

    Returns: str
    """
    return (
        _READ_REPLICA_DB_ALIAS
        if _READ_REPLICA_DB_ALIAS in settings.DATABASES
        else "default"
    )
