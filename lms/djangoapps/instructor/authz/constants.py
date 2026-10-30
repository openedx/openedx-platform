"""
AuthZ permission identifiers used by the Instructor Dashboard.

The ``courses.manage_course_team``, ``courses.view_course_team`` and
``courses.view_grading_settings`` permissions are shared with Authoring and
imported from openedx-authz.

TEMPORARY: the rest depend on a separate issue that will define them in
openedx-authz. They are declared here only for testing.
"""

from openedx_authz.constants import permissions as authz_permissions

# Course info
COURSES_VIEW_COURSE_INFO = "courses.view_course_info"

# Enrollments
COURSES_VIEW_ENROLLMENTS = "courses.view_enrollments"
COURSES_ENROLL_LEARNERS = "courses.enroll_learners"
COURSES_UNENROLL_LEARNERS = "courses.unenroll_learners"

# Course team (shared with Authoring)
COURSES_VIEW_COURSE_TEAM = authz_permissions.COURSES_VIEW_COURSE_TEAM.identifier
COURSES_MANAGE_COURSE_TEAM = authz_permissions.COURSES_MANAGE_COURSE_TEAM.identifier

# Grading
COURSES_VIEW_GRADEBOOK = "courses.view_gradebook"
COURSES_VIEW_GRADING_CONFIGURATION = "courses.view_grading_configuration"
COURSES_VIEW_GRADING_SETTINGS = authz_permissions.COURSES_VIEW_GRADING_SETTINGS.identifier
COURSES_MANAGE_LEARNER_GRADING = "courses.manage_learner_grading"

# Date extensions
COURSES_VIEW_LEARNER_EXTENSIONS = "courses.view_learner_extensions"
COURSES_MANAGE_LEARNER_EXTENSIONS = "courses.manage_learner_extensions"

# Cohorts
COURSES_VIEW_COHORTS = "courses.view_cohorts"
COURSES_MANAGE_COHORTS = "courses.manage_cohorts"

# Every permission whose effective value is returned to the MFE in
# ``authz_permissions``.
INSTRUCTOR_DASHBOARD_PERMISSIONS = (
    COURSES_VIEW_COURSE_INFO,
    COURSES_VIEW_ENROLLMENTS,
    COURSES_ENROLL_LEARNERS,
    COURSES_UNENROLL_LEARNERS,
    COURSES_VIEW_COURSE_TEAM,
    COURSES_MANAGE_COURSE_TEAM,
    COURSES_VIEW_GRADEBOOK,
    COURSES_VIEW_GRADING_CONFIGURATION,
    COURSES_VIEW_GRADING_SETTINGS,
    COURSES_MANAGE_LEARNER_GRADING,
    COURSES_VIEW_LEARNER_EXTENSIONS,
    COURSES_MANAGE_LEARNER_EXTENSIONS,
    COURSES_VIEW_COHORTS,
    COURSES_MANAGE_COHORTS,
)

# Permissions that give access to each tab of the dashboard. A tab is shown when
# the user holds at least one of them, not a specific one.
TAB_PERMISSIONS = {
    "course_info": (COURSES_VIEW_COURSE_INFO,),
    "enrollments": (
        COURSES_VIEW_ENROLLMENTS,
        COURSES_ENROLL_LEARNERS,
        COURSES_UNENROLL_LEARNERS,
        COURSES_MANAGE_COURSE_TEAM,  # It's included because the tab manages the beta testers.
    ),
    "course_team": (COURSES_VIEW_COURSE_TEAM, COURSES_MANAGE_COURSE_TEAM),
    "grading": (
        COURSES_VIEW_GRADEBOOK,
        COURSES_VIEW_GRADING_CONFIGURATION,
        COURSES_VIEW_GRADING_SETTINGS,
        COURSES_MANAGE_LEARNER_GRADING,
    ),
    "date_extensions": (COURSES_VIEW_LEARNER_EXTENSIONS, COURSES_MANAGE_LEARNER_EXTENSIONS),
    "cohorts": (COURSES_VIEW_COHORTS, COURSES_MANAGE_COHORTS),
}
