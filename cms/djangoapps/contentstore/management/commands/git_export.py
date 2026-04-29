"""
This command exports a course from CMS to a git repository.
It takes as arguments the course id to export (i.e MITx/999/2020 ) and
the repository to commit too.  It takes username as an option for identifying
the commit, as well as a directory path to place the git repository.

By default it will use settings.GIT_REPO_EXPORT_DIR/repo_name as the cloned
directory.  It is branch aware, but will reset all local changes to the
repository before attempting to export the XML, add, and commit changes if
any have taken place.

This functionality is also available as an export view in studio if the giturl
attribute is set and the FEATURE['ENABLE_EXPORT_GIT'] is set.
"""


import logging

from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import gettext as _
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

import cms.djangoapps.contentstore.git_export_utils as git_export_utils

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Take a course from studio and export it to a git repository.
    """
    help = _('Take the specified course and attempt to '
             'export it to a git repository\n. Course directory '
             'must already be a git repository. Usage: '
             ' git_export <course_loc> <git_url>')

    def add_arguments(self, parser):
        parser.add_argument('course_loc')
        parser.add_argument('git_url')
        parser.add_argument('--username', '-u', dest='user',
                            help='Specify a username from LMS/Studio to be used as the commit author.')
        parser.add_argument('--repo_dir', '-r', dest='repo', help='Specify existing git repo directory.')

    def handle(self, *args, **options):
        """
        Checks arguments and runs export function if they are good
        """
        # Rethrow GitExportError as CommandError for SystemExit
        try:
            course_key = CourseKey.from_string(options['course_loc'])
        except InvalidKeyError:
            raise CommandError(str(git_export_utils.GitExportError.BAD_COURSE))  # pylint: disable=raise-missing-from  # noqa: B904

        try:
            git_export_utils.export_to_git(
                course_key,
                options['git_url'],
                options.get('user', ''),
                options.get('rdir', None)
            )
        except git_export_utils.GitExportError as ex:
            raise CommandError(str(ex))  # pylint: disable=raise-missing-from  # noqa: B904
