"""
Tests for Discussion API forms
"""


import itertools
from unittest import TestCase
from urllib.parse import urlencode

import ddt
from django.http import QueryDict
from opaque_keys.edx.locator import CourseLocator

from lms.djangoapps.discussion.rest_api.forms import CommentListGetForm, ThreadListGetForm, UserCommentListGetForm
from openedx.core.djangoapps.util.test_forms import FormTestMixin


class PaginationTestMixin:
    """A mixin for testing forms with pagination fields"""

    def test_missing_page(self):
        self.form_data.pop("page")
        self.assert_field_value("page", 1)

    def test_invalid_page(self):
        self.form_data["page"] = "0"
        self.assert_error("page", "Ensure this value is greater than or equal to 1.")

    def test_missing_page_size(self):
        self.form_data.pop("page_size")
        self.assert_field_value("page_size", 10)

    def test_zero_page_size(self):
        self.form_data["page_size"] = "0"
        self.assert_error("page_size", "Ensure this value is greater than or equal to 1.")

    def test_excessive_page_size(self):
        self.form_data["page_size"] = "101"
        self.assert_field_value("page_size", 100)


@ddt.ddt
class ThreadListGetFormTest(FormTestMixin, PaginationTestMixin, TestCase):
    """Tests for ThreadListGetForm"""
    FORM_CLASS = ThreadListGetForm

    def setUp(self):
        super().setUp()
        self.form_data = QueryDict(
            urlencode(
                {
                    "course_id": "Foo/Bar/Baz",
                    "page": "2",
                    "page_size": "13",
                }
            ),
            mutable=True
        )

    def test_basic(self):
        form = self.get_form(expected_valid=True)
        assert form.cleaned_data == {
            'course_id': CourseLocator.from_string('Foo/Bar/Baz'),
            'page': 2,
            'page_size': 13,
            'count_flagged': None,
            'topic_id': set(),
            'text_search': '',
            'following': None,
            'author': '',
            'thread_type': '',
            'flagged': None,
            'view': '',
            'order_by': 'last_activity_at',
            'order_direction': 'desc',
            'requested_fields': set()
        }

    def test_topic_id(self):
        self.form_data.setlist("topic_id", ["example topic_id", "example 2nd topic_id"])
        form = self.get_form(expected_valid=True)
        assert form.cleaned_data['topic_id'] == {'example topic_id', 'example 2nd topic_id'}

    def test_text_search(self):
        self.form_data["text_search"] = "test search string"
        form = self.get_form(expected_valid=True)
        assert form.cleaned_data['text_search'] == 'test search string'

    def test_missing_course_id(self):
        self.form_data.pop("course_id")
        self.assert_error("course_id", "This field is required.")

    def test_invalid_course_id(self):
        self.form_data["course_id"] = "invalid course id"
        self.assert_error("course_id", "'invalid course id' is not a valid course id")

    def test_empty_topic_id(self):
        self.form_data.setlist("topic_id", ["", "not empty"])
        self.assert_error("topic_id", "This field cannot be empty.")

    @ddt.data("discussion", "question")
    def test_thread_type(self, value):
        self.form_data["thread_type"] = value
        self.assert_field_value("thread_type", value)

    def test_thread_type_invalid(self):
        self.form_data["thread_type"] = "invalid-option"
        self.assert_error("thread_type", "Select a valid choice. invalid-option is not one of the available choices.")

    @ddt.data("True", "true", 1, True)
    def test_flagged_true(self, value):
        self.form_data["flagged"] = value
        self.assert_field_value("flagged", True)

    @ddt.data("False", "false", 0, False)
    def test_flagged_false(self, value):
        self.form_data["flagged"] = value
        self.assert_field_value("flagged", False)

    def test_invalid_flagged(self):
        self.form_data["flagged"] = "invalid-boolean"
        self.assert_error("flagged", "Invalid Boolean Value.")

    @ddt.data("True", "true", 1, True)
    def test_following_true(self, value):
        self.form_data["following"] = value
        self.assert_field_value("following", True)

    @ddt.data("False", "false", 0, False)
    def test_following_false(self, value):
        self.form_data["following"] = value
        self.assert_error("following", "The value of the 'following' parameter must be true.")

    def test_invalid_following(self):
        self.form_data["following"] = "invalid-boolean"
        self.assert_error("following", "Invalid Boolean Value.")

    @ddt.data(*itertools.combinations(["topic_id", "text_search", "following"], 2))
    def test_mutually_exclusive(self, params):
        self.form_data.update({param: "True" for param in params})
        self.assert_error(
            "__all__",
            "The following query parameters are mutually exclusive: topic_id, text_search, following"
        )

    def test_invalid_view_choice(self):
        self.form_data["view"] = "not_a_valid_choice"
        self.assert_error("view", "Select a valid choice. not_a_valid_choice is not one of the available choices.")

    def test_invalid_sort_by_choice(self):
        self.form_data["order_by"] = "not_a_valid_choice"
        self.assert_error(
            "order_by",
            "Select a valid choice. not_a_valid_choice is not one of the available choices."
        )

    def test_invalid_sort_direction_choice(self):
        self.form_data["order_direction"] = "not_a_valid_choice"
        self.assert_error(
            "order_direction",
            "Select a valid choice. not_a_valid_choice is not one of the available choices."
        )

    @ddt.data(
        ("view", "unread"),
        ("view", "unanswered"),
        ("order_by", "last_activity_at"),
        ("order_by", "comment_count"),
        ("order_by", "vote_count"),
        ("order_direction", "desc"),
    )
    @ddt.unpack
    def test_valid_choice_fields(self, field, value):
        self.form_data[field] = value
        self.assert_field_value(field, value)

    def test_requested_fields(self):
        self.form_data["requested_fields"] = "profile_image"
        form = self.get_form(expected_valid=True)
        assert form.cleaned_data['requested_fields'] == {'profile_image'}


@ddt.ddt
class CommentListGetFormTest(FormTestMixin, PaginationTestMixin, TestCase):
    """Tests for CommentListGetForm"""
    FORM_CLASS = CommentListGetForm

    def setUp(self):
        super().setUp()
        self.form_data = {
            "thread_id": "deadbeef",
            "endorsed": "False",
            "page": "2",
            "page_size": "13",
            "flagged": "False",
        }

    def test_basic(self):
        form = self.get_form(expected_valid=True)
        assert form.cleaned_data == {
            'thread_id': 'deadbeef',
            'endorsed': False,
            'page': 2,
            'page_size': 13,
            'flagged': False,
            'requested_fields': set(),
            'merge_question_type_responses': False
        }

    def test_missing_thread_id(self):
        self.form_data.pop("thread_id")
        self.assert_error("thread_id", "This field is required.")

    def test_missing_endorsed(self):
        self.form_data.pop("endorsed")
        self.assert_field_value("endorsed", None)

    @ddt.data("True", "true", True, 1)
    def test_endorsed_true(self, value):
        self.form_data["endorsed"] = value
        self.assert_field_value("endorsed", True)

    @ddt.data("False", "false", False, 0)
    def test_endorsed_false(self, value):
        self.form_data["endorsed"] = value
        self.assert_field_value("endorsed", False)

    def test_invalid_endorsed(self):
        self.form_data["endorsed"] = "invalid-boolean"
        self.assert_error("endorsed", "Invalid Boolean Value.")

    def test_requested_fields(self):
        self.form_data["requested_fields"] = {"profile_image"}
        form = self.get_form(expected_valid=True)
        assert form.cleaned_data['requested_fields'] == {'profile_image'}


@ddt.ddt
class UserCommentListGetFormTest(FormTestMixin, PaginationTestMixin, TestCase):
    """Tests for UserCommentListGetForm"""
    FORM_CLASS = UserCommentListGetForm

    def setUp(self):
        super().setUp()
        self.form_data = {
            "course_id": "a/b/c",
            "flagged": "False",
            "page": "2",
            "page_size": "13",
        }

    def test_basic(self):
        form = self.get_form(expected_valid=True)
        assert form.cleaned_data == {
            'course_id': CourseLocator.from_string('a/b/c'),
            'flagged': False,
            'page': 2,
            'page_size': 13,
            'requested_fields': set()
        }

    def test_missing_flagged(self):
        self.form_data.pop("flagged")
        self.assert_field_value("flagged", False)

    @ddt.data("False", "false", False, 0)
    def test_flagged_false(self, value):
        self.form_data["flagged"] = value
        self.assert_field_value("flagged", False)

    @ddt.data("True", "true", True, 1)
    def test_flagged_true(self, value):
        self.form_data["flagged"] = value
        self.assert_field_value("flagged", True)

    def test_requested_fields(self):
        self.form_data["requested_fields"] = {"profile_image"}
        form = self.get_form(expected_valid=True)
        assert form.cleaned_data['requested_fields'] == {'profile_image'}

    def test_missing_course_id(self):
        self.form_data.pop("course_id")
        self.assert_error("course_id", "This field is required.")

    def test_invalid_course_id(self):
        self.form_data["course_id"] = "invalid course id"
        self.assert_error("course_id", "'invalid course id' is not a valid course id")
