"""
Test the heartbeat
"""


import json
from unittest.mock import patch

from django.db.utils import DatabaseError
from django.test.client import Client
from django.urls import reverse

from xmodule.exceptions import HeartbeatFailure
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase


class HeartbeatTestCase(ModuleStoreTestCase):
    """
    Test the heartbeat
    """

    def setUp(self):
        self.client = Client()
        self.heartbeat_url = reverse('heartbeat')
        return super().setUp()

    @patch('openedx.core.djangoapps.heartbeat.runchecks.set_custom_attribute')
    def test_success(self, mock_set_attribute):
        response = self.client.get(self.heartbeat_url + '?extended')

        assert response.status_code == 200
        # We only annotate failing requests
        mock_set_attribute.assert_not_called()

    def test_sql_fail(self):
        with patch('openedx.core.djangoapps.heartbeat.default_checks.connection') as mock_connection:
            mock_connection.cursor.return_value.execute.side_effect = DatabaseError
            response = self.client.get(self.heartbeat_url)
            assert response.status_code == 503
            response_dict = json.loads(response.content.decode('utf-8'))
            assert 'sql' in response_dict

    @patch('openedx.core.djangoapps.heartbeat.runchecks.set_custom_attribute')
    def test_modulestore_fail(self, mock_set_attribute):
        with patch('openedx.core.djangoapps.heartbeat.default_checks.modulestore') as mock_modulestore:
            mock_modulestore.return_value.heartbeat.side_effect = HeartbeatFailure('msg', 'service')
            response = self.client.get(self.heartbeat_url)
            assert response.status_code == 503
        # Spot-checking a failure
        mock_set_attribute.assert_any_call(
            'heartbeat.failure.openedx.core.djangoapps.heartbeat.default_checks.check_modulestore', 'msg'
        )
