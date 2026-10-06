"""Tests for the v2 course textbooks endpoint."""

from pathlib import Path
from unittest.mock import patch

import ddt
from ccx_keys.locator import CCXLocator
from django.db import connection
from django.db.models import signals as model_signals
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from edx_rest_framework_extensions.testing import assert_error_envelope
from openedx_authz.constants.roles import COURSE_AUDITOR, COURSE_LIMITED_STAFF, COURSE_STAFF
from openedx_authz.models.engine import PolicyCacheControl
from rest_framework.test import APIClient

from cms.djangoapps.contentstore.rest_api.v1.views.textbooks import CourseTextbooksView
from cms.djangoapps.contentstore.rest_api.v2.views.tests.helpers import (
    PASSWORD,
    SignalRecorder,
    generate_cms_schema,
    logged_in_client,
    resolve_ref,
    writes,
)
from cms.djangoapps.contentstore.rest_api.v2.views.textbooks import CourseTextbooksViewSet
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.lib.spectacular import cms_api_filter
from common.djangoapps.student.roles import CourseInstructorRole, CourseLimitedStaffRole, CourseStaffRole
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory
from xmodule.modulestore.tests.factories import CourseFactory

URL_NAME = "authoring_v2:course_textbook_list"
LEGACY_URL_NAME = "cms.djangoapps.contentstore:v1:textbooks"
PAGE_KEYS = {"count", "num_pages", "current_page", "start", "next", "previous", "results"}
MISSING_COURSE_KEY = "course-v1:NoSuch+Course+Run"


def _textbook(index, chapter_count=1):
    """Return a stored textbook with ``chapter_count`` chapters."""
    return {
        "id": f"{index}Book",
        "tab_title": f"Book {index}",
        "chapters": [{"title": f"Chapter {n}", "url": f"/static/book{index}_{n}.pdf"} for n in range(chapter_count)],
    }


class TextbooksTestBase(CourseTestCase):
    """A course with textbooks and the callers the tests need."""

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

    def set_textbooks(self, textbooks):
        self.course.pdf_textbooks = textbooks
        self.save_course()

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
class CourseTextbooksAccessTest(TextbooksTestBase):
    """Who may list a course's textbooks, with course-authoring policy disabled."""

    def setUp(self):
        super().setUp()
        self.set_textbooks([_textbook(1)])

    @ddt.data("course_staff", "course_instructor", "global_staff", "superuser")
    def test_allowed_callers_get_the_textbooks(self, caller):
        for method in ("get", "head"):
            response = self.get(getattr(self, caller), method=method)
            assert response.status_code == 200, (caller, method)
        response = self.get(getattr(self, caller))
        assert response.data["results"] == [_textbook(1)]
        assert list(response.data["results"][0]) == ["id", "chapters", "tab_title"]

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
        assert response.data["detail"] == "The requested course does not exist."

    def test_a_missing_course_is_refused_not_revealed_to_a_caller_without_access(self):
        response = self.get(self.no_role_user, course_key=MISSING_COURSE_KEY)
        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    def test_the_legacy_route_reveals_a_missing_course_to_a_caller_without_access(self):
        response = self.get_legacy(self.no_role_user, course_key=MISSING_COURSE_KEY)
        assert response.status_code == 404
        assert response.data["error_code"] == "course_does_not_exist"

    def test_a_ccx_course_is_refused_even_to_global_staff(self):
        ccx_key = CCXLocator.from_course_locator(self.course.id, "1")
        response = self.get(self.global_staff, course_key=ccx_key)
        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")

    def test_an_inactive_course_author_is_authenticated_then_refused_as_on_legacy(self):
        inactive = UserFactory(is_active=False, password=PASSWORD)
        CourseStaffRole(self.course.id).add_users(inactive)
        assert_error_envelope(self.get(inactive), expected_status=403, expected_type_slug="authz")
        assert self.get_legacy(inactive).status_code == 403

    def test_a_jwt_caller_is_served(self):
        client = APIClient()
        response = client.get(self.url(), HTTP_AUTHORIZATION=f"JWT {create_jwt_for_user(self.course_staff)}")
        assert response.status_code == 200

    def test_a_bearer_token_is_not_accepted(self):
        token = AccessTokenFactory(user=self.course_staff, application=ApplicationFactory()).token
        response = APIClient().get(self.url(), HTTP_AUTHORIZATION=f"Bearer {token}")
        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")

    def test_the_legacy_route_still_accepts_a_bearer_token(self):
        token = AccessTokenFactory(user=self.course_staff, application=ApplicationFactory()).token
        response = APIClient().get(self.legacy_url(), HTTP_AUTHORIZATION=f"Bearer {token}")
        assert response.status_code == 200


class CourseTextbooksAuthzEnabledTest(CourseAuthoringAuthzTestMixin, TextbooksTestBase):
    """Who may list a course's textbooks when course-authoring policy decides."""

    def setUp(self):
        super().setUp()
        self.set_textbooks([_textbook(1)])

    def _assert_both_routes(self, user, expected_status):
        new = self.get(user)
        assert new.status_code == expected_status
        assert self.get_legacy(user).status_code == expected_status
        return new

    def test_policy_staff_and_auditor_are_served(self):
        for role in (COURSE_STAFF, COURSE_AUDITOR):
            user = UserFactory(password=PASSWORD)
            self.add_user_to_role_in_course(user, role.external_key, self.course.id)
            response = self._assert_both_routes(user, 200)
            assert response.data["results"] == [_textbook(1)]

    def test_policy_limited_staff_is_refused(self):
        user = UserFactory(password=PASSWORD)
        self.add_user_to_role_in_course(user, COURSE_LIMITED_STAFF.external_key, self.course.id)
        assert_error_envelope(self._assert_both_routes(user, 403), expected_status=403, expected_type_slug="authz")

    def test_a_policy_role_on_another_course_is_refused(self):
        user = UserFactory(password=PASSWORD)
        self.add_user_to_role_in_course(user, COURSE_STAFF.external_key, self.other_course.id)
        self._assert_both_routes(user, 403)

    def test_a_course_role_granted_while_policy_decides_is_honoured(self):
        self._assert_both_routes(self.course_staff, 200)

    def test_an_inactive_caller_with_a_policy_role_is_served_through_a_session(self):
        inactive = UserFactory(is_active=False, password=PASSWORD)
        self.add_user_to_role_in_course(inactive, COURSE_STAFF.external_key, self.course.id)
        self._assert_both_routes(inactive, 200)

    def test_a_caller_with_no_role_is_refused(self):
        self._assert_both_routes(self.no_role_user, 403)

    def test_a_superuser_is_served_and_sees_not_found_for_a_missing_course(self):
        self._assert_both_routes(self.superuser, 200)
        response = self.get(self.superuser, course_key=MISSING_COURSE_KEY)
        assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")

    def test_a_missing_course_is_refused_to_a_caller_without_access(self):
        response = self.get(self.no_role_user, course_key=MISSING_COURSE_KEY)
        assert_error_envelope(response, expected_status=403, expected_type_slug="authz")


class CourseTextbooksParityTest(TextbooksTestBase):
    """The textbooks match the legacy endpoint's, spread over standard pages."""

    def _all_pages(self, user=None, page_size=None):
        """Follow the pages to the end and return every result, checking each envelope."""
        user = user or self.course_staff
        results, page = [], 1
        while True:
            params = {"page": page}
            if page_size:
                params["page_size"] = page_size
            response = self.get(user, **params)
            assert response.status_code == 200
            assert set(response.data) == PAGE_KEYS
            results.extend(response.data["results"])
            if response.data["next"] is None:
                return results
            page += 1

    def _assert_parity(self, textbooks):
        """Store ``textbooks``; assert both routes return them identically, in the same key order."""
        self.set_textbooks(textbooks)
        legacy = self.get_legacy(self.course_staff)
        assert legacy.status_code == 200
        new_results = self._all_pages()
        assert new_results == legacy.data["textbooks"]
        for new_item, legacy_item in zip(new_results, legacy.data["textbooks"], strict=True):
            assert list(new_item) == list(legacy_item)
        return new_results

    def test_no_textbooks(self):
        self.set_textbooks([])
        response = self.get(self.course_staff)
        assert response.data == {
            "count": 0, "num_pages": 1, "current_page": 1, "start": 0,
            "next": None, "previous": None, "results": [],
        }
        assert self.get_legacy(self.course_staff).data == {"textbooks": []}

    def test_one_textbook(self):
        assert self._assert_parity([_textbook(1)]) == [_textbook(1)]

    def test_textbooks_with_one_none_and_five_chapters(self):
        textbooks = [_textbook(1, 1), _textbook(2, 0), _textbook(3, 5)]
        assert self._assert_parity(textbooks) == textbooks

    def test_undeclared_stored_keys_are_dropped_on_both_routes(self):
        stored = [{"id": "1T", "tab_title": "T", "x": 2, "chapters": [{"title": "c", "url": "/u", "extra": 1}]}]
        expected = [{"id": "1T", "chapters": [{"title": "c", "url": "/u"}], "tab_title": "T"}]
        assert self._assert_parity(stored) == expected

    def test_twelve_textbooks_are_paged_in_stored_order(self):
        textbooks = [_textbook(i) for i in range(12)]
        self.set_textbooks(textbooks)
        first = self.get(self.course_staff)
        assert first.data["count"] == 12
        assert first.data["num_pages"] == 2
        assert first.data["current_page"] == 1
        assert first.data["start"] == 0
        assert first.data["previous"] is None
        assert first.data["next"] == f"http://testserver{self.url()}?page=2"
        assert first.data["results"] == textbooks[:10]
        second = self.get(self.course_staff, page=2)
        assert second.data["current_page"] == 2
        assert second.data["start"] == 10
        assert second.data["next"] is None
        assert second.data["results"] == textbooks[10:]
        assert self._assert_parity(textbooks) == textbooks

    def test_page_size_is_honoured_and_capped(self):
        textbooks = [_textbook(i) for i in range(101)]
        self.set_textbooks(textbooks)
        assert len(self.get(self.course_staff, page_size=3).data["results"]) == 3
        assert len(self.get(self.course_staff, page_size=100).data["results"]) == 100
        capped = self.get(self.course_staff, page_size=500)
        assert len(capped.data["results"]) == 100
        assert capped.data["num_pages"] == 2

    def test_a_page_past_the_end_is_not_found(self):
        self.set_textbooks([_textbook(1)])
        response = self.get(self.course_staff, page=99)
        assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")


@ddt.ddt
class CourseTextbooksIncompleteRecordTest(TextbooksTestBase):
    """Stored textbooks missing a field are served instead of failing the page."""

    def _new_and_legacy(self, textbooks):
        """Store ``textbooks``; return the new response and the legacy one, letting legacy fail with a 500."""
        self.set_textbooks(textbooks)
        client = Client(raise_request_exception=False)
        assert client.login(username=self.course_staff.username, password=PASSWORD)
        legacy = client.get(self.legacy_url())
        return self.get(self.course_staff), legacy

    def test_a_textbook_without_an_id(self):
        new, legacy = self._new_and_legacy([{"tab_title": "T", "chapters": [{"title": "c", "url": "/u"}]}])
        assert legacy.status_code == 500
        assert new.status_code == 200
        assert new.data["results"] == [{"id": None, "chapters": [{"title": "c", "url": "/u"}], "tab_title": "T"}]

    def test_a_single_file_textbook_without_chapters(self):
        new, legacy = self._new_and_legacy([{"tab_title": "T", "url": "/static/book.pdf"}])
        assert legacy.status_code == 500
        assert new.data["results"] == [{"id": None, "chapters": [], "tab_title": "T"}]

    def test_a_textbook_without_a_tab_title(self):
        new, legacy = self._new_and_legacy([{"id": "1T", "chapters": []}])
        assert legacy.status_code == 500
        assert new.data["results"] == [{"id": "1T", "chapters": [], "tab_title": None}]

    def test_a_null_chapter_list_is_returned_empty(self):
        new, _ = self._new_and_legacy([{"id": "1T", "chapters": None, "tab_title": "T"}])
        assert new.status_code == 200
        assert new.data["results"] == [{"id": "1T", "chapters": [], "tab_title": "T"}]

    @ddt.data(
        ({"title": "c"}, {"title": "c", "url": ""}),
        ({"url": "/u"}, {"title": "", "url": "/u"}),
        ({"title": None, "url": None}, {"title": "", "url": ""}),
    )
    @ddt.unpack
    def test_a_chapter_missing_a_field_is_returned_with_an_empty_string(self, stored, expected):
        new, _ = self._new_and_legacy([{"id": "1T", "chapters": [stored], "tab_title": "T"}])
        assert new.status_code == 200
        assert new.data["results"] == [{"id": "1T", "chapters": [expected], "tab_title": "T"}]

    def test_one_incomplete_textbook_does_not_hide_the_others(self):
        good = _textbook(1, 2)
        new, legacy = self._new_and_legacy([good, {"tab_title": "Broken"}])
        assert legacy.status_code == 500
        assert new.data["results"] == [good, {"id": None, "chapters": [], "tab_title": "Broken"}]
        self.set_textbooks([good])
        assert self.get_legacy(self.course_staff).data["textbooks"] == [new.data["results"][0]]

    def test_a_course_missing_from_the_modulestore_is_not_found(self):
        with patch("xmodule.modulestore.mixed.MixedModuleStore.get_course", return_value=None):
            response = self.get(self.course_staff)
            client = Client(raise_request_exception=False)
            assert client.login(username=self.course_staff.username, password=PASSWORD)
            legacy = client.get(self.legacy_url())
        assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
        assert legacy.status_code == 500


class CourseTextbooksReadOnlyTest(TextbooksTestBase):
    """Listing textbooks changes nothing, with course-authoring policy disabled."""

    def test_get_and_head_write_nothing_and_send_no_signal(self):
        self.set_textbooks([_textbook(1)])
        client = logged_in_client(self.course_staff)
        for method in ("get", "head"):
            with CaptureQueriesContext(connection) as queries, SignalRecorder() as signals:
                response = getattr(client, method)(self.url())
            assert response.status_code == 200
            assert writes(queries.captured_queries) == [], method
            assert signals.unexpected() == [], method

    def test_the_recorder_sees_a_signal_when_one_is_sent(self):
        """The harness above would notice a signal: a model save inside it is recorded."""
        with CaptureQueriesContext(connection) as queries, SignalRecorder() as signals:
            UserFactory(password=PASSWORD)
        assert writes(queries.captured_queries) != []
        assert any(signal is model_signals.post_save for _, signal in signals.unexpected())

    def test_a_jwt_request_writes_nothing(self):
        self.set_textbooks([_textbook(1)])
        header = f"JWT {create_jwt_for_user(self.course_staff)}"
        with CaptureQueriesContext(connection) as queries:
            response = APIClient().get(self.url(), HTTP_AUTHORIZATION=header)
        assert response.status_code == 200
        assert writes(queries.captured_queries) == []


@ddt.ddt
class AuthorizationCacheInitialisationTest(TextbooksTestBase):
    """
    The one write either route can make: the authorization engine's cache-version row.

    Role checks read that singleton row through ``get_or_create``, so the first
    request against a database without it creates it. Every later request only
    reads it.
    """

    @ddt.data("legacy_url", "url")
    def test_only_the_first_request_creates_the_cache_version_row(self, url_method):
        self.set_textbooks([_textbook(1)])
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


class CourseTextbooksReadOnlyAuthzEnabledTest(CourseAuthoringAuthzTestMixin, TextbooksTestBase):
    """Listing textbooks changes nothing when course-authoring policy decides."""

    def test_get_and_head_write_nothing_and_send_no_signal(self):
        self.set_textbooks([_textbook(1)])
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
class CourseTextbooksQueryCountTest(TextbooksTestBase):
    """The new route issues no more queries than the legacy one."""

    @ddt.data((0, 15, 13), (3, 15, 13))
    @ddt.unpack
    def test_query_count(self, textbook_count, legacy_queries, new_queries):
        self.set_textbooks([_textbook(i, 2) for i in range(textbook_count)])
        client = logged_in_client(self.course_staff)
        # One request to each route first, so per-process caches are warm for both.
        client.get(self.legacy_url())
        client.get(self.url())
        with self.assertNumQueries(legacy_queries, using="default"):
            assert client.get(self.legacy_url()).status_code == 200
        with self.assertNumQueries(new_queries, using="default"):
            assert client.get(self.url()).status_code == 200


class CourseTextbooksUrlTest(TextbooksTestBase):
    """The address, its name, and how malformed requests are answered."""

    def test_reverse_literal(self):
        key = "course-v1:edX+Demo+2026"
        assert reverse(URL_NAME, kwargs={"course_key": key}) == f"/api/authoring/v2/courses/{key}/textbooks/"

    def test_each_address_resolves_to_its_own_view(self):
        assert resolve(self.url()).func.cls is CourseTextbooksViewSet
        assert resolve(self.legacy_url()).func.view_class is CourseTextbooksView
        assert self.legacy_url() == f"/api/contentstore/v1/textbooks/{self.course.id}"

    def test_malformed_and_deprecated_keys_do_not_route(self):
        client = logged_in_client(self.global_staff)
        for key in ("not-a-key", "edX/DemoX/Demo"):
            response = client.get(f"/api/authoring/v2/courses/{key}/textbooks/")
            assert response.status_code == 404, key
            # The service's HTML not-found page answers here: no JSON catch-all
            # exists under this mount.
            assert response["Content-Type"].startswith("text/html"), key

    def test_the_legacy_route_still_serves_a_deprecated_key(self):
        assert resolve("/api/contentstore/v1/textbooks/edX/DemoX/Demo").func.view_class is CourseTextbooksView

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

    def test_an_html_only_client_is_answered_not_acceptable_in_json(self):
        client = logged_in_client(self.global_staff)
        response = client.get(self.url(), HTTP_ACCEPT="text/html")
        assert_error_envelope(response, expected_status=406)
        assert response["Content-Type"] == "application/json"


TEXTBOOKS_PATH = "/api/authoring/v2/courses/{course_key}/textbooks/"
LEGACY_TEXTBOOKS_PATH = "/api/contentstore/v1/textbooks/{course_id}"


class CourseTextbooksSchemaTest(CourseTestCase):
    """What the published schema says about the endpoint."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = generate_cms_schema()

    def _operation(self):
        return self.schema["paths"][TEXTBOOKS_PATH]["get"]

    def test_the_path_is_published_at_its_full_address(self):
        assert set(self.schema["paths"][TEXTBOOKS_PATH]) == {"get"}
        assert self._operation()["tags"] == ["openedx-platform-sdk"]

    def test_the_success_body_is_the_seven_key_page_of_textbooks(self):
        body = self._operation()["responses"]["200"]["content"]["application/json"]["schema"]
        page = resolve_ref(self.schema, body)
        assert set(page["properties"]) == PAGE_KEYS
        assert set(page["required"]) == PAGE_KEYS
        items = page["properties"]["results"]["items"]
        assert items == {"$ref": "#/components/schemas/CourseTextbook"}
        assert page["properties"]["next"]["nullable"] is True

    def test_missing_scalars_are_published_as_nullable(self):
        textbook = self.schema["components"]["schemas"]["CourseTextbook"]
        assert textbook["properties"]["id"]["nullable"] is True
        assert textbook["properties"]["tab_title"]["nullable"] is True
        assert textbook["properties"]["chapters"]["items"] == {"$ref": "#/components/schemas/TextbookChapter"}
        assert "required" not in textbook

    def test_page_parameters_are_published(self):
        names = {p["name"] for p in self._operation()["parameters"]}
        assert names == {"course_key", "page", "page_size"}

    def test_every_error_references_the_shared_envelope(self):
        responses = self._operation()["responses"]
        assert set(responses) == {"200", "401", "403", "404"}
        for code in ("401", "403", "404"):
            assert responses[code]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorResponse"
            }, code

    def test_the_legacy_operation_is_unchanged(self):
        operation = self.schema["paths"][LEGACY_TEXTBOOKS_PATH]["get"]
        assert operation["operationId"] == "v1_textbooks_retrieve"
        assert "deprecated" not in operation


class CmsSchemaFilterTest(CourseTestCase):
    """Which mounts the CMS schema admits."""

    @staticmethod
    def _admitted(path):
        return bool(cms_api_filter([(path, path, "GET", object())]))

    def test_both_api_mounts_are_admitted(self):
        assert self._admitted("/api/authoring/v2/courses/course-v1:a+b+c/textbooks/")
        assert self._admitted("/api/contentstore/v1/textbooks/course-v1:a+b+c")
        assert self._admitted("/api/courses/course-v1:a+b+c/bulk_enable_disable_discussions")

    def test_unrelated_mounts_are_refused(self):
        assert not self._admitted("/api/user/v1/accounts/")
        assert not self._admitted("/api/authoring/courses/")
        assert not self._admitted("/authoring/v2/courses/")


class SpectacularSettingsTest(CourseTestCase):
    """The schema settings of the environments that publish the CMS schema."""

    SETTINGS_MODULES = ("cms/envs/devstack.py", "cms/envs/production.py")

    def _source(self, module):
        return (Path(__file__).resolve().parents[7] / module).read_text()

    def test_no_prefix_is_trimmed_so_paths_are_emitted_in_full(self):
        for module in self.SETTINGS_MODULES:
            source = self._source(module)
            assert "SCHEMA_PATH_PREFIX_TRIM" not in source, module
            assert "'SCHEMA_PATH_PREFIX': r'/api/(contentstore|authoring)'," in source, module

    def test_no_server_advertises_a_single_mount_as_its_root(self):
        for module in self.SETTINGS_MODULES:
            assert "CMS-contentstore" not in self._source(module), module
