# lint-amnesty, pylint: disable=django-not-configured
"""
Management command for expiring entitlements older than 1 year / 18 months.
"""
import logging
from datetime import date
from math import ceil
from textwrap import dedent

from dateutil.relativedelta import relativedelta
from django.core.management import BaseCommand

from common.djangoapps.entitlements.models import CourseEntitlement
from common.djangoapps.entitlements.tasks import expire_and_create_entitlements

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


class Command(BaseCommand):
    """
    Management command for expiring old entitlements and issuing new one against them.


    The command's goal is expire a set of entitlements depending on the --count argument passed to an
    idempotent Celery task for further (parallelized) processing.
    """
    help = dedent(__doc__).strip()

    def add_arguments(self, parser):
        parser.add_argument(
            '-c', '--commit',
            action='store_true',
            default=False,
            help='Submit tasks for processing'
        )

        parser.add_argument(
            '--count',
            type=int,
            default=100,  # arbitrary, should be adjusted if it is found to be inadequate
            help='How many entitlements to expire'
        )

        parser.add_argument(
            '--batch-size',
            type=int,
            default=10,  # arbitrary, should be adjusted if it is found to be inadequate
            help='How many entitlements to give each celery task'
        )

        parser.add_argument(
            '--username',
            required=True,
            help='Username to record in CourseEntitlementSupportDetail as author of operation.'
        )

        parser.add_argument(
            '--exceptional-course-uuids',
            action='extend',
            nargs='+',
            default=[],
            help='Space separated list of course UUIDs that should be expired in 18 months instead of 12 months.'
        )

    def handle(self, *args, **options):

        logger.info('Looking for entitlements which may be expirable.')

        support_username = options.get('username')
        current_date = date.today()
        exceptional_courses = options.get('exceptional_course_uuids')

        expiration_period = current_date - relativedelta(years=1)
        exceptional_expiration_period = current_date - relativedelta(years=1, months=6)

        normal_entitlements = CourseEntitlement.objects.filter(
            expired_at__isnull=True, created__lte=expiration_period,
            enrollment_course_run__isnull=True).exclude(course_uuid__in=exceptional_courses)

        exceptional_entitlements = CourseEntitlement.objects.filter(
            expired_at__isnull=True, created__lte=exceptional_expiration_period,
            enrollment_course_run__isnull=True, course_uuid__in=exceptional_courses)

        entitlements = normal_entitlements | exceptional_entitlements
        entitlements_count = entitlements.count()
        logger.info('Total entitlements that have reached expiration period are %d ', entitlements_count)

        entitlements_to_expire = min(max(1, options.get('count')), entitlements_count)
        batch_size = max(1, options.get('batch_size'))
        num_batches = ceil(entitlements_to_expire / batch_size) if entitlements else 0

        if options.get('commit'):
            logger.info('Enqueuing %d entitlement expiration tasks.', num_batches)
        else:
            logger.info(
                'Found %d batches. To enqueue entitlement expiration tasks, pass the -c or --commit flags.',
                num_batches
            )
            return

        for batch_num in range(num_batches):
            start = batch_num * batch_size
            end = min(start + batch_size, entitlements_to_expire)
            entitlement_ids = [entitlement.id for entitlement in entitlements[start:end]]
            expire_and_create_entitlements.delay(entitlement_ids, support_username)

        logger.info('Done. Successfully enqueued %d tasks.', num_batches)
