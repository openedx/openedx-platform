# pylint: disable=missing-docstring
"""Provides base Commentable model class"""
from __future__ import annotations

from typing import Dict, Optional

from edx_django_utils.monitoring import function_trace
from forum import api as forum_api
from opaque_keys.edx.keys import CourseKey


def get_course_commentable_counts(course_key: CourseKey) -> Dict[str, Dict[str, int]]:
    """
    Get stats about the count of different types of threads for each commentable (topic).

    Args:
        course_key (str|CourseKey): course key for which stats are needed.

    Returns:
        A mapping of topic ids to the number of question and discussion type posts in them.

        e.g.
            {
                "general": { "discussion": 22, "question": 15 },
                "topic-1": { "discussion": 2, "question": 1 },
                ...
            }

    """
    commentable_stats = forum_api.get_commentables_stats(str(course_key))
    return commentable_stats


@function_trace("get_course_user_stats")
def get_course_user_stats(course_key: CourseKey, params: Optional[Dict] = None) -> Dict[str, Dict[str, int]]:
    """
    Get stats about a user's participation in a course.

    Args:
        course_key (str|CourseKey): course key for which stats are needed.
        params (Optional[Dict]): pagination and sorting query parameters.

    Returns:
        A mapping of user ids to stats about the user.

        e.g.
            {
                "user_stats" [
                    {
                        "active_flags": 2,
                        "inactive_flags": 0,
                        "replies": 3,
                        "responses": 2,
                        "threads": 7,
                        "username": "edx"
                    },
                    ...
                ],
                "num_pages": 12,
                "page": 3,
                "count": 124
                ...
            }

    """
    if params is None:
        params = {}
    course_stats = forum_api.get_user_course_stats(str(course_key), **params)
    return course_stats


@function_trace("update_course_users_stats")
def update_course_users_stats(course_key: CourseKey) -> Dict:
    """
    Update the user stats for all users for a particular course.

    Args:
        course_key (str|CourseKey): course key for which stats are needed.

    Returns:
        dict: data returned by API. Contains count of users updated.
    """
    course_stats = forum_api.update_users_in_course(str(course_key))
    return course_stats
