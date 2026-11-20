"""
Tests for the AuthZ enforcement of the Cohorts tab endpoints: CohortSettings,
CohortHandler, CohortUsers, CohortCSV, and the legacy course_cohort_settings_handler.
"""

import tempfile
from unittest.mock import patch

from django.urls import reverse
from openedx_authz.authz import COURSE_ADMIN, COURSE_STAFF
from rest_framework import status
from rest_framework.test import APIClient

from common.djangoapps.student.tests.factories import InstructorFactory, UserFactory
from lms.djangoapps.instructor.authz.tests.mixins import InstructorAuthzTestMixin
from openedx.core.djangoapps.course_groups import cohorts
from openedx.core.djangoapps.course_groups.tests.helpers import CohortFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

API = "lms.djangoapps.instructor.authz.api"


@skip_unless_lms
class CohortsAuthzTest(InstructorAuthzTestMixin, SharedModuleStoreTestCase):
    """
    AuthZ enforcement across every endpoint behind the Cohorts tab.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.course_key = cls.course.id

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.cohort = CohortFactory(course_id=self.course_key)

    def _url(self, name, **kwargs):
        return reverse(f"api_cohorts:{name}", kwargs={"course_key_string": str(self.course_key), **kwargs})

    # (name, kwargs, http method, request kwargs)
    VIEW_REQUESTS = (
        ("cohort_settings", {}, "get", {}),
        ("cohort_handler", {}, "get", {}),
        ("cohort_users", {"cohort_id": None}, "get", {}),
    )
    MANAGE_REQUESTS = (
        ("cohort_settings", {}, "put", {"data": {"is_cohorted": True}, "format": "json"}),
        (
            "cohort_handler",
            {},
            "post",
            {"data": {"name": "New", "assignment_type": "manual"}, "format": "json"},
        ),
        ("cohort_users", {"cohort_id": None}, "post", {"data": {"users": ["nonexistent_user"]}, "format": "json"}),
    )

    def _request(self, user, name, url_kwargs, method, request_kwargs):
        if "cohort_id" in url_kwargs and url_kwargs["cohort_id"] is None:
            url_kwargs = {**url_kwargs, "cohort_id": self.cohort.id}
        self.client.force_authenticate(user=user)
        return getattr(self.client, method)(self._url(name, **url_kwargs), **request_kwargs)

    def test_course_admin_can_view_and_manage(self):
        """A Course Admin can use every GET and write endpoint."""
        user = self.create_user_with_authz_role(COURSE_ADMIN.external_key, self.course_key)

        for name, url_kwargs, method, request_kwargs in (*self.VIEW_REQUESTS, *self.MANAGE_REQUESTS):
            response = self._request(user, name, url_kwargs, method, request_kwargs)
            assert response.status_code < 300, (name, method, response.status_code, response.data)

    def test_course_staff_can_view_and_manage(self):
        """A Course Staff can use every GET and write endpoint."""
        user = self.create_user_with_authz_role(COURSE_STAFF.external_key, self.course_key)

        for name, url_kwargs, method, request_kwargs in (*self.VIEW_REQUESTS, *self.MANAGE_REQUESTS):
            response = self._request(user, name, url_kwargs, method, request_kwargs)
            assert response.status_code < 300, (name, method, response.status_code, response.data)

    def test_authz_role_without_legacy_equivalent_can_view_and_manage(self):
        """With the flag on, AuthZ alone decides: no legacy role (or compat projection) is needed."""
        user = self.create_user_with_authz_role(COURSE_STAFF.external_key, self.course_key)

        with patch(
            "common.djangoapps.student.roles.get_authz_compat_course_access_roles_for_user",
            return_value=set(),
        ):
            for name, url_kwargs, method, request_kwargs in (*self.VIEW_REQUESTS, *self.MANAGE_REQUESTS):
                response = self._request(user, name, url_kwargs, method, request_kwargs)
                assert response.status_code < 300, (name, method, response.status_code, response.data)

    def test_user_without_permission_is_denied(self):
        """A user with no AuthZ role in the course is denied every endpoint."""
        user = UserFactory()

        for name, url_kwargs, method, request_kwargs in (*self.VIEW_REQUESTS, *self.MANAGE_REQUESTS):
            response = self._request(user, name, url_kwargs, method, request_kwargs)
            assert response.status_code == status.HTTP_403_FORBIDDEN, (name, method)

    def test_authz_denied_overrides_legacy_roles(self):
        """With the flag on, AuthZ denying a legacy instructor means no access."""
        instructor = InstructorFactory(course_key=self.course_key)

        for name, url_kwargs, method, request_kwargs in (*self.VIEW_REQUESTS, *self.MANAGE_REQUESTS):
            with self.subTest(name=name, method=method):
                with patch(f"{API}.authz_api.is_user_allowed", return_value=False):
                    response = self._request(instructor, name, url_kwargs, method, request_kwargs)
                assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_legacy_instructor_keeps_access(self):
        """With the flag off, a legacy instructor keeps the view/manage access it always had."""
        self.set_authz_enabled(False)
        instructor = InstructorFactory(course_key=self.course_key)

        for name, url_kwargs, method, request_kwargs in (*self.VIEW_REQUESTS, *self.MANAGE_REQUESTS):
            response = self._request(instructor, name, url_kwargs, method, request_kwargs)
            assert response.status_code < 300, (name, method, response.status_code, response.data)

    def test_legacy_student_is_denied(self):
        """With the flag off, a user with no legacy role is denied every endpoint."""
        self.set_authz_enabled(False)
        user = UserFactory()

        for name, url_kwargs, method, request_kwargs in (*self.VIEW_REQUESTS, *self.MANAGE_REQUESTS):
            response = self._request(user, name, url_kwargs, method, request_kwargs)
            assert response.status_code == status.HTTP_403_FORBIDDEN, (name, method)

    def test_legacy_does_not_query_authz(self):
        """With the flag off, AuthZ is not queried."""
        self.set_authz_enabled(False)
        instructor = InstructorFactory(course_key=self.course_key)

        with patch(f"{API}.authz_api.is_user_allowed") as is_user_allowed:
            response = self._request(instructor, "cohort_settings", {}, "get", {})

        assert response.status_code == status.HTTP_200_OK
        is_user_allowed.assert_not_called()


@skip_unless_lms
class CohortSettingsHandlerAuthzTest(InstructorAuthzTestMixin, SharedModuleStoreTestCase):
    """
    AuthZ enforcement of the legacy ``course_cohort_settings_handler`` view
    (``courses/{course_id}/cohorts/settings``), still used by Studio.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.course_key = cls.course.id

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.url = reverse("course_cohort_settings", kwargs={"course_key_string": str(self.course_key)})

    def _get(self, user):
        # This is a plain Django view (not DRF), so force_authenticate doesn't apply:
        # @login_required needs an actual logged-in session.
        self.client.login(username=user.username, password="Password1234")
        return self.client.get(self.url)

    def _patch(self, user):
        self.client.login(username=user.username, password="Password1234")
        return self.client.patch(self.url, data={"is_cohorted": True}, format="json")

    def test_course_admin_can_view_and_manage(self):
        user = self.create_user_with_authz_role(COURSE_ADMIN.external_key, self.course_key)

        assert self._get(user).status_code == status.HTTP_200_OK
        assert self._patch(user).status_code == status.HTTP_200_OK

    def test_user_without_permission_is_denied(self):
        user = UserFactory()

        assert self._get(user).status_code == status.HTTP_404_NOT_FOUND
        assert self._patch(user).status_code == status.HTTP_404_NOT_FOUND

    def test_legacy_staff_keeps_access(self):
        """With the flag off, course staff keeps the access it always had (404 if not staff)."""
        self.set_authz_enabled(False)
        instructor = InstructorFactory(course_key=self.course_key)
        student = UserFactory()

        assert self._get(instructor).status_code == status.HTTP_200_OK
        assert self._get(student).status_code == status.HTTP_404_NOT_FOUND


@skip_unless_lms
class CohortCSVAuthzTest(InstructorAuthzTestMixin, SharedModuleStoreTestCase):
    """
    AuthZ enforcement of ``CohortCSV``. Its legacy fallback was never gated on course
    staff access like the rest of the cohorts endpoints, only on Django's own
    ``user.is_staff``, so that specific check is preserved rather than reused.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.course_key = cls.course.id

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        cohorts.add_cohort(self.course_key, "DEFAULT", "random")

    def _post_csv(self, user):
        """Upload a minimal valid cohorts CSV as ``user`` and return the response."""
        self.client.force_authenticate(user=user)
        __, file_name = tempfile.mkstemp(suffix=".csv", dir=tempfile.mkdtemp())
        with open(file_name, "w") as file_pointer:
            file_pointer.write("email,cohort\nstudent@example.com,DEFAULT")
        url = reverse("api_cohorts:cohort_users_csv", kwargs={"course_key_string": str(self.course_key)})
        with open(file_name) as file_pointer:
            return self.client.post(url, data={"uploaded-file": file_pointer})

    def test_course_admin_can_upload(self):
        user = self.create_user_with_authz_role(COURSE_ADMIN.external_key, self.course_key)

        assert self._post_csv(user).status_code == status.HTTP_204_NO_CONTENT

    def test_user_without_permission_is_denied(self):
        user = UserFactory()

        assert self._post_csv(user).status_code == status.HTTP_403_FORBIDDEN

    def test_legacy_django_staff_keeps_access_course_staff_does_not(self):
        """
        With the flag off, this endpoint still only grants Django-staff users, unlike
        every other cohorts endpoint (which grants any course staff/instructor).
        """
        self.set_authz_enabled(False)
        django_staff = UserFactory(is_staff=True)
        course_instructor = InstructorFactory(course_key=self.course_key)

        assert self._post_csv(django_staff).status_code == status.HTTP_204_NO_CONTENT
        assert self._post_csv(course_instructor).status_code == status.HTTP_403_FORBIDDEN
