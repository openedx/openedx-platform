"""
Django model specifications for the Program Enrollments API
"""

from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from model_utils.models import TimeStampedModel
from opaque_keys.edx.django.models import CourseKeyField
from simple_history.models import HistoricalRecords
from openedx.core.lib import user_util

from common.djangoapps.student.models import CourseEnrollment

from .constants import ProgramCourseEnrollmentRoles, ProgramCourseEnrollmentStatuses, ProgramEnrollmentStatuses


class ProgramEnrollment(TimeStampedModel):
    """
    This is a model for Program Enrollments from the registrar service

    .. pii: PII is found in the external key for a program enrollment
    .. pii_types: other
    .. pii_retirement: local_api
    """
    STATUS_CHOICES = ProgramEnrollmentStatuses.__MODEL_CHOICES__

    class Meta:
        app_label = "program_enrollments"

        # A student enrolled in a given (program, curriculum) should always
        # have a non-null ``user`` or ``external_user_key`` field (or both).
        unique_together = (
            ('user', 'program_uuid', 'curriculum_uuid'),
            ('external_user_key', 'program_uuid', 'curriculum_uuid'),
        )

    user = models.ForeignKey(
        User,
        null=True,
        blank=True, on_delete=models.CASCADE
    )
    external_user_key = models.CharField(
        db_index=True,
        max_length=255,
        null=True
    )
    program_uuid = models.UUIDField(db_index=True, null=False)
    curriculum_uuid = models.UUIDField(db_index=True, null=False)
    status = models.CharField(max_length=9, choices=STATUS_CHOICES)
    historical_records = HistoricalRecords()

    def clean(self):
        if not (self.user or self.external_user_key):
            raise ValidationError(_('One of user or external_user_key must not be null.'))

    @classmethod
    def retire_user(cls, user_id):
        """
        With the parameter user_id, retire the external_user_key field

        Return True if there is data that was retired
        Return False if there is no matching data
        """

        enrollments = cls.objects.filter(user=user_id)
        if not enrollments:
            return False

        for enrollment in enrollments:
            retired_external_key = user_util.get_retired_external_key(
                enrollment.external_user_key,
                settings.RETIRED_USER_SALTS,
            )
            enrollment.historical_records.update(external_user_key=retired_external_key)
            enrollment.external_user_key = retired_external_key
            enrollment.save()

        return True

    def __str__(self):
        return f'[ProgramEnrollment id={self.id}]'

    def __repr__(self):
        return (  # lint-amnesty, pylint: disable=missing-format-attribute
            "<ProgramEnrollment"    # pylint: disable=missing-format-attribute
            " id={self.id}"
            " user={self.user!r}"
            " external_user_key={self.external_user_key!r}"
            " program_uuid={self.program_uuid!r}"
            " curriculum_uuid={self.curriculum_uuid!r}"
            " status={self.status!r}"
            ">"
        ).format(self=self)


class ProgramCourseEnrollment(TimeStampedModel):
    """
    This is a model to represent a learner's enrollment in a course
    in the context of a program from the registrar service

    .. no_pii:
    """
    STATUS_CHOICES = ProgramCourseEnrollmentStatuses.__MODEL_CHOICES__

    class Meta:
        app_label = "program_enrollments"

        # For each program enrollment, there may be only one
        # waiting program-course enrollment per course key.
        unique_together = (
            ('program_enrollment', 'course_key'),
        )

    program_enrollment = models.ForeignKey(
        ProgramEnrollment,
        on_delete=models.CASCADE,
        related_name="program_course_enrollments"
    )
    # In Django 2.x, we should add a conditional unique constraint to this field so
    # no duplicated tuple of (course_enrollment_id, status=active) exists
    # MST-168 is the Jira ticket to accomplish this once Django is upgraded
    course_enrollment = models.ForeignKey(
        CourseEnrollment,
        null=True,
        blank=True,
        on_delete=models.CASCADE
    )
    course_key = CourseKeyField()
    status = models.CharField(max_length=9, choices=STATUS_CHOICES)
    historical_records = HistoricalRecords()

    @property
    def is_active(self):
        return self.status == ProgramCourseEnrollmentStatuses.ACTIVE

    def __str__(self):
        return f'[ProgramCourseEnrollment id={self.id}]'

    def __repr__(self):
        return (  # lint-amnesty, pylint: disable=missing-format-attribute
            "<ProgramCourseEnrollment"  # pylint: disable=missing-format-attribute
            " id={self.id}"
            " program_enrollment={self.program_enrollment!r}"
            " course_enrollment=<{self.course_enrollment}>"
            " course_key={self.course_key}"
            " status={self.status!r}"
            ">"
        ).format(self=self)


class CourseAccessRoleAssignment(TimeStampedModel):
    """
    This model represents a role that should be assigned to the eventual user of a pending enrollment.

    .. no_pii:
    """
    class Meta:
        unique_together = ('role', 'enrollment')

    role = models.CharField(max_length=64, choices=ProgramCourseEnrollmentRoles.__MODEL_CHOICES__)
    enrollment = models.ForeignKey(ProgramCourseEnrollment, on_delete=models.CASCADE)

    def __str__(self):
        return f'[CourseAccessRoleAssignment id={self.id}]'

    def __repr__(self):
        return (  # lint-amnesty, pylint: disable=missing-format-attribute
            "<CourseAccessRoleAssignment"  # pylint: disable=missing-format-attribute
            " id={self.id}"
            " role={self.role!r}"
            " enrollment={self.enrollment!r}"
            ">"
        ).format(self=self)
