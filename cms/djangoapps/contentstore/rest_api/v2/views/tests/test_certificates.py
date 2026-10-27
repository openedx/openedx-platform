"""Tests for the v2 course certificate settings endpoint."""

from types import SimpleNamespace
from unittest.mock import patch

import ddt
from ccx_keys.locator import CCXLocator
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import NoReverseMatch, resolve, reverse
from edx_rest_framework_extensions.testing import assert_error_envelope
from edx_toggles.toggles.testutils import override_waffle_flag
from openedx_authz.constants.roles import COURSE_AUDITOR, COURSE_EDITOR, COURSE_LIMITED_STAFF, COURSE_STAFF
from openedx_authz.models.engine import PolicyCacheControl
from rest_framework.test import APIClient

from cms.djangoapps.contentstore.rest_api.v1.views.certificates import CourseCertificatesView
from cms.djangoapps.contentstore.rest_api.v2.serializers.certificates import (
    CertificateSettingsSerializer,
    ConfiguredCertificateSerializer,
    ConfiguredCertificateSignatorySerializer,
)
from cms.djangoapps.contentstore.rest_api.v2.views.certificates import CourseCertificateSettingsViewSet
from cms.djangoapps.contentstore.rest_api.v2.views.tests.helpers import (
    PASSWORD,
    SignalRecorder,
    generate_cms_schema,
    logged_in_client,
    resolve_ref,
    writes,
)
from cms.djangoapps.contentstore.tests.utils import CourseTestCase, routed_actions
from cms.djangoapps.contentstore.toggles import LEGACY_STUDIO_EXAM_SETTINGS
from cms.djangoapps.contentstore.views.permissions import CanViewCertificates
from cms.djangoapps.contentstore.views.tests.test_certificates import HelperMethods
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.roles import CourseInstructorRole, CourseLimitedStaffRole, CourseStaffRole
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory
from xmodule.modulestore.tests.factories import CourseFactory

URL_NAME = "authoring_v2:course_certificate_settings"
LEGACY_URL_NAME = "cms.djangoapps.contentstore:v1:certificates"
MISSING_COURSE_KEY = "course-v1:NoSuch+Course+Run"
VERSION_ONLY_COURSE_KEY = "course-v1:version@5f1e2d3c4b5a69788796a5b4"
RESPONSE_KEYS = {
    "certificate_activation_handler_url",
    "certificate_web_view_url",
    "certificates",
    "course_modes",
    "has_certificate_modes",
    "is_active",
    "is_global_staff",
    "mfe_proctored_exam_settings_url",
    "course_number",
    "course_title",
    "course_number_override",
    "can_manage",
}


class CertificateSettingsTestBase(CourseTestCase, HelperMethods):
    """A course and the callers the tests need."""

    def setUp(self):
        super().setUp()
        self.api_client = APIClient()
        self.other_course = CourseFactory.create(org="OtherX", course="Other", run="Run")

        self.no_role_user = UserFactory(password=PASSWORD)
        self.course_staff = UserFactory(password=PASSWORD)
        CourseStaffRole(self.course.id).add_users(self.course_staff)
        self.course_instructor = UserFactory(password=PASSWORD)
        CourseInstructorRole(self.course.id).add_users(self.course_instructor)
        self.limited_staff = UserFactory(password=PASSWORD)
        CourseLimitedStaffRole(self.course.id).add_users(self.limited_staff)
        self.other_course_staff = UserFactory(password=PASSWORD)
        CourseStaffRole(self.other_course.id).add_users(self.other_course_staff)
        self.other_course_instructor = UserFactory(password=PASSWORD)
        CourseInstructorRole(self.other_course.id).add_users(self.other_course_instructor)
        self.global_staff = UserFactory(is_staff=True, password=PASSWORD)
        self.superuser = UserFactory(is_superuser=True, password=PASSWORD)
        # Role checks create the authorization engine's cache-version row on
        # first use; create it here so request-level measurements exclude it.
        PolicyCacheControl.get()

    def url(self, course_key=None):
        return reverse(URL_NAME, kwargs={"course_key": str(course_key or self.course.id)})

    def legacy_url(self, course_key=None):
        return reverse(LEGACY_URL_NAME, kwargs={"course_id": str(course_key or self.course.id)})

    def get(self, user, course_key=None, method="get", **params):
        client = logged_in_client(user) if user is not None else APIClient()
        return getattr(client, method)(self.url(course_key), params)

    def get_legacy(self, user, course_key=None):
        client = logged_in_client(user) if user is not None else APIClient()
        return client.get(self.legacy_url(course_key))


@ddt.ddt
class CourseCertificateSettingsAccessTest(CertificateSettingsTestBase):
    """Who may read a course's certificate settings, with course-authoring policy disabled."""

    def setUp(self):
        super().setUp()
        self._add_course_certificates(count=1, signatory_count=1)

    @ddt.data("course_staff", "course_instructor", "global_staff", "superuser")
    def test_allowed_callers_get_the_settings(self, caller):
        for method in ("get", "head"):
            response = self.get(getattr(self, caller), method=method)
            assert response.status_code == 200, (caller, method)
        response = self.get(getattr(self, caller))
        assert set(response.data) == RESPONSE_KEYS
        assert response.data["course_title"] == self.course.display_name_with_default
        assert response.data["can_manage"] is True

    def test_anonymous_is_refused_with_a_challenge(self):
        response = self.get(None)
        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")
        assert response["WWW-Authenticate"] == 'JWT realm="api"'

    @ddt.data("no_role_user", "limited_staff", "other_course_staff", "other_course_instructor")
    def test_callers_without_access_to_this_course_are_refused(self, caller):
        for method in ("get", "head"):
            response = self.get(getattr(self, caller), method=method)
            assert response.status_code == 403, (caller, method)
        assert_error_envelope(self.get(getattr(self, caller)), expected_status=403, expected_type_slug="authz")

    def test_the_legacy_route_refuses_the_same_callers(self):
        for caller in ("no_role_user", "limited_staff", "other_course_staff", "other_course_instructor"):
            assert self.get_legacy(getattr(self, caller)).status_code == 403, caller
        for caller in ("course_staff", "course_instructor", "global_staff", "superuser"):
            assert self.get_legacy(getattr(self, caller)).status_code == 200, caller

    def test_a_missing_course_is_not_found_for_a_privileged_caller(self):
        response = self.get(self.global_staff, course_key=MISSING_COURSE_KEY)
        assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
        assert self.get_legacy(self.global_staff, course_key=MISSING_COURSE_KEY).status_code == 404

    def test_a_missing_course_is_refused_not_revealed_to_a_caller_without_access(self):
        response = self.get(self.no_role_user, course_key=MISSING_COURSE_KEY)
        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    def test_the_legacy_route_reveals_a_missing_course_to_a_caller_without_access(self):
        response = self.get_legacy(self.no_role_user, course_key=MISSING_COURSE_KEY)
        assert response.status_code == 404
        assert response.data["error_code"] == "course_does_not_exist"

    def test_a_ccx_course_is_refused_even_to_global_staff(self):
        ccx_key = CCXLocator.from_course_locator(self.course.id, "1")
        assert_error_envelope(
            self.get(self.global_staff, course_key=ccx_key), expected_status=403, expected_type_slug="authz",
        )
        assert self.get_legacy(self.global_staff, course_key=ccx_key).status_code == 403

    def test_an_inactive_course_author_is_not_authenticated(self):
        inactive = UserFactory(is_active=False, password=PASSWORD)
        CourseStaffRole(self.course.id).add_users(inactive)
        assert_error_envelope(self.get(inactive), expected_status=401, expected_type_slug="authn")

    def test_a_jwt_caller_is_served(self):
        response = self.api_client.get(self.url(), HTTP_AUTHORIZATION=f"JWT {create_jwt_for_user(self.course_staff)}")
        assert response.status_code == 200

    def test_a_bearer_token_is_not_accepted(self):
        token = AccessTokenFactory(user=self.course_staff, application=ApplicationFactory()).token
        response = self.api_client.get(self.url(), HTTP_AUTHORIZATION=f"Bearer {token}")
        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")

    def test_the_legacy_route_still_accepts_a_bearer_token(self):
        token = AccessTokenFactory(user=self.course_staff, application=ApplicationFactory()).token
        response = self.api_client.get(self.legacy_url(), HTTP_AUTHORIZATION=f"Bearer {token}")
        assert response.status_code == 200

    def test_a_course_missing_from_the_modulestore_is_not_found(self):
        with patch("xmodule.modulestore.mixed.MixedModuleStore.get_course", return_value=None):
            response = self.get(self.global_staff)
        assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
        assert response.data["detail"] == "The requested course does not exist."


class CourseCertificateSettingsAuthzEnabledTest(CourseAuthoringAuthzTestMixin, CertificateSettingsTestBase):
    """Who may read a course's certificate settings when course-authoring policy decides."""

    def setUp(self):
        super().setUp()
        self._add_course_certificates(count=1, signatory_count=1)

    def _policy_user(self, role, course_key=None):
        user = UserFactory(password=PASSWORD)
        self.add_user_to_role_in_course(user, role.external_key, course_key or self.course.id)
        return user

    def _assert_both_routes(self, user, expected_status):
        new = self.get(user)
        assert new.status_code == expected_status
        assert self.get_legacy(user).status_code == expected_status
        return new

    def test_course_staff_is_served_and_may_manage(self):
        response = self._assert_both_routes(self._policy_user(COURSE_STAFF), 200)
        assert response.data["can_manage"] is True

    def test_course_editor_and_auditor_are_served_and_may_not_manage(self):
        for role in (COURSE_EDITOR, COURSE_AUDITOR):
            response = self._assert_both_routes(self._policy_user(role), 200)
            assert response.data["can_manage"] is False, role

    def test_policy_limited_staff_is_refused(self):
        response = self._assert_both_routes(self._policy_user(COURSE_LIMITED_STAFF), 403)
        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    def test_a_policy_role_on_another_course_is_refused(self):
        self._assert_both_routes(self._policy_user(COURSE_STAFF, self.other_course.id), 403)

    def test_a_caller_with_no_role_is_refused(self):
        response = self._assert_both_routes(self.no_role_user, 403)
        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    def test_a_course_role_granted_while_policy_decides_is_honoured_as_on_the_legacy_route(self):
        response = self._assert_both_routes(self.course_staff, 200)
        assert response.data["can_manage"] is True

    def test_a_superuser_is_served_and_sees_not_found_for_a_missing_course(self):
        self._assert_both_routes(self.superuser, 200)
        response = self.get(self.superuser, course_key=MISSING_COURSE_KEY)
        assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")

    def test_a_missing_course_is_refused_to_a_caller_without_access(self):
        response = self.get(self.no_role_user, course_key=MISSING_COURSE_KEY)
        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    def test_a_version_only_course_key_is_not_found_for_any_caller(self):
        for user in (self.no_role_user, self.superuser, self._policy_user(COURSE_STAFF)):
            response = self.get(user, course_key=VERSION_ONLY_COURSE_KEY)
            assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")


class CourseCertificateSettingsParityTest(CertificateSettingsTestBase):
    """The default body matches the legacy endpoint's, value for value."""

    def _assert_parity(self, user=None):
        """Assert both routes answer the same body for ``user``; return it."""
        user = user or self.course_staff
        new = self.get(user)
        legacy = self.get_legacy(user)
        assert new.status_code == legacy.status_code == 200
        assert new.data == legacy.data
        assert list(new.data) == list(legacy.data)
        return new.data

    def test_two_certificates_of_two_signatories(self):
        self._add_course_certificates(count=2, signatory_count=2)
        for user in (self.course_staff, self.global_staff):
            data = self._assert_parity(user)
            assert len(data["certificates"]) == 2
            assert [len(c["signatories"]) for c in data["certificates"]] == [2, 2]
            assert data["is_global_staff"] is (user is self.global_staff)

    def test_no_certificates(self):
        data = self._assert_parity()
        assert data["certificates"] == []
        assert data["is_active"] is False

    def test_an_active_certificate(self):
        self._add_course_certificates(count=1, signatory_count=1, is_active=True)
        assert self._assert_parity()["is_active"] is True

    def test_a_course_without_a_mode_that_awards_certificates(self):
        data = self._assert_parity()
        assert data["course_modes"] == []
        assert data["has_certificate_modes"] is False
        assert data["certificate_web_view_url"] is None

    def test_a_course_with_a_mode_that_awards_certificates(self):
        CourseModeFactory.create(course_id=self.course.id, mode_slug="verified", mode_display_name="Verified")
        data = self._assert_parity()
        assert data["course_modes"] == ["verified"]
        assert data["has_certificate_modes"] is True
        assert data["certificate_web_view_url"].endswith(f"/certificates/course/{self.course.id}?preview=verified")

    def test_the_two_studio_addresses_are_the_legacy_routes(self):
        data = self._assert_parity()
        assert data["certificate_activation_handler_url"] == f"/certificates/activation/{self.course.id}/"

    def test_a_course_number_override_left_at_its_default_is_empty(self):
        assert self.course.display_coursenumber == ""
        assert self._assert_parity()["course_number_override"] == ""

    def test_a_course_number_override_stored_as_null_is_null(self):
        self.course.display_coursenumber = None
        self.save_course()
        assert self._assert_parity()["course_number_override"] is None

    def test_a_set_course_number_override(self):
        self.course.display_coursenumber = "Shown Number"
        self.save_course()
        assert self._assert_parity()["course_number_override"] == "Shown Number"

    def test_the_course_identity_fields(self):
        data = self._assert_parity()
        assert data["course_number"] == self.course.number
        assert data["course_title"] == self.course.display_name_with_default

    def test_a_signatory_organization_is_returned_when_stored(self):
        self._add_course_certificates(count=1, signatory_count=1)
        self.course.certificates["certificates"][0]["signatories"][0]["organization"] = "OrgX"
        self.save_course()
        data = self._assert_parity()
        assert data["certificates"][0]["signatories"][0]["organization"] == "OrgX"

    def test_an_optional_certificate_course_title_and_editing_flag_are_returned_when_stored(self):
        self._add_course_certificates(count=1, signatory_count=0)
        self.course.certificates["certificates"][0].update({"course_title": "Override", "editing": True})
        self.save_course()
        certificate = self._assert_parity()["certificates"][0]
        assert certificate["course_title"] == "Override"
        assert certificate["editing"] is True

    def test_undeclared_stored_keys_are_dropped_on_both_routes(self):
        self._add_course_certificates(count=1, signatory_count=1)
        self.course.certificates["certificates"][0]["extra"] = "x"
        self.course.certificates["certificates"][0]["signatories"][0]["extra"] = "y"
        self.save_course()
        certificate = self._assert_parity()["certificates"][0]
        assert "extra" not in certificate
        assert "extra" not in certificate["signatories"][0]

    @override_waffle_flag(LEGACY_STUDIO_EXAM_SETTINGS, active=False)
    @override_settings(COURSE_AUTHORING_MICROFRONTEND_URL="https://authoring.example.com")
    def test_the_proctored_exam_settings_address_with_the_new_page_and_an_authoring_app(self):
        data = self._assert_parity()
        assert data["mfe_proctored_exam_settings_url"] == (
            f"/course/{self.course.id}/pages-and-resources/proctoring/settings"
        )

    @override_waffle_flag(LEGACY_STUDIO_EXAM_SETTINGS, active=False)
    @override_settings(COURSE_AUTHORING_MICROFRONTEND_URL="")
    def test_the_proctored_exam_settings_address_without_an_authoring_app(self):
        assert self._assert_parity()["mfe_proctored_exam_settings_url"] == ""

    @override_waffle_flag(LEGACY_STUDIO_EXAM_SETTINGS, active=True)
    @override_settings(COURSE_AUTHORING_MICROFRONTEND_URL="https://authoring.example.com")
    def test_the_proctored_exam_settings_address_with_the_legacy_page(self):
        assert self._assert_parity()["mfe_proctored_exam_settings_url"] == ""

    def test_the_query_string_does_not_change_the_body(self):
        self._add_course_certificates(count=1, signatory_count=1)
        plain = self.get(self.course_staff)
        for params in ({"view": "minimal"}, {"fields": "course_title"}, {"page": 2}, {"page_size": 1}):
            assert self.get(self.course_staff, **params).data == plain.data, params


@ddt.ddt
class CourseCertificateSettingsIncompleteRecordTest(CertificateSettingsTestBase):
    """Stored certificates missing a field are served instead of failing the response."""

    def _new_and_legacy(self):
        """Return the new response and the legacy one, letting legacy fail with a 500."""
        client = APIClient(raise_request_exception=False)
        assert client.login(username=self.course_staff.username, password=PASSWORD)
        return self.get(self.course_staff), client.get(self.legacy_url())

    def _stored_certificate(self):
        self._add_course_certificates(count=1, signatory_count=1)
        return self.course.certificates["certificates"][0]

    def test_a_complete_stored_certificate_is_served_by_both_routes(self):
        self._stored_certificate()
        new, legacy = self._new_and_legacy()
        assert new.status_code == legacy.status_code == 200

    @ddt.data(
        ("description", None),
        ("id", None),
        ("is_active", False),
        ("name", None),
        ("signatories", []),
        ("version", None),
    )
    @ddt.unpack
    def test_a_certificate_missing_a_field(self, key, expected):
        del self._stored_certificate()[key]
        self.save_course()
        new, legacy = self._new_and_legacy()
        assert legacy.status_code == 500
        assert new.status_code == 200
        assert new.data["certificates"][0][key] == expected

    @ddt.data("id", "name", "signature_image_path", "title")
    def test_a_signatory_missing_a_field(self, key):
        del self._stored_certificate()["signatories"][0][key]
        self.save_course()
        new, legacy = self._new_and_legacy()
        assert legacy.status_code == 500
        assert new.status_code == 200
        assert new.data["certificates"][0]["signatories"][0][key] is None

    def test_a_certificate_with_a_null_signatory_list_is_served_by_both_routes(self):
        self._stored_certificate()["signatories"] = None
        self.save_course()
        new, legacy = self._new_and_legacy()
        assert new.status_code == legacy.status_code == 200
        assert new.data == legacy.data
        assert new.data["certificates"][0]["signatories"] is None

    def test_one_incomplete_certificate_does_not_hide_the_others(self):
        self._add_course_certificates(count=2, signatory_count=1)
        del self.course.certificates["certificates"][1]["name"]
        self.save_course()
        new, _ = self._new_and_legacy()
        assert [c["name"] for c in new.data["certificates"]] == ["Name 0", None]


class CourseCertificateSettingsFailureTest(CertificateSettingsTestBase):
    """A failure the endpoint cannot recover from."""

    def test_an_unrecoverable_failure_is_an_internal_error_with_a_generic_body(self):
        client = APIClient(raise_request_exception=False)
        assert client.login(username=self.course_staff.username, password=PASSWORD)
        with patch(
            "cms.djangoapps.contentstore.utils.reverse_course_url",
            side_effect=NoReverseMatch("Reverse for 'certificates_list_handler' not found"),
        ):
            response = client.get(self.url())
        assert_error_envelope(response, expected_status=500, expected_type_slug="internal")
        assert "certificates_list_handler" not in response.content.decode()
        assert "NoReverseMatch" not in response.content.decode()


class CourseCertificateSettingsReadOnlyTest(CertificateSettingsTestBase):
    """Reading the settings changes nothing, with course-authoring policy disabled."""

    def test_get_and_head_write_nothing_and_send_no_signal(self):
        self._add_course_certificates(count=2, signatory_count=2)
        client = logged_in_client(self.course_staff)
        for method in ("get", "head"):
            with CaptureQueriesContext(connection) as queries, SignalRecorder() as signals:
                response = getattr(client, method)(self.url())
            assert response.status_code == 200
            assert writes(queries.captured_queries) == [], method
            assert signals.unexpected() == [], method

    def test_the_course_overview_is_not_saved(self):
        self._add_course_certificates(count=1, signatory_count=1)
        client = logged_in_client(self.course_staff)
        with patch.object(CourseOverview, "save") as save, patch.object(CourseOverview, "get_from_id") as get_from_id:
            assert client.get(self.url()).status_code == 200
        save.assert_not_called()
        get_from_id.assert_not_called()

    def test_the_recorder_sees_a_signal_when_one_is_sent(self):
        """The harness above would notice a signal: a model save inside it is recorded."""
        with CaptureQueriesContext(connection) as queries, SignalRecorder() as signals:
            UserFactory(password=PASSWORD)
        assert writes(queries.captured_queries) != []
        assert signals.unexpected() != []

    def test_a_jwt_request_writes_nothing(self):
        header = f"JWT {create_jwt_for_user(self.course_staff)}"
        with CaptureQueriesContext(connection) as queries:
            response = self.api_client.get(self.url(), HTTP_AUTHORIZATION=header)
        assert response.status_code == 200
        assert writes(queries.captured_queries) == []


@ddt.ddt
class AuthorizationCacheInitialisationTest(CertificateSettingsTestBase):
    """
    The one write either route can make: the authorization engine's cache-version row.

    Role checks read that singleton row through ``get_or_create``, so the first
    request against a database without it creates it. Every later request only
    reads it.
    """

    @ddt.data("legacy_url", "url")
    def test_only_the_first_request_creates_the_cache_version_row(self, url_method):
        PolicyCacheControl.objects.all().delete()
        client = logged_in_client(self.course_staff)
        with CaptureQueriesContext(connection) as first:
            assert client.get(getattr(self, url_method)()).status_code == 200
        first_writes = writes(first.captured_queries)
        assert len(first_writes) == 1
        assert first_writes[0].startswith('INSERT INTO "openedx_authz_policycachecontrol"')
        with CaptureQueriesContext(connection) as second:
            assert client.get(getattr(self, url_method)()).status_code == 200
        assert writes(second.captured_queries) == []


class CourseCertificateSettingsReadOnlyAuthzEnabledTest(CourseAuthoringAuthzTestMixin, CertificateSettingsTestBase):
    """Reading the settings changes nothing when course-authoring policy decides."""

    def test_get_and_head_write_nothing_and_send_no_signal(self):
        self._add_course_certificates(count=2, signatory_count=2)
        user = UserFactory(password=PASSWORD)
        self.add_user_to_role_in_course(user, COURSE_STAFF.external_key, self.course.id)
        client = logged_in_client(user)
        for method in ("get", "head"):
            with CaptureQueriesContext(connection) as queries, SignalRecorder() as signals:
                response = getattr(client, method)(self.url())
            assert response.status_code == 200
            assert writes(queries.captured_queries) == [], method
            assert signals.unexpected() == [], method


@ddt.ddt
class CourseCertificateSettingsQueryCountTest(CertificateSettingsTestBase):
    """The new route issues no more queries than the legacy one."""

    @ddt.data((0, 0, 19, 19), (3, 0, 19, 19), (3, 1, 19, 19))
    @ddt.unpack
    def test_query_count(self, certificate_count, mode_count, legacy_queries, new_queries):
        self._add_course_certificates(count=certificate_count, signatory_count=2)
        for _ in range(mode_count):
            CourseModeFactory.create(course_id=self.course.id, mode_slug="verified")
        client = logged_in_client(self.course_staff)
        # One request to each route first, so per-process caches are warm for both.
        client.get(self.legacy_url())
        client.get(self.url())
        with self.assertNumQueries(legacy_queries, using="default"):
            assert client.get(self.legacy_url()).status_code == 200
        with self.assertNumQueries(new_queries, using="default"):
            assert client.get(self.url()).status_code == 200


class CourseCertificateSettingsUrlTest(CertificateSettingsTestBase):
    """The address, its name, and how malformed requests are answered."""

    def test_reverse_literal(self):
        key = "course-v1:edX+Demo+2026"
        assert reverse(URL_NAME, kwargs={"course_key": key}) == f"/api/authoring/v2/courses/{key}/certificate_settings/"

    def test_the_legacy_name_and_address_still_resolve(self):
        assert self.legacy_url() == f"/api/contentstore/v1/certificates/{self.course.id}"
        assert resolve(self.legacy_url()).func.view_class is CourseCertificatesView

    def test_the_address_is_routed_to_the_retrieve_action_only(self):
        match = resolve(self.url())
        assert match.func.cls is CourseCertificateSettingsViewSet
        assert routed_actions(match.func) == {"get": "retrieve"}

    def test_malformed_and_deprecated_keys_do_not_route(self):
        client = logged_in_client(self.global_staff)
        for key in ("not-a-key", "edX/DemoX/Demo"):
            response = client.get(f"/api/authoring/v2/courses/{key}/certificate_settings/")
            assert response.status_code == 404, key
            # The service's HTML not-found page answers here: no JSON catch-all
            # exists under this mount.
            assert response["Content-Type"].startswith("text/html"), key

    def test_a_version_only_course_key_is_not_found_for_any_caller(self):
        for user in (self.global_staff, self.no_role_user, self.course_staff):
            response = self.get(user, course_key=VERSION_ONLY_COURSE_KEY)
            assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
            assert response.data["detail"] == "The requested course does not exist."

    def test_the_permission_is_refused_without_a_course_key_in_the_path(self):
        request = SimpleNamespace(user=self.global_staff)
        assert CanViewCertificates().has_permission(request, SimpleNamespace(kwargs={})) is False

    def test_the_legacy_route_still_serves_a_deprecated_key(self):
        match = resolve("/api/contentstore/v1/certificates/edX/DemoX/Demo")
        assert match.func.view_class is CourseCertificatesView

    def test_a_slashless_address_redirects(self):
        client = logged_in_client(self.global_staff)
        response = client.get(self.url().rstrip("/"))
        assert response.status_code == 301
        assert response["Location"] == self.url()

    def test_writes_are_not_allowed(self):
        client = logged_in_client(self.global_staff)
        for method in ("post", "put", "patch", "delete"):
            response = getattr(client, method)(self.url())
            assert_error_envelope(response, expected_status=405)
            assert response["Content-Type"] == "application/json"

    def test_options_lists_only_the_read_methods(self):
        response = logged_in_client(self.global_staff).options(self.url())
        assert response.status_code == 200
        assert set(response["Allow"].replace(" ", "").split(",")) == {"GET", "HEAD", "OPTIONS"}

    def test_an_html_only_client_is_answered_not_acceptable_in_json(self):
        client = logged_in_client(self.global_staff)
        response = client.get(self.url(), HTTP_ACCEPT="text/html")
        assert_error_envelope(response, expected_status=406)
        assert response["Content-Type"] == "application/json"


CERTIFICATE_SETTINGS_PATH = "/api/authoring/v2/courses/{course_key}/certificate_settings/"
LEGACY_CERTIFICATES_PATH = "/api/contentstore/v1/certificates/{course_id}"


class CourseCertificateSettingsSchemaTest(CourseTestCase):
    """What the published schema says about the endpoint."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = generate_cms_schema()

    def _operation(self):
        return self.schema["paths"][CERTIFICATE_SETTINGS_PATH]["get"]

    def test_the_path_is_published_at_its_full_address(self):
        assert set(self.schema["paths"][CERTIFICATE_SETTINGS_PATH]) == {"get"}
        operation = self._operation()
        assert operation["tags"] == ["openedx-platform-sdk"]
        assert operation["operationId"] == "v2_courses_certificate_settings_retrieve"
        assert "x-internal" not in operation
        assert "deprecated" not in operation

    def test_the_success_body_is_the_certificate_settings_component(self):
        body = self._operation()["responses"]["200"]["content"]["application/json"]["schema"]
        assert body == {"$ref": "#/components/schemas/CertificateSettings"}
        settings = resolve_ref(self.schema, body)
        assert set(settings["properties"]) == RESPONSE_KEYS
        assert settings["properties"]["certificates"]["items"] == {
            "$ref": "#/components/schemas/ConfiguredCertificate"
        }
        certificate = self.schema["components"]["schemas"]["ConfiguredCertificate"]
        assert certificate["properties"]["signatories"]["items"] == {
            "$ref": "#/components/schemas/ConfiguredCertificateSignatory"
        }

    def test_nullable_fields_are_published_as_nullable(self):
        properties = self.schema["components"]["schemas"]["CertificateSettings"]["properties"]
        for name in ("certificate_web_view_url", "certificates", "mfe_proctored_exam_settings_url",
                     "course_number_override"):
            assert properties[name]["nullable"] is True, name
        assert "nullable" not in properties["course_title"]

    def test_every_error_references_the_shared_envelope(self):
        responses = self._operation()["responses"]
        assert set(responses) == {"200", "401", "403", "404"}
        for code in ("401", "403", "404"):
            assert responses[code]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorResponse"
            }, code

    def test_the_path_parameter_is_the_course_key(self):
        assert [p["name"] for p in self._operation()["parameters"]] == ["course_key"]

    def test_the_legacy_operation_keeps_its_id_and_is_marked_deprecated(self):
        operation = self.schema["paths"][LEGACY_CERTIFICATES_PATH]["get"]
        assert operation["operationId"] == "v1_certificates_retrieve"
        assert operation["deprecated"] is True

    def test_other_operations_are_not_marked_deprecated(self):
        for path in (CERTIFICATE_SETTINGS_PATH, "/api/contentstore/v1/group_configurations/{course_id}"):
            assert "deprecated" not in self.schema["paths"][path]["get"], path

    def test_the_legacy_components_keep_their_names(self):
        components = self.schema["components"]["schemas"]
        for name in ("CourseCertificates", "CertificateItem", "CertificateSignatory"):
            assert name in components, name

    def test_every_serializer_field_documents_itself(self):
        for serializer in (
            CertificateSettingsSerializer,
            ConfiguredCertificateSerializer,
            ConfiguredCertificateSignatorySerializer,
        ):
            for name, field in serializer().fields.items():
                assert field.help_text, (serializer.__name__, name)
