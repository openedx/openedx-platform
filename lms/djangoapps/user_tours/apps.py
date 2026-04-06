""" User Tour application configuration. """

from django.apps import AppConfig


class UserTourConfig(AppConfig):
    """ User Tour application configuration. """
    name = 'lms.djangoapps.user_tours'

    def ready(self):
        """ Code to run when getting the app ready. """
        # Connect signal handlers.
        from lms.djangoapps.user_tours import handlers  # pylint: disable=unused-import  # noqa: F401
