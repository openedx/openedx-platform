"""
Django models related to course groups functionality.
"""


import json
import logging

from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from opaque_keys.edx.django.models import CourseKeyField
from openedx_events.learning.data import (  # pylint: disable=wrong-import-order
    CohortData,
    CourseData,
    UserData,
    UserPersonalData,
)
from openedx_events.learning.signals import (
    COHORT_MEMBERSHIP_CHANGED,  # pylint: disable=wrong-import-order
)
from openedx_filters.learning.filters import CohortAssignmentRequested, CohortChangeRequested

from openedx.core.djangolib.model_mixins import DeletableByUserValue

log = logging.getLogger(__name__)


class CohortMembershipException(Exception):
    pass


class CohortChangeNotAllowed(CohortMembershipException):
    pass


class CohortAssignmentNotAllowed(CohortMembershipException):
    pass


class CourseUserGroup(models.Model):
    """
    This model represents groups of users in a course.  Groups may have different types,
    which may be treated specially.  For example, a user can be in at most one cohort per
    course, and cohorts are used to split up the forums by group.

    .. no_pii:
    """
    class Meta:
        unique_together = (('name', 'course_id'), )

    name = models.CharField(max_length=255,  # noqa: DJ012
                            help_text=("What is the name of this group?  "
                                       "Must be unique within a course."))
    users = models.ManyToManyField(User, db_index=True, related_name='course_groups',
                                   help_text="Who is in this group?")

    # Note: groups associated with particular runs of a course.  E.g. Fall 2012 and Spring
    # 2013 versions of 6.00x will have separate groups.
    course_id = CourseKeyField(
        max_length=255,
        db_index=True,
        help_text="Which course is this group associated with?",
    )

    # For now, only have group type 'cohort', but adding a type field to support
    # things like 'question_discussion', 'friends', 'off-line-class', etc
    COHORT = 'cohort'  # If changing this string, update it in migration 0006.forwards() as well
    GROUP_TYPE_CHOICES = ((COHORT, 'Cohort'),)
    group_type = models.CharField(max_length=20, choices=GROUP_TYPE_CHOICES)

    @classmethod
    def create(cls, name, course_id, group_type=COHORT):
        """
        Create a new course user group.

        Args:
            name: Name of group
            course_id: course id
            group_type: group type
        """
        return cls.objects.get_or_create(
            course_id=course_id,
            group_type=group_type,
            name=name
        )

    def __str__(self):  # noqa: DJ012
        return self.name


class CohortMembership(models.Model):  # noqa: DJ008
    """
    Used internally to enforce our particular definition of uniqueness.

    .. no_pii:
    """
    course_user_group = models.ForeignKey(CourseUserGroup, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    course_id = CourseKeyField(max_length=255)

    class Meta:
        unique_together = (('user', 'course_id'), )

    def clean_fields(self, *args, **kwargs):  # pylint: disable=signature-differs
        if self.course_id is None:
            self.course_id = self.course_user_group.course_id
        super().clean_fields(*args, **kwargs)

    def clean(self):
        if self.course_user_group.group_type != CourseUserGroup.COHORT:
            raise ValidationError("CohortMembership cannot be used with CourseGroup types other than COHORT")
        if self.course_user_group.course_id != self.course_id:
            raise ValidationError("Non-matching course_ids provided")

    @classmethod
    def assign(cls, cohort, user):
        """
        Assign user to cohort, switching them to this cohort if they had previously been assigned to another
        cohort
        Returns CohortMembership, previous_cohort (if any)
        """
        try:
            # .. filter_implemented_name: CohortAssignmentRequested
            # .. filter_type: org.openedx.learning.cohort.assignment.requested.v1
            user, cohort = CohortAssignmentRequested.run_filter(user=user, target_cohort=cohort)
        except CohortAssignmentRequested.PreventCohortAssignment as exc:
            raise CohortAssignmentNotAllowed(str(exc)) from exc

        with transaction.atomic():
            membership, created = cls.objects.select_for_update().get_or_create(
                user__id=user.id,
                course_id=cohort.course_id,
                defaults={
                    'course_user_group': cohort,
                    'user': user
                })

            if created:
                membership.course_user_group.users.add(user)
                previous_cohort = None
            elif membership.course_user_group == cohort:
                raise ValueError("User {user_name} already present in cohort {cohort_name}".format(  # noqa: UP032
                    user_name=user.username,
                    cohort_name=cohort.name))
            else:
                previous_cohort = membership.course_user_group

                try:
                    # .. filter_implemented_name: CohortChangeRequested
                    # .. filter_type: org.openedx.learning.cohort.change.requested.v1
                    membership, cohort = CohortChangeRequested.run_filter(
                        current_membership=membership, target_cohort=cohort,
                    )
                except CohortChangeRequested.PreventCohortChange as exc:
                    raise CohortChangeNotAllowed(str(exc)) from exc

                previous_cohort.users.remove(user)

                membership.course_user_group = cohort
                membership.course_user_group.users.add(user)
                membership.save()
        return membership, previous_cohort

    def save(self, *args, **kwargs):  # noqa: DJ012
        self.full_clean(validate_unique=False)

        # .. event_implemented_name: COHORT_MEMBERSHIP_CHANGED
        # .. event_type: org.openedx.learning.cohort_membership.changed.v1
        COHORT_MEMBERSHIP_CHANGED.send_event(
            cohort=CohortData(
                user=UserData(
                    pii=UserPersonalData(
                        username=self.user.username,
                        email=self.user.email,
                        name=self.user.profile.name,
                    ),
                    id=self.user.id,
                    is_active=self.user.is_active,
                ),
                course=CourseData(
                    course_key=self.course_id,
                ),
                name=self.course_user_group.name,
            )
        )

        log.info("Saving CohortMembership for user '%s' in '%s'", self.user.id, self.course_id)
        return super().save(*args, **kwargs)


# Needs to exist outside class definition in order to use 'sender=CohortMembership'
@receiver(pre_delete, sender=CohortMembership)
def remove_user_from_cohort(sender, instance, **kwargs):  # pylint: disable=unused-argument
    """
    Ensures that when a CohortMemebrship is deleted, the underlying CourseUserGroup
    has its users list updated to reflect the change as well.
    """
    instance.course_user_group.users.remove(instance.user)
    instance.course_user_group.save()


class CourseUserGroupPartitionGroup(models.Model):  # noqa: DJ008
    """
    Create User Partition Info.

    .. no_pii:
    """
    course_user_group = models.OneToOneField(CourseUserGroup, on_delete=models.CASCADE)
    partition_id = models.IntegerField(
        help_text="contains the id of a cohorted partition in this course"
    )
    group_id = models.IntegerField(
        help_text="contains the id of a specific group within the cohorted partition"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class CourseCohortsSettings(models.Model):  # noqa: DJ008
    """
    This model represents cohort settings for courses.
    The only non-deprecated fields are `is_cohorted` and `course_id`.

    .. no_pii:
    """
    is_cohorted = models.BooleanField(default=False)

    course_id = CourseKeyField(
        unique=True,
        max_length=255,
        db_index=True,
        help_text="Which course are these settings associated with?",
    )

    _cohorted_discussions = models.TextField(db_column='cohorted_discussions', null=True, blank=True)  # JSON list  # noqa: DJ001  # pylint: disable=line-too-long

    # Note that although a default value is specified here for always_cohort_inline_discussions (False),
    # in reality the default value at the time that cohorting is enabled for a course comes from
    # course_block.always_cohort_inline_discussions (via `migrate_cohort_settings`).
    # DEPRECATED-- DO NOT USE: Instead use `CourseDiscussionSettings.always_divide_inline_discussions`
    # via `CourseDiscussionSettings.get` or `CourseDiscussionSettings.update`.
    always_cohort_inline_discussions = models.BooleanField(default=False)

    @property
    def cohorted_discussions(self):
        """
        DEPRECATED-- DO NOT USE. Instead use `CourseDiscussionSettings.divided_discussions`
        via `CourseDiscussionSettings.get`.
        """
        return json.loads(self._cohorted_discussions)

    @cohorted_discussions.setter
    def cohorted_discussions(self, value):
        """
        DEPRECATED-- DO NOT USE. Instead use `CourseDiscussionSettings.update`
        """
        self._cohorted_discussions = json.dumps(value)


class CourseCohort(models.Model):  # noqa: DJ008
    """
    This model represents cohort related info.

    .. no_pii:
    """
    course_user_group = models.OneToOneField(CourseUserGroup, unique=True, related_name='cohort',
                                             on_delete=models.CASCADE)

    RANDOM = 'random'
    MANUAL = 'manual'
    ASSIGNMENT_TYPE_CHOICES = ((RANDOM, 'Random'), (MANUAL, 'Manual'),)
    assignment_type = models.CharField(max_length=20, choices=ASSIGNMENT_TYPE_CHOICES, default=MANUAL)

    @classmethod
    def create(cls, cohort_name=None, course_id=None, course_user_group=None, assignment_type=MANUAL):
        """
        Create a complete(CourseUserGroup + CourseCohort) object.

        Args:
            cohort_name: Name of the cohort to be created
            course_id: Course Id
            course_user_group: CourseUserGroup
            assignment_type: 'random' or 'manual'
        """
        if course_user_group is None:
            course_user_group, __ = CourseUserGroup.create(cohort_name, course_id)

        course_cohort, __ = cls.objects.get_or_create(
            course_user_group=course_user_group,
            defaults={'assignment_type': assignment_type}
        )

        return course_cohort


class UnregisteredLearnerCohortAssignments(DeletableByUserValue, models.Model):  # noqa: DJ008
    """
    Tracks the assignment of an unregistered learner to a course's cohort.

    .. pii: The email field stores PII.
    .. pii_types: email_address
    .. pii_retirement: local_api
    """
    # pylint: disable=model-missing-unicode
    class Meta:
        unique_together = (('course_id', 'email'), )

    course_user_group = models.ForeignKey(CourseUserGroup, on_delete=models.CASCADE)  # noqa: DJ012
    email = models.CharField(blank=True, max_length=255, db_index=True)
    course_id = CourseKeyField(max_length=255)
