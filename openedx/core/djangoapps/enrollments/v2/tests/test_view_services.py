"""
Unit tests for EnrollmentOperationsService (v2).

Exercises the service methods directly — no HTTP layer involved. Tests the
two-layer authorization model (ADR 0031) and the modern ADR 0029 raise-DRF-
exceptions pattern.
"""
from unittest.mock import MagicMock, patch

import pytest
from django.test import TestCase
from rest_framework.exceptions import NotFound, ValidationError

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.enrollments.v2.view_services import EnrollmentOperationsService


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
