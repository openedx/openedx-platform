"""
Django app configuration for the XBlock Runtime django app
"""
from django.apps import AppConfig, apps
from django.conf import settings

from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from .data import StudentDataMode, AuthoredDataMode


class XBlockAppConfig(AppConfig):
    """
    Django app configuration for the new XBlock Runtime django app
    """
    name = 'openedx.core.djangoapps.xblock'
    verbose_name = 'New XBlock Runtime'
    label = 'xblock_new'  # The name 'xblock' is already taken by ORA2's 'openassessment.xblock' app :/

    def get_runtime_params(self):
        """
        Get the OpenedXContentRuntime parameters appropriate for viewing and/or
        editing XBlock content.
        """
        raise NotImplementedError

    def get_site_root_url(self):
        """
        Get the absolute root URL to this site, e.g. 'https://courses.example.com'
        Should not have any trailing slash.
        """
        raise NotImplementedError

    def get_learning_context_params(self):
        """
        Get additional kwargs that are passed to learning context implementations
        (LearningContext subclass constructors).
        """
        return {}


class LmsXBlockAppConfig(XBlockAppConfig):
    """
    LMS-specific configuration of the XBlock Runtime django app.
    """

    def get_runtime_params(self):
        """
        Get the OpenedXContentRuntime parameters appropriate for viewing and/or
        editing XBlock content in the LMS
        """
        return dict(
            student_data_mode=StudentDataMode.Persisted,
            authored_data_mode=AuthoredDataMode.STRICTLY_PUBLISHED,
        )

    def get_site_root_url(self):
        """
        Get the absolute root URL to this site, e.g. 'https://courses.example.com'
        Should not have any trailing slash.
        """
        return configuration_helpers.get_value('LMS_ROOT_URL', settings.LMS_ROOT_URL)


class StudioXBlockAppConfig(XBlockAppConfig):
    """
    Studio-specific configuration of the XBlock Runtime django app.
    """

    def get_runtime_params(self):
        """
        Get the OpenedXContentRuntime parameters appropriate for viewing and/or
        editing XBlock content in Studio
        """
        return dict(
            student_data_mode=StudentDataMode.Ephemeral,
            authored_data_mode=AuthoredDataMode.DEFAULT_DRAFT,
        )

    def get_site_root_url(self):
        """
        Get the absolute root URL to this site, e.g. 'https://studio.example.com'
        Should not have any trailing slash.
        """
        scheme = "https" if settings.HTTPS == "on" else "http"
        return scheme + '://' + settings.CMS_BASE
        # or for the LMS version: configuration_helpers.get_value('LMS_ROOT_URL', settings.LMS_ROOT_URL)

    def get_learning_context_params(self):
        """
        Get additional kwargs that are passed to learning context implementations
        (LearningContext subclass constructors).
        """
        return {}


def get_xblock_app_config():
    """
    Get whichever of the above AppConfig subclasses is active.
    """
    return apps.get_app_config(XBlockAppConfig.label)
