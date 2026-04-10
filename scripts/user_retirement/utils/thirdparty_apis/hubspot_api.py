"""
Helper API classes for calling Hubspot APIs.
"""
import logging
import os

import backoff
import requests

from scripts.user_retirement.utils.email_utils import send_email

LOG = logging.getLogger(__name__)
MAX_ATTEMPTS = int(os.environ.get('RETRY_HUBSPOT_MAX_ATTEMPTS', 5))

GET_VID_FROM_EMAIL_URL_TEMPLATE = "https://api.hubapi.com/contacts/v1/contact/email/{email}/profile"
DELETE_USER_FROM_VID_TEMPLATE = "https://api.hubapi.com/contacts/v1/contact/vid/{vid}"


class HubspotException(Exception):
    pass


class HubspotAPI:
    """
    Hubspot API client used to make calls to Hubspot
    """

    def __init__(
        self,
        hubspot_api_key,
        aws_region,
        from_address,
        alert_email
    ):
        self.api_key = hubspot_api_key
        self.aws_region = aws_region
        self.from_address = from_address
        self.alert_email = alert_email

    @backoff.on_exception(
        backoff.expo,
        HubspotException,
        max_tries=MAX_ATTEMPTS
    )
    def delete_user(self, learner):
        """
        Delete a learner from hubspot using their email address.
        """
        email = learner.get('original_email', None)
        if not email:
            raise TypeError('Expected an email address for user to delete, but received None.')

        user_vid = self.get_user_vid(email)
        if user_vid:
            self.delete_user_by_vid(user_vid)

    def delete_user_by_vid(self, vid):
        """
        Delete a learner from hubspot using their Hubspot `vid` (unique identifier)
        """
        headers = {
            'content-type': 'application/json',
            'authorization': f'Bearer {self.api_key}'
        }

        req = requests.delete(DELETE_USER_FROM_VID_TEMPLATE.format(
            vid=vid
        ), headers=headers)
        error_msg = ""
        if req.status_code == 200:
            LOG.info("User successfully deleted from Hubspot")
            self.send_marketing_alert(vid)
        elif req.status_code == 401:
            error_msg = "Hubspot user deletion failed due to authorized API call"
        elif req.status_code == 404:
            error_msg = "Hubspot user deletion failed because vid doesn't match user"
        elif req.status_code == 500:
            error_msg = "Hubspot user deletion failed due to server-side (Hubspot) issues"
        else:
            error_msg = "Hubspot user deletion failed due to unknown reasons"

        if error_msg:
            LOG.error(error_msg)
            raise HubspotException(error_msg)

    def get_user_vid(self, email):
        """
        Get a user's `vid` from Hubspot. `vid` is the terminology that hubspot uses for a user ids
        """
        headers = {
            'content-type': 'application/json',
            'authorization': f'Bearer {self.api_key}'
        }

        req = requests.get(GET_VID_FROM_EMAIL_URL_TEMPLATE.format(
            email=email
        ), headers=headers)
        if req.status_code == 200:
            req_data = req.json()
            return req_data.get('vid')
        elif req.status_code == 404:
            LOG.info("No action taken because no user was found in Hubspot.")
            return
        else:
            error_msg = "Error attempted to get user_vid from Hubspot. Error: {}".format(  # noqa: UP032
                req.text
            )
            LOG.error(error_msg)
            raise HubspotException(error_msg)

    def send_marketing_alert(self, vid):
        """
        Notify marketing with user's Hubspot `vid` upon successful deletion.
        """
        subject = "Alert: Hubspot Deletion"
        body = "Learner with the VID \"{}\" has been deleted from Hubspot.".format(vid)  # noqa: UP032
        send_email(
            self.aws_region,
            self.from_address,
            [self.alert_email],
            subject,
            body
        )
