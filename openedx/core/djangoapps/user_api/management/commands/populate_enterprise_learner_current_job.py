""" Management command for populaing current job of learners in learner profile. """

import logging
from urllib.parse import urljoin

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand
from edx_rest_api_client.client import OAuthAPIClient

from common.djangoapps.student.models import User, UserProfile
from openedx.core.djangoapps.user_api import errors

LOGGER = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Command to populate current job of learners in extended learner profile.

    This command will fetch the current job of learners from course-discovery service
    and populate it in the extended learner profile in edx-platform. This command is
    supposed to be run only once in the system.

    Example usage:
        $ # Update the current job of learners.
        $ ./manage.py lms populate_enterprise_learner_current_job
    """

    help = 'Populates current job of learners in extended learner profile.'

    TOTAL_USERS_COUNT = 0
    TOTAL_USERS_UPDATED = 0

    def _get_edx_api_client(self):
        return OAuthAPIClient(
            base_url=settings.ENTERPRISE_BACKEND_SERVICE_EDX_OAUTH2_PROVIDER_URL,
            client_id=settings.ENTERPRISE_BACKEND_SERVICE_EDX_OAUTH2_KEY,
            client_secret=settings.ENTERPRISE_BACKEND_SERVICE_EDX_OAUTH2_SECRET,
        )

    def _fetch_learner_job_data(self, url=None):
        """
        Get the username and current job of learners from discovery service.

        Returns:
            list: List of dictionaries containing username and current job of learners.
        """
        client = self._get_edx_api_client()

        if not url:
            course_discovery_url = settings.COURSE_CATALOG_URL_ROOT
            learner_jobs_endpoint = '/taxonomy/api/v1/learners-current-job/?page_size=1000'
            url = urljoin(course_discovery_url, learner_jobs_endpoint)

        response = client.get(url)
        response.raise_for_status()
        response = response.json()

        self.TOTAL_USERS_COUNT = response['count']
        return response

    def _get_user_profile(self, username):
        """
        Helper method to return the user profile object based on username.
        """
        try:
            existing_user = User.objects.get(username=username)
        except ObjectDoesNotExist:
            raise errors.UserNotFound()  # lint-amnesty, pylint: disable=raise-missing-from

        existing_user_profile, _ = UserProfile.objects.get_or_create(user=existing_user)
        return existing_user_profile

    def _update_current_job_of_learner(self, username, current_job):
        """
        Update the current job of learner in their extended profile.

        Args:
            user_profile (UserProfile): Extended profile of the learner.
            current_job (number): Current job of the learner.
        """
        try:
            user_profile = self._get_user_profile(username)
            meta = user_profile.get_meta()
            meta['enterprise_learner_current_job'] = current_job
            user_profile.set_meta(meta)
            user_profile.save()

            self.TOTAL_USERS_UPDATED += 1
        except Exception:  # lint-amnesty, pylint: disable=broad-except
            LOGGER.exception('Could not update profile of user %s as %s.', username, current_job)

    def _populate_learner_current_job(self, response):
        """
        Populate the current job of learners in extended learner profile. An example of
        response is as follows:
        [
            {
                "username": "learner1",
                "current_job": 1
            },
            {
                "username": "learner2",
                "current_job": 2
            }
        ]

        Args:
            response (list): List of dictionaries containing username and current job of learners.
        Returns:
            None
        """
        for learner_data in response:
            self._update_current_job_of_learner(
                username=learner_data['username'],
                current_job=learner_data['current_job'],
            )

    def _populate_learner_profiles(self):
        """
        Populate the current job of learners in extended learner profile.
        """
        response = self._fetch_learner_job_data()
        self._populate_learner_current_job(response['results'])
        while response['next']:
            response = self._fetch_learner_job_data(response['next'])
            self._populate_learner_current_job(response['results'])

    def handle(self, *args, **options):
        """
        Handle the command.

        Args:
            *args: Variable length argument list.
            **options: Arbitrary keyword arguments.
        """
        try:
            LOGGER.info('Populating current job of learners in their extended profiles.')
            self._populate_learner_profiles()
            LOGGER.info('Successfully populated current job of %s learner(s) from %s total learners.',
                        self.TOTAL_USERS_UPDATED, self.TOTAL_USERS_COUNT)
        except Exception as err:  # lint-amnesty, pylint: disable=broad-except
            LOGGER.exception('Could not populate current job of learners. %s', err)
