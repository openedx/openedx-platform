"""
Tests for the AuthZ enforcement of the Instructor Dashboard API v2.

They cover the tabs, ``authz_enabled`` and the migrated endpoints.
"""

import json
from itertools import product
from textwrap import dedent
from unittest.mock import patch

import ddt
from django.urls import reverse
from openedx_authz.applets.course_team_roles.constants import (
    COURSE_AUDITOR,
    COURSE_BETA_TESTER,
    COURSE_DATA_RESEARCHER,
    COURSE_EDITOR,
    COURSE_LIMITED_STAFF,
)
from openedx_authz.applets.learner_administration.constants import COURSES_VIEW_COURSE_INFO
from openedx_authz.applets.learner_grading.constants import COURSES_MANAGE_LEARNER_GRADING, COURSES_VIEW_GRADEBOOK
from openedx_authz.authz import COURSE_ADMIN, COURSE_STAFF, COURSES_VIEW_GRADING_SETTINGS
from rest_framework import status
from rest_framework.test import APIClient

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import CourseBetaTesterRole, CourseDataResearcherRole, CourseLimitedStaffRole
from common.djangoapps.student.tests.factories import (
    InstructorFactory,
    StaffFactory,
    UserFactory,
)
from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.courseware.tabs import get_course_tab_list
from lms.djangoapps.instructor.authz import constants
from lms.djangoapps.instructor.authz.compat import LEGACY_ROLE_COMPAT_PERMISSIONS
from lms.djangoapps.instructor.authz.tests.mixins import InstructorAuthzTestMixin
from lms.djangoapps.instructor_task.models import InstructorTask
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory

API = "lms.djangoapps.instructor.authz.api"

# Tabs migrated to AuthZ, each with one of the permissions that show it.
MIGRATED_TABS = {tab: permissions[0] for tab, permissions in constants.TAB_PERMISSIONS.items()}

# Every (tab, permission) pair: a single permission of the tab is enough to show it.
TAB_PERMISSION_PAIRS = [
    (tab, permission) for tab, permissions in constants.TAB_PERMISSIONS.items() for permission in permissions
]


def allowed_only(*permissions):
    """
    Build an ``is_user_allowed`` side effect that grants only the given permissions.
    """
    return lambda _username, permission, _scope: permission in permissions


def allowed_all_except(*permissions):
    """
    Build an ``is_user_allowed`` side effect that grants every permission but the given ones.
    """
    return lambda _username, permission, _scope: permission not in permissions


@ddt.ddt
class CourseMetadataViewAuthzTest(InstructorAuthzTestMixin, SharedModuleStoreTestCase):
    """
    ``CourseMetadataView`` tabs and permissions follow the AuthZ flag.

    With the flag on they are resolved through AuthZ, and through the legacy
    roles when it is off.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.course_key = cls.course.id

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        # The Date Extensions tab is only shown when edx-when is enabled for the course.
        patcher = patch("lms.djangoapps.instructor.views.serializers_v2.is_enabled_for_course", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.url = reverse("instructor_api_v2:course_metadata", kwargs={"course_id": str(self.course_key)})

    def _get(self, user):
        self.client.force_authenticate(user=user)
        return self.client.get(self.url)

    def _tab_ids(self, response):
        return {tab["tab_id"] for tab in response.data["tabs"]}

    def test_existing_permissions_field_is_unchanged(self):
        """The legacy ``permissions`` field keeps the same keys."""
        user = self.create_user_with_authz_role(COURSE_ADMIN.external_key, self.course_key)

        response = self._get(user)

        assert response.status_code == status.HTTP_200_OK
        assert set(response.data["permissions"]) == {
            "admin",
            "instructor",
            "finance_admin",
            "sales_admin",
            "staff",
            "forum_admin",
            "data_researcher",
        }

    @ddt.data(COURSE_ADMIN, COURSE_STAFF)
    def test_authz_role_sees_migrated_tabs(self, role):
        """A Course Admin and a Course Staff see exactly the migrated tabs, and AuthZ is reported as enabled."""
        user = self.create_user_with_authz_role(role.external_key, self.course_key)

        response = self._get(user)

        assert response.status_code == status.HTTP_200_OK
        assert self._tab_ids(response) == set(MIGRATED_TABS)
        assert response.data["authz_enabled"] is True

    def test_authz_denied_overrides_legacy_roles(self):
        """
        With the flag on, AuthZ denying a legacy instructor means no access.
        """
        instructor = InstructorFactory(course_key=self.course_key)

        with patch(f"{API}.authz_api.is_user_allowed", return_value=False):
            response = self._get(instructor)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error_code"] == "permission_denied"
        assert response.data["developer_message"]

    def test_authz_limited_staff_has_the_legacy_access(self):
        """
        With the flag on, the legacy limited staff gets the same tabs as with the flag off.

        AuthZ defines no dashboard permission for ``course_limited_staff``, which had the
        access of the course staff before AuthZ.
        """
        self.set_authz_enabled(False)
        legacy_user = UserFactory()
        CourseLimitedStaffRole(self.course_key).add_users(legacy_user)
        legacy_response = self._get(legacy_user)

        self.set_authz_enabled(True)
        user = self.create_user_with_authz_role(COURSE_LIMITED_STAFF.external_key, self.course_key)
        response = self._get(user)

        assert legacy_response.status_code == status.HTTP_200_OK
        assert response.status_code == status.HTTP_200_OK
        assert self._tab_ids(response)
        assert self._tab_ids(response) == self._tab_ids(legacy_response)

    def test_authz_beta_tester_keeps_the_legacy_denial(self):
        """With the flag on, the legacy beta tester is denied, as it was before AuthZ."""
        user = self.create_user_with_authz_role(COURSE_BETA_TESTER.external_key, self.course_key)

        assert self._get(user).status_code == status.HTTP_403_FORBIDDEN

    def test_authz_data_researcher_sees_only_data_downloads(self):
        """
        With the flag on, a data researcher keeps the access it had before AuthZ.

        It opens the dashboard, but only sees the Data Downloads tab.
        """
        user = self.create_user_with_authz_role(COURSE_DATA_RESEARCHER.external_key, self.course_key)

        response = self._get(user)

        assert response.status_code == status.HTTP_200_OK
        assert self._tab_ids(response) == {"data_downloads"}

    def test_legacy_data_researcher_sees_only_data_downloads(self):
        """With the flag off, a data researcher sees the same tab through the legacy role."""
        self.set_authz_enabled(False)
        user = UserFactory()
        CourseDataResearcherRole(self.course_key).add_users(user)

        response = self._get(user)

        assert response.status_code == status.HTTP_200_OK
        assert self._tab_ids(response) == {"data_downloads"}

    @ddt.data(COURSE_AUDITOR.external_key, COURSE_EDITOR.external_key, COURSE_STAFF.external_key)
    def test_authz_other_roles_do_not_get_data_downloads(self, role):
        """With the flag on, the data researcher permission does not leak into other roles."""
        user = self.create_user_with_authz_role(role, self.course_key)

        response = self._get(user)

        assert "data_downloads" not in self._tab_ids(response)

    @ddt.data(*constants.INSTRUCTOR_DASHBOARD_PERMISSIONS)
    def test_authz_any_dashboard_permission_grants_access(self, permission):
        """With the flag on, a single dashboard permission, not only ``view_course_info``, grants access."""
        user = UserFactory()

        with patch(f"{API}.authz_api.is_user_allowed", side_effect=lambda _u, perm, _s: perm == permission):
            response = self._get(user)

        assert response.status_code == status.HTTP_200_OK

    @ddt.data(*TAB_PERMISSION_PAIRS)
    @ddt.unpack
    def test_authz_tab_shown_with_any_of_its_permissions(self, tab_id, tab_permission):
        """
        A tab is shown with any one of its permissions, and hidden without all of them.
        """
        user = UserFactory()

        with patch(
            f"{API}.authz_api.is_user_allowed",
            side_effect=allowed_only(COURSES_VIEW_COURSE_INFO.identifier),
        ):
            tabs_without_permission = self._tab_ids(self._get(user))
        with patch(
            f"{API}.authz_api.is_user_allowed",
            side_effect=allowed_only(COURSES_VIEW_COURSE_INFO.identifier, tab_permission),
        ):
            tabs_with_permission = self._tab_ids(self._get(user))

        assert tab_id in tabs_with_permission
        if tab_id != "course_info":
            assert tab_id not in tabs_without_permission

    @ddt.data(
        (InstructorFactory, set()),
        # Staff without the forum Administrator role does not see Course Team, and without
        # instructor access does not see Date Extensions.
        (StaffFactory, {"course_team", "date_extensions"}),
    )
    @ddt.unpack
    def test_legacy_role_sees_its_migrated_tabs(self, user_factory, hidden_tabs):
        """With the flag off, the legacy role decides the migrated tabs and AuthZ is reported as disabled."""
        self.set_authz_enabled(False)
        user = user_factory(course_key=self.course_key)

        response = self._get(user)

        assert response.status_code == status.HTTP_200_OK
        assert self._tab_ids(response) & set(MIGRATED_TABS) == set(MIGRATED_TABS) - hidden_tabs
        assert response.data["authz_enabled"] is False

    def test_legacy_does_not_query_authz(self):
        """With the flag off, AuthZ is not queried."""
        self.set_authz_enabled(False)
        instructor = InstructorFactory(course_key=self.course_key)

        with patch(f"{API}.authz_api.is_user_allowed") as is_user_allowed:
            response = self._get(instructor)

        assert response.status_code == status.HTTP_200_OK
        is_user_allowed.assert_not_called()


@ddt.ddt
class InstructorDashboardTabAuthzTest(InstructorAuthzTestMixin, SharedModuleStoreTestCase):
    """
    The "Instructor" course tab of the LMS follows the AuthZ flag.

    With the flag on it is shown when the user holds at least one of the Instructor
    Dashboard permissions, and through the legacy roles when it is off.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def _has_instructor_tab(self, user):
        return any(tab.type == "instructor" for tab in get_course_tab_list(user, self.course))

    def test_authz_role_with_permissions_shown(self):
        """With the flag on, a role that holds dashboard permissions sees the tab."""
        user = self.create_user_with_authz_role(COURSE_STAFF.external_key, self.course.id)

        assert self._has_instructor_tab(user)

    def test_authz_limited_staff_shown(self):
        """With the flag on, the legacy limited staff sees the tab, as it did before AuthZ."""
        user = self.create_user_with_authz_role(COURSE_LIMITED_STAFF.external_key, self.course.id)

        assert self._has_instructor_tab(user)

    def test_authz_data_researcher_shown(self):
        """With the flag on, a data researcher sees the tab, as it did before AuthZ."""
        user = self.create_user_with_authz_role(COURSE_DATA_RESEARCHER.external_key, self.course.id)

        assert self._has_instructor_tab(user)

    @ddt.data(*constants.INSTRUCTOR_DASHBOARD_PERMISSIONS)
    def test_authz_instructor_course_tab_shown_with_any_dashboard_permission(self, permission):
        """With the flag on, a single dashboard permission is enough to show the "Instructor" course tab."""
        user = UserFactory()

        with patch(f"{API}.authz_api.is_user_allowed", side_effect=lambda _u, perm, _c: perm == permission):
            assert self._has_instructor_tab(user)

    def test_authz_without_permissions_hidden(self):
        """With the flag on, a user holding none of the permissions does not see the tab."""
        user = UserFactory()

        assert not self._has_instructor_tab(user)

    def test_authz_denied_overrides_legacy_staff(self):
        """With the flag on, legacy staff without AuthZ permissions does not see the tab."""
        staff = StaffFactory(course_key=self.course.id)

        with patch(f"{API}.authz_api.is_user_allowed", return_value=False):
            assert not self._has_instructor_tab(staff)

    def test_legacy_staff_shown(self):
        """With the flag off, a legacy staff user sees the tab without querying AuthZ."""
        self.set_authz_enabled(False)
        staff = StaffFactory(course_key=self.course.id)

        with patch(f"{API}.authz_api.is_user_allowed") as is_user_allowed:
            assert self._has_instructor_tab(staff)

        is_user_allowed.assert_not_called()

    def test_legacy_without_role_hidden(self):
        """With the flag off, a user without any role does not see the tab."""
        self.set_authz_enabled(False)

        assert not self._has_instructor_tab(UserFactory())


# Grading tab requests: the AuthZ permission each one requires and the status it returns on success.
GRADING_ACTIONS = {
    "view_learner": (COURSES_VIEW_GRADEBOOK.identifier, status.HTTP_200_OK),
    "view_problem": (COURSES_VIEW_GRADEBOOK.identifier, status.HTTP_200_OK),
    "view_grading_config": (COURSES_VIEW_GRADING_SETTINGS.identifier, status.HTTP_200_OK),
    "reset_attempts": (COURSES_MANAGE_LEARNER_GRADING.identifier, status.HTTP_200_OK),
    "delete_state": (COURSES_MANAGE_LEARNER_GRADING.identifier, status.HTTP_200_OK),
    "rescore": (COURSES_MANAGE_LEARNER_GRADING.identifier, status.HTTP_202_ACCEPTED),
    "override_score": (COURSES_MANAGE_LEARNER_GRADING.identifier, status.HTTP_202_ACCEPTED),
    "reset_attempts_all_learners": (COURSES_MANAGE_LEARNER_GRADING.identifier, status.HTTP_202_ACCEPTED),
    "rescore_all_learners": (COURSES_MANAGE_LEARNER_GRADING.identifier, status.HTTP_202_ACCEPTED),
}

# All Learners actions. With the flag off, they also require instructor access.
ALL_LEARNERS_GRADING_ACTIONS = ("reset_attempts_all_learners", "rescore_all_learners")

# The reads and the Single Learner actions, which legacy course staff can use with the flag off.
LEGACY_STAFF_GRADING_ACTIONS = tuple(
    action for action in GRADING_ACTIONS if action not in ALL_LEARNERS_GRADING_ACTIONS
)

# Legacy roles that openedx-authz maps without dashboard permissions (see ``authz/compat.py``),
# each with its AuthZ role.
LEGACY_COMPAT_ROLES = (
    (COURSE_LIMITED_STAFF.external_key, CourseLimitedStaffRole),
    (COURSE_DATA_RESEARCHER.external_key, CourseDataResearcherRole),
    (COURSE_BETA_TESTER.external_key, CourseBetaTesterRole),
)


@ddt.ddt
class GradingTabAuthzTest(InstructorAuthzTestMixin, SharedModuleStoreTestCase):
    """
    The Grading tab endpoints follow the AuthZ flag.

    With the flag on, ``LearnerView`` and ``ProblemView`` require
    ``courses.view_gradebook``, ``GradingConfigView`` requires
    ``courses.view_grading_settings``, and the Single Learner and All Learners
    actions require ``courses.manage_learner_grading``. With the flag off,
    each view uses its legacy bridgekeeper permission, and the All Learners
    actions also require instructor access.

    The functional behavior of each endpoint, with a legacy instructor, is
    covered in ``tests/views/test_api_v2.py``.
    """

    PROBLEM_XML = dedent("""\
        <problem>
          <optionresponse>
            <optioninput options="('Option 1','Option 2')" correct="Option 1" />
          </optionresponse>
        </problem>
    """)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.course_key = cls.course.id
        chapter = BlockFactory.create(parent=cls.course, category="chapter")
        sequential = BlockFactory.create(parent=chapter, category="sequential")
        cls.problem = BlockFactory.create(parent=sequential, category="problem", data=cls.PROBLEM_XML)

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        # The asynchronous actions only queue their task, and deleting state skips the grade signal.
        for target in (
            "lms.djangoapps.instructor_task.tasks.reset_problem_attempts.apply_async",
            "lms.djangoapps.instructor_task.tasks.rescore_problem.apply_async",
            "lms.djangoapps.instructor_task.tasks.override_problem_score.apply_async",
            "lms.djangoapps.grades.signals.handlers.PROBLEM_WEIGHTED_SCORE_CHANGED.send",
        ):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.learner = UserFactory()
        CourseEnrollment.enroll(self.learner, self.course_key)
        self._reset_learner_state()

    def _reset_learner_state(self):
        """Give the learner problem state and drop the queued tasks, so an action can be sent again."""
        InstructorTask.objects.filter(course_id=self.course_key).delete()
        StudentModule.objects.update_or_create(
            student=self.learner,
            course_id=self.course_key,
            module_state_key=self.problem.location,
            defaults={"state": json.dumps({"attempts": 1})},
        )

    def _request(self, action, user):
        """Send the request of a Grading tab action as ``user``."""
        self.client.force_authenticate(user=user)
        course_id = str(self.course_key)
        problem = str(self.problem.location)
        single_learner = f"?learner={self.learner.username}"

        def grading_url(name):
            return reverse(f"instructor_api_v2:{name}", kwargs={"course_id": course_id, "problem": problem})

        requests = {
            "view_learner": lambda: self.client.get(reverse(
                "instructor_api_v2:learner_detail",
                kwargs={"course_id": course_id, "email_or_username": self.learner.username},
            )),
            "view_problem": lambda: self.client.get(reverse(
                "instructor_api_v2:problem_detail", kwargs={"course_id": course_id, "location": problem},
            )),
            "view_grading_config": lambda: self.client.get(reverse(
                "instructor_api_v2:grading_config", kwargs={"course_id": course_id},
            )),
            "reset_attempts": lambda: self.client.post(grading_url("reset_attempts") + single_learner),
            "delete_state": lambda: self.client.delete(grading_url("delete_state") + single_learner),
            "rescore": lambda: self.client.post(grading_url("rescore") + single_learner),
            "override_score": lambda: self.client.put(
                grading_url("score_override") + single_learner, {"score": 0.5}, format="json",
            ),
            "reset_attempts_all_learners": lambda: self.client.post(grading_url("reset_attempts")),
            "rescore_all_learners": lambda: self.client.post(grading_url("rescore")),
        }
        return requests[action]()

    def _has_queued_task(self):
        """Check whether any instructor task was submitted for the course."""
        return InstructorTask.objects.filter(course_id=self.course_key).exists()

    @ddt.data(*product((COURSE_ADMIN.external_key, COURSE_STAFF.external_key), GRADING_ACTIONS))
    @ddt.unpack
    def test_authz_course_role_allowed(self, authz_role, action):
        """
        With the flag on, Course Admin and Course Staff can use every Grading tab
        action, including the All Learners actions that legacy course staff cannot run.
        """
        user = self.create_user_with_authz_role(authz_role, self.course_key)

        response = self._request(action, user)

        assert response.status_code == GRADING_ACTIONS[action][1]

    @ddt.data(*GRADING_ACTIONS)
    def test_authz_without_permission_denied(self, action):
        """With the flag on, a user without AuthZ permissions is denied every Grading tab action."""
        user = UserFactory()

        response = self._request(action, user)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not self._has_queued_task()

    @ddt.data(*GRADING_ACTIONS)
    def test_authz_requires_permission(self, action):
        """
        With the flag on, each action requires its own permission: no other
        permission grants it, even to a Course Admin.
        """
        permission = GRADING_ACTIONS[action][0]
        user = self.create_user_with_authz_role(COURSE_ADMIN.external_key, self.course_key)

        # A Course Admin does not hold the legacy compatibility permissions; granting them
        # would send the check to the legacy rule, which a Course Admin passes.
        denied = allowed_all_except(permission, *LEGACY_ROLE_COMPAT_PERMISSIONS)
        with patch(f"{API}.authz_api.is_user_allowed", side_effect=denied):
            response = self._request(action, user)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not self._has_queued_task()

    @ddt.data(*GRADING_ACTIONS)
    def test_authz_permission_alone_grants(self, action):
        """With the flag on, the action's permission grants it to a user without any course role."""
        permission, expected_status = GRADING_ACTIONS[action]
        user = UserFactory()

        with patch(f"{API}.authz_api.is_user_allowed", side_effect=allowed_only(permission)):
            response = self._request(action, user)

        assert response.status_code == expected_status

    @ddt.data(*product(LEGACY_COMPAT_ROLES, GRADING_ACTIONS))
    @ddt.unpack
    def test_authz_legacy_compat_role_keeps_legacy_access(self, roles, action):
        """
        With the flag on, a legacy role that openedx-authz maps without dashboard
        permissions gets the same result as the legacy role with the flag off.
        """
        authz_role, legacy_role = roles
        authz_user = self.create_user_with_authz_role(authz_role, self.course_key)

        authz_status = self._request(action, authz_user).status_code

        self._reset_learner_state()
        self.set_authz_enabled(False)
        legacy_user = UserFactory()
        legacy_role(self.course_key).add_users(legacy_user)

        legacy_status = self._request(action, legacy_user).status_code

        assert authz_status == legacy_status

    @ddt.data(*LEGACY_STAFF_GRADING_ACTIONS)
    def test_legacy_staff_allowed(self, action):
        """With the flag off, legacy course staff can use the reads and the Single Learner actions."""
        self.set_authz_enabled(False)
        staff = StaffFactory(course_key=self.course_key)

        with patch(f"{API}.authz_api.is_user_allowed") as is_user_allowed:
            response = self._request(action, staff)

        assert response.status_code == GRADING_ACTIONS[action][1]
        is_user_allowed.assert_not_called()

    @ddt.data(*ALL_LEARNERS_GRADING_ACTIONS)
    def test_legacy_staff_cannot_run_all_learners_action(self, action):
        """With the flag off, legacy course staff cannot run an All Learners action: it needs instructor access."""
        self.set_authz_enabled(False)
        staff = StaffFactory(course_key=self.course_key)

        with patch(f"{API}.authz_api.is_user_allowed") as is_user_allowed:
            response = self._request(action, staff)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        # Denied by the view's own check, not by the permission class (which answers with ``developer_message``).
        assert "error" in response.data
        assert not self._has_queued_task()
        is_user_allowed.assert_not_called()

    @ddt.data(*GRADING_ACTIONS)
    def test_legacy_without_role_denied(self, action):
        """With the flag off, a user without a course role is denied every Grading tab action."""
        self.set_authz_enabled(False)
        user = UserFactory()

        response = self._request(action, user)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not self._has_queued_task()
