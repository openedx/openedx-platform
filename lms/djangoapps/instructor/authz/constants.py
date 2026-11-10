"""
AuthZ permission identifiers used by the Instructor Dashboard.

The permissions are defined by openedx-authz (``openedx_authz.applets.*.constants``);
use them directly (``COURSES_VIEW_COURSE_INFO.identifier``). This module
only groups their identifiers.
"""

from openedx_authz.applets.enrollments.constants import (
    COURSES_ENROLL_LEARNERS,
    COURSES_UNENROLL_LEARNERS,
    COURSES_VIEW_ENROLLMENTS,
)
from openedx_authz.applets.learner_administration.constants import (
    COURSES_MANAGE_COHORTS,
    COURSES_MANAGE_LEARNER_EXTENSIONS,
    COURSES_VIEW_COHORTS,
    COURSES_VIEW_COURSE_INFO,
    COURSES_VIEW_LEARNER_EXTENSIONS,
)
from openedx_authz.applets.learner_grading.constants import COURSES_MANAGE_LEARNER_GRADING, COURSES_VIEW_GRADEBOOK
from openedx_authz.authz import (
    COURSES_MANAGE_COURSE_TEAM,
    COURSES_VIEW_COURSE_TEAM,
    COURSES_VIEW_GRADING_SETTINGS,
)

# Every permission of the Instructor Dashboard. Holding any of them grants access
# to the dashboard.
INSTRUCTOR_DASHBOARD_PERMISSIONS = (
    COURSES_VIEW_COURSE_INFO.identifier,
    COURSES_VIEW_ENROLLMENTS.identifier,
    COURSES_ENROLL_LEARNERS.identifier,
    COURSES_UNENROLL_LEARNERS.identifier,
    COURSES_VIEW_COURSE_TEAM.identifier,
    COURSES_MANAGE_COURSE_TEAM.identifier,
    COURSES_VIEW_GRADEBOOK.identifier,
    COURSES_VIEW_GRADING_SETTINGS.identifier,
    COURSES_MANAGE_LEARNER_GRADING.identifier,
    COURSES_VIEW_LEARNER_EXTENSIONS.identifier,
    COURSES_MANAGE_LEARNER_EXTENSIONS.identifier,
    COURSES_VIEW_COHORTS.identifier,
    COURSES_MANAGE_COHORTS.identifier,
)

# Permissions that give access to each tab of the dashboard. A tab is shown when
# the user holds at least one of them, not a specific one.
TAB_PERMISSIONS = {
    "course_info": (COURSES_VIEW_COURSE_INFO.identifier,),
    "enrollments": (
        COURSES_VIEW_ENROLLMENTS.identifier,
        COURSES_ENROLL_LEARNERS.identifier,
        COURSES_UNENROLL_LEARNERS.identifier,
        # Included because the tab manages the beta testers.
        COURSES_MANAGE_COURSE_TEAM.identifier,
    ),
    "course_team": (
        COURSES_VIEW_COURSE_TEAM.identifier,
        COURSES_MANAGE_COURSE_TEAM.identifier,
    ),
    "grading": (
        COURSES_VIEW_GRADEBOOK.identifier,
        COURSES_VIEW_GRADING_SETTINGS.identifier,
        COURSES_MANAGE_LEARNER_GRADING.identifier,
    ),
    "date_extensions": (
        COURSES_VIEW_LEARNER_EXTENSIONS.identifier,
        COURSES_MANAGE_LEARNER_EXTENSIONS.identifier,
    ),
    "cohorts": (COURSES_VIEW_COHORTS.identifier, COURSES_MANAGE_COHORTS.identifier),
}
