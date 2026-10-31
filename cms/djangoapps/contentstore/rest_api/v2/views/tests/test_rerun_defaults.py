"""Tests for the v2 course rerun defaults API."""

import json
from itertools import product
from types import SimpleNamespace
from unittest.mock import patch

import ddt
import pytest
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.test.utils import override_settings
from django.urls import Resolver404, resolve, reverse
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.permissions import NotJwtRestrictedApplication
from edx_rest_framework_extensions.testing import assert_error_envelope
from openedx_authz.constants.roles import COURSE_ADMIN, COURSE_AUDITOR, COURSE_EDITOR, COURSE_STAFF
from openedx_authz.models.engine import PolicyCacheControl
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.test import APIClient

from cms.djangoapps.contentstore.rest_api.v1.views.tests.helpers import (
    FIRST_LOOKUP_SIGNALS,
    FIRST_LOOKUP_WRITES,
    UNKNOWN_COURSE_KEY,
    VERSION_ONLY_KEY,
    api_client,
    assert_matches_published_schema,
    assert_refused,
    changed_paths,
    measure,
    published_schema,
    record_effects,
    undeclared_paths,
)
from cms.djangoapps.contentstore.rest_api.v2.views.rerun_defaults import CourseRerunDefaultsViewSet
from cms.djangoapps.contentstore.rest_api.v2.views.tests.helpers import PASSWORD, logged_in_client
from cms.djangoapps.contentstore.tests.utils import routed_actions
from cms.djangoapps.contentstore.views import course as course_views
from cms.djangoapps.contentstore.views.permissions import IsExistingCourse
from cms.djangoapps.course_creators.models import CourseCreator
from cms.lib.spectacular import LEGACY_MIGRATED_PATH_PREFIXES, SUPERSEDED_PATHS
from common.djangoapps.student.models import CourseAccessRole
from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseLimitedStaffRole,
    CourseStaffRole,
    GlobalStaff,
    OrgInstructorRole,
    OrgStaffRole,
)
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.mixed import MixedModuleStore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, LibraryFactory

User = get_user_model()

URL_NAME = "authoring_v2:course_rerun_defaults"
LEGACY_URL_NAME = "cms.djangoapps.contentstore:v1:course_rerun"
LEGACY_VIEW = ("cms.djangoapps.contentstore.rest_api.v1.views.course_rerun", "CourseRerunView")
SERVICE_LOGGER = "cms.djangoapps.contentstore.rest_api.v2.rerun_defaults_service"
COURSE_KEY = "course-v1:edX+DemoX+Demo_Course"
FIELDS = {"allow_unicode_course_id", "course_creator_status", "display_name", "number", "org", "run"}


def url(course_key):
    """Return the rerun defaults address of ``course_key``."""
    return reverse(URL_NAME, kwargs={"course_key": str(course_key)})


def legacy_url(course_key):
    """Return the deprecated course rerun address of ``course_key``."""
    return reverse(LEGACY_URL_NAME, kwargs={"course_id": str(course_key)})


def non_staff_superuser():
    """
    Return a superuser without staff status.

    Saving a superuser gives it staff status (the student app's
    ``grant_staff_access_to_superusers`` receiver), so staff status is cleared
    with an update, which sends no save signal.
    """
    user = UserFactory(is_superuser=True)
    User.objects.filter(pk=user.pk).update(is_staff=False)
    user.refresh_from_db()
    assert (user.is_superuser, user.is_staff) == (True, False)
    return user


def expected_body(course, allow_unicode_course_id=False):
    """The rerun defaults of ``course`` as global staff reads them."""
    return {
        "allow_unicode_course_id": allow_unicode_course_id,
        "course_creator_status": "granted",
        "display_name": course.display_name,
        "number": course.id.course,
        "org": course.id.org,
        "run": course.id.run,
    }


# ---------------------------------------------------------------------------
# Who may call the endpoint
# ---------------------------------------------------------------------------

class _AccessMixin:
    """Checks a caller's status on a read, its body or envelope, and that v1 decides the same."""

    def assert_read(self, user, expected, course_key=None):
        """Assert the status of a read by ``user`` and agreement with the v1 read."""
        course_key = course_key or self.course.id
        response = api_client(user).get(url(course_key))
        legacy = api_client(user).get(legacy_url(course_key))
        assert response.status_code == expected, response.content
        if expected == 200:
            assert response.json() == expected_body(self.course)
        else:
            assert_refused(response, expected)
        assert legacy.status_code == expected, f"v1 decided {legacy.status_code} for the same caller"


def _with_role(role_factory):
    """Return ``(name, function)`` giving a new user ``role_factory(course)`` on a course."""
    def give(course):
        user = UserFactory()
        role_factory(course).add_users(user)
        return user
    return give


COURSE_ROLES = {
    "course staff": _with_role(lambda course: CourseStaffRole(course.id)),
    "course instructor": _with_role(lambda course: CourseInstructorRole(course.id)),
    "course limited staff": _with_role(lambda course: CourseLimitedStaffRole(course.id)),
    "org staff": _with_role(lambda course: OrgStaffRole(course.id.org)),
    "org instructor": _with_role(lambda course: OrgInstructorRole(course.id.org)),
}


@ddt.ddt
class RerunDefaultsAccessLegacyPermissionsTest(_AccessMixin, SharedModuleStoreTestCase):
    """Who may read the defaults when policy-based authoring permissions are off: global staff only."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.other_course = CourseFactory.create()

    def test_anonymous_caller_is_refused_with_401(self):
        self.assert_read(None, 401)

    def test_anonymous_caller_of_an_unknown_course_is_refused_with_401(self):
        self.assert_read(None, 401, course_key=UNKNOWN_COURSE_KEY)

    def test_user_without_a_role_is_refused_with_403(self):
        self.assert_read(UserFactory(), 403)

    def test_unknown_course_is_404_before_permission_is_checked(self):
        self.assert_read(UserFactory(), 404, course_key=UNKNOWN_COURSE_KEY)

    @ddt.data(*COURSE_ROLES)
    def test_every_course_and_org_role_is_refused(self, role):
        self.assert_read(COURSE_ROLES[role](self.course), 403)

    @ddt.data(*COURSE_ROLES)
    def test_a_role_on_another_course_is_refused(self, role):
        self.assert_read(COURSE_ROLES[role](self.other_course), 403)

    def test_global_staff_may_read(self):
        self.assert_read(UserFactory(is_staff=True), 200)

    def test_global_staff_learn_that_a_course_does_not_exist(self):
        self.assert_read(UserFactory(is_staff=True), 404, course_key=UNKNOWN_COURSE_KEY)

    def test_a_superuser_without_staff_status_is_refused(self):
        """Both addresses check staff status alone, which being a superuser does not give."""
        self.assert_read(non_staff_superuser(), 403)

    def test_a_saved_superuser_has_staff_status_and_may_read(self):
        """
        The student app's ``grant_staff_access_to_superusers`` receiver gives
        every superuser staff status when it is saved, so a superuser created
        through the model is admitted by both addresses.
        """
        user = UserFactory(is_superuser=True, is_staff=False)
        assert user.is_staff is True
        assert User.objects.get(pk=user.pk).is_staff is True
        self.assert_read(user, 200)

    def test_a_version_only_course_key_is_404(self):
        """The key parses but names no course run; v1 has no address that takes it."""
        assert_refused(api_client(UserFactory(is_staff=True)).get(url(VERSION_ONLY_KEY)), 404)

    def test_a_library_is_not_a_course(self):
        library_key = LibraryFactory.create().location.library_key
        assert_refused(api_client(UserFactory(is_staff=True)).get(url(library_key)), 404)


@ddt.ddt
class RerunDefaultsAccessAuthzTest(CourseAuthoringAuthzTestMixin, _AccessMixin, SharedModuleStoreTestCase):
    """
    Who may read the defaults when policy-based authoring permissions are on:
    still global staff only, as no course permission is consulted.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def _user_with(self, role):
        user = UserFactory()
        self.add_user_to_role_in_course(user, role, self.course.id)
        return user

    def test_anonymous_caller_is_refused_with_401(self):
        self.assert_read(None, 401)

    def test_user_without_a_role_is_refused_with_403(self):
        self.assert_read(self.unauthorized_user, 403)

    def test_unknown_course_is_404_before_permission_is_checked(self):
        self.assert_read(self.unauthorized_user, 404, course_key=UNKNOWN_COURSE_KEY)

    @ddt.data(COURSE_ADMIN.external_key, COURSE_STAFF.external_key, COURSE_EDITOR.external_key,
              COURSE_AUDITOR.external_key)
    def test_every_course_role_is_refused(self, role):
        self.assert_read(self._user_with(role), 403)

    def test_legacy_course_instructor_is_refused(self):
        user = UserFactory()
        CourseAccessRole.objects.create(user=user, role="instructor", course_id=self.course.id, org=self.course.id.org)
        self.assert_read(user, 403)

    def test_global_staff_may_read(self):
        self.assert_read(self.staff_user, 200)

    def test_a_superuser_without_staff_status_is_refused(self):
        """No course permission is consulted, so the policy admitting superusers plays no part on either address."""
        self.assert_read(non_staff_superuser(), 403)


class RerunDefaultsAuthenticationTest(SharedModuleStoreTestCase):
    """How callers authenticate."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def test_a_jwt_caller_who_is_global_staff_may_read(self):
        header = {"HTTP_AUTHORIZATION": f"JWT {create_jwt_for_user(UserFactory(is_staff=True))}"}
        response = APIClient().get(url(self.course.id), **header)
        assert response.status_code == 200, response.content
        assert response.json() == expected_body(self.course)

    def test_a_jwt_caller_who_is_not_global_staff_is_refused(self):
        user = UserFactory()
        CourseInstructorRole(self.course.id).add_users(user)
        header = {"HTTP_AUTHORIZATION": f"JWT {create_jwt_for_user(user)}"}
        assert_refused(APIClient().get(url(self.course.id), **header), 403)

    def test_a_session_caller_who_is_global_staff_may_read(self):
        client = logged_in_client(UserFactory(is_staff=True, password=PASSWORD))
        response = client.get(url(self.course.id))
        assert response.status_code == 200, response.content
        assert response.json() == expected_body(self.course)

    def test_an_inactive_session_is_authenticated(self):
        """
        A session whose account is not activated is accepted, as on v1. Global
        staff then pass; course instructors are refused (403, not 401), as for
        an active account.
        """
        instructor = UserFactory(password=PASSWORD)
        CourseInstructorRole(self.course.id).add_users(instructor)
        for user, expected in ((UserFactory(is_staff=True, password=PASSWORD), 200), (instructor, 403)):
            client = logged_in_client(user)
            user.is_active = False
            user.save()
            response = client.get(url(self.course.id))
            assert response.status_code == expected, response.content
            if expected == 403:
                assert_refused(response, 403)
            assert client.get(legacy_url(self.course.id)).status_code == expected

    def test_a_bearer_token_is_refused(self):
        """Bearer tokens are not accepted here, though v1 takes them."""
        user = UserFactory(is_staff=True)
        token = AccessTokenFactory(user=user, application=ApplicationFactory(user=user)).token
        header = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        assert_refused(APIClient().get(url(self.course.id), **header), 401)
        assert APIClient().get(legacy_url(self.course.id), **header).status_code == 200


class RerunDefaultsPermissionWiringTest(SimpleTestCase):
    """The checks the endpoint runs, in order, and the staff check against the one v1 runs."""

    def test_the_course_is_checked_before_staff_status(self):
        view = CourseRerunDefaultsViewSet()
        for action in ("retrieve", None, "metadata"):
            view.action = action
            # The JWT middleware appends NotJwtRestrictedApplication to views that take JWTs.
            declared = [
                type(permission) for permission in view.get_permissions()
                if not isinstance(permission, NotJwtRestrictedApplication)
            ]
            assert declared == [IsAuthenticated, IsExistingCourse, IsAdminUser], action

    def test_the_staff_check_decides_as_the_v1_check(self):
        """v1 refuses unless ``GlobalStaff().has_user``; staff status alone decides both, whatever else is set."""
        for is_staff, is_superuser, is_active in product((True, False), repeat=3):
            user = SimpleNamespace(is_staff=is_staff, is_superuser=is_superuser, is_active=is_active)
            decided = IsAdminUser().has_permission(SimpleNamespace(user=user), view=None)
            assert decided is GlobalStaff().has_user(user) is is_staff, (is_staff, is_superuser, is_active)

    def test_bearer_tokens_are_not_among_the_authentication_classes(self):
        assert CourseRerunDefaultsViewSet.authentication_classes == (
            JwtAuthentication, SessionAuthenticationAllowInactiveUser,
        )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

class RerunDefaultsReadTest(ModuleStoreTestCase):
    """What a read by global staff returns."""

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(display_name="Rerun Me")
        self.client = api_client(UserFactory(is_staff=True))

    def get(self, course_key=None, **params):
        response = self.client.get(url(course_key or self.course.id), params)
        assert response.status_code == 200, response.content
        return response.json()

    def test_the_body_is_the_course_key_parts_the_name_and_the_site_setting(self):
        body = self.get()
        assert body == expected_body(self.course)
        assert body["display_name"] == "Rerun Me"
        assert list(body) == sorted(FIELDS)

    @override_settings(ALLOW_UNICODE_COURSE_ID=True)
    def test_the_site_setting_for_non_ascii_keys_is_reported_when_on(self):
        assert self.get() == expected_body(self.course, allow_unicode_course_id=True)
        assert self.client.get(legacy_url(self.course.id)).json()["allow_unicode_course_id"] is True

    @override_settings(ALLOW_UNICODE_COURSE_ID=False)
    def test_the_site_setting_for_non_ascii_keys_is_reported_when_off(self):
        assert self.get()["allow_unicode_course_id"] is False
        assert self.client.get(legacy_url(self.course.id)).json()["allow_unicode_course_id"] is False

    def test_a_course_without_a_name_has_a_null_name(self):
        course = modulestore().get_course(self.course.id)
        course.display_name = None
        modulestore().update_item(course, ModuleStoreEnum.UserID.test)
        assert self.get()["display_name"] is None
        assert self.client.get(legacy_url(self.course.id)).json()["display_name"] is None

    def test_the_body_matches_the_published_schema(self):
        assert_matches_published_schema(self.get(), "CourseRerunDefaults.v2")

    def test_query_parameters_are_ignored(self):
        assert self.get(view="full", fields="org") == self.get()

    def test_head_answers_without_a_body(self):
        response = self.client.head(url(self.course.id))
        assert response.status_code == 200
        assert response.content == b""


@override_settings(ENABLE_CREATOR_GROUP=True)
class RerunDefaultsCourseCreatorTest(ModuleStoreTestCase):
    """
    Looking up whether the caller may create courses records a course creator
    request for a caller who is not global staff. Such a caller is refused
    before the lookup runs, so a read records nothing.
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.instructor = UserFactory()
        CourseInstructorRole(self.course.id).add_users(self.instructor)

    def test_the_lookup_records_a_request_for_a_caller_who_is_not_staff(self):
        """The write the endpoint must never reach exists: a direct lookup makes it, and the recorder sees it."""
        with record_effects(self) as effects:
            status = course_views._get_course_creator_status(self.instructor)  # pylint: disable=protected-access
        assert status == CourseCreator.UNREQUESTED
        assert CourseCreator.objects.filter(user=self.instructor).exists()
        assert ("INSERT", CourseCreator._meta.db_table) in effects["sql_writes"]  # pylint: disable=protected-access

    def test_a_caller_who_is_not_staff_is_refused_before_the_lookup(self):
        lookup = course_views._get_course_creator_status  # pylint: disable=protected-access
        with patch.object(course_views, "_get_course_creator_status", wraps=lookup) as spy:
            assert_refused(api_client(self.instructor).get(url(self.course.id)), 403)
            assert api_client(self.instructor).get(legacy_url(self.course.id)).status_code == 403
        spy.assert_not_called()
        assert not CourseCreator.objects.filter(user=self.instructor).exists()

    def test_global_staff_are_granted_without_a_request_being_recorded(self):
        staff = UserFactory(is_staff=True)
        lookup = course_views._get_course_creator_status  # pylint: disable=protected-access
        with patch.object(course_views, "_get_course_creator_status", wraps=lookup) as spy:
            response = api_client(staff).get(url(self.course.id))
        assert response.status_code == 200, response.content
        assert response.json()["course_creator_status"] == "granted"
        spy.assert_called_once_with(staff)
        assert not CourseCreator.objects.filter(user=staff).exists()


# Requests v1 and v2 answer differently beyond the error body, as
# (request, v1 answer, v2 answer, test in RerunDefaultsResponseDifferencesTest).
RESPONSE_DIFFERENCES = [
    ("read of a course the modulestore returns nothing for", "500", "404",
     "test_a_course_the_modulestore_returns_nothing_for"),
    ("read with a Bearer token", "200", "401", "test_a_bearer_token"),
    ("slash-separated course key", "404 JSON", "404 HTML", "test_a_slash_separated_course_key"),
]


class RerunDefaultsResponseDifferencesTest(ModuleStoreTestCase):
    """Each request v1 and v2 answer differently, both answers observed."""

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.client = api_client(UserFactory(is_staff=True))

    def test_every_listed_difference_has_its_test(self):
        assert [name for *_, name in RESPONSE_DIFFERENCES if not hasattr(self, name)] == []

    def test_a_course_the_modulestore_returns_nothing_for(self):
        with patch.object(MixedModuleStore, "get_course", return_value=None):
            legacy = self.client.get(legacy_url(self.course.id))
            with self.assertLogs(SERVICE_LOGGER, "INFO") as logs:
                new = self.client.get(url(self.course.id))
        assert legacy.status_code == 500
        assert_refused(new, 404)
        (record,) = logs.output
        assert str(self.course.id) in record

    def test_a_bearer_token(self):
        user = UserFactory(is_staff=True)
        token = AccessTokenFactory(user=user, application=ApplicationFactory(user=user)).token
        header = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        assert APIClient().get(legacy_url(self.course.id), **header).status_code == 200
        assert_refused(APIClient().get(url(self.course.id), **header), 401)

    def test_a_slash_separated_course_key(self):
        """v1 routes the slash form and reports the course unknown in JSON; v2 has no route for it."""
        legacy = self.client.get("/api/contentstore/v1/course_rerun/edX/NoSuch/Course")
        new = self.client.get("/api/authoring/v2/courses/edX/NoSuch/Course/rerun_defaults/")
        assert legacy.status_code == new.status_code == 404
        assert legacy.json()["error_code"] == "course_does_not_exist"
        assert new["Content-Type"].startswith("text/html")


# ---------------------------------------------------------------------------
# Parity with v1
# ---------------------------------------------------------------------------

# Members of a response body that differ between v1 and v2 for the same
# request. The comparison asserts nothing else differs, and that each of these
# occurs in the error cases below.
INTENTIONAL_DIFFERENCES = [
    ("type", "errors use the standardized envelope"),
    ("title", "errors use the standardized envelope"),
    ("status", "errors use the standardized envelope"),
    ("detail", "errors use the standardized envelope"),
    ("developer_message", "the v1 error body carries developer_message; the envelope does not"),
    ("error_code", "the v1 not-found body carries error_code; the envelope does not"),
]


def _undeclared(changed):
    """The changed paths no declared intentional difference covers."""
    return undeclared_paths(changed, INTENTIONAL_DIFFERENCES)


def test_only_declared_differences_are_tolerated():
    assert _undeclared({"type", "detail", "developer_message", "error_code"}) == set()
    assert _undeclared({"display_name", "course_creator_status", "typed"}) == {
        "display_name", "course_creator_status", "typed",
    }


class _ParityMixin:
    """
    v2 against v1 for the same course and caller: a read returns an identical
    body, and errors differ only by the declared differences.
    """

    def outsider(self):
        """Return a signed-in user who is not global staff."""
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(display_name="Parity")
        self.reader = UserFactory(is_staff=True)
        self.observed = set()

    def compare(self, legacy, new, expected_status):
        """Assert both statuses and that the bodies differ only as declared; return both bodies."""
        assert legacy.status_code == expected_status, legacy.content
        assert new.status_code == expected_status, new.content
        legacy_body, new_body = json.loads(legacy.content or b"null"), json.loads(new.content or b"null")
        changed = changed_paths(legacy_body, new_body)
        assert _undeclared(changed) == set(), f"undeclared differences: legacy={legacy_body} new={new_body}"
        self.observed |= changed
        return legacy_body, new_body

    def test_a_read_returns_an_identical_body(self):
        client = api_client(self.reader)
        legacy_body, new_body = self.compare(
            client.get(legacy_url(self.course.id)), client.get(url(self.course.id)), 200,
        )
        assert changed_paths(legacy_body, new_body) == set()
        assert list(new_body) == list(legacy_body)
        assert new_body == expected_body(self.course)

    def _error_pairs(self):
        """``(legacy, new, status)`` for each error both addresses produce."""
        for client, course_key, status in (
            (api_client(), self.course.id, 401),
            (api_client(self.outsider()), self.course.id, 403),
            (api_client(self.outsider()), UNKNOWN_COURSE_KEY, 404),
        ):
            yield client.get(legacy_url(course_key)), client.get(url(course_key)), status

    def test_errors_differ_only_by_the_envelope(self):
        for legacy, new, status in self._error_pairs():
            self.compare(legacy, new, status)

    def test_every_declared_difference_occurs(self):
        for legacy, new, status in self._error_pairs():
            self.compare(legacy, new, status)
        missing = [
            path for path, _ in INTENTIONAL_DIFFERENCES
            if not any(key == path or key.startswith(f"{path}.") or key.startswith(f"{path}[") for key in self.observed)
        ]
        assert missing == []


class RerunDefaultsParityLegacyPermissionsTest(_ParityMixin, ModuleStoreTestCase):
    """Parity with policy-based authoring permissions off; the outsider is a course instructor."""

    def outsider(self):
        user = UserFactory()
        CourseInstructorRole(self.course.id).add_users(user)
        return user


class RerunDefaultsParityAuthzTest(_ParityMixin, CourseAuthoringAuthzTestMixin, ModuleStoreTestCase):
    """Parity with policy-based authoring permissions on; the outsider holds the course admin role."""

    def outsider(self):
        user = UserFactory()
        self.add_user_to_role_in_course(user, COURSE_ADMIN.external_key, self.course.id)
        return user


# ---------------------------------------------------------------------------
# Side effects of reads
# ---------------------------------------------------------------------------

# What a read may write on a database that has never served a role or
# permission lookup: nothing, or the permission library's policy version row.
FIRST_READ_EFFECTS: tuple[tuple[list[tuple[str, str]], list[tuple[str, str]]], ...] = (
    ([], []),
    (FIRST_LOOKUP_WRITES, FIRST_LOOKUP_SIGNALS),
)


def fresh_client(user):
    """
    Return a client authenticated as a new instance of ``user``. Role lookups
    are cached on the user instance, so reusing one instance across reads
    would let a later read skip the lookup an earlier read made.
    """
    return api_client(User.objects.get(pk=user.pk))


class _ReadsAreSideEffectFreeMixin:
    """
    A read (GET and HEAD) by global staff writes no row and no Mongo document,
    stores nothing through the modulestore and sends no signal or event. On a
    database that has never served a role or permission lookup, a read may
    insert the permission library's one policy version row, and does exactly
    what the v1 read does there.
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.reader = UserFactory(is_staff=True)

    def _read_on_a_fresh_database(self, address, method="get"):
        """Record the effects of one read made after the policy version row is removed."""
        PolicyCacheControl.objects.all().delete()
        client = fresh_client(self.reader)
        with record_effects(self) as effects:
            response = getattr(client, method)(address)
        assert response.status_code == 200, response.content
        return effects

    def assert_nothing_else(self, effects, label):
        assert effects["events"] == [], label
        assert effects["mongo_writes"] == 0, label
        assert effects["update_item"] == [], label

    def test_reads_have_no_side_effects(self):
        legacy = self._read_on_a_fresh_database(legacy_url(self.course.id))
        assert (legacy["sql_writes"], legacy["signals"]) in FIRST_READ_EFFECTS
        for method in ("get", "head"):
            first = self._read_on_a_fresh_database(url(self.course.id), method)
            assert (first["sql_writes"], first["signals"]) == (legacy["sql_writes"], legacy["signals"]), method
            self.assert_nothing_else(first, method)
            client = fresh_client(self.reader)
            with record_effects(self) as again:
                response = getattr(client, method)(url(self.course.id))
            assert response.status_code == 200, response.content
            assert again["sql_writes"] == [], method
            assert again["signals"] == [], method
            self.assert_nothing_else(again, method)


class RerunDefaultsReadSideEffectsLegacyPermissionsTest(_ReadsAreSideEffectFreeMixin, ModuleStoreTestCase):
    """Reads with policy-based authoring permissions off."""


class RerunDefaultsReadSideEffectsAuthzTest(
    _ReadsAreSideEffectFreeMixin, CourseAuthoringAuthzTestMixin, ModuleStoreTestCase,
):
    """Reads with policy-based authoring permissions on."""


# ---------------------------------------------------------------------------
# Query counts
# ---------------------------------------------------------------------------

class _QueryCountMixin:
    """A read costs no more SQL queries or Mongo reads than the v1 read."""

    def test_a_read_costs_no_more_than_the_v1_read(self):
        course = CourseFactory.create()
        client = api_client(UserFactory(is_staff=True))
        legacy = measure(lambda: client.get(legacy_url(course.id)))
        new = measure(lambda: client.get(url(course.id)))
        assert new[0] <= legacy[0], (new, legacy)
        assert new[1] <= legacy[1], (new, legacy)
        assert new[2] == legacy[2] == 0, (new, legacy)


class RerunDefaultsQueryCountLegacyPermissionsTest(_QueryCountMixin, ModuleStoreTestCase):
    """Costs with policy-based authoring permissions off."""


class RerunDefaultsQueryCountAuthzTest(CourseAuthoringAuthzTestMixin, _QueryCountMixin, ModuleStoreTestCase):
    """Costs with policy-based authoring permissions on."""


# ---------------------------------------------------------------------------
# Addresses and methods
# ---------------------------------------------------------------------------

@ddt.ddt
class RerunDefaultsRoutingTest(SharedModuleStoreTestCase):
    """The address, the v1 address, and requests no route or method serves."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def test_the_address(self):
        assert url(COURSE_KEY) == f"/api/authoring/v2/courses/{COURSE_KEY}/rerun_defaults/"
        match = resolve(url(COURSE_KEY))
        assert match.func.cls is CourseRerunDefaultsViewSet
        assert routed_actions(match.func) == {"get": "retrieve"}

    def test_the_v1_address_is_unchanged(self):
        assert legacy_url(COURSE_KEY) == f"/api/contentstore/v1/course_rerun/{COURSE_KEY}"
        view = resolve(legacy_url(COURSE_KEY)).func.cls
        assert (view.__module__, view.__name__) == LEGACY_VIEW

    def test_v1_still_takes_a_slash_separated_key_that_v2_does_not(self):
        view = resolve("/api/contentstore/v1/course_rerun/edX/DemoX/Demo_Course").func.cls
        assert (view.__module__, view.__name__) == LEGACY_VIEW
        with pytest.raises(Resolver404):
            resolve("/api/authoring/v2/courses/edX/DemoX/Demo_Course/rerun_defaults/")

    def test_the_url_names_are_unique_in_the_namespace(self):
        # Imported here: at module import the URL converters are not registered yet.
        from cms.djangoapps.contentstore.rest_api.v2 import authoring_urls  # noqa: PLC0415
        names = [pattern.name for pattern in authoring_urls.urlpatterns]
        assert len(names) == len(set(names))
        assert "course_rerun_defaults" in names

    # The error library does not catalogue MethodNotAllowed or NotAcceptable, so
    # these answer with the envelope typed internal. Change the expected type
    # here once it does.

    @ddt.data("post", "put", "patch", "delete")
    def test_other_methods_are_not_allowed(self, method):
        response = getattr(api_client(UserFactory(is_staff=True)), method)(url(self.course.id), {}, format="json")
        assert_error_envelope(response, expected_status=405, expected_type_slug="internal")

    def test_unsupported_accept_is_refused_with_406(self):
        response = api_client(UserFactory(is_staff=True)).get(url(self.course.id), HTTP_ACCEPT="application/xml")
        assert_error_envelope(response, expected_status=406, expected_type_slug="internal")

    # These addresses match no route, so the service's HTML 404 page answers
    # them. Change these to the error envelope once a catch-all route under the
    # mount answers unknown addresses.

    @ddt.data("not-a-course-key", "edX/DemoX/Demo_Course", "edX+DemoX+Demo_Course")
    def test_an_unparsable_or_slash_form_key_is_an_html_404(self, key):
        response = api_client(UserFactory(is_staff=True)).get(f"/api/authoring/v2/courses/{key}/rerun_defaults/")
        assert response.status_code == 404
        assert response["Content-Type"].startswith("text/html")

    def test_the_address_without_its_trailing_slash_redirects_to_it(self):
        """No route answers the address without its slash; the service redirects a read to the slashed one."""
        address = f"/api/authoring/v2/courses/{self.course.id}/rerun_defaults"
        response = api_client(UserFactory(is_staff=True)).get(address)
        assert response.status_code == 301
        assert response["Location"] == url(self.course.id)
        with pytest.raises(Resolver404):
            resolve(address)


# ---------------------------------------------------------------------------
# The published schema
# ---------------------------------------------------------------------------

class RerunDefaultsSchemaTest(TestCase):
    """The read as published in the CMS authoring schema."""

    PATH = "/api/authoring/v2/courses/{course_key}/rerun_defaults/"
    LEGACY = "/api/contentstore/v1/course_rerun/{course_id}"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = published_schema()
        cls.components = cls.schema["components"]["schemas"]

    @staticmethod
    def _ref(node):
        return node["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]

    def test_the_read_is_published_for_the_sdk(self):
        operations = self.schema["paths"][self.PATH]
        assert sorted(operations) == ["get"]
        operation = operations["get"]
        assert operation["operationId"] == "v2_courses_rerun_defaults_retrieve"
        assert operation["tags"] == ["openedx-platform-sdk"]
        assert "deprecated" not in operation
        assert self._ref(operation["responses"]["200"]) == "CourseRerunDefaults.v2"
        for status in ("401", "403", "404"):
            assert self._ref(operation["responses"][status]) == "ErrorResponse", status
        assert "400" not in operation["responses"]

    def test_the_only_parameter_is_the_course_key(self):
        (parameter,) = self.schema["paths"][self.PATH]["get"]["parameters"]
        assert (parameter["name"], parameter["in"]) == ("course_key", "path")
        assert parameter["schema"] == {"type": "string", "pattern": "^[^/]+$"}

    def test_the_defaults_component(self):
        component = self.components["CourseRerunDefaults.v2"]
        assert set(component["properties"]) == FIELDS
        assert set(component["required"]) == FIELDS
        nullable = {name for name, field in component["properties"].items() if field.get("nullable")}
        assert nullable == {"display_name"}
        assert component.get("description")
        assert [name for name, spec in component["properties"].items() if not spec.get("description")] == []

    def test_the_example_is_a_valid_body(self):
        content = self.schema["paths"][self.PATH]["get"]["responses"]["200"]["content"]["application/json"]
        assert_matches_published_schema(content["examples"]["RerunDefaults"]["value"], "CourseRerunDefaults.v2")

    def test_the_v1_read_is_deprecated_and_left_out_of_the_sdk(self):
        operations = self.schema["paths"][self.LEGACY]
        assert sorted(operations) == ["get"]
        assert operations["get"]["operationId"] == "v1_course_rerun_retrieve"
        assert operations["get"]["deprecated"] is True
        assert "openedx-platform-sdk" not in operations["get"].get("tags", [])

    def test_only_superseded_or_migrated_addresses_are_deprecated(self):
        """No operation is marked deprecated except by the legacy-address lists, so a stray mark is caught."""
        deprecated = {
            (path, method)
            for path, operations in self.schema["paths"].items()
            for method, operation in operations.items()
            if isinstance(operation, dict) and operation.get("deprecated")
        }
        assert (self.LEGACY, "get") in deprecated
        assert (self.PATH, "get") not in deprecated
        stray = sorted(
            (path, method) for path, method in deprecated
            if path not in SUPERSEDED_PATHS and not path.startswith(LEGACY_MIGRATED_PATH_PREFIXES)
        )
        assert stray == []
