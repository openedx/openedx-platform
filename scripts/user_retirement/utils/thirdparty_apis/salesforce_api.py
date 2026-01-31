"""
Salesforce API class that will call the Salesforce REST API using simple-salesforce.
"""
import logging
import os

import backoff
from requests.exceptions import ConnectionError as RequestsConnectionError
from simple_salesforce import Salesforce, format_soql

LOG = logging.getLogger(__name__)

MAX_ATTEMPTS = int(os.environ.get('RETRY_SALESFORCE_MAX_ATTEMPTS', 5))
RETIREMENT_TASK_DESCRIPTION = (
    "A user data retirement request has been made for "
    "{email} who has been identified as a lead in Salesforce. "
    "Please manually retire the user data for this lead."
)


class SalesforceApi:
    """
    Class for making Salesforce API calls
    """

    def __init__(self, username, password, security_token, domain, assignee_username):
        """
        Create API with credentials
        """
        self._sf = self._get_salesforce_client(
            username=username,
            password=password,
            security_token=security_token,
            domain=domain
        )
        self.assignee_id = self.get_user_id(assignee_username)
        if not self.assignee_id:
            raise Exception("Could not find Salesforce user with username " + assignee_username)

    @staticmethod
    def get_instance(config):
        """
        This function is used to get instance of SalesforceApi.

        Returns:
            SalesforceApi: Returns instance of SalesforceApi.

        Args:
            config (dict): Configuration dictionary.

        Raises:
            KeyError: If salesforce_username, salesforce_password, salesforce_security_token, salesforce_domain, or salesforce_assignee is not present in config.
        """
        salesforce_username = config.get('salesforce_username', None)
        salesforce_password = config.get('salesforce_password', None)
        salesforce_security_token = config.get('salesforce_security_token', None)
        salesforce_domain = config.get('salesforce_domain', None)
        salesforce_assignee = config.get('salesforce_assignee', None)

        if not salesforce_username or not salesforce_password or not salesforce_security_token or not salesforce_domain or not salesforce_assignee:
            raise KeyError("salesforce_username, salesforce_password, salesforce_security_token, salesforce_domain, or salesforce_assignee is not present in config.")

        return SalesforceApi(salesforce_username, salesforce_password, salesforce_security_token, salesforce_domain, salesforce_assignee)

    @backoff.on_exception(
        backoff.expo,
        RequestsConnectionError,
        max_tries=MAX_ATTEMPTS
    )
    def _get_salesforce_client(self, username, password, security_token, domain):
        """
        Returns a constructed Salesforce client and retries upon failure.
        """
        return Salesforce(
            username=username,
            password=password,
            security_token=security_token,
            domain=domain
        )

    @backoff.on_exception(
        backoff.expo,
        RequestsConnectionError,
        max_tries=MAX_ATTEMPTS
    )
    def get_lead_ids_by_email(self, email):
        """
        Given an id, query for a Lead with that email
        Returns a list of ids tht have that email or None if none are found
        """
        id_query = self._sf.query(format_soql("SELECT Id FROM Lead WHERE Email = {email}", email=email))
        total_size = int(id_query['totalSize'])
        if total_size == 0:
            return None
        else:
            ids = [record['Id'] for record in id_query['records']]
            if len(ids) > 1:
                LOG.warning("Multiple Ids returned for Lead with email {}".format(email))
            return ids

    @backoff.on_exception(
        backoff.expo,
        RequestsConnectionError,
        max_tries=MAX_ATTEMPTS
    )
    def get_user_id(self, username):
        """
        Given a username, returns the user id for the User with that username
        or None if no user is found
        Used to get a the user id of the user we will assign the retirement task to
        """
        id_query = self._sf.query(format_soql("SELECT Id FROM User WHERE Username = {username}", username=username))
        total_size = int(id_query['totalSize'])
        if total_size == 0:
            return None
        else:
            return id_query['records'][0]['Id']

    @backoff.on_exception(
        backoff.expo,
        RequestsConnectionError,
        max_tries=MAX_ATTEMPTS
    )
    def _create_retirement_task(self, email, lead_ids):
        """
        Creates a Salesforce Task instructing a user to manually retire the
        given lead
        """
        task_params = {
            'Description': RETIREMENT_TASK_DESCRIPTION.format(email=email),
            'Subject': "GDPR Request: " + email,
            'WhoId': lead_ids[0],
            'OwnerId': self.assignee_id
        }
        if len(lead_ids) > 1:
            note = "\nNotice: Multiple leads were identified with the same email. Please retire all following leads:"
            for lead_id in lead_ids:
                note += "\n{}".format(lead_id)
            task_params['Description'] += note
        created_task = self._sf.Task.create(task_params)
        if created_task['success']:
            LOG.info("Successfully salesforce task created task %s", created_task['id'])
        else:
            LOG.error("Errors while creating task:")
            for error in created_task['errors']:
                LOG.error(error)
            raise Exception("Unable to create retirement task for email " + email)

    def retire_learner(self, learner):
        """
        Given a learner email, check if that learner exists as a lead in Salesforce
        If they do, create a Salesforce Task instructing someone to manually retire
        their personal information
        """
        email = learner.get('original_email', None)
        if not email:
            raise TypeError('Expected an email address for user to delete, but received None.')
        lead_ids = self.get_lead_ids_by_email(email)
        if lead_ids is None:
            LOG.info("No action taken because no lead was found in Salesforce.")
            return
        self._create_retirement_task(email, lead_ids)
