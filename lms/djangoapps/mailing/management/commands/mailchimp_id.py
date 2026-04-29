"""
mailchimp_id: Returns whether or not a given mailchimp key represents
a valid list.
"""


import sys

from django.core.management.base import BaseCommand
from mailsnake import MailSnake


class Command(BaseCommand):
    """
    Given a mailchimp key, validates that a list with that key
    exists in mailchimp.
    """
    help = 'Get the list id from a web_id'

    def add_arguments(self, parser):
        parser.add_argument('--key',
                            required=True,
                            help='mailchimp api key')
        parser.add_argument('--webid',
                            dest='web_id',
                            type=int,
                            required=True,
                            help='mailchimp list web id')

    def handle(self, *args, **options):
        """
        Validates that the id passed in exists in mailchimp.
        """
        key = options['key']
        web_id = options['web_id']

        mailchimp = MailSnake(key)

        lists = mailchimp.lists()['data']
        by_web_id = {mailing_list['web_id']: mailing_list for mailing_list in lists}

        list_with_id = by_web_id.get(web_id, None)

        if list_with_id:
            print("id: {} for web_id: {}".format(list_with_id['id'], web_id))
            print("list name: {}".format(list_with_id['name']))
        else:
            print(f"list with web_id: {web_id} not found.")
            sys.exit(1)
