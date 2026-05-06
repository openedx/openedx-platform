"""
Permissions classes for User-API aware views.
"""


from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import permissions

from openedx.core.djangoapps.user_api.accounts.api import visible_fields


def is_field_shared_factory(field_name):
    """
    Generates a permission class that grants access if a particular profile field is
    shared with the requesting user.
    """

    class IsFieldShared(permissions.BasePermission):
        """
        Grants access if a particular profile field is shared with the requesting user.
        """
        def has_permission(self, request, view):
            url_username = request.parser_context.get('kwargs', {}).get('username', '')
            if request.user.username.lower() == url_username.lower():
                return True
            # Staff can always see profiles.
            if request.user.is_staff:
                return True
            # This should never return Multiple, as we don't allow case name collisions on registration.
            user = get_object_or_404(User, username__iexact=url_username)
            if field_name in visible_fields(user.profile, user):
                return True
            raise Http404()

    return IsFieldShared
