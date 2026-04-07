"""
Management command for deleting notifications with parameters
"""
import datetime
import logging

from django.core.management.base import BaseCommand

from openedx.core.djangoapps.notifications.base_notification import COURSE_NOTIFICATION_APPS, COURSE_NOTIFICATION_TYPES
from openedx.core.djangoapps.notifications.tasks import delete_notifications

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Invoke with:

        python manage.py lms delete_notifications
    """
    help = (
        "Delete notifications that meets a criteria. Requires app_name, notification_type and created (duration)"
    )

    def add_arguments(self, parser):
        """
        Add command line arguments to management command
        """
        parser.add_argument('--app_name', choices=list(COURSE_NOTIFICATION_APPS.keys()), required=True)
        parser.add_argument('--notification_type', choices=list(COURSE_NOTIFICATION_TYPES.keys()),
                            required=True)
        parser.add_argument('--created', type=argparse_date, required=True,
                            help="Allowed date formats YYYY-MM-DD. YYYY Year. MM Month. DD Date."
                                 "Duration can be specified with ~. Maximum 15 days duration is allowed")
        parser.add_argument('--course_id', required=False)

    def handle(self, *args, **kwargs):
        """
        Calls delete notifications task
        """
        delete_notifications.delay(kwargs)
        logger.info('Deletion task is in progress please check logs to verify')


def argparse_date(string: str):
    """
    Argparse Type to return datetime dictionary from string
    Returns {
    'created__gte': datetime,
    'created__lte': datetime,
    }
    """
    ret_dict = {}
    if '~' in string:
        start_date, end_date = string.split('~')
        start_date = parse_date(start_date)
        end_date = parse_date(end_date)
        if (end_date - start_date).days > 15:
            raise ValueError('More than 15 days duration is not allowed')
    else:
        start_date = parse_date(string)
        end_date = start_date
    ret_dict['created__gte'] = datetime.datetime.combine(start_date, datetime.time.min)
    ret_dict['created__lte'] = datetime.datetime.combine(end_date, datetime.time.max)
    return ret_dict


def parse_date(string):
    """
    Converts string date to datetime object
    """
    return datetime.datetime.strptime(string.strip(), "%Y-%m-%d")
