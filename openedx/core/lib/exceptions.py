"""
Common Purpose Errors
"""

from django.core.exceptions import ObjectDoesNotExist


class CourseNotFoundError(ObjectDoesNotExist):
    """
    Course was not found.
    """
    pass  # pylint: disable=unnecessary-pass


class PageNotFoundError(ObjectDoesNotExist):
    """
    Page was not found. Used for paginated endpoint.
    """
    pass  # pylint: disable=unnecessary-pass


class DiscussionNotFoundError(ObjectDoesNotExist):
    """
    Discussion Block was not found.
    """
    pass  # pylint: disable=unnecessary-pass
