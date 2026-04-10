"""
print_setting
=============

Django command to output a single Django setting.
Originally used by "paver" scripts before we removed them.
Still useful when a shell script needs such a value.
Keep in mind that the LMS/CMS startup time is slow, so if you invoke this
Django management multiple times in a command that gets run often, you are
going to be sad.

This handles the one specific use case of the "print_settings" command from
django-extensions that we were actually using.
"""


import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """print_setting command"""

    help = "Print the value of a single Django setting."

    def add_arguments(self, parser):
        parser.add_argument(
            'settings_to_print',
            nargs='+',
            help='Specifies the list of settings to be printed.'
        )

        parser.add_argument(
            '--json',
            action='store_true',
            help='Returns setting as JSON string instead.',
        )

    def handle(self, *args, **options):
        settings_to_print = options.get('settings_to_print')
        dump_as_json = options.get('json')

        for setting in settings_to_print:
            if not hasattr(settings, setting):
                raise CommandError('%s not found in settings.' % setting)  # noqa: UP031

            setting_value = getattr(settings, setting)

            if dump_as_json:
                setting_value = json.dumps(setting_value, sort_keys=True)

            print(setting_value)
