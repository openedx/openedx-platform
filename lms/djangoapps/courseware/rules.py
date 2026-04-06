"""
django-rules and Bridgekeeper rules for courseware related features
"""


import logging
import traceback

import laboratory
import rules
from bridgekeeper.rules import EMPTY, Rule
from django.conf import settings
from django.db.models import Q
from opaque_keys.edx.django.models import CourseKeyField
from opaque_keys.edx.keys import CourseKey, UsageKey
from xblock.core import XBlock

from common.djangoapps.student.models import CourseAccessRole
from common.djangoapps.student.roles import CourseRole, OrgRole, strict_role_checking
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.enrollments.api import is_enrollment_valid_for_proctoring
from xmodule.course_block import CourseBlock  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.error_block import ErrorBlock  # lint-amnesty, pylint: disable=wrong-import-order

from .access import has_access

LOG = logging.getLogger(__name__)


@rules.predicate
def is_track_ok_for_exam(user, exam):
    """
    Returns whether the user is in an appropriate enrollment mode
    """
    course_id = CourseKey.from_string(exam['course_id'])
    return is_enrollment_valid_for_proctoring(user.username, course_id)


# The edx_proctoring.api uses this permission to gate access to the
# proctored experience
can_take_proctored_exam = is_track_ok_for_exam
rules.set_perm('edx_proctoring.can_take_proctored_exam', is_track_ok_for_exam)


class HasAccessRule(Rule):
    """
    A rule that calls `has_access` to determine whether it passes
    """
    def __init__(self, action, strict=False):
        self.action = action
        self.strict = strict

    def check(self, user, instance=None):
        if self.strict:
            with strict_role_checking():
                return has_access(user, self.action, instance)
        return has_access(user, self.action, instance)

    def query(self, user):
        # Return an always-empty queryset filter so that this always
        # fails permissions, but still passes the is_possible_for check
        # that is used to determine if the rule should allow a user
        # into django admin
        return Q(pk__in=[])


class StaffAccessExperiment(laboratory.Experiment):  # lint-amnesty, pylint: disable=missing-class-docstring
    def compare(self, control, candidate):
        return bool(control.value) == candidate.value

    def publish(self, result):
        if not result.match:

            LOG.warning(
                "StaffAccessExperiment: control=%r, candidate=%r\n%s",
                result.control,
                result.candidates[0],
                "".join(traceback.format_stack(limit=10))
            )


class HasStaffAccessToContent(Rule):
    """
    Check whether a user has `staff` access in a course.

    Expects to be used to filter a CourseOverview queryset
    """
    def check(self, user, instance=None):
        """
        Return True if the supplied user has staff-level access to the supplied content.
        """
        staff_sql_experiment = StaffAccessExperiment(
            raise_on_mismatch=settings.DEBUG,
            context={'userid': user.id, 'instance': repr(instance)}
        )
        staff_sql_experiment.control(self._check_with_has_access, args=(user, instance))
        staff_sql_experiment.candidate(self._check_with_query, args=(user, instance))
        return staff_sql_experiment.conduct()

    def _check_with_has_access(self, user, instance=None):
        return has_access(user, 'staff', instance)

    def _check_with_query(self, user, instance=None):
        """
        Use the query method to check whether a single user has access to the supplied object.
        """
        # delegate the work to type-specific functions.
        # (start with more specific types, then get more general)
        if isinstance(instance, (CourseBlock, CourseOverview)):
            course_key = instance.id
        elif isinstance(instance, (ErrorBlock, XBlock)):
            course_key = instance.scope_ids.usage_id.course_key
        elif isinstance(instance, CourseKey):
            course_key = instance
        elif isinstance(instance, UsageKey):
            course_key = instance.course_key
        elif isinstance(instance, str):
            course_key = CourseKey.from_string(instance)

        return self.filter(user, CourseOverview.objects.filter(id=course_key)).exists()

    def query(self, user):
        """
        Returns a Q object that expects to be used to filter CourseOverview queries.
        """
        if not user.is_authenticated:
            return EMPTY

        masq_settings = getattr(user, 'masquerade_settings', {})
        masq_as_student = [
            course_key for
            (course_key, masq_setting) in masq_settings.items()
            if masq_setting.role == 'student'
        ]

        not_masquerading_as_student = ~Q(id__in=masq_as_student)

        is_global_staff = user.is_staff
        course_staff_or_instructor_courses = CourseAccessRole.objects.filter(
            user=user,
            role__in=('staff', 'instructor')
        ).exclude(
            course_id=CourseKeyField.Empty,
        ).values('course_id')
        org_staff_or_instructor_courses = CourseAccessRole.objects.filter(
            user=user,
            role__in=('staff', 'instructor'),
            course_id=CourseKeyField.Empty,
            org__isnull=False
        ).values('org')

        query = not_masquerading_as_student
        if not is_global_staff:
            query &= Q(id__in=course_staff_or_instructor_courses) | Q(org__in=org_staff_or_instructor_courses)
        return query


class HasRolesRule(Rule):  # lint-amnesty, pylint: disable=abstract-method, missing-class-docstring
    def __init__(self, *roles):
        self.roles = roles

    def check(self, user, instance=None):
        if not user.is_authenticated:
            return False
        if isinstance(instance, CourseKey):
            course_key = instance
        elif isinstance(instance, (CourseBlock, CourseOverview)):
            course_key = instance.id
        elif isinstance(instance, (ErrorBlock, XBlock)):
            course_key = instance.scope_ids.usage_id.course_key
        else:
            course_key = CourseKey.from_string(str(instance))

        for role in self.roles:
            if CourseRole(role, course_key).has_user(user):
                return True
            if OrgRole(role, course_key.org).has_user(user):
                return True
        return False
