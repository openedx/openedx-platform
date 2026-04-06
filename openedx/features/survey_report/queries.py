"""
Queries to get data from database.
"""

import logging
from datetime import datetime, timedelta

from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.db.models import Count, OuterRef, Q, Subquery

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.util.query import read_replica_or_default
from lms.djangoapps.grades.models import PersistentCourseGrade
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

log = logging.getLogger(__name__)

MIN_ENROLLS_ACTIVE_COURSE: int = 5


def get_unique_courses_offered() -> int:
    """
    Get total number of unique course that started before today and have an open date,
    or have not finished yet, whose number of enrollments is greater than MIN_ENROLLS_ACTIVE_COURSE.
    """
    log.info("Getting the total number of unique courses offered...")
    total = CourseOverview.objects.annotate(
        count=Subquery(
            CourseEnrollment.objects
                            .filter(course_id=OuterRef('id'))
                            .values('course_id')
                            .annotate(count=Count('course_id'))
                            .values('count')
        ))\
        .filter(count__gt=MIN_ENROLLS_ACTIVE_COURSE)\
        .filter(start__lt=datetime.now())\
        .filter(Q(end__isnull=True) | Q(end__gt=datetime.now()))\
        .using(read_replica_or_default())\
        .count()
    log.info("Getting the total number of unique courses offered... DONE")
    return total


def get_recently_active_users(weeks: int) -> int:
    """
    Get total number of users with last login in the last weeks.

    Args:
        weeks (int): number of weeks since the last login to considerate as an active learner.
    """
    log.info("Getting the total number of recently active users...")
    total = User.objects.filter(last_login__gte=datetime.now() - timedelta(weeks=weeks))\
        .using(read_replica_or_default())\
        .count()
    log.info("Getting total number of recently active users... DONE")
    return total


def get_registered_learners() -> int:
    """
    Get total number of active learners registered.
    """
    log.info("Getting the total number of ever registered learners...")
    total = User.objects.filter(is_active=True)\
        .using(read_replica_or_default())\
        .count()
    log.info("Getting the total number of ever registered learners... DONE")
    return total


def get_generated_certificates() -> int:
    """
    Get total number of generated certificates.
    """
    log.info("Getting the total number of generated certificates...")
    total = PersistentCourseGrade.objects.filter(passed_timestamp__isnull=False)\
        .using(read_replica_or_default())\
        .count()
    log.info("Getting the total number of generated certificates... DONE")
    return total


def get_course_enrollments() -> int:
    """
    Get total number of enrollments from users that aren't staff. Course staff members will be included.
    """
    log.info("Getting the total number of course enrollments...")
    total = CourseEnrollment.objects.filter(is_active=True, user__is_superuser=False, user__is_staff=False)\
        .using(read_replica_or_default())\
        .count()
    log.info("Getting the total number of course enrollments... DONE")
    return total
