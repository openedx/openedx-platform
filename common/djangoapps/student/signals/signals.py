"""
Enrollment track related signals.
"""


from django.dispatch import Signal
from openedx_events.learning.data import CourseAccessRoleData, UserData, UserPersonalData
from openedx_events.learning.signals import COURSE_ACCESS_ROLE_ADDED, COURSE_ACCESS_ROLE_REMOVED

# The purely documentational providing_args argument for Signal is deprecated.
# So we are moving the args to a comment.

# providing_args=['user', 'course_key', 'mode', 'countdown']
ENROLLMENT_TRACK_UPDATED = Signal()

# providing_args=["course_enrollment", "skip_refund"]
UNENROLL_DONE = Signal()

# providing_args=["event", "user", "course_id", "mode", "cost", "currency"]
ENROLL_STATUS_CHANGE = Signal()

# providing_args=["course_enrollment"]
REFUND_ORDER = Signal()

USER_EMAIL_CHANGED = Signal()


def emit_course_access_role_added(user, course_id, org_key, role):
    """
    Emit an event to the event-bus when a CourseAccessRole is added
    """
    # .. event_implemented_name: COURSE_ACCESS_ROLE_ADDED
    # .. event_type: org.openedx.learning.user.course_access_role.added.v1
    COURSE_ACCESS_ROLE_ADDED.send_event(
        course_access_role_data=CourseAccessRoleData(
            user=UserData(
                pii=UserPersonalData(
                    username=user.username,
                    email=user.email,
                ),
                id=user.id,
                is_active=user.is_active,
            ),
            course_key=course_id,
            org_key=org_key,
            role=role,
        )
    )


def emit_course_access_role_removed(user, course_id, org_key, role):
    """
    Emit an event to the event-bus when a CourseAccessRole is deleted
    """
    # .. event_implemented_name: COURSE_ACCESS_ROLE_REMOVED
    # .. event_type: org.openedx.learning.user.course_access_role.removed.v1
    COURSE_ACCESS_ROLE_REMOVED.send_event(
        course_access_role_data=CourseAccessRoleData(
            user=UserData(
                pii=UserPersonalData(
                    username=user.username,
                    email=user.email,
                ),
                id=user.id,
                is_active=user.is_active,
            ),
            course_key=course_id,
            org_key=org_key,
            role=role,
        )
    )
