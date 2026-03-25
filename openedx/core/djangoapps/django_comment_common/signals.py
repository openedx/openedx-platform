# pylint: disable=invalid-name
"""Signals related to the comments service."""


from django.dispatch import Signal  # noqa: I001

# Same providing_args=['user', 'post'] for all following signals.
thread_created = Signal()
thread_edited = Signal()
thread_voted = Signal()
thread_deleted = Signal()
thread_followed = Signal()
thread_unfollowed = Signal()
thread_flagged = Signal()
comment_created = Signal()
comment_edited = Signal()
comment_voted = Signal()
comment_deleted = Signal()
comment_endorsed = Signal()
comment_flagged = Signal()
