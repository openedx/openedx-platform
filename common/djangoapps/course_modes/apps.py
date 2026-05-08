"""Django App config for course_modes"""


from django.apps import AppConfig


class CourseModesConfig(AppConfig):  # pylint: disable=missing-class-docstring
    name = 'common.djangoapps.course_modes'
    verbose_name = "Course Modes"

    def ready(self):
        from . import signals  # pylint: disable=unused-import  # noqa: F401
