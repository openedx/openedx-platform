"""
Unit tests for course details views.
"""
import json
from unittest.mock import patch

import ddt
from django.urls import reverse
from openedx_authz.constants.roles import COURSE_EDITOR, COURSE_STAFF
from rest_framework import status
from rest_framework.test import APIClient

from cms.djangoapps.contentstore.rest_api.v1.views.course_details import _classify_update
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin

from ...mixins import PermissionAccessMixin


@ddt.ddt
class CourseDetailsViewTest(CourseTestCase, PermissionAccessMixin):
    """
    Tests for CourseDetailsView.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse(
            "cms.djangoapps.contentstore:v1:course_details",
            kwargs={"course_id": self.course.id},
        )

    def test_put_permissions_unauthenticated(self):
        """
        Test that an error is returned in the absence of auth credentials.
        """
        self.client.logout()
        response = self.client.put(self.url)
        error = self.get_and_check_developer_response(response)
        self.assertEqual(error, "Authentication credentials were not provided.")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_put_permissions_unauthorized(self):
        """
        Test that an error is returned if the user is unauthorised.
        """
        client, _ = self.create_non_staff_authed_user_client()
        pre_requisite_course_keys = [str(self.course.id), "invalid_key"]
        request_data = {"pre_requisite_courses": pre_requisite_course_keys}
        response = client.put(
            path=self.url,
            data=json.dumps(request_data),
            content_type="application/json",
        )
        error = self.get_and_check_developer_response(response)
        self.assertEqual(error, "You do not have permission to perform this action.")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_PREREQUISITE_COURSES": True})
    def test_put_invalid_pre_requisite_course(self):
        pre_requisite_course_keys = [str(self.course.id), "invalid_key"]
        request_data = {"pre_requisite_courses": pre_requisite_course_keys}
        response = self.client.put(
            path=self.url,
            data=json.dumps(request_data),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["error"], "Invalid prerequisite course key")

    def test_put_course_details(self):
        request_data = {
            "about_sidebar_html": "",
            "banner_image_name": "images_course_image.jpg",
            "banner_image_asset_path": "/asset-v1:edX+E2E-101+course+type@asset+block@images_course_image.jpg",
            "certificate_available_date": "2029-01-02T00:00:00Z",
            "certificates_display_behavior": "end",
            "course_id": "E2E-101",
            "course_image_asset_path": "/static/studio/images/pencils.jpg",
            "course_image_name": "bar_course_image_name",
            "description": "foo_description",
            "duration": "",
            "effort": None,
            "end_date": "2023-08-01T01:30:00Z",
            "enrollment_end": "2023-05-30T01:00:00Z",
            "enrollment_start": "2023-05-29T01:00:00Z",
            "entrance_exam_enabled": "",
            "entrance_exam_id": "",
            "entrance_exam_minimum_score_pct": "50",
            "intro_video": None,
            "language": "creative-commons: ver=4.0 BY NC ND",
            "learning_info": ["foo", "bar"],
            "license": "creative-commons: ver=4.0 BY NC ND",
            "org": "edX",
            "overview": '<section class="about"></section>',
            "pre_requisite_courses": [],
            "run": "course",
            "self_paced": None,
            "short_description": "",
            "start_date": "2023-06-01T01:30:00Z",
            "subtitle": "",
            "syllabus": None,
            "title": "",
            "video_thumbnail_image_asset_path": "/asset-v1:edX+E2E-101+course+type@asset+block@images_course_image.jpg",
            "video_thumbnail_image_name": "images_course_image.jpg",
            "instructor_info": {
                "instructors": [
                    {
                        "name": "foo bar",
                        "title": "title",
                        "organization": "org",
                        "image": "image",
                        "bio": "",
                    }
                ]
            },
        }
        response = self.client.put(
            path=self.url,
            data=json.dumps(request_data),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


@ddt.ddt
class CourseDetailsAuthzViewTest(CourseAuthoringAuthzTestMixin, CourseTestCase):
    """
    Tests for CourseDetailsView using AuthZ permissions.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse(
            "cms.djangoapps.contentstore:v1:course_details",
            kwargs={"course_id": self.course.id},
        )
        self.request_data = {
            "about_sidebar_html": "",
            "banner_image_name": "images_course_image.jpg",
            "banner_image_asset_path": "/asset-v1:edX+E2E-101+course+type@asset+block@images_course_image.jpg",
            "certificate_available_date": "2029-01-02T00:00:00Z",
            "certificates_display_behavior": "end",
            "course_id": "E2E-101",
            "course_image_asset_path": "/static/studio/images/pencils.jpg",
            "course_image_name": "bar_course_image_name",
            "description": "foo_description",
            "duration": "",
            "effort": None,
            "end_date": "2023-08-01T01:30:00Z",
            "enrollment_end": "2023-05-30T01:00:00Z",
            "enrollment_start": "2023-05-29T01:00:00Z",
            "entrance_exam_enabled": "",
            "entrance_exam_id": "",
            "entrance_exam_minimum_score_pct": "50",
            "intro_video": None,
            "language": "creative-commons: ver=4.0 BY NC ND",
            "learning_info": ["foo", "bar"],
            "license": "creative-commons: ver=4.0 BY NC ND",
            "org": "edX",
            "overview": '<section class="about"></section>',
            "pre_requisite_courses": [],
            "run": "course",
            "self_paced": None,
            "short_description": "",
            "start_date": "2023-06-01T01:30:00Z",
            "subtitle": "",
            "syllabus": None,
            "title": "",
            "video_thumbnail_image_asset_path": "/asset-v1:edX+E2E-101+course+type@asset+block@images_course_image.jpg",
            "video_thumbnail_image_name": "images_course_image.jpg",
            "instructor_info": {
                "instructors": [
                    {
                        "name": "foo bar",
                        "title": "title",
                        "organization": "org",
                        "image": "image",
                        "bio": "",
                    }
                ]
            },
        }

    def test_put_permissions_unauthenticated(self):
        """
        Test that an error is returned in the absence of auth credentials.
        """
        client = APIClient()  # no auth
        response = client.put(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_put_permissions_unauthorized(self):
        """
        Test that an error is returned if the user is unauthorised.
        """
        response = self.unauthorized_client.put(
            path=self.url,
            data=json.dumps(self.request_data),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_get_course_details_authorized(self):
        """
        Authorized user with COURSE_EDITOR role can access course details.
        """
        self.add_user_to_role_in_course(
            self.authorized_user,
            COURSE_EDITOR.external_key,
            self.course.id
        )

        response = self.authorized_client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_get_course_details_unauthorized(self):
        """
        Unauthorized user should receive 403.
        """
        response = self.unauthorized_client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_get_course_details_staff_user(self):
        """
        Django staff user should bypass AuthZ and access course details.
        """
        response = self.staff_client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_get_course_details_super_user(self):
        """
        Superuser should bypass AuthZ and access course details.
        """
        response = self.super_client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @ddt.data(
        # No changes
        ({}, (False, False)),
        (
            {"certificates_display_behavior": "end"},  # same value as existing course detail
            (False, False),
        ),

        # Schedule-only fields
        ({"start_date": "2023-01-01"}, (True, False)),
        ({"end_date": "2023-02-01"}, (True, False)),
        ({"enrollment_start": "2023-01-01"}, (True, False)),
        ({"enrollment_end": "2023-01-10"}, (True, False)),

        # Details-only fields
        ({"title": "New Title"}, (False, True)),
        ({"description": "New description"}, (False, True)),
        ({"short_description": "Short"}, (False, True)),
        ({"overview": "<p>HTML</p>"}, (False, True)),

        # Mixed fields
        (
            {"title": "New Title", "start_date": "2023-01-01"},
            (True, True)
        ),

        # Non-updatable / irrelevant fields
        ({"random_field": "value"}, (False, False)),
    )
    @ddt.unpack
    def test_classify_update(self, payload, expected):
        result = _classify_update(payload, self.course.id)
        self.assertEqual(result, expected)

    def test_classyfy_update_with_get_request(self):
        """
        GET request with no changes should not be classified as schedule or details update.
        """
        # Get the current status of the course details to use
        # as the basis for the update request
        self.add_user_to_role_in_course(
            self.authorized_user,
            COURSE_EDITOR.external_key,
            self.course.id
        )
        response = self.authorized_client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        current_course_details = response.json()
        # This field is flagged as a details update because of a type mismatch:
        # the GET response returns an invalid string, while the stored value has a different type.
        # As a result, the equality check fails even though the values are logically the same.
        current_course_details["certificates_display_behavior"] = "end"

        expected = (False, False)
        result = _classify_update(current_course_details, self.course.id)
        self.assertEqual(result, expected)

    def test_course_editor_can_edit_course_details(self):
        """
        User with COURSE_EDITOR role can update course details.
        COURSE_EDITOR does not have permission to edit schedule fields.
        """

        self.add_user_to_role_in_course(
            self.authorized_user,
            COURSE_EDITOR.external_key,
            self.course.id
        )

        # Get the current status of the course details to use
        # as the basis for the update request
        response = self.authorized_client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        current_course_details = response.json()
        # This field is flagged as a details update because of a type mismatch:
        # the GET response returns an invalid string, while the stored value has a different type.
        # As a result, the equality check fails even though the values are logically the same.
        current_course_details["certificates_display_behavior"] = "end"

        # Update the course details with new values,
        # keeping schedule fields the same to ensure we are only
        # testing edit details permission
        current_course_details["title"] = "Updated Title"

        response = self.authorized_client.put(
            path=self.url,
            data=json.dumps(current_course_details),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_course_staff_can_edit_course_schedule(self):
        """
        User with COURSE_STAFF role can update course schedule.
        Only COURSE_STAFF and COURSE_ADMIN can edit schedule related fields.
        """
        self.add_user_to_role_in_course(
            self.authorized_user,
            COURSE_STAFF.external_key,
            self.course.id
        )

        # Get the current status of the course details to use
        # as the basis for the update request
        response = self.authorized_client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        current_course_details = response.json()
        # This field is flagged as a details update because of a type mismatch:
        # the GET response returns an invalid string, while the stored value has a different type.
        # As a result, the equality check fails even though the values are logically the same.
        current_course_details["certificates_display_behavior"] = "end"

        # Update the course details with new values,
        # changing schedule fields to ensure we are only
        # testing edit schedule permission
        current_course_details["end_date"] = "2023-08-01T01:30:00Z"

        response = self.authorized_client.put(
            path=self.url,
            data=json.dumps(current_course_details),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_course_editor_cannot_edit_course_schedule(self):
        """
        User with COURSE_EDITOR role cannot update course schedule.
        Only COURSE_STAFF and COURSE_ADMIN can edit schedule-related fields.
        """
        self.add_user_to_role_in_course(
            self.authorized_user,
            COURSE_EDITOR.external_key,
            self.course.id
        )

        # Get the current status of the course details to use
        # as the basis for the update request
        response = self.authorized_client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        current_course_details = response.json()
        # This field is flagged as a details update because of a type mismatch:
        # the GET response returns an invalid string, while the stored value has a different type.
        # As a result, the equality check fails even though the values are logically the same.
        current_course_details["certificates_display_behavior"] = "end"

        # Update the course details with new values,
        # changing schedule fields to ensure we are only
        # testing edit schedule permission
        current_course_details["end_date"] = "2023-08-01T01:30:00Z"

        response = self.authorized_client.put(
            path=self.url,
            data=json.dumps(current_course_details),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_course_staff_can_edit_course_schedule_and_details(self):
        """
        User with COURSE_STAFF role can update course
        schedule and details.
        """
        self.add_user_to_role_in_course(
            self.authorized_user,
            COURSE_STAFF.external_key,
            self.course.id
        )

        # Get the current status of the course details to use
        # as the basis for the update request
        response = self.authorized_client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        current_course_details = response.json()
        # This field is flagged as a details update because of a type mismatch:
        # the GET response returns an invalid string, while the stored value has a different type.
        # As a result, the equality check fails even though the values are logically the same.
        current_course_details["certificates_display_behavior"] = "end"

        # Update the course details with new values,
        # changing schedule and details fields to ensure user
        # has permission to edit both
        current_course_details["end_date"] = "2023-08-01T01:30:00Z"
        current_course_details["title"] = "Updated Title"

        response = self.authorized_client.put(
            path=self.url,
            data=json.dumps(current_course_details),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_course_editor_cannot_edit_course_schedule_and_details(self):
        """
        User with COURSE_EDITOR role cannot update course
        schedule or course details.
        """
        self.add_user_to_role_in_course(
            self.authorized_user,
            COURSE_EDITOR.external_key,
            self.course.id
        )

        # Get the current status of the course details to use
        # as the basis for the update request
        response = self.authorized_client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        current_course_details = response.json()
        # This field is flagged as a details update because of a type mismatch:
        # the GET response returns an invalid string, while the stored value has a different type.
        # As a result, the equality check fails even though the values are logically the same.
        current_course_details["certificates_display_behavior"] = "end"

        # Update the course details with new values,
        # changing schedule and details fields to ensure user
        # has permission to edit both
        current_course_details["end_date"] = "2023-08-01T01:30:00Z"
        current_course_details["title"] = "Updated Title"

        response = self.authorized_client.put(
            path=self.url,
            data=json.dumps(current_course_details),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthorized_user_cannot_edit_with_any_change_on_the_payload(self):
        """
        An unauthorized user should receive 403 even if the payload contains
        no changes that do not require edit permissions.
        """
        self.add_user_to_role_in_course(
            self.authorized_user,
            COURSE_EDITOR.external_key,
            self.course.id
        )

        # Get the current status of the course details to use
        # as the basis for the update request
        response = self.authorized_client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        current_course_details = response.json()
        # This field is flagged as a details update because of a type mismatch:
        # the GET response returns an invalid string, while the stored value has a different type.
        # As a result, the equality check fails even though the values are logically the same.
        current_course_details["certificates_display_behavior"] = "end"

        # Update the course details with the same values.
        response = self.unauthorized_client.put(
            path=self.url,
            data=json.dumps(current_course_details),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_put_user_without_role_then_added_can_update(self):
        """
        Validate dynamic role assignment works for PUT.
        """
        # Initially unauthorized
        response = self.unauthorized_client.put(
            path=self.url,
            data=json.dumps(self.request_data),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Assign role dynamically
        self.add_user_to_role_in_course(
            self.unauthorized_user,
            COURSE_STAFF.external_key,
            self.course.id
        )

        response = self.unauthorized_client.put(
            path=self.url,
            data=json.dumps(self.request_data),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_PREREQUISITE_COURSES": True})
    def test_put_invalid_pre_requisite_course_with_authz(self):
        """
        Ensure validation still applies under AuthZ.
        """
        self.add_user_to_role_in_course(
            self.authorized_user,
            COURSE_EDITOR.external_key,
            self.course.id
        )

        pre_requisite_course_keys = [str(self.course.id), "invalid_key"]
        request_data = {"pre_requisite_courses": pre_requisite_course_keys}

        response = self.authorized_client.put(
            path=self.url,
            data=json.dumps(request_data),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["error"], "Invalid prerequisite course key")

    def test_staff_user_can_update_without_authz_role(self):
        """
        Django staff user should bypass AuthZ.
        """
        response = self.staff_client.put(
            path=self.url,
            data=json.dumps(self.request_data),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_superuser_can_update_without_authz_role(self):
        """
        Superuser should bypass AuthZ.
        """
        response = self.super_client.put(
            path=self.url,
            data=json.dumps(self.request_data),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
