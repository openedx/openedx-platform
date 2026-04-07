"""
Test the CCXModulestoreWrapper
"""


import datetime
from collections import deque
from itertools import chain

import pytz
from ccx_keys.locator import CCXLocator
from six.moves import zip_longest

from common.djangoapps.student.tests.factories import AdminFactory, UserFactory
from lms.djangoapps.ccx.models import CustomCourseForEdX
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


class TestCCXModulestoreWrapper(SharedModuleStoreTestCase):
    """tests for a modulestore wrapped by CCXModulestoreWrapper
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        start = datetime.datetime(2010, 5, 12, 2, 42, tzinfo=pytz.UTC)
        due = datetime.datetime(2010, 7, 7, 0, 0, tzinfo=pytz.UTC)
        # Create a course outline
        cls.chapters = chapters = [
            BlockFactory.create(start=start, parent=cls.course) for _ in range(2)
        ]
        cls.sequentials = sequentials = [
            BlockFactory.create(parent=c) for _ in range(2) for c in chapters
        ]
        cls.verticals = verticals = [
            BlockFactory.create(
                due=due, parent=s, graded=True, format='Homework'
            ) for _ in range(2) for s in sequentials
        ]
        cls.blocks = [
            BlockFactory.create(parent=v, category='html') for _ in range(2) for v in verticals
        ]

    @classmethod
    def setUpTestData(cls):  # lint-amnesty, pylint: disable=super-method-not-called
        """
        Set up models for the whole TestCase.
        """
        cls.user = UserFactory.create()
        # Create instructor account
        cls.coach = AdminFactory.create()

    def setUp(self):
        """
        Set up tests
        """
        super().setUp()
        self.ccx = ccx = CustomCourseForEdX(
            course_id=self.course.id,
            display_name='Test CCX',
            coach=self.coach
        )
        ccx.save()

        self.ccx_locator = CCXLocator.from_course_locator(self.course.id, ccx.id)

    def get_all_children_bf(self, block):
        """traverse the children of block in a breadth-first order"""
        queue = deque([block])
        while queue:
            item = queue.popleft()
            yield item
            queue.extend(item.get_children())

    def get_course(self, key):
        """get a course given a key"""
        with self.store.bulk_operations(key):
            course = self.store.get_course(key)
        return course

    def test_get_course(self):
        """retrieving a course with a ccx key works"""
        expected = self.get_course(self.ccx_locator.to_course_locator())
        actual = self.get_course(self.ccx_locator)
        assert expected.location.course_key == actual.location.course_key.to_course_locator()
        assert expected.display_name == actual.display_name

    def test_get_children(self):
        """the children of retrieved courses should be the same with course and ccx keys
        """
        course_key = self.ccx_locator.to_course_locator()
        course = self.get_course(course_key)
        ccx = self.get_course(self.ccx_locator)
        test_fodder = zip_longest(
            self.get_all_children_bf(course), self.get_all_children_bf(ccx)
        )
        for expected, actual in test_fodder:
            if expected is None:
                self.fail('course children exhausted before ccx children')
            if actual is None:
                self.fail('ccx children exhausted before course children')
            assert expected.display_name == actual.display_name
            assert expected.location.course_key == course_key
            assert actual.location.course_key == self.ccx_locator

    def test_has_item(self):
        """can verify that a location exists, using ccx block usage key"""
        for item in chain(self.chapters, self.sequentials, self.verticals, self.blocks):
            block_key = self.ccx_locator.make_usage_key(
                item.location.block_type, item.location.block_id
            )
            assert self.store.has_item(block_key)

    def test_get_item(self):
        """can retrieve an item by a location key, using a ccx block usage key

        the retrieved item should be the same as the the one read without ccx
        info
        """
        for expected in chain(self.chapters, self.sequentials, self.verticals, self.blocks):
            block_key = self.ccx_locator.make_usage_key(
                expected.location.block_type, expected.location.block_id
            )
            actual = self.store.get_item(block_key)
            assert expected.display_name == actual.display_name
            assert expected.location == actual.location.to_block_locator()

    def test_publication_api(self):
        """verify that we can correctly discern a published item by ccx key"""
        with self.store.bulk_operations(self.ccx_locator):
            for expected in self.blocks:
                block_key = self.ccx_locator.make_usage_key(
                    expected.location.block_type, expected.location.block_id
                )
                assert self.store.has_published_version(expected)
                self.store.unpublish(block_key, self.user.id)
                assert not self.store.has_published_version(expected)
                self.store.publish(block_key, self.user.id)
                assert self.store.has_published_version(expected)
