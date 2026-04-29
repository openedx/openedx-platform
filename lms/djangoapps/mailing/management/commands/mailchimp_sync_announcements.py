"""
Synchronizes the announcement list with all active students.
"""


import logging

from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.management.base import BaseCommand

from .mailchimp_sync_course import connect_mailchimp, get_cleaned, get_subscribed, get_unsubscribed, subscribe_with_data

log = logging.getLogger('edx.mailchimp')


class Command(BaseCommand):
    """
    Synchronizes the announcement list with all active students.
    """
    help = 'Synchronizes the announcement list with all active students.'

    def add_arguments(self, parser):
        parser.add_argument('--key',
                            required=True,
                            help='mailchimp api key')
        parser.add_argument('--list',
                            dest='list_id',
                            required=True,
                            help='mailchimp list id')

    def handle(self, *args, **options):
        key = options['key']
        list_id = options['list_id']

        log.info('Syncronizing announcement mailing list')

        mailchimp = connect_mailchimp(key)

        subscribed = get_subscribed(mailchimp, list_id)
        unsubscribed = get_unsubscribed(mailchimp, list_id)
        cleaned = get_cleaned(mailchimp, list_id)
        non_subscribed = unsubscribed.union(cleaned)

        enrolled = get_enrolled()
        exclude = subscribed.union(non_subscribed)
        to_subscribe = get_data(enrolled, exclude=exclude)

        subscribe_with_data(mailchimp, list_id, to_subscribe)


def get_enrolled():
    """
    Filter out all users who signed up via a Microsite, which UserSignupSource tracks
    """
    ## TODO (Feanil) This grabs all inactive students and MUST be changed (or, could exclude inactive users in get_data)
    return User.objects.raw('SELECT * FROM auth_user where id not in (SELECT user_id from student_usersignupsource)')


def get_data(users, exclude=None):
    """
    users: set of Django users
    exclude [optional]: set of Django users to exclude

    returns: {'EMAIL': u.email} for all users in users less those in `exclude`
    """
    exclude = exclude if exclude else set()
    emails = (u.email for u in users)
    return ({'EMAIL': e} for e in emails if e not in exclude)
