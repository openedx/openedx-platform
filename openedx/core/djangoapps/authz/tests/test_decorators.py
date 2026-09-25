"""Tests for authz decorators."""
from unittest.mock import Mock, patch

from django.test import RequestFactory, TestCase
from opaque_keys.edx.locator import (
    BlockUsageLocator,
    CourseLocator,
    LibraryContainerLocator,
    LibraryLocatorV2,
    LibraryUsageLocatorV2,
)

from openedx.core.djangoapps.authz.constants import LegacyAuthoringPermission
from openedx.core.djangoapps.authz.decorators import (
    authz_permission_required,
    get_course_key,
    user_has_course_permission_for_upstream,
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


class UserHasCoursePermissionForUpstreamTests(TestCase):
    """Tests for user_has_course_permission_for_upstream."""

    def setUp(self):
        self.factory = RequestFactory()
        self.course_key = CourseLocator("TestX", "TST101", "2025")
        self.downstream_key = BlockUsageLocator(self.course_key, "html", "downstream1")
        self.library_key = LibraryLocatorV2(org="TestX", slug="lib1")
        self.upstream_key = LibraryUsageLocatorV2(self.library_key, block_type="html", usage_id="upstream1")
        self.user = Mock()

    def test_missing_param_denies_without_loading_anything(self):
        """No query param at all means the bypass doesn't apply."""
        request = self.factory.get("/test")

        with patch("xmodule.modulestore.django.modulestore") as mock_modulestore:
            result = user_has_course_permission_for_upstream(
                request, "courses.view_library_updates", self.upstream_key,
            )

        assert result is False
        mock_modulestore.assert_not_called()

    def test_invalid_usage_key_denies_without_loading_anything(self):
        """A malformed usage key is treated as absent, not as an error."""
        request = self.factory.get("/test", {"course_id": "not-a-real-key"})

        with patch("xmodule.modulestore.django.modulestore") as mock_modulestore:
            result = user_has_course_permission_for_upstream(
                request, "courses.view_library_updates", self.upstream_key,
            )

        assert result is False
        mock_modulestore.assert_not_called()

    def test_downstream_not_found_denies(self):
        """If the downstream block doesn't exist, the bypass doesn't apply."""
        from xmodule.modulestore.exceptions import ItemNotFoundError  # pylint: disable=import-outside-toplevel

        request = self.factory.get("/test", {"course_id": str(self.downstream_key)})

        with patch("xmodule.modulestore.django.modulestore") as mock_modulestore, patch(
            "openedx.core.djangoapps.authz.decorators.user_has_course_permission",
        ) as mock_check:
            mock_modulestore.return_value.get_item.side_effect = ItemNotFoundError
            result = user_has_course_permission_for_upstream(
                request, "courses.view_library_updates", self.upstream_key,
            )

        assert result is False
        mock_check.assert_not_called()

    def test_downstream_with_no_upstream_link_denies(self):
        """If the downstream has no upstream field set at all, the bypass doesn't apply."""
        request = self.factory.get("/test", {"course_id": str(self.downstream_key)})

        with patch("xmodule.modulestore.django.modulestore") as mock_modulestore, patch(
            "openedx.core.djangoapps.authz.decorators.user_has_course_permission",
        ) as mock_check:
            mock_modulestore.return_value.get_item.return_value = Mock(spec=["usage_key"])
            result = user_has_course_permission_for_upstream(
                request, "courses.view_library_updates", self.upstream_key,
            )

        assert result is False
        mock_check.assert_not_called()

    def test_downstream_with_malformed_upstream_denies(self):
        """If the downstream's upstream field isn't a parseable library key, deny."""
        request = self.factory.get("/test", {"course_id": str(self.downstream_key)})

        with patch("xmodule.modulestore.django.modulestore") as mock_modulestore, patch(
            "openedx.core.djangoapps.authz.decorators.user_has_course_permission",
        ) as mock_check:
            mock_modulestore.return_value.get_item.return_value = Mock(upstream="not-a-real-key")
            result = user_has_course_permission_for_upstream(
                request, "courses.view_library_updates", self.upstream_key,
            )

        assert result is False
        mock_check.assert_not_called()

    def test_mismatched_upstream_denies(self):
        """
        If the downstream links to a *different* upstream than the one being requested,
        the bypass doesn't apply. This is the crux of the fix for openedx-authz#441: holding
        the course permission isn't enough by itself, the linked resource has to match.
        """
        request = self.factory.get("/test", {"course_id": str(self.downstream_key)})
        unrelated_upstream_key = LibraryUsageLocatorV2(self.library_key, block_type="html", usage_id="other")

        with patch("xmodule.modulestore.django.modulestore") as mock_modulestore, patch(
            "openedx.core.djangoapps.authz.decorators.user_has_course_permission",
        ) as mock_check:
            mock_modulestore.return_value.get_item.return_value = Mock(upstream=str(unrelated_upstream_key))
            result = user_has_course_permission_for_upstream(
                request, "courses.view_library_updates", self.upstream_key,
            )

        assert result is False
        mock_check.assert_not_called()

    def test_matching_upstream_delegates_to_permission_check(self):
        """If the downstream's upstream link matches, we check the course permission."""
        request = self.factory.get("/test", {"course_id": str(self.downstream_key)})
        request.user = self.user

        with patch("xmodule.modulestore.django.modulestore") as mock_modulestore, patch(
            "openedx.core.djangoapps.authz.decorators.user_has_course_permission",
            return_value=True,
        ) as mock_check:
            mock_modulestore.return_value.get_item.return_value = Mock(upstream=str(self.upstream_key))
            result = user_has_course_permission_for_upstream(
                request, "courses.view_library_updates", self.upstream_key,
            )

        assert result is True
        mock_check.assert_called_once_with(self.user, "courses.view_library_updates", self.course_key)

    def test_matching_container_upstream_delegates_to_permission_check(self):
        """Same as above, but the upstream is a container (unit/subsection/section), not a block."""
        container_key = LibraryContainerLocator(self.library_key, container_type="unit", container_id="upstream-unit")
        request = self.factory.get("/test", {"course_id": str(self.downstream_key)})
        request.user = self.user

        with patch("xmodule.modulestore.django.modulestore") as mock_modulestore, patch(
            "openedx.core.djangoapps.authz.decorators.user_has_course_permission",
            return_value=True,
        ) as mock_check:
            mock_modulestore.return_value.get_item.return_value = Mock(upstream=str(container_key))
            result = user_has_course_permission_for_upstream(
                request, "courses.view_library_updates", container_key,
            )

        assert result is True
        mock_check.assert_called_once_with(self.user, "courses.view_library_updates", self.course_key)

    def test_custom_param_name(self):
        """The query param name can be overridden."""
        request = self.factory.get("/test", {"downstream_id": str(self.downstream_key)})
        request.user = self.user

        with patch("xmodule.modulestore.django.modulestore") as mock_modulestore, patch(
            "openedx.core.djangoapps.authz.decorators.user_has_course_permission",
            return_value=True,
        ):
            mock_modulestore.return_value.get_item.return_value = Mock(upstream=str(self.upstream_key))
            result = user_has_course_permission_for_upstream(
                request, "courses.view_library_updates", self.upstream_key, param_name="downstream_id",
            )

        assert result is True
