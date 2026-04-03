"""
Amplitude API class that is used to delete user from Amplitude.
"""
import logging
import os

import backoff
import requests

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = int(os.environ.get("RETRY_MAX_ATTEMPTS", 5))


class AmplitudeException(Exception):
    """
    AmplitudeException will be raised there is fatal error and is not recoverable.
    """
    pass


class AmplitudeRecoverableException(AmplitudeException):
    """
    AmplitudeRecoverableException will be raised when request can be retryable.
    """
    pass


class AmplitudeApi:
    """
    Amplitude API is used to handle communication with Amplitude Api's.
    """

    def __init__(self, amplitude_api_key, amplitude_secret_key):
        self.amplitude_api_key = amplitude_api_key
        self.amplitude_secret_key = amplitude_secret_key
        self.base_url = "https://amplitude.com/"
        self.delete_user_path = "api/2/deletions/users"

    @staticmethod
    def get_instance(config):
        """
        This function is used to get instance of AmplitudeApi.

        Returns:
            AmplitudeApi: Returns instance of AmplitudeApi.

        Args:
            config (dict): Configuration dictionary.

        Raises:
            KeyError: If amplitude_api_key or amplitude_secret_key is not present in config.
        """
        amplitude_api_key = config.get('amplitude_api_key', None)
        amplitude_secret_key = config.get('amplitude_secret_key', None)
        if not amplitude_api_key or not amplitude_secret_key:
            raise KeyError("amplitude_api_key or amplitude_secret_key is not present in config.")
        return AmplitudeApi(amplitude_api_key, amplitude_secret_key)

    def auth(self):
        """
        Returns auth credentials for Amplitude authorization.

        Returns:
            Tuple: Returns authorization tuple.
        """
        return (self.amplitude_api_key, self.amplitude_secret_key)

    @backoff.on_exception(
        backoff.expo,
        AmplitudeRecoverableException,
        max_tries=MAX_ATTEMPTS,
    )
    def delete_user(self, user):
        """
        This function send an API request to delete user from Amplitude. It then parse the response and
        try again if it is recoverable.

        Returns:
            None

        Args:
            user (dict): raw data of user to delete.

        Raises:
          AmplitudeException: if the error from amplitude is unrecoverable/unretryable.
          AmplitudeRecoverableException: if the error from amplitude is recoverable/retryable.
        """
        response = requests.post(
            self.base_url + self.delete_user_path,
            headers={"Content-Type": "application/json"},
            json={
                "user_ids": [user["user"]["id"]],
                'ignore_invalid_id': 'true',  # When true, the job ignores users that don't exist in the project.
                "requester": "user-retirement-pipeline",
            },
            auth=self.auth()
        )

        if response.status_code == 200:
            logger.info("Amplitude user deletion succeeded")
            return

        # We have some sort of error. Parse it, log it, and retry as needed.
        error_msg = "Amplitude user deletion failed due to {reason}".format(reason=response.reason)
        logger.error(error_msg)
        # Status 429 is returned when there are too many requests and can be resolved in retrying sending
        # request.
        if response.status_code == 429 or 500 <= response.status_code < 600:
            raise AmplitudeRecoverableException(error_msg)
        else:
            raise AmplitudeException(error_msg)
