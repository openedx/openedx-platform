"""
Instructor Task Django app REST API views.
"""
import datetime
import json
import logging

import dateutil
import pytz
from celery.states import REVOKED
from django.db import transaction
from rest_framework import generics, status
from rest_framework.response import Response

from lms.djangoapps.bulk_email.api import update_course_email
from lms.djangoapps.instructor_task.data import InstructorTaskTypes
from lms.djangoapps.instructor_task.models import SCHEDULED, InstructorTask, InstructorTaskSchedule
from lms.djangoapps.instructor_task.rest_api.v1.exceptions import TaskUpdateException
from lms.djangoapps.instructor_task.rest_api.v1.permissions import CanViewOrModifyScheduledBulkCourseEmailTasks
from lms.djangoapps.instructor_task.rest_api.v1.serializers import ScheduledBulkEmailSerializer

log = logging.getLogger(__name__)


class ListScheduledBulkEmailInstructorTasks(generics.ListAPIView):
    """
    Read only view to list all scheduled bulk email messages for a course-run.

    Path: GET `api/instructor_task/v1/schedules/{course_id}/bulk_email`

    Returns:
        * 200: OK - Contains a list of all scheduled bulk email instructor tasks that haven't been executed yet. This
               data also includes information about the and course email instance associated with each task.
        * 403: User does not have the required role to view this data.
    """
    permission_classes = (
        CanViewOrModifyScheduledBulkCourseEmailTasks,
    )
    serializer_class = ScheduledBulkEmailSerializer

    def get_queryset(self):
        """
        Filters the results so that only scheduled bulk email tasks for the specific course-run are returned.
        """
        course_id = self.kwargs["course_id"]
        return (
            InstructorTaskSchedule.objects
            .filter(task__course_id=course_id)
            .filter(task__task_state=SCHEDULED)
            .filter(task__task_type=InstructorTaskTypes.BULK_COURSE_EMAIL)
            .order_by('id')
        )


class ModifyScheduledBulkEmailInstructorTask(generics.DestroyAPIView, generics.UpdateAPIView):
    """
    A view that supports the modification of instructor task schedules. It provides the ability to:
        * Delete an instructor task schedule
        * Update an instructor task schedule and/or update the course email associated with the scheduled task.


    Path: DELETE or PATCH `api/instructor_task/v1/schedules/{course_id}/bulk_email/{task_schedule_id}`

    Returns:
        * 200: The schedule or email content was updated successfully.
        * 204: No Content - Deleting the schedule was successful.
        * 400: Bad request - Updating the email content or schedule failed due to bad data in the request.
        * 403: User does not have permission to modify the object specified.
        * 404: Requested schedule object could not be found and thus could not be modified or removed.
    """
    permission_classes = (
        CanViewOrModifyScheduledBulkCourseEmailTasks,
    )
    serializer_class = ScheduledBulkEmailSerializer

    def destroy(self, request, *args, **kwargs):
        course_id = kwargs["course_id"]
        schedule_id = kwargs["schedule_id"]

        log.info(f"Cancelling instructor task schedule with id '{schedule_id}' in course '{course_id}'")
        try:
            schedule = InstructorTaskSchedule.objects.get(id=schedule_id)
        except InstructorTaskSchedule.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        task = schedule.task
        # verify the task hasn't already been processed before revoking
        try:
            self._verify_task_state(task)
        except TaskUpdateException as task_update_err:
            return Response(
                {"detail": str(task_update_err)},
                status=status.HTTP_400_BAD_REQUEST
            )

        # update the task's status to REVOKED and then delete the task schedule instance
        log.info(f"Revoking instructor task with id '{task.id}' for course '{task.course_id}'")
        task.task_state = REVOKED
        task.task_output = InstructorTask.create_output_for_revoked()
        task.save()
        schedule.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)

    def patch(self, request, *args, **kwargs):
        course_id = kwargs["course_id"]
        schedule_id = kwargs["schedule_id"]

        # extract schedule and email data from the request
        schedule = request.data.get("schedule", None)
        email_data = request.data.get("email", None)

        try:
            task_schedule = InstructorTaskSchedule.objects.get(id=schedule_id)
            task = task_schedule.task
            self._verify_task_state(task)

            with transaction.atomic():
                if schedule:
                    schedule_dt = dateutil.parser.parse(schedule).replace(tzinfo=pytz.utc)
                    self._verify_valid_schedule(schedule_id, schedule_dt)
                    task_schedule.task_due = schedule_dt
                    task_schedule.save()
                if email_data:
                    email_id = email_data.get("id")
                    self._verify_task_and_email_associated(task, email_id)
                    targets = email_data.get("targets")
                    subject = email_data.get("subject")
                    message = email_data.get("message")
                    update_course_email(course_id, email_id, targets, subject, message)
        except (TaskUpdateException, ValueError) as task_update_err:
            return Response(
                {"detail": str(task_update_err)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except InstructorTaskSchedule.DoesNotExist:
            error_message = (
                f"Cannot update instructor task schedule '{schedule_id}', a schedule with this ID does not exist"
            )
            return Response(
                {"detail": error_message},
                status=status.HTTP_404_NOT_FOUND
            )
        else:
            return Response(ScheduledBulkEmailSerializer(instance=task_schedule).data)

    def _verify_valid_schedule(self, schedule_id, schedule):
        """
        Verifies that the updated schedule data for the task is valid. We check to make sure that the date or time
        requested is not in the past.
        """
        now = datetime.datetime.now(pytz.utc)
        if schedule < now:
            raise TaskUpdateException(
                f"Cannot update instructor task schedule '{schedule_id}', the updated schedule occurs in the past"
            )

    def _verify_task_state(self, task):
        """
        Verifies that the task is still in the `SCHEDULED` state. If we have already started (or finished) processing
        the task then there is no need to update it.
        """
        if task.task_state != SCHEDULED:
            raise TaskUpdateException(
                f"Cannot update instructor task '{task.id}' for course '{task.course_id}', this task has already been "
                "processed"
            )

    def _verify_task_and_email_associated(self, task, email_id):
        """
        Verifies that the email we are trying to update is actually associated with the task in question.
        """
        email_id_from_task = json.loads(task.task_input).get("email_id", None)
        if email_id_from_task != email_id:
            raise TaskUpdateException(
                f"Cannot update instructor task '{task.id} for course '{task.course_id}', the email id '{email_id}' "
                f"specified in the request does not match the email id associated with the task"
            )
