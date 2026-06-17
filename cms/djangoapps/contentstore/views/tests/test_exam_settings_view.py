"""
Exam Settings View Tests
"""
from django.test.utils import override_settings

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import reverse_course_url
from common.djangoapps.util.testing import UrlResetMixin


@override_settings(
    CERTIFICATES_HTML_VIEW=True,
    ENABLE_PROCTORED_EXAMS=True,
)
@override_settings(COURSE_AUTHORING_MICROFRONTEND_URL='https://mfe.example')
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

    def test_grading_handler_redirects_to_mfe(self):
        """grading_handler redirects to the authoring MFE."""
        url = reverse_course_url('grading_handler', self.course.id)
        resp = self.client.get(url, HTTP_ACCEPT='text/html')
        self.assertEqual(resp.status_code, 302)  # noqa: PT009

    def test_settings_handler_redirects_to_mfe(self):
        """settings_handler (schedule & details) redirects to the authoring MFE."""
        url = reverse_course_url('settings_handler', self.course.id)
        resp = self.client.get(url, HTTP_ACCEPT='text/html')
        self.assertEqual(resp.status_code, 302)  # noqa: PT009

    def test_certificates_list_handler_redirects_to_mfe(self):
        """certificates_list_handler redirects to the authoring MFE."""
        url = reverse_course_url('certificates_list_handler', self.course.id)
        resp = self.client.get(url, HTTP_ACCEPT='text/html')
        self.assertEqual(resp.status_code, 302)  # noqa: PT009

    def test_advanced_settings_handler_redirects_to_mfe(self):
        """advanced_settings_handler redirects to the authoring MFE."""
        url = reverse_course_url('advanced_settings_handler', self.course.id)
        resp = self.client.get(url, HTTP_ACCEPT='text/html')
        self.assertEqual(resp.status_code, 302)  # noqa: PT009

    def test_group_configurations_list_handler_redirects_to_mfe(self):
        """group_configurations_list_handler redirects to the authoring MFE."""
        url = reverse_course_url('group_configurations_list_handler', self.course.id)
        resp = self.client.get(url, HTTP_ACCEPT='text/html')
        self.assertEqual(resp.status_code, 302)  # noqa: PT009
