# pylint: disable=missing-module-docstring
import json
import threading
import unittest

import pytest

from lms.djangoapps.discussion.django_comment_client.tests.mock_cs_server.mock_cs_server import MockCommentServiceServer


class MockCommentServiceServerTest(unittest.TestCase):
    '''
    A mock version of the Comment Service server that listens on a local
    port and responds with pre-defined grade messages.
    '''

    def setUp(self):
        super().setUp()

        # This is a test of the test setup,
        # so it does not need to run as part of the unit test suite
        # You can re-enable it by commenting out the line below
        pytest.skip()

        # Create the server
        server_port = 4567
        self.server_url = 'http://127.0.0.1:%d' % server_port  # noqa: UP031

        # Start up the server and tell it that by default it should
        # return this as its json response
        self.expected_response = {'username': 'user100', 'external_id': '4'}
        self.server = MockCommentServiceServer(port_num=server_port,
                                               response=self.expected_response)
        self.addCleanup(self.server.shutdown)

        # Start the server in a separate daemon thread
        server_thread = threading.Thread(target=self.server.serve_forever)
        server_thread.daemon = True
        server_thread.start()

    def test_new_user_request(self):
        """
        Test the mock comment service using an example
        of how you would create a new user
        """
        # Send a request
        values = {'username': 'user100',
                  'external_id': '4', 'email': 'user100@edx.org'}
        data = json.dumps(values)
        headers = {'Content-Type': 'application/json', 'Content-Length': len(data), 'X-Edx-Api-Key': 'TEST_API_KEY'}
        req = six.moves.urllib.request.Request(self.server_url + '/api/v1/users/4', data, headers)  # pylint: disable=undefined-variable  # noqa: F821

        # Send the request to the mock cs server
        response = six.moves.urllib.request.urlopen(req)  # pylint: disable=undefined-variable  # noqa: F821

        # Receive the reply from the mock cs server
        response_dict = json.loads(response.read())

        # You should have received the response specified in the setup above
        assert response_dict == self.expected_response
