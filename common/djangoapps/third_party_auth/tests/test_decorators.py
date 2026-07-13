"""
Tests for third_party_auth decorators.
"""

import ddt
from django.http import HttpResponse
from django.test import RequestFactory

from common.djangoapps.third_party_auth.decorators import xframe_allow_whitelisted
from common.djangoapps.third_party_auth.tests.testutil import TestCase
from common.djangoapps.third_party_auth.tests.utils import skip_unless_thirdpartyauth


@xframe_allow_whitelisted
def mock_view(_request):
    """ A test view for testing purposes. """
    return HttpResponse()


@skip_unless_thirdpartyauth()
@ddt.ddt
class TestXFrameWhitelistDecorator(TestCase):
    """ Test the xframe_allow_whitelisted decorator. """

    def setUp(self):
        super().setUp()
        self.configure_lti_provider(name='Test', lti_hostname='localhost', lti_consumer_key='test_key', enabled=True)
        self.factory = RequestFactory()

    def construct_request(self, referer):
        """ Add the given referer to a request and then return it. """
        request = self.factory.get('/login')
        request.META['HTTP_REFERER'] = referer
        return request

    @ddt.unpack
    @ddt.data(
        ('http://localhost:8000/login', 'ALLOW'),
        ('http://not-a-real-domain.com/login', 'DENY'),
        (None, 'DENY')
    )
    def test_x_frame_options(self, url, expected_result):
        request = self.construct_request(url)

        response = mock_view(request)

        assert response['X-Frame-Options'] == expected_result

    @ddt.data('http://localhost/login', 'http://not-a-real-domain.com', None)
    def test_feature_flag_off(self, url):
        with self.settings(ENABLE_THIRD_PARTY_AUTH=False):
            request = self.construct_request(url)
            response = mock_view(request)
            assert response['X-Frame-Options'] == 'DENY'
