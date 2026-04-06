# Vendored from version 3.4.0 (9d93069564a60495e0ebd697b33e16fcff14195b)
# of social-core:
# https://github.com/python-social-auth/social-core/blob/3.4.0/social_core/backends/apple.py
#
# Additional changes:
#
# - Patch for JWT algorithms specification: eed3007c4ccdbe959b1a3ac83102fe869d261948
#
# v3.4.0 is unreleased at this time (2020-07-28) and contains several necessary
# bugfixes over 3.3.3 for AppleID, but also causes the
# TestShibIntegrationTest.test_full_pipeline_succeeds_for_unlinking_testshib_account
# test in common/djangoapps/third_party_auth/tests/specs/test_testshib.py to break
# (somehow related to social-core's change 561642bf which makes a bugfix to partial
# pipeline cleaning).
#
# Since we're not maintaining this file and want a relatively clean diff:
# pylint: skip-file
#
#
# social-core, and therefore this code, is under a BSD license:
#
#
# Copyright (c) 2012-2016, Matías Aguirre
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#     1. Redistributions of source code must retain the above copyright notice,
#        this list of conditions and the following disclaimer.
#
#     2. Redistributions in binary form must reproduce the above copyright
#        notice, this list of conditions and the following disclaimer in the
#        documentation and/or other materials provided with the distribution.
#
#     3. Neither the name of this project nor the names of its contributors may be
#        used to endorse or promote products derived from this software without
#        specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
# ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
# ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""
Sign In With Apple authentication backend.

Docs:
    * https://developer.apple.com/documentation/signinwithapplerestapi
    * https://developer.apple.com/documentation/signinwithapplerestapi/tokenresponse

Settings:
    * `TEAM` - your team id;
    * `KEY` - your key id;
    * `CLIENT` - your client id;
    * `AUDIENCE` - a list of authorized client IDs, defaults to [CLIENT].
                   Use this if you need to accept both service and bundle id to
                   be able to login both via iOS and ie a web form.
    * `SECRET` - your secret key;
    * `SCOPE` (optional) - e.g. `['name', 'email']`;
    * `EMAIL_AS_USERNAME` - use apple email is username is set, use apple id
                            otherwise.
    * `AppleIdAuth.TOKEN_TTL_SEC` - time before JWT token expiration, seconds.
    * `SOCIAL_AUTH_APPLE_ID_INACTIVE_USER_LOGIN` - allow inactive users email to
                                                   login
"""

import json
import time

import jwt
import social_django
from django.apps import apps
from jwt.algorithms import RSAAlgorithm
from jwt.exceptions import PyJWTError
from social_core.backends.oauth import BaseOAuth2
from social_core.exceptions import AuthFailed

from common.djangoapps.third_party_auth.toggles import is_apple_user_migration_enabled


class AppleIdAuth(BaseOAuth2):
    name = 'apple-id'

    JWK_URL = 'https://appleid.apple.com/auth/keys'
    AUTHORIZATION_URL = 'https://appleid.apple.com/auth/authorize'
    ACCESS_TOKEN_URL = 'https://appleid.apple.com/auth/token'
    ACCESS_TOKEN_METHOD = 'POST'
    RESPONSE_MODE = None

    ID_KEY = 'sub'
    TOKEN_KEY = 'id_token'
    STATE_PARAMETER = True
    REDIRECT_STATE = False

    TOKEN_AUDIENCE = 'https://appleid.apple.com'
    TOKEN_TTL_SEC = 6 * 30 * 24 * 60 * 60

    def get_audience(self):
        client_id = self.setting('CLIENT')
        return self.setting('AUDIENCE', default=[client_id])

    def auth_params(self, *args, **kwargs):
        """
        Apple requires to set `response_mode` to `form_post` if `scope`
        parameter is passed.
        """
        params = super().auth_params(*args, **kwargs)
        if self.RESPONSE_MODE:
            params['response_mode'] = self.RESPONSE_MODE
        elif self.get_scope():
            params['response_mode'] = 'form_post'
        return params

    def get_private_key(self):
        """
        Return contents of the private key file. Override this method to provide
        secret key from another source if needed.
        """
        return self.setting('SECRET')

    def generate_client_secret(self):
        now = int(time.time())
        client_id = self.setting('CLIENT')
        team_id = self.setting('TEAM')
        key_id = self.setting('KEY')
        private_key = self.get_private_key()

        headers = {'kid': key_id}
        payload = {
            'iss': team_id,
            'iat': now,
            'exp': now + self.TOKEN_TTL_SEC,
            'aud': self.TOKEN_AUDIENCE,
            'sub': client_id,
        }

        return jwt.encode(payload, key=private_key, algorithm='ES256',
                          headers=headers)

    def get_key_and_secret(self):
        client_id = self.setting('CLIENT')
        client_secret = self.generate_client_secret()
        return client_id, client_secret

    def get_apple_jwk(self, kid=None):
        """
        Return requested Apple public key or all available.
        """
        keys = self.get_json(url=self.JWK_URL).get('keys')

        if not isinstance(keys, list) or not keys:
            raise AuthFailed(self, 'Invalid jwk response')

        if kid:
            return json.dumps([key for key in keys if key['kid'] == kid][0])
        else:
            return (json.dumps(key) for key in keys)

    def decode_id_token(self, id_token):
        """
        Decode and validate JWT token from apple and return payload including
        user data.
        """
        if not id_token:
            raise AuthFailed(self, 'Missing id_token parameter')

        try:
            kid = jwt.get_unverified_header(id_token).get('kid')
            public_key = RSAAlgorithm.from_jwk(self.get_apple_jwk(kid))
            decoded = jwt.decode(
                id_token,
                key=public_key,
                audience=self.get_audience(),
                algorithms=['RS256'],
            )
        except PyJWTError:
            raise AuthFailed(self, 'Token validation failed')

        return decoded

    def get_user_details(self, response):
        name = response.get('name') or {}
        fullname, first_name, last_name = self.get_user_names(
            fullname='',
            first_name=name.get('firstName', ''),
            last_name=name.get('lastName', '')
        )

        email = response.get('email', '')
        apple_id = response.get(self.ID_KEY, '')
        # prevent updating User with empty strings
        user_details = {
            'first_name': first_name or None,
            'last_name': last_name or None,
            'email': email,
        }
        if email and self.setting('EMAIL_AS_USERNAME'):
            user_details['username'] = email
        if apple_id and not self.setting('EMAIL_AS_USERNAME'):
            user_details['username'] = apple_id

        return user_details

    def get_user_id(self, details, response):
        """
        If Apple team has been migrated, return the correct team_scoped apple_id that matches
        existing UserSocialAuth instance. Else return apple_id as received in response.
        """
        apple_id = super().get_user_id(details, response)

        if is_apple_user_migration_enabled():
            if social_django.models.DjangoStorage.user.get_social_auth(provider=self.name, uid=apple_id):
                return apple_id

            transfer_sub = response.get('transfer_sub')
            if transfer_sub:
                # Apple will send a transfer_sub till 60 days after the Apple Team has been migrated.
                # If the team has been migrated and UserSocialAuth entries have not yet been updated
                # with the new team-scoped apple-ids', use the transfer_sub to match to old apple ids'
                # belonging to already signed-in users.
                AppleMigrationUserIdInfo = apps.get_model('third_party_auth', 'AppleMigrationUserIdInfo')
                user_apple_id_info = AppleMigrationUserIdInfo.objects.filter(transfer_id=transfer_sub).first()
                if user_apple_id_info:
                    old_apple_id = user_apple_id_info.old_apple_id
                    if social_django.models.DjangoStorage.user.get_social_auth(provider=self.name, uid=old_apple_id):
                        user_apple_id_info.new_apple_id = response.get(self.ID_KEY)
                        user_apple_id_info.save()
                        return user_apple_id_info.old_apple_id

        return apple_id

    def do_auth(self, access_token, *args, **kwargs):
        response = kwargs.pop('response', None) or {}
        jwt_string = response.get(self.TOKEN_KEY) or access_token

        if not jwt_string:
            raise AuthFailed(self, 'Missing id_token parameter')

        decoded_data = self.decode_id_token(jwt_string)
        return super().do_auth(
            access_token,
            response=decoded_data,
            *args,
            **kwargs
        )
