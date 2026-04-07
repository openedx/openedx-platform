"""
Content Library LTI authentication.

This module offers an authentication backend to support LTI launches within
content libraries.
"""


import logging

from django.contrib.auth.backends import ModelBackend

from .models import LtiProfile

log = logging.getLogger(__name__)


class LtiAuthenticationBackend(ModelBackend):
    """
    Authenticate based on content library LTI profile.

    The backend assumes the profile was previously created and its presence is
    enough to assume the launch claims are valid.
    """

    # pylint: disable=arguments-differ
    def authenticate(self, request, iss=None, aud=None, sub=None, **kwargs):
        """
        Authenticate if the user in the request has an LTI profile.
        """
        log.info('LTI 1.3 authentication: iss=%s, sub=%s', iss, sub)
        try:
            lti_profile = LtiProfile.objects.get_from_claims(
                iss=iss, aud=aud, sub=sub)
        except LtiProfile.DoesNotExist:
            return None
        user = lti_profile.user
        log.info('LTI 1.3 authentication profile: profile=%s user=%s',
                 lti_profile, user)
        if user and self.user_can_authenticate(user):
            return user
        return None
