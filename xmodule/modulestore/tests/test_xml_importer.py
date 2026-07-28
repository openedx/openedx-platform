"""
Tests for XML importer.
"""


import importlib
import os
import unittest
from unittest import mock
from uuid import uuid4

from django.core.exceptions import ObjectDoesNotExist
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator
from path import Path as path
from xblock.fields import List, Scope, ScopeIds, String
from xblock.runtime import DictKeyValueStore, KvsFieldData, Runtime

from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.inheritance import InheritanceMixin
from xmodule.modulestore.tests.mongo_connection import MONGO_HOST, MONGO_PORT_NUM
from xmodule.modulestore.xml_importer import StaticContentImporter, _update_and_import_block, _update_block_location
from xmodule.tests import DATA_DIR
from xmodule.x_module import XModuleMixin

OPEN_BUILTIN = 'builtins.open'


class ModuleStoreNoSettings(unittest.TestCase):
    """
    A mixin to create a mongo modulestore that avoids settings
    """
    HOST = MONGO_HOST
    PORT = MONGO_PORT_NUM
    DB = 'test_mongo_%s' % uuid4().hex[:5]  # noqa: UP031
    COLLECTION = 'modulestore'
    FS_ROOT = DATA_DIR
    DEFAULT_CLASS = 'xmodule.modulestore.tests.test_xml_importer.StubXBlock'
    RENDER_TEMPLATE = lambda t_n, d, ctx=None, nsp='main': ''

    modulestore_options = {
        'default_class': DEFAULT_CLASS,
        'fs_root': DATA_DIR,
        'render_template': RENDER_TEMPLATE,
    }
    DOC_STORE_CONFIG = {
        'host': HOST,
        'port': PORT,
        'db': DB,
        'collection': COLLECTION,
    }
    MODULESTORE = {
        'ENGINE': 'xmodule.modulestore.mongo.DraftMongoModuleStore',
        'DOC_STORE_CONFIG': DOC_STORE_CONFIG,
        'OPTIONS': modulestore_options
    }

    modulestore = None

    def cleanup_modulestore(self):
        """
        cleanup
        """
        if self.modulestore:
            self.modulestore._drop_database()  # pylint: disable=protected-access

    def setUp(self):
        """
        Add cleanups
        """
        self.addCleanup(self.cleanup_modulestore)
        super().setUp()


#===========================================
def modulestore():
    """
    Mock the django dependent global modulestore function to disentangle tests from django
    """
    def load_function(engine_path):
        """
        Load the given engine
        """
        module_path, _, name = engine_path.rpartition('.')
        return getattr(importlib.import_module(module_path), name)

    if ModuleStoreNoSettings.modulestore is None:
        class_ = load_function(ModuleStoreNoSettings.MODULESTORE['ENGINE'])

        options = {}

        options.update(ModuleStoreNoSettings.MODULESTORE['OPTIONS'])
        options['render_template'] = render_to_template_mock

        # pylint: disable=bad-option-value, star-args
        ModuleStoreNoSettings.modulestore = class_(
            None,  # contentstore
            ModuleStoreNoSettings.MODULESTORE['DOC_STORE_CONFIG'],
            branch_setting_func=lambda: ModuleStoreEnum.Branch.draft_preferred,
            **options
        )

    return ModuleStoreNoSettings.modulestore


# pylint: disable=unused-argument
def render_to_template_mock(*args):
    pass


class StubXBlock(XModuleMixin, InheritanceMixin):
    """
    Stub XBlock used for testing.
    """
    test_content_field = String(
        help="A content field that will be explicitly set",
        scope=Scope.content,
        default="default value"
    )

    test_settings_field = String(
        help="A settings field that will be explicitly set",
        scope=Scope.settings,
        default="default value"
    )


class StubXBlockWithMutableFields(StubXBlock):
    """
    Stub XBlock used for testing mutable fields and children
    """
    has_children = True

    test_mutable_content_field = List(
        help="A mutable content field that will be explicitly set",
        scope=Scope.content,
    )

    test_mutable_settings_field = List(
        help="A mutable settings field that will be explicitly set",
        scope=Scope.settings,
    )


class UpdateLocationTest(ModuleStoreNoSettings):
    """
    Test that updating location preserves "is_set_on" status on fields
    """
    CONTENT_FIELDS = ['test_content_field', 'test_mutable_content_field']
    SETTINGS_FIELDS = ['test_settings_field', 'test_mutable_settings_field']
    CHILDREN_FIELDS = ['children']

    def setUp(self):
        """
        Create a stub XBlock backed by in-memory storage.
        """
        self.runtime = mock.MagicMock(Runtime)
        self.field_data = KvsFieldData(kvs=DictKeyValueStore())
        self.scope_ids = ScopeIds('Bob', 'mutablestubxblock', '123', 'import')
        self.xblock = StubXBlockWithMutableFields(self.runtime, self.field_data, self.scope_ids)

        self.fake_children_locations = [
            BlockUsageLocator(CourseLocator('org', 'course', 'run'), 'mutablestubxblock', 'child1'),
            BlockUsageLocator(CourseLocator('org', 'course', 'run'), 'mutablestubxblock', 'child2'),
        ]

        super().setUp()

    def _check_explicitly_set(self, block, scope, expected_explicitly_set_fields, should_be_set=False):
        """ Gets fields that are explicitly set on block and checks if they are marked as explicitly set or not """
        actual_explicitly_set_fields = block.get_explicitly_set_fields_by_scope(scope=scope)
        assertion = self.assertIn if should_be_set else self.assertNotIn
        for field in expected_explicitly_set_fields:
            assertion(field, actual_explicitly_set_fields)

    def test_update_locations_native_xblock(self):
        """ Update locations updates location and keeps values and "is_set_on" status """
        # Set the XBlock's location
        self.xblock.location = BlockUsageLocator(CourseLocator("org", "import", "run"), "category", "stubxblock")

        # Explicitly set the content, settings and children fields
        self.xblock.test_content_field = 'Explicitly set'
        self.xblock.test_settings_field = 'Explicitly set'
        self.xblock.test_mutable_content_field = [1, 2, 3]
        self.xblock.test_mutable_settings_field = ["a", "s", "d"]
        self.xblock.children = self.fake_children_locations  # pylint:disable=attribute-defined-outside-init
        self.xblock.save()

        # Update location
        target_location = self.xblock.location.replace(revision='draft')
        _update_block_location(self.xblock, target_location)
        new_version = self.xblock  # _update_block_location updates in-place

        # Check the XBlock's location
        assert new_version.location == target_location

        # Check the values of the fields.
        # The content, settings and children fields should be preserved
        assert new_version.test_content_field == 'Explicitly set'
        assert new_version.test_settings_field == 'Explicitly set'
        assert new_version.test_mutable_content_field == [1, 2, 3]
        assert new_version.test_mutable_settings_field == ['a', 's', 'd']
        assert new_version.children == self.fake_children_locations

        # Expect that these fields are marked explicitly set
        self._check_explicitly_set(new_version, Scope.content, self.CONTENT_FIELDS, should_be_set=True)
        self._check_explicitly_set(new_version, Scope.settings, self.SETTINGS_FIELDS, should_be_set=True)
        self._check_explicitly_set(new_version, Scope.children, self.CHILDREN_FIELDS, should_be_set=True)

        # Expect these fields pass "is_set_on" test
        for field in self.CONTENT_FIELDS + self.SETTINGS_FIELDS + self.CHILDREN_FIELDS:
            assert new_version.fields[field].is_set_on(new_version)  # pylint: disable=unsubscriptable-object


class StaticContentImporterTest(unittest.TestCase):  # pylint: disable=missing-class-docstring

    def setUp(self):  # pylint: disable=super-method-not-called
        self.course_data_path = path('/path')
        self.mocked_content_store = mock.Mock()
        self.static_content_importer = StaticContentImporter(
            static_content_store=self.mocked_content_store,
            course_data_path=self.course_data_path,
            target_id=CourseKey.from_string('course-v1:edX+DemoX+Demo_Course')
        )

    def test_import_static_content_directory(self):
        static_content_dir = 'static'
        expected_base_dir = path(self.course_data_path / static_content_dir)
        mocked_os_walk_yield = [
            ('static', None, ['file1.txt', 'file2.txt']),
            ('static/inner', None, ['file1.txt']),
        ]
        with mock.patch(
            'xmodule.modulestore.xml_importer.os.walk',
            return_value=mocked_os_walk_yield
        ), mock.patch.object(
            self.static_content_importer, 'import_static_file'
        ) as patched_import_static_file:
            self.static_content_importer.import_static_content_directory('static')
            patched_import_static_file.assert_any_call(
                'static/file1.txt', base_dir=expected_base_dir
            )
            patched_import_static_file.assert_any_call(
                'static/file2.txt', base_dir=expected_base_dir
            )
            patched_import_static_file.assert_any_call(
                'static/inner/file1.txt', base_dir=expected_base_dir
            )

    def test_import_static_file(self):
        base_dir = path('/path/to/dir')
        full_file_path = os.path.join(base_dir, 'static/some_file.txt')
        self.mocked_content_store.generate_thumbnail.return_value = (None, None)
        with mock.patch(OPEN_BUILTIN, mock.mock_open(read_data=b"data")) as mock_file:
            self.static_content_importer.import_static_file(
                full_file_path=full_file_path,
                base_dir=base_dir
            )
            mock_file.assert_called_with(full_file_path, 'rb')
            self.mocked_content_store.generate_thumbnail.assert_called_once()


class UpdateAndImportBlockLibraryContentTest(unittest.TestCase):
    """
    Tests for the library_content special-case handling inside _update_and_import_block.

    Covers the bug where importing a course containing a library_content block into a
    fresh environment (first import, library exists on dest) failed with:
        KeyError: BlockKey(type='problem', id='...')
    because sync_from_library was called on the published block (wrong branch) and with
    a source-environment library version GUID that does not exist on the destination.
    """

    def _make_block(self, block_type='library_content', source_library_id='TestOrg+TestLib',
                    source_library_version='aabbcc112233', has_children=True):
        """Build a minimal mock block with the attributes _update_and_import_block needs."""
        block = mock.MagicMock()
        # block.location must be a MagicMock (not a real OpaqueKey) so its
        # attributes can be freely set. Real OpaqueKeys are immutable.
        block.location.block_type = block_type
        block.location.block_id = 'test_lib_content'
        block.source_library_id = source_library_id
        block.source_library_version = source_library_version
        block.source_library_key = mock.MagicMock()
        block.fields = {}
        block.get_asides.return_value = []
        block.has_children = has_children
        return block

    def _make_store(self, branch_setting=ModuleStoreEnum.Branch.published_only,
                    block_already_published=False, library_exists=True):
        """Build a minimal mock store."""
        store = mock.MagicMock()
        store.get_branch_setting.return_value = branch_setting
        store.branch_setting.return_value.__enter__ = mock.Mock(return_value=None)
        store.branch_setting.return_value.__exit__ = mock.Mock(return_value=False)
        store.has_item.return_value = block_already_published
        store.get_library.return_value = mock.MagicMock() if library_exists else None

        # import_xblock returns the published block (as split_draft does under published_only)
        published_location = mock.MagicMock()
        published_location.block_type = 'library_content'

        published_block = mock.MagicMock()
        published_block.location = published_location
        published_block.location.block_type = 'library_content'
        published_block.source_library_id = 'TestOrg+TestLib'
        published_block.source_library_key = mock.MagicMock()
        store.import_xblock.return_value = published_block

        # get_item(draft_location) returns a draft block with sync_from_library available
        draft_block = mock.MagicMock()
        store.get_item.return_value = draft_block

        return store, published_block, draft_block

    def _call(self, source_block, store):
        course_key = CourseLocator('TestOrg', 'TestCourse', '2026_T1')
        return _update_and_import_block(
            block=source_block,
            store=store,
            user_id=1,
            source_course_id=course_key,
            dest_course_id=course_key,
            do_import_static=False,
            runtime=mock.MagicMock(),
        )

    def test_first_import_syncs_draft_block_with_upgrade_to_latest(self):
        """
        On first import (block not yet published), sync_from_library must be called
        on the DRAFT block with upgrade_to_latest=True so that copy_from_template
        creates child blocks in the draft structure before publish() is called.
        """
        source_block = self._make_block()
        store, published_block, draft_block = self._make_store(block_already_published=False)

        self._call(source_block, store)

        # Must fetch the draft block explicitly
        store.get_item.assert_called_once_with(
            published_block.location.for_branch(ModuleStoreEnum.BranchName.draft)
        )
        # Must call sync with upgrade_to_latest=True (not the source version GUID)
        draft_block.sync_from_library.assert_called_once_with(upgrade_to_latest=True)

    def test_first_import_publishes_after_sync(self):
        """
        After a successful sync on first import, the block must be published.
        """
        source_block = self._make_block()
        store, published_block, _draft_block = self._make_store(block_already_published=False)

        self._call(source_block, store)

        store.publish.assert_called_once_with(published_block.location, 1)

    def test_reimport_skips_sync_when_already_published(self):
        """
        When the library_content block already exists in the published branch
        (re-import scenario), sync_from_library must NOT be called.
        """
        source_block = self._make_block()
        store, _published_block, draft_block = self._make_store(block_already_published=True)

        self._call(source_block, store)

        draft_block.sync_from_library.assert_not_called()
        store.publish.assert_not_called()

    def test_no_sync_when_library_missing_on_destination(self):
        """
        If the library does not exist on the destination, neither sync_from_library
        nor publish should be called.
        """
        source_block = self._make_block()
        store, _published_block, draft_block = self._make_store(
            block_already_published=False, library_exists=False
        )

        self._call(source_block, store)

        draft_block.sync_from_library.assert_not_called()
        store.publish.assert_not_called()

    def test_object_does_not_exist_during_sync_is_handled(self):
        """
        If sync_from_library raises ObjectDoesNotExist, the inner except swallows it.
        The outer try/except ValueError completes normally, so its else clause runs
        and publish IS still called. The import must not raise.
        """
        source_block = self._make_block()
        store, published_block, draft_block = self._make_store(block_already_published=False)
        draft_block.sync_from_library.side_effect = ObjectDoesNotExist("library not found")

        # Should not raise
        self._call(source_block, store)

        # Outer else runs even though inner sync failed → publish is called
        store.publish.assert_called_once_with(published_block.location, 1)
