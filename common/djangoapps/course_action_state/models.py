"""
Models for course action state

If you make changes to this model, be sure to create an appropriate migration
file and check it in at the same time as your model changes. To do that,

1. Go to the edx-platform dir
2. ./manage.py cms schemamigration course_action_state --auto description_of_your_change
3. It adds the migration file to edx-platform/common/djangoapps/course_action_state/migrations/

"""

from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.db import models
from opaque_keys.edx.django.models import CourseKeyField

from common.djangoapps.course_action_state.managers import CourseActionStateManager, CourseRerunUIStateManager


class CourseActionState(models.Model):
    """
    A django model for maintaining state data for course actions that take a long time.
    For example: course copying (reruns), import, export, and validation.
    """

    class Meta:
        """
        For performance reasons, we disable "concrete inheritance", by making the Model base class abstract.
        With the "abstract base class" inheritance model, tables are only created for derived models, not for
        the parent classes.  This way, we don't have extra overhead of extra tables and joins that would
        otherwise happen with the multi-table inheritance model.
        """
        abstract = True

    # FIELDS

    # Created is the time this action was initiated
    created_time = models.DateTimeField(auto_now_add=True)  # noqa: DJ012

    # Updated is the last time this entry was modified
    updated_time = models.DateTimeField(auto_now=True)

    # User who initiated the course action
    created_user = models.ForeignKey(
        User,
        # allow NULL values in case the action is not initiated by a user (e.g., a background thread)
        null=True,
        # set on_delete to SET_NULL to prevent this model from being deleted in the event the user is deleted
        on_delete=models.SET_NULL,
        # add a '+' at the end to prevent a backward relation from the User model
        related_name='created_by_user+'
    )

    # User who last updated the course action
    updated_user = models.ForeignKey(
        User,
        # allow NULL values in case the action is not updated by a user (e.g., a background thread)
        null=True,
        # set on_delete to SET_NULL to prevent this model from being deleted in the event the user is deleted
        on_delete=models.SET_NULL,
        # add a '+' at the end to prevent a backward relation from the User model
        related_name='updated_by_user+'
    )

    # Course that is being acted upon
    course_key = CourseKeyField(max_length=255, db_index=True)

    # Action that is being taken on the course
    action = models.CharField(max_length=100, db_index=True)

    # Current state of the action.
    state = models.CharField(max_length=50)

    # MANAGERS
    objects = CourseActionStateManager()  # noqa: DJ012


class CourseActionUIState(CourseActionState):
    """
    An abstract django model that is a sub-class of CourseActionState with additional fields related to UI.
    """
    class Meta:
        """
        See comment in CourseActionState on disabling "concrete inheritance".
        """
        abstract = True

    # WARNING - when you edit this value, you're also modifying the max_length
    # of the `message` column (see below)
    MAX_MESSAGE_LENGTH = 1000

    # FIELDS

    # Whether or not the status should be displayed to users
    should_display = models.BooleanField(default=False)  # noqa: DJ012

    # Message related to the status
    message = models.CharField(max_length=MAX_MESSAGE_LENGTH)


# Rerun courses also need these fields. All rerun course actions will have a row here as well.
class CourseRerunState(CourseActionUIState):  # noqa: DJ008
    """
    A concrete django model for maintaining state specifically for the Action Course Reruns.

    .. no_pii:
    """
    class Meta:
        """
        Set the (destination) course_key field to be unique for the rerun action
        Although multiple reruns can be in progress simultaneously for a particular source course_key,
        only a single rerun action can be in progress for the destination course_key.
        """
        unique_together = ("course_key", "action")

    # FIELDS
    # Original course that is being rerun
    source_course_key = CourseKeyField(max_length=255, db_index=True)

    # Display name for destination course
    display_name = models.CharField(max_length=255, default="", blank=True)  # noqa: DJ012

    # MANAGERS
    # Override the abstract class' manager with a Rerun-specific manager that inherits from the base class' manager.
    objects = CourseRerunUIStateManager()  # noqa: DJ012
