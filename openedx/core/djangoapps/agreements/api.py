"""
Agreements API
"""

import logging
from datetime import datetime
from typing import Iterable, Optional

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from opaque_keys.edx.keys import CourseKey

from .data import LTIPIISignatureData, LTIToolsReceivingPIIData, UserAgreementRecordData
from .models import IntegritySignature, LTIPIISignature, LTIPIITool, UserAgreementRecord

log = logging.getLogger(__name__)
User = get_user_model()


def create_integrity_signature(username, course_id):
    """
    Create an integrity signature. If a signature already exists, do not create a new one.

    Arguments:
        * username (str)
        * course_id (str)

    Returns:
        * IntegritySignature object
    """
    user = User.objects.get(username=username)
    course_key = CourseKey.from_string(course_id)
    signature, created = IntegritySignature.objects.get_or_create(user=user, course_key=course_key)
    if not created:
        log.warning(
            'Integrity signature already exists for user_id={user_id} and '
            'course_id={course_id}'.format(user_id=user.id, course_id=course_id)
        )
    return signature


def get_integrity_signature(username, course_id):
    """
    Get an integrity signature.

    Arguments:
        * username (str)
        * course_id (str)

    Returns:
        * An IntegritySignature object, or None if one does not exist for the
          user + course combination.
    """
    user = User.objects.get(username=username)
    course_key = CourseKey.from_string(course_id)
    try:
        return IntegritySignature.objects.get(user=user, course_key=course_key)
    except ObjectDoesNotExist:
        return None


def get_integrity_signatures_for_course(course_id):
    """
    Get all integrity signatures for a given course.

    Arguments:
        * course_id (str)

    Returns:
        * QuerySet of IntegritySignature objects (can be empty).
    """
    course_key = CourseKey.from_string(course_id)
    return IntegritySignature.objects.filter(course_key=course_key)


def create_lti_pii_signature(username, course_id, lti_tools):
    """
    Creates an lti pii tool signature. If the signature already exist, do not create a new one.

    Arguments:
        * course_key (str)
        * lti_tools (dict)
        * lti_tools_hash (int)
    Returns:
        * An LTIPIISignature, or None if a signature already exists.
    """
    course_key = CourseKey.from_string(course_id)
    lti_tools_hash = hash(str(lti_tools))

    # if user and course exists, update, otherwise create a new signature
    try:
        user = User.objects.get(username=username)
        LTIPIISignature.objects.get(user=user, course_key=course_key)
    except User.DoesNotExist:
        return None
    except LTIPIISignature.DoesNotExist:
        signature = LTIPIISignature.objects.create(
            user=user,
            course_key=course_key,
            lti_tools=lti_tools,
            lti_tools_hash=lti_tools_hash)
    else:
        signature = LTIPIISignature.objects.update(
            user=user,
            course_key=course_key,
            lti_tools=lti_tools,
            lti_tools_hash=lti_tools_hash)

    return signature


def get_lti_pii_signature(username, course_id):
    """
    Get the lti pii signature of a user in a course.

    Arguments:
        * username (str)
        * course_id (str)

    Returns:
        * An LTIPIISignature object, or None if one does not exist for the
          user + course combination.
    """
    course_key = CourseKey.from_string(course_id)
    try:
        user = User.objects.get(username=username)
        signature = LTIPIISignature.objects.get(user=user, course_key=course_key)
    except (User.DoesNotExist, LTIPIISignature.DoesNotExist):
        return None
    else:
        return LTIPIISignatureData(user=signature.user, course_id=str(signature.course_key),
                                   lti_tools=signature.lti_tools, lti_tools_hash=signature.lti_tools_hash)


def get_pii_receiving_lti_tools(course_id):
    """
    Get a course's LTI tools that share PII.

    Arguments:
        * course_id (str)

    Returns:
        * A List of LTI tools sharing PII.
    """

    course_key = CourseKey.from_string(course_id)
    try:
        course_ltipiitools = LTIPIITool.objects.get(course_key=course_key).lti_tools
    except LTIPIITool.DoesNotExist:
        return None

    return LTIToolsReceivingPIIData(lii_tools_receiving_pii=course_ltipiitools)


def user_lti_pii_signature_needed(username, course_id):
    """
    Determines if a user needs to acknowledge the LTI PII Agreement.

    Arguments:
        * username (str)

    Returns:
        * True if the user needs to sign a new acknowledgement.
        * False if the acknowledgements are up to date.
    """
    course_has_lti_pii_tools = _course_has_lti_pii_tools(course_id)
    signature_exists = _user_lti_pii_signature_exists(username, course_id)
    signature_out_of_date = _user_signature_out_of_date(username, course_id)

    return ((course_has_lti_pii_tools and (not signature_exists)) or
            (course_has_lti_pii_tools and signature_exists and signature_out_of_date))


def _course_has_lti_pii_tools(course_id):
    """
    Determines if a specifc course has lti tools sharing pii.

    Arguments:
        * course_id (str)

    Returns:
        * True if the course does have a list.
        * False if the course does not.
    """
    course_key = CourseKey.from_string(course_id)
    try:
        course_lti_pii_tools = LTIPIITool.objects.get(course_key=course_key)
    except LTIPIITool.DoesNotExist:
        # no entry in the database
        return False
    else:
        # returns True if there are entries, and False if the list is empty
        return bool(course_lti_pii_tools.lti_tools)


def _user_lti_pii_signature_exists(username, course_id):
    """
    Determines if a user's lti pii signature exists for a specfic course

    Arguments:
        * username (str)
        * course_id (str)

    Returns:
        * True if user has a signature for the given course.
        * False if the user does not have a signature for the given course.
    """
    course_key = CourseKey.from_string(course_id)

    try:
        user = User.objects.get(username=username)
        LTIPIISignature.objects.get(user=user, course_key=course_key)
    except (User.DoesNotExist, LTIPIISignature.DoesNotExist):
        return False
    else:
        return True  # signature exist


def _user_signature_out_of_date(username, course_id):
    """
        Determines if a user's existing lti pii signature is out-of-date for a given course.

        Arguments:
            * username (str)
            * course_id (str)

        Returns:
            * True if signature is out-of-date and needs a new signature.
            * False if the user has an up-to-date signature.
        """
    course_key = CourseKey.from_string(course_id)

    try:
        user = User.objects.get(username=username)
        user_lti_pii_signature_hash = LTIPIISignature.objects.get(course_key=course_key, user=user).lti_tools_hash
        course_lti_pii_tools_hash = LTIPIITool.objects.get(course_key=course_key).lti_tools_hash
    except (User.DoesNotExist, LTIPIISignature.DoesNotExist, LTIPIITool.DoesNotExist):
        return False
    else:
        return user_lti_pii_signature_hash != course_lti_pii_tools_hash


def get_user_agreement_records(user: User) ->Iterator[UserAgreementRecordData]:
    """
    Retrieves all the agreements that the specified user has acknowledged.
    """
    for agreement_record in UserAgreementRecord.objects.filter(user=user).select_related("agreement", "user"):
        yield UserAgreementRecordData.from_model(agreement_record)


def get_latest_user_agreement_record(
    user: User,
    agreement_type: str,
) -> Optional[UserAgreementRecordData]:
    """
    Retrieve the user agreement record for the specified user and agreement type.

    An agreement update timestamp can be provided to return a record only if it
    was signed after that timestamp.
    """
    try:
        record_query = UserAgreementRecord.objects.filter(
            user=user,
            agreement__type=agreement_type,
        )
        record = record_query.latest("timestamp")
        return UserAgreementRecordData.from_model(record)
    except UserAgreementRecord.DoesNotExist:
        return None


def create_user_agreement_record(user: User, agreement_type: str) -> UserAgreementRecordData:
    """
    Creates a user agreement record if one doesn't already exist, or updates existing
    record to current timestamp.
    """
    record = UserAgreementRecord.objects.create(
        user=user,
        agreement__type=agreement_type,
        timestamp=datetime.now(),
    )
    return UserAgreementRecordData.from_model(record)
