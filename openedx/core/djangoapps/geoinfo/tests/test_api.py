"""
Tests for the geoinfo API.
"""


from unittest.mock import MagicMock, PropertyMock, patch

import ddt
import geoip2
import maxminddb
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import TestCase
from django.test.client import RequestFactory

from openedx.core.djangoapps.geoinfo.api import country_code_for_request


@ddt.ddt
class CountryCodeForRequestTests(TestCase):
    """
    Tests of country_code_for_request.
    """
    def setUp(self):
        super().setUp()
        self.request_factory = RequestFactory()
        patcher = patch.object(maxminddb, 'open_database')
        patcher.start()
        self.country_mock = MagicMock(side_effect=self.mock_country)
        country_patcher = patch.object(geoip2.database.Reader, 'country', self.country_mock)
        country_patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(country_patcher.stop)

    def mock_country(self, ip_address):
        """
        Return a mock geoip2 country response for the given IP address.
        """
        ip_dict = {
            '117.79.83.1': 'CN',
            '4.0.0.0': 'SD',
            '2001:da8:20f:1502:edcf:550b:4a9c:207d': 'CN',
        }

        magic_mock = MagicMock()
        magic_mock.country = MagicMock()
        type(magic_mock.country).iso_code = PropertyMock(return_value=ip_dict.get(ip_address))

        return magic_mock

    @ddt.data(
        ('117.79.83.1', 'CN'),
        ('4.0.0.0', 'SD'),
        ('2001:da8:20f:1502:edcf:550b:4a9c:207d', 'CN'),
        ('8.8.8.8', ''),
    )
    @ddt.unpack
    def test_country_code(self, ip_address, expected_country_code):
        request = self.request_factory.get('/somewhere', HTTP_X_FORWARDED_FOR=ip_address)
        assert country_code_for_request(request) == expected_country_code

    def test_non_global_ip_address_is_not_looked_up(self):
        request = self.request_factory.get('/somewhere', HTTP_X_FORWARDED_FOR='10.0.0.1')
        assert country_code_for_request(request) == ''
        self.country_mock.assert_not_called()

    def test_lookup_runs_once_per_request(self):
        request = self.request_factory.get('/somewhere', HTTP_X_FORWARDED_FOR='117.79.83.1')
        assert country_code_for_request(request) == 'CN'
        assert country_code_for_request(request) == 'CN'
        self.country_mock.assert_called_once_with('117.79.83.1')

    def test_session_is_not_modified(self):
        request = self.request_factory.get('/somewhere', HTTP_X_FORWARDED_FOR='117.79.83.1')
        SessionMiddleware(get_response=lambda request: None).process_request(request)
        assert country_code_for_request(request) == 'CN'
        assert not request.session.modified
        assert request.session.is_empty()
