"""
Unit tests for course index outline.
"""
from django.conf import settings
from django.test import RequestFactory
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag
from rest_framework import status

from cms.djangoapps.contentstore.config.waffle import CUSTOM_RELATIVE_DATES
from cms.djangoapps.contentstore.rest_api.v1.mixins import PermissionAccessMixin
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import get_lms_link_for_item, get_pages_and_resources_url
from cms.djangoapps.contentstore.views.course import _course_outline_json
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.waffle_utils.testutils import WAFFLE_TABLES
from xmodule.modulestore.tests.factories import BlockFactory, check_mongo_calls


class CourseIndexViewTest(CourseTestCase, PermissionAccessMixin):
    """
    Tests for CourseIndexView.
    """
    maxDiff = None  # Show the entire dictionary in the diff

    def setUp(self):
        super().setUp()
        with self.store.bulk_operations(self.course.id, emit_signals=False):
            self.chapter = BlockFactory.create(
                parent=self.course, display_name='Overview'
            )
            self.section = BlockFactory.create(
                parent=self.chapter, display_name='Welcome'
            )
            self.unit = BlockFactory.create(
                parent=self.section, display_name='New Unit'
            )
            self.xblock = BlockFactory.create(
                parent=self.unit,
                category='problem',
                display_name='Some problem'
            )
        self.user = UserFactory()
        self.factory = RequestFactory()
        self.request = self.factory.get(f"/course/{self.course.id}")
        self.request.user = self.user
        self.reload_course()
        self.url = reverse(
            "cms.djangoapps.contentstore:v1:course_index",
            kwargs={"course_id": self.course.id},
        )

    @override_waffle_flag(CUSTOM_RELATIVE_DATES, active=True)
    def test_course_index_response(self):
        """Check successful response content"""
        response = self.client.get(self.url)
        expected_response = {
            "course_release_date": "Set Date",
            "course_structure": _course_outline_json(self.request, self.course),
            "deprecated_blocks_info": {
                "deprecated_enabled_block_types": [],
                "blocks": [],
                "advance_settings_url": f"/settings/advanced/{self.course.id}"
            },
            "discussions_incontext_feedback_url": "",
            "discussions_incontext_learnmore_url": settings.DISCUSSIONS_INCONTEXT_LEARNMORE_URL,
            "is_custom_relative_dates_active": True,
            "initial_state": None,
            "initial_user_clipboard": {
                "content": None,
                "source_usage_key": "",
                "source_context_title": "",
                "source_edit_url": ""
            },
            "language_code": "en",
            "lms_link": get_lms_link_for_item(self.course.location),
            "mfe_proctored_exam_settings_url": (
                f"http://course-authoring-mfe/course/{self.course.id}"
                "/pages-and-resources/proctoring/settings"
            ),
            "notification_dismiss_url": None,
            "proctoring_errors": [],
            "reindex_link": f"/course/{self.course.id}/search_reindex",
            "rerun_notification_id": None,
            "discussions_settings": {
                "enable_in_context": True,
                "enable_graded_units": False,
                "unit_level_visibility": True,
                'discussion_configuration_url': f'{get_pages_and_resources_url(self.course.id)}/discussion/settings',
            },
            "advance_settings_url": f"/settings/advanced/{self.course.id}",
            'created_on': None,
        }

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertDictEqual(expected_response, response.data)  # noqa: PT009

    @override_waffle_flag(CUSTOM_RELATIVE_DATES, active=False)
    def test_course_index_response_with_show_locators(self):
        """Check successful response content with show query param"""
        response = self.client.get(self.url, {"show": str(self.unit.location)})
        expected_response = {
            "course_release_date": "Set Date",
            "course_structure": _course_outline_json(self.request, self.course),
            "deprecated_blocks_info": {
                "deprecated_enabled_block_types": [],
                "blocks": [],
                "advance_settings_url": f"/settings/advanced/{self.course.id}"
            },
            "discussions_incontext_feedback_url": "",
            "discussions_incontext_learnmore_url": settings.DISCUSSIONS_INCONTEXT_LEARNMORE_URL,
            "is_custom_relative_dates_active": False,
            "initial_state": {
                "expanded_locators": [
                    str(self.unit.location),
                    str(self.xblock.location),
                ],
                "locator_to_show": str(self.unit.location),
            },
            "initial_user_clipboard": {
                "content": None,
                "source_usage_key": "",
                "source_context_title": "",
                "source_edit_url": ""
            },
            "language_code": "en",
            "lms_link": get_lms_link_for_item(self.course.location),
            "mfe_proctored_exam_settings_url": (
                f"http://course-authoring-mfe/course/{self.course.id}"
                "/pages-and-resources/proctoring/settings"
            ),
            "notification_dismiss_url": None,
            "proctoring_errors": [],
            "reindex_link": f"/course/{self.course.id}/search_reindex",
            "rerun_notification_id": None,
            "discussions_settings": {
                "enable_in_context": True,
                "enable_graded_units": False,
                "unit_level_visibility": True,
                'discussion_configuration_url': f'{get_pages_and_resources_url(self.course.id)}/discussion/settings',
            },
            "advance_settings_url": f"/settings/advanced/{self.course.id}",
            'created_on': None,
        }

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertDictEqual(expected_response, response.data)  # noqa: PT009

    def test_course_index_response_with_invalid_course(self):
        """Check error response for invalid course id"""
        response = self.client.get(self.url + "1")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)  # noqa: PT009
        self.assertEqual(response.data, {  # noqa: PT009
            "developer_message": f"Unknown course {self.course.id}1",
            "error_code": "course_does_not_exist"
        })

    def test_number_of_calls_to_db(self):
        """
        Test to check number of queries made to mysql and mongo
        """
        with self.assertNumQueries(34, table_ignorelist=WAFFLE_TABLES):
            with check_mongo_calls(3):
                self.client.get(self.url)
