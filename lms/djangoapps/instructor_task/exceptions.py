"""
Exception classes used by Instructor tasks.
"""


class UpdateProblemModuleStateError(Exception):
    """
    Error signaling a fatal condition while updating problem modules.

    Used when the current module cannot be processed and no more
    modules should be attempted.
    """
    pass  # pylint: disable=unnecessary-pass


class DuplicateTaskException(Exception):
    """Exception indicating that a task already exists or has already completed."""
    pass  # pylint: disable=unnecessary-pass
