"""
This module provides a custom DRF Permission class for supporting the course certificates
to Admin users and users whom they belongs to.
"""

from django.contrib.auth import get_user_model  # noqa: I001
from rest_framework.permissions import BasePermission

from openedx.core.djangoapps.user_api.models import UserPreference

User = get_user_model()


class IsOwnerOrPublicCertificates(BasePermission):
    """
    Method that will ensure whether the requesting user is staff or
    the user whom the certificate belongs to
    """
    def has_permission(self, request, view):
        requested_profile_username = view.kwargs.get('username')
        # check whether requesting user is the owner of certs or not
        if request.user.username == requested_profile_username:
            return True

        user = User.objects.get(username=requested_profile_username)
        cert_privacy = UserPreference.get_value(user, 'visibility.course_certificates')

        return cert_privacy == 'all_users'
