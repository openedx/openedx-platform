# pylint: skip-file


import json
from logging import getLogger

from six.moves.BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer

logger = getLogger(__name__)


class MockCommentServiceRequestHandler(BaseHTTPRequestHandler):
    '''
    A handler for Comment Service POST requests.
    '''
    protocol = "HTTP/1.0"

    def do_POST(self):
        '''
        Handle a POST request from the client
        Used by the APIs for comment threads, commentables, comments,
        subscriptions, commentables, users
        '''
        # Retrieve the POST data into a dict.
        # It should have been sent in json format
        length = int(self.headers.getheader('content-length'))
        data_string = self.rfile.read(length)
        post_dict = json.loads(data_string)

        # Log the request
        # pylint: disable=logging-format-interpolation
        logger.debug(
            "Comment Service received POST request {} to path {}"  # noqa: UP032
            .format(json.dumps(post_dict), self.path)
        )

        # Every good post has at least an API key
        if 'X-Edx-Api-Key' in self.headers:
            response = self.server._response_str
            # Log the response
            logger.debug("Comment Service: sending response %s", json.dumps(response))

            # Send a response back to the client
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(response)

        else:
            # Respond with failure
            self.send_response(500, 'Bad Request: does not contain API key')
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            return False

    def do_PUT(self):
        '''
        Handle a PUT request from the client
        Used by the APIs for comment threads, commentables, comments,
        subscriptions, commentables, users
        '''
        # Retrieve the PUT data into a dict.
        # It should have been sent in json format
        length = int(self.headers.getheader('content-length'))
        data_string = self.rfile.read(length)
        post_dict = json.loads(data_string)

        # Log the request
        # pylint: disable=logging-format-interpolation
        logger.debug(
            "Comment Service received PUT request {} to path {}"  # noqa: UP032
            .format(json.dumps(post_dict), self.path)
        )

        # Every good post has at least an API key
        if 'X-Edx-Api-Key' in self.headers:
            response = self.server._response_str
            # Log the response
            logger.debug("Comment Service: sending response %s", json.dumps(response))

            # Send a response back to the client
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(response)

        else:
            # Respond with failure
            self.send_response(500, 'Bad Request: does not contain API key')
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            return False


class MockCommentServiceServer(HTTPServer):
    '''
    A mock Comment Service server that responds
    to POST requests to localhost.
    '''
    def __init__(self, port_num,
                 response={'username': 'new', 'external_id': 1}):  # noqa: B006
        '''
        Initialize the mock Comment Service server instance.
        *port_num* is the localhost port to listen to
        *response* is a dictionary that will be JSON-serialized
            and sent in response to comment service requests.
        '''
        self._response_str = json.dumps(response)

        handler = MockCommentServiceRequestHandler
        address = ('', port_num)
        HTTPServer.__init__(self, address, handler)

    def shutdown(self):
        '''
        Stop the server and free up the port
        '''
        # First call superclass shutdown()
        HTTPServer.shutdown(self)

        # We also need to manually close the socket
        self.socket.close()
