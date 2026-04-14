"""
Tests for the Mobile Course Info utils
"""
from unittest.mock import patch

import ddt
from django.test import RequestFactory
from django.urls import reverse

from lms.djangoapps.mobile_api.course_info.utils import get_user_certificate_download_url
from lms.djangoapps.mobile_api.testutils import MobileAPITestCase


@ddt.ddt
class TestCourseInfoUtils(MobileAPITestCase):
    """
    Tests for Course info utils
    """
    @ddt.data(
        ({'is_downloadable': True, 'download_url': 'https://test_certificate_url'},
         {'url': 'https://test_certificate_url'}),
        ({'is_downloadable': False}, {}),
    )
    @ddt.unpack
    @patch('lms.djangoapps.mobile_api.course_info.utils.certificate_downloadable_status')
    def test_get_certificate(self, certificate_status_return, expected_output, mock_certificate_status):
        """
        Test get_certificate utility from the Course info utils.
        Parameters:
        certificate_status_return: returned value of the mocked certificate_downloadable_status function.
        expected_output: return_value of the get_certificate function with specified mock return_value.
        """
        mock_certificate_status.return_value = certificate_status_return
        url = reverse('blocks_info_in_course', kwargs={'api_version': 'v3'})
        request = RequestFactory().get(url)
        request.user = self.user

        certificate_info = get_user_certificate_download_url(
            request, self.user, 'course-v1:Test+T101+2021_T1'
        )
        self.assertEqual(certificate_info, expected_output)  # noqa: PT009
