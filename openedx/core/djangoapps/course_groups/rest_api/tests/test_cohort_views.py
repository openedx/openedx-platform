"""
Tests for the v2 cohorts REST API.
"""
import pytest
from django.urls import NoReverseMatch, Resolver404, resolve, reverse
from rest_framework import status
from rest_framework.test import APIClient

from common.djangoapps.student.roles import CourseStaffRole
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.course_groups.models import CourseCohortsSettings, CourseUserGroup
from openedx.core.djangoapps.course_groups.tests.helpers import CohortFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import ToyCourseFactory

ERROR_ENVELOPE_FIELDS = ("type", "title", "status", "detail", "instance")


@skip_unless_lms
class CohortV2TestCase(SharedModuleStoreTestCase):
    """Shared fixtures for the v2 cohort endpoints."""

    password = "password"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = ToyCourseFactory.create()
        cls.course_key = cls.course.id

    def setUp(self):
        super().setUp()
        self.staff_user = UserFactory.create(password=self.password)
        CourseStaffRole(self.course_key).add_users(self.staff_user)
        self.outsider = UserFactory.create(password=self.password)
        self.cohort = CohortFactory(course_id=self.course_key, name="Alpha")
        self.client = APIClient()
        self.client.login(username=self.staff_user.username, password=self.password)

    def list_url(self):
        return reverse("api_cohorts:cohort_list", kwargs={"course_key_string": str(self.course_key)})

    def detail_url(self, cohort_id=None):
        return reverse("api_cohorts:cohort_detail", kwargs={
            "course_key_string": str(self.course_key),
            "cohort_id": cohort_id or self.cohort.id,
        })

    def members_url(self, cohort_id=None):
        return reverse("api_cohorts:cohort_member_list", kwargs={
            "course_key_string": str(self.course_key),
            "cohort_id": cohort_id or self.cohort.id,
        })

    def settings_url(self):
        return reverse("api_cohorts:course_cohort_settings",
                       kwargs={"course_key_string": str(self.course_key)})


class TestCohortV2Routing(CohortV2TestCase):
    """Each route must resolve exactly one address."""

    def test_list_requires_trailing_slash(self):
        """The slashless path must not resolve to the list endpoint."""
        assert self.list_url().endswith("/")

    def test_singular_users_segment_does_not_resolve(self):
        """
        v1 spells the members path 'users?', so /user resolves as well as
        /users. The v2 path must not.
        """
        with pytest.raises(Resolver404):
            resolve(self.members_url().replace("/users/", "/user/"))

    def test_username_segment_does_not_span_path_separators(self):
        """
        v1 captures the username with '.+', so /users/bob/extra yields the
        username 'bob/extra'. The v2 pattern must reject it.
        """
        with pytest.raises(NoReverseMatch):
            reverse("api_cohorts:cohort_member_detail", kwargs={
                "course_key_string": str(self.course_key),
                "cohort_id": self.cohort.id,
                "username": "bob/extra",
            })

    def test_list_and_detail_are_separate_routes(self):
        """A single pattern with an optional id must not serve both."""
        assert self.list_url() != self.detail_url()


class TestCohortV2Access(CohortV2TestCase):
    """Authentication and authorization."""

    def test_anonymous_is_rejected(self):
        self.client.logout()
        response = self.client.get(self.list_url())
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_unprivileged_user_is_forbidden(self):
        self.client.logout()
        self.client.login(username=self.outsider.username, password=self.password)
        response = self.client.get(self.list_url())
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_course_staff_may_read(self):
        response = self.client.get(self.list_url())
        assert response.status_code == status.HTTP_200_OK

    def test_bearer_authentication_is_not_accepted(self):
        """The deprecated Bearer class is absent from the v2 views."""
        from openedx.core.djangoapps.course_groups.rest_api.cohort_views import CohortViewSet
        declared = {cls.__name__ for cls in CohortViewSet.authentication_classes}
        assert "BearerAuthenticationAllowInactiveUser" not in declared
        assert "JwtAuthentication" in declared

    def test_malformed_course_key_is_not_a_server_error(self):
        response = self.client.get("/api/cohorts/v2/courses/not-a-key/cohorts/")
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestCohortV2ReadsDoNotWrite(CohortV2TestCase):
    """Read paths must not create rows."""

    def test_listing_cohorts_creates_no_settings_row(self):
        """
        The v1 list endpoint reaches migrate_cohort_settings() and creates a
        CourseCohortsSettings row plus CourseCohort rows.
        """
        CourseCohortsSettings.objects.filter(course_id=self.course_key).delete()
        before = CourseCohortsSettings.objects.count()
        self.client.get(self.list_url())
        assert CourseCohortsSettings.objects.count() == before

    def test_reading_settings_creates_no_settings_row(self):
        CourseCohortsSettings.objects.filter(course_id=self.course_key).delete()
        before = CourseCohortsSettings.objects.count()
        response = self.client.get(self.settings_url())
        assert response.status_code == status.HTTP_200_OK
        assert "is_cohorted" in response.data
        assert CourseCohortsSettings.objects.count() == before

    def test_listing_cohorts_creates_no_cohort_rows(self):
        before = CourseUserGroup.objects.count()
        self.client.get(self.list_url())
        assert CourseUserGroup.objects.count() == before


class TestCohortV2ListEnvelope(CohortV2TestCase):
    """List endpoints must return the standard envelope."""

    def test_cohort_list_returns_envelope(self):
        """
        The v1 list endpoint paginates and then discards the paginator,
        returning a bare array with no count and no next link.
        """
        response = self.client.get(self.list_url())
        assert response.status_code == status.HTTP_200_OK
        for field in ("count", "num_pages", "current_page", "start", "next", "previous", "results"):
            assert field in response.data

    def test_member_list_returns_envelope(self):
        response = self.client.get(self.members_url())
        assert response.status_code == status.HTTP_200_OK
        for field in ("count", "num_pages", "current_page", "start", "next", "previous", "results"):
            assert field in response.data

    def test_list_is_not_a_bare_array(self):
        response = self.client.get(self.list_url())
        assert not isinstance(response.data, list)


class TestCohortV2Ordering(CohortV2TestCase):
    """Ordering follows the field-with-optional-minus convention."""

    def setUp(self):
        super().setUp()
        CohortFactory(course_id=self.course_key, name="Zulu")

    def test_default_ordering_is_by_name(self):
        response = self.client.get(self.list_url())
        names = [row["name"] for row in response.data["results"]]
        assert names == sorted(names)

    def test_descending_ordering(self):
        response = self.client.get(self.list_url(), {"ordering": "-name"})
        names = [row["name"] for row in response.data["results"]]
        assert names == sorted(names, reverse=True)

    def test_unsupported_ordering_field_is_rejected(self):
        response = self.client.get(self.list_url(), {"ordering": "secret"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestCohortV2Errors(CohortV2TestCase):
    """Errors carry the standardized envelope."""

    def test_missing_cohort_uses_envelope(self):
        response = self.client.get(self.detail_url(cohort_id=99999))
        assert response.status_code == status.HTTP_404_NOT_FOUND
        for field in ERROR_ENVELOPE_FIELDS:
            assert field in response.data

    def test_legacy_error_fields_are_absent(self):
        response = self.client.get(self.detail_url(cohort_id=99999))
        assert "developer_message" not in response.data
        assert "error_code" not in response.data

    def test_duplicate_cohort_name_is_rejected(self):
        response = self.client.post(
            self.list_url(),
            {"name": self.cohort.name, "assignment_type": "manual"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_group_id_without_partition_is_rejected(self):
        response = self.client.post(
            self.list_url(),
            {"name": "Beta", "assignment_type": "manual", "group_id": 1},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestCohortV1Deprecation(CohortV2TestCase):
    """v1 keeps working but advertises its successor."""

    def test_v1_cohort_list_sets_deprecation_header(self):
        url = reverse("api_cohorts:cohort_handler",
                      kwargs={"course_key_string": str(self.course_key)})
        response = self.client.get(url)
        assert response["Deprecation"] == "true"
        assert "successor-version" in response["Link"]

    def test_v1_still_serves(self):
        url = reverse("api_cohorts:cohort_handler",
                      kwargs={"course_key_string": str(self.course_key)})
        response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK
