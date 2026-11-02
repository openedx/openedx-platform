"""
Command to build or re-build the search index for courses (in Studio, i.e. Draft
mode), in Meilisearch.

See also cms/djangoapps/contentstore/management/commands/reindex_course.py which
indexes LMS (published) courses in ElasticSearch.
"""
from django.core.management import BaseCommand, CommandError

from ... import api


class Command(BaseCommand):
    """
    Build or re-build the Meilisearch search index for courses and libraries in Studio.

    This is separate from LMS search features like courseware search or forum search.
    """

    # TODO: improve this - see https://github.com/openedx/edx-platform/issues/36868

    def add_arguments(self, parser):
        parser.add_argument("--experimental", action="store_true")  # kept for compatibility but ignored.
        parser.add_argument("--reset", action="store_true")
        parser.add_argument("--init", action="store_true")
        parser.add_argument("--incremental", action="store_true")
        parser.set_defaults(experimental=False, reset=False, init=False, incremental=False)

    def handle(self, *args, **options):
        """
        Build a new search index for Studio, containing content from courses and libraries
        """
        if not api.is_meilisearch_enabled():
            raise CommandError("Meilisearch is not enabled. Please set MEILISEARCH_ENABLED to True in your settings.")

        if options["reset"]:
            api.reset_index(self.stdout.write)
        elif options["init"]:
            api.init_index(self.stdout.write, self.stderr.write)
        elif options["incremental"]:
            api.rebuild_index(self.stdout.write, incremental=True)
        else:
            api.rebuild_index(self.stdout.write)
