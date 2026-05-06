"""
Factories for course mode models.
"""


import random

from factory import lazy_attribute
from factory.django import DjangoModelFactory
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.models import CourseMode
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory


# Factories are self documenting
class CourseModeFactory(DjangoModelFactory):  # pylint: disable=missing-class-docstring
    class Meta:
        model = CourseMode

    mode_slug = CourseMode.DEFAULT_MODE_SLUG
    currency = 'usd'
    expiration_datetime = None
    suggested_prices = ''

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        manager = cls._get_manager(model_class)
        course_kwargs = {}
        for key in list(kwargs):
            if key.startswith('course__'):
                course_kwargs[key.split('__')[1]] = kwargs.pop(key)

        if 'course' not in kwargs:
            course_id = kwargs.get('course_id')
            course_overview = None
            course_kwargs.setdefault('id', course_id)
            if course_id is not None:
                if isinstance(course_id, str):
                    course_id = CourseKey.from_string(course_id)
                    course_kwargs['id'] = course_id
                try:
                    course_overview = CourseOverview.get_from_id(course_id)
                except CourseOverview.DoesNotExist:
                    pass

            if course_overview is None:
                course_overview = CourseOverviewFactory(**course_kwargs)

            kwargs['course'] = course_overview

            del kwargs['course_id']

        return manager.create(*args, **kwargs)

    @lazy_attribute
    def min_price(self):
        if CourseMode.is_verified_slug(self.mode_slug):
            return random.randint(1, 100)
        return 0

    @lazy_attribute
    def mode_display_name(self):
        return f'{self.mode_slug} course'
