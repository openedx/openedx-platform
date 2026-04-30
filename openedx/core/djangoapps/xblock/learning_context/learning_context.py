"""
A "Learning Context" is a course, a library, a program, or some other collection
of content where learning happens.
"""
from opaque_keys.edx.keys import UsageKeyV2

from openedx.core.types import User as UserType


class LearningContext:
    """
    Abstract base class for learning context implementations.

    A "Learning Context" is a course, a library, a program,
    or some other collection of content where learning happens.

    Additional learning contexts can be implemented as plugins, by subclassing
    this class and registering in the 'openedx.learning_context' entry point.
    """

    def __init__(self, **kwargs):
        """
        Initialize this learning context.

        Subclasses should pass **kwargs to this constructor to allow for future
        parameters without changing the API.
        """

    def can_edit_block(self, user: UserType, usage_key: UsageKeyV2) -> bool:  # pylint: disable=unused-argument
        """
        Assuming a block with the specified ID (usage_key) exists, does the
        specified user have permission to edit it (make changes to the
        fields / authored data store)?

        user: a Django User object (may be an AnonymousUser)

        usage_key: the UsageKeyV2 subclass used for this learning context

        Must return a boolean.
        """
        return False

    def can_view_block_for_editing(self, user: UserType, usage_key: UsageKeyV2) -> bool:
        """
        Assuming a block with the specified ID (usage_key) exists, does the
        specified user have permission to view its fields and OLX details (but
        not necessarily to make changes to it)?
        """
        return self.can_edit_block(user, usage_key)

    def can_view_block(self, user: UserType, usage_key: UsageKeyV2) -> bool:  # pylint: disable=unused-argument
        """
        Assuming a block with the specified ID (usage_key) exists, does the
        specified user have permission to view it and interact with it (call
        handlers, save user state, etc.)? This is also sometimes called the
        "can_learn" permission.

        user: a Django User object (may be an AnonymousUser)

        usage_key: the UsageKeyV2 subclass used for this learning context

        Must return a boolean.
        """
        return False

    def definition_for_usage(self, usage_key, **kwargs):
        """
        Given a usage key in this context, return the key indicating the actual XBlock definition.

        Retuns None if the usage key doesn't exist in this context.
        """
        raise NotImplementedError
