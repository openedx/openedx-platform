"""
Mixins for JWT auth tests.
"""

from time import time

import jwt
from django.conf import settings

JWT_AUTH = 'JWT_AUTH'


class JwtMixin:
    """ Mixin with JWT-related helper functions. """

    JWT_SECRET_KEY = getattr(settings, JWT_AUTH)['JWT_SECRET_KEY'] if hasattr(settings, JWT_AUTH) else ''
    JWT_ISSUER = getattr(settings, JWT_AUTH)['JWT_ISSUER'] if hasattr(settings, JWT_AUTH) else ''
    JWT_AUDIENCE = getattr(settings, JWT_AUTH)['JWT_AUDIENCE'] if hasattr(settings, JWT_AUTH) else ''

    def generate_token(self, payload, secret=None):
        """ Generate a JWT token with the provided payload."""
        secret = secret or self.JWT_SECRET_KEY
        token = jwt.encode(payload, secret)
        return token

    def generate_id_token(self, user, ttl=1, **overrides):
        """ Generate a JWT id_token that looks like the ones currently
        returned by the edx oidc provider.
        """
        payload = self.default_payload(user=user, ttl=ttl)
        payload.update(overrides)
        return self.generate_token(payload)

    def default_payload(self, user, ttl=1):
        """ Generate a bare payload, in case tests need to manipulate
        it directly before encoding.
        """
        now = int(time())

        return {
            "iss": self.JWT_ISSUER,
            "aud": self.JWT_AUDIENCE,
            "nonce": "dummy-nonce",
            "exp": now + ttl,
            "iat": now,
            "username": user.username,
            "email": user.email,
        }
