"""
Agreements models
"""

from django.contrib.auth import get_user_model
from django.db import models
from model_utils.models import TimeStampedModel
from opaque_keys.edx.django.models import CourseKeyField

User = get_user_model()


class IntegritySignature(TimeStampedModel):
    """
    This model represents an integrity signature for a user + course combination.

    .. no_pii:
    """
    user = models.ForeignKey(User, db_index=True, on_delete=models.CASCADE)
    course_key = CourseKeyField(max_length=255, db_index=True)

    class Meta:
        app_label = 'agreements'
        unique_together = ('user', 'course_key')


class LTIPIITool(TimeStampedModel):
    """
    This model stores the relationship between a course and the LTI tools in the course that share PII.

    .. no_pii:
    """
    course_key = CourseKeyField(max_length=255, unique=True, db_index=True)
    lti_tools = models.JSONField()
    lti_tools_hash = models.IntegerField()

    class Meta:
        app_label = 'agreements'


class LTIPIISignature(TimeStampedModel):
    """
    This model stores a user's acknowledgement to share PII via LTI tools in a particular course.

    .. no_pii:
    """
    user = models.ForeignKey(User, db_index=True, on_delete=models.CASCADE)
    course_key = CourseKeyField(max_length=255, db_index=True)
    lti_tools = models.JSONField()

    # lti_tools_hash represents the hash of the list of LTI tools receiving
    # PII acknowledged by the user. The hash is used to compare user
    # acknowledgments - which reduces response time and decreases any impact
    # on unit rendering time.
    lti_tools_hash = models.IntegerField()

    class Meta:
        app_label = 'agreements'


class ProctoringPIISignature(TimeStampedModel):
    """
    This model stores a user's acknowledgment to share PII via proctoring in a particular course.

    .. no_pii:
    """
    user = models.ForeignKey(User, db_index=True, on_delete=models.CASCADE)
    course_key = CourseKeyField(max_length=255, db_index=True)
    proctoring_provider = models.CharField(max_length=255)

    class Meta:
        app_label = 'agreements'


class UserAgreementRecord(models.Model):
    """
    This model stores the agreements a user has accepted or acknowledged.

    Each record here represents a user agreeing to the agreement type represented
    by `agreement_type` at a particular time.

    .. no_pii:
    """
    user = models.ForeignKey(User, db_index=True, on_delete=models.CASCADE)
    agreement_type = models.CharField(max_length=255)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'agreements'
