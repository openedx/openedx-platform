"""
Unit tests for Contentstore Proctored Exam Settings.
"""
from unittest.mock import patch

import ddt
from django.conf import settings
from django.test.utils import override_settings
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag
from rest_framework import status
from rest_framework.test import APITestCase

from cms.djangoapps.contentstore.tests.test_utils import AuthorizeStaffTestCase
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from openedx.core import toggles as core_toggles
from openedx.core.djangoapps.course_apps.toggles import EXAMS_IDA
from xmodule.modulestore.django import (
    modulestore,
)  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,
)  # lint-amnesty, pylint: disable=wrong-import-order

from ...mixins import PermissionAccessMixin


class ProctoringExamSettingsTestcase(AuthorizeStaffTestCase):
    """setup for proctored exam settings tests"""

    def get_url(self, course_key):
        return reverse(
            "cms.djangoapps.contentstore:v1:proctored_exam_settings",
            kwargs={"course_id": course_key},
        )

    def test_404_no_course_block(self):
        course_id = "course-v1:edX+ToyX_Nonexistent_Course+Toy_Course"
        self.client.login(username=self.global_staff, password=self.password)
        response = self.make_request(course_id=course_id)
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data == {
            "detail": f"Course with course_id {course_id} does not exist."
        }


class ProctoringExamSettingsGetTests(
    ProctoringExamSettingsTestcase, ModuleStoreTestCase, APITestCase
):
    """Tests for proctored exam settings GETs"""

    @classmethod
    def get_expected_response_data(
        cls, course, user
    ):  # pylint: disable=unused-argument
        return {
            "proctored_exam_settings": {
                "enable_proctored_exams": course.enable_proctored_exams,
                "allow_proctoring_opt_out": course.allow_proctoring_opt_out,
                "proctoring_provider": course.proctoring_provider,
                "proctoring_escalation_email": course.proctoring_escalation_email,
                "create_zendesk_tickets": course.create_zendesk_tickets,
            },
            "course_start_date": "2030-01-01T00:00:00Z",
            "available_proctoring_providers": ["null"],
            "requires_escalation_email_providers": [],
        }

    def make_request(self, course_id=None, data=None):
        course_id = course_id if course_id else self.course.id
        url = self.get_url(course_id)
        return self.client.get(url)

    def test_global_staff(self, expect_status=status.HTTP_200_OK):
        response = super().test_global_staff(expect_status=expect_status)
        assert response.data == self.get_expected_response_data(
            self.course, self.global_staff
        )

    def test_course_instructor(self, expect_status=status.HTTP_200_OK):
        response = super().test_course_instructor(expect_status=expect_status)
        assert response.data == self.get_expected_response_data(
            self.course, self.course_instructor
        )

    @override_waffle_flag(EXAMS_IDA, active=False)
    def test_providers_with_disabled_lti(self):
        self.client.login(
            username=self.course_instructor.username, password=self.password
        )
        response = self.make_request()
        assert response.status_code == status.HTTP_200_OK

        # expected data should not include lti_external value
        expected_data = {
            "proctored_exam_settings": {
                "enable_proctored_exams": self.course.enable_proctored_exams,
                "allow_proctoring_opt_out": self.course.allow_proctoring_opt_out,
                "proctoring_provider": self.course.proctoring_provider,
                "proctoring_escalation_email": self.course.proctoring_escalation_email,
                "create_zendesk_tickets": self.course.create_zendesk_tickets,
            },
            "course_start_date": "2030-01-01T00:00:00Z",
            "available_proctoring_providers": ["null"],
            "requires_escalation_email_providers": [],
        }
        assert response.data == expected_data

    @override_waffle_flag(EXAMS_IDA, active=True)
    def test_providers_with_enabled_lti(self):
        self.client.login(
            username=self.course_instructor.username, password=self.password
        )
        response = self.make_request()
        assert response.status_code == status.HTTP_200_OK

        # expected data should include lti_external value
        expected_data = {
            "proctored_exam_settings": {
                "enable_proctored_exams": self.course.enable_proctored_exams,
                "allow_proctoring_opt_out": self.course.allow_proctoring_opt_out,
                "proctoring_provider": self.course.proctoring_provider,
                "proctoring_escalation_email": self.course.proctoring_escalation_email,
                "create_zendesk_tickets": self.course.create_zendesk_tickets,
            },
            "course_start_date": "2030-01-01T00:00:00Z",
            "available_proctoring_providers": ["lti_external", "null"],
            "requires_escalation_email_providers": ["lti_external"],
        }
        assert response.data == expected_data


@ddt.ddt
class ProctoringExamSettingsPostTests(
    ProctoringExamSettingsTestcase, ModuleStoreTestCase, APITestCase
):
    """Tests for proctored exam settings POST"""

    @classmethod
    def get_request_data(  # pylint: disable=missing-function-docstring
        cls,
        enable_proctored_exams=False,
        allow_proctoring_opt_out=True,
        proctoring_provider="null",
        proctoring_escalation_email="example@edx.org",
        create_zendesk_tickets=True,
    ):
        return {
            "proctored_exam_settings": {
                "enable_proctored_exams": enable_proctored_exams,
                "allow_proctoring_opt_out": allow_proctoring_opt_out,
                "proctoring_provider": proctoring_provider,
                "proctoring_escalation_email": proctoring_escalation_email,
                "create_zendesk_tickets": create_zendesk_tickets,
            }
        }

    def make_request(self, course_id=None, data=None):
        course_id = course_id if course_id else self.course.id
        url = self.get_url(course_id)
        if data is None:
            data = self.get_request_data()
        return self.client.post(url, data, format="json")

    def test_course_instructor(self, expect_status=status.HTTP_403_FORBIDDEN):
        return super().test_course_instructor(expect_status=expect_status)

    @override_settings(
        PROCTORING_BACKENDS={
            "DEFAULT": "test_proctoring_provider",
            "test_proctoring_provider": {"requires_escalation_email": True},
        },
    )
    def test_update_exam_settings_200_escalation_email(self):
        """update exam settings for provider that requires an escalation email"""
        self.client.login(username=self.global_staff.username, password=self.password)
        data = self.get_request_data(
            enable_proctored_exams=True,
            proctoring_provider="test_proctoring_provider",
            proctoring_escalation_email="foo@bar.com",
        )
        response = self.make_request(data=data)

        # response is correct
        assert response.status_code == status.HTTP_200_OK
        self.assertDictEqual(
            response.data,
            {
                "proctored_exam_settings": {
                    "enable_proctored_exams": True,
                    "allow_proctoring_opt_out": True,
                    "proctoring_provider": "test_proctoring_provider",
                    "proctoring_escalation_email": "foo@bar.com",
                    "create_zendesk_tickets": True,
                }
            },
        )

        # course settings have been updated
        updated = modulestore().get_item(self.course.location)
        assert updated.enable_proctored_exams is True
        assert updated.proctoring_provider == "test_proctoring_provider"
        assert updated.proctoring_escalation_email == "foo@bar.com"

    @override_settings(
        PROCTORING_BACKENDS={
            "DEFAULT": "test_proctoring_provider",
            "test_proctoring_provider": {},
        },
    )
    def test_update_exam_settings_200_no_escalation_email(self):
        """escalation email may be blank if not required by the provider"""
        self.client.login(username=self.global_staff.username, password=self.password)
        data = self.get_request_data(
            enable_proctored_exams=True,
            proctoring_provider="test_proctoring_provider",
            proctoring_escalation_email=None,
        )
        response = self.make_request(data=data)

        # response is correct
        assert response.status_code == status.HTTP_200_OK
        self.assertDictEqual(
            response.data,
            {
                "proctored_exam_settings": {
                    "enable_proctored_exams": True,
                    "allow_proctoring_opt_out": True,
                    "proctoring_provider": "test_proctoring_provider",
                    "proctoring_escalation_email": None,
                    "create_zendesk_tickets": True,
                }
            },
        )

        # course settings have been updated
        updated = modulestore().get_item(self.course.location)
        assert updated.enable_proctored_exams is True
        assert updated.proctoring_provider == "test_proctoring_provider"
        assert updated.proctoring_escalation_email is None

    def test_update_exam_settings_excluded_field(self):
        """
        Excluded settings in POST data should not be updated
        """
        self.client.login(username=self.global_staff.username, password=self.password)
        data = self.get_request_data(
            proctoring_escalation_email="foo@bar.com",
        )
        response = self.make_request(data=data)

        # response is correct
        assert response.status_code == status.HTTP_200_OK
        self.assertDictEqual(
            response.data,
            {
                "proctored_exam_settings": {
                    "enable_proctored_exams": False,
                    "allow_proctoring_opt_out": True,
                    "proctoring_provider": "null",
                    "proctoring_escalation_email": None,
                    "create_zendesk_tickets": True,
                }
            },
        )

        # excluded course settings are not updated
        updated = modulestore().get_item(self.course.location)
        assert updated.proctoring_escalation_email is None

    @override_settings(
        PROCTORING_BACKENDS={"DEFAULT": "null", "test_proctoring_provider": {}},
    )
    def test_update_exam_settings_invalid_value(self):
        self.client.login(username=self.global_staff.username, password=self.password)
        PROCTORED_EXAMS_ENABLED_FEATURES = settings.FEATURES
        PROCTORED_EXAMS_ENABLED_FEATURES["ENABLE_PROCTORED_EXAMS"] = True
        with override_settings(FEATURES=PROCTORED_EXAMS_ENABLED_FEATURES):
            data = self.get_request_data(
                enable_proctored_exams=True,
                proctoring_provider="notvalidprovider",
            )
            response = self.make_request(data=data)

        # response is correct
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        self.assertIn(
            {
                "proctoring_provider": (
                    "The selected proctoring provider, notvalidprovider, is not a valid provider. "
                    "Please select from one of ['test_proctoring_provider']."
                )
            },
            response.data['detail'],
        )

        # course settings have been updated
        updated = modulestore().get_item(self.course.location)
        assert updated.enable_proctored_exams is False
        assert updated.proctoring_provider == "null"

    def test_403_if_instructor_request_includes_opting_out(self):
        self.client.login(username=self.course_instructor, password=self.password)
        data = self.get_request_data()
        response = self.make_request(data=data)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @override_settings(
        PROCTORING_BACKENDS={
            "DEFAULT": "test_proctoring_provider",
            "test_proctoring_provider": {"requires_escalation_email": True},
        },
    )
    def test_200_for_instructor_request_compatibility(self):
        self.client.login(username=self.course_instructor, password=self.password)
        data = {
            "proctored_exam_settings": {
                "enable_proctored_exams": True,
                "proctoring_provider": "test_proctoring_provider",
                "proctoring_escalation_email": "foo@bar.com",
            }
        }
        response = self.make_request(data=data)
        assert response.status_code == status.HTTP_200_OK

    @override_settings(
        PROCTORING_BACKENDS={
            "DEFAULT": "software_secure",
            "software_secure": {},
        },
    )
    @patch("logging.Logger.info")
    @ddt.data(
        ("software_secure", True, False),
        ("software_secure", False, True),
    )
    @ddt.unpack
    def test_nonadmin_with_zendesk_ticket(
        self, proctoring_provider, create_zendesk_tickets, expect_log, logger_mock
    ):
        self.client.login(username=self.course_instructor, password=self.password)
        data = {
            "proctored_exam_settings": {
                "enable_proctored_exams": True,
                "proctoring_provider": proctoring_provider,
                "proctoring_escalation_email": "foo@bar.com",
                "create_zendesk_tickets": create_zendesk_tickets,
            }
        }
        response = self.make_request(data=data)
        assert response.status_code == status.HTTP_200_OK
        if expect_log:
            logger_string = (
                "create_zendesk_tickets set to {ticket_value} but proctoring "
                "provider is {provider} for course {course_id}. create_zendesk_tickets "
                "should be updated for this course.".format(
                    ticket_value=create_zendesk_tickets,
                    provider=proctoring_provider,
                    course_id=self.course.id,
                )
            )
            logger_mock.assert_any_call(logger_string)

        updated = modulestore().get_item(self.course.location)
        assert updated.create_zendesk_tickets is create_zendesk_tickets

    @override_waffle_flag(EXAMS_IDA, active=True)
    def test_200_for_lti_provider(self):
        self.client.login(username=self.global_staff.username, password=self.password)
        PROCTORED_EXAMS_ENABLED_FEATURES = settings.FEATURES
        PROCTORED_EXAMS_ENABLED_FEATURES["ENABLE_PROCTORED_EXAMS"] = True
        with override_settings(FEATURES=PROCTORED_EXAMS_ENABLED_FEATURES):
            data = self.get_request_data(
                enable_proctored_exams=True,
                proctoring_provider="lti_external",
            )
            response = self.make_request(data=data)

        # response is correct
        assert response.status_code == status.HTTP_200_OK

        self.assertDictEqual(
            response.data,
            {
                "proctored_exam_settings": {
                    "enable_proctored_exams": True,
                    "allow_proctoring_opt_out": True,
                    "proctoring_provider": "lti_external",
                    "proctoring_escalation_email": None,
                    "create_zendesk_tickets": True,
                }
            },
        )

        # course settings have been updated
        updated = modulestore().get_item(self.course.location)
        assert updated.enable_proctored_exams is True
        assert updated.proctoring_provider == "lti_external"

    @override_waffle_flag(EXAMS_IDA, active=False)
    def test_400_for_disabled_lti(self):
        self.client.login(username=self.global_staff.username, password=self.password)
        PROCTORED_EXAMS_ENABLED_FEATURES = settings.FEATURES
        PROCTORED_EXAMS_ENABLED_FEATURES["ENABLE_PROCTORED_EXAMS"] = True
        with override_settings(FEATURES=PROCTORED_EXAMS_ENABLED_FEATURES):
            data = self.get_request_data(
                enable_proctored_exams=True,
                proctoring_provider="lti_external",
            )
            response = self.make_request(data=data)

        # response is correct
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        self.assertIn(
            {
                "proctoring_provider": (
                    "The selected proctoring provider, lti_external, is not a valid provider. "
                    "Please select from one of ['null']."
                )
            },
            response.data['detail'],
        )

        # course settings have been updated
        updated = modulestore().get_item(self.course.location)
        assert updated.enable_proctored_exams is False
        assert updated.proctoring_provider == "null"


@ddt.ddt
class CourseProctoringErrorsViewTest(CourseTestCase, PermissionAccessMixin):
    """
    Tests for ProctoringErrorsView.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse(
            "cms.djangoapps.contentstore:v1:proctoring_errors",
            kwargs={"course_id": self.course.id},
        )
        self.non_staff_client, _ = self.create_non_staff_authed_user_client()

    @ddt.data(False, True)
    def test_disable_advanced_settings_feature(self, disable_advanced_settings):
        """
        If this feature is enabled, only Django Staff/Superuser should be able to see the proctoring errors.
        For non-staff users the proctoring errors should be unavailable.
        """
        with override_settings(
            FEATURES={"DISABLE_ADVANCED_SETTINGS": disable_advanced_settings}
        ):
            response = self.non_staff_client.get(self.url)
            self.assertEqual(
                response.status_code, 403 if disable_advanced_settings else 200
            )

    @patch.object(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, 'is_enabled', return_value=True)
    @patch('common.djangoapps.student.auth.authz_api.is_user_allowed')
    def test_authz_user_allowed(self, mock_is_user_allowed, mock_flag):
        """User with authz permission can access proctoring errors."""
        mock_is_user_allowed.return_value = True
        response = self.non_staff_client.get(self.url)
        self.assertEqual(response.status_code, 200)
        mock_is_user_allowed.assert_called_once()

    @patch.object(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, 'is_enabled', return_value=True)
    @patch('common.djangoapps.student.auth.authz_api.is_user_allowed')
    def test_authz_user_not_allowed(self, mock_is_user_allowed, mock_flag):
        """User without authz permission cannot access proctoring errors."""
        mock_is_user_allowed.return_value = False
        response = self.non_staff_client.get(self.url)
        self.assertEqual(response.status_code, 403)
        mock_is_user_allowed.assert_called_once()

    @patch.object(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, 'is_enabled', return_value=True)
    @patch('common.djangoapps.student.auth.authz_api.is_user_allowed')
    def test_authz_with_disable_advanced_settings_staff_allowed(self, mock_is_user_allowed, mock_flag):
        """Staff user can access when DISABLE_ADVANCED_SETTINGS is enabled, bypassing authz."""
        with override_settings(FEATURES={"DISABLE_ADVANCED_SETTINGS": True}):
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200)
            mock_is_user_allowed.assert_not_called()

    @patch.object(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, 'is_enabled', return_value=True)
    @patch('common.djangoapps.student.auth.authz_api.is_user_allowed')
    def test_authz_with_disable_advanced_settings_non_staff_denied(self, mock_is_user_allowed, mock_flag):
        """Non-staff user is denied when DISABLE_ADVANCED_SETTINGS is enabled, bypassing authz."""
        with override_settings(FEATURES={"DISABLE_ADVANCED_SETTINGS": True}):
            response = self.non_staff_client.get(self.url)
            self.assertEqual(response.status_code, 403)
            mock_is_user_allowed.assert_not_called()
