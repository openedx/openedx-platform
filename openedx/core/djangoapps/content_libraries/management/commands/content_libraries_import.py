"""
Command to import modulestore content into Content Libraries.
"""

import argparse
import logging

from django.conf import settings
from django.core.management import BaseCommand, CommandError

from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import LibraryLocatorV2
from openedx.core.djangoapps.content_libraries import api as contentlib_api


log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Import modulestore content references from a course into a Content Libraries
    library.
    """

    def add_arguments(self, parser):
        """
        Add arguments to the argument parser.
        """
        parser.add_argument(
            'library-key',
            type=LibraryLocatorV2.from_string,
            help=('Usage key of the Content Library to import content into.'),
        )
        parser.add_argument(
            'course-key',
            type=CourseKey.from_string,
            help=('The Course Key string, used to identify the course to import '
                  'content from.'),
        )
        subparser = parser.add_subparsers(
            title='Courseware location and methods',
            dest='method',
            description=('Select the method and location to locate the course and '
                         'its contents.')
        )
        api_parser = subparser.add_parser(
            'api',

            help=('Query and retrieve course blocks from a remote instance using '
                  'Open edX course and OLX export APIs.  You need to enable API access '
                  'on the instance.')
        )
        api_parser.add_argument(
            '--lms-url',
            default=settings.LMS_ROOT_URL,
            help=("The LMS URL, used to retrieve course content (default: "
                  "'%(default)s')."),
        )
        api_parser.add_argument(
            '--studio-url',
            default=f"https://{settings.CMS_BASE}",
            help=("The Studio URL, used to retrieve block OLX content (default: "
                  "'%(default)s')"),
        )
        oauth_group = api_parser.add_mutually_exclusive_group(required=False)
        oauth_group.add_argument(
            '--oauth-creds-file',
            type=argparse.FileType('r'),
            help=('The edX OAuth credentials in a filename.  The first line is '
                  'the OAuth key, second line is the OAuth secret.  This is '
                  'preferred compared to passing the credentials in the command '
                  'line.'),
        )
        oauth_group.add_argument(
            '--oauth-creds',
            nargs=2,
            help=('The edX OAuth credentials in the command line.  The first '
                  'argument is the OAuth secret, the second argument is the '
                  'OAuth key. Notice that command line arguments are insecure, '
                  'see `--oauth-creds-file`.'),
        )
        subparser.add_parser(
            'modulestore',
            help=("Use a local modulestore instance to retrieve blocks database on "
                  "the instance where the command is being run.  You don't need "
                  "to enable API access.")
        )

    def handle(self, *args, **options):
        """
        Collect all blocks from a course that are "importable" and write them to the
        a openedx_content library.
        """

        # Search for the library.

        try:
            contentlib_api.get_library(options['library-key'])
        except contentlib_api.ContentLibraryNotFound as exc:
            raise CommandError("The library specified does not exist: "
                               f"{options['library-key']}") from exc

        # Validate the method and its arguments, instantiate the openedx client.

        if options['method'] == 'api':
            if options['oauth_creds_file']:
                with options['oauth_creds_file'] as creds_f:
                    oauth_key, oauth_secret = [v.strip() for v in creds_f.readlines()]
            elif options['oauth_creds']:
                oauth_key, oauth_secret = options['oauth_creds']
            else:
                raise CommandError("Method 'api' requires one of the "
                                   "--oauth-* options, and none was specified.")
            edx_client = contentlib_api.EdxApiImportClient(
                options['lms_url'],
                options['studio_url'],
                oauth_key,
                oauth_secret,
                library_key=options['library-key'],
            )
        elif options['method'] == 'modulestore':
            edx_client = contentlib_api.EdxModulestoreImportClient(
                library_key=options['library-key'],
            )
        else:
            raise CommandError(f"Method not supported: {options['method']}")

        failed_blocks = []

        def on_progress(block_key, block_num, block_count, exception=None):
            self.stdout.write(f"{block_num}/{block_count}: {block_key}: ", ending='')
            # In case stdout is a term and line buffered:
            self.stdout.flush()
            if exception:
                self.stdout.write(self.style.ERROR('❌'))
                log.error('Failed to import block: %s', block_key, exc_info=exception)
                failed_blocks.append(block_key)
            else:
                self.stdout.write(self.style.SUCCESS('✓'))

        edx_client.import_blocks_from_course(options['course-key'], on_progress)

        if failed_blocks:
            self.stdout.write(self.style.ERROR(f"❌ {len(failed_blocks)} failed:"))
            for key in failed_blocks:
                self.stdout.write(self.style.ERROR(str(key)))
