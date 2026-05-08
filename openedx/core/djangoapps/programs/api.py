"""
Python APIs exposed by the Programs app to other in-process apps.
"""

from .utils import is_user_enrolled_in_program_type as _is_user_enrolled_in_program_type


def is_user_enrolled_in_program_type(user, program_type_slug, paid_modes_only=False, enrollments=None, entitlements=None):  # pylint: disable=line-too-long
    """
    This method will look at the learners Enrollments and Entitlements to determine
    if a learner is enrolled in a Program of the given type.

    NOTE: This method relies on the Program Cache right now. The goal is to move away from this
    in the future.

    Arguments:
        user (User): The user we are looking for.
        program_type_slug (str): The slug of the Program type we are looking for.
        paid_modes_only (bool): Request if the user is enrolled in a Program in a paid mode, False by default.
        enrollments (List[Dict]): Takes a serialized list of CourseEnrollments linked to the user
        entitlements (List[CourseEntitlement]): Take a list of CourseEntitlement objects linked to the user

        NOTE: Both enrollments and entitlements will be collected if they are not passed in. They are available
        as parameters in case they were already collected, to save duplicate queries in high traffic areas.

    Returns:
        bool: True is the user is enrolled in programs of the requested type
    """

    return _is_user_enrolled_in_program_type(
        user,
        program_type_slug,
        paid_modes_only=paid_modes_only,
        enrollments=enrollments,
        entitlements=entitlements
    )
