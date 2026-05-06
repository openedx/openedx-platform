"""
course_groups API
"""


from django.contrib.auth.models import User  # pylint: disable=imported-auth-user

from openedx.core.djangoapps.course_groups.models import CohortMembership


def remove_user_from_cohort(course_key, username, cohort_id=None):
    """
    Removes an user from a course group.
    """
    if username is None:
        raise ValueError('Need a valid username')
    user = User.objects.get(username=username)
    if cohort_id is not None:
        membership = CohortMembership.objects.get(
            user=user, course_id=course_key, course_user_group__id=cohort_id
        )
        membership.delete()
    else:
        try:
            membership = CohortMembership.objects.get(user=user, course_id=course_key)
        except CohortMembership.DoesNotExist:
            pass
        else:
            membership.delete()
