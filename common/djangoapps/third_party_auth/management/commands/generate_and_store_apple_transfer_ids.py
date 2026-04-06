"""
Management command to generate Transfer Identifiers for users who signed in with Apple.
These transfer identifiers are used in the event of migrating an app from one team to another.
"""


import logging
import time

import jwt
import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from social_django.models import UserSocialAuth
from social_django.utils import load_strategy

from common.djangoapps.third_party_auth.appleid import AppleIdAuth
from common.djangoapps.third_party_auth.models import AppleMigrationUserIdInfo

log = logging.getLogger(__name__)

INVALID_GRANT_ERROR = "invalid_grant"
INVALID_CLIENT_ERROR = "invalid_client"


class AccessTokenExpiredException(Exception):
    """
    Raised when access token has been expired.
    """


class BadRequestException(Exception):
    """
    Raised when access token has been expired.
    """


class Command(BaseCommand):
    """
    Management command to generate transfer identifiers for apple users using their apple_id
    stored in social_django.models.UserSocialAuth.uid.

    Usage:
        manage.py generate_and_store_apple_transfer_ids <target_team_id>
    """

    def _generate_client_secret(self):
        """
        Generate client secret for use in Apple API's
        """
        now = int(time.time())
        expiry = 60 * 60 * 12  # 12 hours

        backend = load_strategy().get_backend(AppleIdAuth.name)
        team_id = backend.setting('TEAM')
        key_id = backend.setting('KEY')
        private_key = backend.get_private_key()
        audience = backend.TOKEN_AUDIENCE

        headers = {
            "alg": "ES256",
            'kid': key_id
        }
        payload = {
            'iss': team_id,
            'iat': now,
            'exp': now + expiry,
            'aud': audience,
            'sub': "org.edx.mobile",
        }

        return jwt.encode(payload, key=private_key, algorithm='ES256',
                          headers=headers)

    def _generate_access_token(self, client_secret):
        """
        Generate access token for use in Apple API's
        """
        access_token_url = 'https://appleid.apple.com/auth/token'
        app_id = "org.edx.mobile"
        payload = {
            "grant_type": "client_credentials",
            "scope": "user.migration",
            "client_id": app_id,
            "client_secret": client_secret
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Host": "appleid.apple.com"
        }
        response = requests.post(access_token_url, data=payload, headers=headers)
        access_token = response.json().get('access_token')
        return access_token

    def _get_token_and_secret(self):
        """
        Get access_token and client_secret
        """
        client_secret = self._generate_client_secret()
        access_token = self._generate_access_token(client_secret)
        return access_token, client_secret

    def _update_token_and_secret(self):
        self.access_token, self.client_secret = self._get_token_and_secret()  # pylint: disable=W0201

    def _fetch_transfer_id(self, apple_id, target_team_id):
        """
        Fetch Transfer ID for a given Apple ID from Apple API.
        """
        migration_url = "https://appleid.apple.com/auth/usermigrationinfo"
        app_id = "org.edx.mobile"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Host": "appleid.apple.com",
            "Authorization": "Bearer " + self.access_token
        }
        payload = {
            "target": target_team_id,
            "client_id": app_id,
            "client_secret": self.client_secret,
            "sub": apple_id
        }
        response = requests.post(migration_url, data=payload, headers=headers)
        if response.status_code == 400:
            error = response.json().get('error')
            if error in [INVALID_GRANT_ERROR, INVALID_CLIENT_ERROR]:
                log.info("Error while fetching transfer_id for uid %s. Error: %s", apple_id, error)
                raise AccessTokenExpiredException

            log.info("Request error while fetching transfer_id for uid %s. Error: %s "
                     "\nPayload: %s \nHeaders: %s", apple_id, error, str(payload), str(headers))
            raise BadRequestException

        return response.json().get('transfer_sub')

    def _get_transfer_id_for_apple_id(self, apple_id, target_team_id):
        """
        Given an Apple ID from the old transferring team,
        create and return its respective transfer id.
        Update access token if expired.
        Skip the current apple_id in case of a malformed
        request error and log info.
        """
        try:
            transfer_id = self._fetch_transfer_id(apple_id, target_team_id)
        except AccessTokenExpiredException:
            log.info('Access token expired. Re-creating access token.')
            self._update_token_and_secret()
            transfer_id = self._fetch_transfer_id(apple_id, target_team_id)
        except BadRequestException:
            log.info('Bad request for uid %s.', apple_id)
            transfer_id = ''
        except (requests.exceptions.JSONDecodeError, AttributeError):
            log.info('JSONDecodeError/AttributeError for uid %s.', apple_id)
            transfer_id = ''

        return transfer_id

    def add_arguments(self, parser):
        parser.add_argument('target_team_id', help='Team ID to which the app is to be migrated to.')

    def handle(self, *args, **options):
        target_team_id = options['target_team_id']

        self._update_token_and_secret()
        if not self.access_token:
            raise CommandError('Failed to create access token.')

        already_processed_apple_ids = AppleMigrationUserIdInfo.objects.all().exclude(
            Q(transfer_id__isnull=True) | Q(transfer_id="")).values_list('old_apple_id', flat=True)
        apple_ids = UserSocialAuth.objects.filter(provider=AppleIdAuth.name).exclude(
            uid__in=already_processed_apple_ids).values_list('uid', flat=True)
        for apple_id in apple_ids:
            log.info("Begin processing uid %s", apple_id)
            transfer_id = self._get_transfer_id_for_apple_id(apple_id, target_team_id)
            if transfer_id:
                with transaction.atomic():
                    apple_user_id_info, _ = AppleMigrationUserIdInfo.objects.get_or_create(old_apple_id=apple_id)
                    apple_user_id_info.transfer_id = transfer_id
                    apple_user_id_info.save()
                    log.info('Updated transfer_id for uid %s', apple_id)
            else:
                log.info('Unable to fetch transfer_id for uid %s. Skipping.', apple_id)
