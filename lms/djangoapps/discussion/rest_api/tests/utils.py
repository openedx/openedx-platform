"""
Discussion API test utilities
"""


import hashlib
import json
from contextlib import closing
from datetime import datetime

from PIL import Image
from pytz import UTC

from lms.djangoapps.discussion.django_comment_client.tests.mixins import MockForumApiMixin
from openedx.core.djangoapps.django_comment_common.comment_client.utils import CommentClientRequestError
from openedx.core.djangoapps.profile_images.images import create_profile_images
from openedx.core.djangoapps.profile_images.tests.helpers import make_image_file
from openedx.core.djangoapps.user_api.accounts.image_helpers import get_profile_image_names, set_has_profile_image


def make_thread_callback(thread_data):
    """
    Returns a function that simulates thread creation/update behavior,
    applying overrides based on keyword arguments (e.g., mock request body).
    """

    def callback(*args, **kwargs):
        # Simulate default thread response
        response_data = make_minimal_cs_thread(thread_data)
        original_data = response_data.copy()

        for key, val in kwargs.items():
            if key in ["anonymous", "anonymous_to_peers", "closed", "pinned"]:
                response_data[key] = val is True or val == "True"
            elif key == "edit_reason_code":
                response_data["edit_history"] = [{
                    "original_body": original_data["body"],
                    "author": thread_data.get("username"),
                    "reason_code": val,
                }]
            else:
                response_data[key] = val

        return response_data

    return callback


def make_comment_callback(comment_data, thread_id, parent_id):
    """
    Returns a callable that mimics comment creation or update behavior,
    applying overrides based on keyword arguments like a parsed request body.
    """

    def callback(*args, **kwargs):
        response_data = make_minimal_cs_comment(comment_data)
        original_data = response_data.copy()

        # Inject thread_id and parent_id
        response_data["thread_id"] = thread_id
        response_data["parent_id"] = parent_id

        # Override fields based on "incoming request"
        for key, val in kwargs.items():
            if key in ["anonymous", "anonymous_to_peers", "endorsed"]:
                response_data[key] = val is True or val == "True"
            elif key == "edit_reason_code":
                response_data["edit_history"] = [{
                    "original_body": original_data["body"],
                    "author": comment_data.get("username"),
                    "reason_code": val,
                }]
            else:
                response_data[key] = val

        return response_data

    return callback


def make_user_callbacks(user_map):
    """
    Returns a callable that mimics user creation.
    """
    def callback(*args, **kwargs):
        user_id = args[0] if args else kwargs.get('user_id')
        return user_map[str(user_id)]
    return callback


class ForumMockUtilsMixin(MockForumApiMixin):
    """Mixin with utility methods for mocking the comments service"""

    def register_get_threads_response(self, threads, page, num_pages):
        """Register a mock response for GET on the CS thread list endpoint"""
        self.set_mock_return_value('get_user_threads', {
            "collection": threads,
            "page": page,
            "num_pages": num_pages,
            "thread_count": len(threads),
        })

    def register_get_course_commentable_counts_response(self, course_id, thread_counts):
        """Register a mock response for GET on the CS thread list endpoint"""
        self.set_mock_return_value('get_commentables_stats', thread_counts)

    def register_get_threads_search_response(self, threads, rewrite, num_pages=1):
        """Register a mock response for GET on the CS thread search endpoint"""
        self.set_mock_return_value('search_threads', {
            "collection": threads,
            "page": 1,
            "num_pages": num_pages,
            "corrected_text": rewrite,
            "thread_count": len(threads),
        })

    def register_post_thread_response(self, thread_data):
        self.set_mock_side_effect('create_thread', make_thread_callback(thread_data))

    def register_put_thread_response(self, thread_data):
        self.set_mock_side_effect('update_thread', make_thread_callback(thread_data))

    def register_get_thread_error_response(self, thread_id, status_code):
        self.set_mock_side_effect(
            'get_thread',
            CommentClientRequestError(f"Thread does not exist with Id: {thread_id}")
        )

    def register_get_thread_response(self, thread):
        self.set_mock_return_value('get_thread', thread)

    def register_get_comments_response(self, comments, page, num_pages):
        """Register a mock response for get_user_comments API call."""
        self.set_mock_return_value('get_user_comments', {
            "collection": comments,
            "page": page,
            "num_pages": num_pages,
            "comment_count": len(comments),
        })

    def register_post_comment_response(self, comment_data, thread_id, parent_id=None):
        self.set_mock_side_effect(
            'create_child_comment' if parent_id else 'create_parent_comment',
            make_comment_callback(comment_data, thread_id, parent_id)
        )

    def register_put_comment_response(self, comment_data):
        thread_id = comment_data["thread_id"]
        parent_id = comment_data.get("parent_id")
        self.set_mock_side_effect(
            'update_comment',
            make_comment_callback(comment_data, thread_id, parent_id)
        )

    def register_get_comment_error_response(self, comment_id, status_code):
        self.set_mock_side_effect(
            'get_parent_comment',
            CommentClientRequestError(f"Comment does not exist with Id: {comment_id}")
        )

    def register_get_comment_response(self, response_overrides):
        comment = make_minimal_cs_comment(response_overrides)
        self.set_mock_return_value('get_parent_comment', comment)

    def register_get_user_response(self, user, subscribed_thread_ids=None, upvoted_ids=None):
        """Register a mock response for GET on the CS user endpoint"""
        self.users_map[str(user.id)] = {
            "id": str(user.id),
            "subscribed_thread_ids": subscribed_thread_ids or [],
            "upvoted_ids": upvoted_ids or [],
        }
        self.set_mock_side_effect('get_user', make_user_callbacks(self.users_map))

    def register_get_user_retire_response(self, user, status=200, body=""):
        self.set_mock_return_value('retire_user', body)

    def register_get_username_replacement_response(self, user, status=200, body=""):
        self.set_mock_return_value('update_username', body)

    def register_subscribed_threads_response(self, user, threads, page, num_pages):
        """Register a mock response for get_user_threads and get_user_subscriptions API calls."""
        self.set_mock_return_value('get_user_threads', {
            "collection": threads,
            "page": page,
            "num_pages": num_pages,
            "thread_count": len(threads),
        })
        # Also mock get_user_subscriptions for the Forum v2 API
        self.set_mock_return_value('get_user_subscriptions', {
            "collection": threads,
            "page": page,
            "num_pages": num_pages,
            "thread_count": len(threads),
        })

    def register_course_stats_response(self, course_key, stats, page, num_pages):
        self.set_mock_return_value('get_user_course_stats', {
            "user_stats": stats,
            "page": page,
            "num_pages": num_pages,
            "count": len(stats),
        })

    def register_subscription_response(self, user):
        self.set_mock_return_value('create_subscription', {})
        self.set_mock_return_value('delete_subscription', {})

    def register_thread_votes_response(self, thread_id):
        self.set_mock_return_value('update_thread_votes', {})
        self.set_mock_return_value('delete_thread_vote', {})

    def register_comment_votes_response(self, comment_id):
        self.set_mock_return_value('update_comment_votes', {})
        self.set_mock_return_value('delete_comment_vote', {})

    def register_flag_response(self, content_type, content_id):
        if content_type == 'thread':
            self.set_mock_return_value('update_thread_flag', {})
        elif content_type == 'comment':
            self.set_mock_return_value('update_comment_flag', {})

    def register_read_response(self, user, content_type, content_id):
        self.set_mock_return_value('mark_thread_as_read', {})

    def register_delete_thread_response(self, thread_id):
        self.set_mock_return_value('delete_thread', {})

    def register_delete_comment_response(self, comment_id):
        self.set_mock_return_value('delete_comment', {})

    def register_user_active_threads(self, user_id, response):
        self.set_mock_return_value('get_user_active_threads', response)

    def register_get_subscriptions(self, thread_id, response):
        self.set_mock_return_value('get_thread_subscriptions', response)

    def register_thread_flag_response(self, thread_id):
        """Register a mock response for PUT on the CS thread flag endpoints"""
        self.register_flag_response("thread", thread_id)

    def register_comment_flag_response(self, comment_id):
        """Register a mock response for PUT on the CS comment flag endpoints"""
        self.register_flag_response("comment", comment_id)

    def request_patch(self, request_data):
        """
        make a request to PATCH endpoint and return response
        """
        return self.client.patch(
            self.url,
            json.dumps(request_data),
            content_type="application/merge-patch+json"
        )

    def expected_thread_data(self, overrides=None):
        """
        Returns expected thread data in API response
        """
        response_data = {
            "anonymous": False,
            "anonymous_to_peers": False,
            "author": self.user.username,
            "author_label": None,
            "created_at": "1970-01-01T00:00:00Z",
            "updated_at": "1970-01-01T00:00:00Z",
            "raw_body": "Test body",
            "rendered_body": "<p>Test body</p>",
            "preview_body": "Test body",
            "abuse_flagged": False,
            "abuse_flagged_count": None,
            "voted": False,
            "vote_count": 0,
            "editable_fields": [
                "abuse_flagged",
                "anonymous",
                "copy_link",
                "following",
                "raw_body",
                "read",
                "title",
                "topic_id",
                "type",
            ],
            "course_id": str(self.course.id),
            "topic_id": "test_topic",
            "group_id": None,
            "group_name": None,
            "title": "Test Title",
            "pinned": False,
            "closed": False,
            "can_delete": True,
            "following": False,
            "comment_count": 1,
            "unread_comment_count": 0,
            "comment_list_url": "http://testserver/api/discussion/v1/comments/?thread_id=test_thread",
            "endorsed_comment_list_url": None,
            "non_endorsed_comment_list_url": None,
            "read": False,
            "has_endorsed": False,
            "id": "test_thread",
            "type": "discussion",
            "response_count": 0,
            "last_edit": None,
            "edit_by_label": None,
            "closed_by": None,
            "closed_by_label": None,
            "close_reason": None,
            "close_reason_code": None,
        }
        response_data.update(overrides or {})
        return response_data


def make_minimal_cs_thread(overrides=None):
    """
    Create a dictionary containing all needed thread fields as returned by the
    comments service with dummy data and optional overrides
    """
    ret = {
        "type": "thread",
        "id": "dummy",
        "course_id": "course-v1:dummy+dummy+dummy",
        "commentable_id": "dummy",
        "group_id": None,
        "user_id": "0",
        "username": "dummy",
        "anonymous": False,
        "anonymous_to_peers": False,
        "created_at": "1970-01-01T00:00:00Z",
        "updated_at": "1970-01-01T00:00:00Z",
        "last_activity_at": "1970-01-01T00:00:00Z",
        "thread_type": "discussion",
        "title": "dummy",
        "body": "dummy",
        "pinned": False,
        "closed": False,
        "abuse_flaggers": [],
        "abuse_flagged_count": None,
        "votes": {"up_count": 0},
        "comments_count": 0,
        "unread_comments_count": 0,
        "children": [],
        "read": False,
        "endorsed": False,
        "resp_total": 0,
        "closed_by": None,
        "close_reason_code": None,
    }
    ret.update(overrides or {})
    return ret


def make_minimal_cs_comment(overrides=None):
    """
    Create a dictionary containing all needed comment fields as returned by the
    comments service with dummy data and optional overrides
    """
    ret = {
        "type": "comment",
        "id": "dummy",
        "commentable_id": "dummy",
        "thread_id": "dummy",
        "parent_id": None,
        "user_id": "0",
        "username": "dummy",
        "anonymous": False,
        "anonymous_to_peers": False,
        "created_at": "1970-01-01T00:00:00Z",
        "updated_at": "1970-01-01T00:00:00Z",
        "body": "dummy",
        "abuse_flaggers": [],
        "votes": {"up_count": 0},
        "endorsed": False,
        "child_count": 0,
        "children": [],
    }
    ret.update(overrides or {})
    return ret


def make_paginated_api_response(results=None, count=0, num_pages=0, next_link=None, previous_link=None):
    """
    Generates the response dictionary of paginated APIs with passed data
    """
    return {
        "pagination": {
            "next": next_link,
            "previous": previous_link,
            "count": count,
            "num_pages": num_pages,
        },
        "results": results or []
    }


class ProfileImageTestMixin:
    """
    Mixin with utility methods for user profile image
    """

    TEST_PROFILE_IMAGE_UPLOADED_AT = datetime(2002, 1, 9, 15, 43, 1, tzinfo=UTC)

    def create_profile_image(self, user, storage):
        """
        Creates profile image for user and checks that created image exists in storage
        """
        with make_image_file() as image_file:
            create_profile_images(image_file, get_profile_image_names(user.username))
            self.check_images(user, storage)
            set_has_profile_image(user.username, True, self.TEST_PROFILE_IMAGE_UPLOADED_AT)

    def check_images(self, user, storage, exist=True):
        """
        If exist is True, make sure the images physically exist in storage
        with correct sizes and formats.

        If exist is False, make sure none of the images exist.
        """
        for size, name in get_profile_image_names(user.username).items():
            if exist:
                assert storage.exists(name)
                with closing(Image.open(storage.path(name))) as img:
                    assert img.size == (size, size)
                    assert img.format == 'JPEG'
            else:
                assert not storage.exists(name)

    def get_expected_user_profile(self, username):
        """
        Returns the expected user profile data for a given username
        """
        url = 'http://example-storage.com/profile-images/{filename}_{{size}}.jpg?v={timestamp}'.format(
            filename=hashlib.md5(b'secret' + username.encode('utf-8')).hexdigest(),
            timestamp=self.TEST_PROFILE_IMAGE_UPLOADED_AT.strftime("%s")
        )
        return {
            'profile': {
                'image': {
                    'has_image': True,
                    'image_url_full': url.format(size=500),
                    'image_url_large': url.format(size=120),
                    'image_url_medium': url.format(size=50),
                    'image_url_small': url.format(size=30),
                }
            }
        }


class ThreadMock(object):  # noqa: UP004
    """
    A mock thread object
    """

    def __init__(self, thread_id, creator, title, parent_id=None, body='', commentable_id=None):
        self.id = thread_id
        self.user_id = str(creator.id)
        self.username = creator.username
        self.title = title
        self.parent_id = parent_id
        self.body = body
        self.commentable_id = commentable_id

    def url_with_id(self, params):
        return f"http://example.com/{params['id']}"
