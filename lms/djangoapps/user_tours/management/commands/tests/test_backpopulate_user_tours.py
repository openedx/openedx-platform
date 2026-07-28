""" Tests for backpopulate user tours Command. """

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db.models.signals import post_save
from django.test import TestCase

from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.user_tours.handlers import init_user_tour
from lms.djangoapps.user_tours.models import UserTour
from openedx.core.djangolib.testing.utils import skip_unless_lms

User = get_user_model()


@skip_unless_lms
class TestBackpopulateUserTourCommand(TestCase):
    """ Tests for the backpopulate user tours Command. """
    @classmethod
    def setUpClass(cls):
        """ Init required for the class to properly run. """
        super().setUpClass()
        # We need to disable the UserTour handler for the backpopulate command since it is assuming
        # users without UserTours to run.
        post_save.disconnect(init_user_tour, sender=User)

    @classmethod
    def tearDownClass(cls):
        """ Tear down for the class. """
        super().tearDownClass()
        post_save.connect(init_user_tour, sender=User)

    def test_happy_path(self):
        """ Tests happy path of command with one user. """
        user = UserFactory()
        assert UserTour.objects.count() == 0
        call_command('backpopulate_user_tours')
        assert UserTour.objects.count() == 1
        tour = UserTour.objects.get(user=user)
        assert tour.course_home_tour_status == UserTour.CourseHomeChoices.NEW_USER_TOUR
        assert tour.show_courseware_tour

    def test_mix_of_users(self):
        """ Tests having new and existing users. """
        new_user = UserFactory()
        existing_user = UserFactory()
        CourseEnrollmentFactory(user=existing_user)
        assert UserTour.objects.count() == 0
        call_command('backpopulate_user_tours')
        assert UserTour.objects.count() == 2

        new_user_tour = UserTour.objects.get(user=new_user)
        assert new_user_tour.course_home_tour_status == UserTour.CourseHomeChoices.NEW_USER_TOUR
        assert new_user_tour.show_courseware_tour

        existing_user_tour = UserTour.objects.get(user=existing_user)
        assert existing_user_tour.course_home_tour_status == UserTour.CourseHomeChoices.EXISTING_USER_TOUR
        assert not existing_user_tour.show_courseware_tour

    def test_rerun_of_command(self):
        """
        Tests command successfully reruns if needed.

        The command will ignore any user that already has a UserTour.
        """
        user = UserFactory()
        assert UserTour.objects.count() == 0
        call_command('backpopulate_user_tours')
        assert UserTour.objects.count() == 1
        tour = UserTour.objects.get(user=user)
        assert tour.course_home_tour_status == UserTour.CourseHomeChoices.NEW_USER_TOUR
        assert tour.show_courseware_tour
        # We will update the old user to show they are untouched by a rerun because they already have a UserTour.
        # These values are the opposite of what they would have from the command running.
        tour.course_home_tour_status = UserTour.CourseHomeChoices.EXISTING_USER_TOUR
        tour.show_courseware_tour = False
        tour.save()

        # Now add in a new user to show the command still works for this new user.
        new_user = UserFactory()
        call_command('backpopulate_user_tours')
        assert UserTour.objects.count() == 2
        new_tour = UserTour.objects.get(user=new_user)
        assert new_tour.course_home_tour_status == UserTour.CourseHomeChoices.NEW_USER_TOUR
        assert new_tour.show_courseware_tour
        # But the old tour is left alone
        tour = UserTour.objects.get(user=user)
        assert tour.course_home_tour_status == UserTour.CourseHomeChoices.EXISTING_USER_TOUR
        assert not tour.show_courseware_tour
