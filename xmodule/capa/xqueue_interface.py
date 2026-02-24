"""
LMS Interface to external queueing system (xqueue)
"""

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Dict, Optional

import requests
from requests.auth import HTTPBasicAuth

from xmodule.capa.xqueue_submission import XQueueInterfaceSubmission

if TYPE_CHECKING:
    from xmodule.capa_block import ProblemBlock

log = logging.getLogger(__name__)
DATEFORMAT = "%Y%m%d%H%M%S"

XQUEUE_METRIC_NAME = "edxapp.xqueue"

# Wait time for response from Xqueue.
XQUEUE_TIMEOUT = 35  # seconds
CONNECT_TIMEOUT = 3.05  # seconds
READ_TIMEOUT = 10  # seconds


def make_hashkey(seed):
    """
    Generate a string key by hashing
    """
    h = hashlib.md5()
    h.update(str(seed).encode("latin-1"))
    return h.hexdigest()


def make_xheader(lms_callback_url, lms_key, queue_name):
    """
    Generate header for delivery and reply of queue request.

    Xqueue header is a JSON-serialized dict:
        { 'lms_callback_url': url to which xqueue will return the request (string),
          'lms_key': secret key used by LMS to protect its state (string),
          'queue_name': designate a specific queue within xqueue server, e.g. 'MITx-6.00x' (string)
        }
    """
    return json.dumps({"lms_callback_url": lms_callback_url, "lms_key": lms_key, "queue_name": queue_name})


def parse_xreply(xreply):
    """
    Parse the reply from xqueue. Messages are JSON-serialized dict:
        { 'return_code': 0 (success), 1 (fail)
          'content': Message from xqueue (string)
        }
    """
    try:
        xreply = json.loads(xreply)
    except ValueError as err:
        log.error(err)
        return (1, "unexpected reply from server")

    return_code = xreply["return_code"]
    content = xreply["content"]

    return (return_code, content)


class XQueueInterface:  # pylint: disable=too-few-public-methods
    """Initializes the XQueue interface."""

    def __init__(
        self,
        url: str,
        django_auth: Dict[str, str],
        requests_auth: Optional[HTTPBasicAuth] = None,
        block: "ProblemBlock" = None,
        use_submission_service: bool = False,
    ):
        """
        Initializes the XQueue interface.

        Args:
            url (str): The URL of the XQueue service.
            django_auth (Dict[str, str]): Authentication credentials for Django.
            requests_auth (Optional[HTTPBasicAuth], optional): Authentication for HTTP requests. Defaults to None.
            block ('ProblemBlock', optional): Added as a parameter only to extract the course_id
                to check the course waffle flag `send_to_submission_course.enable`.
                This can be removed after the legacy xqueue is deprecated. Defaults to None.
            use_submission_service (bool): If True, use the edx-submissions service instead of XQueue.
        """
        self.url = url
        self.auth = django_auth
        self.session = requests.Session()
        self.session.auth = requests_auth
        self.block = block
        self.submission = XQueueInterfaceSubmission(self.block)
        self.use_submission_service = use_submission_service

    def send_to_queue(self, header, body, files_to_upload=None):
        """
        Submit a request to xqueue.

        header: JSON-serialized dict in the format described in 'xqueue_interface.make_xheader'

        body: Serialized data for the receipient behind the queueing service. The operation of
              xqueue is agnostic to the contents of 'body'

        files_to_upload: List of file objects to be uploaded to xqueue along with queue request

        Returns (error_code, msg) where error_code != 0 indicates an error
        """

        # log the send to xqueue
        header_info = json.loads(header)
        queue_name = header_info.get("queue_name", "")  # pylint: disable=unused-variable

        # Attempt to send to queue
        (error, msg) = self._send_to_queue(header, body, files_to_upload)

        # Log in, then try again
        if error and (msg == "login_required"):
            (error, content) = self._login()
            if error != 0:
                # when the login fails
                log.debug("Failed to login to queue: %s", content)
                return (error, content)
            if files_to_upload is not None:
                # Need to rewind file pointers
                for f in files_to_upload:
                    f.seek(0)
            (error, msg) = self._send_to_queue(header, body, files_to_upload)

        return error, msg

    def _login(self):
        payload = {"username": self.auth["username"], "password": self.auth["password"]}
        return self._http_post(self.url + "/xqueue/login/", payload)

    def _send_to_queue(self, header, body, files_to_upload):
        """Send the problem submission to XQueue, handling legacy fallback and edX submission logic."""

        payload = {"xqueue_header": header, "xqueue_body": body}
        files = {}
        if files_to_upload is not None:
            for f in files_to_upload:
                files.update({f.name: f})

        if self.block is None:
            # XQueueInterface: if self.block is None, falling back to legacy xqueue submission.
            log.error(
                "Unexpected None block: falling back to legacy xqueue submission. "
                "This may indicate a problem with the xqueue transition."
            )
            return self._http_post(self.url + "/xqueue/submit/", payload, files=files)

        header_info = json.loads(header)
        queue_key = header_info["lms_key"]  # pylint: disable=unused-variable

        if self.use_submission_service:
            submission = self.submission.send_to_submission(  # pylint: disable=unused-variable
                header, body, queue_key, files
            )
            return None, ""

        return self._http_post(self.url + "/xqueue/submit/", payload, files=files)

    def _http_post(self, url, data, files=None):
        """Send an HTTP POST request and handle connection errors, timeouts, and unexpected status codes."""

        try:
            response = self.session.post(url, data=data, files=files, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        except requests.exceptions.ConnectionError as err:
            log.error(err)
            return 1, "cannot connect to server"

        except requests.exceptions.ReadTimeout as err:
            log.error(err)
            return 1, "failed to read from the server"

        if response.status_code not in [200]:
            return 1, f"unexpected HTTP status code [{response.status_code}]"

        return parse_xreply(response.text)
