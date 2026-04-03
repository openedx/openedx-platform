"""
Segment API call wrappers
"""
import logging
import sys
import traceback

import backoff
import requests
from simplejson.errors import JSONDecodeError
from six import text_type

# Maximum number of tries on Segment API calls
MAX_TRIES = 4

# These are the required/optional keys in the learner dict that contain IDs we need to retire from Segment.
REQUIRED_IDENTIFYING_KEYS = [('user', 'id'), 'original_username']
OPTIONAL_IDENTIFYING_KEYS = ['ecommerce_segment_id']

# The Segment Config API for bulk deleting users for a particular workspace
BULK_REGULATE_URL = 'v1beta/workspaces/{}/regulations'

# The Segment Config API for querying the status of a bulk user deletion request for a particular workspace
BULK_REGULATE_STATUS_URL = 'v1beta/workspaces/{}/regulations/{}'

# According to Segment this represents the maximum limits of the bulk regulation call.
# https://reference.segmentapis.com/?version=latest#57a69434-76cc-43cc-a547-98c319182247
MAXIMUM_USERS_IN_REGULATION_REQUEST = 5000

LOG = logging.getLogger(__name__)


def _backoff_handler(details):
    """
    Simple logging handler for when timeout backoff occurs.
    """
    LOG.error('Trying again in {wait:0.1f} seconds after {tries} tries calling {target}'.format(**details))

    # Log the text response from any HTTPErrors, if possible
    try:
        LOG.error(traceback.format_exc())
        exc = sys.exc_info()[1]
        LOG.error("HTTPError code {}: {}".format(exc.response.status_code, exc.response.text))
    except Exception:  # pylint: disable=broad-except
        pass


def _wait_30_seconds():
    """
    Backoff generator that waits for 30 seconds.
    """
    return backoff.constant(interval=30)


def _http_status_giveup(exc):
    """
    Giveup method that gives up backoff upon any non-5xx and 504 server errors.
    """
    return not 429 == exc.response.status_code and not 500 <= exc.response.status_code < 600


def _retry_segment_api():
    """
    Decorator which enables retries with sane backoff defaults
    """

    def inner(func):  # pylint: disable=missing-docstring
        func_with_decode_backoff = backoff.on_exception(
            backoff.expo,
            JSONDecodeError,
            max_tries=MAX_TRIES,
            on_backoff=lambda details: _backoff_handler(details)  # pylint: disable=unnecessary-lambda
        )
        func_with_backoff = backoff.on_exception(
            backoff.expo,
            requests.exceptions.HTTPError,
            max_tries=MAX_TRIES,
            giveup=_http_status_giveup,
            on_backoff=lambda details: _backoff_handler(details)  # pylint: disable=unnecessary-lambda
        )
        func_with_timeout_backoff = backoff.on_exception(
            _wait_30_seconds,
            requests.exceptions.Timeout,
            max_tries=MAX_TRIES,
            on_backoff=lambda details: _backoff_handler(details)  # pylint: disable=unnecessary-lambda
        )
        return func_with_decode_backoff(func_with_backoff(func_with_timeout_backoff(func)))

    return inner


class SegmentApi:
    """
    Segment API client with convenience methods
    """

    def __init__(self, base_url, auth_token, workspace_slug):
        self.base_url = base_url
        self.auth_token = auth_token
        self.workspace_slug = workspace_slug

    @staticmethod
    def get_instance(config):
        """
        This function is used to get instance of SegmentApi.

        Returns:
            SegmentApi: Returns instance of SegmentApi.

        Args:
            config (dict): Configuration dictionary.

        Raises:
            KeyError: If segment_base_url, segment_auth_token, and segment_workspace_slug are not present in config.
        """
        segment_base_url = config.get('segment_base_url', None)
        segment_auth_token = config.get('segment_auth_token', None)
        segment_workspace_slug = config.get('segment_workspace_slug', None)

        if not segment_base_url or not segment_auth_token or not segment_workspace_slug:
            raise KeyError("segment_base_url, segment_auth_token or segment_workspace_slug is not present in config.")

        return SegmentApi(segment_base_url, segment_auth_token, segment_workspace_slug)

    @_retry_segment_api()
    def _call_segment_post(self, url, params):
        """
        Actually makes the Segment REST POST call.

        5xx errors and timeouts will be retried via _retry_segment_api,
        all others will bubble up.
        """
        headers = {
            "Authorization": "Bearer {}".format(self.auth_token),
            "Content-Type": "application/json"
        }
        resp = requests.post(self.base_url + url, json=params, headers=headers)
        resp.raise_for_status()
        return resp

    @_retry_segment_api()
    def _call_segment_get(self, url):
        """
        Actually makes the Segment REST GET call.

        5xx errors and timeouts will be retried via _retry_segment_api,
        all others will bubble up.
        """
        headers = {
            "Authorization": "Bearer {}".format(self.auth_token)
        }
        resp = requests.get(self.base_url + url, headers=headers)
        resp.raise_for_status()
        return resp

    def _get_value_from_learner(self, learner, key):
        """
        Return the value from a learner dict for the given key or 2-tuple of keys.

        Allows us to map things like learner['user']['id'] in a single entry in REQUIRED_IDENTIFYING_KEYS.
        """
        if isinstance(key, tuple):
            val = learner[key[0]][key[1]]
        else:
            val = learner[key]

        return text_type(val)

    def _send_regulation_request(self, params):
        """
        Make the call to the Segment Regulate API, cleanly report any errors
        """
        resp_json = ""

        try:
            resp = self._call_segment_post(BULK_REGULATE_URL.format(self.workspace_slug), params)
            try:
                resp_json = resp.json()
                bulk_user_delete_id = resp_json['regulate_id']
                LOG.info('Bulk user regulation queued. Id: {}'.format(bulk_user_delete_id))
            except JSONDecodeError:
                resp_json = resp.text
                raise

        # If we get here we got some kind of JSON response from Segment, we'll try to get
        # the data we need. If it doesn't exist we'll bubble up the error from Segment and
        # eat the TypeError / KeyError since they won't be relevant.
        except (TypeError, KeyError, requests.exceptions.HTTPError, JSONDecodeError) as exc:
            LOG.exception(exc)
            err = u'Error was encountered for params: {} \n\n Response: {}'.format(
                params,
                text_type(resp_json)
            ).encode('utf-8')
            LOG.error(err)

            raise Exception(err)

    def delete_and_suppress_learner(self, learner):
        """
        Delete AND suppress a single Segment user using the bulk user deletion REST API.

        :param learner: Single user retirement status row with its fields.
        """
        # Send a list of one learner to be deleted by the multiple learner deletion call.
        return self.delete_and_suppress_learners([learner], 1)

    def unsuppress_learners_by_key(self, key, learners, chunk_size, beginning_idx=0):
        """
        Sets up the Segment REST API calls to UNSUPPRESS users in chunks.

        :param key: Key in the learner dict to pull the ID we care about from.
        :param learners: List of learner dicts to be worked on. We only use the key passed in.
        :param chunk_size: How many learners should be retired in this batch.
        :param beginning_idx: Index into learners where this batch should start.
        """
        curr_idx = beginning_idx
        while curr_idx < len(learners):
            start_idx = curr_idx
            end_idx = min(start_idx + chunk_size - 1, len(learners) - 1)

            LOG.info(
                "Attempting unsuppress for key '%s', start index %s, end index %s for learners '%s' through '%s'",
                key,
                start_idx, end_idx,
                learners[start_idx]['original_username'],
                learners[end_idx]['original_username']
            )

            learner_vals = []
            for idx in range(start_idx, end_idx + 1):
                learner_vals.append(self._get_value_from_learner(learners[idx], key))

            if len(learner_vals) >= MAXIMUM_USERS_IN_REGULATION_REQUEST:
                LOG.error(
                    'Attempting to UNSUPPRESS too many user values (%s) at once in bulk request - decrease chunk_size.',
                    len(learner_vals)
                )
                return

            params = {
                "regulation_type": "Unsuppress",
                "attributes": {
                    "name": "userId",
                    "values": learner_vals
                }
            }

            self._send_regulation_request(params)

            curr_idx += chunk_size

    def delete_and_suppress_learners(self, learners, chunk_size, beginning_idx=0):
        """
        Sets up the Segment REST API calls to GDPR-delete users in chunks.

        :param learners: List of learner dicts returned from LMS, should contain all we need to retire this learner.
        :param chunk_size: How many learners should be retired in this batch.
        :param beginning_idx: Index into learners where this batch should start.
        """
        curr_idx = beginning_idx
        while curr_idx < len(learners):
            start_idx = curr_idx
            end_idx = min(start_idx + chunk_size - 1, len(learners) - 1)
            LOG.info(
                "Attempting Segment deletion with start index %s, end index %s for learners (%s, %s) through (%s, %s)",
                start_idx, end_idx,
                learners[start_idx]['user']['id'], learners[start_idx]['original_username'],
                learners[end_idx]['user']['id'], learners[end_idx]['original_username']
            )

            learner_vals = []
            for idx in range(start_idx, end_idx + 1):
                for id_key in REQUIRED_IDENTIFYING_KEYS:
                    learner_vals.append(self._get_value_from_learner(learners[idx], id_key))
                for id_key in OPTIONAL_IDENTIFYING_KEYS:
                    if id_key in learners[idx]:
                        learner_vals.append(self._get_value_from_learner(learners[idx], id_key))

            if len(learner_vals) >= MAXIMUM_USERS_IN_REGULATION_REQUEST:
                LOG.error(
                    'Attempting to delete too many user values (%s) at once in bulk request - decrease chunk_size.',
                    len(learner_vals)
                )
                return

            params = {
                "regulation_type": "Suppress_With_Delete",
                "attributes": {
                    "name": "userId",
                    "values": learner_vals
                }
            }

            self._send_regulation_request(params)

            curr_idx += chunk_size

    def get_bulk_delete_status(self, bulk_delete_id):
        """
        Queries the status of a previously submitted bulk delete request.

        :param bulk_delete_id: ID returned from a previously-submitted bulk delete request.
        """
        resp = self._call_segment_get(BULK_REGULATE_STATUS_URL.format(self.workspace_slug, bulk_delete_id))
        resp_json = resp.json()
        LOG.info(text_type(resp_json))
