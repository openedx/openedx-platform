"""
Helper API classes for calling Braze APIs.
"""
import logging
import os

import backoff
import requests

LOG = logging.getLogger(__name__)
MAX_ATTEMPTS = int(os.environ.get('RETRY_BRAZE_MAX_ATTEMPTS', 5))


class BrazeException(Exception):
    pass


class BrazeRecoverableException(BrazeException):
    pass


class BrazeApi:
    """
    Braze API client used to make calls to Braze
    """

    def __init__(self, braze_api_key, braze_instance):
        self.api_key = braze_api_key

        # https://www.braze.com/docs/api/basics/#endpoints
        self.base_url = 'https://rest.{instance}.braze.com'.format(instance=braze_instance)

    @staticmethod
    def get_instance(config):
        """
        This function is used to get instance of BrazeApi.

        Returns:
            BrazeApi: Returns instance of BrazeApi.

        Args:
            config (dict): Configuration dictionary.

        Raises:
            KeyError: If braze_api_key or braze_instance is not present in config.
        """
        braze_api_key = config.get('braze_api_key', None)
        braze_instance = config.get('braze_instance', None)
        if not braze_api_key or not braze_instance:
            raise KeyError("braze_api_key or braze_instance is not present in config.")

        return BrazeApi(braze_api_key, braze_instance)

    def auth_headers(self):
        """Returns authorization headers suitable for passing to the requests library"""
        return {
            'Authorization': 'Bearer ' + self.api_key,
        }

    @staticmethod
    def get_error_message(response):
        """Returns a string suitable for logging"""
        try:
            json = response.json()
        except ValueError:
            json = {}

        # https://www.braze.com/docs/api/errors
        message = json.get('message')

        return message or response.reason

    def process_response(self, response, action):
        """Log response status and raise an error as needed"""
        if response.ok:
            LOG.info('Braze {action} succeeded'.format(action=action))
            return

        # We have some sort of error. Parse it, log it, and retry as needed.
        error_msg = 'Braze {action} failed due to {msg}'.format(action=action, msg=self.get_error_message(response))
        LOG.error(error_msg)

        if response.status_code == 429 or 500 <= response.status_code < 600:
            raise BrazeRecoverableException(error_msg)
        else:
            raise BrazeException(error_msg)

    @backoff.on_exception(
        backoff.expo,
        BrazeRecoverableException,
        max_tries=MAX_ATTEMPTS,
    )
    def delete_user(self, learner):
        """
        Delete a learner from Braze.
        """
        # https://www.braze.com/docs/help/gdpr_compliance/#the-right-to-erasure
        # https://www.braze.com/docs/api/endpoints/user_data/post_user_delete
        response = requests.post(
            self.base_url + '/users/delete',
            headers=self.auth_headers(),
            json={
                'external_ids': [learner['user']['id']],  # Braze external ids are LMS user ids
            },
        )
        self.process_response(response, 'user deletion')
