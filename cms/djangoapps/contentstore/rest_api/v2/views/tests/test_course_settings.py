"""Tests for the v2 course settings API."""

import json
import re
from datetime import UTC, datetime
from unittest.mock import patch

import ddt
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.test.utils import override_settings
from django.urls import Resolver404, resolve, reverse
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.permissions import NotJwtRestrictedApplication
from edx_rest_framework_extensions.testing import assert_error_envelope
from openedx_authz.api.users import assign_role_to_user_in_scope
from openedx_authz.constants.roles import (
    COURSE_ADMIN,
    COURSE_AUDITOR,
    COURSE_BETA_TESTER,
    COURSE_DATA_RESEARCHER,
    COURSE_EDITOR,
    COURSE_LIMITED_STAFF,
    COURSE_STAFF,
)
from openedx_authz.engine.enforcer import AuthzEnforcer
from openedx_authz.models.engine import PolicyCacheControl
from rest_framework.permissions import IsAuthenticated
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
from cms.djangoapps.contentstore.rest_api.v2 import course_settings_service
from cms.djangoapps.contentstore.rest_api.v2.views.course_settings import CourseSettingsViewSet
from cms.djangoapps.contentstore.rest_api.v2.views.tests.helpers import PASSWORD, logged_in_client
from cms.djangoapps.contentstore.tests.utils import routed_actions
from cms.djangoapps.contentstore.views import course as course_views
from cms.djangoapps.contentstore.views.permissions import CanViewCourse
from cms.lib.spectacular import LEGACY_MIGRATED_PATH_PREFIXES, SUPERSEDED_PATHS
from common.djangoapps.course_action_state.models import CourseRerunState
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import CourseAccessRole
from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseLimitedStaffRole,
    CourseStaffRole,
    OrgInstructorRole,
    OrgStaffRole,
)
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core import toggles as core_toggles
from openedx.core.djangoapps.authz.tests.fixtures import seed_policies
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.credit.tests.factories import CreditCourseFactory, CreditRequirementFactory
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.oauth_dispatch.tests.factories import AccessTokenFactory, ApplicationFactory
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.mixed import MixedModuleStore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, LibraryFactory

User = get_user_model()

URL_NAME = "authoring_v2:course_settings"
LEGACY_URL_NAME = "cms.djangoapps.contentstore:v1:course_settings"
LEGACY_VIEW = ("cms.djangoapps.contentstore.rest_api.v1.views.settings", "CourseSettingsView")
GROUP_CONFIGURATIONS_URL_NAME = "cms.djangoapps.contentstore:v1:group_configurations"
SERVICE_LOGGER = "cms.djangoapps.contentstore.rest_api.v2.course_settings_service"
COURSE_KEY = "course-v1:edX+DemoX+Demo_Course"
FULL = {"view": "full"}
VIEWS = ("minimal", "full")
HEAVY_FIELDS = {"credit_requirements", "language_options", "possible_pre_requisite_courses"}
MINIMAL_FIELDS = {
    "about_page_editable", "can_show_certificate_available_date_field", "content_experiments_enabled",
    "course_display_name", "course_display_name_with_default", "credit_eligibility_enabled",
    "enable_extended_course_details", "enrollment_end_editable", "is_credit_course", "is_entrance_exams_enabled",
    "is_prerequisite_courses_enabled", "licensing_enabled", "lms_link_for_about_page", "marketing_enabled",
    "mfe_proctored_exam_settings_url", "platform_name", "short_description_editable", "show_min_grade_warning",
    "sidebar_html_enabled", "upgrade_deadline",
}
PREREQUISITE_MEMBERS = {"course_key", "display_name", "lms_link", "number", "org", "rerun_link", "run", "url"}
PREREQUISITES_ON = override_settings(ENABLE_PREREQUISITE_COURSES=True, MILESTONES_APP=True)
# Query parameters the Studio course listing reads, with values that narrow it or that it refuses.
COURSE_LIST_PARAMETERS = {"search": "Alpha", "ordering": "-display_name", "active_only": "true"}


def url(course_key):
    """Return the settings address of ``course_key``."""
    return reverse(URL_NAME, kwargs={"course_key": str(course_key)})


def legacy_url(course_key):
    """Return the deprecated course settings address of ``course_key``."""
    return reverse(LEGACY_URL_NAME, kwargs={"course_id": str(course_key)})


def view_params(view):
    """The query parameters that ask for ``view``; the minimal view is asked for by sending none."""
    return FULL if view == "full" else {}


def roots(paths):
    """The top-level member of each json path."""
    return {re.split(r"[.\[]", path, maxsplit=1)[0] for path in paths}


def create_course(**kwargs):
    """Create a course and its overview, as publishing it from Studio does."""
    course = CourseFactory.create(**kwargs)
    CourseOverview.load_from_module_store(course.id)
    return course


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


def turn_on_content_experiments(course_key, user_id=ModuleStoreEnum.UserID.test):
    """Add content experiments to the advanced modules of the stored course."""
    course = modulestore().get_course(course_key)
    course.advanced_modules = [*course.advanced_modules, "split_test"]
    modulestore().update_item(course, user_id)


def make_credit_course(course_key, requirements=(("grade", "grade", "Grade", {"min_grade": 0.8}),)):
    """Register ``course_key`` as a credit course with ``(namespace, name, display_name, criteria)`` requirements."""
    credit_course = CreditCourseFactory(course_key=course_key, enabled=True)
    for namespace, name, display_name, criteria in requirements:
        CreditRequirementFactory(
            course=credit_course, namespace=namespace, name=name, display_name=display_name, criteria=criteria,
        )
    return credit_course


# ---------------------------------------------------------------------------
# Who may call the endpoint
# ---------------------------------------------------------------------------

class _AccessMixin:
    """Checks a caller's status on both views against the expected one and the v1 decision."""

    def assert_read(self, user, view, expected, course_key=None):
        """Assert the status of a read by ``user``, its members or envelope, and agreement with v1."""
        course_key = course_key or self.course.id
        response = api_client(user).get(url(course_key), view_params(view))
        legacy = api_client(user).get(legacy_url(course_key))
        assert response.status_code == expected, response.content
        if expected == 200:
            extra = {"language_options"} if view == "full" else set()
            assert set(response.json()) == MINIMAL_FIELDS | extra
        else:
            assert_refused(response, expected)
        assert legacy.status_code == expected, f"v1 decided {legacy.status_code} for the same caller"


@ddt.ddt
class SettingsAccessLegacyPermissionsTest(_AccessMixin, SharedModuleStoreTestCase):
    """Who may read the settings when policy-based authoring permissions are off."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.other_course = CourseFactory.create()

    def _user_with(self, role):
        user = UserFactory()
        role.add_users(user)
        return user

    @ddt.data(*VIEWS)
    def test_anonymous_caller_is_refused_with_401(self, view):
        self.assert_read(None, view, 401)

    @ddt.data(*VIEWS)
    def test_anonymous_caller_of_an_unknown_course_is_refused_with_401(self, view):
        self.assert_read(None, view, 401, course_key=UNKNOWN_COURSE_KEY)

    @ddt.data(*VIEWS)
    def test_user_without_a_role_is_refused_with_403(self, view):
        self.assert_read(UserFactory(), view, 403)

    @ddt.data(*VIEWS)
    def test_unknown_course_is_404_before_permission_is_checked(self, view):
        self.assert_read(UserFactory(), view, 404, course_key=UNKNOWN_COURSE_KEY)

    @ddt.data(*VIEWS)
    def test_limited_staff_is_refused(self, view):
        self.assert_read(self._user_with(CourseLimitedStaffRole(self.course.id)), view, 403)

    @ddt.data(*VIEWS)
    def test_staff_and_instructors_of_another_course_are_refused(self, view):
        self.assert_read(self._user_with(CourseStaffRole(self.other_course.id)), view, 403)
        self.assert_read(self._user_with(CourseInstructorRole(self.other_course.id)), view, 403)

    @ddt.data(*VIEWS)
    def test_course_staff_and_instructor_may_read(self, view):
        self.assert_read(self._user_with(CourseStaffRole(self.course.id)), view, 200)
        self.assert_read(self._user_with(CourseInstructorRole(self.course.id)), view, 200)

    @ddt.data(*VIEWS)
    def test_org_staff_and_org_instructor_may_read(self, view):
        self.assert_read(self._user_with(OrgStaffRole(self.course.id.org)), view, 200)
        self.assert_read(self._user_with(OrgInstructorRole(self.course.id.org)), view, 200)

    @ddt.data(*VIEWS)
    def test_global_staff_may_read(self, view):
        self.assert_read(UserFactory(is_staff=True), view, 200)

    @ddt.data(*VIEWS)
    def test_a_superuser_without_staff_status_is_refused(self, view):
        """
        Studio read access comes from staff status or a course or organization
        role, and being a superuser is neither, so both addresses refuse.
        """
        self.assert_read(non_staff_superuser(), view, 403)

    @ddt.data(*VIEWS)
    def test_a_saved_superuser_has_staff_status_and_may_read(self, view):
        """
        The student app's ``grant_staff_access_to_superusers`` receiver gives
        every superuser staff status when it is saved, so a superuser created
        through the model is admitted by both addresses.
        """
        user = UserFactory(is_superuser=True, is_staff=False)
        assert User.objects.get(pk=user.pk).is_staff is True
        self.assert_read(user, view, 200)

    def test_a_policy_role_does_not_count_for_an_inactive_account(self):
        """
        A course staff role assigned through policy-based permissions counts as
        Studio's course staff role while those permissions are off for the
        course, but, as for any course role, only while the account is active.
        """
        seed_policies()
        self.addCleanup(AuthzEnforcer.get_enforcer().clear_policy)
        user = UserFactory(password=PASSWORD)
        assign_role_to_user_in_scope(user.username, COURSE_STAFF.external_key, str(self.course.id))
        AuthzEnforcer.get_enforcer().load_policy()
        assert not CourseAccessRole.objects.filter(user=user).exists()
        client = logged_in_client(user)
        with patch.object(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, "is_enabled", return_value=False):
            for is_active, expected in ((True, 200), (False, 403)):
                user.is_active = is_active
                user.save()
                for params in ({}, FULL):
                    response = client.get(url(self.course.id), params)
                    assert response.status_code == expected, (is_active, params, response.content)
                    if expected == 403:
                        assert_refused(response, 403)
                legacy = client.get(legacy_url(self.course.id))
                assert legacy.status_code == expected, f"v1 decided {legacy.status_code} for is_active={is_active}"

    def test_a_version_only_course_key_is_404(self):
        """The key parses but names no course run; v1 has no address that takes it."""
        assert_refused(api_client(UserFactory(is_staff=True)).get(url(VERSION_ONLY_KEY)), 404)

    def test_a_library_is_not_a_course(self):
        library_key = LibraryFactory.create().location.library_key
        assert_refused(api_client(UserFactory(is_staff=True)).get(url(library_key)), 404)


@ddt.ddt
class SettingsAccessAuthzTest(CourseAuthoringAuthzTestMixin, _AccessMixin, SharedModuleStoreTestCase):
    """Who may read the settings when policy-based authoring permissions are on: the course view permission."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.other_course = CourseFactory.create()

    def _user_with(self, role, course_key=None):
        user = UserFactory()
        self.add_user_to_role_in_course(user, role, course_key or self.course.id)
        return user

    @ddt.data(*VIEWS)
    def test_anonymous_caller_is_refused_with_401(self, view):
        self.assert_read(None, view, 401)

    @ddt.data(*VIEWS)
    def test_user_without_a_role_is_refused_with_403(self, view):
        self.assert_read(self.unauthorized_user, view, 403)

    @ddt.data(*VIEWS)
    def test_unknown_course_is_404_before_permission_is_checked(self, view):
        self.assert_read(self.unauthorized_user, view, 404, course_key=UNKNOWN_COURSE_KEY)

    @ddt.data(*[(view, role) for view in VIEWS for role in (
        COURSE_AUDITOR.external_key, COURSE_EDITOR.external_key, COURSE_STAFF.external_key, COURSE_ADMIN.external_key,
    )])
    @ddt.unpack
    def test_roles_that_may_view_the_course_may_read(self, view, role):
        self.assert_read(self._user_with(role), view, 200)

    @ddt.data(*[(view, role) for view in VIEWS for role in (
        COURSE_LIMITED_STAFF.external_key, COURSE_DATA_RESEARCHER.external_key, COURSE_BETA_TESTER.external_key,
    )])
    @ddt.unpack
    def test_other_roles_are_refused(self, view, role):
        self.assert_read(self._user_with(role), view, 403)

    @ddt.data(*VIEWS)
    def test_a_role_on_another_course_is_refused(self, view):
        user = self._user_with(COURSE_ADMIN.external_key, self.other_course.id)
        self.assert_read(user, view, 200, course_key=self.other_course.id)
        self.assert_read(user, view, 403)

    @ddt.data(*VIEWS)
    def test_legacy_course_staff_without_a_policy_role_is_refused(self, view):
        user = UserFactory()
        CourseAccessRole.objects.create(user=user, role="staff", course_id=self.course.id, org=self.course.id.org)
        self.assert_read(user, view, 403)

    @ddt.data(*VIEWS)
    def test_global_staff_may_read_without_a_role(self, view):
        self.assert_read(self.staff_user, view, 200)

    @ddt.data(*VIEWS)
    def test_a_superuser_without_staff_status_may_read_without_a_role(self, view):
        """The policy admits staff and superusers on every course, so both addresses admit this account."""
        self.assert_read(non_staff_superuser(), view, 200)

    def test_an_inactive_session_with_a_role_may_read(self):
        """The policy does not look at whether the account is activated, on v2 as on v1."""
        user = UserFactory(password=PASSWORD)
        self.add_user_to_role_in_course(user, COURSE_AUDITOR.external_key, self.course.id)
        client = logged_in_client(user)
        user.is_active = False
        user.save()
        assert client.get(url(self.course.id)).status_code == 200
        assert client.get(legacy_url(self.course.id)).status_code == 200


class SettingsAuthenticationTest(SharedModuleStoreTestCase):
    """How callers authenticate, with policy-based authoring permissions off."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def _course_staff(self):
        user = UserFactory(password=PASSWORD)
        CourseStaffRole(self.course.id).add_users(user)
        return user

    def test_a_jwt_caller_may_read(self):
        header = {"HTTP_AUTHORIZATION": f"JWT {create_jwt_for_user(self._course_staff())}"}
        response = APIClient().get(url(self.course.id), **header)
        assert response.status_code == 200, response.content
        assert set(response.json()) == MINIMAL_FIELDS

    def test_a_session_caller_may_read(self):
        response = logged_in_client(self._course_staff()).get(url(self.course.id), FULL)
        assert response.status_code == 200, response.content
        assert set(response.json()) == MINIMAL_FIELDS | {"language_options"}

    def test_an_inactive_session_is_authenticated(self):
        """
        A session whose account is not activated is accepted, as on v1. Global
        staff then pass; course staff are refused (403, not 401) because a
        course role does not count while the account is inactive.
        """
        for user, expected in ((UserFactory(is_staff=True, password=PASSWORD), 200), (self._course_staff(), 403)):
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


class SettingsPermissionWiringTest(SimpleTestCase):
    """The checks the endpoint runs, in order."""

    def test_the_course_view_permission_is_checked_after_authentication(self):
        view = CourseSettingsViewSet()
        for action in ("retrieve", None, "metadata"):
            view.action = action
            # The JWT middleware appends NotJwtRestrictedApplication to views that take JWTs.
            declared = [
                type(permission) for permission in view.get_permissions()
                if not isinstance(permission, NotJwtRestrictedApplication)
            ]
            assert declared == [IsAuthenticated, CanViewCourse], action

    def test_bearer_tokens_are_not_among_the_authentication_classes(self):
        assert CourseSettingsViewSet.authentication_classes == (
            JwtAuthentication, SessionAuthenticationAllowInactiveUser,
        )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

@ddt.ddt
class SettingsReadTest(ModuleStoreTestCase):
    """What a read returns, by view, with every feature that adds members turned on."""

    def setUp(self):
        super().setUp()
        self.course = create_course(display_name="Settings")
        self.alpha = create_course(display_name="Alpha")
        self.beta = create_course(display_name="Beta")
        make_credit_course(self.course.id)
        self.client = api_client(UserFactory(is_staff=True))

    def get(self, **params):
        response = self.client.get(url(self.course.id), params)
        assert response.status_code == 200, response.content
        return response.json()

    @PREREQUISITES_ON
    def test_the_minimal_view_leaves_out_the_lists_and_credit_requirements(self):
        body = self.get()
        assert set(body) == MINIMAL_FIELDS
        assert (body["is_prerequisite_courses_enabled"], body["is_credit_course"]) == (True, True)

    @PREREQUISITES_ON
    def test_the_full_view_adds_the_lists_and_credit_requirements(self):
        body = self.get(**FULL)
        assert set(body) == MINIMAL_FIELDS | HEAVY_FIELDS
        assert body["language_options"] == [list(pair) for pair in settings.ALL_LANGUAGES]
        assert body["credit_requirements"] == {
            "grade": [{"name": "grade", "display_name": "Grade", "criteria": {"min_grade": 0.8}}],
        }
        prerequisites = body["possible_pre_requisite_courses"]
        assert [course["course_key"] for course in prerequisites] == [str(self.alpha.id), str(self.beta.id)]
        assert {member for course in prerequisites for member in course} == PREREQUISITE_MEMBERS

    def test_the_full_view_without_prerequisites_or_credit_adds_the_language_list_only(self):
        with override_settings(ENABLE_CREDIT_ELIGIBILITY=False):
            body = self.get(**FULL)
        assert set(body) == MINIMAL_FIELDS | {"language_options"}
        assert (body["is_prerequisite_courses_enabled"], body["is_credit_course"]) == (False, False)

    def test_the_minimal_view_is_the_default(self):
        assert self.get(view="minimal") == self.get()

    def test_an_unknown_view_is_refused(self):
        response = self.client.get(url(self.course.id), {"view": "default"})
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"view": ['"default" is not a valid choice.']}

    @ddt.data(*VIEWS)
    @PREREQUISITES_ON
    def test_the_course_list_parameters_change_nothing(self, view):
        """The prerequisite list is the same whatever course list parameters the request carries."""
        expected = self.get(**view_params(view))
        assert self.get(**view_params(view), **COURSE_LIST_PARAMETERS) == expected
        assert self.get(**view_params(view), start_date_on_or_after="not a date") == expected

    @PREREQUISITES_ON
    def test_the_course_listing_is_given_the_caller_and_no_query_parameters(self):
        target = "cms.djangoapps.contentstore.rest_api.v2.course_settings_service.get_course_settings"
        with patch(target, wraps=course_settings_service.get_course_settings) as listing:
            self.get(**FULL, **COURSE_LIST_PARAMETERS)
        (given, course_key, course), _ = listing.call_args
        assert dict(given.query_params) == {}
        assert dict(given.GET) == {}
        assert given.user.is_staff is True
        assert (course_key, course.id) == (self.course.id, self.course.id)

    @ddt.data(*VIEWS)
    @PREREQUISITES_ON
    def test_the_body_matches_the_published_schema(self, view):
        assert_matches_published_schema(self.get(**view_params(view)), "CourseSettings.v2")

    def test_content_experiments_enabled_follows_the_advanced_modules(self):
        assert self.get()["content_experiments_enabled"] is False
        turn_on_content_experiments(self.course.id)
        assert self.get()["content_experiments_enabled"] is True

    def test_a_course_without_a_name(self):
        course = modulestore().get_course(self.course.id)
        course.display_name = None
        modulestore().update_item(course, ModuleStoreEnum.UserID.test)
        body = self.get()
        assert body["course_display_name"] is None
        assert body["course_display_name_with_default"] == modulestore().get_course(
            self.course.id,
        ).display_name_with_default
        legacy = self.client.get(legacy_url(self.course.id)).json()
        assert legacy["course_display_name"] is None
        assert legacy["course_display_name_with_default"] == body["course_display_name_with_default"]

    @ddt.data(*VIEWS)
    def test_head_answers_without_a_body(self, view):
        response = self.client.head(url(self.course.id), view_params(view))
        assert response.status_code == 200
        assert response.content == b""


# Requests v1 and v2 answer differently beyond the error body, as
# (request, v1 answer, v2 answer, test in SettingsResponseDifferencesTest).
RESPONSE_DIFFERENCES = [
    ("read with course list parameters such as search", "prerequisites narrowed or reordered",
     "prerequisites unchanged", "test_course_list_parameters"),
    ("read with a malformed start_date_on_or_after", "400", "200", "test_a_malformed_start_date_parameter"),
    ("read with view=default", "200", "400", "test_an_unknown_view"),
    ("read of a course the modulestore returns nothing for", "500", "404",
     "test_a_course_the_modulestore_returns_nothing_for"),
    ("read with a Bearer token", "200", "401", "test_a_bearer_token"),
    ("slash-separated course key", "404 JSON", "404 HTML", "test_a_slash_separated_course_key"),
]


@PREREQUISITES_ON
class SettingsResponseDifferencesTest(ModuleStoreTestCase):
    """Each request v1 and v2 answer differently, both answers observed."""

    def setUp(self):
        super().setUp()
        self.course = create_course()
        self.alpha = create_course(display_name="Alpha")
        self.beta = create_course(display_name="Beta")
        self.client = api_client(UserFactory(is_staff=True))

    def both(self, **params):
        """The v1 and v2 (full view) responses to a read with ``params``."""
        legacy = self.client.get(legacy_url(self.course.id), params)
        return legacy, self.client.get(url(self.course.id), {**FULL, **params})

    def test_every_listed_difference_has_its_test(self):
        assert [name for *_, name in RESPONSE_DIFFERENCES if not hasattr(self, name)] == []

    def test_course_list_parameters(self):
        all_keys = [str(self.alpha.id), str(self.beta.id)]
        for params, narrowed in (
            ({"search": "Alpha"}, [str(self.alpha.id)]),
            ({"ordering": "-display_name"}, [str(self.beta.id), str(self.alpha.id)]),
        ):
            legacy, new = self.both(**params)
            assert legacy.status_code == new.status_code == 200
            assert [c["course_key"] for c in legacy.json()["possible_pre_requisite_courses"]] == narrowed, params
            assert [c["course_key"] for c in new.json()["possible_pre_requisite_courses"]] == all_keys, params

    def test_a_malformed_start_date_parameter(self):
        legacy, new = self.both(start_date_on_or_after="not a date")
        assert legacy.status_code == 400
        assert new.status_code == 200, new.content

    def test_an_unknown_view(self):
        legacy = self.client.get(legacy_url(self.course.id), {"view": "default"})
        new = self.client.get(url(self.course.id), {"view": "default"})
        assert legacy.status_code == 200
        assert_error_envelope(new, expected_status=400, expected_type_slug="validation")

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
        legacy = self.client.get("/api/contentstore/v1/course_settings/edX/NoSuch/Course")
        new = self.client.get("/api/authoring/v2/courses/edX/NoSuch/Course/settings/")
        assert legacy.status_code == new.status_code == 404
        assert legacy.json()["error_code"] == "course_does_not_exist"
        assert new["Content-Type"].startswith("text/html")


# ---------------------------------------------------------------------------
# Parity with v1
# ---------------------------------------------------------------------------

# Members of a response body that differ between v1 and v2 for the same
# caller and course. The comparisons assert nothing else differs, which of
# these each fixture produces, and (with every feature on) that each occurs.
INTENTIONAL_DIFFERENCES = [
    ("content_experiments_enabled", "added: whether content experiments apply to the course, which the v1 "
                                    "group configurations read reports as should_show_experiment_groups"),
    ("language_options", "returned only with view=full"),
    ("possible_pre_requisite_courses", "returned only with view=full"),
    ("credit_requirements", "returned only with view=full"),
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
    assert _undeclared({"content_experiments_enabled", "possible_pre_requisite_courses[0].url", "detail"}) == set()
    assert _undeclared({"is_credit_course", "upgrade_deadline", "language"}) == {
        "is_credit_course", "upgrade_deadline", "language",
    }


def test_roots_are_the_top_level_members():
    assert roots({"a", "b.c", "d[0].e", "f[1]"}) == {"a", "b", "d", "f"}


class _ParityMixin:
    """
    v2 against v1 for the same course and caller: the full view is the v1
    body plus content_experiments_enabled, the minimal view is that without
    the heavy members, and errors differ only by the declared differences.
    """

    def reader_for(self, courses):
        """Return a user who may view each of ``courses`` in Studio."""
        raise NotImplementedError

    def outsider(self):
        """Return a signed-in user who may not view the course."""
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.course = create_course(display_name="Parity")
        self.other_course = create_course(display_name="Other")
        self.reader = self.reader_for([self.course, self.other_course])
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

    def assert_parity(self, reader=None):
        """Compare both views with v1 for ``reader``; return the v1 body."""
        client = api_client(reader or self.reader)
        legacy = client.get(legacy_url(self.course.id))
        legacy_body, full = self.compare(legacy, client.get(url(self.course.id), FULL), 200)
        assert changed_paths(legacy_body, full) == {"content_experiments_enabled"}
        _, minimal = self.compare(legacy, client.get(url(self.course.id)), 200)
        assert roots(changed_paths(legacy_body, minimal)) == {"content_experiments_enabled"} | (
            HEAVY_FIELDS & set(legacy_body)
        )
        assert minimal == {name: value for name, value in full.items() if name not in HEAVY_FIELDS}
        return legacy_body

    def test_both_views_match_v1(self):
        legacy_body = self.assert_parity()
        assert "language_options" in legacy_body

    def test_global_staff_get_what_v1_gives_them(self):
        self.assert_parity(UserFactory(is_staff=True))

    def test_content_experiments_enabled_is_the_v1_group_configurations_flag(self):
        staff = api_client(UserFactory(is_staff=True))
        address = reverse(GROUP_CONFIGURATIONS_URL_NAME, kwargs={"course_id": str(self.course.id)})
        for turned_on in (False, True):
            if turned_on:
                turn_on_content_experiments(self.course.id)
            body = api_client(self.reader).get(url(self.course.id)).json()
            group_configurations = staff.get(address)
            assert group_configurations.status_code == 200, group_configurations.content
            assert body["content_experiments_enabled"] is turned_on
            assert group_configurations.json()["should_show_experiment_groups"] is turned_on
            self.assert_parity()

    @override_settings(ENABLE_PUBLISHER=True)
    def test_a_course_managed_by_a_publishing_service(self):
        legacy_body = self.assert_parity()
        assert legacy_body["about_page_editable"] is False
        assert legacy_body["enrollment_end_editable"] is False
        staff_body = self.assert_parity(UserFactory(is_staff=True))
        assert staff_body["enrollment_end_editable"] is True

    def test_a_course_with_a_verified_mode_deadline(self):
        CourseModeFactory(
            course_id=self.course.id,
            mode_slug=CourseMode.VERIFIED,
            expiration_datetime=datetime(2030, 1, 1, tzinfo=UTC),
        )
        legacy_body = self.assert_parity()
        assert legacy_body["upgrade_deadline"] == "2030-01-01T00:00:00+00:00"

    def _error_pairs(self):
        """``(legacy, new, status)`` for each error both addresses produce, on both views."""
        for client, course_key, status in (
            (api_client(), self.course.id, 401),
            (api_client(self.outsider()), self.course.id, 403),
            (api_client(self.outsider()), UNKNOWN_COURSE_KEY, 404),
        ):
            legacy = client.get(legacy_url(course_key))
            for params in ({}, FULL):
                yield legacy, client.get(url(course_key), params), status

    def test_errors_differ_only_by_the_envelope(self):
        for legacy, new, status in self._error_pairs():
            self.compare(legacy, new, status)


class _AllFeaturesParityMixin(_ParityMixin):
    """
    Parity with prerequisite courses on and a credit course, so every member
    v1 can return is returned, and every declared difference occurs.
    """

    def setUp(self):
        super().setUp()
        make_credit_course(self.course.id)

    def test_the_lists_and_credit_requirements_match_v1(self):
        legacy_body = self.assert_parity()
        assert HEAVY_FIELDS <= set(legacy_body)
        keys = [course["course_key"] for course in legacy_body["possible_pre_requisite_courses"]]
        assert keys == [str(self.other_course.id)]
        assert list(legacy_body["credit_requirements"]) == ["grade"]

    def test_a_credit_course_without_a_minimum_grade_warns(self):
        course = modulestore().get_course(self.course.id)
        course.minimum_grade_credit = 0
        modulestore().update_item(course, ModuleStoreEnum.UserID.test)
        assert self.assert_parity()["show_min_grade_warning"] is True

    def test_courses_being_created_as_reruns_are_not_prerequisites(self):
        """Global staff list every course; one whose rerun is still running is left out by both."""
        staff = UserFactory(is_staff=True)
        rerun = create_course(display_name="Rerun in progress")
        CourseRerunState.objects.initiated(self.other_course.id, rerun.id, staff, "Rerun in progress")
        legacy_body = self.assert_parity(staff)
        keys = {course["course_key"] for course in legacy_body["possible_pre_requisite_courses"]}
        assert str(self.other_course.id) in keys
        assert str(rerun.id) not in keys

    def test_every_declared_difference_occurs(self):
        self.assert_parity()
        for legacy, new, status in self._error_pairs():
            self.compare(legacy, new, status)
        missing = [
            path for path, _ in INTENTIONAL_DIFFERENCES
            if not any(key == path or key.startswith(f"{path}.") or key.startswith(f"{path}[") for key in self.observed)
        ]
        assert missing == []


class _LegacyPermissionsReaders:
    """Readers and outsiders with policy-based authoring permissions off."""

    def reader_for(self, courses):
        reader = UserFactory()
        for course in courses:
            CourseStaffRole(course.id).add_users(reader)
        return reader

    def outsider(self):
        return UserFactory()


class _AuthzReaders:
    """Readers and outsiders with policy-based authoring permissions on."""

    def reader_for(self, courses):
        reader = UserFactory()
        for course in courses:
            self.add_user_to_role_in_course(reader, COURSE_STAFF.external_key, course.id)
        return reader

    def outsider(self):
        return self.unauthorized_user


class SettingsParityLegacyPermissionsTest(_LegacyPermissionsReaders, _ParityMixin, ModuleStoreTestCase):
    """Parity with prerequisite courses off and a course that is not a credit course."""


class SettingsParityAuthzTest(_AuthzReaders, _ParityMixin, CourseAuthoringAuthzTestMixin, ModuleStoreTestCase):
    """As above, with policy-based authoring permissions on."""


@PREREQUISITES_ON
@override_settings(ENABLE_CREDIT_ELIGIBILITY=True)
class SettingsParityAllFeaturesLegacyPermissionsTest(
    _LegacyPermissionsReaders, _AllFeaturesParityMixin, ModuleStoreTestCase,
):
    """Parity with prerequisite courses and credit eligibility on, for a credit course."""


@PREREQUISITES_ON
@override_settings(ENABLE_CREDIT_ELIGIBILITY=True)
class SettingsParityAllFeaturesAuthzTest(
    _AuthzReaders, _AllFeaturesParityMixin, CourseAuthoringAuthzTestMixin, ModuleStoreTestCase,
):
    """As above, with policy-based authoring permissions on."""


@override_settings(ENABLE_CREDIT_ELIGIBILITY=False)
class SettingsParityCreditOffLegacyPermissionsTest(_LegacyPermissionsReaders, _ParityMixin, ModuleStoreTestCase):
    """Parity with credit eligibility off: a registered credit course is reported as not one."""

    def test_a_registered_credit_course_is_not_reported(self):
        make_credit_course(self.course.id)
        legacy_body = self.assert_parity()
        assert (legacy_body["credit_eligibility_enabled"], legacy_body["is_credit_course"]) == (False, False)
        assert "credit_requirements" not in legacy_body


@override_settings(ENABLE_CREDIT_ELIGIBILITY=False)
class SettingsParityCreditOffAuthzTest(
    _AuthzReaders, _ParityMixin, CourseAuthoringAuthzTestMixin, ModuleStoreTestCase,
):
    """As above, with policy-based authoring permissions on."""

    def test_a_registered_credit_course_is_not_reported(self):
        make_credit_course(self.course.id)
        legacy_body = self.assert_parity()
        assert (legacy_body["credit_eligibility_enabled"], legacy_body["is_credit_course"]) == (False, False)
        assert "credit_requirements" not in legacy_body


# ---------------------------------------------------------------------------
# Side effects of reads
# ---------------------------------------------------------------------------

# What a read may write on a database that has never served a role or
# permission lookup: nothing, or the permission library's policy version row.
FIRST_READ_EFFECTS: tuple[tuple[list[tuple[str, str]], list[tuple[str, str]]], ...] = (
    ([], []),
    (FIRST_LOOKUP_WRITES, FIRST_LOOKUP_SIGNALS),
)
READS = [("get", {}), ("get", FULL), ("head", {}), ("head", FULL)]


def fresh_client(user):
    """
    Return a client authenticated as a new instance of ``user``. Role lookups
    are cached on the user instance, so reusing one instance across reads
    would let a later read skip the lookup an earlier read made.
    """
    return api_client(User.objects.get(pk=user.pk))


class _ReadsAreSideEffectFreeMixin:
    """
    Every read (both views, GET and HEAD) by course staff, by global staff and
    by a course reader whose role records send the course listing to its
    every-course fallback writes no row and no Mongo document, stores nothing
    through the modulestore and sends no signal or event. On a database that
    has never served a role or permission lookup, a read may insert the
    permission library's one policy version row, and does exactly what the v1
    read does.

    The course is a credit course with a verified mode, and another course has
    a rerun still running, so every lookup a read can make has a row to find.
    """

    # Whether prerequisite courses are on, so that a read lists courses.
    lists_prerequisites = False

    def reader_for(self, courses):
        """Return a user who may view each of ``courses`` in Studio."""
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.course = create_course()
        self.other_course = create_course()
        make_credit_course(self.course.id)
        CourseModeFactory(
            course_id=self.course.id,
            mode_slug=CourseMode.VERIFIED,
            expiration_datetime=datetime(2030, 1, 1, tzinfo=UTC),
        )
        self.rerun = create_course()
        CourseRerunState.objects.initiated(self.other_course.id, self.rerun.id, UserFactory(), "Rerun in progress")

    def readers(self):
        """
        Return the readers by name. The fallback reader also holds a staff role
        record with neither a course nor an organization, which makes the
        course listing consider every course and check the running reruns
        against the caller's permission, as it does for global staff.
        """
        fallback = self.reader_for([self.course, self.other_course])
        CourseAccessRole.objects.create(user=fallback, role=CourseStaffRole.ROLE, org="", course_id=None)
        return {
            "course reader": self.reader_for([self.course, self.other_course]),
            "global staff": UserFactory(is_staff=True),
            "fallback reader": fallback,
        }

    def test_each_reader_reaches_its_course_listing_branch(self):
        """
        With prerequisite courses on, the course listing checks the running
        rerun against the permission of global staff and of the fallback
        reader, and not of the course reader, whose courses come from its
        roles. With them off, no read lists courses.
        """
        listing_checks = {"course reader": False, "global staff": True, "fallback reader": True}
        reads = ((url(self.course.id), FULL), (url(self.course.id), {}), (legacy_url(self.course.id), {}))
        for name, reader in self.readers().items():
            for address, params in reads:
                with (
                    patch.object(course_views, "get_courses_accessible_to_user",
                                 wraps=course_views.get_courses_accessible_to_user) as listing,
                    patch.object(course_views, "get_in_process_course_actions",
                                 wraps=course_views.get_in_process_course_actions) as running,
                    patch.object(course_views, "user_has_course_permission",
                                 wraps=course_views.user_has_course_permission) as row_check,
                ):
                    response = api_client(reader).get(address, params)
                assert response.status_code == 200, (name, address, response.content)
                assert listing.call_count == int(self.lists_prerequisites), (name, address)
                checks_rows = self.lists_prerequisites and listing_checks[name]
                assert running.call_count == int(checks_rows), (name, address)
                checked = [call.args[2] for call in row_check.call_args_list]
                assert checked == ([self.rerun.id] if checks_rows else []), (name, address)

    def _read_on_a_fresh_database(self, reader, address, method, params):
        """Record the effects of one read made after the policy version row is removed."""
        PolicyCacheControl.objects.all().delete()
        client = fresh_client(reader)
        with record_effects(self) as effects:
            response = getattr(client, method)(address, params)
        assert response.status_code == 200, response.content
        return effects

    def test_reads_have_no_side_effects(self):
        """
        Each read writes and sends exactly what the v1 read does on a database
        that has never served a lookup, and nothing at all on one that has.
        Every read is made by a new instance of the reader, so no role lookup
        cached on the instance of an earlier read hides the lookup this one makes.
        """
        for name, reader in self.readers().items():
            legacy = self._read_on_a_fresh_database(reader, legacy_url(self.course.id), "get", {})
            first_effects = (legacy["sql_writes"], legacy["signals"])
            assert first_effects in FIRST_READ_EFFECTS, name
            for method, params in READS:
                read = (name, method, params)
                first = self._read_on_a_fresh_database(reader, url(self.course.id), method, params)
                assert (first["sql_writes"], first["signals"]) == first_effects, read
                client = fresh_client(reader)
                with record_effects(self) as again:
                    response = getattr(client, method)(url(self.course.id), params)
                assert response.status_code == 200, (read, response.content)
                assert again["sql_writes"] == [], read
                assert again["signals"] == [], read
                for effects in (first, again):
                    assert effects["events"] == [], read
                    assert effects["mongo_writes"] == 0, read
                    assert effects["update_item"] == [], read

    def test_a_course_without_an_overview_is_read_without_building_one(self):
        """Building a course overview saves it and sends signals, which a read must not do; v1 builds none either."""
        course = CourseFactory.create()
        client = api_client(UserFactory(is_staff=True))
        for params in ({}, FULL):
            assert client.get(url(course.id), params).status_code == 200
        assert not CourseOverview.objects.filter(id=course.id).exists()
        assert client.get(legacy_url(course.id)).status_code == 200
        assert not CourseOverview.objects.filter(id=course.id).exists()


class SettingsReadSideEffectsLegacyPermissionsTest(
    _LegacyPermissionsReaders, _ReadsAreSideEffectFreeMixin, ModuleStoreTestCase,
):
    """Reads with policy-based authoring permissions off and prerequisite courses off."""


@PREREQUISITES_ON
class SettingsReadSideEffectsPrerequisitesLegacyPermissionsTest(
    _LegacyPermissionsReaders, _ReadsAreSideEffectFreeMixin, ModuleStoreTestCase,
):
    """Reads with policy-based authoring permissions off and prerequisite courses on."""

    lists_prerequisites = True


class SettingsReadSideEffectsAuthzTest(
    _AuthzReaders, _ReadsAreSideEffectFreeMixin, CourseAuthoringAuthzTestMixin, ModuleStoreTestCase,
):
    """Reads with policy-based authoring permissions on and prerequisite courses off."""


@PREREQUISITES_ON
class SettingsReadSideEffectsPrerequisitesAuthzTest(
    _AuthzReaders, _ReadsAreSideEffectFreeMixin, CourseAuthoringAuthzTestMixin, ModuleStoreTestCase,
):
    """Reads with policy-based authoring permissions on and prerequisite courses on."""

    lists_prerequisites = True


# ---------------------------------------------------------------------------
# Query counts
# ---------------------------------------------------------------------------

class _QueryCountMixin:
    """
    Each view costs no more SQL queries or Mongo reads than the v1 read, with
    prerequisite courses on and a credit course, by course staff and global
    staff.
    """

    def reader_for(self, courses):
        """Return a user who may view each of ``courses`` in Studio."""
        raise NotImplementedError

    def test_each_view_costs_no_more_than_the_v1_read(self):
        course, other_course = create_course(), create_course()
        make_credit_course(course.id)
        for reader in (self.reader_for([course, other_course]), UserFactory(is_staff=True)):
            client = api_client(reader)
            legacy = measure(lambda client=client: client.get(legacy_url(course.id)))
            for params in ({}, FULL):
                new = measure(lambda client=client, params=params: client.get(url(course.id), params))
                assert new[0] <= legacy[0], (reader.is_staff, params, new, legacy)
                assert new[1] <= legacy[1], (reader.is_staff, params, new, legacy)
                assert new[2] == legacy[2] == 0, (reader.is_staff, params, new, legacy)


@PREREQUISITES_ON
class SettingsQueryCountLegacyPermissionsTest(_LegacyPermissionsReaders, _QueryCountMixin, ModuleStoreTestCase):
    """Costs with policy-based authoring permissions off."""


@PREREQUISITES_ON
class SettingsQueryCountAuthzTest(
    _AuthzReaders, CourseAuthoringAuthzTestMixin, _QueryCountMixin, ModuleStoreTestCase,
):
    """Costs with policy-based authoring permissions on."""


# ---------------------------------------------------------------------------
# Addresses and methods
# ---------------------------------------------------------------------------

@ddt.ddt
class SettingsRoutingTest(SharedModuleStoreTestCase):
    """The address, the v1 address, and requests no route or method serves."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def test_the_address(self):
        assert url(COURSE_KEY) == f"/api/authoring/v2/courses/{COURSE_KEY}/settings/"
        match = resolve(url(COURSE_KEY))
        assert match.func.cls is CourseSettingsViewSet
        assert routed_actions(match.func) == {"get": "retrieve"}

    def test_the_v1_address_is_unchanged(self):
        assert legacy_url(COURSE_KEY) == f"/api/contentstore/v1/course_settings/{COURSE_KEY}"
        view = resolve(legacy_url(COURSE_KEY)).func.cls
        assert (view.__module__, view.__name__) == LEGACY_VIEW

    def test_v1_still_takes_a_slash_separated_key_that_v2_does_not(self):
        view = resolve("/api/contentstore/v1/course_settings/edX/DemoX/Demo_Course").func.cls
        assert (view.__module__, view.__name__) == LEGACY_VIEW
        with pytest.raises(Resolver404):
            resolve("/api/authoring/v2/courses/edX/DemoX/Demo_Course/settings/")

    def test_the_url_names_are_unique_in_the_namespace(self):
        # Imported here: at module import the URL converters are not registered yet.
        from cms.djangoapps.contentstore.rest_api.v2 import authoring_urls  # noqa: PLC0415
        names = [pattern.name for pattern in authoring_urls.urlpatterns]
        assert len(names) == len(set(names))
        assert {"course_settings", "course_rerun_defaults"} <= set(names)

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
        response = api_client(UserFactory(is_staff=True)).get(f"/api/authoring/v2/courses/{key}/settings/")
        assert response.status_code == 404
        assert response["Content-Type"].startswith("text/html")

    def test_the_address_without_its_trailing_slash_redirects_to_it(self):
        """No route answers the address without its slash; the service redirects a read to the slashed one."""
        address = f"/api/authoring/v2/courses/{self.course.id}/settings"
        response = api_client(UserFactory(is_staff=True)).get(address)
        assert response.status_code == 301
        assert response["Location"] == url(self.course.id)
        with pytest.raises(Resolver404):
            resolve(address)


# ---------------------------------------------------------------------------
# The published schema
# ---------------------------------------------------------------------------

class SettingsSchemaTest(TestCase):
    """The read as published in the CMS authoring schema."""

    PATH = "/api/authoring/v2/courses/{course_key}/settings/"
    LEGACY = "/api/contentstore/v1/course_settings/{course_id}"

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
        assert operation["operationId"] == "v2_courses_settings_retrieve"
        assert operation["tags"] == ["openedx-platform-sdk"]
        assert "deprecated" not in operation
        assert self._ref(operation["responses"]["200"]) == "CourseSettings.v2"
        for status in ("400", "401", "403", "404"):
            assert self._ref(operation["responses"][status]) == "ErrorResponse", status

    def test_the_parameters(self):
        parameters = {p["name"]: p for p in self.schema["paths"][self.PATH]["get"]["parameters"]}
        assert set(parameters) == {"course_key", "view"}
        assert parameters["course_key"]["in"] == "path"
        assert parameters["course_key"]["schema"] == {"type": "string", "pattern": "^[^/]+$"}
        assert parameters["view"]["in"] == "query"
        assert not parameters["view"].get("required")
        assert parameters["view"]["schema"] == {"type": "string", "enum": ["full", "minimal"], "default": "minimal"}

    def test_the_settings_component(self):
        component = self.components["CourseSettings.v2"]
        assert set(component["properties"]) == MINIMAL_FIELDS | HEAVY_FIELDS
        assert set(component["required"]) == MINIMAL_FIELDS
        nullable = {name for name, field in component["properties"].items() if field.get("nullable")}
        assert nullable == {"course_display_name", "upgrade_deadline"}
        prerequisites = component["properties"]["possible_pre_requisite_courses"]
        assert prerequisites["items"] == {"$ref": "#/components/schemas/PrerequisiteCourse.v2"}
        requirements = component["properties"]["credit_requirements"]["additionalProperties"]
        assert requirements["items"] == {"$ref": "#/components/schemas/CreditRequirement.v2"}

    def test_the_prerequisite_course_component(self):
        component = self.components["PrerequisiteCourse.v2"]
        assert set(component["properties"]) == PREREQUISITE_MEMBERS
        assert set(component["required"]) == PREREQUISITE_MEMBERS
        nullable = {name for name, field in component["properties"].items() if field.get("nullable")}
        assert nullable == {"display_name", "lms_link", "rerun_link", "url"}

    def test_every_member_is_described(self):
        for name in ("CourseSettings.v2", "PrerequisiteCourse.v2", "CreditRequirement.v2"):
            component = self.components[name]
            assert component.get("description"), name
            properties = component["properties"]
            assert [field for field, spec in properties.items() if not spec.get("description")] == [], name

    def test_the_examples_are_valid_bodies(self):
        content = self.schema["paths"][self.PATH]["get"]["responses"]["200"]["content"]["application/json"]
        examples = content["examples"]
        assert set(examples) == {"CourseSettings", "CourseSettingsWithLists"}
        assert set(examples["CourseSettings"]["value"]) == MINIMAL_FIELDS
        for example in examples.values():
            assert_matches_published_schema(example["value"], "CourseSettings.v2")

    def test_the_v1_read_is_deprecated_and_left_out_of_the_sdk(self):
        operations = self.schema["paths"][self.LEGACY]
        assert sorted(operations) == ["get"]
        assert operations["get"]["operationId"] == "v1_course_settings_retrieve"
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
