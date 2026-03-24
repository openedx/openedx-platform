"""
Models used to implement support related models in such as SSO History model
"""
from django.contrib.auth import get_user_model
from django.db.models import ForeignKey, DO_NOTHING, CASCADE, TextChoices
from django.db.models.fields import BooleanField, CharField, DateTimeField

from model_utils.models import TimeStampedModel
from opaque_keys.edx.django.models import CourseKeyField
from simple_history import register
from social_django.models import UserSocialAuth

from common.djangoapps.student.models import CourseEnrollment

User = get_user_model()

# Registers UserSocialAuth with simple-django-history.
register(UserSocialAuth, app=__package__)


class CourseResetCourseOptIn(TimeStampedModel):
    """
    Model that represents a course which has opted in to the course reset feature.

    .. no_pii:
    """
    course_id = CourseKeyField(unique=True)
    active = BooleanField()

    def __str__(self):
        return f'{self.course_id} - {"ACTIVE" if self.active else "INACTIVE"}'

    @staticmethod
    def all_active():
        return CourseResetCourseOptIn.objects.filter(active=True)

    @staticmethod
    def all_active_course_ids():
        return [course.course_id for course in CourseResetCourseOptIn.all_active()]


class CourseResetAudit(TimeStampedModel):
    """
    Model which records the course reset action's status and metadata

    .. no_pii:
    """
    class CourseResetStatus(TextChoices):
        IN_PROGRESS = "in_progress"
        COMPLETE = "complete"
        ENQUEUED = "enqueued"
        FAILED = "failed"

    course = ForeignKey(
        CourseResetCourseOptIn,
        on_delete=CASCADE
    )
    course_enrollment = ForeignKey(
        CourseEnrollment,
        on_delete=DO_NOTHING
    )
    reset_by = ForeignKey(
        User,
        on_delete=DO_NOTHING
    )
    status = CharField(
        max_length=12,
        choices=CourseResetStatus.choices,
        default=CourseResetStatus.ENQUEUED,
    )
    completed_at = DateTimeField(default=None, null=True, blank=True)
    comment = CharField(max_length=255, default="", blank=True)

    def status_message(self):
        """ Return a string message about the status of this audit """
        if self.status == self.CourseResetStatus.FAILED:
            return f"Failed on {self.modified}"
        if self.status == self.CourseResetStatus.ENQUEUED:
            return f"Enqueued - Created {self.created} by {self.reset_by.username}"
        if self.status == self.CourseResetStatus.COMPLETE:
            return f"Completed on {self.completed_at} by {self.reset_by.username}"
        if self.status == self.CourseResetStatus.IN_PROGRESS:
            return f"In progress - Started on {self.modified} by {self.reset_by.username}"
        return self.status
