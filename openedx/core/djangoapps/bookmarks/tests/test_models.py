"""
Tests for Bookmarks models.
"""


import datetime
from contextlib import contextmanager
from unittest import mock
from zoneinfo import ZoneInfo

import ddt
from freezegun import freeze_time
from opaque_keys.edx.keys import UsageKey
from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory, check_mongo_calls

from openedx.core.djangolib.testing.utils import skip_unless_lms
from common.djangoapps.student.tests.factories import AdminFactory, UserFactory

from .. import DEFAULT_FIELDS, OPTIONAL_FIELDS, PathItem
from ..models import Bookmark, XBlockCache, parse_path_data
from .factories import BookmarkFactory

EXAMPLE_USAGE_KEY_1 = 'i4x://org.15/course_15/chapter/Week_1'
EXAMPLE_USAGE_KEY_2 = 'i4x://org.15/course_15/chapter/Week_2'


noop_contextmanager = contextmanager(lambda x: (yield))  # pylint: disable=invalid-name


class BookmarksTestsBase(ModuleStoreTestCase):
    """
    Test the Bookmark model.
    """
    ALL_FIELDS = DEFAULT_FIELDS + OPTIONAL_FIELDS
    STORE_TYPE = ModuleStoreEnum.Type.split
    TEST_PASSWORD = 'test'

    ENABLED_CACHES = ['default', 'mongo_metadata_inheritance', 'loc_cache']

    def setUp(self):
        super().setUp()

        self.admin = AdminFactory()
        self.user = UserFactory.create(password=self.TEST_PASSWORD)
        self.other_user = UserFactory.create(password=self.TEST_PASSWORD)
        self.setup_data(self.STORE_TYPE)

    def setup_data(self, store_type=ModuleStoreEnum.Type.split):
        """ Create courses and add some test blocks. """

        with self.store.default_store(store_type):

            self.course = CourseFactory.create(display_name='An Introduction to API Testing')
            self.course_id = str(self.course.id)

            self.chapter_1 = BlockFactory.create(
                parent=self.course, category='chapter', display_name='Week 1'
            )
            self.chapter_2 = BlockFactory.create(
                parent=self.course, category='chapter', display_name='Week 2'
            )

            self.sequential_1 = BlockFactory.create(
                parent=self.chapter_1, category='sequential', display_name='Lesson 1'
            )
            self.sequential_2 = BlockFactory.create(
                parent=self.chapter_1, category='sequential', display_name='Lesson 2'
            )

            self.vertical_1 = BlockFactory.create(
                parent=self.sequential_1, category='vertical', display_name='Subsection 1'
            )
            self.vertical_2 = BlockFactory.create(
                parent=self.sequential_2, category='vertical', display_name='Subsection 2'
            )
            self.vertical_3 = BlockFactory.create(
                parent=self.sequential_2, category='vertical', display_name='Subsection 3'
            )

            self.html_1 = BlockFactory.create(
                parent=self.vertical_2, category='html', display_name='Details 1'
            )

        self.path = [
            PathItem(self.chapter_1.location, self.chapter_1.display_name),
            PathItem(self.sequential_2.location, self.sequential_2.display_name),
        ]

        self.bookmark_1 = BookmarkFactory.create(
            user=self.user,
            course_key=self.course_id,
            usage_key=self.sequential_1.location,
            xblock_cache=XBlockCache.create({
                'display_name': self.sequential_1.display_name,
                'usage_key': self.sequential_1.location,
            }),
        )
        self.bookmark_2 = BookmarkFactory.create(
            user=self.user,
            course_key=self.course_id,
            usage_key=self.sequential_2.location,
            xblock_cache=XBlockCache.create({
                'display_name': self.sequential_2.display_name,
                'usage_key': self.sequential_2.location,
            }),
        )
        self.bookmark_3 = BookmarkFactory.create(
            user=self.user,
            course_key=self.course_id,
            usage_key=self.vertical_3.location,
            xblock_cache=XBlockCache.create({
                'display_name': self.vertical_3.display_name,
                'usage_key': self.vertical_3.location,
            }),
        )
        self.bookmark_4 = BookmarkFactory.create(
            user=self.user,
            course_key=self.course_id,
            usage_key=self.chapter_2.location,
            xblock_cache=XBlockCache.create({
                'display_name': self.chapter_2.display_name,
                'usage_key': self.chapter_2.location,
            }),
        )

        self.other_course = CourseFactory.create(display_name='An Introduction to API Testing 2')

        with self.store.bulk_operations(self.other_course.id):

            self.other_chapter_1 = BlockFactory.create(
                parent=self.other_course, category='chapter', display_name='Other Week 1'
            )
            self.other_sequential_1 = BlockFactory.create(
                parent=self.other_chapter_1, category='sequential', display_name='Other Lesson 1'
            )
            self.other_sequential_2 = BlockFactory.create(
                parent=self.other_chapter_1, category='sequential', display_name='Other Lesson 2'
            )
            self.other_vertical_1 = BlockFactory.create(
                parent=self.other_sequential_1, category='vertical', display_name='Other Subsection 1'
            )
            self.other_vertical_2 = BlockFactory.create(
                parent=self.other_sequential_1, category='vertical', display_name='Other Subsection 2'
            )

            # self.other_vertical_1 has two parents
            self.other_sequential_2.children.append(self.other_vertical_1.location)
            with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, self.course.id):
                self.store.update_item(self.other_sequential_2, self.admin.id)

        self.other_bookmark_1 = BookmarkFactory.create(
            user=self.user,
            course_key=str(self.other_course.id),
            usage_key=self.other_vertical_1.location,
            xblock_cache=XBlockCache.create({
                'display_name': self.other_vertical_1.display_name,
                'usage_key': self.other_vertical_1.location,
            }),
        )

    def create_course_with_blocks(self, children_per_block=1, depth=1, store_type=ModuleStoreEnum.Type.split):
        """
        Create a course and add blocks.
        """
        with self.store.default_store(store_type):

            course = CourseFactory.create()
            display_name = 0

            blocks_at_next_level = [course]

            for __ in range(depth):
                blocks_at_current_level = blocks_at_next_level
                blocks_at_next_level = []

                for block in blocks_at_current_level:
                    for __ in range(children_per_block):
                        blocks_at_next_level += [BlockFactory.create(
                            parent_location=block.location, display_name=str(display_name)
                        )]
                        display_name += 1

        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, course.id):
            return self.store.get_course(course.id, depth=None)

    def create_course_with_bookmarks_count(self, count, store_type=ModuleStoreEnum.Type.split):
        """
        Create a course, add some content and add bookmarks.
        """
        with self.store.default_store(store_type):

            course = CourseFactory.create()

            blocks = [BlockFactory.create(
                parent=course, category='chapter', display_name=str(index)
            ) for index in range(count)]

            bookmarks = [BookmarkFactory.create(
                user=self.user,
                course_key=course.id,
                usage_key=block.location,
                xblock_cache=XBlockCache.create({
                    'display_name': block.display_name,
                    'usage_key': block.location,
                }),
            ) for block in blocks]

        return course, blocks, bookmarks

    def assert_bookmark_model_is_valid(self, bookmark, bookmark_data):
        """
        Assert that the attributes of the bookmark model were set correctly.
        """
        assert bookmark.user == bookmark_data['user']
        assert bookmark.course_key == bookmark_data['course_key']
        assert str(bookmark.usage_key) == str(bookmark_data['usage_key'])
        assert bookmark.resource_id == '{},{}'.format(bookmark_data['user'], bookmark_data['usage_key'])
        assert bookmark.display_name == bookmark_data['display_name']
        assert bookmark.path == self.path
        assert bookmark.created is not None

        assert bookmark.xblock_cache.course_key == bookmark_data['course_key']
        assert bookmark.xblock_cache.display_name == bookmark_data['display_name']

    def assert_bookmark_data_is_valid(self, bookmark, bookmark_data, check_optional_fields=False):
        """
        Assert that the bookmark data matches the data in the model.
        """
        assert bookmark_data['id'] == bookmark.resource_id
        assert bookmark_data['course_id'] == str(bookmark.course_key)
        assert bookmark_data['usage_id'] == str(bookmark.usage_key)
        assert bookmark_data['block_type'] == str(bookmark.usage_key.block_type)
        assert bookmark_data['created'] is not None

        if check_optional_fields:
            assert bookmark_data['display_name'] == bookmark.display_name
            assert bookmark_data['path'] == bookmark.path


@ddt.ddt
@skip_unless_lms
class BookmarkModelTests(BookmarksTestsBase):
    """
    Test the Bookmark model.
    """

    def setUp(self):
        super().setUp()

        self.vertical_4 = BlockFactory.create(
            parent=self.sequential_2,
            category='vertical',
            display_name=None
        )

    def get_bookmark_data(self, block, user=None):
        """
        Returns bookmark data for testing.
        """
        return {
            'user': user or self.user,
            'usage_key': block.location,
            'course_key': block.location.course_key,
            'display_name': block.display_name,
        }

    @ddt.data(
        ('course', [], 2),
        ('chapter_1', [], 1),
        ('sequential_1', ['chapter_1'], 1),
        ('vertical_1', ['chapter_1', 'sequential_1'], 1),
        ('html_1', ['chapter_1', 'sequential_2', 'vertical_2'], 1),
    )
    @ddt.unpack
    def test_path_and_queries_on_create(self, block_to_bookmark, ancestors_attrs, expected_mongo_calls):
        """
        In case of mongo, 1 query is used to fetch the block, and 2
        by path_to_location(), and then 1 query per parent in path
        is needed to fetch the parent blocks.
        """

        self.setup_data()
        user = UserFactory.create()

        expected_path = [PathItem(
            usage_key=getattr(self, ancestor_attr).location, display_name=getattr(self, ancestor_attr).display_name
        ) for ancestor_attr in ancestors_attrs]

        bookmark_data = self.get_bookmark_data(getattr(self, block_to_bookmark), user=user)

        with check_mongo_calls(expected_mongo_calls):
            bookmark, __ = Bookmark.create(bookmark_data)

        assert bookmark.path == expected_path
        assert bookmark.xblock_cache is not None
        assert bookmark.xblock_cache.paths == []

    def test_create_bookmark_success(self):
        """
        Tests creation of bookmark.
        """
        bookmark_data = self.get_bookmark_data(self.vertical_2)
        bookmark, __ = Bookmark.create(bookmark_data)
        self.assert_bookmark_model_is_valid(bookmark, bookmark_data)

        bookmark_data_different_values = self.get_bookmark_data(self.vertical_2)
        bookmark_data_different_values['display_name'] = 'Introduction Video'
        bookmark2, __ = Bookmark.create(bookmark_data_different_values)
        # The bookmark object already created should have been returned without modifications.
        assert bookmark == bookmark2
        assert bookmark.xblock_cache == bookmark2.xblock_cache
        self.assert_bookmark_model_is_valid(bookmark2, bookmark_data)

        bookmark_data_different_user = self.get_bookmark_data(self.vertical_2)
        bookmark_data_different_user['user'] = UserFactory.create()
        bookmark3, __ = Bookmark.create(bookmark_data_different_user)
        assert bookmark != bookmark3
        self.assert_bookmark_model_is_valid(bookmark3, bookmark_data_different_user)

    def test_create_bookmark_successfully_with_display_name_none(self):
        """
        Tests creation of bookmark with display_name None.
        """
        bookmark_data = self.get_bookmark_data(self.vertical_4)
        bookmark, __ = Bookmark.create(bookmark_data)
        bookmark_data['display_name'] = self.vertical_4.display_name_with_default
        self.assert_bookmark_model_is_valid(bookmark, bookmark_data)

    @ddt.data(
        (-30, [[PathItem(EXAMPLE_USAGE_KEY_1, '1')]], 1),
        (30, None, 2),
        (30, [], 2),
        (30, [[PathItem(EXAMPLE_USAGE_KEY_1, '1')]], 1),
        (30, [[PathItem(EXAMPLE_USAGE_KEY_1, '1')], [PathItem(EXAMPLE_USAGE_KEY_2, '2')]], 2),
    )
    @ddt.unpack
    @mock.patch('openedx.core.djangoapps.bookmarks.models.Bookmark.get_path')
    def test_path(self, seconds_delta, paths, get_path_call_count, mock_get_path):

        block_path = [PathItem(UsageKey.from_string(EXAMPLE_USAGE_KEY_1), '1')]
        mock_get_path.return_value = block_path

        html = BlockFactory.create(
            parent=self.other_chapter_1, category='html', display_name='Other Lesson 1'
        )

        bookmark_data = self.get_bookmark_data(html)
        bookmark, __ = Bookmark.create(bookmark_data)
        assert bookmark.xblock_cache is not None

        modification_datetime = datetime.datetime.now(ZoneInfo("UTC")) + datetime.timedelta(seconds=seconds_delta)
        with freeze_time(modification_datetime):
            bookmark.xblock_cache.paths = paths
            bookmark.xblock_cache.save()

        assert bookmark.path == block_path
        assert mock_get_path.call_count == get_path_call_count

    @ddt.data(
        (2, 2, 1),
        (4, 2, 1),
        (2, 3, 1),
        # (4, 3, 1),
        (2, 4, 1),
    )
    @ddt.unpack
    def test_get_path_queries(self, children_per_block, depth, expected_mongo_calls):
        """
        In case of mongo, 2 queries are used by path_to_location(), and then
        1 query per parent in path is needed to fetch the parent blocks.
        """

        course = self.create_course_with_blocks(children_per_block, depth)

        # Find a leaf block.
        block = course
        for __ in range(depth - 1):
            children = block.get_children()
            block = children[-1]

        with check_mongo_calls(expected_mongo_calls):
            path = Bookmark.get_path(block.location)
            assert len(path) == (depth - 2)

    def test_get_path_in_case_of_exceptions(self):

        user = UserFactory.create()

        # Block does not exist
        key = self.course.id
        usage_key = UsageKey.from_string(f'block-v1:{key.org}+{key.course}+{key.run}+type@vertical+block@interactive')
        assert not Bookmark.get_path(usage_key)

        # Block is an orphan
        self.other_sequential_1.children = []
        modulestore().update_item(self.other_sequential_1, self.admin.id)

        bookmark_data = self.get_bookmark_data(self.other_vertical_2, user=user)
        bookmark, __ = Bookmark.create(bookmark_data)

        assert bookmark.path == []
        assert bookmark.xblock_cache is not None
        assert bookmark.xblock_cache.paths == []

        # Parent block could not be retrieved
        with mock.patch('openedx.core.djangoapps.bookmarks.models.search.path_to_location') as mock_path_to_location:
            mock_path_to_location.return_value = [usage_key]
            bookmark_data = self.get_bookmark_data(self.other_sequential_1, user=user)
            bookmark, __ = Bookmark.create(bookmark_data)
            assert bookmark.path == []


@ddt.ddt
class XBlockCacheModelTest(ModuleStoreTestCase):
    """
    Test the XBlockCache model.
    """
    COURSE_KEY = CourseLocator(org='test', course='test', run='test')
    CHAPTER1_USAGE_KEY = BlockUsageLocator(COURSE_KEY, block_type='chapter', block_id='chapter1')
    SECTION1_USAGE_KEY = BlockUsageLocator(COURSE_KEY, block_type='section', block_id='section1')
    SECTION2_USAGE_KEY = BlockUsageLocator(COURSE_KEY, block_type='section', block_id='section1')
    VERTICAL1_USAGE_KEY = BlockUsageLocator(COURSE_KEY, block_type='vertical', block_id='sequential1')
    PATH1 = [
        [str(CHAPTER1_USAGE_KEY), 'Chapter 1'],
        [str(SECTION1_USAGE_KEY), 'Section 1'],
    ]
    PATH2 = [
        [str(CHAPTER1_USAGE_KEY), 'Chapter 1'],
        [str(SECTION2_USAGE_KEY), 'Section 2'],
    ]

    def assert_xblock_cache_data(self, xblock_cache, data):
        """
        Assert that the XBlockCache object values match.
        """
        assert xblock_cache.usage_key == data['usage_key']
        assert xblock_cache.course_key == data['usage_key'].course_key
        assert xblock_cache.display_name == data['display_name']
        assert xblock_cache._paths == data['_paths']  # pylint: disable=protected-access
        assert xblock_cache.paths == [parse_path_data(path) for path in data['_paths']]

    @ddt.data(
        (
            [
                {'usage_key': VERTICAL1_USAGE_KEY, },
                {'display_name': '', '_paths': [], },
            ],
            [
                {'usage_key': VERTICAL1_USAGE_KEY, 'display_name': 'Vertical 5', '_paths': [PATH2]},
                {'_paths': []},
            ],
        ),
        (
            [
                {'usage_key': VERTICAL1_USAGE_KEY, 'display_name': 'Vertical 4', '_paths': [PATH1]},
                {},
            ],
            [
                {'usage_key': VERTICAL1_USAGE_KEY, 'display_name': 'Vertical 5', '_paths': [PATH2]},
                {'_paths': [PATH1]},
            ],
        ),
    )
    def test_create(self, data):
        """
        Test XBlockCache.create() constructs and updates objects correctly.
        """
        for create_data, additional_data_to_expect in data:
            xblock_cache = XBlockCache.create(create_data)
            create_data.update(additional_data_to_expect)
            self.assert_xblock_cache_data(xblock_cache, create_data)

    @ddt.data(
        ([], [PATH1]),
        ([PATH1, PATH2], [PATH1]),
        ([PATH1], []),
    )
    @ddt.unpack
    def test_paths(self, original_paths, updated_paths):
        xblock_cache = XBlockCache.create({
            'usage_key': self.VERTICAL1_USAGE_KEY,
            'display_name': 'The end.',
            '_paths': original_paths,
        })
        assert xblock_cache.paths == [parse_path_data(path) for path in original_paths]

        xblock_cache.paths = [parse_path_data(path) for path in updated_paths]
        xblock_cache.save()

        xblock_cache = XBlockCache.objects.get(id=xblock_cache.id)
        assert xblock_cache._paths == updated_paths  # pylint: disable=protected-access
        assert xblock_cache.paths == [parse_path_data(path) for path in updated_paths]
