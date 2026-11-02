
"""
Course Experience API utilities.
"""
import logging
from eventtracking import tracker

from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.courseware.masquerade import is_masquerading, setup_masquerade
from lms.djangoapps.course_api.api import course_detail
from openedx.core.djangoapps.schedules.utils import reset_self_paced_schedule
from openedx.features.course_experience.utils import dates_banner_should_display


logger = logging.getLogger(__name__)


def reset_course_deadlines_for_user(user, course_key):
    """
    Core function to reset deadlines for a single course and user.

    Args:
        user: The user object
        course_key: The course key

    Returns:
        bool: True if deadlines were reset, False if gated content prevents reset
    """
    # We ignore the missed_deadlines because this util is used in endpoint from the Learning MFE for
    # learners who have remaining attempts on a problem and reset their due dates in order to
    # submit additional attempts. This can apply for 'completed' (submitted) content that would
    # not be marked as past_due
    _missed_deadlines, missed_gated_content = dates_banner_should_display(course_key, user)
    if not missed_gated_content:
        reset_self_paced_schedule(user, course_key)
        return True
    return False


def reset_bulk_course_deadlines(request, course_keys, research_event_data={}):  # lint-amnesty, pylint: disable=dangerous-default-value
    """
    Reset deadlines for multiple courses for the requesting user.

    Args:
        request (Request): The request object
        course_keys (list): List of course keys
        research_event_data (dict): Any data that should be included in the research tracking event

    Returns:
        tuple: (success_course_keys, failed_course_keys)
    """
    success_course_keys = []
    failed_course_keys = []

    for course_key in course_keys:
        try:
            course_masquerade, user = setup_masquerade(
                request,
                course_key,
                has_access(request.user, 'staff', course_key)
            )

            if reset_course_deadlines_for_user(user, course_key):
                success_course_keys.append(course_key)

                course_overview = course_detail(request, user.username, course_key)

                research_event_data.update({
                    'courserun_key': str(course_key),
                    'is_masquerading': is_masquerading(user, course_key, course_masquerade),
                    'is_staff': has_access(user, 'staff', course_key).has_access,
                    'org_key': course_overview.display_org_with_default,
                    'user_id': user.id,
                })
                tracker.emit('edx.ui.lms.reset_deadlines.clicked', research_event_data)
            else:
                failed_course_keys.append(course_key)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception('Error occurred while trying to reset deadlines!')
            failed_course_keys.append(course_key)

    return success_course_keys, failed_course_keys


def reset_deadlines_for_course(request, course_key, research_event_data={}):  # lint-amnesty, pylint: disable=dangerous-default-value
    """
    Set the start_date of a schedule to today, which in turn will adjust due dates for
    sequentials belonging to a self paced course

    Args:
        request (Request): The request object
        course_key (str): The course key
        research_event_data (dict): Any data that should be included in the research tracking event
            Example: sending the location of where the reset deadlines banner (i.e. outline-tab)
    """

    course_masquerade, user = setup_masquerade(
        request,
        course_key,
        has_access(request.user, 'staff', course_key)
    )

    if reset_course_deadlines_for_user(user, course_key):
        course_overview = course_detail(request, user.username, course_key)
        # For context here, research_event_data should already contain `location` indicating
        # the page/location dates were reset from and could also contain `block_id` if reset
        # within courseware.
        research_event_data.update({
            'courserun_key': str(course_key),
            'is_masquerading': is_masquerading(user, course_key, course_masquerade),
            'is_staff': has_access(user, 'staff', course_key).has_access,
            'org_key': course_overview.display_org_with_default,
            'user_id': user.id,
        })
        tracker.emit('edx.ui.lms.reset_deadlines.clicked', research_event_data)
