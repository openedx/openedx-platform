"""
Utils for discussion API.
"""
import logging
from datetime import datetime
from typing import Callable, Dict, List  # noqa: UP035

import requests
from crum import get_current_request
from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models.functions import Length
from pytz import UTC

from common.djangoapps.student.models import CourseAccessRole
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from lms.djangoapps.discussion.config.settings import ENABLE_CAPTCHA_IN_DISCUSSION
from lms.djangoapps.discussion.django_comment_client.utils import has_discussion_privileges
from openedx.core.djangoapps.discussions.models import DiscussionsConfiguration, PostingRestriction
from openedx.core.djangoapps.django_comment_common.comment_client.thread import Thread
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_GROUP_MODERATOR,
    FORUM_ROLE_MODERATOR,
    FORUM_ROLE_STUDENT,
    Role,
)

from ..django_comment_client.utils import get_user_role_names

log = logging.getLogger(__name__)


class AttributeDict(dict):
    """
    Converts Dict Keys into Attributes
    """
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__


def discussion_open_for_user(course, user):
    """
    Check if the course discussion are open or not for user.

    Arguments:
            course: Course to check discussions for
            user: User to check for privileges in course
    """
    discussions_posting_restrictions = DiscussionsConfiguration.get(course.id).posting_restrictions
    blackout_dates = course.get_discussion_blackout_datetimes()
    return (
        is_posting_allowed(discussions_posting_restrictions, blackout_dates) or
        has_discussion_privileges(user, course.id)
    )


def set_attribute(threads, attribute, value):
    """
    Iterates over the list of dicts and assigns the provided value to the given attribute

    Arguments:
        threads: List of threads (dict objects)
        attribute: the key for thread dict
        value: the value to assign to the thread attribute
    """
    for thread in threads:
        thread[attribute] = value
    return threads


def get_usernames_from_search_string(course_id, search_string, page_number, page_size):
    """
    Gets usernames for all users in course that match string.

    Args:
            course_id (CourseKey): Course to check discussions for
            search_string (str): String to search matching
            page_number (int): Page number to fetch
            page_size (int): Number of items in each page

    Returns:
            page_matched_users (str): comma seperated usernames for the page
            matched_users_count (int): count of matched users in course
            matched_users_pages (int): pages of matched users in course
    """
    matched_users_in_course = User.objects.filter(
        courseenrollment__course_id=course_id,
        username__icontains=search_string).order_by(Length('username').asc()).values_list('username', flat=True)
    if not matched_users_in_course:
        return '', 0, 0
    matched_users_count = len(matched_users_in_course)
    paginator = Paginator(matched_users_in_course, page_size)
    page_matched_users = paginator.page(page_number)
    matched_users_pages = int(matched_users_count / page_size)
    return ','.join(page_matched_users), matched_users_count, matched_users_pages


def get_usernames_for_course(course_id, page_number, page_size):
    """
    Gets usernames for all users in course.

    Args:
            course_id (CourseKey): Course to check discussions for
            page_number (int): Page numbers to fetch
            page_size (int): Number of items in each page

    Returns:
            page_matched_users (str): comma seperated usernames for the page
            matched_users_count (int): count of matched users in course
            matched_users_pages (int): pages of matched users in course
    """
    matched_users_in_course = User.objects.filter(courseenrollment__course_id=course_id, ) \
        .order_by(Length('username').asc()).values_list('username', flat=True)
    if not matched_users_in_course:
        return '', 0, 0
    matched_users_count = len(matched_users_in_course)
    paginator = Paginator(matched_users_in_course, page_size)
    page_matched_users = paginator.page(page_number)
    matched_users_pages = int(matched_users_count / page_size)
    return ','.join(page_matched_users), matched_users_count, matched_users_pages


def add_stats_for_users_with_no_discussion_content(course_stats, users_in_course):
    """
    Update users stats for users with no discussion stats available in course
    """
    users_returned_from_api = [user['username'] for user in course_stats]
    user_list = users_in_course.split(',')
    users_with_no_discussion_content = set(user_list) ^ set(users_returned_from_api)
    updated_course_stats = course_stats
    for user in users_with_no_discussion_content:
        updated_course_stats.append({
            'username': user,
            'threads': 0,
            'replies': 0,
            'responses': 0,
            'active_flags': 0,
            'inactive_flags': 0,
        })
    updated_course_stats = sorted(updated_course_stats, key=lambda d: len(d['username']))
    return updated_course_stats


def get_course_staff_users_list(course_id):
    """
    Gets user ids for Staff roles for course discussions.
    Roles Course Instructor and Course Staff.
    """
    # TODO: cache course_staff_user_ids if we need to improve perf
    course_staff_user_ids = []
    staff = list(CourseStaffRole(course_id).users_with_role().values_list('id', flat=True))
    admins = list(CourseInstructorRole(course_id).users_with_role().values_list('id', flat=True))
    course_staff_user_ids.extend(staff)
    course_staff_user_ids.extend(admins)
    return list(set(course_staff_user_ids))


def get_course_ta_users_list(course_id):
    """
    Gets user ids for TA roles for course discussions.
    Roles include Community TA and Group Community TA.
    """
    # TODO: cache ta_users_ids if we need to improve perf
    ta_users_ids = [
        user.id
        for role in Role.objects.filter(name__in=[FORUM_ROLE_GROUP_MODERATOR,
                                                  FORUM_ROLE_COMMUNITY_TA], course_id=course_id)
        for user in role.users.all()
    ]
    return ta_users_ids


def get_moderator_users_list(course_id):
    """
    Gets user ids for Moderator roles for course discussions.
    Roles include Discussion Administrator and Discussion Moderator.
    """
    # TODO: cache moderator_user_ids if we need to improve perf
    moderator_user_ids = [
        user.id
        for role in Role.objects.filter(
            name__in=[FORUM_ROLE_ADMINISTRATOR, FORUM_ROLE_MODERATOR],
            course_id=course_id
        )
        for user in role.users.all()
    ]
    return moderator_user_ids


def filter_topic_from_discussion_id(discussion_id, topics_list):
    """
    Returns topic based on discussion id
    """
    for topic in topics_list:
        if topic.get("id") == discussion_id:
            return topic
    return {}


def create_discussion_children_from_ids(children_ids, blocks, topics):
    """
    Takes ids of discussion and return discussion dictionary
    """
    discussions = []
    for child_id in children_ids:
        topic = blocks.get(child_id)
        if topic.get('type') == 'vertical':
            discussions_id = topic.get('discussions_id')
            topic = filter_topic_from_discussion_id(discussions_id, topics)
        if topic:
            discussions.append(topic)
    return discussions


def create_blocks_params(course_usage_key, user):
    """
    Returns param dict that is needed to get blocks
    """
    return {
        'usage_key': course_usage_key,
        'user': user,
        'depth': None,
        'nav_depth': None,
        'requested_fields': {
            'display_name',
            'student_view_data',
            'children',
            'discussions_id',
            'type',
            'block_types_filter'
        },
        'block_counts': set(),
        'student_view_data': {'discussion'},
        'return_type': 'dict',
        'block_types_filter': {
            'discussion',
            'chapter',
            'vertical',
            'sequential',
            'course'
        }
    }


def add_thread_stats_to_subsection(topics_list):
    """
    Add topic stats at subsection by adding stats of all units in
    the topic
    """
    for section in topics_list:
        for subsection in section.get('children', []):
            discussions = 0
            questions = 0
            for unit in subsection.get('children', []):
                thread_counts = unit.get('thread_counts', {})
                discussions += thread_counts.get('discussion', 0)
                questions += thread_counts.get('question', 0)
            subsection['thread_counts'] = {
                'discussion': discussions,
                'question': questions,
            }


def create_topics_v3_structure(blocks, topics):
    """
    Create V3 topics structure from blocks and v2 topics
    """
    non_courseware_topics = [
        dict({**topic, 'courseware': False})
        for topic in topics
        if topic.get('usage_key', '') is None
    ]
    courseware_topics = []
    for key, value in blocks.items():  # noqa: B007
        if value.get("type") == "chapter":
            value['courseware'] = True
            courseware_topics.append(value)
            value['children'] = create_discussion_children_from_ids(
                value.get('children', []),
                blocks,
                topics,
            )
            subsections = value.get('children')
            for subsection in subsections:
                subsection['children'] = create_discussion_children_from_ids(
                    subsection.get('children', []),
                    blocks,
                    topics,
                )

    add_thread_stats_to_subsection(courseware_topics)
    structured_topics = non_courseware_topics + courseware_topics
    topic_ids = get_topic_ids_from_topics(topics)

    # Remove all topic ids that are contained in the structured topics
    for chapter in structured_topics:
        for sequential in chapter.get('children', []):
            for item in sequential['children']:
                topic_ids.remove(item['id'])

    archived_topics = {
        'id': "archived",
        'children': get_archived_topics(topic_ids, topics)
    }
    if archived_topics['children']:
        structured_topics.append(archived_topics)

    return remove_empty_sequentials(structured_topics)


def remove_empty_sequentials(data):
    """
    Removes all objects of type "sequential" from a nested list of objects if they
    have no children.
    After removing the empty sequentials, if the parent of the sequential is now empty,
    it will also be removed.
    Parameters:
    data (list): A list of nested objects to check and remove empty sequentials from.

    Returns:
    list: The modified list with empty sequentials removed.
    """
    new_data = []
    for obj in data:
        block_type = obj.get('type')
        if block_type != 'sequential' or (block_type == 'sequential' and obj.get('children')):
            if obj.get('children'):
                obj['children'] = remove_empty_sequentials(obj['children'])
                if obj['children'] or block_type != 'chapter':
                    new_data.append(obj)
            else:
                if block_type != 'chapter':
                    new_data.append(obj)
    return new_data


def get_topic_ids_from_topics(topics: List[Dict[str, str]]) -> List[str]:  # noqa: UP006
    """
    This function takes a list of topics and returns a list of the topic ids.

    Args:
    - topics (List[Dict[str, str]]): A list of topic dictionaries. Each dictionary should have an 'id' field.

    Returns:
    - A list of topic ids, extracted from the input list of topics.
    """
    return [topic['id'] for topic in topics]


def get_archived_topics(filtered_topic_ids: List[str], topics: List[Dict[str, str]]) -> List[Dict[str, str]]:  # noqa: UP006  # pylint: disable=line-too-long
    """
    This function takes a list of topic ids and a list of topics, and returns the list of archived topics.

    A topic is considered archived if it has a non-null `usage_key` field.

    Args:
    - filtered_topic_ids (List[str]): A list of topic ids to filter on.
    - topics (List[Dict[str, str]]): A list of topic dictionaries.
    - Each dictionary should have a 'id' and a 'usage_key' field.

    Returns:
    - A list of archived topic dictionaries, with the same format as the input topics.
    """
    archived_topics = []
    for topic_id in filtered_topic_ids:
        for topic in topics:
            if topic['id'] == topic_id and topic['usage_key'] is not None:
                archived_topics.append(topic)
    return archived_topics


def is_posting_allowed(posting_restrictions: str, blackout_schedules: List):  # noqa: UP006
    """
    Check if posting is allowed based on the given posting restrictions and blackout schedules.

    Args:
        posting_restrictions (str): Values would be  "disabled", "scheduled" or "enabled".
        blackout_schedules (List[Dict[str, datetime]]): The list of blackout schedules

    Returns:
        bool: True if posting is allowed, False otherwise.
    """
    now = datetime.now(UTC)
    if posting_restrictions == PostingRestriction.DISABLED:
        return True
    elif posting_restrictions == PostingRestriction.SCHEDULED:
        return not any(schedule["start"] <= now <= schedule["end"] for schedule in blackout_schedules)
    else:
        return False


def can_user_notify_all_learners(user_roles, is_course_staff, is_course_admin):
    """
    Check if user posting is allowed to notify all learners based on the given restrictions

    Args:
        user_roles (Dict): Roles of the posting user
        is_course_staff (Boolean): Whether the user has a course staff access.
        is_course_admin (Boolean): Whether the user has a course admin access.

    Returns:
        bool: True if posting for all learner is allowed to this user, False otherwise.
    """
    is_staff_or_instructor = any([
        user_roles.intersection({FORUM_ROLE_ADMINISTRATOR, FORUM_ROLE_MODERATOR}),
        is_course_staff,
        is_course_admin,
    ])

    return is_staff_or_instructor


def is_captcha_enabled(course_id) -> bool:
    """
    Check if reCAPTCHA is enabled for discussion posts in the given course.
    """
    return bool(ENABLE_CAPTCHA_IN_DISCUSSION.is_enabled(course_id) and settings.RECAPTCHA_PRIVATE_KEY)


def get_course_id_from_thread_id(thread_id: str) -> str:
    """
    Get course id from thread id.
    """
    thread = Thread(id=thread_id).retrieve(**{
        'with_responses': False,
        'mark_as_read': False
    })
    return thread["course_id"]


def is_only_student(course_key, user) -> bool:
    """
        Check if the user is only a user and doesn't hold any other roles the given course.
    """
    is_course_staff_or_admin = (CourseAccessRole.objects.filter
                                (user=user,
                                 course_id__in=[course_key],
                                 role__in=["instructor", "staff", "limited_staff"]
                                 ).exists())
    is_user_admin = user.is_staff
    user_roles = get_user_role_names(user, course_key)
    return user_roles == {FORUM_ROLE_STUDENT} and not (is_course_staff_or_admin or is_user_admin)


def verify_recaptcha_token(token: str) -> bool:
    """
    Assess the reCAPTCHA token using Google reCAPTCHA Enterprise API.
    Logs success or error and returns True if an error occurs, along with logging the error.
    """
    try:
        site_key = get_captcha_site_key_by_platform(get_platform_from_request())
        url = (f"https://recaptchaenterprise.googleapis.com/v1/projects/{settings.RECAPTCHA_PROJECT_ID}/assessments"
               f"?key={settings.RECAPTCHA_PRIVATE_KEY}")
        data = {
            "event": {
                "token": token,
                "siteKey": site_key,
            }
        }

        response = requests.post(url, json=data, timeout=10).json()

        if response.get('tokenProperties', {}).get('valid'):
            logging.info("reCAPTCHA token assessment successful. Token is valid.")
            return True
        elif response.get('error'):
            logging.error(f"reCAPTCHA token assessment failed: {response['error']}.")
            return True
        else:
            logging.error(f"reCAPTCHA token assessment failed: Invalid token.{response}.")
            return False
    except requests.exceptions.RequestException as e:
        logging.error(f"Network or API error during reCAPTCHA assessment: {e}")
        return True
    except KeyError as e:
        logging.error(f"Unexpected response format from reCAPTCHA API. Missing key: {e}. Full response: {response}")
        return True
    except Exception as e:  # pylint: disable=broad-except
        logging.error(f"An unexpected error occurred during reCAPTCHA assessment: {e}", exc_info=True)
        return True


def get_platform_from_request():
    """
    get Mobile-Platform-Identifier header value from request
    """
    return get_current_request().headers.get('Mobile-Platform-Identifier', 'web')


def get_captcha_site_key_by_platform(platform: str) -> str | None:
    """
     Get reCAPTCHA site key based on the platform.
    """
    return settings.RECAPTCHA_SITE_KEYS.get(platform, None)


def send_signal_after_commit(signal_func: Callable):
    """
    Schedule a signal to be sent after the current database transaction commits.

    This helper ensures that signals are only sent after the transaction commits,
    preventing race conditions where async tasks (like Celery workers) may try to
    access database records before they are visible (especially important for MySQL
    backend with transaction isolation).

    Args:
        signal_func: A callable that sends the signal. This will be executed
                     after the transaction commits.

    Example:
        send_signal_after_commit(
            lambda: thread_created.send(sender=None, user=user, post=thread, notify_all_learners=False)
        )
    """
    transaction.on_commit(signal_func)
