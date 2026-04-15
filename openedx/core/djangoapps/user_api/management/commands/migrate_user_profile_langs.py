"""
Migrates user preferences from one language code to another in batches. Dark lang preferences are not affected.
"""


import logging
from time import sleep

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max, Q

from openedx.core.djangoapps.dark_lang.models import DarkLangConfig
from openedx.core.djangoapps.user_api.models import UserPreference

DEFAULT_CHUNK_SIZE = 10000
DEFAULT_SLEEP_TIME_SECS = 10

LOGGER = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Implementation of the migrate command
    """
    help = 'Migrate all user language preferences (excluding dark languages) from one language code to another.'

    def add_arguments(self, parser):
        parser.add_argument('old_lang_code',
                            help='Original language code, ex. "zh-cn"')
        parser.add_argument('new_lang_code',
                            help='New language code, ex. "zh-hans"')
        parser.add_argument('--start_id',
                            type=int,
                            default=1,
                            help='ID to begin from, in case a run needs to be restarted from the middle.')
        parser.add_argument('--chunk_size',
                            type=int,
                            default=DEFAULT_CHUNK_SIZE,
                            help='Number of users whose preferences will be updated per batch.')
        parser.add_argument('--sleep_time_secs',
                            type=int,
                            default=DEFAULT_SLEEP_TIME_SECS,
                            help='Number of seconds to sleep between batches.')

    def handle(self, *args, **options):
        """
        Execute the command.
        """
        old_lang_code = options['old_lang_code']
        new_lang_code = options['new_lang_code']
        chunk_size = options['chunk_size']
        sleep_time_secs = options['sleep_time_secs']
        start = options['start_id']
        end = start + chunk_size

        # Make sure we're changing to a code that actually exists. Presumably it's safe to move away from a code that
        # doesn't.
        dark_lang_config = DarkLangConfig.current()
        langs = [lang_code[0] for lang_code in settings.LANGUAGES]
        langs += dark_lang_config.released_languages_list
        langs += dark_lang_config.beta_languages_list if dark_lang_config.enable_beta_languages else []

        if new_lang_code not in langs:
            raise CommandError('{} is not a configured language code in settings.LANGUAGES '  # noqa: UP032
                               'or the current DarkLangConfig.'.format(new_lang_code))

        max_id = UserPreference.objects.all().aggregate(Max('id'))['id__max']

        print('Updating user language preferences from {} to {}. '  # noqa: UP032
              'Start id is {}, current max id is {}. '
              'Chunk size is of {}'.format(old_lang_code, new_lang_code, start, max_id, chunk_size))

        updated_count = 0

        while True:
            # On the last time through catch any new rows added since this run began
            if end >= max_id:
                print('Last round, includes all new rows added since this run started.')
                id_query = Q(id__gte=start)
            else:
                id_query = Q(id__gte=start) & Q(id__lt=end)

            curr = UserPreference.objects.filter(
                id_query,
                key='pref-lang',
                value=old_lang_code
            ).update(value=new_lang_code)

            updated_count += curr

            print(f'Updated rows {start} to {end - 1}, {curr} rows affected')

            if end >= max_id:
                break

            start = end
            end += chunk_size
            sleep(sleep_time_secs)

        print('Finished! Updated {} total preferences from {} to {}'.format(  # noqa: UP032
            updated_count,
            old_lang_code,
            new_lang_code
        ))
