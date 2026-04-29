""" Errors used by the Discussion API. """


from django.core.exceptions import ObjectDoesNotExist
from rest_framework.exceptions import APIException


class DiscussionDisabledError(ObjectDoesNotExist):
    """ Discussion is disabled. """
    pass  # pylint: disable=unnecessary-pass


class ThreadNotFoundError(ObjectDoesNotExist):
    """ Thread was not found. """
    pass  # pylint: disable=unnecessary-pass


class CommentNotFoundError(ObjectDoesNotExist):
    """ Comment was not found. """
    pass  # pylint: disable=unnecessary-pass


class DiscussionBlackOutException(APIException):
    """ Discussions are in blackout period. """
    status_code = 403
    default_detail = 'Discussions are in blackout period.'
