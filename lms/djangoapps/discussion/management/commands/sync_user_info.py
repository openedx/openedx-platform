"""
One-off script to sync all user information to the
discussion service (later info will be synced automatically)
"""


from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.management.base import BaseCommand

import openedx.core.djangoapps.django_comment_common.comment_client as cc


class Command(BaseCommand):
    """
    Management command for adding all users to the discussion service.
    """
    help = 'Sync all user ids, usernames, and emails to the discussion service.'

    def handle(self, *args, **options):
        for user in User.objects.all().iterator():
            cc_user = cc.User.from_django_user(user)
            cc_user.save()
