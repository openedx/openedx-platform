"""
Management command to perform data migration for copying values between date fields of Schedule Model
"""
import time

from django.core.management.base import BaseCommand
from django.db import transaction

from openedx.core.djangoapps.schedules.models import Schedule


class Command(BaseCommand):
    """
    Command to perform data migration for Schedule Model
    """
    help = 'Copy values from start to start_date in Schedule model'

    def add_arguments(self, parser):
        parser.add_argument('--delay', type=float, default=0.2, help='Time delay in each iteration')
        parser.add_argument('--size', type=int, default=1000, help='Batch size for atomic migration')

    def handle(self, *args, **kwargs):
        delay = kwargs['delay']
        size = kwargs['size']
        while Schedule.objects.filter(start_date__isnull=True).exists():
            time.sleep(delay)
            with transaction.atomic():
                for row in Schedule.objects.filter(start_date__isnull=True)[:size]:
                    time.sleep(delay)
                    row.start_date = row.start
                    row.save()
