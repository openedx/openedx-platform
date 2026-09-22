"""
Unit tests for EnrollmentOperationsService (v2).

Exercises the service methods directly — no HTTP layer involved. Tests the
two-layer authorization model (ADR 0031) and the modern ADR 0029 raise-DRF-
exceptions pattern.
"""
from unittest.mock import MagicMock, patch

import pytest
from django.test import TestCase, override_settings
from opaque_keys.edx.keys import CourseKey
from openedx_filters import PipelineStep
from openedx_filters.learning.filters import CourseEnrollmentViewStarted
from rest_framework.exceptions import NotFound, ValidationError

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.enrollments.v2.view_services import EnrollmentOperationsService

SERVICE_MODULE = "openedx.core.djangoapps.enrollments.v2.view_services"
TESTS_MODULE = "openedx.core.djangoapps.enrollments.v2.tests.test_view_services"
ENROLLMENT_VIEW_STARTED = "org.openedx.learning.course.enrollment.view.started.v1"
ENROLLMENT_ERROR_MESSAGE = "An error occurred while creating the new course enrollment"

# Arguments seen by RecordingEnrollmentViewStep, one dict per invocation.
RECORDED_CALLS = []


class RecordingEnrollmentViewStep(PipelineStep):
    """
    Utility pipeline step that records how CourseEnrollmentViewStarted was invoked.
    """

    def run_filter(self, user, course_key, requester_is_backend_service):  # pylint: disable=arguments-differ
        """Record the incoming arguments and pass them through unchanged."""
        arguments = {
            "user": user,
            "course_key": course_key,
            "requester_is_backend_service": requester_is_backend_service,
        }
        RECORDED_CALLS.append(arguments)
        return arguments


class BlockingEnrollmentViewStep(PipelineStep):
    """
    Utility pipeline step that halts the enrollment via PreventEnrollment.
    """

    def run_filter(self, user, course_key, requester_is_backend_service):  # pylint: disable=arguments-differ
        """Halt the enrollment before it reaches the enrollment API."""
        raise CourseEnrollmentViewStarted.PreventEnrollment("Enrollment halted by the pipeline.")


def filters_config(step_class_name):
    """Build an OPEN_EDX_FILTERS_CONFIG wiring ``step_class_name`` into the enrollment filter."""
    return {
        ENROLLMENT_VIEW_STARTED: {
            "pipeline": [f"{TESTS_MODULE}.{step_class_name}"],
            "fail_silently": False,
        },
    }


class TestUnenrollUserForRetirement(TestCase):
    """ADR 0029 — error handling for the retirement unenroll flow."""

    def setUp(self):
        super().setUp()
        self.service = EnrollmentOperationsService()

    def test_missing_username_raises_validation_error(self):
        with pytest.raises(ValidationError):
            self.service.unenroll_user_for_retirement(None)

    def test_blank_username_raises_validation_error(self):
        with pytest.raises(ValidationError):
            self.service.unenroll_user_for_retirement("")

    @patch(
        "openedx.core.djangoapps.enrollments.v2.view_services.UserRetirementStatus.get_retirement_for_retirement_action"
    )
    def test_unknown_retirement_status_raises_not_found(self, mock_get):
        from openedx.core.djangoapps.user_api.models import UserRetirementStatus
        mock_get.side_effect = UserRetirementStatus.DoesNotExist()
        with pytest.raises(NotFound):
            self.service.unenroll_user_for_retirement("ghost-user")


class TestListEnrollmentsForUser(TestCase):
    """ADR 0031 — per-operation permission filter in the listing helper."""

    def setUp(self):
        super().setUp()
        self.service = EnrollmentOperationsService()
        self.user = UserFactory.create()
        self.other = UserFactory.create()

    @patch("openedx.core.djangoapps.enrollments.v2.view_services.CourseEnrollment.objects")
    def test_self_lookup_returns_full_list_unfiltered(self, mock_objects):
        """Requesting your own enrollments bypasses the course-staff filter."""
        mock_qs = MagicMock()
        mock_qs.__iter__ = lambda self: iter([])
        mock_objects.filter.return_value.select_related.return_value = mock_qs
        result = self.service.list_enrollments_for_user(
            request_user=self.user, target_username=self.user.username, has_api_key=False,
        )
        assert isinstance(result, list)

    @patch("openedx.core.djangoapps.enrollments.v2.view_services.CourseEnrollment.objects")
    def test_api_key_bypasses_per_course_filter(self, mock_objects):
        """has_api_key=True returns the full list even across user boundaries."""
        mock_qs = MagicMock()
        mock_qs.__iter__ = lambda self: iter([])
        mock_objects.filter.return_value.select_related.return_value = mock_qs
        result = self.service.list_enrollments_for_user(
            request_user=self.user, target_username=self.other.username, has_api_key=True,
        )
        assert isinstance(result, list)


class TestDeleteAllowedEnrollment(TestCase):
    """ADR 0029 — delete raises NotFound when the row is missing."""

    def setUp(self):
        super().setUp()
        self.service = EnrollmentOperationsService()

    @patch("openedx.core.djangoapps.enrollments.v2.view_services.CourseEnrollmentAllowed.objects")
    def test_delete_missing_row_raises_not_found(self, mock_objects):
        from django.core.exceptions import ObjectDoesNotExist
        mock_objects.get.side_effect = ObjectDoesNotExist()
        with pytest.raises(NotFound):
            self.service.delete_allowed_enrollment("ghost@example.com", "course-v1:org+course+run")


class TestCreateOrUpdateEnrollmentFilter(TestCase):
    """The create/update flow runs CourseEnrollmentViewStarted and honors PreventEnrollment."""

    def setUp(self):
        super().setUp()
        RECORDED_CALLS.clear()
        self.service = EnrollmentOperationsService()
        self.user = UserFactory.create()
        self.course_id = CourseKey.from_string("course-v1:edX+DemoX+Demo_Course")
        self.request = MagicMock()
        self.request.user = self.user
        self.request.data = {"user": self.user.username, "mode": "audit"}

    @override_settings(OPEN_EDX_FILTERS_CONFIG=filters_config(step_class_name="RecordingEnrollmentViewStep"))
    @patch(f"{SERVICE_MODULE}.api.add_enrollment", return_value={"mode": "audit"})
    @patch(f"{SERVICE_MODULE}.api.get_enrollment", return_value=None)
    @patch(f"{SERVICE_MODULE}.embargo_api.get_embargo_response", return_value=None)
    def test_filter_runs_for_backend_service_request(self, mock_embargo, mock_get_enrollment, mock_add_enrollment):
        """
        A backend-service create runs the pipeline step and still enrolls the user.

        Expected result:
            - The step receives the resolved user, the course key, and requester_is_backend_service=True.
            - The enrollment API is still called and its payload is returned.
        """
        response_data = self.service.create_or_update_enrollment(
            request=self.request,
            has_api_key=True,
            course_id=self.course_id,
        )

        assert response_data == {"mode": "audit"}
        assert len(RECORDED_CALLS) == 1
        assert RECORDED_CALLS[0]["user"] == self.user
        assert RECORDED_CALLS[0]["course_key"] == self.course_id
        assert RECORDED_CALLS[0]["requester_is_backend_service"] is True
        assert mock_add_enrollment.call_count == 1

    @override_settings(OPEN_EDX_FILTERS_CONFIG=filters_config(step_class_name="RecordingEnrollmentViewStep"))
    @patch(f"{SERVICE_MODULE}.api.add_enrollment", return_value={"mode": "audit"})
    @patch(f"{SERVICE_MODULE}.api.get_enrollment", return_value=None)
    @patch(f"{SERVICE_MODULE}.embargo_api.get_embargo_response", return_value=None)
    def test_filter_runs_for_end_user_request(self, mock_embargo, mock_get_enrollment, mock_add_enrollment):
        """
        A self-service create runs the pipeline step with requester_is_backend_service=False.

        Expected result:
            - The step still runs, but is told the requester is not a backend service.
            - The enrollment API is still called.
        """
        self.service.create_or_update_enrollment(
            request=self.request,
            has_api_key=False,
            course_id=self.course_id,
        )

        assert len(RECORDED_CALLS) == 1
        assert RECORDED_CALLS[0]["requester_is_backend_service"] is False
        assert mock_add_enrollment.call_count == 1

    @override_settings(OPEN_EDX_FILTERS_CONFIG=filters_config(step_class_name="BlockingEnrollmentViewStep"))
    @patch(f"{SERVICE_MODULE}.api.add_enrollment")
    @patch(f"{SERVICE_MODULE}.api.get_enrollment", return_value=None)
    @patch(f"{SERVICE_MODULE}.embargo_api.get_embargo_response", return_value=None)
    def test_prevent_enrollment_stops_the_enrollment(self, mock_embargo, mock_get_enrollment, mock_add_enrollment):
        """
        PreventEnrollment is translated into the ADR 0029 ValidationError envelope.

        Expected result:
            - The enrollment API is never reached.
            - The caller sees ValidationError, i.e. the same 400 the v1 view returns.
            - The detail carries the generic enrollment-error message, not the pipeline's own
              message, matching the v1 response body.
        """
        with pytest.raises(ValidationError) as exc_info:
            self.service.create_or_update_enrollment(
                request=self.request,
                has_api_key=True,
                course_id=self.course_id,
            )

        assert ENROLLMENT_ERROR_MESSAGE in str(exc_info.value)
        assert mock_add_enrollment.call_count == 0

    @override_settings(OPEN_EDX_FILTERS_CONFIG={})
    @patch(f"{SERVICE_MODULE}.api.add_enrollment", return_value={"mode": "audit"})
    @patch(f"{SERVICE_MODULE}.api.get_enrollment", return_value=None)
    @patch(f"{SERVICE_MODULE}.embargo_api.get_embargo_response", return_value=None)
    def test_enrollment_proceeds_without_filter_configuration(
        self, mock_embargo, mock_get_enrollment, mock_add_enrollment,
    ):
        """
        With no pipeline configured the filter is inert and the enrollment proceeds.

        Expected result:
            - No pipeline step runs.
            - The enrollment API is called and its payload is returned unchanged.
        """
        response_data = self.service.create_or_update_enrollment(
            request=self.request,
            has_api_key=True,
            course_id=self.course_id,
        )

        assert response_data == {"mode": "audit"}
        assert not RECORDED_CALLS
        assert mock_add_enrollment.call_count == 1
