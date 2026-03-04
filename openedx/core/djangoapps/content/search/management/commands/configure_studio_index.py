"""
Command to incrementially index content in the search index for courses (in Studio, i.e. Draft
mode), in Meilisearch.

See also ./reindex_studio.py

See also cms/djangoapps/contentstore/management/commands/reindex_course.py which
indexes LMS (published) courses in ElasticSearch.
"""

from django.core.management import BaseCommand, CommandError

from cms.djangoapps.contentstore.management.commands.prompt import query_yes_no
from ... import api


class Command(BaseCommand):
    """
    Build or re-build the Meilisearch search index for courses and libraries in Studio.

    This is separate from LMS search features like courseware search or forum search.
    """

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help=(
                "Reset the index to a new clean state. "
                "Warning: this deletes everything from the index."
            ),
            default=False,
        )

    def handle(self, *args, **options):
        """
        Configure the index
        """
        if not api.is_meilisearch_enabled():
            raise CommandError(
                "Meilisearch is not enabled. Please set MEILISEARCH_ENABLED to True in your settings."
            )

        if options["reset"]:
            api.reset_index(self.stdout.write)
        else:
            api.init_index(self.stdout.write, self.stderr.write)
