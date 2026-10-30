"""Tests for the v4 legacy library list endpoint."""

from unittest.mock import patch

import pytest
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import Resolver404, resolve, reverse
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.testing import assert_error_envelope
from edx_toggles.toggles.testutils import override_waffle_flag
from opaque_keys.edx.locator import LibraryLocatorV2
from openedx_authz.models.engine import PolicyCacheControl
from openedx_content import api as content_api
from organizations.tests.factories import OrganizationFactory
from rest_framework.test import APIClient

from cms.djangoapps.contentstore.rest_api.v1.views.home import HomePageLibrariesView
from cms.djangoapps.contentstore.rest_api.v2.views.tests.helpers import (
    PASSWORD,
    SignalRecorder,
    generate_cms_schema,
    logged_in_client,
    resolve_ref,
    writes,
)
from cms.djangoapps.contentstore.rest_api.v3.views.home import HomeViewSet
from cms.djangoapps.contentstore.rest_api.v4.views.home import HomeCoursesViewSet
from cms.djangoapps.contentstore.rest_api.v4.views.legacy_libraries import (
    LegacyLibraryPagination,
    LegacyLibraryViewSet,
)
from cms.djangoapps.contentstore.tests.test_libraries import LibraryTestCase
from cms.djangoapps.contentstore.tests.utils import routed_actions
from cms.djangoapps.contentstore.toggles import DISABLE_LEGACY_LIBRARIES
from cms.djangoapps.course_creators.models import CourseCreator
from cms.djangoapps.modulestore_migrator import api as migrator_api
from cms.djangoapps.modulestore_migrator.data import CompositionLevel, RepeatHandlingStrategy
from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseLimitedStaffRole,
    CourseStaffRole,
    LibraryUserRole,
    OrgInstructorRole,
    OrgLibraryUserRole,
    OrgStaffRole,
)
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content_libraries import api as lib_api
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory

URL_NAME = "authoring_v4:legacy_library_list"
URL = "/api/authoring/v4/legacy_libraries/"
LEGACY_URL = "/api/authoring/v3/home/libraries/"
PATH = "/api/authoring/v4/legacy_libraries/"
PAGE_KEYS = {"count", "num_pages", "current_page", "start", "next", "previous", "results"}
# Queries for global staff listing the five fixture libraries. The legacy list
# is measured, not pinned: it also loads the caller's permissions and
# organizations for screen data it builds and discards, and must cost no less.
NEW_QUERIES = 14

# Responses of the legacy list differ from this one only as listed here, per
# case. Each listed path must differ, and nothing else may. The legacy list is
# sorted into this list's order and unwrapped from its "libraries" key first.
INTENTIONAL_DIFFERENCES = {
    "items": [],
    "unauthenticated": [],
    "invalid_is_migrated": [
        ("errors", "a parameter error is keyed by the parameter name"),
        ("detail", "the error library renders a keyed error as the text of the whole error dictionary"),
    ],
}

VOLATILE_FIELDS = {"instance"}


def _normalize(obj, path=""):
    """Sort keys, drop volatile fields, and collect a {json_path: value} map for diffing."""
    flat = {}
    if isinstance(obj, dict):
        for key in sorted(obj):
            if key in VOLATILE_FIELDS:
                continue
            flat.update(_normalize(obj[key], f"{path}.{key}" if path else key))
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            flat.update(_normalize(item, f"{path}[{index}]"))
    else:
        flat[path] = obj
    return flat


def _covers(declared, path):
    return path == declared or path.startswith(declared + ".") or path.startswith(declared + "[")


def _diff(legacy, new, intentional):
    """Return the changed json paths, and those of them no intentional difference covers."""
    old_flat, new_flat = _normalize(legacy), _normalize(new)
    changed = {
        key for key in set(old_flat) | set(new_flat)
        if old_flat.get(key, "<absent>") != new_flat.get(key, "<absent>")
    }
    unexpected = {key for key in changed if not any(_covers(path, key) for path, _ in intentional)}
    return changed, unexpected


def _list_order(row):
    return row["display_name"].casefold(), row["library_key"]


class LegacyLibraryTestBase(LibraryTestCase):
    """
    Five legacy libraries in two organizations, and the callers the tests need.

    In list order: ``lib3`` ("alpha", forwards to a content library without a
    collection), ``lib4`` ("Beta", organization ``otherorg``, not migrated),
    then three named "Test Library": ``lib`` (not migrated), ``lib1``
    (forwards into a collection) and ``lib2`` (migrated without forwarding, so
    not migrated as far as the list is concerned).
    """

    def setUp(self):
        super().setUp()
        self.lib1 = self._create_library(library="lib1")
        self.lib2 = self._create_library(library="lib2")
        self.lib3 = self._create_library(library="lib3", display_name="alpha")
        self.lib4 = self._create_library(org="otherorg", library="lib4", display_name="Beta")

        organization = OrganizationFactory()
        self.v2_key = LibraryLocatorV2.from_string(f"lib:{organization.short_name}:test-key")
        lib_api.create_library(org=organization, slug=self.v2_key.slug, title="Test Library")
        learning_package = lib_api.ContentLibrary.objects.get(slug=self.v2_key.slug).learning_package
        content_api.create_collection(
            learning_package_id=learning_package.id,
            collection_code="test-collection",
            title="Test Collection",
            created_by=self.user.id,
        )
        self._migrate(self.lib1, collection="test-collection", forward=True)
        self._migrate(self.lib2, collection="test-collection", forward=False)
        self._migrate(self.lib3, collection=None, forward=True)

        self.staff = UserFactory(is_staff=True, password=PASSWORD)
        # Role checks create the authorization engine's cache-version row on
        # first use; create it here so request-level measurements exclude it.
        PolicyCacheControl.get()

    def _migrate(self, source_key, collection, forward):
        migrator_api.start_migration_to_library(
            user=self.user,
            source_key=source_key,
            target_library_key=self.v2_key,
            target_collection_slug=collection,
            composition_level=CompositionLevel.Component,
            repeat_handling_strategy=RepeatHandlingStrategy.Skip,
            preserve_url_slugs=True,
            forward_source_to_target=forward,
        )

    def user_with(self, *roles, **fields):
        """Return a new user holding ``roles``."""
        user = UserFactory(password=PASSWORD, **fields)
        for role in roles:
            role.add_users(user)
        return user

    def row(self, library_key, display_name="Test Library", can_edit=True, collection=False):
        """
        Return the row the list shows for ``library_key``.

        ``collection`` is False for a library that is not migrated, None for
        one migrated without a collection, and True for one migrated into the
        fixture's collection.
        """
        row = {
            "display_name": display_name,
            "library_key": str(library_key),
            "url": f"/library/{library_key}",
            "org": library_key.org,
            "number": library_key.library,
            "can_edit": can_edit,
            "is_migrated": collection is not False,
        }
        if collection is not False:
            row.update({
                "migrated_to_key": str(self.v2_key),
                "migrated_to_title": "Test Library",
                "migrated_to_collection_key": "test-collection" if collection else None,
                "migrated_to_collection_title": "Test Collection" if collection else None,
            })
        return row

    def all_rows(self, can_edit=True):
        """Return every fixture library's row, in list order."""
        return [
            self.row(self.lib3, "alpha", can_edit, collection=None),
            self.row(self.lib4, "Beta", can_edit),
            self.row(self.lib_key, can_edit=can_edit),
            self.row(self.lib1, can_edit=can_edit, collection=True),
            self.row(self.lib2, can_edit=can_edit),
        ]

    def get(self, user, url=URL, method="get", **params):
        client = logged_in_client(user) if user is not None else APIClient()
        return getattr(client, method)(url, params)

    def keys(self, response):
        assert response.status_code == 200, response.content
        return [row["library_key"] for row in response.json()["results"]]

    def every_row(self, user, url=URL, **params):
        """Return the rows of every page of the list, fetched 100 at a time."""
        rows, page = [], 1
        while True:
            body = self.get(user, url, page=page, page_size=100, **params).json()
            rows.extend(body["results"])
            if body["next"] is None:
                return rows
            page += 1


class LegacyLibraryListContentTest(LegacyLibraryTestBase):
    """What the list returns to global staff."""

    def test_staff_get_every_legacy_library_in_list_order(self):
        response = self.get(self.staff)
        assert response.status_code == 200
        assert response.json() == {
            "count": 5,
            "num_pages": 1,
            "current_page": 1,
            "start": 0,
            "next": None,
            "previous": None,
            "results": self.all_rows(),
        }

    def test_migration_fields_appear_only_on_migrated_libraries(self):
        rows = {row["library_key"]: row for row in self.get(self.staff).json()["results"]}
        assert set(rows[str(self.lib_key)]) == {
            "display_name", "library_key", "url", "org", "number", "can_edit", "is_migrated",
        }
        assert rows[str(self.lib3)]["migrated_to_collection_key"] is None
        assert rows[str(self.lib3)]["migrated_to_collection_title"] is None
        assert rows[str(self.lib1)]["migrated_to_collection_key"] == "test-collection"

    def test_is_migrated_true_returns_only_forwarding_libraries(self):
        response = self.get(self.staff, is_migrated="true")
        assert response.json()["results"] == [
            self.row(self.lib3, "alpha", collection=None),
            self.row(self.lib1, collection=True),
        ]

    def test_is_migrated_false_returns_the_others(self):
        response = self.get(self.staff, is_migrated="false")
        assert response.json()["count"] == 3
        assert response.json()["results"] == [
            self.row(self.lib4, "Beta"),
            self.row(self.lib_key),
            self.row(self.lib2),
        ]

    def test_org_returns_only_that_organizations_libraries(self):
        assert self.keys(self.get(self.staff, org="otherorg")) == [str(self.lib4)]
        assert self.keys(self.get(self.staff, org="org")) == [
            str(self.lib3), str(self.lib_key), str(self.lib1), str(self.lib2),
        ]

    def test_libraries_sharing_a_display_name_are_ordered_by_key(self):
        """Created in reverse key order, so the store's order is not the list's."""
        lib9 = self._create_library(library="lib9", display_name="Same Name")
        lib8 = self._create_library(library="lib8", display_name="Same Name")
        assert self.keys(self.get(self.staff)) == [
            str(self.lib3), str(self.lib4), str(lib8), str(lib9), str(self.lib_key), str(self.lib1), str(self.lib2),
        ]

    def test_org_matches_the_key_exactly(self):
        assert self.keys(self.get(self.staff, org="ORG")) == []
        assert self.keys(self.get(self.staff, org="othe")) == []
        assert self.keys(self.get(self.staff, org="nosuchorg")) == []

    def test_org_and_is_migrated_combine(self):
        assert self.keys(self.get(self.staff, org="org", is_migrated="false")) == [
            str(self.lib_key), str(self.lib2),
        ]
        assert self.keys(self.get(self.staff, org="otherorg", is_migrated="true")) == []

    def test_head_answers_like_get_without_a_body(self):
        response = self.get(self.staff, method="head")
        assert response.status_code == 200
        assert response.content == b""


class LegacyLibraryListAccessTest(LegacyLibraryTestBase):
    """Who is refused, and which libraries each caller is shown."""

    def test_anonymous_is_refused_with_a_challenge(self):
        response = self.get(None)
        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")

    def test_a_user_without_roles_gets_an_empty_page(self):
        response = self.get(self.user_with())
        assert response.json()["count"] == 0
        assert response.json()["results"] == []

    def test_a_library_user_sees_only_that_library_read_only(self):
        response = self.get(self.user_with(LibraryUserRole(self.lib1)))
        assert response.json()["results"] == [self.row(self.lib1, can_edit=False, collection=True)]

    def test_an_org_library_user_sees_that_organizations_libraries_read_only(self):
        response = self.get(self.user_with(OrgLibraryUserRole("org")))
        assert self.keys(response) == [str(self.lib3), str(self.lib_key), str(self.lib1), str(self.lib2)]
        assert {row["can_edit"] for row in response.json()["results"]} == {False}

    def test_library_staff_and_instructors_see_their_library_with_edit_rights(self):
        for role, key in ((CourseStaffRole(self.lib2), self.lib2), (CourseInstructorRole(self.lib1), self.lib1)):
            response = self.get(self.user_with(role))
            assert self.keys(response) == [str(key)], role
            assert response.json()["results"][0]["can_edit"] is True, role

    def test_org_staff_and_instructors_see_that_organizations_libraries_with_edit_rights(self):
        for role in (OrgStaffRole("otherorg"), OrgInstructorRole("otherorg")):
            response = self.get(self.user_with(role))
            assert response.json()["results"] == [self.row(self.lib4, "Beta", can_edit=True)], role

    def test_limited_staff_are_hidden_even_when_they_hold_a_library_user_role(self):
        library_user = self.user_with(CourseLimitedStaffRole(self.lib1), LibraryUserRole(self.lib1))
        assert self.keys(self.get(library_user)) == []
        org_library_user = self.user_with(CourseLimitedStaffRole(self.lib1), OrgLibraryUserRole("org"))
        assert self.keys(self.get(org_library_user)) == [str(self.lib3), str(self.lib_key), str(self.lib2)]

    def test_another_users_library_never_appears_under_any_filter(self):
        user = self.user_with(LibraryUserRole(self.lib1))
        self.user_with(LibraryUserRole(self.lib2))
        for params in ({}, {"org": "org"}, {"is_migrated": "true"}, {"is_migrated": "false"}, {"org": "otherorg"}):
            assert str(self.lib2) not in self.keys(self.get(user, **params)), params

    def test_filters_only_narrow_what_the_caller_can_read(self):
        user = self.user_with(LibraryUserRole(self.lib1))
        assert self.keys(self.get(user, org="otherorg")) == []
        assert self.keys(self.get(user, is_migrated="false")) == []
        assert self.keys(self.get(user, is_migrated="true")) == [str(self.lib1)]
        assert self.keys(self.get(user, org="org", is_migrated="true")) == [str(self.lib1)]

    def test_an_inactive_author_is_authenticated_but_shown_nothing(self):
        inactive = self.user_with(LibraryUserRole(self.lib1), is_active=False)
        response = self.get(inactive)
        assert response.status_code == 200
        assert response.json()["count"] == 0

    def test_inactive_staff_are_authenticated_and_shown_every_library(self):
        inactive_staff = UserFactory(is_staff=True, is_active=False, password=PASSWORD)
        assert self.keys(self.get(inactive_staff)) == [row["library_key"] for row in self.all_rows()]

    def test_a_jwt_caller_gets_the_same_page_as_a_session_caller(self):
        response = APIClient().get(URL, HTTP_AUTHORIZATION=f"JWT {create_jwt_for_user(self.staff)}")
        assert response.status_code == 200
        assert response.json() == self.get(self.staff).json()

    def test_a_bearer_token_is_not_accepted(self):
        expected = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
        assert LegacyLibraryViewSet.authentication_classes == expected
        token = AccessTokenFactory(user=self.staff, application=ApplicationFactory()).token
        response = APIClient().get(URL, HTTP_AUTHORIZATION=f"Bearer {token}")
        assert_error_envelope(response, expected_status=401, expected_type_slug="authn")


class ContentLibrariesDisabledTest(LegacyLibraryTestBase):
    """With content libraries switched off, nobody is shown a legacy library."""

    def setUp(self):
        super().setUp()
        self.enterContext(override_settings(ENABLE_CONTENT_LIBRARIES=False))

    def test_staff_get_an_empty_page_as_on_the_legacy_list(self):
        for params in ({}, {"is_migrated": "true"}, {"org": "org"}):
            response = self.get(self.staff, **params)
            assert response.json()["count"] == 0, params
            assert self.get(self.staff, LEGACY_URL, **params).json() == {"libraries": []}, params


class LegacyLibrariesDisabledTest(LegacyLibraryTestBase):
    """With legacy libraries switched off, nobody is shown one."""

    def setUp(self):
        super().setUp()
        self.enterContext(override_waffle_flag(DISABLE_LEGACY_LIBRARIES, active=True))

    def test_staff_get_an_empty_page_as_on_the_legacy_list(self):
        for params in ({}, {"is_migrated": "false"}, {"org": "otherorg"}):
            response = self.get(self.staff, **params)
            assert response.json()["count"] == 0, params
            assert self.get(self.staff, LEGACY_URL, **params).json() == {"libraries": []}, params


class LegacyLibraryListErrorTest(LegacyLibraryTestBase):
    """How invalid requests are answered."""

    def test_an_invalid_is_migrated_is_a_validation_error_on_that_parameter(self):
        for value in ("maybe", "", "null"):
            response = self.get(self.staff, is_migrated=value)
            body = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
            assert body["errors"] == {"is_migrated": ["Must be a valid boolean."]}, value

    def test_an_empty_org_is_a_validation_error_on_that_parameter(self):
        response = self.get(self.staff, org="")
        body = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert body["errors"] == {"org": ["This field may not be blank."]}

    def test_a_page_past_the_end_or_not_a_number_is_not_found(self):
        for page in (999, "two"):
            response = self.get(self.staff, page=page)
            assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")

    def test_writes_are_not_allowed(self):
        """Unsupported methods get the envelope; the library has no error type of its own for them yet."""
        client = logged_in_client(self.staff)
        for method in ("post", "put", "patch", "delete"):
            response = getattr(client, method)(URL)
            assert_error_envelope(response, expected_status=405, expected_type_slug="internal")
            assert response["Content-Type"] == "application/json", method


class LegacyLibraryListPagesTest(LegacyLibraryTestBase):
    """How the list is cut into pages."""

    def setUp(self):
        super().setUp()
        for index in range(7):
            self._create_library(library=f"extra{index}", display_name=f"Extra {index}")

    def test_the_default_page_holds_ten_libraries(self):
        body = self.get(self.staff).json()
        assert set(body) == PAGE_KEYS
        assert (body["count"], body["num_pages"], body["current_page"], body["start"]) == (12, 2, 1, 0)
        assert len(body["results"]) == 10
        assert body["previous"] is None
        assert body["next"] == "http://testserver/api/authoring/v4/legacy_libraries/?page=2"

    def test_the_second_page_holds_the_rest(self):
        body = self.get(self.staff, page=2).json()
        assert (body["count"], body["num_pages"], body["current_page"], body["start"]) == (12, 2, 2, 10)
        assert len(body["results"]) == 2
        assert body["next"] is None
        assert body["previous"] == "http://testserver/api/authoring/v4/legacy_libraries/"

    def test_page_size_is_honoured_and_capped(self):
        assert (LegacyLibraryPagination.page_size, LegacyLibraryPagination.max_page_size) == (10, 100)
        body = self.get(self.staff, page_size=5, page=3).json()
        assert (body["num_pages"], body["start"], len(body["results"])) == (3, 10, 2)
        with patch.object(LegacyLibraryPagination, "max_page_size", 4):
            body = self.get(self.staff, page_size=1000).json()
        assert (body["num_pages"], len(body["results"])) == (3, 4)

    def test_the_order_is_the_same_whatever_the_page_size(self):
        whole = self.keys(self.get(self.staff, page_size=100))
        one_by_one = [self.keys(self.get(self.staff, page=page, page_size=1))[0] for page in range(1, 13)]
        assert one_by_one == whole
        expected = sorted(self.every_row(self.staff), key=_list_order)
        assert whole == [row["library_key"] for row in expected]
        assert whole[:2] == [str(self.lib3), str(self.lib4)]


class LegacyLibraryListParityTest(LegacyLibraryTestBase):
    """The new list returns what the legacy list returns, page by page, in a fixed order."""

    def assert_parity(self, legacy, new, case):
        """Assert that ``legacy`` and ``new`` differ exactly by the differences declared for ``case``."""
        intentional = INTENTIONAL_DIFFERENCES[case]
        changed, unexpected = _diff(legacy, new, intentional)
        assert not unexpected, f"unexpected differences: {sorted(unexpected)}\nlegacy={legacy}\nnew={new}"
        for path, reason in intentional:
            assert any(_covers(path, key) for key in changed), f"declared difference did not occur: {path} ({reason})"

    def legacy_rows(self, user, **params):
        response = self.get(user, LEGACY_URL, **params)
        assert response.status_code == 200, response.content
        return sorted(response.json()["libraries"], key=_list_order)

    def test_every_field_of_every_row_matches(self):
        compared = 0
        for user in (self.staff, self.user_with(LibraryUserRole(self.lib1)), self.user_with(OrgStaffRole("org"))):
            for params in ({}, {"is_migrated": "true"}, {"is_migrated": "false"}):
                legacy = self.legacy_rows(user, **params)
                new = self.every_row(user, **params)
                self.assert_parity(legacy, new, "items")
                compared += len(new)
        # Staff: 5 + 2 + 3 rows; the library user: 1 + 1 + 0; organization staff: 4 + 2 + 2.
        assert compared == 20

    def test_org_narrows_the_rows_the_legacy_list_returns(self):
        for org in ("org", "otherorg"):
            prefix = f"library-v1:{org}+"
            legacy = [row for row in self.legacy_rows(self.staff) if row["library_key"].startswith(prefix)]
            self.assert_parity(legacy, self.every_row(self.staff, org=org), "items")
        # The legacy list ignores the parameter.
        assert len(self.legacy_rows(self.staff, org="otherorg")) == 5

    def test_the_unauthenticated_envelopes_match(self):
        legacy, new = self.get(None, LEGACY_URL), self.get(None)
        assert legacy.status_code == new.status_code == 401
        self.assert_parity(legacy.json(), new.json(), "unauthenticated")

    def test_an_invalid_is_migrated_is_refused_by_both(self):
        for value in ("maybe", "", "null"):
            legacy = self.get(self.staff, LEGACY_URL, is_migrated=value)
            new = self.get(self.staff, is_migrated=value)
            assert legacy.status_code == new.status_code == 400, value
            self.assert_parity(legacy.json(), new.json(), "invalid_is_migrated")

    def test_the_harness_reports_a_changed_field(self):
        legacy = self.legacy_rows(self.staff)
        new = [dict(row) for row in self.every_row(self.staff)]
        new[0]["can_edit"] = not new[0]["can_edit"]
        _, unexpected = _diff(legacy, new, INTENTIONAL_DIFFERENCES["items"])
        assert unexpected == {"[0].can_edit"}


class LegacyLibraryListReadOnlyTest(LegacyLibraryTestBase):
    """
    Listing changes nothing, where the legacy list records the caller as a course creator.

    Course-creator approval is on, and the caller has never been recorded as a
    course creator: the legacy list adds them, this one does not.
    """

    def setUp(self):
        super().setUp()
        self.enterContext(override_settings(ENABLE_CREATOR_GROUP=True))
        self.author = self.user_with(LibraryUserRole(self.lib1))

    def test_get_and_head_write_nothing_and_send_no_signal(self):
        client = logged_in_client(self.author)
        for method in ("get", "head"):
            for params in ({}, {"is_migrated": "true"}, {"org": "org", "is_migrated": "false"}):
                with CaptureQueriesContext(connection) as queries, SignalRecorder() as signals:
                    response = getattr(client, method)(URL, params)
                assert response.status_code == 200, (method, params)
                assert writes(queries.captured_queries) == [], (method, params)
                assert signals.unexpected() == [], (method, params)
        assert not CourseCreator.objects.filter(user=self.author).exists()

    def test_the_legacy_list_does_write_for_the_same_caller(self):
        """The fixture reaches the write: the legacy list records the caller."""
        with CaptureQueriesContext(connection) as queries:
            response = logged_in_client(self.author).get(LEGACY_URL)
        assert response.status_code == 200
        assert CourseCreator.objects.get(user=self.author).state == CourseCreator.UNREQUESTED
        inserts = [sql for sql in writes(queries.captured_queries) if sql.startswith("INSERT INTO")]
        assert any('"course_creators_coursecreator"' in sql for sql in inserts)

    def test_a_jwt_request_writes_nothing(self):
        """The caller's first request is the one measured: the legacy write happens once per caller."""
        header = f"JWT {create_jwt_for_user(self.author)}"
        with CaptureQueriesContext(connection) as queries:
            response = APIClient().get(URL, HTTP_AUTHORIZATION=header)
        assert response.status_code == 200
        assert writes(queries.captured_queries) == []
        assert not CourseCreator.objects.filter(user=self.author).exists()


class LegacyLibraryListQueryCountTest(LegacyLibraryTestBase):
    """How many queries a page costs, against the legacy list."""

    def measure(self, client, url, params=None):
        client.get(url, params)
        with CaptureQueriesContext(connection) as queries:
            assert client.get(url, params).status_code == 200
        return len(queries.captured_queries)

    def test_the_first_page_costs_no_more_than_the_legacy_list(self):
        client = logged_in_client(self.staff)
        legacy = self.measure(client, LEGACY_URL)
        client.get(URL)
        with self.assertNumQueries(NEW_QUERIES, using="default"):
            assert client.get(URL).status_code == 200
        assert NEW_QUERIES <= legacy

    def test_a_page_reads_migrations_for_its_own_libraries_only(self):
        client = logged_in_client(self.staff)
        before = self.measure(client, URL, {"page_size": 1})
        self._create_library(library="lib5")
        assert self.measure(client, URL, {"page_size": 1}) == before

    def test_filtering_on_migration_reads_one_migration_per_visible_library(self):
        client = logged_in_client(self.staff)
        before = self.measure(client, URL, {"page_size": 1, "is_migrated": "true"})
        self._create_library(library="lib5")
        assert self.measure(client, URL, {"page_size": 1, "is_migrated": "true"}) == before + 1

    def test_visibility_costs_no_query_per_library_for_a_role_holder(self):
        client = logged_in_client(self.user_with(OrgLibraryUserRole("org")))
        before = self.measure(client, URL, {"page_size": 1})
        self._create_library(library="lib5")
        assert self.measure(client, URL, {"page_size": 1}) == before


class LegacyLibraryListUrlTest(LegacyLibraryTestBase):
    """The address and its name, beside the legacy addresses of the same list."""

    def test_reverse_literal(self):
        assert reverse(URL_NAME) == URL

    def test_the_address_resolves_to_the_list_action(self):
        match = resolve(URL)
        assert match.func.cls is LegacyLibraryViewSet
        assert routed_actions(match.func) == {"get": "list"}

    def test_the_slashless_address_does_not_resolve(self):
        with pytest.raises(Resolver404):
            resolve(URL.rstrip("/"))

    def test_the_neighbouring_course_list_is_unchanged(self):
        assert reverse("authoring_v4:course_list") == "/api/authoring/v4/courses/"
        assert resolve("/api/authoring/v4/courses/").func.cls is HomeCoursesViewSet

    def test_the_legacy_addresses_keep_their_names_and_views(self):
        assert reverse("cms.djangoapps.contentstore:v1:libraries") == "/api/contentstore/v1/home/libraries"
        assert reverse("cms.djangoapps.contentstore:v3:home-libraries") == "/api/contentstore/v3/home/libraries/"
        assert reverse("authoring_v3:home_libraries") == LEGACY_URL
        assert resolve("/api/contentstore/v1/home/libraries").func.view_class is HomePageLibrariesView
        assert resolve("/api/contentstore/v3/home/libraries/").func.cls is HomeViewSet
        assert resolve(LEGACY_URL).func.cls is HomeViewSet


class LegacyLibraryListSchemaTest(LibraryTestCase):
    """What the published schema says about the list and the addresses it supersedes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = generate_cms_schema()

    def operation(self, path=PATH):
        return self.schema["paths"][path]["get"]

    def test_the_operation_is_published_once_under_the_sdk_tag(self):
        assert set(self.schema["paths"][PATH]) == {"get"}
        operation = self.operation()
        assert operation["operationId"] == "v4_legacy_libraries_list"
        assert operation["tags"] == ["openedx-platform-sdk"]
        assert "deprecated" not in operation
        assert "x-internal" not in operation

    def test_the_query_parameters_are_published_with_their_descriptions(self):
        parameters = {parameter["name"]: parameter for parameter in self.operation()["parameters"]}
        assert set(parameters) == {"page", "page_size", "org", "is_migrated"}
        assert parameters["is_migrated"]["schema"] == {"type": "boolean"}
        assert parameters["org"]["schema"] == {"type": "string", "minLength": 1}
        assert {name: parameter["description"] for name, parameter in parameters.items()} == {
            "page": "A page number within the paginated result set.",
            "page_size": (
                "Number of libraries per page. Defaults to 10; a value above 100 returns 100 libraries per page."
            ),
            "org": (
                "Return only libraries whose key has exactly this organization, for example `OpenedX`. "
                "The match is case-sensitive: `openedx` does not match `OpenedX`."
            ),
            "is_migrated": (
                "`true` returns only libraries that have been migrated to a content library they now forward to. "
                "`false` returns all others, including libraries copied to a content library without forwarding. "
                "Omit it to return both."
            ),
        }

    def test_the_success_body_is_the_seven_key_page_of_legacy_libraries(self):
        body = self.operation()["responses"]["200"]["content"]["application/json"]["schema"]
        page = resolve_ref(self.schema, body)
        assert set(page["properties"]) == PAGE_KEYS
        assert set(page["required"]) == PAGE_KEYS
        assert page["properties"]["results"]["items"] == {"$ref": "#/components/schemas/LegacyLibrary"}
        assert page["properties"]["next"] == {
            "type": "string",
            "format": "uri",
            "nullable": True,
            "description": "Address of the next page, or null on the last page.",
        }
        assert page["properties"]["previous"] == {
            "type": "string",
            "format": "uri",
            "nullable": True,
            "description": "Address of the previous page, or null on the first page.",
        }

    def test_the_migration_fields_are_optional_and_the_collection_ones_nullable(self):
        library = self.schema["components"]["schemas"]["LegacyLibrary"]
        assert set(library["required"]) == {
            "display_name", "library_key", "url", "org", "number", "can_edit", "is_migrated",
        }
        assert library["properties"]["migrated_to_collection_key"]["nullable"] is True
        assert library["properties"]["migrated_to_collection_title"]["nullable"] is True
        assert "nullable" not in library["properties"]["migrated_to_key"]
        assert {name: field["description"] for name, field in library["properties"].items()} == {
            "display_name": "Display name of the library.",
            "library_key": "Key of the library, for example `library-v1:OpenedX+Lib1`.",
            "url": "Path of the library's page in Studio.",
            "org": "Organization part of the library key.",
            "number": "Library code part of the library key.",
            "can_edit": "Whether the requester can edit the library.",
            "is_migrated": "Whether the library has been migrated to a content library that it now forwards to.",
            "migrated_to_key": "Key of the content library the library was migrated to.",
            "migrated_to_title": "Title of the content library the library was migrated to.",
            "migrated_to_collection_key": "Key of the collection the library was migrated into, or null if none.",
            "migrated_to_collection_title": "Title of the collection the library was migrated into, or null if none.",
        }

    def test_each_error_is_documented_with_the_error_envelope(self):
        responses = self.operation()["responses"]
        assert set(responses) == {"200", "400", "401", "404"}
        for code in ("400", "401", "404"):
            schema = responses[code]["content"]["application/json"]["schema"]
            assert schema == {"$ref": "#/components/schemas/ErrorResponse"}, code

    def test_the_superseded_addresses_are_deprecated(self):
        assert self.operation("/api/contentstore/v1/home/libraries")["deprecated"] is True
        assert self.operation("/api/contentstore/v3/home/libraries/")["deprecated"] is True
        assert self.operation(LEGACY_URL)["deprecated"] is True
        assert self.operation(LEGACY_URL)["x-internal"] is True

    def test_the_other_home_operations_stay_current(self):
        for path in ("/api/authoring/v3/home/", "/api/authoring/v3/home/courses/", "/api/authoring/v4/courses/"):
            assert "deprecated" not in self.operation(path), path
