"""
Tests for Discussion API internal interface
"""

from unittest import mock

import ddt
import pytest
from django.contrib.auth import get_user_model
from django.test.client import RequestFactory
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.discussion.rest_api.api import get_user_comments
from lms.djangoapps.discussion.rest_api.tests.utils import ForumMockUtilsMixin, make_minimal_cs_comment
from openedx.core.lib.exceptions import CourseNotFoundError, PageNotFoundError
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

User = get_user_model()


@ddt.ddt
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class GetUserCommentsTest(ForumMockUtilsMixin, SharedModuleStoreTestCase):
    """
    Tests for get_user_comments.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        super().setUpClassAndForumMock()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        super().disposeForumMocks()

    @mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    def setUp(self):
        super().setUp()

        self.course = CourseFactory.create()

        # create staff user so that we don't need to worry about
        # permissions here
        self.user = UserFactory.create(is_staff=True)
        self.register_get_user_response(self.user)

        self.request = RequestFactory().get(f'/api/discussion/v1/users/{self.user.username}/{self.course.id}')
        self.request.user = self.user

    def test_call_with_single_results_page(self):
        """
        Assert that a minimal call with valid inputs, and single result,
        returns the expected response structure.
        """
        self.register_get_comments_response(
            [make_minimal_cs_comment()],
            page=1,
            num_pages=1,
        )
        response = get_user_comments(
            request=self.request,
            author=self.user,
            course_key=self.course.id,
        )
        assert "results" in response.data
        assert "pagination" in response.data
        assert response.data["pagination"]["count"] == 1
        assert response.data["pagination"]["num_pages"] == 1
        assert response.data["pagination"]["next"] is None
        assert response.data["pagination"]["previous"] is None

    @ddt.data(1, 2, 3)
    def test_call_with_paginated_results(self, page):
        """
        Assert that paginated results return the correct pagination
        information at the pagination boundaries.
        """
        self.register_get_comments_response(
            [make_minimal_cs_comment() for _ in range(30)],
            page=page,
            num_pages=3,
        )
        response = get_user_comments(
            request=self.request,
            author=self.user,
            course_key=self.course.id,
            page=page,
        )
        assert "pagination" in response.data
        assert response.data["pagination"]["count"] == 30
        assert response.data["pagination"]["num_pages"] == 3

        if page in (1, 2):
            assert response.data["pagination"]["next"] is not None
            assert f"page={page+1}" in response.data["pagination"]["next"]
        if page in (2, 3):
            assert response.data["pagination"]["previous"] is not None
            assert f"page={page-1}" in response.data["pagination"]["previous"]
        if page == 1:
            assert response.data["pagination"]["previous"] is None
        if page == 3:
            assert response.data["pagination"]["next"] is None

    def test_call_with_invalid_page(self):
        """
        Assert that calls for pages that exceed the existing number of
        results pages raise PageNotFoundError.
        """
        self.register_get_comments_response([], page=2, num_pages=1)
        with pytest.raises(PageNotFoundError):
            get_user_comments(
                request=self.request,
                author=self.user,
                course_key=self.course.id,
                page=2,
            )

    def test_call_with_non_existent_course(self):
        """
        Assert that calls for comments in a course that doesn't exist
        result in a CourseNotFoundError error.
        """
        self.register_get_comments_response(
            [make_minimal_cs_comment()],
            page=1,
            num_pages=1,
        )
        with pytest.raises(CourseNotFoundError):
            get_user_comments(
                request=self.request,
                author=self.user,
                course_key=CourseKey.from_string("course-v1:x+y+z"),
                page=2,
            )
