"""
Unit tests for the course waffle flags view
"""

from django.urls import reverse

from cms.djangoapps.contentstore import toggles
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from openedx.core.djangoapps.waffle_utils.models import WaffleFlagCourseOverrideModel


class CourseWaffleFlagsViewTest(CourseTestCase):
    """
    Basic test for the CourseWaffleFlagsView endpoint, which returns waffle flag states
    for a specific course or globally if no course ID is provided.
    """

    maxDiff = None  # Show the whole dictionary in the diff

    defaults = {
        "enable_course_optimizer": False,
        "use_new_advanced_settings_page": True,
        "use_new_certificates_page": True,
        "use_new_course_outline_page": True,
        "use_new_course_team_page": True,
        "use_new_custom_pages": True,
        "use_new_export_page": True,
        "use_new_files_uploads_page": True,
        "use_new_grading_page": True,
        "use_new_group_configurations_page": True,
        "use_new_home_page": True,
        "use_new_import_page": True,
        "use_new_schedule_details_page": True,
        "use_new_textbooks_page": True,
        "use_new_unit_page": True,
        "use_new_updates_page": True,
        "use_new_video_uploads_page": False,
        "use_react_markdown_editor": False,
        "use_video_gallery_flow": False,
        "enable_course_optimizer_check_prev_run_links": False,
        "enable_authz_course_authoring": False,
    }

    def setUp(self):
        super().setUp()
        WaffleFlagCourseOverrideModel.objects.create(
            waffle_flag=toggles.ENABLE_COURSE_OPTIMIZER.name,
            course_id=self.course.id,
            enabled=True,
        )
        WaffleFlagCourseOverrideModel.objects.create(
            waffle_flag=toggles.ENABLE_COURSE_OPTIMIZER_CHECK_PREV_RUN_LINKS.name,
            course_id=self.course.id,
            enabled=True,
        )

    def test_global_defaults(self):
        url = reverse("cms.djangoapps.contentstore:v1:course_waffle_flags")
        response = self.client.get(url)
        assert response.data == self.defaults

    def test_course_override(self):
        url = reverse(
            "cms.djangoapps.contentstore:v1:course_waffle_flags",
            kwargs={"course_id": self.course.id},
        )
        response = self.client.get(url)
        assert response.data == {
            **self.defaults,
            "enable_course_optimizer": True,
            "enable_course_optimizer_check_prev_run_links": True,
        }
