"""
Test for CourseEnrollmentViewStarted filter in enrollment views.
"""
import json

from django.test import override_settings
from django.urls import reverse
from openedx_filters import PipelineStep
from openedx_filters.learning.filters import CourseEnrollmentViewStarted
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.tests.factories import UserFactory, UserProfileFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

TEST_STEP_PATH = "openedx.core.djangoapps.enrollments.tests.test_filters.TestCourseEnrollmentViewStartedPipelineStep"


class TestCourseEnrollmentViewStartedPipelineStep(PipelineStep):
    """
    Utility pipeline step that prevents enrollment via CourseEnrollmentViewStarted filter.
    """

    def run_filter(self, user, course_key, requester_is_backend_service):  # pylint: disable=arguments-differ
        """Pipeline step that prevents enrollment for specific users or conditions."""
        if user.username == "blocked_user":
            raise CourseEnrollmentViewStarted.PreventEnrollment(
                "User is not allowed to enroll in this course."
            )
        # Set a side effect on the user to verify the filter was executed
        user.profile.set_meta({"enrollment_filter_executed": True})
        user.profile.save()
        return {
            "user": user,
            "course_key": course_key,
            "requester_is_backend_service": requester_is_backend_service,
        }


@skip_unless_lms
class CourseEnrollmentViewStartedFilterTest(APITestCase, ModuleStoreTestCase):
    """
    Tests for the CourseEnrollmentViewStarted filter in the enrollment view.

    This class guarantees that the following filters are triggered when attempting
    to enroll a user through the enrollment API endpoint:

    - CourseEnrollmentViewStarted
    """

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.course = CourseFactory.create(emit_signals=True)
        self.user = UserFactory.create(
            username="test_user",
            email="test@example.com",
            password="password",
        )
        self.blocked_user = UserFactory.create(
            username="blocked_user",
            email="blocked@example.com",
            password="password",
        )
        UserProfileFactory.create(user=self.user, name="Test User")
        UserProfileFactory.create(user=self.blocked_user, name="Blocked User")

        # Create course modes
        CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug='audit',
            mode_display_name='Audit'
        )

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course.enrollment.view.started.v1": {
                "pipeline": [
                    TEST_STEP_PATH,
                ],
                "fail_silently": False,
            },
        },
    )
    def test_course_enrollment_view_started_filter_executed(self):
        """
        Test that the CourseEnrollmentViewStarted filter is executed when enrolling through the view.

        Expected result:
            - CourseEnrollmentViewStarted is triggered and executes the pipeline step.
            - The enrollment is created successfully if the filter allows it.
            - The filter pipeline step sets a side effect on the user's profile.
        """
        self.client.login(username=self.user.username, password="password")

        response = self.client.post(
            reverse('courseenrollments'),
            {
                "course_details": {"course_id": str(self.course.id)},
                "mode": "audit",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        # Verify the filter was executed by checking the side effect
        self.user.profile.refresh_from_db()
        meta = self.user.profile.get_meta()
        assert meta.get("enrollment_filter_executed") is True

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.learning.course.enrollment.view.started.v1": {
                "pipeline": [
                    TEST_STEP_PATH,
                ],
                "fail_silently": False,
            },
        },
    )
    def test_course_enrollment_view_started_filter_prevent_enrollment(self):
        """
        Test that enrollment is prevented when filter raises PreventEnrollment.

        Expected result:
            - CourseEnrollmentViewStarted is triggered and executes the pipeline step.
            - The pipeline step raises PreventEnrollment for blocked_user.
            - The endpoint returns HTTP 400 Bad Request.
        """
        self.client.login(username=self.blocked_user.username, password="password")

        response = self.client.post(
            reverse('courseenrollments'),
            {
                "course_details": {"course_id": str(self.course.id)},
                "mode": "audit",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        response_data = json.loads(response.content.decode('utf-8'))
        assert "message" in response_data
        assert "An error occurred while creating the new course enrollment" in response_data["message"]

    @override_settings(OPEN_EDX_FILTERS_CONFIG={})
    def test_course_enrollment_view_started_without_filter_configuration(self):
        """
        Test enrollment without filter configuration (no filter interference).

        Expected result:
            - CourseEnrollmentViewStarted does not have any effect.
            - The enrollment process succeeds without filter intervention.
        """
        self.client.login(username=self.user.username, password="password")

        response = self.client.post(
            reverse('courseenrollments'),
            {
                "course_details": {"course_id": str(self.course.id)},
                "mode": "audit",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
