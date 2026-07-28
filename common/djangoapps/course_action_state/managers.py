"""
Model Managers for Course Actions
"""

import traceback

from django.db import models, transaction


class CourseActionStateManager(models.Manager):
    """
    An abstract Model Manager class for Course Action State models.
    This abstract class expects child classes to define the ACTION (string) field.
    """
    class Meta:
        """Abstract manager class, with subclasses defining the ACTION (string) field."""
        abstract = True

    def find_all(self, exclude_args=None, **kwargs):
        """
        Finds and returns all entries for this action and the given field names-and-values in kwargs.
        The exclude_args dict allows excluding entries with the field names-and-values in exclude_args.
        """
        return self.filter(action=self.ACTION, **kwargs).exclude(**(exclude_args or {}))

    def find_first(self, exclude_args=None, **kwargs):
        """
        Returns the first entry for the this action and the given fields in kwargs, if found.
        The exclude_args dict allows excluding entries with the field names-and-values in exclude_args.

        Raises ItemNotFoundError if more than 1 entry is found.

        There may or may not be greater than one entry, depending on the usage pattern for this Action.
        """
        objects = self.find_all(exclude_args=exclude_args, **kwargs)
        if len(objects) == 0:  # pylint: disable=no-else-raise
            raise CourseActionStateItemNotFoundError(
                "No entry found for action {action} with filter {filter}, excluding {exclude}".format(  # noqa: UP032
                    action=self.ACTION,
                    filter=kwargs,
                    exclude=exclude_args,
                ))
        else:
            return objects[0]

    def delete(self, entry_id):
        """
        Deletes the entry with given id.
        """
        self.filter(id=entry_id).delete()


class CourseActionUIStateManager(CourseActionStateManager):
    """
    A Model Manager subclass of the CourseActionStateManager class that is aware of UI-related fields related
    to state management, including "should_display" and "message".
    """

    # add transaction protection to revert changes by get_or_create if an exception is raised before the final save.
    @transaction.atomic
    def update_state(
            self, course_key, new_state, should_display=True, message="", user=None, allow_not_found=False, **kwargs
    ):
        """
        Updates the state of the given course for this Action with the given data.
        If allow_not_found is True, automatically creates an entry if it doesn't exist.
        Raises CourseActionStateException if allow_not_found is False and an entry for the given course
            for this Action doesn't exist.
        """
        state_object, created = self.get_or_create(course_key=course_key, action=self.ACTION)

        if created:
            if allow_not_found:
                state_object.created_user = user
            else:
                raise CourseActionStateItemNotFoundError(
                    "Cannot update non-existent entry for course_key {course_key} and action {action}".format(  # noqa: UP032  # pylint: disable=line-too-long
                        action=self.ACTION,
                        course_key=course_key,
                    ))

        # some state changes may not be user-initiated so override the user field only when provided
        if user:
            state_object.updated_user = user

        state_object.state = new_state
        state_object.should_display = should_display
        state_object.message = message

        # update any additional fields in kwargs
        if kwargs:
            for key, value in kwargs.items():
                setattr(state_object, key, value)

        state_object.save()
        return state_object

    def update_should_display(self, entry_id, user, should_display):
        """
        Updates the should_display field with the given value for the entry for the given id.
        """
        return self.update(id=entry_id, updated_user=user, should_display=should_display)


class CourseRerunUIStateManager(CourseActionUIStateManager):
    """
    A concrete model Manager for the Reruns Action.
    """
    ACTION = "rerun"

    class State:
        """
        An Enum class for maintaining the list of possible states for Reruns.
        """
        IN_PROGRESS = "in_progress"
        FAILED = "failed"
        SUCCEEDED = "succeeded"

    def initiated(self, source_course_key, destination_course_key, user, display_name):
        """
        To be called when a new rerun is initiated for the given course by the given user.
        """
        self.update_state(
            course_key=destination_course_key,
            new_state=self.State.IN_PROGRESS,
            user=user,
            allow_not_found=True,
            source_course_key=source_course_key,
            display_name=display_name,
        )

    def succeeded(self, course_key):
        """
        To be called when an existing rerun for the given course has successfully completed.
        """
        self.update_state(
            course_key=course_key,
            new_state=self.State.SUCCEEDED,
        )

    def failed(self, course_key):
        """
        To be called within an exception handler when an existing rerun for the given course has failed.
        """
        self.update_state(
            course_key=course_key,
            new_state=self.State.FAILED,
            message=traceback.format_exc()[-self.model.MAX_MESSAGE_LENGTH:],  # truncate to fit
        )


class CourseActionStateItemNotFoundError(Exception):
    """An exception class for errors specific to Course Action states."""
    pass  # pylint: disable=unnecessary-pass
