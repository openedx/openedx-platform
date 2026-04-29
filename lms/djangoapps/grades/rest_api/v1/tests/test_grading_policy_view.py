"""
Tests for the views
"""


from datetime import datetime

import ddt
from django.urls import reverse
from pytz import UTC
from xblocks_contrib.problem.capa.tests.response_xml_factory import MultipleChoiceResponseXMLFactory

from common.djangoapps.student.tests.factories import GlobalStaffFactory, StaffFactory, UserFactory
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


@ddt.ddt
class GradingPolicyTestMixin:
    """
    Mixin class for Grading Policy tests
    """
    view_name = None

    def setUp(self):
        super().setUp()
        self.create_user_and_access_token()

    def create_user_and_access_token(self):
        self.user = GlobalStaffFactory.create()
        self.oauth_client = ApplicationFactory.create()
        self.access_token = AccessTokenFactory.create(user=self.user, application=self.oauth_client).token

    @classmethod
    def create_course_data(cls):  # pylint: disable=missing-function-docstring
        cls.invalid_course_id = 'course-v1:foo+bar+baz'
        cls.course = CourseFactory.create(
            display_name='An Introduction to API Testing', grading_policy=cls.grading_policy
        )
        cls.course_id = str(cls.course.id)
        with cls.store.bulk_operations(cls.course.id, emit_signals=False):
            cls.sequential = BlockFactory.create(
                category="sequential",
                parent_location=cls.course.location,
                display_name="Lesson 1",
                format="Homework",
                graded=True
            )

            factory = MultipleChoiceResponseXMLFactory()
            args = {'choices': [False, True, False]}
            problem_xml = factory.build_xml(**args)
            cls.problem = BlockFactory.create(
                category="problem",
                parent_location=cls.sequential.location,
                display_name="Problem 1",
                format="Homework",
                data=problem_xml,
            )

            cls.video = BlockFactory.create(
                category="video",
                parent_location=cls.sequential.location,
                display_name="Video 1",
            )

            cls.html = BlockFactory.create(
                category="html",
                parent_location=cls.sequential.location,
                display_name="HTML 1",
            )

    def http_get(self, uri, **headers):
        """
        Submit an HTTP GET request
        """

        default_headers = {
            'HTTP_AUTHORIZATION': 'Bearer ' + self.access_token
        }
        default_headers.update(headers)

        response = self.client.get(uri, follow=True, **default_headers)
        return response

    def assert_get_for_course(self, course_id=None, expected_status_code=200, **headers):
        """
        Submit an HTTP GET request to the view for the given course.
        Validates the status_code of the response is as expected.
        """

        response = self.http_get(
            reverse(self.view_name, kwargs={'course_id': course_id or self.course_id}),
            **headers
        )
        assert response.status_code == expected_status_code
        return response

    def get_auth_header(self, user):
        """
        Returns Bearer auth header with a generated access token
        for the given user.
        """
        access_token = AccessTokenFactory.create(user=user, application=self.oauth_client).token
        return 'Bearer ' + access_token

    def test_get_invalid_course(self):
        """
        The view should return a 404 for an invalid course ID.
        """
        self.assert_get_for_course(course_id=self.invalid_course_id, expected_status_code=404)

    def test_get(self):
        """
        The view should return a 200 for a valid course ID.
        """
        return self.assert_get_for_course()

    def test_not_authenticated(self):
        """
        The view should return HTTP status 401 if user is unauthenticated.
        """
        self.assert_get_for_course(expected_status_code=401, HTTP_AUTHORIZATION="")

    def test_staff_authorized(self):
        """
        The view should return a 200 when provided an access token
        for course staff.
        """
        user = StaffFactory(course_key=self.course.id)
        auth_header = self.get_auth_header(user)
        self.assert_get_for_course(HTTP_AUTHORIZATION=auth_header)

    def test_not_authorized(self):
        """
        The view should return HTTP status 404 when provided an
        access token for an unauthorized user.
        """
        user = UserFactory()
        auth_header = self.get_auth_header(user)
        self.assert_get_for_course(expected_status_code=403, HTTP_AUTHORIZATION=auth_header)

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_course_keys(self, modulestore_type):
        """
        The view should be addressable by course-keys from both module stores.
        """
        course = CourseFactory.create(
            start=datetime(2014, 6, 16, 14, 30, tzinfo=UTC),
            end=datetime(2015, 1, 16, tzinfo=UTC),
            org="MTD",
            default_store=modulestore_type,
        )
        self.assert_get_for_course(course_id=str(course.id))


class CourseGradingPolicyTests(GradingPolicyTestMixin, SharedModuleStoreTestCase):
    """
    Tests for CourseGradingPolicy view.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE
    view_name = 'grades_api:v1:course_grading_policy'

    grading_policy = {
        'GRADER': [
            {
                "min_count": 24,
                "weight": 0.2,
                "type": "Homework",
                "drop_count": 0,
                "short_label": "HW"
            },
            {
                "min_count": 4,
                "weight": 0.8,
                "type": "Exam",
                "drop_count": 0,
                "short_label": "Exam"
            }
        ]
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.create_course_data()

    def test_get(self):
        """
        The view should return grading policy for a course.
        """
        response = super().test_get()

        expected = [
            {
                "count": 24,
                "weight": 0.2,
                "assignment_type": "Homework",
                "dropped": 0
            },
            {
                "count": 4,
                "weight": 0.8,
                "assignment_type": "Exam",
                "dropped": 0
            }
        ]
        self.assertListEqual(response.data, expected)  # noqa: PT009


class CourseGradingPolicyMissingFieldsTests(GradingPolicyTestMixin, SharedModuleStoreTestCase):
    """
    Tests for CourseGradingPolicy view when fields are missing.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE
    view_name = 'grades_api:v1:course_grading_policy'

    # Raw grader with missing keys
    grading_policy = {
        'GRADER': [
            {
                "min_count": 24,
                "weight": 0.2,
                "type": "Homework",
                "drop_count": 0,
                "short_label": "HW"
            },
            {
                # Deleted "min_count" key
                "weight": 0.8,
                "type": "Exam",
                "drop_count": 0,
                "short_label": "Exam"
            }
        ]
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.create_course_data()

    def test_get(self):
        """
        The view should return grading policy for a course.
        """
        response = super().test_get()

        expected = [
            {
                "count": 24,
                "weight": 0.2,
                "assignment_type": "Homework",
                "dropped": 0
            },
            {
                "count": None,
                "weight": 0.8,
                "assignment_type": "Exam",
                "dropped": 0
            }
        ]
        self.assertListEqual(response.data, expected)  # noqa: PT009
