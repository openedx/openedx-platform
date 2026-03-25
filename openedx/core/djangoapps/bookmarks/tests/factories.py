"""
Factories for Bookmark models.
"""


from functools import partial  # noqa: I001

import factory
from factory.django import DjangoModelFactory
from opaque_keys.edx.locator import CourseLocator

from common.djangoapps.student.tests.factories import UserFactory

from ..models import Bookmark, XBlockCache

COURSE_KEY = CourseLocator('edX', 'test_course', 'test')
LOCATION = partial(COURSE_KEY.make_usage_key, 'problem')


class BookmarkFactory(DjangoModelFactory):
    """ Simple factory class for generating Bookmark """

    class Meta:
        model = Bookmark

    user = factory.SubFactory(UserFactory)
    course_key = COURSE_KEY
    usage_key = LOCATION('usage_id')
    xblock_cache = factory.SubFactory(
        'openedx.core.djangoapps.bookmarks.tests.factories.XBlockCacheFactory',
        course_key=factory.SelfAttribute('..course_key'),
        usage_key=factory.SelfAttribute('..usage_key'),
    )


class XBlockCacheFactory(DjangoModelFactory):
    """ Simple factory class for generating XblockCache. """

    class Meta:
        model = XBlockCache

    course_key = COURSE_KEY
    usage_key = factory.Sequence('4x://edx/100/block/{}'.format)
    display_name = ''
    paths = []
