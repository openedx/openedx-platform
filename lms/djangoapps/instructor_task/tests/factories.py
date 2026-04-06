"""
Instructor Task Factory
"""

import datetime
import json

import factory
from celery.states import PENDING
from factory.django import DjangoModelFactory
from opaque_keys.edx.locator import CourseLocator

from common.djangoapps.student.tests.factories import UserFactory as StudentUserFactory
from lms.djangoapps.instructor_task.data import InstructorTaskTypes
from lms.djangoapps.instructor_task.models import InstructorTask, InstructorTaskSchedule


class InstructorTaskFactory(DjangoModelFactory):
    """
    Factory used to create InstructorTask instances in unit tests.
    """
    class Meta:
        model = InstructorTask

    task_type = InstructorTaskTypes.RESCORE_PROBLEM
    course_id = CourseLocator("MITx", "999", "Robot_Super_Course")
    task_input = json.dumps({})
    task_key = None
    task_id = None
    task_state = PENDING
    task_output = None
    requester = factory.SubFactory(StudentUserFactory)


class InstructorTaskScheduleFactory(DjangoModelFactory):
    """
    Factory used to create InstructorTaskSchedule instances in unit tests.
    """
    class Meta:
        model = InstructorTaskSchedule

    task = factory.SubFactory(InstructorTaskFactory)
    task_args = "{}"
    task_due = datetime.datetime.now().replace(tzinfo=datetime.timezone.utc) - datetime.timedelta(days=1)  # noqa: UP017
