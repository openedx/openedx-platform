""" Management command to backpopulate User Tours. """

import time  # noqa: I001

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.user_tours.models import UserTour

User = get_user_model()


class Command(BaseCommand):
    """
    Example usage:
        $ ./manage.py lms backpopulate_user_tours
    """
    help = 'Creates or updates a row in the UserTour table for all users in the platform.'

    def add_arguments(self, parser):
        parser.add_argument('--batch-delay', type=float, default=1.0, help='Time delay in each iteration')
        parser.add_argument('--batch-size', type=int, default=10000, help='Batch size')

    def handle(self, *args, **options):
        """
        Backpopulates UserTour objects for all existing users who don't already have a UserTour.

        If the user has any prior enrollments, we will treat them as an existing user,
        otherwise they will receive a new user treatment.
        """
        def get_user_queryset(user_id=1):
            return User.objects.filter(id__gte=user_id, tour__isnull=True).order_by('id')[:batch_size]

        batch_delay = options['batch_delay']
        batch_size = options['batch_size']
        qset = get_user_queryset()
        # qset is evaluated here and checks for any results being returned.
        while qset:
            time.sleep(batch_delay)
            # Since qset has already been evaluated, this can just iterate through.
            for user in qset:
                if CourseEnrollment.objects.filter(user=user).exists():
                    course_home_tour_status = UserTour.CourseHomeChoices.EXISTING_USER_TOUR
                    show_courseware_tour = False
                else:
                    course_home_tour_status = UserTour.CourseHomeChoices.NEW_USER_TOUR
                    show_courseware_tour = True

                UserTour.objects.update_or_create(
                    user=user,
                    defaults={
                        'course_home_tour_status': course_home_tour_status,
                        'show_courseware_tour': show_courseware_tour,
                    },
                )
                last_seen_user_id = user.id
            # Use the last seen user's id to set a lower bound of rows to look at
            qset = get_user_queryset(last_seen_user_id)
