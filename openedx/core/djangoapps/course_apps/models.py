"""
Models to store course apps data.
"""
from typing import Dict  # noqa: UP035

from django.db import models
from model_utils.models import TimeStampedModel
from opaque_keys.edx.django.models import CourseKeyField
from opaque_keys.edx.keys import CourseKey
from simple_history.models import HistoricalRecords


class CourseAppStatus(TimeStampedModel):
    """
    CourseAppStatus stores the enabled/disabled status for course apps.

    .. no_pii:
    """

    app_id = models.CharField(max_length=32, db_index=True)
    enabled = models.BooleanField(default=False)
    course_key = CourseKeyField(max_length=255, db_index=True)

    history = HistoricalRecords()

    def __str__(self):
        return f'CourseAppStatus(course_key="{self.course_key}", app_id="{self.app_id}", enabled="{self.enabled})"'

    @classmethod
    def get_all_app_status_data_for_course(cls, course_key: CourseKey) -> Dict[str, bool]:  # noqa: UP006
        """
        Get a dictionary containing the status of all apps linked to the course.

        Args:
            course_key (CourseKey): the course id for which app status is needed

        Returns:
             A dictionary where the keys are app ids and the values are booleans
             indicating if the app with that id is enabled
        """
        return dict(
            cls.objects.filter(course_key=course_key).values("app_id", "enabled")
        )

    @classmethod
    def update_status_for_course_app(cls, course_key: CourseKey, app_id: str, enabled: bool):
        """
        Creates or updates a status entry for the specified app and course.

        Args:
            course_key (CourseKey): the course id for which the app status is to
                be set
            app_id (str): id for course app to update
            enabled (bool): enabled status to set for app
        """
        CourseAppStatus.objects.update_or_create(
            course_key=course_key,
            app_id=app_id,
            defaults={'enabled': enabled}
        )

    class Meta:
        constraints = [models.UniqueConstraint(fields=("app_id", "course_key"), name="unique_app_config_for_course")]
        indexes = [models.Index(fields=("app_id", "course_key"))]
        app_label = "course_apps"
