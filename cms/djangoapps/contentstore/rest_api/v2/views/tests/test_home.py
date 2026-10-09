"""
Unit tests for home page view.
"""

from collections import OrderedDict
from datetime import UTC, datetime, timedelta, timezone

import ddt
from django.conf import settings
from django.urls import reverse
from rest_framework import status

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import reverse_course_url
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory


@ddt.ddt
class HomePageCoursesViewV2Test(CourseTestCase):
    """
    Tests for HomePageView view version 2.
    """

    def setUp(self):
        super().setUp()
        self.api_v2_url = reverse("cms.djangoapps.contentstore:v2:courses")
        self.active_course = CourseOverviewFactory.create(
            id=self.course.id,
            org=self.course.org,
            display_name=self.course.display_name,
        )
        archived_course_key = self.store.make_course_key('demo-org', 'demo-number', 'demo-run')
        self.archived_course = CourseOverviewFactory.create(
            display_name="Demo Course (Sample)",
            id=archived_course_key,
            org=archived_course_key.org,
            end=(datetime.now() - timedelta(days=365)).replace(tzinfo=timezone.utc),  # noqa: UP017
        )
        self.non_staff_client, _ = self.create_non_staff_authed_user_client()

    def test_home_page_response(self):
        """Get list of courses available to the logged in user.

        Expected result:
        - A paginated response.
        - A list of courses available to the logged in user.
        """
        response = self.client.get(self.api_v2_url)
        course_id = str(self.course.id)
        archived_course_id = str(self.archived_course.id)

        expected_data = {
            "courses": [
                OrderedDict([
                    ("course_key", course_id),
                    ("display_name", self.course.display_name),
                    ("lms_link", f'{settings.LMS_ROOT_URL}/courses/{course_id}/jump_to/{self.course.location}'),
                    ("cms_link", f'//{settings.CMS_BASE}{reverse_course_url("course_handler", self.course.id)}'),
                    ("number", self.active_course.number),
                    ("display_number", self.active_course.display_number_with_default),
                    ("org", self.active_course.org),
                    ("display_org", self.active_course.display_org_with_default),
                    ("rerun_link", f'/course_rerun/{course_id}'),
                    ("run", self.course.id.run),
                    ("url", f'/course/{course_id}'),
                    ("is_active", True),
                ]),
                OrderedDict([
                    ("course_key", str(self.archived_course.id)),
                    ("display_name", self.archived_course.display_name),
                    (
                        "lms_link",
                        f'{settings.LMS_ROOT_URL}/courses/{archived_course_id}/jump_to/{self.archived_course.location}'
                    ),
                    (
                        "cms_link",
                        f'//{settings.CMS_BASE}{reverse_course_url("course_handler", self.archived_course.id)}',
                    ),
                    ("number", self.archived_course.number),
                    ("display_number", self.archived_course.display_number_with_default),
                    ("org", self.archived_course.org),
                    ("display_org", self.archived_course.display_org_with_default),
                    ("rerun_link", f'/course_rerun/{str(self.archived_course.id)}'),
                    ("run", self.archived_course.id.run),
                    ("url", f'/course/{str(self.archived_course.id)}'),
                    ("is_active", False),
                ]),
            ],
            "in_process_course_actions": [],
        }
        expected_response = OrderedDict([
            ('count', 2),
            ('num_pages', 1),
            ('next', None),
            ('previous', None),
            ('results', expected_data),
        ])

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertDictEqual(expected_response, response.data)  # noqa: PT009

    def test_active_only_query_if_passed(self):
        """Get list of active courses only.

        Expected result:
        - A list of active courses available to the logged in user.
        """
        response = self.client.get(self.api_v2_url, {"active_only": "true"})

        self.assertEqual(len(response.data["results"]["courses"]), 1)  # noqa: PT009
        self.assertEqual(response.data["results"]["courses"], [OrderedDict([  # noqa: PT009
            ("course_key", str(self.course.id)),
            ("display_name", self.course.display_name),
            ("lms_link", f'{settings.LMS_ROOT_URL}/courses/{str(self.course.id)}/jump_to/{self.course.location}'),
            ("cms_link", f'//{settings.CMS_BASE}{reverse_course_url("course_handler", self.course.id)}'),
            ("number", self.active_course.number),
            ("display_number", self.active_course.display_number_with_default),
            ("org", self.active_course.org),
            ("display_org", self.active_course.display_org_with_default),
            ("rerun_link", f'/course_rerun/{str(self.course.id)}'),
            ("run", self.course.id.run),
            ("url", f'/course/{str(self.course.id)}'),
            ("is_active", True),
        ])])
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009

    def test_archived_only_query_if_passed(self):
        """Get list of archived courses only.

        Expected result:
        - A list of archived courses available to the logged in user.
        """
        response = self.client.get(self.api_v2_url, {"archived_only": "true"})

        self.assertEqual(len(response.data["results"]["courses"]), 1)  # noqa: PT009
        self.assertEqual(response.data["results"]["courses"], [OrderedDict([  # noqa: PT009
            ("course_key", str(self.archived_course.id)),
            ("display_name", self.archived_course.display_name),
            (
                "lms_link",
                '{url_root}/courses/{course_id}/jump_to/{location}'.format(  # noqa: UP032
                    url_root=settings.LMS_ROOT_URL,
                    course_id=str(self.archived_course.id),
                    location=self.archived_course.location
                ),
            ),
            ("cms_link", f'//{settings.CMS_BASE}{reverse_course_url("course_handler", self.archived_course.id)}'),
            ("number", self.archived_course.number),
            ("display_number", self.archived_course.display_number_with_default),
            ("org", self.archived_course.org),
            ("display_org", self.archived_course.display_org_with_default),
            ("rerun_link", f'/course_rerun/{str(self.archived_course.id)}'),
            ("run", self.archived_course.id.run),
            ("url", f'/course/{str(self.archived_course.id)}'),
            ("is_active", False),
        ])])
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009

    def test_search_query_if_passed(self):
        """Get list of courses when search filter passed as a query param.

        Expected result:
        - A list of courses (active or inactive) available to the logged in user for the specified search.
        """
        response = self.client.get(self.api_v2_url, {"search": "sample"})

        self.assertEqual(len(response.data["results"]["courses"]), 1)  # noqa: PT009
        self.assertEqual(response.data["results"]["courses"], [OrderedDict([  # noqa: PT009
            ("course_key", str(self.archived_course.id)),
            ("display_name", self.archived_course.display_name),
            (
                "lms_link",
                '{url_root}/courses/{course_id}/jump_to/{location}'.format(  # noqa: UP032
                    url_root=settings.LMS_ROOT_URL,
                    course_id=str(self.archived_course.id),
                    location=self.archived_course.location
                ),
            ),
            ("cms_link", f'//{settings.CMS_BASE}{reverse_course_url("course_handler", self.archived_course.id)}'),
            ("number", self.archived_course.number),
            ("display_number", self.archived_course.display_number_with_default),
            ("org", self.archived_course.org),
            ("display_org", self.archived_course.display_org_with_default),
            ("rerun_link", f'/course_rerun/{str(self.archived_course.id)}'),
            ("run", self.archived_course.id.run),
            ("url", f'/course/{str(self.archived_course.id)}'),
            ("is_active", False),
        ])])
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009

    def test_search_query_matches_display_number(self):
        """Search must match course display number values."""
        course_key = self.store.make_course_key("search-org", "opaque-number", "run")
        searchable_course = CourseOverviewFactory.create(
            id=course_key,
            org=course_key.org,
            display_name="Unrelated Title",
            display_number_with_default="Friendly Number 42",
        )

        response = self.client.get(self.api_v2_url, {"search": "Friendly Number"})

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]["courses"]) == 1
        assert response.data["results"]["courses"][0]["course_key"] == str(searchable_course.id)

    def test_order_query_if_passed(self):
        """Get list of courses when order filter passed as a query param.

        Expected result:
        - A list of courses (active or inactive) available to the logged in user for the specified order.
        """
        response = self.client.get(self.api_v2_url, {"order": "org"})

        self.assertEqual(len(response.data["results"]["courses"]), 2)  # noqa: PT009
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(response.data["results"]["courses"][0]["org"], "demo-org")  # noqa: PT009

    def test_page_query_if_passed(self):
        """Get list of courses when page filter passed as a query param.

        Expected result:
        - A list of courses (active or inactive) available to the logged in user for the specified page.
        """
        response = self.client.get(self.api_v2_url, {"page": 1})

        self.assertEqual(response.data["count"], 2)  # noqa: PT009
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009

    @ddt.data(
        ("active_only", "true"),
        ("archived_only", "true"),
        ("search", "sample"),
        ("order", "org"),
        ("page", 1),
        ("start_date_on_or_after", "2099-01-01T00:00:00Z"),
    )
    @ddt.unpack
    def test_if_empty_list_of_courses(self, query_param, value):
        """Get list of courses when no courses are available.

        Expected result:
        - An empty list of courses available to the logged in user.
        """
        self.active_course.delete()
        self.archived_course.delete()

        response = self.client.get(self.api_v2_url, {query_param: value})

        self.assertEqual(len(response.data['results']['courses']), 0)  # noqa: PT009
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009

    @ddt.data(
        ("start_date_on_or_after", "not-a-date"),
        ("start_date_on_or_after", "2024-01-01"),
        ("start_date_on_or_after", "2024-01-01T10:00:00"),
        ("start_date_on_or_before", "not-a-date"),
        ("start_date_on_or_before", "2024-01-01"),
        ("start_date_on_or_before", "2024-01-01T10:00:00"),
        ("start_date_on_or_after", "2024-01-01 10:00"),
        ("start_date_on_or_before", "2024-01-01 10:00"),
    )
    @ddt.unpack
    def test_start_date_invalid_format_returns_400(self, query_param, value):
        """Get list of courses when a start date param is garbage, a bare date, or a datetime without an offset.

        Expected result:
        - An HTTP 400 "Bad Request" response.
        """
        response = self.client.get(self.api_v2_url, {query_param: value})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)  # noqa: PT009

    def test_start_date_invalid_returns_400_when_user_has_no_courses(self):
        """Get list of courses with a bare-date start param as a user without accessible courses.

        Expected result:
        - An HTTP 400 "Bad Request" response.
        """
        response = self.non_staff_client.get(self.api_v2_url, {"start_date_on_or_after": "2024-01-01"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_start_date_unencoded_plus_is_read_as_offset(self):
        """Get list of courses with a start param whose '+' arrived as a space.

        Expected result:
        - An HTTP 200 "OK" response with the same courses as the '+04:00' request.
        """
        course_key = self.store.make_course_key("tz-org", "tz-plus", "tz-run")
        CourseOverviewFactory.create(id=course_key, org=course_key.org, start=datetime(2027, 7, 1, 6, 0, tzinfo=UTC))

        def get_course_keys(after):
            response = self.client.get(self.api_v2_url, {"start_date_on_or_after": after})
            assert response.status_code == status.HTTP_200_OK
            return {course["course_key"] for course in response.data["results"]["courses"]}

        # 06:00Z is 10:00+04:00 local, so only a +04:00 reading of the bound is on or before it.
        plus_keys = get_course_keys("2027-07-01T10:00:00 04:00")
        assert str(course_key) in plus_keys
        assert plus_keys == get_course_keys("2027-07-01T10:00:00+04:00")
        assert str(course_key) not in get_course_keys("2027-07-01T10:00:00Z")

    def test_start_date_with_utc_offset_returns_200(self):
        """Get list of courses when start date params are datetimes with a UTC offset.

        Expected result:
        - An HTTP 200 "OK" response.
        """
        response = self.client.get(self.api_v2_url, {
            "start_date_on_or_after": "2024-01-01T00:00:00+04:00",
            "start_date_on_or_before": "2099-01-01T23:59:59.999999Z",
        })

        assert response.status_code == status.HTTP_200_OK

    def test_start_date_with_space_separator_and_offset_returns_200(self):
        """Get list of courses when the start date uses a space between date and time.

        Expected result:
        - An HTTP 200 "OK" response.
        """
        response = self.client.get(self.api_v2_url, {"start_date_on_or_after": "2024-01-01 10:00:00+04:00"})

        assert response.status_code == status.HTTP_200_OK

    def test_start_date_range_compares_instants_using_offset(self):
        """Get list of courses for a one-day start date range sent with a UTC offset and again as UTC.

        Course starts, as UTC and as UTC+04:00 local times:
        - A: 2027-06-30T22:00Z is 2027-07-01T02:00 local.
        - B: 2027-07-01T02:00Z is 2027-07-01T06:00 local.
        - C: 2027-07-01T20:30Z is 2027-07-02T00:30 local.

        Expected result:
        - July 1 in UTC+04:00 returns courses A and B.
        - The same wall-clock range in UTC returns courses B and C.
        """
        starts = {
            "a": datetime(2027, 6, 30, 22, 0, tzinfo=UTC),
            "b": datetime(2027, 7, 1, 2, 0, tzinfo=UTC),
            "c": datetime(2027, 7, 1, 20, 30, tzinfo=UTC),
        }
        course_keys = {}
        for name, start in starts.items():
            course_key = self.store.make_course_key("tz-org", f"tz-{name}", "tz-run")
            CourseOverviewFactory.create(id=course_key, org=course_key.org, start=start)
            course_keys[name] = str(course_key)

        def get_course_keys(after, before):
            response = self.client.get(self.api_v2_url, {
                "start_date_on_or_after": after,
                "start_date_on_or_before": before,
            })
            assert response.status_code == status.HTTP_200_OK
            return {course["course_key"] for course in response.data["results"]["courses"]}

        assert get_course_keys(
            "2027-07-01T00:00:00+04:00", "2027-07-01T23:59:59.999999+04:00"
        ) == {course_keys["a"], course_keys["b"]}
        assert get_course_keys(
            "2027-07-01T00:00:00Z", "2027-07-01T23:59:59.999999Z"
        ) == {course_keys["b"], course_keys["c"]}

    @ddt.data(
        ("active_only", "true", 2, 0),
        ("archived_only", "true", 0, 1),
        ("search", "foo", 1, 0),
        ("search", "demo", 0, 1),
        ("order", "org", 2, 1),
        ("order", "display_name", 2, 1),
        ("order", "number", 2, 1),
        ("order", "run", 2, 1)
    )
    @ddt.unpack
    def test_filter_and_ordering_courses(
        self,
        filter_key,
        filter_value,
        expected_active_length,
        expected_archived_length
    ):
        """Get list of courses when filter and ordering are applied.

        This test creates two courses besides the default courses created in the setUp method.
        Then filters and orders them based on the filter_key and filter_value passed as query parameters.

        Expected result:
        - A list of courses available to the logged in user for the specified filter and order.
        """
        archived_course_key = self.store.make_course_key("demo-org", "demo-number", "demo-run")
        CourseOverviewFactory.create(
            display_name="Course (Demo)",
            id=archived_course_key,
            org=archived_course_key.org,
            end=(datetime.now() - timedelta(days=365)).replace(tzinfo=timezone.utc),  # noqa: UP017
        )
        active_course_key = self.store.make_course_key("foo-org", "foo-number", "foo-run")
        CourseOverviewFactory.create(
            display_name="Course (Foo)",
            id=active_course_key,
            org=active_course_key.org,
        )

        response = self.client.get(self.api_v2_url, {filter_key: filter_value})

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(  # noqa: PT009
            len([course for course in response.data["results"]["courses"] if course["is_active"]]),
            expected_active_length
        )
        self.assertEqual(  # noqa: PT009
            len([course for course in response.data["results"]["courses"] if not course["is_active"]]),
            expected_archived_length
        )

    @ddt.data(
        ("active_only", "true"),
        ("archived_only", "true"),
        ("search", "sample"),
        ("order", "org"),
        ("page", 1),
    )
    @ddt.unpack
    def test_if_empty_list_of_courses_non_staff(self, query_param, value):
        """Get list of courses when no courses are available for non-staff users.

        Expected result:
        - An empty list of courses available to the logged in user.
        """
        self.active_course.delete()
        self.archived_course.delete()

        response = self.non_staff_client.get(self.api_v2_url, {query_param: value})

        self.assertEqual(len(response.data["results"]["courses"]), 0)  # noqa: PT009
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
