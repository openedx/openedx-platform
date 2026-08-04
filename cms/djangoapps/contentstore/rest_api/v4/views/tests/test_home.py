"""
Unit tests for HomeCoursesViewSet (v4).
"""

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import ddt
from django.conf import settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from cms.djangoapps.contentstore.rest_api.v4.views.home import (
    _LEGACY_ORDER_DEPRECATION_HEADER,
)
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import reverse_course_url
from openedx.core.djangoapps.content.course_overviews.tests.factories import (
    CourseOverviewFactory,
)

_MOCK_GET_COURSE_CONTEXT_V2 = (
    "cms.djangoapps.contentstore.rest_api.v4.views.home.get_course_context_v2"
)


class TestHomeCoursesViewSetPermissions(APITestCase):
    """ADR 0026 - permission regression tests for HomeCoursesViewSet."""

    def setUp(self):
        super().setUp()
        self.list_url = reverse("cms.djangoapps.contentstore:v4:home-courses-list")

    def test_unauthenticated_returns_401(self):
        """Unauthenticated GET /v4/home/courses/ must return 401."""
        client = APIClient()
        response = client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)  # noqa: PT009

    def test_authenticated_staff_gets_200(self):
        """Authenticated staff user must receive 200."""
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(
            username="teststaff", password="pass", is_staff=True
        )
        self.client.force_authenticate(user=user)
        with patch(_MOCK_GET_COURSE_CONTEXT_V2, return_value=([], [])):
            response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009


@ddt.ddt
class TestHomeCoursesViewSet(CourseTestCase):
    """Functional tests for HomeCoursesViewSet list action."""

    def setUp(self):
        super().setUp()
        self.api_v4_url = reverse("cms.djangoapps.contentstore:v4:home-courses-list")
        self.active_course = CourseOverviewFactory.create(
            id=self.course.id,
            org=self.course.org,
            display_name=self.course.display_name,
        )
        archived_course_key = self.store.make_course_key(
            "demo-org", "demo-number", "demo-run"
        )
        self.archived_course = CourseOverviewFactory.create(
            display_name="Demo Course (Sample)",
            id=archived_course_key,
            org=archived_course_key.org,
            end=(datetime.now() - timedelta(days=365)).replace(
                tzinfo=timezone.utc  # noqa: UP017
            ),
        )
        self.non_staff_client, _ = self.create_non_staff_authed_user_client()

    def test_home_page_response(self):
        """GET /v4/home/courses/ must return the 7-field ADR 0032 pagination envelope."""
        response = self.client.get(self.api_v4_url)
        course_id = str(self.course.id)
        archived_course_id = str(self.archived_course.id)

        expected_data = {
            "courses": [
                OrderedDict(
                    [
                        ("course_key", course_id),
                        ("display_name", self.course.display_name),
                        (
                            "lms_link",
                            f"{settings.LMS_ROOT_URL}/courses/{course_id}/jump_to/{self.course.location}",
                        ),
                        (
                            "cms_link",
                            f'//{settings.CMS_BASE}{reverse_course_url("course_handler", self.course.id)}',
                        ),
                        ("number", self.course.number),
                        ("display_number", self.active_course.display_number_with_default),
                        ("org", self.course.org),
                        ("display_org", self.active_course.display_org_with_default),
                        ("rerun_link", f"/course_rerun/{course_id}"),
                        ("run", self.course.id.run),
                        ("url", f"/course/{course_id}"),
                        ("is_active", True),
                    ]
                ),
                OrderedDict(
                    [
                        ("course_key", str(self.archived_course.id)),
                        ("display_name", self.archived_course.display_name),
                        (
                            "lms_link",
                            f"{settings.LMS_ROOT_URL}/courses/{archived_course_id}"
                            f"/jump_to/{self.archived_course.location}",
                        ),
                        (
                            "cms_link",
                            f'//{settings.CMS_BASE}{reverse_course_url("course_handler", self.archived_course.id)}',
                        ),
                        ("number", self.archived_course.number),
                        ("display_number", self.archived_course.display_number_with_default),
                        ("org", self.archived_course.org),
                        ("display_org", self.archived_course.display_org_with_default),
                        ("rerun_link", f"/course_rerun/{str(self.archived_course.id)}"),
                        ("run", self.archived_course.id.run),
                        ("url", f"/course/{str(self.archived_course.id)}"),
                        ("is_active", False),
                    ]
                ),
            ],
            "in_process_course_actions": [],
        }
        expected_response = {
            "count": 2,
            "num_pages": 1,
            "current_page": 1,
            "start": 0,
            "next": None,
            "previous": None,
            "results": expected_data,
        }

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertDictEqual(expected_response, response.data)  # noqa: PT009

    def test_active_only_query_if_passed(self):
        """?active_only=true must return only active courses."""
        response = self.client.get(self.api_v4_url, {"active_only": "true"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(len(response.data["results"]["courses"]), 1)  # noqa: PT009
        self.assertTrue(  # noqa: PT009
            response.data["results"]["courses"][0]["is_active"]
        )

    def test_archived_only_query_if_passed(self):
        """?archived_only=true must return only archived courses."""
        response = self.client.get(self.api_v4_url, {"archived_only": "true"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(len(response.data["results"]["courses"]), 1)  # noqa: PT009
        self.assertFalse(  # noqa: PT009
            response.data["results"]["courses"][0]["is_active"]
        )

    def test_search_query_if_passed(self):
        """?search=sample must filter courses by name."""
        response = self.client.get(self.api_v4_url, {"search": "sample"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(len(response.data["results"]["courses"]), 1)  # noqa: PT009

    def test_ordering_query_if_passed(self):
        """?ordering=org must order courses by org (ADR 0033 standard parameter)."""
        response = self.client.get(self.api_v4_url, {"ordering": "org"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(len(response.data["results"]["courses"]), 2)  # noqa: PT009
        self.assertEqual(  # noqa: PT009
            response.data["results"]["courses"][0]["org"], "demo-org"
        )

    def test_legacy_order_query_still_works(self):
        """?order=org must still work (deprecated alias, ADR 0033)."""
        response = self.client.get(self.api_v4_url, {"order": "org"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(len(response.data["results"]["courses"]), 2)  # noqa: PT009

    def test_page_query_if_passed(self):
        """?page=1 must return paginated result with count."""
        response = self.client.get(self.api_v4_url, {"page": 1})

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(response.data["count"], 2)  # noqa: PT009

    @ddt.data(
        ("active_only", "true"),
        ("archived_only", "true"),
        ("search", "sample"),
        ("ordering", "org"),
        ("page", 1),
    )
    @ddt.unpack
    def test_if_empty_list_of_courses(self, query_param, value):
        """Empty course list returns empty results, not an error."""
        self.active_course.delete()
        self.archived_course.delete()

        response = self.client.get(self.api_v4_url, {query_param: value})

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(len(response.data["results"]["courses"]), 0)  # noqa: PT009

    @ddt.data(
        ("active_only", "true"),
        ("archived_only", "true"),
        ("search", "sample"),
        ("ordering", "org"),
        ("page", 1),
    )
    @ddt.unpack
    def test_if_empty_list_of_courses_non_staff(self, query_param, value):
        """Non-staff users with no courses get an empty result."""
        self.active_course.delete()
        self.archived_course.delete()

        response = self.non_staff_client.get(self.api_v4_url, {query_param: value})

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(len(response.data["results"]["courses"]), 0)  # noqa: PT009


class TestHomeCoursesViewSetOrderingDeprecation(CourseTestCase):
    """ADR 0033 – Deprecation header tests for the legacy ``order`` parameter."""

    def setUp(self):
        super().setUp()
        self.list_url = reverse("cms.djangoapps.contentstore:v4:home-courses-list")

    def test_ordering_param_no_deprecation_header(self):
        """``?ordering=display_name`` must not emit a Deprecation header."""
        with patch(_MOCK_GET_COURSE_CONTEXT_V2, return_value=([], [])):
            response = self.client.get(self.list_url, {"ordering": "display_name"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertNotIn("Deprecation", response)  # noqa: PT009

    def test_legacy_order_param_emits_deprecation_header(self):
        """``?order=display_name`` must emit the ADR 0033 Deprecation header."""
        with patch(_MOCK_GET_COURSE_CONTEXT_V2, return_value=([], [])):
            response = self.client.get(self.list_url, {"order": "display_name"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertIn("Deprecation", response)  # noqa: PT009
        self.assertEqual(  # noqa: PT009
            response["Deprecation"], _LEGACY_ORDER_DEPRECATION_HEADER
        )

    def test_ordering_wins_when_both_present(self):
        """When both params sent, ``ordering`` wins and Deprecation header is still emitted."""
        with patch(_MOCK_GET_COURSE_CONTEXT_V2, return_value=([], [])):
            response = self.client.get(
                self.list_url, {"ordering": "org", "order": "display_name"}
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertIn("Deprecation", response)  # noqa: PT009

    def test_no_ordering_param_no_deprecation_header(self):
        """Plain GET /v4/home/courses/ must not emit a Deprecation header."""
        with patch(_MOCK_GET_COURSE_CONTEXT_V2, return_value=([], [])):
            response = self.client.get(self.list_url)

        self.assertNotIn("Deprecation", response)  # noqa: PT009
