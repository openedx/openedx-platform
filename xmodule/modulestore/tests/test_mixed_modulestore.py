"""
Unit tests for the Mixed Modulestore, with DDT for the various stores (Split, Draft, XML)
"""


import datetime
import itertools
import logging
import mimetypes
from collections import namedtuple
from contextlib import contextmanager
from shutil import rmtree
from tempfile import mkdtemp
from uuid import uuid4
from unittest.mock import Mock, call, patch

import ddt
from openedx_events.content_authoring.data import CourseData, XBlockData
from openedx_events.content_authoring.signals import (
    COURSE_CREATED,
    XBLOCK_CREATED,
    XBLOCK_DELETED,
    XBLOCK_PUBLISHED,
    XBLOCK_UPDATED
)
from openedx_events.tests.utils import OpenEdxEventsTestMixin
import pymongo
import pytest
# Mixed modulestore depends on django, so we'll manually configure some django settings
# before importing the module
# TODO remove this import and the configuration -- xmodule should not depend on django!
from django.conf import settings
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator, LibraryLocator  # pylint: disable=unused-import
from pytz import UTC
from web_fragments.fragment import Fragment
from xblock.core import XBlockAside
from xblock.fields import Scope, ScopeIds, String
from xblock.runtime import DictKeyValueStore, KvsFieldData
from xblock.test.tools import TestRuntime

from openedx.core.lib.tests import attr
from xmodule.contentstore.content import StaticContent
from xmodule.exceptions import InvalidVersionError
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.draft_and_published import DIRECT_ONLY_CATEGORIES, UnsupportedRevisionError
from xmodule.modulestore.edit_info import EditInfoMixin
from xmodule.modulestore.exceptions import (
    DuplicateCourseError,
    ItemNotFoundError,
    NoPathToItem,
)
from xmodule.modulestore.inheritance import InheritanceMixin
from xmodule.modulestore.mixed import MixedModuleStore
from xmodule.modulestore.search import navigation_index, path_to_location
from xmodule.modulestore.split_mongo.split import SplitMongoModuleStore
from xmodule.modulestore.store_utilities import DETACHED_XBLOCK_TYPES
from xmodule.modulestore.tests.factories import check_exact_number_of_calls, check_mongo_calls
from xmodule.modulestore.tests.mongo_connection import MONGO_HOST, MONGO_PORT_NUM
from xmodule.modulestore.tests.test_asides import AsideTestType
from xmodule.modulestore.tests.utils import MongoContentstoreBuilder, create_modulestore_instance
from xmodule.modulestore.xml_exporter import export_course_to_xml
from xmodule.modulestore.xml_importer import LocationMixin, import_course_from_xml
from xmodule.tests import DATA_DIR, CourseComparisonTest
from xmodule.x_module import XModuleMixin
from common.test.utils import assert_dict_contains_subset

if not settings.configured:
    settings.configure()


log = logging.getLogger(__name__)


class CommonMixedModuleStoreSetup(CourseComparisonTest, OpenEdxEventsTestMixin):
    """
    Quasi-superclass which tests Location based apps against both split and mongo dbs (Locator and
    Location-based dbs)
    """
    HOST = MONGO_HOST
    PORT = MONGO_PORT_NUM
    DB = 'test_mongo_%s' % uuid4().hex[:5]
    COLLECTION = 'modulestore'
    ASSET_COLLECTION = 'assetstore'
    FS_ROOT = DATA_DIR
    DEFAULT_CLASS = 'xmodule.hidden_block.HiddenBlock'
    RENDER_TEMPLATE = lambda t_n, d, ctx=None, nsp='main': ''

    MONGO_COURSEID = 'MITx/999/2013_Spring'

    modulestore_options = {
        'default_class': DEFAULT_CLASS,
        'fs_root': DATA_DIR,
        'render_template': RENDER_TEMPLATE,
        'xblock_mixins': (EditInfoMixin, InheritanceMixin, LocationMixin, XModuleMixin),
    }
    DOC_STORE_CONFIG = {
        'host': HOST,
        'port': PORT,
        'db': DB,
        'collection': COLLECTION,
        'asset_collection': ASSET_COLLECTION,
    }
    OPTIONS = {
        'stores': [
            {
                'NAME': ModuleStoreEnum.Type.split,
                'ENGINE': 'xmodule.modulestore.split_mongo.split_draft.DraftVersioningModuleStore',
                'DOC_STORE_CONFIG': DOC_STORE_CONFIG,
                'OPTIONS': modulestore_options
            },
            {
                'NAME': ModuleStoreEnum.Type.mongo,
                'ENGINE': 'xmodule.modulestore.mongo.draft.DraftModuleStore',
                'DOC_STORE_CONFIG': DOC_STORE_CONFIG,
                'OPTIONS': modulestore_options
            },
        ],
        'xblock_mixins': modulestore_options['xblock_mixins'],
    }
    ENABLED_OPENEDX_EVENTS = [
        "org.openedx.content_authoring.course.created.v1",
        "org.openedx.content_authoring.xblock.created.v1",
        "org.openedx.content_authoring.xblock.updated.v1",
        "org.openedx.content_authoring.xblock.deleted.v1",
        "org.openedx.content_authoring.xblock.published.v1",
    ]

    @classmethod
    def setUpClass(cls):
        """
        Set up class method for the Test class.
        This method starts manually events isolation. Explanation here:
        openedx/core/djangoapps/user_authn/views/tests/test_events.py#L44
        """
        super().setUpClass()
        cls.start_events_isolation()

    def setUp(self):
        """
        Set up the database for testing
        """
        super().setUp()

        self.exclude_field(None, 'wiki_slug')
        self.exclude_field(None, 'xml_attributes')
        self.exclude_field(None, 'parent')
        self.ignore_asset_key('_id')
        self.ignore_asset_key('uploadDate')
        self.ignore_asset_key('content_son')
        self.ignore_asset_key('thumbnail_location')

        self.options = getattr(self, 'options', self.OPTIONS)
        self.connection = pymongo.MongoClient(
            host=self.HOST,
            port=self.PORT,
            tz_aware=True,
        )
        self.connection.drop_database(self.DB)
        self.addCleanup(self._drop_database)
        self.addCleanup(self._close_connection)

        # define attrs which get set in initdb to quell pylint
        self.writable_chapter_location = self.store = self.fake_location = None
        self.course_locations = {}

        self.user_id = ModuleStoreEnum.UserID.test
        # mock and ignore publishable link entity related tasks to avoid unnecessary
        # errors as it is tested separately
        if settings.ROOT_URLCONF == 'cms.urls':
            create_xblock_upstream_link_patch = patch(
                'cms.djangoapps.contentstore.signals.handlers.handle_create_xblock_upstream_link'
            )
            create_xblock_upstream_link_patch.start()
            self.addCleanup(create_xblock_upstream_link_patch.stop)
            update_xblock_upstream_link_patch = patch(
                'cms.djangoapps.contentstore.signals.handlers.handle_update_xblock_upstream_link'
            )
            update_xblock_upstream_link_patch.start()
            self.addCleanup(update_xblock_upstream_link_patch.stop)
            component_link_patch = patch(
                'cms.djangoapps.contentstore.signals.handlers.ComponentLink'
            )
            component_link_patch.start()
            self.addCleanup(component_link_patch.stop)
            container_link_patch = patch(
                'cms.djangoapps.contentstore.signals.handlers.ContainerLink'
            )
            container_link_patch.start()
            self.addCleanup(container_link_patch.stop)

    def _check_connection(self):
        """
        Check mongodb connection is open or not.
        """
        try:
            self.connection.admin.command('ping')
            return True
        except pymongo.errors.InvalidOperation:
            return False

    def _ensure_connection(self):
        """
        Make sure that mongodb connection is open.
        """
        if not self._check_connection():
            self.connection = pymongo.MongoClient(
                host=self.HOST,
                port=self.PORT,
                tz_aware=True,
            )

    def _drop_database(self):
        """
        Drop mongodb database.
        """
        self._ensure_connection()
        self.connection.drop_database(self.DB)

    def _close_connection(self):
        """
        Close mongodb connection.
        """
        try:
            self.connection.close()
        except pymongo.errors.InvalidOperation:
            pass

    def _create_course(self, course_key, asides=None):
        """
        Create a course w/ one item in the persistence store using the given course & item location.
        """
        # create course
        with self.store.bulk_operations(course_key):
            self.course = self.store.create_course(course_key.org, course_key.course, course_key.run, self.user_id)  # lint-amnesty, pylint: disable=attribute-defined-outside-init
            if isinstance(self.course.id, CourseLocator):
                self.course_locations[self.MONGO_COURSEID] = self.course.location
            else:
                assert self.course.id == course_key

            # create chapter
            chapter = self.store.create_child(self.user_id, self.course.location, 'chapter',
                                              block_id='Overview', asides=asides)
            self.writable_chapter_location = chapter.location

    def _create_block_hierarchy(self):
        """
        Creates a hierarchy of blocks for testing
        Each block's (version_agnostic) location is assigned as a field of the class and can be easily accessed
        """
        BlockInfo = namedtuple('BlockInfo', 'field_name, category, display_name, sub_tree')

        trees = [
            BlockInfo(
                'chapter_x', 'chapter', 'Chapter_x', [
                    BlockInfo(
                        'sequential_x1', 'sequential', 'Sequential_x1', [
                            BlockInfo(
                                'vertical_x1a', 'vertical', 'Vertical_x1a', [
                                    BlockInfo('problem_x1a_1', 'problem', 'Problem_x1a_1', []),
                                    BlockInfo('problem_x1a_2', 'problem', 'Problem_x1a_2', []),
                                    BlockInfo('problem_x1a_3', 'problem', 'Problem_x1a_3', []),
                                    BlockInfo('html_x1a_1', 'html', 'HTML_x1a_1', []),
                                ]
                            ),
                            BlockInfo(
                                'vertical_x1b', 'vertical', 'Vertical_x1b', []
                            )
                        ]
                    ),
                    BlockInfo(
                        'sequential_x2', 'sequential', 'Sequential_x2', []
                    )
                ]
            ),
            BlockInfo(
                'chapter_y', 'chapter', 'Chapter_y', [
                    BlockInfo(
                        'sequential_y1', 'sequential', 'Sequential_y1', [
                            BlockInfo(
                                'vertical_y1a', 'vertical', 'Vertical_y1a', [
                                    BlockInfo('problem_y1a_1', 'problem', 'Problem_y1a_1', []),
                                    BlockInfo('problem_y1a_2', 'problem', 'Problem_y1a_2', []),
                                    BlockInfo('problem_y1a_3', 'problem', 'Problem_y1a_3', []),
                                ]
                            )
                        ]
                    )
                ]
            )
        ]

        def create_sub_tree(parent, block_info):
            """
            recursive function that creates the given block and its descendants
            """
            block = self.store.create_child(
                self.user_id, parent.location,
                block_info.category, block_id=block_info.display_name,
                fields={'display_name': block_info.display_name},
            )
            for tree in block_info.sub_tree:
                create_sub_tree(block, tree)
            setattr(self, block_info.field_name, block.location)

        with self.store.bulk_operations(self.course.id):
            for tree in trees:
                create_sub_tree(self.course, tree)

    def _course_key_from_string(self, string):
        """
        Get the course key for the given course string
        """
        return self.course_locations[string].course_key

    def _has_changes(self, location):
        """
        Helper function that loads the item before calling has_changes
        """
        return self.store.has_changes(self.store.get_item(location))

    def _initialize_mixed(self, mappings=None, contentstore=None):
        """
        initializes the mixed modulestore.
        """
        mappings = mappings or {}
        self.store = MixedModuleStore(
            contentstore, create_modulestore_instance=create_modulestore_instance,
            mappings=mappings,
            **self.options
        )
        self.addCleanup(self.store.close_all_connections)

    def initdb(self, default):
        """
        Initialize the database and create one test course in it
        """
        # set the default modulestore
        store_configs = self.options['stores']
        for index in range(len(store_configs)):  # lint-amnesty, pylint: disable=consider-using-enumerate
            if store_configs[index]['NAME'] == default:
                if index > 0:
                    store_configs[index], store_configs[0] = store_configs[0], store_configs[index]
                break
        self._initialize_mixed()

        test_course_key = CourseLocator.from_string(self.MONGO_COURSEID)
        test_course_key = test_course_key.make_usage_key('course', test_course_key.run).course_key
        self.fake_location = self.store.make_course_key(
            test_course_key.org,
            test_course_key.course,
            test_course_key.run
        ).make_usage_key('vertical', 'fake')
        self._create_course(test_course_key)

        assert default == self.store.get_modulestore_type(self.course.id)


class AsideFoo(XBlockAside):
    """
    Test xblock aside class
    """
    FRAG_CONTENT = "<p>Aside Foo rendered</p>"

    field11 = String(default="aside1_default_value1", scope=Scope.content)
    field12 = String(default="aside1_default_value2", scope=Scope.settings)

    @XBlockAside.aside_for('student_view')
    def student_view_aside(self, block, context):  # pylint: disable=unused-argument
        """Add to the student view"""
        return Fragment(self.FRAG_CONTENT)


class AsideBar(XBlockAside):
    """
    Test xblock aside class
    """
    FRAG_CONTENT = "<p>Aside Bar rendered</p>"

    field21 = String(default="aside2_default_value1", scope=Scope.content)
    field22 = String(default="aside2_default_value2", scope=Scope.settings)

    @XBlockAside.aside_for('student_view')
    def student_view_aside(self, block, context):  # pylint: disable=unused-argument
        """Add to the student view"""
        return Fragment(self.FRAG_CONTENT)


@ddt.ddt
@attr('mongo')
class TestMixedModuleStore(CommonMixedModuleStoreSetup):
    """
    Tests of the MixedModulestore interface methods.
    """
    @ddt.data(ModuleStoreEnum.Type.split)
    def test_get_modulestore_type(self, default_ms):
        """
        Make sure we get back the store type we expect for given mappings
        """
        self.initdb(default_ms)
        assert self.store.get_modulestore_type(self._course_key_from_string(self.MONGO_COURSEID)) == default_ms
        # try an unknown mapping, it should be the 'default' store
        assert self.store.get_modulestore_type(CourseKey.from_string('foo/bar/2012_Fall')) == default_ms

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_get_modulestore_cache(self, default_ms):
        """
        Make sure we cache discovered course mappings
        """
        self.initdb(default_ms)
        # unset mappings
        self.store.mappings = {}
        course_key = self.course_locations[self.MONGO_COURSEID].course_key
        with check_exact_number_of_calls(self.store.default_modulestore, 'has_course', 1):
            assert self.store.default_modulestore == self.store._get_modulestore_for_courselike(course_key)  # pylint: disable=protected-access, line-too-long
            assert course_key in self.store.mappings
            assert self.store.default_modulestore == self.store._get_modulestore_for_courselike(course_key)  # pylint: disable=protected-access, line-too-long

    @ddt.data(*itertools.product(
        (ModuleStoreEnum.Type.split,),
        (True, False)
    ))
    @ddt.unpack
    def test_duplicate_course_error(self, default_ms, reset_mixed_mappings):
        """
        Make sure we get back the store type we expect for given mappings
        """
        self._initialize_mixed(mappings={})
        with self.store.default_store(default_ms):
            self.store.create_course('org_x', 'course_y', 'run_z', self.user_id)
            if reset_mixed_mappings:
                self.store.mappings = {}
            with pytest.raises(DuplicateCourseError):
                self.store.create_course('org_x', 'course_y', 'run_z', self.user_id)

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_duplicate_course_error_with_different_case_ids(self, default_store):
        """
        Verify that course can not be created with same course_id with different case.
        """
        self._initialize_mixed(mappings={})
        with self.store.default_store(default_store):
            self.store.create_course('org_x', 'course_y', 'run_z', self.user_id)

            with pytest.raises(DuplicateCourseError):
                self.store.create_course('ORG_X', 'COURSE_Y', 'RUN_Z', self.user_id)

    # split: has one lookup for the course and then one for the course items
    #    but the active_versions check is done in MySQL
    @ddt.data((ModuleStoreEnum.Type.split, [1, 1], 0))
    @ddt.unpack
    def test_has_item(self, default_ms, max_find, max_send):
        self.initdb(default_ms)
        self._create_block_hierarchy()

        with check_mongo_calls(max_find.pop(0), max_send):
            assert self.store.has_item(self.problem_x1a_1)  # lint-amnesty, pylint: disable=no-member

        # try negative cases
        with check_mongo_calls(max_find.pop(0), max_send):
            assert not self.store.has_item(self.fake_location)

        # verify that an error is raised when the revision is not valid
        with pytest.raises(UnsupportedRevisionError):
            self.store.has_item(self.fake_location, revision=ModuleStoreEnum.RevisionOption.draft_preferred)

    # split:
    #   problem: active_versions, structure
    #   non-existent problem: ditto
    @ddt.data((ModuleStoreEnum.Type.split, [1, 0], [1, 1], 0))
    @ddt.unpack
    def test_get_item(self, default_ms, num_mysql, max_find, max_send):
        self.initdb(default_ms)
        self._create_block_hierarchy()

        with check_mongo_calls(max_find.pop(0), max_send), self.assertNumQueries(num_mysql.pop(0)):
            assert self.store.get_item(self.problem_x1a_1) is not None  # lint-amnesty, pylint: disable=no-member

        # try negative cases
        with check_mongo_calls(max_find.pop(0), max_send), self.assertNumQueries(num_mysql.pop(0)):
            with pytest.raises(ItemNotFoundError):
                self.store.get_item(self.fake_location)

        # verify that an error is raised when the revision is not valid
        with pytest.raises(UnsupportedRevisionError):
            self.store.get_item(self.fake_location, revision=ModuleStoreEnum.RevisionOption.draft_preferred)

    # Split:
    #    mysql: fetch course's active version from SplitModulestoreCourseIndex, spurious refetch x2
    #    find: get structure
    @ddt.data((ModuleStoreEnum.Type.split, 2, 1, 0))
    @ddt.unpack
    def test_get_items(self, default_ms, num_mysql, max_find, max_send):
        self.initdb(default_ms)
        self._create_block_hierarchy()

        course_locn = self.course_locations[self.MONGO_COURSEID]
        with check_mongo_calls(max_find, max_send), self.assertNumQueries(num_mysql):
            blocks = self.store.get_items(course_locn.course_key, qualifiers={'category': 'problem'})
        assert len(blocks) == 6

        # verify that an error is raised when the revision is not valid
        with pytest.raises(UnsupportedRevisionError):
            self.store.get_items(
                self.course_locations[self.MONGO_COURSEID].course_key,
                revision=ModuleStoreEnum.RevisionOption.draft_preferred
            )

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_course_version_on_block(self, default_ms):
        self.initdb(default_ms)
        self._create_block_hierarchy()

        course = self.store.get_course(self.course.id)
        course_version = course.course_version

        if default_ms == ModuleStoreEnum.Type.split:
            assert course_version is not None
        else:
            assert course_version is None

        blocks = self.store.get_items(self.course.id, qualifiers={'category': 'problem'})
        blocks.append(self.store.get_item(self.problem_x1a_1))  # lint-amnesty, pylint: disable=no-member
        assert len(blocks) == 7
        for block in blocks:
            assert block.course_version == course_version
            # ensure that when the block is retrieved from the runtime cache,
            # the course version is still present
            cached_block = course.runtime.get_block(block.location)
            assert cached_block.course_version == block.course_version

    @ddt.data((ModuleStoreEnum.Type.split, 2, False))
    @ddt.unpack
    def test_get_items_include_orphans(self, default_ms, expected_items_in_tree, orphan_in_items):
        """
        Test `include_orphans` option helps in returning only those items which are present in course tree.
        It tests that orphans are not fetched when calling `get_item` with `include_orphans`.

        Params:
            expected_items_in_tree:
                Number of items that will be returned after `get_items` would be called with `include_orphans`.
                In split, it would not get orphan items.
                In mongo, it would still get orphan items because `include_orphans` would not have any impact on mongo
                    modulestore which will return same number of items as called without `include_orphans` kwarg.

            orphan_in_items:
                When `get_items` is called with `include_orphans` kwarg, then check if an orphan is returned or not.
                False when called in split modulestore because in split get_items is expected to not retrieve orphans
                    now because of `include_orphans`.
                True when called in mongo modulstore because `include_orphans` does not have any effect on mongo.
        """
        self.initdb(default_ms)

        test_course = self.store.create_course('testx', 'GreekHero', 'test_run', self.user_id)
        course_key = test_course.id

        items = self.store.get_items(course_key)
        # Check items found are either course or about type
        assert {'course', 'about'}.issubset({item.location.block_type for item in items})  # pylint: disable=line-too-long
        # Assert that about is a detached category found in get_items
        assert [item.location.block_type for item in items if item.location.block_type == 'about'][0]\
            in DETACHED_XBLOCK_TYPES
        assert len(items) == 2

        # Check that orphans are not found
        orphans = self.store.get_orphans(course_key)
        assert len(orphans) == 0

        # Add an orphan to test course
        orphan = course_key.make_usage_key('chapter', 'OrphanChapter')
        self.store.create_item(self.user_id, orphan.course_key, orphan.block_type, block_id=orphan.block_id)

        # Check that now an orphan is found
        orphans = self.store.get_orphans(course_key)
        assert orphan in orphans
        assert len(orphans) == 1

        # Check now `get_items` retrieves an extra item added above which is an orphan.
        items = self.store.get_items(course_key)
        assert orphan in [item.location for item in items]
        assert len(items) == 3

        # Check now `get_items` with `include_orphans` kwarg does not retrieves an orphan block.
        items_in_tree = self.store.get_items(course_key, include_orphans=False)

        # Check that course and about blocks are found in get_items
        assert {'course', 'about'}.issubset({item.location.block_type for item in items_in_tree})
        # Check orphan is found or not - this is based on mongo/split modulestore. It should be found in mongo.
        assert (orphan in [item.location for item in items_in_tree]) == orphan_in_items
        assert len(items_in_tree) == expected_items_in_tree

    # split:
    #    mysql: SplitModulestoreCourseIndex - select 2x (by course_id, by objectid), update, update historical record,
    #           check CONTENT_TAGGING_AUTO CourseWaffleFlag
    #    find: definitions (calculator field), structures
    #    sends: 2 sends to update index & structure (note, it would also be definition if a content field changed)
    @ddt.data((ModuleStoreEnum.Type.split, 4, 2, 2))
    @ddt.unpack
    def test_update_item(self, default_ms, num_mysql, max_find, max_send):
        """
        Update should succeed for r/w dbs
        """
        self.initdb(default_ms)
        self._create_block_hierarchy()
        problem = self.store.get_item(self.problem_x1a_1)  # lint-amnesty, pylint: disable=no-member
        # if following raised, then the test is really a noop, change it
        assert problem.max_attempts != 2, 'Default changed making test meaningless'
        problem.max_attempts = 2
        with check_mongo_calls(max_find, max_send), self.assertNumQueries(num_mysql):
            problem = self.store.update_item(problem, self.user_id)

        assert problem.max_attempts == 2, "Update didn't persist"

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_has_changes_direct_only(self, default_ms):
        """
        Tests that has_changes() returns false when a new xblock in a direct only category is checked
        """
        self.initdb(default_ms)

        test_course = self.store.create_course('testx', 'GreekHero', 'test_run', self.user_id)

        # Create dummy direct only xblocks
        chapter = self.store.create_item(
            self.user_id,
            test_course.id,
            'chapter',
            block_id='vertical_container'
        )

        # Check that neither xblock has changes
        assert not self.store.has_changes(test_course)
        assert not self.store.has_changes(chapter)

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_has_changes(self, default_ms):
        """
        Tests that has_changes() only returns true when changes are present
        """
        self.initdb(default_ms)

        test_course = self.store.create_course('testx', 'GreekHero', 'test_run', self.user_id)

        # Create a dummy component to test against
        xblock = self.store.create_item(
            self.user_id,
            test_course.id,
            'vertical',
            block_id='test_vertical'
        )

        # Not yet published, so changes are present
        assert self.store.has_changes(xblock)

        # Publish and verify that there are no unpublished changes
        newXBlock = self.store.publish(xblock.location, self.user_id)
        assert not self.store.has_changes(newXBlock)

        # Change the component, then check that there now are changes
        component = self.store.get_item(xblock.location)
        component.display_name = 'Changed Display Name'

        component = self.store.update_item(component, self.user_id)
        assert self.store.has_changes(component)

        # Publish and verify again
        component = self.store.publish(component.location, self.user_id)
        assert not self.store.has_changes(component)

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_unit_stuck_in_draft_mode(self, default_ms):
        """
        After revert_to_published() the has_changes() should return false if draft has no changes
        """
        self.initdb(default_ms)

        test_course = self.store.create_course('testx', 'GreekHero', 'test_run', self.user_id)

        # Create a dummy component to test against
        xblock = self.store.create_item(
            self.user_id,
            test_course.id,
            'vertical',
            block_id='test_vertical'
        )

        # Not yet published, so changes are present
        assert self.store.has_changes(xblock)

        # Publish and verify that there are no unpublished changes
        component = self.store.publish(xblock.location, self.user_id)
        assert not self.store.has_changes(component)

        self.store.revert_to_published(component.location, self.user_id)
        component = self.store.get_item(component.location)
        assert not self.store.has_changes(component)

        # Publish and verify again
        component = self.store.publish(component.location, self.user_id)
        assert not self.store.has_changes(component)

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_unit_stuck_in_published_mode(self, default_ms):
        """
        After revert_to_published() the has_changes() should return true if draft has changes
        """
        self.initdb(default_ms)

        test_course = self.store.create_course('testx', 'GreekHero', 'test_run', self.user_id)

        # Create a dummy component to test against
        xblock = self.store.create_item(
            self.user_id,
            test_course.id,
            'vertical',
            block_id='test_vertical'
        )

        # Not yet published, so changes are present
        assert self.store.has_changes(xblock)

        # Publish and verify that there are no unpublished changes
        component = self.store.publish(xblock.location, self.user_id)
        assert not self.store.has_changes(component)

        # Discard changes and verify that there are no changes
        self.store.revert_to_published(component.location, self.user_id)
        component = self.store.get_item(component.location)
        assert not self.store.has_changes(component)

        # Change the component, then check that there now are changes
        component = self.store.get_item(component.location)
        component.display_name = 'Changed Display Name'
        self.store.update_item(component, self.user_id)

        # Verify that changes are present
        assert self.store.has_changes(component)

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_unit_stuck_in_published_mode_after_delete(self, default_ms):
        """
        Test that a unit does not get stuck in published mode
        after discarding a component changes and deleting a component
        """
        self.initdb(default_ms)

        test_course = self.store.create_course('testx', 'GreekHero', 'test_run', self.user_id)

        # Create a dummy vertical & html component to test against
        vertical = self.store.create_item(
            self.user_id,
            test_course.id,
            'vertical',
            block_id='test_vertical'
        )
        component = self.store.create_child(
            self.user_id,
            vertical.location,
            'html',
            block_id='html_component'
        )

        # publish vertical changes
        self.store.publish(vertical.location, self.user_id)
        assert not self._has_changes(vertical.location)

        # Change a component, then check that there now are changes
        component = self.store.get_item(component.location)
        component.display_name = 'Changed Display Name'
        self.store.update_item(component, self.user_id)
        assert self._has_changes(vertical.location)

        # Discard changes and verify that there are no changes
        self.store.revert_to_published(vertical.location, self.user_id)
        assert not self._has_changes(vertical.location)

        # Delete the component and verify that the unit has changes
        self.store.delete_item(component.location, self.user_id)
        vertical = self.store.get_item(vertical.location)
        assert self._has_changes(vertical.location)

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_publish_automatically_after_delete_unit(self, default_ms):
        """
        Check that sequential publishes automatically after deleting a unit
        """
        self.initdb(default_ms)

        test_course = self.store.create_course('test_org', 'test_course', 'test_run', self.user_id)

        # create sequential and vertical to test against
        sequential = self.store.create_child(self.user_id, test_course.location, 'sequential', 'test_sequential')
        vertical = self.store.create_child(self.user_id, sequential.location, 'vertical', 'test_vertical')

        # publish sequential changes
        self.store.publish(sequential.location, self.user_id)
        assert not self._has_changes(sequential.location)

        # delete vertical and check sequential has no changes
        self.store.delete_item(vertical.location, self.user_id)
        assert not self._has_changes(sequential.location)

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_course_create_event(self, default_ms):
        """
        Check that COURSE_CREATED event is sent when a course is created.
        """
        self.initdb(default_ms)

        event_receiver = Mock()
        COURSE_CREATED.connect(event_receiver)

        test_course = self.store.create_course('test_org', 'test_course', 'test_run', self.user_id)

        event_receiver.assert_called()

        assert_dict_contains_subset(
            self,
            {
                "signal": COURSE_CREATED,
                "sender": None,
                "course": CourseData(
                    course_key=test_course.id,
                ),
            },
            event_receiver.call_args.kwargs,
        )

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_xblock_create_event(self, default_ms):
        """
        Check that XBLOCK_CREATED event is sent when xblock is created.
        """
        self.initdb(default_ms)

        event_receiver = Mock()
        XBLOCK_CREATED.connect(event_receiver)

        test_course = self.store.create_course('test_org', 'test_course', 'test_run', self.user_id)

        # create sequential to test against
        sequential = self.store.create_child(self.user_id, test_course.location, 'sequential', 'test_sequential')

        event_receiver.assert_called()

        assert event_receiver.call_args.kwargs['signal'] == XBLOCK_CREATED
        assert event_receiver.call_args.kwargs['xblock_info'].usage_key == sequential.location
        assert event_receiver.call_args.kwargs['xblock_info'].block_type == sequential.location.block_type
        assert event_receiver.call_args.kwargs['xblock_info'].version.for_branch(None) == sequential.location

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_xblock_update_event(self, default_ms):
        """
        Check that XBLOCK_UPDATED event is sent when xblock is updated.
        """
        self.initdb(default_ms)

        event_receiver = Mock()
        XBLOCK_UPDATED.connect(event_receiver)

        test_course = self.store.create_course('test_org', 'test_course', 'test_run', self.user_id)

        # create sequential to test against
        sequential = self.store.create_child(self.user_id, test_course.location, 'sequential', 'test_sequential')

        # Change the xblock
        sequential.display_name = 'Updated Display Name'
        self.store.update_item(sequential, user_id=self.user_id)

        event_receiver.assert_called()

        assert event_receiver.call_args.kwargs['signal'] == XBLOCK_UPDATED
        assert event_receiver.call_args.kwargs['xblock_info'].usage_key == sequential.location
        assert event_receiver.call_args.kwargs['xblock_info'].block_type == sequential.location.block_type
        assert event_receiver.call_args.kwargs['xblock_info'].version.for_branch(None) == sequential.location

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_xblock_publish_event(self, default_ms):
        """
        Check that XBLOCK_PUBLISHED event is sent when xblock is published.
        """
        self.initdb(default_ms)
        event_receiver = Mock()
        XBLOCK_PUBLISHED.connect(event_receiver)

        test_course = self.store.create_course('test_org', 'test_course', 'test_run', self.user_id)

        # create sequential and vertical to test against
        sequential = self.store.create_child(self.user_id, test_course.location, 'sequential', 'test_sequential')
        self.store.create_child(self.user_id, sequential.location, 'vertical', 'test_vertical')

        # publish sequential changes
        self.store.publish(sequential.location, self.user_id)

        event_receiver.assert_called()
        assert_dict_contains_subset(
            self,
            {
                "signal": XBLOCK_PUBLISHED,
                "sender": None,
                "xblock_info": XBlockData(
                    usage_key=sequential.location,
                    block_type=sequential.location.block_type,
                ),
            },
            event_receiver.call_args.kwargs,
        )

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_xblock_delete_event(self, default_ms):
        """
        Check that XBLOCK_DELETED event is sent when xblock is deleted.
        """
        self.initdb(default_ms)
        event_receiver = Mock()
        XBLOCK_DELETED.connect(event_receiver)

        test_course = self.store.create_course('test_org', 'test_course', 'test_run', self.user_id)

        # create sequential and vertical to test against
        sequential = self.store.create_child(self.user_id, test_course.location, 'sequential', 'test_sequential')
        vertical = self.store.create_child(self.user_id, sequential.location, 'vertical', 'test_vertical')

        # publish sequential changes
        self.store.publish(sequential.location, self.user_id)

        # delete vertical
        self.store.delete_item(vertical.location, self.user_id)

        event_receiver.assert_called()
        assert_dict_contains_subset(
            self,
            {
                "signal": XBLOCK_DELETED,
                "sender": None,
                "xblock_info": XBlockData(
                    usage_key=vertical.location,
                    block_type=vertical.location.block_type,
                ),
            },
            event_receiver.call_args.kwargs,
        )

    def setup_has_changes(self, default_ms):
        """
        Common set up for has_changes tests below.
        Returns a dictionary of useful location maps for testing.
        """
        self.initdb(default_ms)
        self._create_block_hierarchy()

        locations = {
            'grandparent': self.chapter_x,  # lint-amnesty, pylint: disable=no-member
            'parent_sibling': self.sequential_x2,  # lint-amnesty, pylint: disable=no-member
            'parent': self.sequential_x1,  # lint-amnesty, pylint: disable=no-member
            'child_sibling': self.vertical_x1b,  # lint-amnesty, pylint: disable=no-member
            'child': self.vertical_x1a,  # lint-amnesty, pylint: disable=no-member
        }

        # Publish the vertical units
        self.store.publish(locations['parent_sibling'], self.user_id)
        self.store.publish(locations['parent'], self.user_id)

        return locations

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_has_changes_ancestors(self, default_ms):
        """
        Tests that has_changes() returns true on ancestors when a child is changed
        """
        locations = self.setup_has_changes(default_ms)

        # Verify that there are no unpublished changes
        for key in locations:
            assert not self._has_changes(locations[key])

        # Change the child
        child = self.store.get_item(locations['child'])
        child.display_name = 'Changed Display Name'
        self.store.update_item(child, self.user_id)

        # All ancestors should have changes, but not siblings
        assert self._has_changes(locations['grandparent'])
        assert self._has_changes(locations['parent'])
        assert self._has_changes(locations['child'])
        assert not self._has_changes(locations['parent_sibling'])
        assert not self._has_changes(locations['child_sibling'])

        # Publish the unit with changes
        self.store.publish(locations['parent'], self.user_id)

        # Verify that there are no unpublished changes
        for key in locations:
            assert not self._has_changes(locations[key])

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_has_changes_publish_ancestors(self, default_ms):
        """
        Tests that has_changes() returns false after a child is published only if all children are unchanged
        """
        locations = self.setup_has_changes(default_ms)

        # Verify that there are no unpublished changes
        for key in locations:
            assert not self._has_changes(locations[key])

        # Change both children
        child = self.store.get_item(locations['child'])
        child_sibling = self.store.get_item(locations['child_sibling'])
        child.display_name = 'Changed Display Name'
        child_sibling.display_name = 'Changed Display Name'
        self.store.update_item(child, user_id=self.user_id)
        self.store.update_item(child_sibling, user_id=self.user_id)

        # Verify that ancestors have changes
        assert self._has_changes(locations['grandparent'])
        assert self._has_changes(locations['parent'])

        # Publish one child
        self.store.publish(locations['child_sibling'], self.user_id)

        # Verify that ancestors still have changes
        assert self._has_changes(locations['grandparent'])
        assert self._has_changes(locations['parent'])

        # Publish the other child
        self.store.publish(locations['child'], self.user_id)

        # Verify that ancestors now have no changes
        assert not self._has_changes(locations['grandparent'])
        assert not self._has_changes(locations['parent'])

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_has_changes_add_remove_child(self, default_ms):
        """
        Tests that has_changes() returns true for the parent when a child with changes is added
        and false when that child is removed.
        """
        locations = self.setup_has_changes(default_ms)

        # Test that the ancestors don't have changes
        assert not self._has_changes(locations['grandparent'])
        assert not self._has_changes(locations['parent'])

        # Create a new child and attach it to parent
        self.store.create_child(
            self.user_id,
            locations['parent'],
            'vertical',
            block_id='new_child',
        )

        # Verify that the ancestors now have changes
        assert self._has_changes(locations['grandparent'])
        assert self._has_changes(locations['parent'])

        # Remove the child from the parent
        parent = self.store.get_item(locations['parent'])
        parent.children = [locations['child'], locations['child_sibling']]
        self.store.update_item(parent, user_id=self.user_id)

        # Verify that ancestors now have no changes
        assert not self._has_changes(locations['grandparent'])
        assert not self._has_changes(locations['parent'])

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_has_changes_non_direct_only_children(self, default_ms):
        """
        Tests that has_changes() returns true after editing the child of a vertical (both not direct only categories).
        """
        self.initdb(default_ms)

        parent = self.store.create_item(
            self.user_id,
            self.course.id,
            'vertical',
            block_id='parent',
        )
        child = self.store.create_child(
            self.user_id,
            parent.location,
            'html',
            block_id='child',
        )
        self.store.publish(parent.location, self.user_id)

        # Verify that there are no changes
        assert not self._has_changes(parent.location)
        assert not self._has_changes(child.location)

        # Change the child
        child.display_name = 'Changed Display Name'
        self.store.update_item(child, user_id=self.user_id)

        # Verify that both parent and child have changes
        assert self._has_changes(parent.location)
        assert self._has_changes(child.location)

    @ddt.data(*itertools.product(
        (ModuleStoreEnum.Type.split,),
        (ModuleStoreEnum.Branch.draft_preferred, ModuleStoreEnum.Branch.published_only)
    ))
    @ddt.unpack
    def test_has_changes_missing_child(self, default_ms, default_branch):
        """
        Tests that has_changes() does not throw an exception when a child doesn't exist.
        """
        self.initdb(default_ms)

        with self.store.branch_setting(default_branch, self.course.id):
            # Create the parent and point it to a fake child
            parent = self.store.create_item(
                self.user_id,
                self.course.id,
                'vertical',
                block_id='parent',
            )
            parent.children += [self.course.id.make_usage_key('vertical', 'does_not_exist')]
            parent = self.store.update_item(parent, self.user_id)

            # Check the parent for changes should return True and not throw an exception
            assert self.store.has_changes(parent)

    # Split
    #   mysql: SplitModulestoreCourseIndex - select 2x (by course_id, by objectid), update, update historical record,
    #          check CONTENT_TAGGING_AUTO CourseWaffleFlag
    #   Find: active_versions, 2 structures (published & draft), definition (unnecessary)
    #   Sends: updated draft and published structures and active_versions
    @ddt.data((ModuleStoreEnum.Type.split, 5, 2, 3))
    @ddt.unpack
    def test_delete_item(self, default_ms, num_mysql, max_find, max_send):
        """
        Delete should reject on r/o db and work on r/w one
        """
        self.initdb(default_ms)
        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, self.writable_chapter_location.course_key):  # lint-amnesty, pylint: disable=line-too-long
            with check_mongo_calls(max_find, max_send), self.assertNumQueries(num_mysql):
                self.store.delete_item(self.writable_chapter_location, self.user_id)

            # verify it's gone
            with pytest.raises(ItemNotFoundError):
                self.store.get_item(self.writable_chapter_location)
        # verify it's gone from published too
        with pytest.raises(ItemNotFoundError):
            self.store.get_item(self.writable_chapter_location, revision=ModuleStoreEnum.RevisionOption.published_only)

    # Split:
    #    mysql: SplitModulestoreCourseIndex - select 2x (by course_id, by objectid), update, update historical record,
    #           check CONTENT_TAGGING_AUTO CourseWaffleFlag
    #    find: draft and published structures, definition (unnecessary)
    #    sends: update published (why?), draft, and active_versions
    @ddt.data((ModuleStoreEnum.Type.split, 5, 3, 3))
    @ddt.unpack
    def test_delete_private_vertical(self, default_ms, num_mysql, max_find, max_send):
        """
        Because old mongo treated verticals as the first layer which could be draft, it has some interesting
        behavioral properties which this deletion test gets at.
        """
        self.initdb(default_ms)
        # create and delete a private vertical with private children
        private_vert = self.store.create_child(
            # don't use course_location as it may not be the repr
            self.user_id, self.course_locations[self.MONGO_COURSEID],
            'vertical', block_id='private'
        )
        private_leaf = self.store.create_child(
            # don't use course_location as it may not be the repr
            self.user_id, private_vert.location, 'html', block_id='private_leaf'
        )

        # verify pre delete state (just to verify that the test is valid)
        if hasattr(private_vert.location, 'version_guid'):
            # change to the HEAD version
            vert_loc = private_vert.location.for_version(private_leaf.location.version_guid)
        else:
            vert_loc = private_vert.location
        assert self.store.has_item(vert_loc)
        assert self.store.has_item(private_leaf.location)
        course = self.store.get_course(self.course_locations[self.MONGO_COURSEID].course_key, 0)
        assert vert_loc in course.children

        # delete the vertical and ensure the course no longer points to it
        with check_mongo_calls(max_find, max_send), self.assertNumQueries(num_mysql):
            self.store.delete_item(vert_loc, self.user_id)
        course = self.store.get_course(self.course_locations[self.MONGO_COURSEID].course_key, 0)
        if hasattr(private_vert.location, 'version_guid'):
            # change to the HEAD version
            vert_loc = private_vert.location.for_version(course.location.version_guid)
            leaf_loc = private_leaf.location.for_version(course.location.version_guid)
        else:
            vert_loc = private_vert.location
            leaf_loc = private_leaf.location
        assert not self.store.has_item(vert_loc)
        assert not self.store.has_item(leaf_loc)
        assert vert_loc not in course.children

    # Split:
    #   mysql: SplitModulestoreCourseIndex - select 2x (by course_id, by objectid), update, update historical record,
    #          check CONTENT_TAGGING_AUTO CourseWaffleFlag
    #   find: structure (cached)
    #   send: update structure and active_versions
    @ddt.data((ModuleStoreEnum.Type.split, 5, 1, 2))
    @ddt.unpack
    def test_delete_draft_vertical(self, default_ms, num_mysql, max_find, max_send):
        """
        Test deleting a draft vertical which has a published version.
        """
        self.initdb(default_ms)

        # reproduce bug STUD-1965
        # create and delete a private vertical with private children
        private_vert = self.store.create_child(
            # don't use course_location as it may not be the repr
            self.user_id, self.course_locations[self.MONGO_COURSEID], 'vertical', block_id='publish'
        )
        private_leaf = self.store.create_child(
            self.user_id, private_vert.location, 'html', block_id='bug_leaf'
        )

        # verify that an error is raised when the revision is not valid
        with pytest.raises(UnsupportedRevisionError):
            self.store.delete_item(
                private_leaf.location,
                self.user_id,
                revision=ModuleStoreEnum.RevisionOption.draft_preferred
            )

        self.store.publish(private_vert.location, self.user_id)
        private_leaf.display_name = 'change me'
        private_leaf = self.store.update_item(private_leaf, self.user_id)
        # test succeeds if delete succeeds w/o error
        with check_mongo_calls(max_find, max_send), self.assertNumQueries(num_mysql):
            self.store.delete_item(private_leaf.location, self.user_id)

    # Split:
    #   mysql: 3 selects on SplitModulestoreCourseIndex - 1 to get all courses, 2 to get specific course (this query is
    #          executed twice, possibly unnecessarily)
    #   find: 2 reads of structure, definition (s/b lazy; so, unnecessary),
    #         plus 1 wildcard find in draft mongo which has none
    @ddt.data((ModuleStoreEnum.Type.split, 2, 3, 0))
    @ddt.unpack
    def test_get_courses(self, default_ms, num_mysql, max_find, max_send):
        self.initdb(default_ms)
        # we should have one course across all stores
        with check_mongo_calls(max_find, max_send), self.assertNumQueries(num_mysql):
            courses = self.store.get_courses()
            course_ids = [course.location for course in courses]
            assert len(courses) == 1, f'Not one course: {course_ids}'
            assert self.course_locations[self.MONGO_COURSEID] in course_ids

        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
            draft_courses = self.store.get_courses(remove_branch=True)
        with self.store.branch_setting(ModuleStoreEnum.Branch.published_only):
            published_courses = self.store.get_courses(remove_branch=True)
        assert [c.id for c in draft_courses] == [c.id for c in published_courses]

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_create_child_detached_tabs(self, default_ms):
        """
        test 'create_child' method with a detached category ('static_tab')
        to check that new static tab is not a direct child of the course
        """
        self.initdb(default_ms)
        mongo_course = self.store.get_course(self.course_locations[self.MONGO_COURSEID].course_key)
        assert len(mongo_course.children) == 1

        # create a static tab of the course
        self.store.create_child(
            self.user_id,
            self.course.location,
            'static_tab'
        )

        # now check that the course has same number of children
        mongo_course = self.store.get_course(self.course_locations[self.MONGO_COURSEID].course_key)
        assert len(mongo_course.children) == 1

    # split: active_versions (mysql), structure, definition (to load course wiki string)
    @ddt.data((ModuleStoreEnum.Type.split, 1, 2, 0))
    @ddt.unpack
    def test_get_course(self, default_ms, num_mysql, max_find, max_send):
        """
        This test is here for the performance comparison not functionality. It tests the performance
        of getting an item whose scope.content fields are looked at.
        """
        self.initdb(default_ms)
        with check_mongo_calls(max_find, max_send), self.assertNumQueries(num_mysql):
            course = self.store.get_item(self.course_locations[self.MONGO_COURSEID])
            assert course.id == self.course_locations[self.MONGO_COURSEID].course_key

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_get_library(self, default_ms):
        """
        Test that create_library and get_library work regardless of the default modulestore.
        Other tests of MixedModulestore support are in test_libraries.py but this one must
        be done here so we can test the configuration where Draft/old is the first modulestore.
        """
        self.initdb(default_ms)
        with self.store.default_store(ModuleStoreEnum.Type.split):  # The CMS also wraps create_library like this
            library = self.store.create_library("org", "lib", self.user_id, {"display_name": "Test Library"})
        library_key = library.location.library_key
        assert isinstance(library_key, LibraryLocator)
        # Now load with get_library and make sure it works:
        library = self.store.get_library(library_key)
        assert library.location.library_key == library_key

        # Clear the mappings so we can test get_library code path without mapping set:
        self.store.mappings.clear()
        library = self.store.get_library(library_key)
        assert library.location.library_key == library_key

    # notice this doesn't test getting a public item via draft_preferred which draft would have 2 hits (split
    # still only 2)
    # Split: active_versions, structure
    @ddt.data((ModuleStoreEnum.Type.split, 1, 1, 0))
    @ddt.unpack
    def test_get_parent_locations(self, default_ms, num_mysql, max_find, max_send):
        """
        Test a simple get parent for a direct only category (i.e, always published)
        """
        self.initdb(default_ms)
        self._create_block_hierarchy()

        with check_mongo_calls(max_find, max_send), self.assertNumQueries(num_mysql):
            parent = self.store.get_parent_location(self.problem_x1a_1)  # lint-amnesty, pylint: disable=no-member
            assert parent == self.vertical_x1a  # lint-amnesty, pylint: disable=no-member

    def verify_get_parent_locations_results(self, expected_results):
        """
        Verifies the results of calling get_parent_locations matches expected_results.
        """
        for child_location, parent_location, revision in expected_results:
            assert parent_location.for_branch(None) if parent_location else parent_location == \
                self.store.get_parent_location(child_location, revision=revision)

    def verify_item_parent(self, item_location, expected_parent_location, old_parent_location, is_reverted=False):
        """
        Verifies that item is placed under expected parent.

        Arguments:
            item_location (BlockUsageLocator)    : Locator of item.
            expected_parent_location (BlockUsageLocator)  : Expected parent block locator.
            old_parent_location (BlockUsageLocator)  : Old parent block locator.
            is_reverted (Boolean)   : A flag to notify that item was reverted.
        """
        with self.store.bulk_operations(self.course.id):
            source_item = self.store.get_item(item_location)
            old_parent = self.store.get_item(old_parent_location)
            expected_parent = self.store.get_item(expected_parent_location)

            assert expected_parent_location == source_item.get_parent().location

            # If an item is reverted, it means it's actual parent was the one that is the current parent now
            # i.e expected_parent_location otherwise old_parent_location.
            published_parent_location = expected_parent_location if is_reverted else old_parent_location

            # Check parent locations wrt branches
            with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
                assert expected_parent_location == self.store.get_item(item_location).get_parent().location
            with self.store.branch_setting(ModuleStoreEnum.Branch.published_only):
                assert published_parent_location == self.store.get_item(item_location).get_parent().location

            # Make location specific to published branch for verify_get_parent_locations_results call.
            published_parent_location = published_parent_location.for_branch(ModuleStoreEnum.BranchName.published)

            # Verify expected item parent locations
            self.verify_get_parent_locations_results([
                (item_location, expected_parent_location, None),
                (item_location, expected_parent_location, ModuleStoreEnum.RevisionOption.draft_preferred),
                (item_location, published_parent_location, ModuleStoreEnum.RevisionOption.published_only),
            ])

            # Also verify item.parent has correct parent location set.
            assert source_item.parent == expected_parent_location
            assert source_item.parent == self.store.get_parent_location(item_location)

            # Item should be present in new parent's children list but not in old parent's children list.
            assert item_location in expected_parent.children
            assert item_location not in old_parent.children

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_update_item_parent(self, store_type):
        """
        Test that when we move an item from old to new parent, the item should be present in new parent.
        """
        self.initdb(store_type)
        self._create_block_hierarchy()

        # Publish the course.
        self.course = self.store.publish(self.course.location, self.user_id)  # lint-amnesty, pylint: disable=attribute-defined-outside-init

        # Move child problem_x1a_1 to vertical_y1a.
        item_location = self.problem_x1a_1  # lint-amnesty, pylint: disable=no-member
        new_parent_location = self.vertical_y1a  # lint-amnesty, pylint: disable=no-member
        old_parent_location = self.vertical_x1a  # lint-amnesty, pylint: disable=no-member
        updated_item_location = self.store.update_item_parent(
            item_location, new_parent_location, old_parent_location, self.user_id
        )
        assert updated_item_location == item_location

        self.verify_item_parent(
            item_location=item_location,
            expected_parent_location=new_parent_location,
            old_parent_location=old_parent_location
        )

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_move_revert(self, store_type):
        """
        Test that when we move an item to new parent and then discard the original parent, the item should be present
        back in original parent.
        """
        self.initdb(store_type)
        self._create_block_hierarchy()

        # Publish the course
        self.course = self.store.publish(self.course.location, self.user_id)  # lint-amnesty, pylint: disable=attribute-defined-outside-init

        # Move child problem_x1a_1 to vertical_y1a.
        item_location = self.problem_x1a_1  # lint-amnesty, pylint: disable=no-member
        new_parent_location = self.vertical_y1a  # lint-amnesty, pylint: disable=no-member
        old_parent_location = self.vertical_x1a  # lint-amnesty, pylint: disable=no-member
        updated_item_location = self.store.update_item_parent(
            item_location, new_parent_location, old_parent_location, self.user_id
        )
        assert updated_item_location == item_location

        self.verify_item_parent(
            item_location=item_location,
            expected_parent_location=new_parent_location,
            old_parent_location=old_parent_location
        )

        # Now discard changes in old_parent_location i.e original parent.
        self.store.revert_to_published(old_parent_location, self.user_id)

        self.verify_item_parent(
            item_location=item_location,
            expected_parent_location=old_parent_location,
            old_parent_location=new_parent_location,
            is_reverted=True
        )

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_move_delete_revert(self, store_type):
        """
        Test that when we move an item and delete it and then discard changes for original parent, item should be
        present back in original parent.
        """
        self.initdb(store_type)
        self._create_block_hierarchy()

        # Publish the course
        self.course = self.store.publish(self.course.location, self.user_id)  # lint-amnesty, pylint: disable=attribute-defined-outside-init

        # Move child problem_x1a_1 to vertical_y1a.
        item_location = self.problem_x1a_1  # lint-amnesty, pylint: disable=no-member
        new_parent_location = self.vertical_y1a  # lint-amnesty, pylint: disable=no-member
        old_parent_location = self.vertical_x1a  # lint-amnesty, pylint: disable=no-member
        updated_item_location = self.store.update_item_parent(
            item_location, new_parent_location, old_parent_location, self.user_id
        )
        assert updated_item_location == item_location

        self.verify_item_parent(
            item_location=item_location,
            expected_parent_location=new_parent_location,
            old_parent_location=old_parent_location
        )

        # Now delete the item.
        self.store.delete_item(item_location, self.user_id)

        # Now discard changes in old_parent_location i.e original parent.
        self.store.revert_to_published(old_parent_location, self.user_id)

        self.verify_item_parent(
            item_location=item_location,
            expected_parent_location=old_parent_location,
            old_parent_location=new_parent_location,
            is_reverted=True
        )

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_move_revert_move(self, store_type):
        """
        Test that when we move an item to new parent and discard changes for the old parent, then the item should be
        present in the old parent and then moving an item from old parent to new parent should place that item under
        new parent.
        """
        self.initdb(store_type)
        self._create_block_hierarchy()

        # Publish the course
        self.course = self.store.publish(self.course.location, self.user_id)  # lint-amnesty, pylint: disable=attribute-defined-outside-init

        # Move child problem_x1a_1 to vertical_y1a.
        item_location = self.problem_x1a_1  # lint-amnesty, pylint: disable=no-member
        new_parent_location = self.vertical_y1a  # lint-amnesty, pylint: disable=no-member
        old_parent_location = self.vertical_x1a  # lint-amnesty, pylint: disable=no-member
        updated_item_location = self.store.update_item_parent(
            item_location, new_parent_location, old_parent_location, self.user_id
        )
        assert updated_item_location == item_location

        self.verify_item_parent(
            item_location=item_location,
            expected_parent_location=new_parent_location,
            old_parent_location=old_parent_location
        )

        # Now discard changes in old_parent_location i.e original parent.
        self.store.revert_to_published(old_parent_location, self.user_id)

        self.verify_item_parent(
            item_location=item_location,
            expected_parent_location=old_parent_location,
            old_parent_location=new_parent_location,
            is_reverted=True
        )

        # Again try to move from x1 to y1
        updated_item_location = self.store.update_item_parent(
            item_location, new_parent_location, old_parent_location, self.user_id
        )
        assert updated_item_location == item_location

        self.verify_item_parent(
            item_location=item_location,
            expected_parent_location=new_parent_location,
            old_parent_location=old_parent_location
        )

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_move_edited_revert(self, store_type):
        """
        Test that when we move an edited item from old parent to new parent and then discard changes in old parent,
        item should be placed under original parent with initial state.
        """
        self.initdb(store_type)
        self._create_block_hierarchy()

        # Publish the course.
        self.course = self.store.publish(self.course.location, self.user_id)  # lint-amnesty, pylint: disable=attribute-defined-outside-init

        # Move child problem_x1a_1 to vertical_y1a.
        item_location = self.problem_x1a_1  # lint-amnesty, pylint: disable=no-member
        new_parent_location = self.vertical_y1a  # lint-amnesty, pylint: disable=no-member
        old_parent_location = self.vertical_x1a  # lint-amnesty, pylint: disable=no-member

        problem = self.store.get_item(self.problem_x1a_1)  # lint-amnesty, pylint: disable=no-member
        orig_display_name = problem.display_name

        # Change display name of problem and update just it.
        problem.display_name = 'updated'
        self.store.update_item(problem, self.user_id)

        updated_problem = self.store.get_item(self.problem_x1a_1)  # lint-amnesty, pylint: disable=no-member
        assert updated_problem.display_name == 'updated'

        # Now, move from x1 to y1.
        updated_item_location = self.store.update_item_parent(
            item_location, new_parent_location, old_parent_location, self.user_id
        )
        assert updated_item_location == item_location

        self.verify_item_parent(
            item_location=item_location,
            expected_parent_location=new_parent_location,
            old_parent_location=old_parent_location
        )

        # Now discard changes in old_parent_location i.e original parent.
        self.store.revert_to_published(old_parent_location, self.user_id)

        # Check that problem has the original name back.
        reverted_problem = self.store.get_item(self.problem_x1a_1)  # lint-amnesty, pylint: disable=no-member
        assert orig_display_name == reverted_problem.display_name

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_move_1_moved_1_unchanged(self, store_type):
        """
        Test that when we move an item from an old parent which have multiple items then only moved item's parent
        is changed while other items are still present inside old parent.
        """
        self.initdb(store_type)
        self._create_block_hierarchy()

        # Create some children in vertical_x1a
        problem_item2 = self.store.create_child(self.user_id, self.vertical_x1a, 'problem', 'Problem_Item2')  # lint-amnesty, pylint: disable=no-member

        # Publish the course.
        self.course = self.store.publish(self.course.location, self.user_id)  # lint-amnesty, pylint: disable=attribute-defined-outside-init

        item_location = self.problem_x1a_1  # lint-amnesty, pylint: disable=no-member
        new_parent_location = self.vertical_y1a  # lint-amnesty, pylint: disable=no-member
        old_parent_location = self.vertical_x1a  # lint-amnesty, pylint: disable=no-member

        # Move problem_x1a_1 from x1 to y1.
        updated_item_location = self.store.update_item_parent(
            item_location, new_parent_location, old_parent_location, self.user_id
        )
        assert updated_item_location == item_location

        self.verify_item_parent(
            item_location=item_location,
            expected_parent_location=new_parent_location,
            old_parent_location=old_parent_location
        )

        # Check that problem_item2 is still present in vertical_x1a
        problem_item2 = self.store.get_item(problem_item2.location)
        assert problem_item2.parent == self.vertical_x1a  # lint-amnesty, pylint: disable=no-member
        assert problem_item2.location in problem_item2.get_parent().children

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_move_1_moved_1_edited(self, store_type):
        """
        Test that when we move an item inside an old parent having multiple items, we edit one item and move
        other item from old to new parent, then discard changes in old parent would discard the changes of the
        edited item and move back the moved item to old location.
        """
        self.initdb(store_type)
        self._create_block_hierarchy()

        # Create some children in vertical_x1a
        problem_item2 = self.store.create_child(self.user_id, self.vertical_x1a, 'problem', 'Problem_Item2')  # lint-amnesty, pylint: disable=no-member
        orig_display_name = problem_item2.display_name

        # Publish the course.
        self.course = self.store.publish(self.course.location, self.user_id)  # lint-amnesty, pylint: disable=attribute-defined-outside-init

        # Edit problem_item2.
        problem_item2.display_name = 'updated'
        self.store.update_item(problem_item2, self.user_id)

        updated_problem2 = self.store.get_item(problem_item2.location)
        assert updated_problem2.display_name == 'updated'

        item_location = self.problem_x1a_1  # lint-amnesty, pylint: disable=no-member
        new_parent_location = self.vertical_y1a  # lint-amnesty, pylint: disable=no-member
        old_parent_location = self.vertical_x1a  # lint-amnesty, pylint: disable=no-member

        # Move problem_x1a_1 from x1 to y1.
        updated_item_location = self.store.update_item_parent(
            item_location, new_parent_location, old_parent_location, self.user_id
        )
        assert updated_item_location == item_location

        self.verify_item_parent(
            item_location=item_location,
            expected_parent_location=new_parent_location,
            old_parent_location=old_parent_location
        )

        # Now discard changes in old_parent_location i.e original parent.
        self.store.revert_to_published(old_parent_location, self.user_id)

        # Check that problem_item2 has the original name back.
        reverted_problem2 = self.store.get_item(problem_item2.location)
        assert orig_display_name == reverted_problem2.display_name

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_move_1_moved_1_deleted(self, store_type):
        """
        Test that when we move an item inside an old parent having multiple items, we delete one item and move
        other item from old to new parent, then discard changes in old parent would undo delete the deleted
        item and move back the moved item to old location.
        """
        self.initdb(store_type)
        self._create_block_hierarchy()

        # Create some children in vertical_x1a
        problem_item2 = self.store.create_child(self.user_id, self.vertical_x1a, 'problem', 'Problem_Item2')  # lint-amnesty, pylint: disable=no-member
        orig_display_name = problem_item2.display_name  # lint-amnesty, pylint: disable=unused-variable

        # Publish the course.
        self.course = self.store.publish(self.course.location, self.user_id)  # lint-amnesty, pylint: disable=attribute-defined-outside-init

        # Now delete other problem problem_item2.
        self.store.delete_item(problem_item2.location, self.user_id)

        # Move child problem_x1a_1 to vertical_y1a.
        item_location = self.problem_x1a_1  # lint-amnesty, pylint: disable=no-member
        new_parent_location = self.vertical_y1a  # lint-amnesty, pylint: disable=no-member
        old_parent_location = self.vertical_x1a  # lint-amnesty, pylint: disable=no-member

        # Move problem_x1a_1 from x1 to y1.
        updated_item_location = self.store.update_item_parent(
            item_location, new_parent_location, old_parent_location, self.user_id
        )
        assert updated_item_location == item_location

        self.verify_item_parent(
            item_location=item_location,
            expected_parent_location=new_parent_location,
            old_parent_location=old_parent_location
        )

        # Now discard changes in old_parent_location i.e original parent.
        self.store.revert_to_published(old_parent_location, self.user_id)

        # Check that problem_item2 is also back in vertical_x1a
        problem_item2 = self.store.get_item(problem_item2.location)
        assert problem_item2.parent == self.vertical_x1a  # lint-amnesty, pylint: disable=no-member
        assert problem_item2.location in problem_item2.get_parent().children

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_get_parent_locations_moved_child(self, default_ms):
        self.initdb(default_ms)
        self._create_block_hierarchy()

        # publish the course
        self.course = self.store.publish(self.course.location, self.user_id)  # lint-amnesty, pylint: disable=attribute-defined-outside-init

        with self.store.bulk_operations(self.course.id):
            # make drafts of verticals
            self.store.convert_to_draft(self.vertical_x1a, self.user_id)  # lint-amnesty, pylint: disable=no-member
            self.store.convert_to_draft(self.vertical_y1a, self.user_id)  # lint-amnesty, pylint: disable=no-member

            # move child problem_x1a_1 to vertical_y1a
            child_to_move_location = self.problem_x1a_1  # lint-amnesty, pylint: disable=no-member
            new_parent_location = self.vertical_y1a  # lint-amnesty, pylint: disable=no-member
            old_parent_location = self.vertical_x1a  # lint-amnesty, pylint: disable=no-member

            with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
                old_parent = self.store.get_item(child_to_move_location).get_parent()

            assert old_parent_location == old_parent.location

            child_to_move_contextualized = child_to_move_location.map_into_course(old_parent.location.course_key)
            old_parent.children.remove(child_to_move_contextualized)
            self.store.update_item(old_parent, self.user_id)

            new_parent = self.store.get_item(new_parent_location)
            new_parent.children.append(child_to_move_location)
            self.store.update_item(new_parent, self.user_id)

            with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
                assert new_parent_location == self.store.get_item(child_to_move_location).get_parent().location
            with self.store.branch_setting(ModuleStoreEnum.Branch.published_only):
                assert old_parent_location == self.store.get_item(child_to_move_location).get_parent().location
            old_parent_published_location = old_parent_location.for_branch(ModuleStoreEnum.BranchName.published)
            self.verify_get_parent_locations_results([
                (child_to_move_location, new_parent_location, None),
                (child_to_move_location, new_parent_location, ModuleStoreEnum.RevisionOption.draft_preferred),
                (child_to_move_location, old_parent_published_location, ModuleStoreEnum.RevisionOption.published_only),
            ])

        # publish the course again
        self.store.publish(self.course.location, self.user_id)
        new_parent_published_location = new_parent_location.for_branch(ModuleStoreEnum.BranchName.published)
        self.verify_get_parent_locations_results([
            (child_to_move_location, new_parent_location, None),
            (child_to_move_location, new_parent_location, ModuleStoreEnum.RevisionOption.draft_preferred),
            (child_to_move_location, new_parent_published_location, ModuleStoreEnum.RevisionOption.published_only),
        ])

    # Split: loading structure from mongo (also loads active version from MySQL, not tracked here)
    @ddt.data((ModuleStoreEnum.Type.split, [1, 0], [2, 1], 0))
    @ddt.unpack
    def test_path_to_location(self, default_ms, num_mysql, num_finds, num_sends):
        """
        Make sure that path_to_location works
        """
        self.initdb(default_ms)

        course_key = self.course_locations[self.MONGO_COURSEID].course_key
        with self.store.branch_setting(ModuleStoreEnum.Branch.published_only, course_key):
            self._create_block_hierarchy()

            should_work = (
                (self.problem_x1a_2,  # lint-amnesty, pylint: disable=no-member
                 (course_key, "Chapter_x", "Sequential_x1", 'Vertical_x1a', '1', self.problem_x1a_2)),  # lint-amnesty, pylint: disable=no-member
                (self.chapter_x,  # lint-amnesty, pylint: disable=no-member
                 (course_key, "Chapter_x", None, None, None, self.chapter_x)),  # lint-amnesty, pylint: disable=no-member
            )

            for location, expected in should_work:
                # each iteration has different find count, pop this iter's find count
                with check_mongo_calls(num_finds.pop(0), num_sends), self.assertNumQueries(num_mysql.pop(0)):
                    path = path_to_location(self.store, location, branch_type=ModuleStoreEnum.Branch.published_only)
                    assert path == expected

        not_found = (
            course_key.make_usage_key('video', 'WelcomeX'),
            course_key.make_usage_key('course', 'NotHome'),
        )
        for location in not_found:
            with pytest.raises(ItemNotFoundError):
                path_to_location(self.store, location)

        # Orphaned items should not be found.
        orphan = course_key.make_usage_key('chapter', 'OrphanChapter')
        self.store.create_item(
            self.user_id,
            orphan.course_key,
            orphan.block_type,
            block_id=orphan.block_id
        )

        with pytest.raises(NoPathToItem):
            path_to_location(self.store, orphan)

    def test_navigation_index(self):
        """
        Make sure that navigation_index correctly parses the various position values that we might get from calls to
        path_to_location
        """
        assert 1 == navigation_index('1')
        assert 10 == navigation_index('10')
        assert navigation_index(None) is None
        assert 1 == navigation_index('1_2')
        assert 5 == navigation_index('5_2')
        assert 7 == navigation_index('7_3_5_6_')

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_revert_to_published_root_draft(self, default_ms):
        """
        Test calling revert_to_published on draft vertical.
        """
        self.initdb(default_ms)
        self._create_block_hierarchy()

        vertical = self.store.get_item(self.vertical_x1a)  # lint-amnesty, pylint: disable=no-member
        vertical_children_num = len(vertical.children)

        self.store.publish(self.course.location, self.user_id)
        assert not self._has_changes(self.vertical_x1a)  # lint-amnesty, pylint: disable=no-member

        # delete leaf problem (will make parent vertical a draft)
        self.store.delete_item(self.problem_x1a_1, self.user_id)  # lint-amnesty, pylint: disable=no-member
        assert self._has_changes(self.vertical_x1a)  # lint-amnesty, pylint: disable=no-member

        draft_parent = self.store.get_item(self.vertical_x1a)  # lint-amnesty, pylint: disable=no-member
        assert (vertical_children_num - 1) == len(draft_parent.children)
        published_parent = self.store.get_item(
            self.vertical_x1a,  # lint-amnesty, pylint: disable=no-member
            revision=ModuleStoreEnum.RevisionOption.published_only
        )
        assert vertical_children_num == len(published_parent.children)

        self.store.revert_to_published(self.vertical_x1a, self.user_id)  # lint-amnesty, pylint: disable=no-member
        reverted_parent = self.store.get_item(self.vertical_x1a)  # lint-amnesty, pylint: disable=no-member
        assert vertical_children_num == len(published_parent.children)
        self.assertBlocksEqualByFields(reverted_parent, published_parent)
        assert not self._has_changes(self.vertical_x1a)  # lint-amnesty, pylint: disable=no-member

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_revert_to_published_root_published(self, default_ms):
        """
        Test calling revert_to_published on a published vertical with a draft child.
        """
        self.initdb(default_ms)
        self._create_block_hierarchy()
        self.store.publish(self.course.location, self.user_id)

        problem = self.store.get_item(self.problem_x1a_1)  # lint-amnesty, pylint: disable=no-member
        orig_display_name = problem.display_name

        # Change display name of problem and update just it (so parent remains published)
        problem.display_name = "updated before calling revert"
        self.store.update_item(problem, self.user_id)
        self.store.revert_to_published(self.vertical_x1a, self.user_id)  # lint-amnesty, pylint: disable=no-member

        reverted_problem = self.store.get_item(self.problem_x1a_1)  # lint-amnesty, pylint: disable=no-member
        assert orig_display_name == reverted_problem.display_name

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_revert_to_published_no_draft(self, default_ms):
        """
        Test calling revert_to_published on vertical with no draft content does nothing.
        """
        self.initdb(default_ms)
        self._create_block_hierarchy()
        self.store.publish(self.course.location, self.user_id)

        orig_vertical = self.store.get_item(self.vertical_x1a)  # lint-amnesty, pylint: disable=no-member
        self.store.revert_to_published(self.vertical_x1a, self.user_id)  # lint-amnesty, pylint: disable=no-member
        reverted_vertical = self.store.get_item(self.vertical_x1a)  # lint-amnesty, pylint: disable=no-member

        self.assertBlocksEqualByFields(orig_vertical, reverted_vertical)

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_revert_to_published_no_published(self, default_ms):
        """
        Test calling revert_to_published on vertical with no published version errors.
        """
        self.initdb(default_ms)
        self._create_block_hierarchy()
        with pytest.raises(InvalidVersionError):
            self.store.revert_to_published(self.vertical_x1a, self.user_id)  # lint-amnesty, pylint: disable=no-member

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_revert_to_published_direct_only(self, default_ms):
        """
        Test calling revert_to_published on a direct-only item is a no-op.
        """
        self.initdb(default_ms)
        self._create_block_hierarchy()
        num_children = len(self.store.get_item(self.sequential_x1).children)  # lint-amnesty, pylint: disable=no-member
        self.store.revert_to_published(self.sequential_x1, self.user_id)  # lint-amnesty, pylint: disable=no-member
        reverted_parent = self.store.get_item(self.sequential_x1)  # lint-amnesty, pylint: disable=no-member
        # It does not discard the child vertical, even though that child is a draft (with no published version)
        assert num_children == len(reverted_parent.children)

    def test_reset_course_to_version(self):
        """
        Test calling `DraftVersioningModuleStore.test_reset_course_to_version`.
        """
        # Set up test course.
        self.initdb(ModuleStoreEnum.Type.split)  # Old Mongo does not support this operation.
        self._create_block_hierarchy()
        self.store.publish(self.course.location, self.user_id)

        # Get children of a vertical as a set.
        # We will use this set as a basis for content comparision in this test.
        original_vertical = self.store.get_item(self.vertical_x1a)  # lint-amnesty, pylint: disable=no-member
        original_vertical_children = set(original_vertical.children)

        # Find the version_guid of our course by diving into Split Mongo.
        split = self._get_split_modulestore()
        course_index = split.get_course_index(self.course.location.course_key)
        log.warning(f"Banana course index: {course_index}")
        original_version_guid = course_index["versions"]["published-branch"]

        # Reset course to currently-published version.
        # This should be a no-op.
        self.store.reset_course_to_version(
            self.course.location.course_key,
            original_version_guid,
            self.user_id,
        )
        noop_reset_vertical = self.store.get_item(self.vertical_x1a)  # lint-amnesty, pylint: disable=no-member
        assert set(noop_reset_vertical.children) == original_vertical_children

        # Delete a problem from the vertical and publish.
        # Vertical should have one less problem than before.
        self.store.delete_item(self.problem_x1a_1, self.user_id)  # lint-amnesty, pylint: disable=no-member
        self.store.publish(self.course.location, self.user_id)
        modified_vertical = self.store.get_item(self.vertical_x1a)  # lint-amnesty, pylint: disable=no-member
        assert set(modified_vertical.children) == (
            original_vertical_children - {self.problem_x1a_1}  # lint-amnesty, pylint: disable=no-member
        )

        # Add a couple more children to the vertical.
        # and publish a couple more times.
        # We want to make sure we can restore from something a few versions back.
        self.store.create_child(
            self.user_id,
            self.vertical_x1a,  # lint-amnesty, pylint: disable=no-member
            'problem',
            block_id='new_child1',
        )
        self.store.publish(self.course.location, self.user_id)
        self.store.create_child(
            self.user_id,
            self.vertical_x1a,  # lint-amnesty, pylint: disable=no-member
            'problem',
            block_id='new_child2',
        )
        self.store.publish(self.course.location, self.user_id)

        # Add another child, but don't publish.
        # We want to make sure that this works with a dirty draft branch.
        self.store.create_child(
            self.user_id,
            self.vertical_x1a,  # lint-amnesty, pylint: disable=no-member
            'problem',
            block_id='new_child3',
        )

        # Reset course to original version.
        # The restored vertical should have the same children as it did originally.
        self.store.reset_course_to_version(
            self.course.location.course_key,
            original_version_guid,
            self.user_id,
        )
        restored_vertical = self.store.get_item(self.vertical_x1a)  # lint-amnesty, pylint: disable=no-member
        assert set(restored_vertical.children) == original_vertical_children

    def _get_split_modulestore(self):
        """
        Grab the SplitMongo modulestore instance from within the Mixed modulestore.

        Assumption: There is a SplitMongo modulestore within the Mixed modulestore.
        This assumpion is hacky, but it seems OK because we're removing the
        Old (non-Split) Mongo modulestores soon.

        Returns: SplitMongoModuleStore
        """
        for store in self.store.modulestores:
            if isinstance(store, SplitMongoModuleStore):
                return store
        assert False, "SplitMongoModuleStore was not found in MixedModuleStore"

    # Split: active_versions (mysql), structure (mongo)
    @ddt.data((ModuleStoreEnum.Type.split, 1, 1, 0))
    @ddt.unpack
    def test_get_orphans(self, default_ms, num_mysql, max_find, max_send):
        """
        Test finding orphans.
        """
        self.initdb(default_ms)
        course_id = self.course_locations[self.MONGO_COURSEID].course_key

        # create parented children
        self._create_block_hierarchy()

        # orphans
        orphan_locations = [
            course_id.make_usage_key('chapter', 'OrphanChapter'),
            course_id.make_usage_key('vertical', 'OrphanVertical'),
            course_id.make_usage_key('problem', 'OrphanProblem'),
            course_id.make_usage_key('html', 'OrphanHTML'),
        ]

        # detached items (not considered as orphans)
        detached_locations = [
            course_id.make_usage_key('static_tab', 'StaticTab'),
            course_id.make_usage_key('course_info', 'updates'),
        ]

        for location in orphan_locations + detached_locations:
            self.store.create_item(
                self.user_id,
                location.course_key,
                location.block_type,
                block_id=location.block_id
            )

        with check_mongo_calls(max_find, max_send), self.assertNumQueries(num_mysql):
            found_orphans = self.store.get_orphans(self.course_locations[self.MONGO_COURSEID].course_key)
        self.assertCountEqual(found_orphans, orphan_locations)

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_create_item_populates_edited_info(self, default_ms):
        self.initdb(default_ms)
        block = self.store.create_item(
            self.user_id,
            self.course.location.course_key,
            'problem'
        )
        assert self.user_id == block.edited_by
        assert datetime.datetime.now(UTC) > block.edited_on

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_create_item_populates_subtree_edited_info(self, default_ms):
        self.initdb(default_ms)
        block = self.store.create_item(
            self.user_id,
            self.course.location.course_key,
            'problem'
        )
        assert self.user_id == block.subtree_edited_by
        assert datetime.datetime.now(UTC) > block.subtree_edited_on

    # Split: wildcard search of draft (find) and split (mysql)
    @ddt.data((ModuleStoreEnum.Type.split, 1, 1, 0))
    @ddt.unpack
    def test_get_courses_for_wiki(self, default_ms, num_mysql, max_find, max_send):
        """
        Test the get_courses_for_wiki method
        """
        self.initdb(default_ms)
        # Test Mongo wiki
        with check_mongo_calls(max_find, max_send), self.assertNumQueries(num_mysql):
            wiki_courses = self.store.get_courses_for_wiki('999')
        assert len(wiki_courses) == 1
        assert self.course_locations[self.MONGO_COURSEID].course_key.replace(branch=None) in wiki_courses

        assert len(self.store.get_courses_for_wiki('edX.simple.2012_Fall')) == 0
        assert len(self.store.get_courses_for_wiki('no_such_wiki')) == 0

    # Split:
    #    MySQL SplitModulestoreCourseIndex:
    #      1. Select by course ID
    #      2. Select by objectid
    #      3-4. Update index version, update historical record
    #    Find: 2 structures (pre & post published?)
    #    Sends:
    #      1. insert structure
    #      2. write index entry
    @ddt.data((ModuleStoreEnum.Type.split, 4, 2, 2))
    @ddt.unpack
    def test_unpublish(self, default_ms, num_mysql, max_find, max_send):
        """
        Test calling unpublish
        """
        self.initdb(default_ms)
        self._create_block_hierarchy()

        # publish
        self.store.publish(self.course.location, self.user_id)
        published_xblock = self.store.get_item(
            self.vertical_x1a,  # lint-amnesty, pylint: disable=no-member
            revision=ModuleStoreEnum.RevisionOption.published_only
        )
        assert published_xblock is not None

        # unpublish
        with check_mongo_calls(max_find, max_send), self.assertNumQueries(num_mysql):
            self.store.unpublish(self.vertical_x1a, self.user_id)  # lint-amnesty, pylint: disable=no-member

        with pytest.raises(ItemNotFoundError):
            self.store.get_item(
                self.vertical_x1a,  # lint-amnesty, pylint: disable=no-member
                revision=ModuleStoreEnum.RevisionOption.published_only
            )

        # make sure draft version still exists
        draft_xblock = self.store.get_item(
            self.vertical_x1a,  # lint-amnesty, pylint: disable=no-member
            revision=ModuleStoreEnum.RevisionOption.draft_only
        )
        assert draft_xblock is not None

    # Split: active_versions from MySQL, structure from mongo
    @ddt.data((ModuleStoreEnum.Type.split, 1, 1, 0))
    @ddt.unpack
    def test_has_published_version(self, default_ms, mysql_queries, max_find, max_send):
        """
        Test the has_published_version method
        """
        self.initdb(default_ms)
        self._create_block_hierarchy()

        # start off as Private
        item = self.store.create_child(self.user_id, self.writable_chapter_location, 'problem', 'test_compute_publish_state')  # lint-amnesty, pylint: disable=line-too-long
        item_location = item.location
        with self.assertNumQueries(mysql_queries), check_mongo_calls(max_find, max_send):
            assert not self.store.has_published_version(item)

        # Private -> Public
        self.store.publish(item_location, self.user_id)
        item = self.store.get_item(item_location)
        assert self.store.has_published_version(item)

        # Public -> Private
        self.store.unpublish(item_location, self.user_id)
        item = self.store.get_item(item_location)
        assert not self.store.has_published_version(item)

        # Private -> Public
        self.store.publish(item_location, self.user_id)
        item = self.store.get_item(item_location)
        assert self.store.has_published_version(item)

        # Public -> Draft with NO changes
        self.store.convert_to_draft(item_location, self.user_id)
        item = self.store.get_item(item_location)
        assert self.store.has_published_version(item)

        # Draft WITH changes
        item.display_name = 'new name'
        item = self.store.update_item(item, self.user_id)
        assert self.store.has_changes(item)
        assert self.store.has_published_version(item)

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_update_edit_info_ancestors(self, default_ms):
        """
        Tests that edited_on, edited_by, subtree_edited_on, and subtree_edited_by are set correctly during update
        """
        self.initdb(default_ms)

        test_course = self.store.create_course('testx', 'GreekHero', 'test_run', self.user_id)

        def check_node(location_key, after, before, edited_by, subtree_after, subtree_before, subtree_by):
            """
            Checks that the node given by location_key matches the given edit_info constraints.
            """
            node = self.store.get_item(location_key)
            if after:
                assert after < node.edited_on
            assert node.edited_on < before
            assert node.edited_by == edited_by
            if subtree_after:
                assert subtree_after < node.subtree_edited_on
            assert node.subtree_edited_on < subtree_before
            assert node.subtree_edited_by == subtree_by

        with self.store.bulk_operations(test_course.id):
            # Create a dummy vertical & html to test against
            component = self.store.create_child(
                self.user_id,
                test_course.location,
                'vertical',
                block_id='test_vertical'
            )
            child = self.store.create_child(
                self.user_id,
                component.location,
                'html',
                block_id='test_html'
            )
            sibling = self.store.create_child(
                self.user_id,
                component.location,
                'html',
                block_id='test_html_no_change'
            )

        after_create = datetime.datetime.now(UTC)
        # Verify that all nodes were last edited in the past by create_user
        for block in [component, child, sibling]:
            check_node(block.location, None, after_create, self.user_id, None, after_create, self.user_id)

        # Change the component, then check that there now are changes
        component.display_name = 'Changed Display Name'

        editing_user = self.user_id - 2
        with self.store.bulk_operations(test_course.id):  # TNL-764 bulk ops disabled ancestor updates
            component = self.store.update_item(component, editing_user)
        after_edit = datetime.datetime.now(UTC)
        check_node(component.location, after_create, after_edit, editing_user, after_create, after_edit, editing_user)
        # but child didn't change
        check_node(child.location, None, after_create, self.user_id, None, after_create, self.user_id)

        # Change the child
        child = self.store.get_item(child.location)
        child.display_name = 'Changed Display Name'
        self.store.update_item(child, user_id=editing_user)

        after_edit = datetime.datetime.now(UTC)

        # Verify that child was last edited between after_create and after_edit by edit_user
        check_node(child.location, after_create, after_edit, editing_user, after_create, after_edit, editing_user)

        # Verify that ancestors edit info is unchanged, but their subtree edit info matches child
        check_node(test_course.location, None, after_create, self.user_id, after_create, after_edit, editing_user)

        # Verify that others have unchanged edit info
        check_node(sibling.location, None, after_create, self.user_id, None, after_create, self.user_id)

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_update_edit_info(self, default_ms):
        """
        Tests that edited_on and edited_by are set correctly during an update
        """
        self.initdb(default_ms)

        test_course = self.store.create_course('testx', 'GreekHero', 'test_run', self.user_id)

        # Create a dummy component to test against
        component = self.store.create_child(
            self.user_id,
            test_course.location,
            'vertical',
        )

        # Store the current edit time and verify that user created the component
        assert component.edited_by == self.user_id
        old_edited_on = component.edited_on

        edit_user = self.user_id - 2
        # Change the component
        component.display_name = 'Changed'
        self.store.update_item(component, edit_user)
        updated_component = self.store.get_item(component.location)

        # Verify the ordering of edit times and that dummy_user made the edit
        assert old_edited_on < updated_component.edited_on
        assert updated_component.edited_by == edit_user

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_update_published_info(self, default_ms):
        """
        Tests that published_on and published_by are set correctly
        """
        self.initdb(default_ms)

        test_course = self.store.create_course('testx', 'GreekHero', 'test_run', self.user_id)

        publish_user = 456

        # Create a dummy component to test against
        component = self.store.create_child(
            self.user_id,
            test_course.location,
            'vertical',
        )

        # Store the current time, then publish
        old_time = datetime.datetime.now(UTC)
        self.store.publish(component.location, publish_user)
        updated_component = self.store.get_item(component.location)

        # Verify the time order and that publish_user caused publication
        assert old_time <= updated_component.published_on
        assert updated_component.published_by == publish_user

        # Verify that changing the item doesn't unset the published info
        updated_component.display_name = 'changed'
        self.store.update_item(updated_component, self.user_id)
        updated_component = self.store.get_item(updated_component.location)
        assert old_time <= updated_component.published_on
        assert updated_component.published_by == publish_user

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_auto_publish(self, default_ms):
        """
        Test that the correct things have been published automatically
        Assumptions:
            * we auto-publish courses, chapters, sequentials
            * we don't auto-publish problems
        """

        self.initdb(default_ms)

        # test create_course to make sure we are autopublishing
        test_course = self.store.create_course('testx', 'GreekHero', 'test_run', self.user_id)
        assert self.store.has_published_version(test_course)

        test_course_key = test_course.id

        # test create_item of direct-only category to make sure we are autopublishing
        chapter = self.store.create_child(self.user_id, test_course.location, 'chapter', 'Overview')
        with self.store.branch_setting(ModuleStoreEnum.Branch.published_only):
            assert chapter.location in self.store.get_item(test_course.location).children
        assert self.store.has_published_version(chapter)

        chapter_location = chapter.location

        # test create_child of direct-only category to make sure we are autopublishing
        sequential = self.store.create_child(self.user_id, chapter_location, 'sequential', 'Sequence')
        with self.store.branch_setting(ModuleStoreEnum.Branch.published_only):
            assert sequential.location in self.store.get_item(chapter_location).children
        assert self.store.has_published_version(sequential)

        # test update_item of direct-only category to make sure we are autopublishing
        sequential.display_name = 'sequential1'
        sequential = self.store.update_item(sequential, self.user_id)
        assert self.store.has_published_version(sequential)

        # test delete_item of direct-only category to make sure we are autopublishing
        self.store.delete_item(sequential.location, self.user_id, revision=ModuleStoreEnum.RevisionOption.all)
        with self.store.branch_setting(ModuleStoreEnum.Branch.published_only):
            assert sequential.location not in self.store.get_item(chapter_location).children
        chapter = self.store.get_item(chapter.location.for_branch(None))
        assert self.store.has_published_version(chapter)

        # test create_child of NOT direct-only category to make sure we aren't autopublishing
        problem_child = self.store.create_child(self.user_id, chapter_location, 'problem', 'Problem_Child')
        assert not self.store.has_published_version(problem_child)

        # test create_item of NOT direct-only category to make sure we aren't autopublishing
        problem_item = self.store.create_item(self.user_id, test_course_key, 'problem', 'Problem_Item')
        assert not self.store.has_published_version(problem_item)

        # test update_item of NOT direct-only category to make sure we aren't autopublishing
        problem_item.display_name = 'Problem_Item1'
        problem_item = self.store.update_item(problem_item, self.user_id)
        assert not self.store.has_published_version(problem_item)

        # test delete_item of NOT direct-only category to make sure we aren't autopublishing
        self.store.delete_item(problem_child.location, self.user_id)
        chapter = self.store.get_item(chapter.location.for_branch(None))
        assert self.store.has_published_version(chapter)

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_get_courses_for_wiki_shared(self, default_ms):
        """
        Test two courses sharing the same wiki
        """
        self.initdb(default_ms)

        # verify initial state - initially, we should have a wiki for the Mongo course
        wiki_courses = self.store.get_courses_for_wiki('999')
        assert self.course_locations[self.MONGO_COURSEID].course_key.replace(branch=None) in wiki_courses

        # set Mongo course to share the wiki with simple course
        mongo_course = self.store.get_course(self.course_locations[self.MONGO_COURSEID].course_key)
        mongo_course.wiki_slug = 'simple'
        self.store.update_item(mongo_course, self.user_id)

        # now mongo_course should not be retrievable with old wiki_slug
        wiki_courses = self.store.get_courses_for_wiki('999')
        assert len(wiki_courses) == 0

        # but there should be one course with wiki_slug 'simple'
        wiki_courses = self.store.get_courses_for_wiki('simple')
        assert len(wiki_courses) == 1
        assert self.course_locations[self.MONGO_COURSEID].course_key.replace(branch=None) in wiki_courses

        # configure mongo course to use unique wiki_slug.
        mongo_course = self.store.get_course(self.course_locations[self.MONGO_COURSEID].course_key)
        mongo_course.wiki_slug = 'MITx.999.2013_Spring'
        self.store.update_item(mongo_course, self.user_id)
        # it should be retrievable with its new wiki_slug
        wiki_courses = self.store.get_courses_for_wiki('MITx.999.2013_Spring')
        assert len(wiki_courses) == 1
        assert self.course_locations[self.MONGO_COURSEID].course_key.replace(branch=None) in wiki_courses
        # and NOT retriveable with its old wiki_slug
        wiki_courses = self.store.get_courses_for_wiki('simple')
        assert len(wiki_courses) == 0
        assert self.course_locations[self.MONGO_COURSEID].course_key.replace(branch=None) not in wiki_courses

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_branch_setting(self, default_ms):
        """
        Test the branch_setting context manager
        """
        self.initdb(default_ms)
        self._create_block_hierarchy()

        problem_location = self.problem_x1a_1.for_branch(None)  # lint-amnesty, pylint: disable=no-member
        problem_original_name = 'Problem_x1a_1'

        course_key = problem_location.course_key
        problem_new_name = 'New Problem Name'

        def assertNumProblems(display_name, expected_number):
            """
            Asserts the number of problems with the given display name is the given expected number.
            """
            assert len(self.store.get_items(course_key.for_branch(None), settings={'display_name': display_name})) ==\
                expected_number

        def assertProblemNameEquals(expected_display_name):
            """
            Asserts the display_name of the xblock at problem_location matches the given expected value.
            """
            # check the display_name of the problem
            problem = self.store.get_item(problem_location)
            assert problem.display_name == expected_display_name

            # there should be only 1 problem with the expected_display_name
            assertNumProblems(expected_display_name, 1)

        # verify Draft problem
        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, course_key):
            assert self.store.has_item(problem_location)
            assertProblemNameEquals(problem_original_name)

        # verify Published problem doesn't exist
        with self.store.branch_setting(ModuleStoreEnum.Branch.published_only, course_key):
            assert not self.store.has_item(problem_location)
            with pytest.raises(ItemNotFoundError):
                self.store.get_item(problem_location)

        # PUBLISH the problem
        self.store.publish(self.vertical_x1a, self.user_id)  # lint-amnesty, pylint: disable=no-member
        self.store.publish(problem_location, self.user_id)

        # verify Published problem
        with self.store.branch_setting(ModuleStoreEnum.Branch.published_only, course_key):
            assert self.store.has_item(problem_location)
            assertProblemNameEquals(problem_original_name)

        # verify Draft-preferred
        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, course_key):
            assertProblemNameEquals(problem_original_name)

        # EDIT name
        problem = self.store.get_item(problem_location)
        problem.display_name = problem_new_name
        self.store.update_item(problem, self.user_id)

        # verify Draft problem has new name
        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, course_key):
            assertProblemNameEquals(problem_new_name)

        # verify Published problem still has old name
        with self.store.branch_setting(ModuleStoreEnum.Branch.published_only, course_key):
            assertProblemNameEquals(problem_original_name)
            # there should be no published problems with the new name
            assertNumProblems(problem_new_name, 0)

        # PUBLISH the problem
        self.store.publish(problem_location, self.user_id)

        # verify Published problem has new name
        with self.store.branch_setting(ModuleStoreEnum.Branch.published_only, course_key):
            assertProblemNameEquals(problem_new_name)
            # there should be no published problems with the old name
            assertNumProblems(problem_original_name, 0)

        # verify branch setting is published-only in manager
        with self.store.branch_setting(ModuleStoreEnum.Branch.published_only):
            assert self.store.get_branch_setting() == ModuleStoreEnum.Branch.published_only

        # verify branch setting is draft-preferred in manager
        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
            assert self.store.get_branch_setting() == ModuleStoreEnum.Branch.draft_preferred

    def verify_default_store(self, store_type):
        """
        Verifies the default_store property
        """
        assert self.store.default_modulestore.get_modulestore_type() == store_type

        # verify internal helper method
        store = self.store._get_modulestore_for_courselike()  # pylint: disable=protected-access
        assert store.get_modulestore_type() == store_type

        # verify store used for creating a course
        course = self.store.create_course("org", "course{}".format(uuid4().hex[:5]), "run", self.user_id)
        assert course.runtime.modulestore.get_modulestore_type() == store_type

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_default_store(self, default_ms):
        """
        Test the default store context manager
        """
        # initialize the mixed modulestore
        self._initialize_mixed(mappings={})

        with self.store.default_store(default_ms):
            self.verify_default_store(default_ms)

    def test_default_store_fake(self):
        """
        Test the default store context manager, asking for a fake store
        """
        # initialize the mixed modulestore
        self._initialize_mixed(mappings={})

        fake_store = "fake"
        with self.assertRaisesRegex(Exception, f"Cannot find store of type {fake_store}"):
            with self.store.default_store(fake_store):
                pass  # pragma: no cover

    def save_asset(self, asset_key):
        """
        Load and save the given file. (taken from test_contentstore)
        """
        with open(f"{DATA_DIR}/static/{asset_key.block_id}", "rb") as f:
            content = StaticContent(
                asset_key, "Funky Pix", mimetypes.guess_type(asset_key.block_id)[0], f.read(),
            )
            self.store.contentstore.save(content)

    @ddt.data(
        [ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.split]
    )
    @ddt.unpack
    def test_clone_course(self, source_modulestore, destination_modulestore):
        """
        Test clone course
        """

        with MongoContentstoreBuilder().build() as contentstore:
            # initialize the mixed modulestore
            self._initialize_mixed(contentstore=contentstore, mappings={})

            with self.store.default_store(source_modulestore):

                source_course_key = self.store.make_course_key("org.source", "course.source", "run.source")
                self._create_course(source_course_key)
                self.save_asset(source_course_key.make_asset_key('asset', 'picture1.jpg'))

            with self.store.default_store(destination_modulestore):
                dest_course_id = self.store.make_course_key("org.other", "course.other", "run.other")
                self.store.clone_course(source_course_key, dest_course_id, self.user_id)

                # pylint: disable=protected-access
            source_store = self.store._get_modulestore_by_type(source_modulestore)
            dest_store = self.store._get_modulestore_by_type(destination_modulestore)
            self.assertCoursesEqual(source_store, source_course_key, dest_store, dest_course_id)

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_bulk_operations_signal_firing(self, default):
        """ Signals should be fired right before bulk_operations() exits. """
        with MongoContentstoreBuilder().build() as contentstore:
            signal_handler = Mock(name='signal_handler')
            self.store = MixedModuleStore(
                contentstore=contentstore,
                create_modulestore_instance=create_modulestore_instance,
                mappings={},
                signal_handler=signal_handler,
                **self.OPTIONS
            )
            self.addCleanup(self.store.close_all_connections)

            with self.store.default_store(default):

                signal_handler.send.assert_not_called()

                # Course creation and publication should fire the signal
                course = self.store.create_course('org_x', 'course_y', 'run_z', self.user_id)
                signal_handler.send.assert_called_with('course_published', course_key=course.id)
                signal_handler.reset_mock()

                course_key = course.id

                def _clear_bulk_ops_record(course_key):  # pylint: disable=unused-argument
                    """
                    Check if the signal has been fired.
                    The course_published signal fires before the _clear_bulk_ops_record.
                    """
                    signal_handler.send.assert_called_with('course_published', course_key=course.id)

                with patch.object(
                    self.store.thread_cache.default_store, '_clear_bulk_ops_record', wraps=_clear_bulk_ops_record
                ) as mock_clear_bulk_ops_record:

                    with self.store.bulk_operations(course_key):
                        categories = DIRECT_ONLY_CATEGORIES
                        for block_type in categories:
                            self.store.create_item(self.user_id, course_key, block_type)
                            signal_handler.send.assert_not_called()

                    assert mock_clear_bulk_ops_record.call_count == 1

                signal_handler.send.assert_called_with('course_published', course_key=course.id)

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_course_publish_signal_direct_firing(self, default):
        with MongoContentstoreBuilder().build() as contentstore:
            signal_handler = Mock(name='signal_handler')
            self.store = MixedModuleStore(
                contentstore=contentstore,
                create_modulestore_instance=create_modulestore_instance,
                mappings={},
                signal_handler=signal_handler,
                **self.OPTIONS
            )
            self.addCleanup(self.store.close_all_connections)

            with self.store.default_store(default):
                assert self.store.thread_cache.default_store.signal_handler is not None

                signal_handler.send.assert_not_called()

                # Course creation and publication should fire the signal
                course = self.store.create_course('org_x', 'course_y', 'run_z', self.user_id)
                signal_handler.send.assert_called_with('course_published', course_key=course.id)

                course_key = course.id

                # Test non-draftable block types. The block should be published with every change.
                categories = DIRECT_ONLY_CATEGORIES
                for block_type in categories:
                    log.debug('Testing with block type %s', block_type)
                    signal_handler.reset_mock()
                    block = self.store.create_item(self.user_id, course_key, block_type)
                    signal_handler.send.assert_called_with('course_published', course_key=course.id)

                    signal_handler.reset_mock()
                    block.display_name = block_type
                    self.store.update_item(block, self.user_id)
                    signal_handler.send.assert_called_with('course_published', course_key=course.id)

                    signal_handler.reset_mock()
                    self.store.publish(block.location, self.user_id)
                    signal_handler.send.assert_called_with('course_published', course_key=course.id)

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_course_publish_signal_rerun_firing(self, default):
        with MongoContentstoreBuilder().build() as contentstore:
            signal_handler = Mock(name='signal_handler')
            self.store = MixedModuleStore(
                contentstore=contentstore,
                create_modulestore_instance=create_modulestore_instance,
                mappings={},
                signal_handler=signal_handler,
                **self.OPTIONS
            )
            self.addCleanup(self.store.close_all_connections)

            with self.store.default_store(default):
                assert self.store.thread_cache.default_store.signal_handler is not None

                signal_handler.send.assert_not_called()

                # Course creation and publication should fire the signal
                course = self.store.create_course('org_x', 'course_y', 'run_z', self.user_id)
                signal_handler.send.assert_called_with('course_published', course_key=course.id)

                course_key = course.id

                # Test course re-runs
                signal_handler.reset_mock()
                dest_course_id = self.store.make_course_key("org.other", "course.other", "run.other")
                self.store.clone_course(course_key, dest_course_id, self.user_id)
                signal_handler.send.assert_called_with('course_published', course_key=dest_course_id)

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_course_publish_signal_import_firing(self, default):
        with MongoContentstoreBuilder().build() as contentstore:
            signal_handler = Mock(name='signal_handler')
            self.store = MixedModuleStore(
                contentstore=contentstore,
                create_modulestore_instance=create_modulestore_instance,
                mappings={},
                signal_handler=signal_handler,
                **self.OPTIONS
            )
            self.addCleanup(self.store.close_all_connections)

            with self.store.default_store(default):
                assert self.store.thread_cache.default_store.signal_handler is not None

                signal_handler.send.assert_not_called()

                # Test course imports
                # Note: The signal is fired once when the course is created and
                # a second time after the actual data import.
                import_course_from_xml(
                    self.store, self.user_id, DATA_DIR, ['toy'], load_error_blocks=False,
                    static_content_store=contentstore,
                    create_if_not_present=True,
                )
                signal_handler.send.assert_has_calls([
                    call('pre_publish', course_key=self.store.make_course_key('edX', 'toy', '2012_Fall')),
                    call('course_published', course_key=self.store.make_course_key('edX', 'toy', '2012_Fall')),
                    call('pre_publish', course_key=self.store.make_course_key('edX', 'toy', '2012_Fall')),
                    call('course_published', course_key=self.store.make_course_key('edX', 'toy', '2012_Fall')),
                ])

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_course_publish_signal_publish_firing(self, default):
        with MongoContentstoreBuilder().build() as contentstore:
            signal_handler = Mock(name='signal_handler')
            self.store = MixedModuleStore(
                contentstore=contentstore,
                create_modulestore_instance=create_modulestore_instance,
                mappings={},
                signal_handler=signal_handler,
                **self.OPTIONS
            )
            self.addCleanup(self.store.close_all_connections)

            with self.store.default_store(default):
                assert self.store.thread_cache.default_store.signal_handler is not None

                signal_handler.send.assert_not_called()

                # Course creation and publication should fire the signal
                course = self.store.create_course('org_x', 'course_y', 'run_z', self.user_id)
                signal_handler.send.assert_called_with('course_published', course_key=course.id)

                # Test a draftable block type, which needs to be explicitly published, and nest it within the
                # normal structure - this is important because some implementors change the parent when adding a
                # non-published child; if parent is in DIRECT_ONLY_CATEGORIES then this should not fire the event
                signal_handler.reset_mock()
                section = self.store.create_item(self.user_id, course.id, 'chapter')
                signal_handler.send.assert_called_with('course_published', course_key=course.id)

                signal_handler.reset_mock()
                subsection = self.store.create_child(self.user_id, section.location, 'sequential')
                signal_handler.send.assert_called_with('course_published', course_key=course.id)

                # 'units' and 'blocks' are draftable types
                signal_handler.reset_mock()
                unit = self.store.create_child(self.user_id, subsection.location, 'vertical')
                signal_handler.send.assert_not_called()

                block = self.store.create_child(self.user_id, unit.location, 'problem')
                signal_handler.send.assert_not_called()

                self.store.update_item(block, self.user_id)
                signal_handler.send.assert_not_called()

                signal_handler.reset_mock()
                self.store.publish(unit.location, self.user_id)
                signal_handler.send.assert_called_with('course_published', course_key=course.id)

                signal_handler.reset_mock()
                self.store.unpublish(unit.location, self.user_id)
                signal_handler.send.assert_called_with('course_published', course_key=course.id)

                signal_handler.reset_mock()
                self.store.delete_item(unit.location, self.user_id)
                signal_handler.send.assert_called_with('course_published', course_key=course.id)

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_bulk_course_publish_signal_direct_firing(self, default):
        with MongoContentstoreBuilder().build() as contentstore:
            signal_handler = Mock(name='signal_handler')
            self.store = MixedModuleStore(
                contentstore=contentstore,
                create_modulestore_instance=create_modulestore_instance,
                mappings={},
                signal_handler=signal_handler,
                **self.OPTIONS
            )
            self.addCleanup(self.store.close_all_connections)

            with self.store.default_store(default):
                assert self.store.thread_cache.default_store.signal_handler is not None

                signal_handler.send.assert_not_called()

                # Course creation and publication should fire the signal
                course = self.store.create_course('org_x', 'course_y', 'run_z', self.user_id)
                signal_handler.send.assert_called_with('course_published', course_key=course.id)

                course_key = course.id

                # Test non-draftable block types. No signals should be received until
                signal_handler.reset_mock()
                with self.store.bulk_operations(course_key):
                    categories = DIRECT_ONLY_CATEGORIES
                    for block_type in categories:
                        log.debug('Testing with block type %s', block_type)
                        block = self.store.create_item(self.user_id, course_key, block_type)
                        signal_handler.send.assert_not_called()

                        block.display_name = block_type
                        self.store.update_item(block, self.user_id)
                        signal_handler.send.assert_not_called()

                        self.store.publish(block.location, self.user_id)
                        signal_handler.send.assert_not_called()

                signal_handler.send.assert_called_with('course_published', course_key=course.id)

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_bulk_course_publish_signal_publish_firing(self, default):
        with MongoContentstoreBuilder().build() as contentstore:
            signal_handler = Mock(name='signal_handler')
            self.store = MixedModuleStore(
                contentstore=contentstore,
                create_modulestore_instance=create_modulestore_instance,
                mappings={},
                signal_handler=signal_handler,
                **self.OPTIONS
            )
            self.addCleanup(self.store.close_all_connections)

            with self.store.default_store(default):
                assert self.store.thread_cache.default_store.signal_handler is not None

                signal_handler.send.assert_not_called()

                # Course creation and publication should fire the signal
                course = self.store.create_course('org_x', 'course_y', 'run_z', self.user_id)
                signal_handler.send.assert_called_with('course_published', course_key=course.id)

                course_key = course.id

                # Test a draftable block type, which needs to be explicitly published, and nest it within the
                # normal structure - this is important because some implementors change the parent when adding a
                # non-published child; if parent is in DIRECT_ONLY_CATEGORIES then this should not fire the event
                signal_handler.reset_mock()
                with self.store.bulk_operations(course_key):
                    section = self.store.create_item(self.user_id, course_key, 'chapter')
                    signal_handler.send.assert_not_called()

                    subsection = self.store.create_child(self.user_id, section.location, 'sequential')
                    signal_handler.send.assert_not_called()

                    # 'units' and 'blocks' are draftable types
                    unit = self.store.create_child(self.user_id, subsection.location, 'vertical')
                    signal_handler.send.assert_not_called()

                    block = self.store.create_child(self.user_id, unit.location, 'problem')
                    signal_handler.send.assert_not_called()

                    self.store.update_item(block, self.user_id)
                    signal_handler.send.assert_not_called()

                    self.store.publish(unit.location, self.user_id)
                    signal_handler.send.assert_not_called()

                signal_handler.send.assert_called_with('course_published', course_key=course.id)

                # Test editing draftable block type without publish
                signal_handler.reset_mock()
                with self.store.bulk_operations(course_key):
                    unit = self.store.create_child(self.user_id, subsection.location, 'vertical')
                    signal_handler.send.assert_not_called()
                    block = self.store.create_child(self.user_id, unit.location, 'problem')
                    signal_handler.send.assert_not_called()
                    self.store.publish(unit.location, self.user_id)
                    signal_handler.send.assert_not_called()
                signal_handler.send.assert_called_with('course_published', course_key=course.id)

                signal_handler.reset_mock()
                with self.store.bulk_operations(course_key):
                    signal_handler.send.assert_not_called()
                    unit.display_name = "Change this unit"
                    self.store.update_item(unit, self.user_id)
                    signal_handler.send.assert_not_called()
                signal_handler.send.assert_not_called()

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_course_deleted_signal(self, default):
        with MongoContentstoreBuilder().build() as contentstore:
            signal_handler = Mock(name='signal_handler')
            self.store = MixedModuleStore(
                contentstore=contentstore,
                create_modulestore_instance=create_modulestore_instance,
                mappings={},
                signal_handler=signal_handler,
                **self.OPTIONS
            )
            self.addCleanup(self.store.close_all_connections)

            with self.store.default_store(default):
                assert self.store.thread_cache.default_store.signal_handler is not None

                signal_handler.send.assert_not_called()

                # Create a course
                course = self.store.create_course('org_x', 'course_y', 'run_z', self.user_id)
                course_key = course.id

                # Delete the course
                course = self.store.delete_course(course_key, self.user_id)

                # Verify that the signal was emitted
                signal_handler.send.assert_called_with('course_deleted', course_key=course_key)

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_delete_published_item_orphans(self, default_store):
        """
        Tests delete published item dont create any oprhans in course
        """
        self.initdb(default_store)
        course_locator = self.course.id

        chapter = self.store.create_child(
            self.user_id, self.course.location, 'chapter', block_id='section_one'
        )

        sequential = self.store.create_child(
            self.user_id, chapter.location, 'sequential', block_id='subsection_one'
        )

        vertical = self.store.create_child(
            self.user_id, sequential.location, 'vertical', block_id='moon_unit'
        )

        problem = self.store.create_child(
            self.user_id, vertical.location, 'problem', block_id='problem'
        )

        self.store.publish(chapter.location, self.user_id)
        # Verify that there are no changes
        assert not self._has_changes(chapter.location)
        assert not self._has_changes(sequential.location)
        assert not self._has_changes(vertical.location)
        assert not self._has_changes(problem.location)

        # No orphans in course
        course_orphans = self.store.get_orphans(course_locator)
        assert len(course_orphans) == 0
        self.store.delete_item(vertical.location, self.user_id)

        # No orphans in course after delete, except
        # in old mongo, which still creates orphans
        course_orphans = self.store.get_orphans(course_locator)
        if default_store == ModuleStoreEnum.Type.mongo:
            assert len(course_orphans) == 1
        else:
            assert len(course_orphans) == 0

        course_locator_publish = course_locator.for_branch(ModuleStoreEnum.BranchName.published)
        # No published oprhans after delete, except
        # in old mongo, which still creates orphans
        course_publish_orphans = self.store.get_orphans(course_locator_publish)

        if default_store == ModuleStoreEnum.Type.mongo:
            assert len(course_publish_orphans) == 1
        else:
            assert len(course_publish_orphans) == 0

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_delete_draft_item_orphans(self, default_store):
        """
        Tests delete draft item create no orphans in course
        """
        self.initdb(default_store)
        course_locator = self.course.id

        chapter = self.store.create_child(
            self.user_id, self.course.location, 'chapter', block_id='section_one'
        )

        sequential = self.store.create_child(
            self.user_id, chapter.location, 'sequential', block_id='subsection_one'
        )

        vertical = self.store.create_child(
            self.user_id, sequential.location, 'vertical', block_id='moon_unit'
        )

        problem = self.store.create_child(
            self.user_id, vertical.location, 'problem', block_id='problem'
        )

        self.store.publish(chapter.location, self.user_id)
        # Verify that there are no changes
        assert not self._has_changes(chapter.location)
        assert not self._has_changes(sequential.location)
        assert not self._has_changes(vertical.location)
        assert not self._has_changes(problem.location)

        # No orphans in course
        course_orphans = self.store.get_orphans(course_locator)
        assert len(course_orphans) == 0

        problem.display_name = 'changed'
        problem = self.store.update_item(problem, self.user_id)
        assert self._has_changes(vertical.location)
        assert self._has_changes(problem.location)

        self.store.delete_item(vertical.location, self.user_id)
        # No orphans in course after delete, except
        # in old mongo, which still creates them
        course_orphans = self.store.get_orphans(course_locator)
        if default_store == ModuleStoreEnum.Type.mongo:
            assert len(course_orphans) == 1
        else:
            assert len(course_orphans) == 0

        course_locator_publish = course_locator.for_branch(ModuleStoreEnum.BranchName.published)
        # No published orphans after delete, except
        # in old mongo, which still creates them
        course_publish_orphans = self.store.get_orphans(course_locator_publish)

        if default_store == ModuleStoreEnum.Type.mongo:
            assert len(course_publish_orphans) == 1
        else:
            assert len(course_publish_orphans) == 0


@ddt.ddt
@attr('mongo')
class TestPublishOverExportImport(CommonMixedModuleStoreSetup):
    """
    Tests which publish (or don't publish) items - and then export/import the course,
    checking the state of the imported items.
    """

    def setUp(self):
        """
        Set up the database for testing
        """
        super().setUp()

        self.user_id = ModuleStoreEnum.UserID.test
        self.export_dir = mkdtemp()
        self.addCleanup(rmtree, self.export_dir, ignore_errors=True)

    def _export_import_course_round_trip(self, modulestore, contentstore, source_course_key, export_dir):
        """
        Export the course from a modulestore and then re-import the course.
        """
        top_level_export_dir = 'exported_source_course'
        export_course_to_xml(
            modulestore,
            contentstore,
            source_course_key,
            export_dir,
            top_level_export_dir,
        )

        import_course_from_xml(
            modulestore,
            'test_user',
            export_dir,
            source_dirs=[top_level_export_dir],
            static_content_store=contentstore,
            target_id=source_course_key,
            create_if_not_present=True,
            raise_on_failure=True,
        )

    @contextmanager
    def _build_store(self, default_ms):
        """
        Perform the modulestore-building and course creation steps for a mixed modulestore test.
        """
        with MongoContentstoreBuilder().build() as contentstore:
            # initialize the mixed modulestore
            self._initialize_mixed(contentstore=contentstore, mappings={})
            with self.store.default_store(default_ms):
                source_course_key = self.store.make_course_key("org.source", "course.source", "run.source")
                self._create_course(source_course_key)
                yield contentstore, source_course_key

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_draft_has_changes_before_export_and_after_import(self, default_ms):
        """
        Tests that an unpublished unit remains with no changes across export and re-import.
        """
        with self._build_store(default_ms) as (contentstore, source_course_key):

            # Create a dummy component to test against and don't publish it.
            draft_xblock = self.store.create_item(
                self.user_id,
                self.course.id,
                'vertical',
                block_id='test_vertical'
            )
            # Not yet published, so changes are present
            assert self._has_changes(draft_xblock.location)

            self._export_import_course_round_trip(
                self.store, contentstore, source_course_key, self.export_dir
            )

            # Verify that the imported block still is a draft, i.e. has changes.
            assert self._has_changes(draft_xblock.location)

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_published_has_changes_before_export_and_after_import(self, default_ms):
        """
        Tests that an published unit remains published across export and re-import.
        """
        with self._build_store(default_ms) as (contentstore, source_course_key):

            # Create a dummy component to test against and publish it.
            published_xblock = self.store.create_item(
                self.user_id,
                self.course.id,
                'vertical',
                block_id='test_vertical'
            )
            self.store.publish(published_xblock.location, self.user_id)

            # Retrieve the published block and make sure it's published.
            assert not self._has_changes(published_xblock.location)

            self._export_import_course_round_trip(
                self.store, contentstore, source_course_key, self.export_dir
            )

            # Get the published xblock from the imported course.
            # Verify that it still is published, i.e. has no changes.
            assert not self._has_changes(published_xblock.location)

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_changed_published_has_changes_before_export_and_after_import(self, default_ms):
        """
        Tests that an published unit with an unpublished draft remains published across export and re-import.
        """
        with self._build_store(default_ms) as (contentstore, source_course_key):

            # Create a dummy component to test against and publish it.
            published_xblock = self.store.create_item(
                self.user_id,
                self.course.id,
                'vertical',
                block_id='test_vertical'
            )
            self.store.publish(published_xblock.location, self.user_id)

            # Retrieve the published block and make sure it's published.
            assert not self._has_changes(published_xblock.location)

            updated_display_name = 'Changed Display Name'
            component = self.store.get_item(published_xblock.location)
            component.display_name = updated_display_name
            component = self.store.update_item(component, self.user_id)
            assert self.store.has_changes(component)

            self._export_import_course_round_trip(
                self.store, contentstore, source_course_key, self.export_dir
            )

            # Get the published xblock from the imported course.
            # Verify that the published block still has a draft block, i.e. has changes.
            assert self._has_changes(published_xblock.location)

            # Verify that the changes in the draft vertical still exist.
            with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, source_course_key):
                component = self.store.get_item(published_xblock.location)
                assert component.display_name == updated_display_name

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_seq_with_unpublished_vertical_has_changes_before_export_and_after_import(self, default_ms):
        """
        Tests that an published unit with an unpublished draft remains published across export and re-import.
        """
        with self._build_store(default_ms) as (contentstore, source_course_key):

            # create chapter
            chapter = self.store.create_child(
                self.user_id, self.course.location, 'chapter', block_id='section_one'
            )
            self.store.publish(chapter.location, self.user_id)

            # create sequential
            sequential = self.store.create_child(
                self.user_id, chapter.location, 'sequential', block_id='subsection_one'
            )
            self.store.publish(sequential.location, self.user_id)

            # create vertical - don't publish it!
            vertical = self.store.create_child(
                self.user_id, sequential.location, 'vertical', block_id='moon_unit'
            )

            # Retrieve the published block and make sure it's published.
            # Chapter is published - but the changes in vertical below means it "has_changes".
            assert self._has_changes(chapter.location)
            # Sequential is published - but the changes in vertical below means it "has_changes".
            assert self._has_changes(sequential.location)
            # Vertical is unpublished - so it "has_changes".
            assert self._has_changes(vertical.location)

            self._export_import_course_round_trip(
                self.store, contentstore, source_course_key, self.export_dir
            )

            # Get the published xblock from the imported course.
            # Verify that the published block still has a draft block, i.e. has changes.
            assert self._has_changes(chapter.location)
            assert self._has_changes(sequential.location)
            assert self._has_changes(vertical.location)

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_vertical_with_draft_and_published_unit_has_changes_before_export_and_after_import(self, default_ms):
        """
        Tests that an published unit with an unpublished draft remains published across export and re-import.
        """
        with self._build_store(default_ms) as (contentstore, source_course_key):

            # create chapter
            chapter = self.store.create_child(
                self.user_id, self.course.location, 'chapter', block_id='section_one'
            )
            self.store.publish(chapter.location, self.user_id)

            # create sequential
            sequential = self.store.create_child(
                self.user_id, chapter.location, 'sequential', block_id='subsection_one'
            )
            self.store.publish(sequential.location, self.user_id)

            # create vertical
            vertical = self.store.create_child(
                self.user_id, sequential.location, 'vertical', block_id='moon_unit'
            )
            # Vertical has changes until it is actually published.
            assert self._has_changes(vertical.location)
            self.store.publish(vertical.location, self.user_id)
            assert not self._has_changes(vertical.location)

            # create unit
            unit = self.store.create_child(
                self.user_id, vertical.location, 'html', block_id='html_unit'
            )
            # Vertical has a new child -and- unit is unpublished. So both have changes.
            assert self._has_changes(vertical.location)
            assert self._has_changes(unit.location)

            # Publishing the vertical also publishes its unit child.
            self.store.publish(vertical.location, self.user_id)
            assert not self._has_changes(vertical.location)
            assert not self._has_changes(unit.location)

            # Publishing the unit separately has no effect on whether it has changes - it's already published.
            self.store.publish(unit.location, self.user_id)
            assert not self._has_changes(vertical.location)
            assert not self._has_changes(unit.location)

            # Retrieve the published block and make sure it's published.
            self.store.publish(chapter.location, self.user_id)
            assert not self._has_changes(chapter.location)
            assert not self._has_changes(sequential.location)
            assert not self._has_changes(vertical.location)
            assert not self._has_changes(unit.location)

            # Now make changes to the unit - but don't publish them.
            component = self.store.get_item(unit.location)
            updated_display_name = 'Changed Display Name'
            component.display_name = updated_display_name
            component = self.store.update_item(component, self.user_id)
            assert self._has_changes(component.location)

            # Export the course - then import the course export.
            self._export_import_course_round_trip(
                self.store, contentstore, source_course_key, self.export_dir
            )

            # Get the published xblock from the imported course.
            # Verify that the published block still has a draft block, i.e. has changes.
            assert self._has_changes(chapter.location)
            assert self._has_changes(sequential.location)
            assert self._has_changes(vertical.location)
            assert self._has_changes(unit.location)

            # Verify that the changes in the draft unit still exist.
            with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, source_course_key):
                component = self.store.get_item(unit.location)
                assert component.display_name == updated_display_name

            # Verify that the draft changes don't exist in the published unit - it still uses the default name.
            with self.store.branch_setting(ModuleStoreEnum.Branch.published_only, source_course_key):
                component = self.store.get_item(unit.location)
                assert component.display_name == 'Text'

    @ddt.data(ModuleStoreEnum.Type.split)
    def test_vertical_with_published_unit_remains_published_before_export_and_after_import(self, default_ms):
        """
        Tests that an published unit remains published across export and re-import.
        """
        with self._build_store(default_ms) as (contentstore, source_course_key):

            # create chapter
            chapter = self.store.create_child(
                self.user_id, self.course.location, 'chapter', block_id='section_one'
            )
            self.store.publish(chapter.location, self.user_id)

            # create sequential
            sequential = self.store.create_child(
                self.user_id, chapter.location, 'sequential', block_id='subsection_one'
            )
            self.store.publish(sequential.location, self.user_id)

            # create vertical
            vertical = self.store.create_child(
                self.user_id, sequential.location, 'vertical', block_id='moon_unit'
            )
            # Vertical has changes until it is actually published.
            assert self._has_changes(vertical.location)
            self.store.publish(vertical.location, self.user_id)
            assert not self._has_changes(vertical.location)

            # create unit
            unit = self.store.create_child(
                self.user_id, vertical.location, 'html', block_id='html_unit'
            )
            # Now make changes to the unit.
            updated_display_name = 'Changed Display Name'
            unit.display_name = updated_display_name
            unit = self.store.update_item(unit, self.user_id)
            assert self._has_changes(unit.location)

            # Publishing the vertical also publishes its unit child.
            self.store.publish(vertical.location, self.user_id)
            assert not self._has_changes(vertical.location)
            assert not self._has_changes(unit.location)

            # Export the course - then import the course export.
            self._export_import_course_round_trip(
                self.store, contentstore, source_course_key, self.export_dir
            )

            # Get the published xblock from the imported course.
            # Verify that the published block still has a draft block, i.e. has changes.
            assert not self._has_changes(chapter.location)
            assert not self._has_changes(sequential.location)
            assert not self._has_changes(vertical.location)
            assert not self._has_changes(unit.location)

            # Verify that the published changes exist in the published unit.
            with self.store.branch_setting(ModuleStoreEnum.Branch.published_only, source_course_key):
                component = self.store.get_item(unit.location)
                assert component.display_name == updated_display_name

    @ddt.data(ModuleStoreEnum.Type.split)
    @XBlockAside.register_temp_plugin(AsideTestType, 'test_aside')
    @patch('xmodule.modulestore.split_mongo.runtime.SplitModuleStoreRuntime.applicable_aside_types',
           lambda self, block: ['test_aside'])
    def test_aside_crud(self, default_store):
        """
        Check that asides could be imported from XML and the modulestores handle asides crud
        """
        if default_store == ModuleStoreEnum.Type.mongo:
            pytest.skip("asides not supported in old mongo")
        with MongoContentstoreBuilder().build() as contentstore:
            self.store = MixedModuleStore(
                contentstore=contentstore,
                create_modulestore_instance=create_modulestore_instance,
                mappings={},
                **self.OPTIONS
            )
            self.addCleanup(self.store.close_all_connections)
            with self.store.default_store(default_store):
                dest_course_key = self.store.make_course_key('edX', "aside_test", "2012_Fall")
                courses = import_course_from_xml(
                    self.store, self.user_id, DATA_DIR, ['aside'],
                    load_error_blocks=False,
                    static_content_store=contentstore,
                    target_id=dest_course_key,
                    create_if_not_present=True,
                )

                # check that the imported blocks have the right asides and values
                def check_block(block):
                    """
                    Check whether block has the expected aside w/ its fields and then recurse to the block's children
                    """
                    asides = block.runtime.get_asides(block)

                    assert len(asides) == 1, f'Found {asides} asides but expected only test_aside'
                    assert isinstance(asides[0], AsideTestType)
                    category = block.scope_ids.block_type
                    assert asides[0].data_field == f'{category} aside data'
                    assert asides[0].content == f'{category.capitalize()} Aside'

                    for child in block.get_children():
                        check_block(child)

                check_block(courses[0])

                # create a new block and ensure its aside magically appears with the right fields
                new_chapter = self.store.create_child(self.user_id, courses[0].location, 'chapter', 'new_chapter')
                asides = new_chapter.runtime.get_asides(new_chapter)

                assert len(asides) == 1, f'Found {asides} asides but expected only test_aside'
                chapter_aside = asides[0]
                assert isinstance(chapter_aside, AsideTestType)
                assert not chapter_aside.fields['data_field'].is_set_on(chapter_aside), \
                    f"data_field says it's assigned to {chapter_aside.data_field}"
                assert not chapter_aside.fields['content'].is_set_on(chapter_aside), \
                    f"content says it's assigned to {chapter_aside.content}"

                # now update the values
                chapter_aside.data_field = 'new value'
                self.store.update_item(new_chapter, self.user_id, asides=[chapter_aside])

                new_chapter = self.store.get_item(new_chapter.location)
                chapter_aside = new_chapter.runtime.get_asides(new_chapter)[0]
                assert 'new value' == chapter_aside.data_field

                # update the values the second time
                chapter_aside.data_field = 'another one value'
                self.store.update_item(new_chapter, self.user_id, asides=[chapter_aside])

                new_chapter2 = self.store.get_item(new_chapter.location)
                chapter_aside2 = new_chapter2.runtime.get_asides(new_chapter2)[0]
                assert 'another one value' == chapter_aside2.data_field

    @ddt.data(ModuleStoreEnum.Type.split)
    @XBlockAside.register_temp_plugin(AsideTestType, 'test_aside')
    @patch('xmodule.modulestore.split_mongo.runtime.SplitModuleStoreRuntime.applicable_aside_types',
           lambda self, block: ['test_aside'])
    def test_export_course_with_asides(self, default_store):
        if default_store == ModuleStoreEnum.Type.mongo:
            pytest.skip("asides not supported in old mongo")
        with MongoContentstoreBuilder().build() as contentstore:
            self.store = MixedModuleStore(
                contentstore=contentstore,
                create_modulestore_instance=create_modulestore_instance,
                mappings={},
                **self.OPTIONS
            )
            self.addCleanup(self.store.close_all_connections)
            with self.store.default_store(default_store):
                dest_course_key = self.store.make_course_key('edX', "aside_test", "2012_Fall")
                dest_course_key2 = self.store.make_course_key('edX', "aside_test_2", "2012_Fall_2")

                courses = import_course_from_xml(
                    self.store,
                    self.user_id,
                    DATA_DIR,
                    ['aside'],
                    load_error_blocks=False,
                    static_content_store=contentstore,
                    target_id=dest_course_key,
                    create_if_not_present=True,
                )

                def update_block_aside(block):
                    """
                    Check whether block has the expected aside w/ its fields and then recurse to the block's children
                    """
                    asides = block.runtime.get_asides(block)
                    asides[0].data_field = ''.join(['Exported data_field ', asides[0].data_field])
                    asides[0].content = ''.join(['Exported content ', asides[0].content])

                    self.store.update_item(block, self.user_id, asides=[asides[0]])

                    for child in block.get_children():
                        update_block_aside(child)

                update_block_aside(courses[0])

                # export course to xml
                top_level_export_dir = 'exported_source_course_with_asides'
                export_course_to_xml(
                    self.store,
                    contentstore,
                    dest_course_key,
                    self.export_dir,
                    top_level_export_dir,
                )

                # and restore the new one from the exported xml
                courses2 = import_course_from_xml(
                    self.store,
                    self.user_id,
                    self.export_dir,
                    source_dirs=[top_level_export_dir],
                    static_content_store=contentstore,
                    target_id=dest_course_key2,
                    create_if_not_present=True,
                    raise_on_failure=True,
                )

                assert 1 == len(courses2)

                # check that the imported blocks have the right asides and values
                def check_block(block):
                    """
                    Check whether block has the expected aside w/ its fields and then recurse to the block's children
                    """
                    asides = block.runtime.get_asides(block)

                    assert len(asides) == 1, f'Found {asides} asides but expected only test_aside'
                    assert isinstance(asides[0], AsideTestType)
                    category = block.scope_ids.block_type
                    assert asides[0].data_field == f'Exported data_field {category} aside data'
                    assert asides[0].content == f'Exported content {category.capitalize()} Aside'

                    for child in block.get_children():
                        check_block(child)

                check_block(courses2[0])

    @ddt.data(ModuleStoreEnum.Type.split)
    @XBlockAside.register_temp_plugin(AsideTestType, 'test_aside')
    @patch('xmodule.modulestore.split_mongo.runtime.SplitModuleStoreRuntime.applicable_aside_types',
           lambda self, block: ['test_aside'])
    def test_export_course_after_creating_new_items_with_asides(self, default_store):  # pylint: disable=too-many-statements
        if default_store == ModuleStoreEnum.Type.mongo:
            pytest.skip("asides not supported in old mongo")
        with MongoContentstoreBuilder().build() as contentstore:
            self.store = MixedModuleStore(
                contentstore=contentstore,
                create_modulestore_instance=create_modulestore_instance,
                mappings={},
                **self.OPTIONS
            )
            self.addCleanup(self.store.close_all_connections)
            with self.store.default_store(default_store):
                dest_course_key = self.store.make_course_key('edX', "aside_test", "2012_Fall")
                dest_course_key2 = self.store.make_course_key('edX', "aside_test_2", "2012_Fall_2")

                courses = import_course_from_xml(
                    self.store,
                    self.user_id,
                    DATA_DIR,
                    ['aside'],
                    load_error_blocks=False,
                    static_content_store=contentstore,
                    target_id=dest_course_key,
                    create_if_not_present=True,
                )

                # create new chapter and modify aside for it
                new_chapter_display_name = 'New Chapter'
                new_chapter = self.store.create_child(self.user_id, courses[0].location, 'chapter', 'new_chapter')
                new_chapter.display_name = new_chapter_display_name
                asides = new_chapter.runtime.get_asides(new_chapter)

                assert len(asides) == 1, f'Found {asides} asides but expected only test_aside'
                chapter_aside = asides[0]
                assert isinstance(chapter_aside, AsideTestType)
                chapter_aside.data_field = 'new value'
                self.store.update_item(new_chapter, self.user_id, asides=[chapter_aside])

                # create new problem and modify aside for it
                sequence = courses[0].get_children()[0].get_children()[0]
                new_problem_display_name = 'New Problem'
                new_problem = self.store.create_child(self.user_id, sequence.location, 'problem', 'new_problem')
                new_problem.display_name = new_problem_display_name
                asides = new_problem.runtime.get_asides(new_problem)

                assert len(asides) == 1, f'Found {asides} asides but expected only test_aside'
                problem_aside = asides[0]
                assert isinstance(problem_aside, AsideTestType)
                problem_aside.data_field = 'new problem value'
                problem_aside.content = 'new content value'
                self.store.update_item(new_problem, self.user_id, asides=[problem_aside])

                # export course to xml
                top_level_export_dir = 'exported_source_course_with_asides'
                export_course_to_xml(
                    self.store,
                    contentstore,
                    dest_course_key,
                    self.export_dir,
                    top_level_export_dir,
                )

                # and restore the new one from the exported xml
                courses2 = import_course_from_xml(
                    self.store,
                    self.user_id,
                    self.export_dir,
                    source_dirs=[top_level_export_dir],
                    static_content_store=contentstore,
                    target_id=dest_course_key2,
                    create_if_not_present=True,
                    raise_on_failure=True,
                )

                assert 1 == len(courses2)

                # check that aside for the new chapter was exported/imported properly
                chapters = courses2[0].get_children()
                assert 2 == len(chapters)
                assert new_chapter_display_name in [item.display_name for item in chapters]

                found = False
                for child in chapters:
                    if new_chapter.display_name == child.display_name:
                        found = True
                        asides = child.runtime.get_asides(child)
                        assert len(asides) == 1
                        child_aside = asides[0]
                        assert isinstance(child_aside, AsideTestType)
                        assert child_aside.data_field == 'new value'
                        break

                assert found, 'new_chapter not found'

                # check that aside for the new problem was exported/imported properly
                sequence_children = courses2[0].get_children()[0].get_children()[0].get_children()
                assert 2 == len(sequence_children)
                assert new_problem_display_name in [item.display_name for item in sequence_children]

                found = False
                for child in sequence_children:
                    if new_problem.display_name == child.display_name:
                        found = True
                        asides = child.runtime.get_asides(child)
                        assert len(asides) == 1
                        child_aside = asides[0]
                        assert isinstance(child_aside, AsideTestType)
                        assert child_aside.data_field == 'new problem value'
                        assert child_aside.content == 'new content value'
                        break

                assert found, 'new_chapter not found'


@ddt.ddt
@attr('mongo')
class TestAsidesWithMixedModuleStore(CommonMixedModuleStoreSetup):
    """
    Tests of the MixedModulestore interface methods with XBlock asides.
    """

    def setUp(self):
        """
        Setup environment for testing
        """
        super().setUp()
        key_store = DictKeyValueStore()
        field_data = KvsFieldData(key_store)
        self.runtime = TestRuntime(services={'field-data': field_data})

    @ddt.data(ModuleStoreEnum.Type.split)
    @XBlockAside.register_temp_plugin(AsideFoo, 'test_aside1')
    @XBlockAside.register_temp_plugin(AsideBar, 'test_aside2')
    @patch('xmodule.modulestore.split_mongo.runtime.SplitModuleStoreRuntime.applicable_aside_types',
           lambda self, block: ['test_aside1', 'test_aside2'])
    def test_get_and_update_asides(self, default_store):
        """
        Tests that connected asides could be stored, received and updated along with connected course items
        """
        if default_store == ModuleStoreEnum.Type.mongo:
            pytest.skip("asides not supported in old mongo")

        self.initdb(default_store)

        block_type1 = 'test_aside1'
        def_id = self.runtime.id_generator.create_definition(block_type1)
        usage_id = self.runtime.id_generator.create_usage(def_id)

        # the first aside item
        aside1 = AsideFoo(scope_ids=ScopeIds('user', block_type1, def_id, usage_id), runtime=self.runtime)
        aside1.field11 = 'new_value11'
        aside1.field12 = 'new_value12'

        block_type2 = 'test_aside2'
        def_id = self.runtime.id_generator.create_definition(block_type1)
        usage_id = self.runtime.id_generator.create_usage(def_id)

        # the second aside item
        aside2 = AsideBar(scope_ids=ScopeIds('user', block_type2, def_id, usage_id), runtime=self.runtime)
        aside2.field21 = 'new_value21'

        # create new item with two asides
        published_xblock = self.store.create_item(
            self.user_id,
            self.course.id,
            'vertical',
            block_id='test_vertical',
            asides=[aside1, aside2]
        )

        def _check_asides(asides, field11, field12, field21, field22):
            """ Helper function to check asides """
            assert len(asides) == 2
            assert {type(asides[0]), type(asides[1])} == {AsideFoo, AsideBar}
            assert asides[0].field11 == field11
            assert asides[0].field12 == field12
            assert asides[1].field21 == field21
            assert asides[1].field22 == field22

        # get saved item and check asides
        component = self.store.get_item(published_xblock.location)
        asides = component.runtime.get_asides(component)
        _check_asides(asides, 'new_value11', 'new_value12', 'new_value21', 'aside2_default_value2')

        asides[0].field11 = 'other_value11'

        # update the first aside item and check that it was stored correctly
        self.store.update_item(component, self.user_id, asides=[asides[0]])
        cached_asides = component.runtime.get_asides(component)
        _check_asides(cached_asides, 'other_value11', 'new_value12', 'new_value21', 'aside2_default_value2')

        new_component = self.store.get_item(published_xblock.location)
        new_asides = new_component.runtime.get_asides(new_component)
        _check_asides(new_asides, 'other_value11', 'new_value12', 'new_value21', 'aside2_default_value2')

    @ddt.data(ModuleStoreEnum.Type.split)
    @XBlockAside.register_temp_plugin(AsideFoo, 'test_aside1')
    @patch('xmodule.modulestore.split_mongo.runtime.SplitModuleStoreRuntime.applicable_aside_types',
           lambda self, block: ['test_aside1'])
    def test_clone_course_with_asides(self, default_store):
        """
        Tests that connected asides will be cloned together with the parent courses
        """
        if default_store == ModuleStoreEnum.Type.mongo:
            pytest.skip("asides not supported in old mongo")

        with MongoContentstoreBuilder().build() as contentstore:
            # initialize the mixed modulestore
            self._initialize_mixed(contentstore=contentstore, mappings={})

            with self.store.default_store(default_store):
                block_type1 = 'test_aside1'
                def_id = self.runtime.id_generator.create_definition(block_type1)
                usage_id = self.runtime.id_generator.create_usage(def_id)

                aside1 = AsideFoo(scope_ids=ScopeIds('user', block_type1, def_id, usage_id), runtime=self.runtime)
                aside1.field11 = 'test1'

                source_course_key = self.store.make_course_key("org.source", "course.source", "run.source")
                self._create_course(source_course_key, asides=[aside1])

                dest_course_id = self.store.make_course_key("org.other", "course.other", "run.other")
                self.store.clone_course(source_course_key, dest_course_id, self.user_id)

            source_store = self.store._get_modulestore_by_type(default_store)  # pylint: disable=protected-access
            self.assertCoursesEqual(source_store, source_course_key, source_store, dest_course_id)

            # after clone get connected aside and check that it was cloned correctly
            actual_items = source_store.get_items(dest_course_id,
                                                  revision=ModuleStoreEnum.RevisionOption.published_only)
            chapter_is_found = False

            for block in actual_items:
                if block.scope_ids.block_type == 'chapter':
                    asides = block.runtime.get_asides(block)
                    assert len(asides) == 1
                    assert asides[0].field11 == 'test1'
                    assert asides[0].field12 == 'aside1_default_value2'
                    chapter_is_found = True
                    break

            assert chapter_is_found

    @ddt.data(ModuleStoreEnum.Type.split)
    @XBlockAside.register_temp_plugin(AsideFoo, 'test_aside1')
    @patch('xmodule.modulestore.split_mongo.runtime.SplitModuleStoreRuntime.applicable_aside_types',
           lambda self, block: ['test_aside1'])
    def test_delete_item_with_asides(self, default_store):
        """
        Tests that connected asides will be removed together with the connected items
        """
        if default_store == ModuleStoreEnum.Type.mongo:
            pytest.skip("asides not supported in old mongo")

        self.initdb(default_store)

        block_type1 = 'test_aside1'
        def_id = self.runtime.id_generator.create_definition(block_type1)
        usage_id = self.runtime.id_generator.create_usage(def_id)

        aside1 = AsideFoo(scope_ids=ScopeIds('user', block_type1, def_id, usage_id), runtime=self.runtime)
        aside1.field11 = 'new_value11'
        aside1.field12 = 'new_value12'

        published_xblock = self.store.create_item(
            self.user_id,
            self.course.id,
            'vertical',
            block_id='test_vertical',
            asides=[aside1]
        )

        asides = published_xblock.runtime.get_asides(published_xblock)
        assert asides[0].field11 == 'new_value11'
        assert asides[0].field12 == 'new_value12'

        # remove item
        self.store.delete_item(published_xblock.location, self.user_id)

        # create item again
        published_xblock2 = self.store.create_item(
            self.user_id,
            self.course.id,
            'vertical',
            block_id='test_vertical'
        )

        # check that aside has default values
        asides2 = published_xblock2.runtime.get_asides(published_xblock2)
        assert asides2[0].field11 == 'aside1_default_value1'
        assert asides2[0].field12 == 'aside1_default_value2'

    @ddt.data((ModuleStoreEnum.Type.split, 1, 0))
    @XBlockAside.register_temp_plugin(AsideFoo, 'test_aside1')
    @patch('xmodule.modulestore.split_mongo.runtime.SplitModuleStoreRuntime.applicable_aside_types',
           lambda self, block: ['test_aside1'])
    @ddt.unpack
    def test_published_and_unpublish_item_with_asides(self, default_store, max_find, max_send):
        """
        Tests that public/unpublish doesn't affect connected stored asides
        """
        if default_store == ModuleStoreEnum.Type.mongo:
            pytest.skip("asides not supported in old mongo")

        self.initdb(default_store)

        block_type1 = 'test_aside1'
        def_id = self.runtime.id_generator.create_definition(block_type1)
        usage_id = self.runtime.id_generator.create_usage(def_id)

        aside1 = AsideFoo(scope_ids=ScopeIds('user', block_type1, def_id, usage_id), runtime=self.runtime)
        aside1.field11 = 'new_value11'
        aside1.field12 = 'new_value12'

        def _check_asides(item):
            """ Helper function to check asides """
            asides = item.runtime.get_asides(item)
            assert asides[0].field11 == 'new_value11'
            assert asides[0].field12 == 'new_value12'

        # start off as Private
        item = self.store.create_child(self.user_id, self.writable_chapter_location, 'problem',
                                       'test_compute_publish_state', asides=[aside1])
        item_location = item.location
        with check_mongo_calls(max_find, max_send):
            assert not self.store.has_published_version(item)
        _check_asides(item)

        # Private -> Public
        published_block = self.store.publish(item_location, self.user_id)
        _check_asides(published_block)

        item = self.store.get_item(item_location)
        assert self.store.has_published_version(item)
        _check_asides(item)

        # Public -> Private
        unpublished_block = self.store.unpublish(item_location, self.user_id)
        _check_asides(unpublished_block)

        item = self.store.get_item(item_location)
        assert not self.store.has_published_version(item)
        _check_asides(item)
