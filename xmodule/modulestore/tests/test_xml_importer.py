"""
Tests for XML importer.
"""


import importlib
import os
import unittest
from unittest import mock
from uuid import uuid4

from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator
from path import Path as path
from xblock.fields import List, Scope, ScopeIds, String
from xblock.runtime import DictKeyValueStore, KvsFieldData, Runtime

from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.inheritance import InheritanceMixin
from xmodule.modulestore.tests.mongo_connection import MONGO_HOST, MONGO_PORT_NUM
from xmodule.modulestore.xml_importer import StaticContentImporter, _update_block_location
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

        # lint-amnesty, pylint: disable=bad-option-value, star-args
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


class StaticContentImporterTest(unittest.TestCase):  # lint-amnesty, pylint: disable=missing-class-docstring

    def setUp(self):  # lint-amnesty, pylint: disable=super-method-not-called
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


class TestApplySequentialGating(unittest.TestCase):
    """
    Unit tests for ``_apply_sequential_gating`` — bug #36995 regression.

    Subsection gating metadata must round-trip through OLX export/import.
    """

    def setUp(self):
        super().setUp()
        self.course_key = CourseLocator('org', 'course', 'run')
        self.seq_loc = self.course_key.make_usage_key('sequential', 'gated_seq')
        self.prereq_loc = self.course_key.make_usage_key('sequential', 'prereq_seq')
        self.block = mock.Mock()
        self.block.location = self.seq_loc

    def _run(self, attrs, gating_enabled=True, prereq_exists=True):
        """Invoke _apply_sequential_gating with the given config and return the mocks."""
        from xmodule.modulestore.xml_importer import _apply_sequential_gating  # noqa: PLC0415
        course = mock.Mock(enable_subsection_gating=gating_enabled)
        with mock.patch('xmodule.modulestore.xml_importer.modulestore') as ms, \
                mock.patch('openedx.core.lib.gating.api.is_prerequisite', return_value=False), \
                mock.patch('openedx.core.lib.gating.api.add_prerequisite') as add_p, \
                mock.patch('openedx.core.lib.gating.api.set_required_content') as set_r:
            ms.return_value.get_course.return_value = course
            ms.return_value.has_item.return_value = prereq_exists
            _apply_sequential_gating(self.block, self.course_key, attrs)
        return add_p, set_r

    def test_unit_noop_when_gating_disabled_on_course(self):
        """If the course has gating disabled, do nothing."""
        add_p, set_r = self._run(
            {
                'required_prereq': str(self.prereq_loc),
                'min_score': '80',
                'min_completion': '90',
            },
            gating_enabled=False,
        )
        add_p.assert_not_called()
        set_r.assert_not_called()

    def test_unit_is_prerequisite_flag_only(self):
        """is_prerequisite='true' alone marks the subsection as an eligible prereq source."""
        add_p, set_r = self._run({'is_prerequisite': 'true'})
        add_p.assert_called_once_with(self.course_key, self.seq_loc)
        # set_required_content is still called once to clear stale state.
        assert set_r.call_count == 1

    def test_integration_missing_prereq_raises(self):
        """Referencing a non-existent prereq must raise CourseImportException."""
        from xmodule.modulestore.xml_importer import CourseImportException  # noqa: PLC0415
        with self.assertRaises(CourseImportException):
            self._run(
                {
                    'required_prereq': str(self.prereq_loc),
                    'min_score': '80',
                    'min_completion': '90',
                },
                prereq_exists=False,
            )

    def test_bug_36995_regression_invalid_prereq_key_raises(self):
        """Regression for #36995: invalid key string must raise CourseImportException."""
        from xmodule.modulestore.xml_importer import CourseImportException  # noqa: PLC0415
        with self.assertRaises(CourseImportException):
            self._run({
                'required_prereq': 'not-a-usage-key',
                'min_score': '80',
                'min_completion': '90',
            })
