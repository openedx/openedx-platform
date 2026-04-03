"""
Tests of Zendesk interaction utility functions
"""


import json
from collections import OrderedDict
from unittest.mock import MagicMock, patch

import ddt
from django.test.utils import override_settings

from openedx.core.djangoapps.zendesk_proxy.utils import create_zendesk_ticket
from openedx.core.lib.api.test_utils import ApiTestCase


@ddt.ddt
@override_settings(
    ZENDESK_URL="https://www.superrealurlsthataredefinitelynotfake.com",
    ZENDESK_OAUTH_ACCESS_TOKEN="abcdefghijklmnopqrstuvwxyz1234567890",
    ZENDESK_GROUP_ID_MAPPING={"Financial Assistance": 123},
)
class TestUtils(ApiTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    def setUp(self):
        self.request_data = {
            'email': 'JohnQStudent@example.com',
            'name': 'John Q. Student',
            'subject': 'Python Unit Test Help Request',
            'body': "Help! I'm trapped in a unit test factory and I can't get out!",
        }
        return super().setUp()

    @override_settings(
        ZENDESK_URL=None,
        ZENDESK_OAUTH_ACCESS_TOKEN=None
    )
    def test_missing_settings(self):
        status_code = create_zendesk_ticket(
            requester_name=self.request_data['name'],
            requester_email=self.request_data['email'],
            subject=self.request_data['subject'],
            body=self.request_data['body'],
        )

        assert status_code == 503

    @ddt.data(201, 400, 401, 403, 404, 500)
    def test_zendesk_status_codes(self, mock_code):
        with patch('requests.post', return_value=MagicMock(status_code=mock_code)):
            status_code = create_zendesk_ticket(
                requester_name=self.request_data['name'],
                requester_email=self.request_data['email'],
                subject=self.request_data['subject'],
                body=self.request_data['body'],
            )

            assert status_code == mock_code

    def test_unexpected_error_pinging_zendesk(self):
        with patch('requests.post', side_effect=Exception("WHAMMY")):
            status_code = create_zendesk_ticket(
                requester_name=self.request_data['name'],
                requester_email=self.request_data['email'],
                subject=self.request_data['subject'],
                body=self.request_data['body'],
            )
            assert status_code == 500

    def test_financial_assistant_ticket(self):
        """ Test Financial Assistent request ticket. """
        ticket_creation_response_data = {
            "ticket": {
                "id": 35436,
                "subject": "My printer is on fire!",
            }
        }
        response_text = json.dumps(ticket_creation_response_data)
        with patch('requests.post', return_value=MagicMock(status_code=200, text=response_text)):
            with patch('requests.put', return_value=MagicMock(status_code=200)):
                status_code = create_zendesk_ticket(
                    requester_name=self.request_data['name'],
                    requester_email=self.request_data['email'],
                    subject=self.request_data['subject'],
                    body=self.request_data['body'],
                    group='Financial Assistance',
                    additional_info=OrderedDict(
                        (
                            ('Username', 'test'),
                            ('Full Name', 'Legal Name'),
                            ('Course ID', 'course_key'),
                            ('Country', 'Country'),
                        )
                    ),
                )
                assert status_code == 200
