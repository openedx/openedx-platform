""" Signal handlers for User Tours. """

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.db.utils import ProgrammingError
from django.dispatch import receiver

from lms.djangoapps.user_tours.models import UserTour

User = get_user_model()


@receiver(post_save, sender=User)
def init_user_tour(sender, instance, created, **kwargs):  # pylint: disable=unused-argument
    """
    Initialize a new User Tour when a new user is created.
    """
    if created:
        try:
            UserTour.objects.create(user=instance)
        # So this is here because there is a dependency issue in the migrations where
        # this signal handler tries to run before the UserTour model is created.
        # In reality, this should never be hit because migrations will have already run.
        # If anyone better at migration dependencies is able to resolve this, please
        # feel free to remove this try/except.
        # The exact error we are catching is
        # django.db.utils.ProgrammingError: (1146, "Table 'edxtest.user_tours_usertour' doesn't exist")
        except ProgrammingError as e:  # noqa: F841
            pass
