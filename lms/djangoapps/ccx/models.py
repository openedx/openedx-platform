"""
Models for the custom course feature
"""


import json
import logging
from datetime import datetime

from ccx_keys.locator import CCXLocator
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.db import models
from lazy import lazy
from opaque_keys.edx.django.models import CourseKeyField, UsageKeyField
from pytz import utc

from xmodule.error_block import ErrorBlock
from xmodule.modulestore.django import modulestore

log = logging.getLogger("edx.ccx")


class CustomCourseForEdX(models.Model):
    """
    A Custom Course.

    .. no_pii:
    """
    course_id = CourseKeyField(db_index=True)
    display_name = models.CharField(max_length=255)
    coach = models.ForeignKey(User, db_index=True, on_delete=models.CASCADE)
    # if not empty, this field contains a json serialized list of
    # the master course modules
    structure_json = models.TextField(verbose_name='Structure JSON', blank=True, null=True)

    class Meta:
        app_label = 'ccx'

    @lazy
    def course(self):
        """Return the CourseBlock of the course related to this CCX"""
        store = modulestore()
        with store.bulk_operations(self.course_id):
            course = store.get_course(self.course_id)
            if not course or isinstance(course, ErrorBlock):
                log.error("CCX {0} from {2} course {1}".format(  # pylint: disable=logging-format-interpolation
                    self.display_name, self.course_id, "broken" if course else "non-existent"
                ))
            return course

    @lazy
    def start(self):
        """Get the value of the override of the 'start' datetime for this CCX
        """
        # avoid circular import problems
        from .overrides import get_override_for_ccx
        return get_override_for_ccx(self, self.course, 'start')

    @lazy
    def due(self):
        """Get the value of the override of the 'due' datetime for this CCX
        """
        # avoid circular import problems
        from .overrides import get_override_for_ccx
        return get_override_for_ccx(self, self.course, 'due')

    @lazy
    def max_student_enrollments_allowed(self):
        """
        Get the value of the override of the 'max_student_enrollments_allowed'
        datetime for this CCX
        """
        # avoid circular import problems
        from .overrides import get_override_for_ccx
        return get_override_for_ccx(self, self.course, 'max_student_enrollments_allowed')

    def has_started(self):
        """Return True if the CCX start date is in the past"""
        return datetime.now(utc) > self.start

    def has_ended(self):
        """Return True if the CCX due date is set and is in the past"""
        if self.due is None:
            return False

        return datetime.now(utc) > self.due

    @property
    def structure(self):
        """
        Deserializes a course structure JSON object
        """
        if self.structure_json:
            return json.loads(self.structure_json)
        return None

    @property
    def locator(self):
        """
        Helper property that gets a corresponding CCXLocator for this CCX.

        Returns:
            The CCXLocator corresponding to this CCX.
        """
        return CCXLocator.from_course_locator(self.course_id, str(self.id))


class CcxFieldOverride(models.Model):
    """
    Field overrides for custom courses.

    .. no_pii:
    """
    ccx = models.ForeignKey(CustomCourseForEdX, db_index=True, on_delete=models.CASCADE)
    location = UsageKeyField(db_index=True)
    field = models.CharField(max_length=255)

    class Meta:
        app_label = 'ccx'
        unique_together = (('ccx', 'location', 'field'),)

    value = models.TextField(default='null')
