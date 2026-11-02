"""
Exam Settings View Tests
"""
from unittest import SkipTest
from unittest.mock import patch

import ddt
import lxml
from django.conf import settings
from django.test.utils import override_settings
from edx_toggles.toggles.testutils import override_waffle_flag

from cms.djangoapps.contentstore import toggles
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import get_proctored_exam_settings_url, reverse_course_url
from common.djangoapps.util.testing import UrlResetMixin


@ddt.ddt
@override_settings(
    FEATURES={
        **settings.FEATURES,
        "CERTIFICATES_HTML_VIEW": True,
        "ENABLE_PROCTORED_EXAMS": True,
    },
)
@override_waffle_flag(toggles.LEGACY_STUDIO_CERTIFICATES, True)
@override_waffle_flag(toggles.LEGACY_STUDIO_SCHEDULE_DETAILS, True)
@override_waffle_flag(toggles.LEGACY_STUDIO_CONFIGURATIONS, True)
@override_waffle_flag(toggles.LEGACY_STUDIO_GRADING, True)
@override_waffle_flag(toggles.LEGACY_STUDIO_ADVANCED_SETTINGS, True)
class TestExamSettingsView(CourseTestCase, UrlResetMixin):
    """
    Unit tests for the exam settings view.
    """
    def setUp(self):
        """
        Set up the for the exam settings view tests.
        """
        super().setUp()
        self.reset_urls()

    @staticmethod
    def _get_exam_settings_alert_text(raw_html_content):
        """ Get text content of alert banner """
        parsed_html = lxml.html.fromstring(raw_html_content)
        alert_nodes = parsed_html.find_class('exam-settings-alert')
        assert len(alert_nodes) == 1
        alert_node = alert_nodes[0]
        return alert_node.text_content()

    @override_waffle_flag(toggles.LEGACY_STUDIO_EXAM_SETTINGS, True)
    @ddt.data(
        "certificates_list_handler",
        "settings_handler",
        "group_configurations_list_handler",
        "grading_handler",
        "advanced_settings_handler"
    )
    def test_view_without_exam_settings_enabled(self, handler):
        """
        Tests pages should not have `Exam Settings` item
        if course does not have the Exam Settings view enabled.
        """
        outline_url = reverse_course_url(handler, self.course.id)
        resp = self.client.get(outline_url, HTTP_ACCEPT='text/html')
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Proctored Exam Settings')

    @ddt.data(
        "certificates_list_handler",
        "settings_handler",
        "group_configurations_list_handler",
        "grading_handler",
        "advanced_settings_handler"
    )
    def test_view_with_exam_settings_enabled(self, handler):
        """
        Tests pages should have `Exam Settings` item
        if course does have Exam Settings view enabled.
        """
        outline_url = reverse_course_url(handler, self.course.id)
        resp = self.client.get(outline_url, HTTP_ACCEPT='text/html')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Proctored Exam Settings')

    @override_settings(
        PROCTORING_BACKENDS={
            'DEFAULT': 'test_proctoring_provider',
            'proctortrack': {}
        },
    )
    @ddt.data(
        "advanced_settings_handler",
    )
    def test_exam_settings_alert_with_exam_settings_enabled(self, page_handler):
        """
        An alert should appear if current exam settings are invalid.
        The exam settings alert should direct users to the exam settings page.
        """
        # create an error by setting proctortrack as the provider and not setting
        # the (required) escalation contact
        self.course.proctoring_provider = 'proctortrack'
        self.course.enable_proctored_exams = True
        self.save_course()

        # course_handler raise 404 for old mongo course
        if self.course.id.deprecated:
            raise SkipTest("course_handler raise 404 for old mongo course")

        url = reverse_course_url(page_handler, self.course.id)
        resp = self.client.get(url, HTTP_ACCEPT='text/html')
        alert_text = self._get_exam_settings_alert_text(resp.content)
        assert (
            'This course has proctored exam settings that are incomplete or invalid.'
            in alert_text
        )
        assert (
            'To update these settings go to the Proctored Exam Settings page.'
            in alert_text
        )

    @override_settings(
        PROCTORING_BACKENDS={
            'DEFAULT': 'test_proctoring_provider',
            'proctortrack': {}
        },
    )
    @ddt.data(
        "advanced_settings_handler",
    )
    @override_waffle_flag(toggles.LEGACY_STUDIO_EXAM_SETTINGS, True)
    def test_exam_settings_alert_with_exam_settings_disabled(self, page_handler):
        """
        An alert should appear if current exam settings are invalid.
        The exam settings alert should direct users to the advanced settings page
        if the exam settings feature is not available.
        """
        # create an error by setting proctortrack as the provider and not setting
        # the (required) escalation contact
        self.course.proctoring_provider = 'proctortrack'
        self.course.enable_proctored_exams = True
        self.save_course()

        # course_handler raise 404 for old mongo course
        if self.course.id.deprecated and page_handler == 'course_handler':
            raise SkipTest("course_handler raise 404 for old mongo course")
        url = reverse_course_url(page_handler, self.course.id)
        resp = self.client.get(url, HTTP_ACCEPT='text/html')
        alert_text = self._get_exam_settings_alert_text(resp.content)
        assert (
            'This course has proctored exam settings that are incomplete or invalid.'
            in alert_text
        )
        self.maxDiff = None
        if page_handler == 'advanced_settings_handler':
            assert (
                'You will be unable to make changes until the following settings are updated on the page below.'
                in alert_text
            )
        else:
            assert 'To update these settings go to the Advanced Settings page.' in alert_text

    @override_settings(
        PROCTORING_BACKENDS={
            'DEFAULT': 'test_proctoring_provider',
            'proctortrack': {},
            'test_proctoring_provider': {},
        },
    )
    @ddt.data(
        "advanced_settings_handler",
    )
    def test_invalid_provider_alert(self, page_handler):
        """
        An alert should appear if the course has a proctoring provider that is not valid.
        """
        # create an error by setting an invalid proctoring provider
        self.course.proctoring_provider = 'invalid_provider'
        self.course.enable_proctored_exams = True
        self.save_course()

        url = reverse_course_url(page_handler, self.course.id)
        resp = self.client.get(url, HTTP_ACCEPT='text/html')
        alert_text = self._get_exam_settings_alert_text(resp.content)
        assert (
            'This course has proctored exam settings that are incomplete or invalid.'
            in alert_text
        )
        assert (
            'The proctoring provider configured for this course, \'invalid_provider\', is not valid.'
            in alert_text
        )

    @ddt.data(
        "advanced_settings_handler",
    )
    def test_exam_settings_alert_not_shown(self, page_handler):
        """
        Alert should not be visible if no proctored exam setting error exists
        """
        # course_handler raise 404 for old mongo course
        if self.course.id.deprecated and page_handler == 'course_handler':
            raise SkipTest("course_handler raise 404 for old mongo course")
        url = reverse_course_url(page_handler, self.course.id)
        resp = self.client.get(url, HTTP_ACCEPT='text/html')
        parsed_html = lxml.html.fromstring(resp.content)
        alert_nodes = parsed_html.find_class('exam-settings-alert')
        assert len(alert_nodes) == 0

    @patch('cms.djangoapps.models.settings.course_metadata.CourseMetadata.validate_proctoring_settings')
    def test_proctoring_link_is_visible(self, mock_validate_proctoring_settings):

        """
        Test to check proctored exam settings mfe url is rendering properly
        """
        mock_validate_proctoring_settings.return_value = [
            {
                'key': 'proctoring_provider',
                'message': 'error message',
                'model': {'display_name': 'proctoring_provider'}
            },
            {
                'key': 'proctoring_provider',
                'message': 'error message',
                'model': {'display_name': 'proctoring_provider'}
            }
        ]
        response = self.client.get_html(reverse_course_url('advanced_settings_handler', self.course.id))
        proctored_exam_settings_url = get_proctored_exam_settings_url(self.course.id)
        self.assertContains(response, proctored_exam_settings_url, 3)
