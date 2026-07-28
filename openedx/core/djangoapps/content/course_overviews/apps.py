"""
Configuration for course_overviews Django app
"""


from django.apps import AppConfig


class CourseOverviewsConfig(AppConfig):
    """
    Configuration class for course_overviews Django app
    """
    name = 'openedx.core.djangoapps.content.course_overviews'
    verbose_name = "Course Overviews"

    def ready(self):
        # Import signals to activate signal handler which invalidates
        # the CourseOverview cache every time a course is published.
        from . import signals  # pylint: disable=unused-import, unused-variable  # noqa: F401
