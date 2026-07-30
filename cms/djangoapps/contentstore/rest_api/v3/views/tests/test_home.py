"""
Unit tests for HomeViewSet — v3 (ADR 0025 / 0026 / 0028).

MongoDB-free: all service-layer calls are mocked.

patch.object is used for the ViewSet's get_serializer() method because:
  - get_serializer_class() returns a *different* serializer per action.
  - Each serializer (StudioHomeSerializer, CourseHomeTabSerializer,
    LibraryTabSerializer) has many required fields that would be painful to
    satisfy with synthetic data.
  - Patching get_serializer() lets us focus on routing + service-call
    assertions without re-testing serializer logic (covered in serializer
    unit tests).
"""
from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from cms.djangoapps.contentstore.rest_api.v3.views.home import HomeViewSet
from common.djangoapps.student.tests.factories import UserFactory

MOCK_GET_HOME_CONTEXT = (
    'cms.djangoapps.contentstore.rest_api.v3.views.home.get_home_context'
)
MOCK_GET_COURSE_CONTEXT = (
    'cms.djangoapps.contentstore.rest_api.v3.views.home.get_course_context'
)
MOCK_GET_LIBRARY_CONTEXT = (
    'cms.djangoapps.contentstore.rest_api.v3.views.home.get_library_context'
)
MOCK_ORG_API = (
    'cms.djangoapps.contentstore.rest_api.v3.views.home.org_api'
)


class TestHomeViewSetPermissions(APITestCase):
    """
    ADR 0026 – permission regression tests for HomeViewSet (v3).

    Verifies that ``permission_classes = (IsAuthenticated,)`` enforces the
    access rules expected of the consolidated viewset.
    """

    def test_unauthenticated_list_returns_401(self):
        """Unauthenticated GET /home/ must return 401."""
        url = reverse('cms.djangoapps.contentstore:v3:home-list')
        response = self.client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_unauthenticated_courses_returns_401(self):
        """Unauthenticated GET /home/courses/ must return 401."""
        url = reverse('cms.djangoapps.contentstore:v3:home-courses')
        response = self.client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_unauthenticated_libraries_returns_401(self):
        """Unauthenticated GET /home/libraries/ must return 401."""
        url = reverse('cms.djangoapps.contentstore:v3:home-libraries')
        response = self.client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestHomeViewSetActions(APITestCase):
    """
    Action tests for HomeViewSet (list, courses, libraries).

    Any authenticated user can access these endpoints — no course-staff role
    is required — so a plain (non-staff) factory user is sufficient.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.client.force_authenticate(user=self.user)

    @patch.object(HomeViewSet, 'get_serializer')
    @patch(MOCK_ORG_API)
    @patch(MOCK_GET_HOME_CONTEXT)
    def test_list_calls_get_home_context(self, mock_home, mock_org, mock_get_ser):
        """GET /home/ calls get_home_context() and returns 200."""
        mock_home.return_value = {'can_create_organizations': True}
        mock_org.is_autocreate_enabled.return_value = True
        mock_get_ser.return_value.data = {'studio_name': 'Studio'}

        response = self.client.get(reverse('cms.djangoapps.contentstore:v3:home-list'))

        assert response.status_code == status.HTTP_200_OK
        mock_home.assert_called_once()

    @patch.object(HomeViewSet, 'get_serializer')
    @patch(MOCK_GET_COURSE_CONTEXT)
    def test_courses_calls_get_course_context(self, mock_courses, mock_get_ser):
        """GET /home/courses/ calls get_course_context() and returns 200."""
        mock_courses.return_value = ([], [], [])
        mock_get_ser.return_value.data = {
            'courses': [],
            'archived_courses': [],
            'in_process_course_actions': [],
        }

        response = self.client.get(reverse('cms.djangoapps.contentstore:v3:home-courses'))

        assert response.status_code == status.HTTP_200_OK
        mock_courses.assert_called_once()

    @patch.object(HomeViewSet, 'get_serializer')
    @patch(MOCK_GET_LIBRARY_CONTEXT)
    def test_libraries_calls_get_library_context(self, mock_libs, mock_get_ser):
        """GET /home/libraries/ calls get_library_context() and returns 200."""
        mock_libs.return_value = {'libraries': []}
        mock_get_ser.return_value.data = {'libraries': []}

        response = self.client.get(reverse('cms.djangoapps.contentstore:v3:home-libraries'))

        assert response.status_code == status.HTTP_200_OK
        mock_libs.assert_called_once()


# ---------------------------------------------------------------------------
# ADR 0036 — ?fields= field selection on the list action
# ---------------------------------------------------------------------------
class TestHomeViewSetFieldSelection(APITestCase):
    """
    ADR 0036 — verify ``?fields=`` filters top-level keys on the ``list``
    action's wide ``StudioHomeSerializer`` response. ``courses`` and
    ``libraries`` actions are out of scope (single-key dicts).
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.client.force_authenticate(user=self.user)
        self.url = reverse('cms.djangoapps.contentstore:v3:home-list')

    @patch.object(HomeViewSet, 'get_serializer')
    @patch(MOCK_ORG_API)
    @patch(MOCK_GET_HOME_CONTEXT)
    def test_default_response_keeps_all_keys(self, mock_home, mock_org, mock_get_ser):  # noqa: ARG002
        """Without ``?fields=`` every top-level key is returned."""
        mock_home.return_value = {'can_create_organizations': True}
        mock_org.is_autocreate_enabled.return_value = True
        mock_get_ser.return_value.data = {
            'studio_name': 'Studio', 'platform_name': 'edX',
            'courses': [], 'libraries': [], 'archived_courses': [],
        }

        response = self.client.get(self.url)

        assert response.status_code == status.HTTP_200_OK
        assert set(response.data.keys()) == {
            'studio_name', 'platform_name', 'courses', 'libraries', 'archived_courses',
        }

    @patch.object(HomeViewSet, 'get_serializer')
    @patch(MOCK_ORG_API)
    @patch(MOCK_GET_HOME_CONTEXT)
    def test_fields_csv_restricts_top_level_keys(self, mock_home, mock_org, mock_get_ser):  # noqa: ARG002
        """``?fields=courses,libraries`` returns exactly those keys."""
        mock_home.return_value = {'can_create_organizations': True}
        mock_org.is_autocreate_enabled.return_value = True
        mock_get_ser.return_value.data = {
            'studio_name': 'Studio', 'platform_name': 'edX',
            'courses': [], 'libraries': [], 'archived_courses': [],
        }

        response = self.client.get(self.url, {'fields': 'courses,libraries'})

        assert response.status_code == status.HTTP_200_OK
        assert set(response.data.keys()) == {'courses', 'libraries'}
        assert 'studio_name' not in response.data
        assert 'platform_name' not in response.data
