"""
Mark video uploads that never finished as failed.
"""
import logging
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from edxval.models import Video
from pytz import UTC

from cms.djangoapps.contentstore.video_storage_handlers import MAX_UPLOAD_HOURS, convert_video_status

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Mark every video still in the `upload` status after MAX_UPLOAD_HOURS as `upload_failed`.

    Studio's video list does the same for the videos of the course it shows; this command does it
    for all of them, without anyone having to open that list.

    Example:
        ./manage.py cms fail_stuck_video_uploads --dry-run
        ./manage.py cms fail_stuck_video_uploads
    """
    help = f'Mark videos still uploading after {MAX_UPLOAD_HOURS} hours as failed.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report the stuck uploads without changing them.',
        )

    def handle(self, *args, **options):
        cutoff = datetime.now(UTC) - timedelta(hours=MAX_UPLOAD_HOURS)
        stuck = list(
            Video.objects.filter(status='upload', created__lt=cutoff)
            .values('edx_video_id', 'status', 'created')
        )
        for video in stuck:
            if options['dry_run']:
                log.info('Video [%s] is still in upload since [%s]', video['edx_video_id'], video['created'])
            else:
                convert_video_status(video)
        log.info(
            '%s %d video upload(s) stuck for more than %d hours',
            'Found' if options['dry_run'] else 'Marked as failed',
            len(stuck),
            MAX_UPLOAD_HOURS,
        )
