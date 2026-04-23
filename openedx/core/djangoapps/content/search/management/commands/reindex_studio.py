"""
Command to queue incremental population of the Studio Meilisearch search index.

Index creation, configuration, and schema reconciliation are handled
automatically via the post_migrate signal. This command is solely
responsible for enqueuing the population task in Celery.

See also cms/djangoapps/contentstore/management/commands/reindex_course.py which
indexes LMS (published) courses in ElasticSearch.
"""

import logging

from django.conf import settings
from django.core.management import BaseCommand, CommandError

from ... import api
from ...tasks import rebuild_index_incremental

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Add all course and library content to the Studio search index.

    This enqueues a Celery task that incrementally indexes all courses and
    libraries. Progress is tracked via IncrementalIndexCompleted, so the task
    can safely resume if interrupted.

    Index creation and configuration are handled by post_migrate reconciliation
    (runs automatically on ./manage.py cms migrate).

    If it's ever necessary to reset the incremental indexing state (force
    the full re-index process to start from the beginning), use:

    ./manage.py cms shell -c 'IncrementalIndexCompleted.objects.all().delete()'

    This will delete all the IncrementalIndexCompleted records and will help in restarting the index population.
    """

    help = "Add all course and library content to the Studio search index."

    def add_arguments(self, parser):
        # Removed flags — provide clear error messages for operators with old automation.
        parser.add_argument(
            "--experimental",
            action="store_true",
            default=False,
            help="(Removed) reindex_studio is no longer experimental.",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            default=False,
            help="(Removed) Index reset is now handled by post_migrate reconciliation.",
        )
        parser.add_argument(
            "--init",
            action="store_true",
            default=False,
            help="(Removed) Index initialization is now handled by post_migrate reconciliation.",
        )
        parser.add_argument(
            "--incremental",
            action="store_true",
            default=False,
            help="(Removed) Incremental is now the default and only population mode.",
        )

    def handle(self, *args, **options):
        if not api.is_meilisearch_enabled():
            raise CommandError("Meilisearch is not enabled. Please set MEILISEARCH_ENABLED to True in your settings.")

        if options["reset"]:
            log.warning(
                "The --reset flag has been removed. "
                "Index reset is now handled automatically by post_migrate reconciliation. "
                "Run: ./manage.py cms migrate"
            )

        if options["init"]:
            log.warning(
                "The --init flag has been removed. "
                "Index initialization is now handled automatically by post_migrate reconciliation. "
                "Run: ./manage.py cms migrate"
            )

        if options["incremental"]:
            log.warning(
                "The --incremental flag has been removed. "
                "Incremental population is now the default behavior of this command."
            )
        if options["experimental"]:
            log.warning(
                "The --experimental flag has been removed. "
                "reindex_studio is now a stable command, so the flag is no longer necessary."
            )

        result = rebuild_index_incremental.delay()

        if settings.CELERY_ALWAYS_EAGER:
            self.stdout.write("Indexing complete!")
        else:
            self.stdout.write(
                f"Studio search index population has been queued (task_id={result.id}). "
                "Population will run incrementally in a Celery worker. "
                "Monitor progress in Celery worker logs. "
                "In order to reset the incremental indexing state, please run: "
                "./manage.py cms shell -c 'IncrementalIndexCompleted.objects.all().delete()'"
            )
