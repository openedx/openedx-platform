"""
Module for checking permissions with the comment_client backend
"""


import logging

from edx_django_utils.cache import DEFAULT_REQUEST_CACHE
from opaque_keys.edx.keys import CourseKey

from lms.djangoapps.teams.models import CourseTeam
from openedx.core.djangoapps.django_comment_common.comment_client import Thread
from openedx.core.djangoapps.django_comment_common.models import (
    CourseDiscussionSettings,
    has_permission
)
from openedx.core.lib.cache_utils import request_cached


CONDITIONS = ['is_open', 'is_author', 'is_question_author', 'is_team_member_if_applicable']


@request_cached()
def get_team(commentable_id):
    """ Returns the team that the commentable_id belongs to if it exists. Returns None otherwise. """
    try:
        team = CourseTeam.objects.get(discussion_topic_id=commentable_id)
    except CourseTeam.DoesNotExist:
        team = None

    return team


def _check_condition(user, condition, content):
    """ Check whether or not the given condition applies for the given user and content. """

    def check_open(_user, content):
        """ Check whether the content is open. """
        try:
            return content and not content['closed']
        except KeyError:
            return False

    def check_author(user, content):
        """ Check if the given user is the author of the content. """
        try:
            return content and content['user_id'] == str(user.id)
        except KeyError:
            return False

    def check_question_author(user, content):
        """ Check if the given user is the author of the original question for both threads and comments. """
        if not content:
            return False
        try:
            request_cache_dict = DEFAULT_REQUEST_CACHE.data
            if content["type"] == "thread":
                cache_key = "django_comment_client.permissions._check_condition.check_question_author.{}.{}".format(
                    user.id, content['id']
                )
                if cache_key in request_cache_dict:
                    return request_cache_dict[cache_key]
                else:
                    result = content["thread_type"] == "question" and content["user_id"] == str(user.id)
                    request_cache_dict[cache_key] = result
                    return result
            else:
                cache_key = "django_comment_client.permissions._check_condition.check_question_author.{}.{}".format(
                    user.id, content['thread_id']
                )
                if cache_key in request_cache_dict:
                    return request_cache_dict[cache_key]
                else:
                    # make the now-unavoidable comments service query
                    thread = Thread(id=content['thread_id']).to_dict()
                    return check_question_author(user, thread)
        except KeyError:
            return False

    def check_team_member(user, content):
        """
        If the content has a commentable_id, verifies that either it is not associated with a team,
        or if it is, that the user is a member of that team.
        """
        if not content:
            return False
        try:
            commentable_id = content['commentable_id']
            request_cache_dict = DEFAULT_REQUEST_CACHE.data
            cache_key = f"django_comment_client.check_team_member.{user.id}.{commentable_id}"
            if cache_key in request_cache_dict:
                return request_cache_dict[cache_key]
            team = get_team(commentable_id)
            if team is None:
                passes_condition = True
            else:
                passes_condition = team.users.filter(id=user.id).exists()
            request_cache_dict[cache_key] = passes_condition
        except KeyError:
            # We do not expect KeyError in production-- it usually indicates an improper test mock.
            logging.warning("Did not find key commentable_id in content.")
            passes_condition = False
        return passes_condition

    handlers = {
        'is_open': check_open,
        'is_author': check_author,
        'is_question_author': check_question_author,
        'is_team_member_if_applicable': check_team_member
    }

    return handlers[condition](user, content)


def _check_conditions_permissions(user, permissions, course_id, content, user_group_id=None, content_user_group=None):
    """
    Accepts a list of permissions and proceed if any of the permission is valid.
    Note that ["can_view", "can_edit"] will proceed if the user has either
    "can_view" or "can_edit" permission. To use AND operator in between, wrap them in
    a list.
    """

    def test(user, per, operator="or"):
        if isinstance(per, str):
            if per in CONDITIONS:
                return _check_condition(user, per, content)
            if 'group_' in per:
                # If a course does not have divided discussions
                # or a course has divided discussions, but the current user's content group does not equal
                # the content group of the commenter/poster,
                # then the current user does not have group edit permissions.
                division_scheme = CourseDiscussionSettings.get(course_id).division_scheme
                if (division_scheme is CourseDiscussionSettings.NONE
                        or user_group_id is None
                        or content_user_group is None
                        or user_group_id != content_user_group):
                    return False
            return has_permission(user, per, course_id=course_id)
        elif isinstance(per, list) and operator in ["and", "or"]:
            results = [test(user, x, operator="and") for x in per]
            if operator == "or":
                return True in results
            elif operator == "and":
                return False not in results

    return test(user, permissions, operator="or")


# Note: 'edit_content' is being used as a generic way of telling if someone is a privileged user
# (forum Moderator/Admin/TA), because there is a desire that team membership does not impact privileged users.
VIEW_PERMISSIONS = {
    'update_thread': ['group_edit_content', 'edit_content', ['update_thread', 'is_open', 'is_author']],
    'create_comment': ['group_edit_content', 'edit_content', ["create_comment", "is_open",
                                                              "is_team_member_if_applicable"]],
    'delete_thread': ['group_delete_thread', 'delete_thread', ['update_thread', 'is_author']],
    'update_comment': ['group_edit_content', 'edit_content', ['update_comment', 'is_open', 'is_author']],
    'endorse_comment': ['endorse_comment', 'is_question_author'],
    'openclose_thread': ['group_openclose_thread', 'openclose_thread'],
    'create_sub_comment': ['group_edit_content', 'edit_content', ['create_sub_comment', 'is_open',
                                                                  'is_team_member_if_applicable']],
    'delete_comment': ['group_delete_comment', 'delete_comment', ['update_comment', 'is_open', 'is_author']],
    'vote_for_comment': ['group_edit_content', 'edit_content', ['vote', 'is_open', 'is_team_member_if_applicable']],
    'undo_vote_for_comment': ['group_edit_content', 'edit_content', ['unvote', 'is_open',
                                                                     'is_team_member_if_applicable']],
    'vote_for_thread': ['group_edit_content', 'edit_content', ['vote', 'is_open', 'is_team_member_if_applicable']],
    'flag_abuse_for_thread': ['group_edit_content', 'edit_content', ['vote', 'is_team_member_if_applicable']],
    'un_flag_abuse_for_thread': ['group_edit_content', 'edit_content', ['vote', 'is_team_member_if_applicable']],
    'flag_abuse_for_comment': ['group_edit_content', 'edit_content', ['vote', 'is_team_member_if_applicable']],
    'un_flag_abuse_for_comment': ['group_edit_content', 'edit_content', ['vote', 'is_team_member_if_applicable']],
    'undo_vote_for_thread': ['group_edit_content', 'edit_content', ['unvote', 'is_open',
                                                                    'is_team_member_if_applicable']],
    'pin_thread': ['group_openclose_thread', 'openclose_thread'],
    'un_pin_thread': ['group_openclose_thread', 'openclose_thread'],
    'follow_thread': ['group_edit_content', 'edit_content', ['follow_thread', 'is_team_member_if_applicable']],
    'follow_commentable': ['group_edit_content', 'edit_content', ['follow_commentable',
                                                                  'is_team_member_if_applicable']],
    'unfollow_thread': ['group_edit_content', 'edit_content', ['unfollow_thread', 'is_team_member_if_applicable']],
    'unfollow_commentable': ['group_edit_content', 'edit_content', ['unfollow_commentable',
                                                                    'is_team_member_if_applicable']],
    'create_thread': ['group_edit_content', 'edit_content', ['create_thread', 'is_team_member_if_applicable']],
}


def check_permissions_by_view(user, course_id, content, name, group_id=None, content_user_group=None):
    assert isinstance(course_id, CourseKey)
    p = VIEW_PERMISSIONS.get(name)
    return _check_conditions_permissions(user, p, course_id, content, group_id, content_user_group)
