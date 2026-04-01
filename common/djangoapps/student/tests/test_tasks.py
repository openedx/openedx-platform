"""
Celery task tests
"""
from unittest.mock import patch, Mock, PropertyMock

import pytest
from django.conf import settings
from django.test.utils import override_settings

from common.djangoapps.student.tasks import (
    MAX_RETRIES,
    send_course_enrollment_email
)
from common.djangoapps.student.tests.factories import UserFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

TASK_LOGGER = 'common.djangoapps.student.tasks.log'
BRAZE_COURSE_ENROLLMENT_CANVAS_ID = "braze-canvas-id"


@override_settings(
    BRAZE_COURSE_ENROLLMENT_CANVAS_ID=BRAZE_COURSE_ENROLLMENT_CANVAS_ID,
    LEARNING_MICROFRONTEND_URL="https://learningmfe.openedx.org",
)
class TestCourseEnrollmentEmailTask(ModuleStoreTestCase):
    """
    Tests for send_course_enrollment_email task.
    """

    def setUp(self):
        """
        Set up tests
        """
        super().setUp()
        self.user = UserFactory.create(
            username="joe", email="joe@joe.com", password="password"
        )
        self.course = CourseFactory.create()
        self.course_uuid = "d08af18e-7fd5-45eb-a834-a9decc6d9afa"
        self.send_course_enrollment_email_kwargs = {
            "user_id": self.user.id,
            "course_id": str(self.course.id),
            "course_title": "Test course",
            "short_description": "Short description of course",
            "course_ended": False,
            "pacing_type": "self-paced",
            "track_mode": "audit",
        }

    @staticmethod
    def _get_course_run():
        """
        Helper method for course run details.
        """
        return {
            "title": "Test Course",
            "short_description": "An introduction to computer science.",
            "weeks_to_complete": 8,
            "min_effort": 5,
            "max_effort": 10,
            "pacing_type": "self-paced",
            "image": {
                "src": "https://prod/media/course/image/a3d1899c3344.png",
            },
            "staff": [
                {
                    "given_name": "Mario",
                    "family_name": "Ricci",
                    "slug": "mario-ricci",
                    "position": {
                        "organization_name": "University of Adelaide",
                    },
                    "profile_image_url": "https://prod.org/media/people/profile_images/0ad.jpg",
                },
            ],
            "learners_count": "12345",
        }

    @staticmethod
    def _get_course_owners():
        """
        Helper method for course owner details.
        """
        return [
            {
                "logo_image_url": "https://prod/organization/logos/2cc39992c67a.png",
                "name": "edX University",
            }
        ]

    @staticmethod
    def _get_course_dates():
        """
        Helper method for course dates.
        """
        return [
            {
                "due_date": "Thu, Jul 28, 2022",
                "title": "Course starts",
                "assignment_type": "",
                "link": "",
                "assignment_count": 0,
                "due_time": "",
            },
            {
                "due_date": "Thu, Aug 25, 2022",
                "title": "",
                "assignment_type": "",
                "link": "",
                "assignment_count": 0,
                "due_time": "",
            },
            {
                "due_date": "Mon, Aug 29, 2022",
                "title": "Importance of an Operations Mindset",
                "assignment_type": "Ops Challenge",
                "link": "https://courses.edx.org/courses/course-v1:BabsonX+EPS03x+3T2018",
                "assignment_count": 5,
                "due_time": "2:25 AM GMT+5",
            },
        ]

    def _get_canvas_properties(
        self, add_course_run_details=True, add_course_dates=True
    ):
        """
        Helper method that returns canvas entry properties.
        """
        canvas_properties = {
            "course_run_key": str(self.course.id),
            "learning_base_url": "https://learningmfe.openedx.org",
            "lms_base_url": settings.LMS_ROOT_URL,
            "course_price": 0,
            "goals_enabled": False,
            "course_date_blocks": [],
            "course_title": self.send_course_enrollment_email_kwargs["course_title"],
            "short_description": self.send_course_enrollment_email_kwargs["short_description"],
            "pacing_type": self.send_course_enrollment_email_kwargs["pacing_type"],
            "track_mode": self.send_course_enrollment_email_kwargs["track_mode"],
        }

        if add_course_dates:
            canvas_properties.update({"course_date_blocks": self._get_course_dates()})

        if add_course_run_details:
            course_run = self._get_course_run()
            canvas_properties.update(
                {
                    "instructors": [
                        {
                            "name": "Mario Ricci",
                            "profile_image_url": "https://prod.org/media/people/profile_images/0ad.jpg",
                            "organization_name": "University of Adelaide",
                            "bio_url": "None/bio/mario-ricci",
                        }
                    ],
                    "instructors_count": "odd",
                    "min_effort": course_run["min_effort"],
                    "max_effort": course_run["max_effort"],
                    "weeks_to_complete": course_run["weeks_to_complete"],
                    "learners_count": "",
                    "banner_image_url": course_run["image"]["src"],
                    "course_title": course_run["title"],
                    "short_description": course_run["short_description"],
                    "pacing_type": course_run["pacing_type"],
                    "partner_image_url": self._get_course_owners()[0]["logo_image_url"],
                    "org_name": self._get_course_owners()[0]["name"],
                }
            )

        return canvas_properties

    @patch("common.djangoapps.student.tasks.get_course_uuid_for_course")
    @patch("common.djangoapps.student.tasks.get_owners_for_course")
    @patch("common.djangoapps.student.tasks.get_course_run_details")
    @patch("common.djangoapps.student.tasks.get_course_dates_for_email")
    @patch("common.djangoapps.student.tasks.get_email_client")
    def test_success_calls_for_canvas_properties(
        self,
        mock_get_email_client,
        mock_get_course_dates_for_email,
        mock_get_course_run_details,
        mock_get_owners_for_course,
        mock_get_course_uuid_for_course,
    ):
        """
        Test to verify the "canvas entry properties" for enrollment email when
        all external calls are successful.
        """
        mock_get_course_uuid_for_course.return_value = self.course_uuid
        mock_get_owners_for_course.return_value = self._get_course_owners()
        mock_get_course_run_details.return_value = self._get_course_run()
        mock_get_course_dates_for_email.return_value = self._get_course_dates()

        send_course_enrollment_email.apply_async(
            kwargs=self.send_course_enrollment_email_kwargs
        )
        mock_get_email_client.return_value.send_canvas_message.assert_called_with(
            canvas_id=BRAZE_COURSE_ENROLLMENT_CANVAS_ID,
            recipients=[
                {
                    "external_user_id": self.user.id,
                }
            ],
            canvas_entry_properties=self._get_canvas_properties(),
        )

    @patch("common.djangoapps.student.tasks.get_course_uuid_for_course")
    @patch("common.djangoapps.student.tasks.get_owners_for_course")
    @patch("common.djangoapps.student.tasks.get_course_run_details")
    @patch("common.djangoapps.student.tasks.get_email_client")
    @patch(
        "common.djangoapps.student.tasks.get_course_dates_for_email",
        Mock(side_effect=Exception),
    )
    def test_canvas_properties_without_course_dates(
        self,
        mock_get_email_client,
        mock_get_course_run_details,
        mock_get_owners_for_course,
        mock_get_course_uuid_for_course,
    ):
        """
        Test that if exception is raised for the course dates call, correct
        canvas properties are sent to Braze.
        """
        mock_get_course_uuid_for_course.return_value = self.course_uuid
        mock_get_owners_for_course.return_value = self._get_course_owners()
        mock_get_course_run_details.return_value = self._get_course_run()

        send_course_enrollment_email.apply_async(
            kwargs=self.send_course_enrollment_email_kwargs
        )
        mock_get_email_client.return_value.send_canvas_message.assert_called_with(
            canvas_id=BRAZE_COURSE_ENROLLMENT_CANVAS_ID,
            recipients=[
                {
                    "external_user_id": self.user.id,
                }
            ],
            canvas_entry_properties=self._get_canvas_properties(add_course_dates=False),
        )

    @patch("common.djangoapps.student.tasks.get_course_uuid_for_course")
    @patch("common.djangoapps.student.tasks.get_owners_for_course")
    @patch("common.djangoapps.student.tasks.get_course_dates_for_email")
    @patch("common.djangoapps.student.tasks.get_email_client")
    @patch(
        "common.djangoapps.student.tasks.get_course_run_details",
        Mock(side_effect=Exception),
    )
    def test_canvas_properties_on_get_course_run_details_failure(
        self,
        mock_get_email_client,
        mock_get_course_dates_for_email,
        mock_get_owners_for_course,
        mock_get_course_uuid_for_course,
    ):
        """
        Test to verify the "canvas entry properties" in the enrollment email when
        get_course_run_details fails.
        """
        mock_get_course_uuid_for_course.return_value = self.course_uuid
        mock_get_owners_for_course.return_value = self._get_course_owners()
        mock_get_course_dates_for_email.return_value = self._get_course_dates()

        send_course_enrollment_email.apply_async(
            kwargs=self.send_course_enrollment_email_kwargs
        )
        mock_get_email_client.return_value.send_canvas_message.assert_called_with(
            canvas_id=BRAZE_COURSE_ENROLLMENT_CANVAS_ID,
            recipients=[
                {
                    "external_user_id": self.user.id,
                }
            ],
            canvas_entry_properties=self._get_canvas_properties(
                add_course_run_details=False
            ),
        )

    @patch("common.djangoapps.student.tasks.get_course_uuid_for_course")
    @patch("common.djangoapps.student.tasks.get_course_dates_for_email")
    @patch("common.djangoapps.student.tasks.get_email_client")
    @patch(TASK_LOGGER)
    def test_email_task_when_course_uuid_is_missing(
        self,
        mocked_logger,
        mock_get_email_client,
        mock_get_course_dates_for_email,
        mock_get_course_uuid_for_course,
    ):
        """
        Test that exception is logged when course_uuid returned by
        get_course_uuid_for_course is None and that email is sent with
        appropriate canvas properties.
        """
        mock_get_course_uuid_for_course.return_value = None
        mock_get_course_dates_for_email.return_value = self._get_course_dates()

        send_course_enrollment_email.apply_async(
            kwargs=self.send_course_enrollment_email_kwargs
        )
        mocked_logger.warning.assert_called_once_with(
            f"[Course Enrollment] Course run call failed for "
            f"user: {self.user.id} course: {self.course.id} error: Missing course_uuid"
        )
        mock_get_email_client.return_value.send_canvas_message.assert_called_with(
            canvas_id=BRAZE_COURSE_ENROLLMENT_CANVAS_ID,
            recipients=[
                {
                    "external_user_id": self.user.id,
                }
            ],
            canvas_entry_properties=self._get_canvas_properties(add_course_run_details=False),
        )

    @patch("common.djangoapps.student.tasks.get_course_uuid_for_course")
    @patch("common.djangoapps.student.tasks.get_owners_for_course")
    @patch("common.djangoapps.student.tasks.get_course_run_details")
    @patch("common.djangoapps.student.tasks.get_course_dates_for_email")
    @patch("common.djangoapps.student.tasks.get_email_client")
    @patch(TASK_LOGGER)
    def test_email_task_when_course_run_is_missing(
        self,
        mocked_logger,
        mock_get_email_client,
        mock_get_course_dates_for_email,
        mock_get_course_run_details,
        mock_get_owners_for_course,
        mock_get_course_uuid_for_course,
    ):
        """
        Test that exception is logged when course_run returned by
        get_course_run_details is an empty dictionary and that email is sent with
        appropriate canvas properties.
        """
        mock_get_course_dates_for_email.return_value = self._get_course_dates()
        mock_get_course_uuid_for_course.return_value = self.course_uuid
        mock_get_owners_for_course.return_value = []
        mock_get_course_run_details.return_value = {}

        send_course_enrollment_email.apply_async(
            kwargs=self.send_course_enrollment_email_kwargs
        )
        mocked_logger.warning.assert_called_once_with(
            f"[Course Enrollment] Course run call failed for "
            f"user: {self.user.id} course: {self.course.id} error: Missing course_run"
        )
        mock_get_email_client.return_value.send_canvas_message.assert_called_with(
            canvas_id=BRAZE_COURSE_ENROLLMENT_CANVAS_ID,
            recipients=[
                {
                    "external_user_id": self.user.id,
                }
            ],
            canvas_entry_properties=self._get_canvas_properties(add_course_run_details=False),
        )

    @patch("common.djangoapps.student.tasks.get_course_uuid_for_course")
    @patch("common.djangoapps.student.tasks.get_owners_for_course")
    @patch("common.djangoapps.student.tasks.get_course_run_details")
    @patch("common.djangoapps.student.tasks.get_course_dates_for_email")
    def test_retry_with_email_client_exception(
        self,
        mock_get_course_dates_for_email,
        mock_get_course_run_details,
        mock_get_owners_for_course,
        mock_get_course_uuid_for_course,
    ):
        """
        Test that we retry when an exception occurs from Braze Client
        """

        mock_get_course_uuid_for_course.return_value = self.course_uuid
        mock_get_owners_for_course.return_value = self._get_course_owners()
        mock_get_course_run_details.return_value = self._get_course_run()
        mock_get_course_dates_for_email.return_value = self._get_course_dates()

        with patch(
            'common.djangoapps.student.tasks.get_email_client',
            new_callable=PropertyMock,
            side_effect=Exception('Braze Client Exception')
        ) as mock_get_email_client:
            task = send_course_enrollment_email.apply_async(
                kwargs=self.send_course_enrollment_email_kwargs
            )
        pytest.raises(Exception, task.get)
        self.assertEqual(mock_get_email_client.call_count, (MAX_RETRIES + 1))

    @patch("common.djangoapps.student.tasks.get_course_uuid_for_course")
    @patch("common.djangoapps.student.tasks.get_owners_for_course")
    @patch("common.djangoapps.student.tasks.get_course_run_details")
    @patch("common.djangoapps.student.tasks.get_course_dates_for_email")
    @patch("common.djangoapps.student.tasks.get_email_client")
    def test_learning_base_url_uses_site_config(
        self,
        mock_get_email_client,
        mock_get_course_dates_for_email,
        mock_get_course_run_details,
        mock_get_owners_for_course,
        mock_get_course_uuid_for_course,
    ):
        """Test Braze payload sets learning_base_url from site-configured MFE URL."""
        mock_get_course_uuid_for_course.return_value = self.course_uuid
        mock_get_owners_for_course.return_value = self._get_course_owners()
        mock_get_course_run_details.return_value = self._get_course_run()
        mock_get_course_dates_for_email.return_value = self._get_course_dates()

        siteconf_url = "https://learningmfe.siteconf"

        def _get_value(key, default):
            return siteconf_url if key == "LEARNING_MICROFRONTEND_URL" else default

        with patch(
            "common.djangoapps.student.tasks.configuration_helpers.get_value",
            side_effect=_get_value,
        ) as mock_get_value:
            send_course_enrollment_email.apply_async(kwargs=self.send_course_enrollment_email_kwargs)

        expected = self._get_canvas_properties()
        expected["learning_base_url"] = siteconf_url

        mock_get_email_client.return_value.send_canvas_message.assert_called_with(
            canvas_id=BRAZE_COURSE_ENROLLMENT_CANVAS_ID,
            recipients=[{"external_user_id": self.user.id}],
            canvas_entry_properties=expected,
        )
        mock_get_value.assert_any_call("LEARNING_MICROFRONTEND_URL", settings.LEARNING_MICROFRONTEND_URL)
        mock_get_value.assert_any_call("LMS_ROOT_URL", settings.LMS_ROOT_URL)
