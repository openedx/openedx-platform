"""
Tests for the course advanced settings API.
"""
import json
import pkg_resources
from unittest.mock import patch

import casbin
import ddt
from django.test import override_settings
from django.urls import reverse
from milestones.tests.utils import MilestonesTestCaseMixin
from rest_framework.test import APIClient

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core import toggles as core_toggles
from openedx_authz.api.users import assign_role_to_user_in_scope
from openedx_authz.constants.roles import COURSE_STAFF
from openedx_authz.engine.enforcer import AuthzEnforcer
from openedx_authz.engine.utils import migrate_policy_between_enforcers


@ddt.ddt
class CourseAdvanceSettingViewTest(CourseTestCase, MilestonesTestCaseMixin):
    """
    Tests for AdvanceSettings API View.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse(
            "cms.djangoapps.contentstore:v0:course_advanced_settings",
            kwargs={"course_id": self.course.id},
        )

    def get_and_check_developer_response(self, response):
        """
        Make basic asserting about the presence of an error response, and return the developer response.
        """
        content = json.loads(response.content.decode("utf-8"))
        assert "developer_message" in content
        return content["developer_message"]

    def test_permissions_unauthenticated(self):
        """
        Test that an error is returned in the absence of auth credentials.
        """
        self.client.logout()
        response = self.client.get(self.url)
        error = self.get_and_check_developer_response(response)
        assert error == "Authentication credentials were not provided."

    def test_permissions_unauthorized(self):
        """
        Test that an error is returned if the user is unauthorised.
        """
        client, _ = self.create_non_staff_authed_user_client()
        response = client.get(self.url)
        error = self.get_and_check_developer_response(response)
        assert error == "You do not have permission to perform this action."

    @ddt.data(
        ("ENABLE_EDXNOTES", "edxnotes"),
        ("ENABLE_OTHER_COURSE_SETTINGS", "other_course_settings"),
    )
    @ddt.unpack
    def test_conditionally_excluded_fields_present(self, setting, excluded_field):
        """
        Test that the response contain all fields irrespective of exclusions.
        """
        for setting_value in (True, False):
            with override_settings(FEATURES={setting: setting_value}):
                response = self.client.get(self.url)
                content = json.loads(response.content.decode("utf-8"))
                assert excluded_field in content

    @ddt.data(
        ("", ("display_name", "due"), ()),
        ("display_name", ("display_name",), ("due", "edxnotes")),
        ("display_name,edxnotes", ("display_name", "edxnotes"), ("due", "tags")),
    )
    @ddt.unpack
    def test_filtered_fields(self, filtered_fields, present_fields, absent_fields):
        """
        Test that the response contain all fields that are in the filter, and none that are filtered out.
        """
        response = self.client.get(self.url, {"filter_fields": filtered_fields})
        content = json.loads(response.content.decode("utf-8"))
        for field in present_fields:
            assert field in content.keys()
        for field in absent_fields:
            assert field not in content.keys()

    @ddt.data(
        ("ENABLE_EDXNOTES", "edxnotes"),
        ("ENABLE_OTHER_COURSE_SETTINGS", "other_course_settings"),
    )
    @ddt.unpack
    def test_disabled_fetch_all_query_param(self, setting, excluded_field):
        with override_settings(FEATURES={setting: False}):
            resp = self.client.get(self.url, {"fetch_all": 0})
            assert excluded_field not in resp.data


@patch.object(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, 'is_enabled', return_value=True)
class AdvancedSettingsAuthzTest(CourseTestCase):
    """
    Tests for AdvancedCourseSettingsView authorization with openedx-authz.

    These tests enable the AUTHZ_COURSE_AUTHORING_FLAG by default.
    """

    def setUp(self):
        super().setUp()
        self._seed_database_with_policies()
        self.url = reverse(
            "cms.djangoapps.contentstore:v0:course_advanced_settings",
            kwargs={"course_id": self.course.id},
        )

        # Create test users
        self.authorized_user = UserFactory()
        self.unauthorized_user = UserFactory()

        # Assign role to authorized user
        assign_role_to_user_in_scope(
            self.authorized_user.username,
            COURSE_STAFF.external_key,
            str(self.course.id)
        )
        AuthzEnforcer.get_enforcer().load_policy()

        # Create API clients and force_authenticate
        self.authorized_client = APIClient()
        self.authorized_client.force_authenticate(user=self.authorized_user)
        self.unauthorized_client = APIClient()
        self.unauthorized_client.force_authenticate(user=self.unauthorized_user)

    def tearDown(self):
        super().tearDown()
        AuthzEnforcer.get_enforcer().clear_policy()

    @classmethod
    def _seed_database_with_policies(cls):
        """Seed the database with policies from the policy file."""
        global_enforcer = AuthzEnforcer.get_enforcer()
        global_enforcer.load_policy()
        model_path = pkg_resources.resource_filename("openedx_authz.engine", "config/model.conf")
        policy_path = pkg_resources.resource_filename("openedx_authz.engine", "config/authz.policy")
        migrate_policy_between_enforcers(
            source_enforcer=casbin.Enforcer(model_path, policy_path),
            target_enforcer=global_enforcer,
        )

    def test_authorized_for_specific_course(self, mock_flag):
        """User authorized for specific course can access."""
        response = self.authorized_client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_unauthorized_for_specific_course(self, mock_flag):
        """User without authorization for specific course cannot access."""
        response = self.unauthorized_client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_unauthorized_for_different_course(self, mock_flag):
        """User authorized for one course cannot access another course."""
        other_course = self.store.create_course("OtherOrg", "OtherCourse", "Run", self.user.id)
        other_url = reverse(
            "cms.djangoapps.contentstore:v0:course_advanced_settings",
            kwargs={"course_id": other_course.id},
        )
        response = self.authorized_client.get(other_url)
        self.assertEqual(response.status_code, 403)

    def test_staff_authorized_by_default(self, mock_flag):
        """Staff users are authorized by default."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_superuser_authorized_by_default(self, mock_flag):
        """Superusers are authorized by default."""
        superuser = UserFactory(is_superuser=True, is_staff=False)
        superuser_client = APIClient()
        superuser_client.force_authenticate(user=superuser)
        response = superuser_client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_patch_authorized_for_specific_course(self, mock_flag):
        """User authorized for specific course can PATCH."""
        response = self.authorized_client.patch(
            self.url,
            {"display_name": {"value": "Test"}},
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)

    def test_patch_unauthorized_for_specific_course(self, mock_flag):
        """User without authorization for specific course cannot PATCH."""
        response = self.unauthorized_client.patch(
            self.url,
            {"display_name": {"value": "Test"}},
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 403)
