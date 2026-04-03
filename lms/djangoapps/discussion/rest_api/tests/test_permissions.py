"""
Tests for discussion API permission logic
"""


import itertools

import ddt

from lms.djangoapps.discussion.rest_api.permissions import (
    can_delete,
    get_editable_fields,
    get_initializable_comment_fields,
    get_initializable_thread_fields,
)
from openedx.core.djangoapps.django_comment_common.comment_client.comment import Comment
from openedx.core.djangoapps.django_comment_common.comment_client.thread import Thread
from openedx.core.djangoapps.django_comment_common.comment_client.user import User
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


def _get_context(
    requester_id,
    is_cohorted=False,
    thread=None,
    allow_anonymous=True,
    allow_anonymous_to_peers=False,
    has_moderation_privilege=False,
    is_staff_or_admin=False,
):
    """Return a context suitable for testing the permissions module"""
    return {
        "cc_requester": User(id=requester_id),
        "course": CourseFactory(
            cohort_config={"cohorted": is_cohorted},
            allow_anonymous=allow_anonymous,
            allow_anonymous_to_peers=allow_anonymous_to_peers,
        ),
        "discussion_division_enabled": is_cohorted,
        "thread": thread,
        "has_moderation_privilege": has_moderation_privilege,
        "is_staff_or_admin": is_staff_or_admin,
    }


@ddt.ddt
class GetInitializableFieldsTest(ModuleStoreTestCase):
    """Tests for get_*_initializable_fields"""
    @ddt.data(*itertools.product(*[[True, False] for _ in range(4)]))
    @ddt.unpack
    def test_thread(
        self,
        is_privileged,
        is_cohorted,
        allow_anonymous,
        allow_anonymous_to_peers,
    ):
        context = _get_context(
            requester_id="5",
            has_moderation_privilege=is_privileged,
            is_cohorted=is_cohorted,
            allow_anonymous=allow_anonymous,
            allow_anonymous_to_peers=allow_anonymous_to_peers,
        )
        actual = get_initializable_thread_fields(context)
        expected = {
            "abuse_flagged", "copy_link", "course_id", "following", "raw_body",
            "read", "title", "topic_id", "type"
        }
        if is_privileged:
            expected |= {"closed", "pinned", "close_reason_code", "voted"}
        if is_privileged and is_cohorted:
            expected |= {"group_id"}
        if allow_anonymous:
            expected |= {"anonymous"}
        if allow_anonymous_to_peers:
            expected |= {"anonymous_to_peers"}
        assert actual == expected

    @ddt.data(*itertools.product([True, False], ["question", "discussion"], [True, False]))
    @ddt.unpack
    def test_comment(self, is_thread_author, thread_type, is_privileged):
        context = _get_context(
            requester_id="5",
            has_moderation_privilege=is_privileged,
            thread=Thread(user_id="5" if is_thread_author else "6", thread_type=thread_type)
        )
        actual = get_initializable_comment_fields(context)
        expected = {
            "anonymous", "abuse_flagged", "parent_id", "raw_body", "thread_id"
        }
        if is_privileged:
            expected |= {"voted"}
        if (is_thread_author and thread_type == "question") or is_privileged:
            expected |= {"endorsed"}
        assert actual == expected


@ddt.ddt
class GetEditableFieldsTest(ModuleStoreTestCase):
    """Tests for get_editable_fields"""
    @ddt.data(*itertools.product(*[[True, False] for _ in range(6)]))
    @ddt.unpack
    def test_thread(
        self,
        is_author,
        is_cohorted,
        allow_anonymous,
        allow_anonymous_to_peers,
        has_moderation_privilege,
        is_staff_or_admin,
    ):
        thread = Thread(user_id="5" if is_author else "6", type="thread")
        context = _get_context(
            requester_id="5",
            is_cohorted=is_cohorted,
            allow_anonymous=allow_anonymous,
            allow_anonymous_to_peers=allow_anonymous_to_peers,
            has_moderation_privilege=has_moderation_privilege,
            is_staff_or_admin=is_staff_or_admin,
        )
        actual = get_editable_fields(thread, context)
        expected = {"abuse_flagged", "copy_link", "following", "read"}
        if has_moderation_privilege:
            expected |= {"closed", "close_reason_code"}
        if has_moderation_privilege or is_staff_or_admin:
            expected |= {"pinned"}
        if has_moderation_privilege or not is_author or is_staff_or_admin:
            expected |= {"voted"}
        if has_moderation_privilege and not is_author:
            expected |= {"edit_reason_code"}
        if is_author or has_moderation_privilege:
            expected |= {"raw_body", "topic_id", "type", "title"}
        if has_moderation_privilege and is_cohorted:
            expected |= {"group_id"}
        if is_author and allow_anonymous:
            expected |= {"anonymous"}
        if is_author and allow_anonymous_to_peers:
            expected |= {"anonymous_to_peers"}

        assert actual == expected

    @ddt.data(*itertools.product(*[[True, False] for _ in range(6)], ["question", "discussion"]))
    @ddt.unpack
    def test_comment(
        self,
        is_author,
        is_thread_author,
        allow_anonymous,
        allow_anonymous_to_peers,
        has_parent,
        has_moderation_privilege,
        thread_type,
    ):
        comment = Comment(
            user_id="5" if is_author else "6",
            type="comment",
            parent_id="parent-id" if has_parent else None,
        )
        context = _get_context(
            requester_id="5",
            thread=Thread(user_id="5" if is_thread_author else "6", thread_type=thread_type),
            allow_anonymous=allow_anonymous,
            allow_anonymous_to_peers=allow_anonymous_to_peers,
            has_moderation_privilege=has_moderation_privilege,
        )
        actual = get_editable_fields(comment, context)
        expected = {"abuse_flagged"}
        if has_moderation_privilege or not is_author:
            expected |= {"voted"}
        if has_moderation_privilege and not is_author:
            expected |= {"edit_reason_code"}
        if is_author or has_moderation_privilege:
            expected |= {"raw_body"}
        if not has_parent and ((is_thread_author and thread_type == "question") or has_moderation_privilege):
            expected |= {"endorsed"}
        if is_author and allow_anonymous:
            expected |= {"anonymous"}
        if is_author and allow_anonymous_to_peers:
            expected |= {"anonymous_to_peers"}
        assert actual == expected


@ddt.ddt
class CanDeleteTest(ModuleStoreTestCase):
    """Tests for can_delete"""
    @ddt.data(*itertools.product([True, False], [True, False]))
    @ddt.unpack
    def test_thread(self, is_author, is_privileged):
        thread = Thread(user_id="5" if is_author else "6")
        context = _get_context(requester_id="5", has_moderation_privilege=is_privileged)
        assert can_delete(thread, context) == (is_author or is_privileged)

    @ddt.data(*itertools.product([True, False], [True, False], [True, False]))
    @ddt.unpack
    def test_comment(self, is_author, is_thread_author, is_privileged):
        comment = Comment(user_id="5" if is_author else "6")
        context = _get_context(
            requester_id="5",
            has_moderation_privilege=is_privileged,
            thread=Thread(user_id="5" if is_thread_author else "6")
        )
        assert can_delete(comment, context) == (is_author or is_privileged)
