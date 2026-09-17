"""Tests for authz decorators."""
from unittest.mock import Mock, patch

from django.test import RequestFactory, TestCase
from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator

from openedx.core.djangoapps.authz.constants import LegacyAuthoringPermission
from openedx.core.djangoapps.authz.decorators import (
    authz_permission_required,
    get_course_key,
    user_has_course_permission_from_query_param,
)
from openedx.core.lib.api.view_utils import DeveloperErrorResponseException


class AuthzPermissionRequiredDecoratorTests(TestCase):
    """
    Tests focused on the authz_permission_required decorator behavior.
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.course_key = CourseLocator("TestX", "TST101", "2025")

        self.user = Mock()
        self.user.username = "testuser"
        self.user.id = 1

        self.view_instance = Mock()

    def _build_request(self):
        request = self.factory.get("/test")
        request.user = self.user
        return request

    def test_view_executes_when_permission_granted(self):
        """Decorator allows execution when permission check passes."""
        request = self._build_request()

        mock_view = Mock(return_value="success")

        with patch(
            "openedx.core.djangoapps.authz.decorators.user_has_course_permission",
            return_value=True,
        ):
            decorated = authz_permission_required("courses.view")(mock_view)

            result = decorated(self.view_instance, request, str(self.course_key))

        self.assertEqual(result, "success")  # noqa: PT009
        mock_view.assert_called_once_with(
            self.view_instance,
            request,
            self.course_key,
        )

    def test_view_executes_when_legacy_fallback_read(self):
        """Decorator allows execution when AuthZ denies but legacy permission succeeds."""
        request = self._build_request()

        mock_view = Mock(return_value="success")

        with patch(
            "openedx.core.djangoapps.authz.decorators.enable_authz_course_authoring",
            return_value=False,
        ), patch(
            "openedx.core.djangoapps.authz.decorators.authz_api.is_user_allowed",
            return_value=True,  # Should not be used when AuthZ is disabled, but set to True just in case
        ), patch(
            "openedx.core.djangoapps.authz.constants.has_studio_read_access",
            return_value=True,
        ):
            decorated = authz_permission_required(
                "courses.view",
                legacy_permission=LegacyAuthoringPermission.READ
            )(mock_view)

            result = decorated(self.view_instance, request, str(self.course_key))

        self.assertEqual(result, "success")  # noqa: PT009
        mock_view.assert_called_once()

    def test_view_executes_when_legacy_fallback_write(self):
        """Decorator allows execution when AuthZ denies but legacy write permission succeeds."""
        request = self._build_request()

        mock_view = Mock(return_value="success")

        with patch(
            "openedx.core.djangoapps.authz.decorators.enable_authz_course_authoring",
            return_value=False,
        ), patch(
            "openedx.core.djangoapps.authz.decorators.authz_api.is_user_allowed",
            return_value=True,  # Should not be used when AuthZ is disabled, but set to True just in case
        ), patch(
            "openedx.core.djangoapps.authz.constants.has_studio_write_access",
            return_value=True,
        ):
            decorated = authz_permission_required(
                "courses.edit",
                legacy_permission=LegacyAuthoringPermission.WRITE
            )(mock_view)

            result = decorated(self.view_instance, request, str(self.course_key))

        self.assertEqual(result, "success")  # noqa: PT009
        mock_view.assert_called_once()

    def test_access_denied_when_permission_fails(self):
        """Decorator raises API error when permission fails."""
        request = self._build_request()

        mock_view = Mock()

        with patch(
            "openedx.core.djangoapps.authz.decorators.user_has_course_permission",
            return_value=False,
        ):
            decorated = authz_permission_required("courses.view")(mock_view)

            with self.assertRaises(DeveloperErrorResponseException) as context:  # noqa: PT027
                decorated(self.view_instance, request, str(self.course_key))

        self.assertEqual(context.exception.response.status_code, 403)  # noqa: PT009
        mock_view.assert_not_called()

    def test_decorator_preserves_function_name(self):
        """Decorator preserves wrapped function metadata."""

        def sample_view(self, request, course_key):
            return "ok"

        decorated = authz_permission_required("courses.view")(sample_view)

        self.assertEqual(decorated.__name__, "sample_view")  # noqa: PT009


class GetCourseKeyTests(TestCase):
    """Tests for the get_course_key function used in the authz decorators."""

    def setUp(self):
        self.course_key = CourseLocator("TestX", "TST101", "2025")

    def test_course_key_string(self):
        """Valid course key string returns CourseKey."""
        result = get_course_key(str(self.course_key))

        self.assertEqual(result, self.course_key)  # noqa: PT009

    def test_usage_key_string(self):
        """UsageKey string resolves to course key."""
        usage_key = BlockUsageLocator(
            self.course_key,
            "html",
            "block1"
        )

        result = get_course_key(str(usage_key))

        self.assertEqual(result, self.course_key)  # noqa: PT009


class UserHasCoursePermissionFromQueryParamTests(TestCase):
    """Tests for user_has_course_permission_from_query_param."""

    def setUp(self):
        self.factory = RequestFactory()
        self.course_key = CourseLocator("TestX", "TST101", "2025")
        self.user = Mock()

    def test_missing_param_denies_without_checking_permission(self):
        """No query param at all means the bypass doesn't apply."""
        request = self.factory.get("/test")

        with patch("openedx.core.djangoapps.authz.decorators.user_has_course_permission") as mock_check:
            result = user_has_course_permission_from_query_param(request, "courses.view_library_updates")

        assert result is False
        mock_check.assert_not_called()

    def test_invalid_course_id_denies_without_checking_permission(self):
        """A malformed course/usage id is treated as absent, not as an error."""
        request = self.factory.get("/test", {"course_id": "not-a-real-key"})

        with patch("openedx.core.djangoapps.authz.decorators.user_has_course_permission") as mock_check:
            result = user_has_course_permission_from_query_param(request, "courses.view_library_updates")

        assert result is False
        mock_check.assert_not_called()

    def test_valid_course_id_delegates_to_permission_check(self):
        """A valid course id is parsed and passed through to the real permission check."""
        request = self.factory.get("/test", {"course_id": str(self.course_key)})
        request.user = self.user

        with patch(
            "openedx.core.djangoapps.authz.decorators.user_has_course_permission",
            return_value=True,
        ) as mock_check:
            result = user_has_course_permission_from_query_param(request, "courses.view_library_updates")

        assert result is True
        mock_check.assert_called_once_with(self.user, "courses.view_library_updates", self.course_key)

    def test_usage_key_in_param_resolves_to_its_course(self):
        """A usage key (not just a bare course key) resolves to the course it belongs to."""
        usage_key = BlockUsageLocator(self.course_key, "html", "block1")
        request = self.factory.get("/test", {"course_id": str(usage_key)})
        request.user = self.user

        with patch(
            "openedx.core.djangoapps.authz.decorators.user_has_course_permission",
            return_value=True,
        ) as mock_check:
            result = user_has_course_permission_from_query_param(request, "courses.view_library_updates")

        assert result is True
        mock_check.assert_called_once_with(self.user, "courses.view_library_updates", self.course_key)

    def test_custom_param_name(self):
        """The query param name can be overridden."""
        request = self.factory.get("/test", {"downstream_course_id": str(self.course_key)})
        request.user = self.user

        with patch(
            "openedx.core.djangoapps.authz.decorators.user_has_course_permission",
            return_value=True,
        ):
            result = user_has_course_permission_from_query_param(
                request, "courses.view_library_updates", param_name="downstream_course_id",
            )

        assert result is True
