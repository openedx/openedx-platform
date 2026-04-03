"""
Tests for Discussion API views
"""

import json
from datetime import datetime
from unittest import mock
from urllib.parse import urlencode

import ddt
from django.urls import reverse
from pytz import UTC
from rest_framework import status

from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole, GlobalStaff
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from common.djangoapps.util.testing import UrlResetMixin
from lms.djangoapps.discussion.rest_api.tests.utils import (
    ForumMockUtilsMixin,
    make_minimal_cs_comment,
    make_minimal_cs_thread
)
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


@ddt.ddt
@mock.patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
class CommentViewSetListByUserTest(
    ForumMockUtilsMixin,
    UrlResetMixin,
    ModuleStoreTestCase,
):
    """
    Common test cases for views retrieving user-published content.
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

        self.user = UserFactory.create(password=self.TEST_PASSWORD)
        self.register_get_user_response(self.user)

        self.other_user = UserFactory.create(password=self.TEST_PASSWORD)
        self.register_get_user_response(self.other_user)

        self.course = CourseFactory.create(org="a", course="b", run="c", start=datetime.now(UTC))
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)

        self.url = self.build_url(self.user.username, self.course.id)

    def register_mock_endpoints(self):
        """
        Register forum service mocks for sample threads and comments.
        """
        self.register_get_threads_response(
            threads=[
                make_minimal_cs_thread({
                    "id": f"test_thread_{index}",
                    "course_id": str(self.course.id),
                    "commentable_id": f"test_topic_{index}",
                    "username": self.user.username,
                    "user_id": str(self.user.id),
                    "thread_type": "discussion",
                    "title": f"Test Title #{index}",
                    "body": f"Test body #{index}",
                })
                for index in range(30)
            ],
            page=1,
            num_pages=1,
        )
        self.register_get_comments_response(
            comments=[
                make_minimal_cs_comment({
                    "id": f"test_comment_{index}",
                    "thread_id": "test_thread",
                    "user_id": str(self.user.id),
                    "username": self.user.username,
                    "created_at": "2015-05-11T00:00:00Z",
                    "updated_at": "2015-05-11T11:11:11Z",
                    "body": f"Test body #{index}",
                    "votes": {"up_count": 4},
                })
                for index in range(30)
            ],
            page=1,
            num_pages=1,
        )

    def build_url(self, username, course_id, **kwargs):
        """
        Builds an URL to access content from an user on a specific course.
        """
        base = reverse("comment-list")
        query = urlencode({
            "username": username,
            "course_id": str(course_id),
            **kwargs,
        })
        return f"{base}?{query}"

    def assert_successful_response(self, response):
        """
        Check that the response was successful and contains the expected fields.
        """
        assert response.status_code == status.HTTP_200_OK
        response_data = json.loads(response.content)
        assert "results" in response_data
        assert "pagination" in response_data

    def test_request_by_unauthenticated_user(self):
        """
        Unauthenticated users are not allowed to request users content.
        """
        self.register_mock_endpoints()
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_request_by_unauthorized_user(self):
        """
        Users are not allowed to request content from courses in which
        they're not either enrolled or staff members.
        """
        self.register_mock_endpoints()
        self.client.login(username=self.other_user.username, password=self.TEST_PASSWORD)
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert json.loads(response.content)["developer_message"] == "Course not found."

    def test_request_by_enrolled_user(self):
        """
        Users that are enrolled in a course are allowed to get users'
        comments in that course.
        """
        self.register_mock_endpoints()
        self.client.login(username=self.other_user.username, password=self.TEST_PASSWORD)
        CourseEnrollmentFactory.create(user=self.other_user, course_id=self.course.id)
        self.assert_successful_response(self.client.get(self.url))

    def test_request_by_global_staff(self):
        """
        Staff users are allowed to get any user's comments.
        """
        self.register_mock_endpoints()
        self.client.login(username=self.other_user.username, password=self.TEST_PASSWORD)
        GlobalStaff().add_users(self.other_user)
        self.assert_successful_response(self.client.get(self.url))

    @ddt.data(CourseStaffRole, CourseInstructorRole)
    def test_request_by_course_staff(self, role):
        """
        Course staff users are allowed to get an user's comments in that
        course.
        """
        self.register_mock_endpoints()
        self.client.login(username=self.other_user.username, password=self.TEST_PASSWORD)
        role(course_key=self.course.id).add_users(self.other_user)
        self.assert_successful_response(self.client.get(self.url))

    def test_request_with_non_existent_user(self):
        """
        Requests for users that don't exist result in a 404 response.
        """
        self.register_mock_endpoints()
        self.client.login(username=self.other_user.username, password=self.TEST_PASSWORD)
        GlobalStaff().add_users(self.other_user)
        url = self.build_url("non_existent", self.course.id)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_request_with_non_existent_course(self):
        """
        Requests for courses that don't exist result in a 404 response.
        """
        self.register_mock_endpoints()
        self.client.login(username=self.other_user.username, password=self.TEST_PASSWORD)
        GlobalStaff().add_users(self.other_user)
        url = self.build_url(self.user.username, "course-v1:x+y+z")
        response = self.client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_request_with_invalid_course_id(self):
        """
        Requests with invalid course ID should fail form validation.
        """
        self.register_mock_endpoints()
        self.client.login(username=self.other_user.username, password=self.TEST_PASSWORD)
        GlobalStaff().add_users(self.other_user)
        url = self.build_url(self.user.username, "an invalid course")
        response = self.client.get(url)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        parsed_response = json.loads(response.content)
        assert parsed_response["field_errors"]["course_id"]["developer_message"] == \
            "'an invalid course' is not a valid course id"

    def test_request_with_empty_results_page(self):
        """
        Requests for pages that exceed the available number of pages
        result in a 404 response.
        """
        self.register_get_threads_response(threads=[], page=1, num_pages=1)
        self.register_get_comments_response(comments=[], page=1, num_pages=1)

        self.client.login(username=self.other_user.username, password=self.TEST_PASSWORD)
        GlobalStaff().add_users(self.other_user)
        url = self.build_url(self.user.username, self.course.id, page=2)
        response = self.client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND
