"""
An API for retiring user accounts.
"""
import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from openedx.core.djangoapps.user_api.accounts.permissions import CanRetireUser
from openedx.core.djangoapps.user_api.accounts.utils import create_retirement_request_and_deactivate_account

log = logging.getLogger(__name__)


class BulkUsersRetirementView(APIView):
    """
    **Use Case**

        Implementation for Bulk User Retirement API. Creates a retirement request
        for one or more users.

    **Example Request**

        POST /v1/accounts/bulk_retire_users {
            "usernames": "test_user1, test_user2"
        }

        **POST Parameters**

          A POST request can include the following parameter.

          * usernames: Comma separated strings of usernames that should be retired.
    """
    permission_classes = (permissions.IsAuthenticated, CanRetireUser)

    def post(self, request, **kwargs):  # pylint: disable=unused-argument
        """
        Initiates the bulk retirement process for the given users.
        """
        request_usernames = request.data.get('usernames')

        if request_usernames:
            usernames_to_retire = [each_username.strip() for each_username in request_usernames.split(',')]
        else:
            usernames_to_retire = []

        User = get_user_model()

        successful_user_retirements, failed_user_retirements = [], []

        for username in usernames_to_retire:
            try:
                user_to_retire = User.objects.get(username=username)
                with transaction.atomic():
                    create_retirement_request_and_deactivate_account(user_to_retire)
                log.info(f'The user "{username}" has been added to the retirement pipeline \
                         by "{request.user}"')

            except User.DoesNotExist:
                log.exception(f'The user "{username}" does not exist.')
                failed_user_retirements.append(username)

            except Exception as exc:  # pylint: disable=broad-except
                log.exception(f'500 error retiring account {exc}')
                failed_user_retirements.append(username)

        successful_user_retirements = sorted(set(usernames_to_retire).difference(failed_user_retirements))

        return Response(
            status=status.HTTP_200_OK,
            data={
                "successful_user_retirements": successful_user_retirements,
                "failed_user_retirements": failed_user_retirements
            }
        )
