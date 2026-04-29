"""Exceptions raised by the credit API. """


from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import APIException

# TODO: Cleanup this mess! ECOM-2908


class CreditApiBadRequest(Exception):
    """
    Could not complete a request to the credit API because
    there was a problem with the request (as opposed to an internal error).
    """
    pass  # pylint: disable=unnecessary-pass


class InvalidCreditRequirements(CreditApiBadRequest):
    """
    The requirement dictionary provided has invalid format.
    """
    pass  # pylint: disable=unnecessary-pass


class InvalidCreditCourse(CreditApiBadRequest):
    """
    The course is not configured for credit.
    """
    pass  # pylint: disable=unnecessary-pass


class UserIsNotEligible(CreditApiBadRequest):
    """
    The user has not satisfied eligibility requirements for credit.
    """
    pass  # pylint: disable=unnecessary-pass


class CreditProviderNotConfigured(CreditApiBadRequest):
    """
    The requested credit provider is not configured correctly for the course.
    """
    pass  # pylint: disable=unnecessary-pass


class RequestAlreadyCompleted(CreditApiBadRequest):
    """
    The user has already submitted a request and received a response from the credit provider.
    """
    pass  # pylint: disable=unnecessary-pass


class CreditRequestNotFound(CreditApiBadRequest):
    """
    The request does not exist.
    """
    pass  # pylint: disable=unnecessary-pass


class InvalidCreditStatus(CreditApiBadRequest):
    """
    The status is not either "approved" or "rejected".
    """
    pass  # pylint: disable=unnecessary-pass


class InvalidCreditRequest(APIException):
    """ API request is invalid. """
    status_code = status.HTTP_400_BAD_REQUEST


class UserNotEligibleException(InvalidCreditRequest):
    """ User not eligible for credit for a given course. """

    def __init__(self, course_key, username):
        detail = _('[{username}] is not eligible for credit for [{course_key}].').format(username=username,
                                                                                         course_key=course_key)
        super().__init__(detail)


class InvalidCourseKey(InvalidCreditRequest):
    """ Course key is invalid. """

    def __init__(self, course_key):
        detail = _('[{course_key}] is not a valid course key.').format(course_key=course_key)
        super().__init__(detail)
