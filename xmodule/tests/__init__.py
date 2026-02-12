"""
unittests for xmodule
"""


import inspect
import json
import os
import sys
import traceback
import unittest
from contextlib import contextmanager
from functools import wraps
from unittest.mock import Mock

from django.test import TransactionTestCase

from opaque_keys.edx.keys import CourseKey
from path import Path as path
from xblock.core import XBlock
from xblock.field_data import DictFieldData
from xblock.fields import Reference, ReferenceList, ReferenceValueDict, ScopeIds

from xmodule.capa.xqueue_interface import XQueueService
from xmodule.assetstore import AssetMetadata
from xmodule.contentstore.django import contentstore
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.draft_and_published import ModuleStoreDraftAndPublished
from xmodule.modulestore.inheritance import InheritanceMixin
from xmodule.modulestore.xml import CourseLocationManager
from xmodule.tests.helpers import StubReplaceURLService, mock_render_template, StubMakoService, StubUserService
from xmodule.util.sandboxing import SandboxService
from xmodule.x_module import DoNothingCache, XModuleMixin, ModuleStoreRuntime
from openedx.core.djangoapps.video_config.services import VideoConfigService
from openedx.core.lib.cache_utils import CacheService
from openedx.core.djangoapps.discussions.services import DiscussionConfigService

MODULE_DIR = path(__file__).dirname()
# Location of common test DATA directory
# '../../common/test/data/'
DATA_DIR = MODULE_DIR.parent.parent / "common" / "test" / "data"


def handler_url(block, handler, suffix='', query='', thirdparty=False):  # lint-amnesty, pylint: disable=arguments-differ
    return '{usage_id}/{handler}{suffix}?{query}'.format(
        usage_id=str(block.scope_ids.usage_id),
        handler=handler,
        suffix=suffix,
        query=query,
    )


def local_resource_url(block, uri):
    return 'resource/{usage_id}/{uri}'.format(
        usage_id=str(block.scope_ids.usage_id),
        uri=uri,
    )


# Disable XBlockAsides in most tests
def get_asides(block):
    return []


@property
def resources_fs():
    return Mock(name='TestModuleStoreRuntime.resources_fs', root_path='.')


class TestModuleStoreRuntime(ModuleStoreRuntime):  # pylint: disable=abstract-method
    """
    ModuleStore-based XBlock Runtime for testing
    """
    def handler_url(self, block, handler, suffix='', query='', thirdparty=False):  # lint-amnesty, pylint: disable=arguments-differ
        return '{usage_id}/{handler}{suffix}?{query}'.format(
            usage_id=str(block.scope_ids.usage_id),
            handler=handler,
            suffix=suffix,
            query=query,
        )

    def local_resource_url(self, block, uri):
        return 'resource/{usage_id}/{uri}'.format(
            usage_id=str(block.scope_ids.usage_id),
            uri=uri,
        )

    # Disable XBlockAsides in most tests
    def get_asides(self, block):
        return []

    def resources_fs(self):  # lint-amnesty, pylint: disable=method-hidden
        return Mock(name='TestModuleStoreRuntime.resources_fs', root_path='.')

    def __repr__(self):
        """
        Custom hacky repr.
        XBlock.Runtime.render() replaces the _view_name attribute while rendering, which
        causes rendered comparisons of blocks to fail as unequal. So make the _view_name
        attribute None during the base repr - and set it back to original value afterward.
        """
        orig_view_name = None
        if hasattr(self, '_view_name'):
            orig_view_name = self._view_name
        self._view_name = None
        rt_repr = super().__repr__()
        self._view_name = orig_view_name
        return rt_repr


def get_test_system(
    course_id=CourseKey.from_string('/'.join(['org', 'course', 'run'])),
    user=None,
    user_is_staff=False,
    user_location=None,
    render_template=None,
    add_get_block_overrides=False
):
    """
    Construct a test ModuleStoreRuntime instance.

    By default, the descriptor system's render_template() method simply returns the repr of the
    context it is passed.  You can override this by passing in a different render_template argument.
    """

    id_manager = CourseLocationManager(course_id)

    descriptor_system = get_test_descriptor_system(id_reader=id_manager, id_generator=id_manager)

    if not user:
        user = Mock(name='get_test_system.user', is_staff=False)
    if not user_location:
        user_location = Mock(name='get_test_system.user_location')
    user_service = StubUserService(
        user=user,
        anonymous_user_id='student',
        deprecated_anonymous_user_id='student',
        user_is_staff=user_is_staff,
        user_role='student',
        request_country_code=user_location,
    )

    mako_service = StubMakoService(render_template=render_template)

    replace_url_service = StubReplaceURLService()

    def get_block(block):
        """Mocks module_system get_block function"""

        prepare_block_runtime(block.runtime, add_overrides=add_get_block_overrides)
        block.runtime.get_block_for_descriptor = get_block
        block.bind_for_student(user.id)

        return block

    services = {
        'user': user_service,
        'mako': mako_service,
        'replace_urls': replace_url_service,
        'cache': CacheService(DoNothingCache()),
        'field-data': DictFieldData({}),
        'sandbox': SandboxService(contentstore, course_id),
        'video_config': VideoConfigService(),
        'discussion_config_service': DiscussionConfigService()
    }

    descriptor_system.get_block_for_descriptor = get_block  # lint-amnesty, pylint: disable=attribute-defined-outside-init
    descriptor_system._services.update(services)  # lint-amnesty, pylint: disable=protected-access

    return descriptor_system


def prepare_block_runtime(
    runtime,
    course_id=CourseKey.from_string('/'.join(['org', 'course', 'run'])),
    user=None,
    user_is_staff=False,
    user_location=None,
    render_template=None,
    add_overrides=False,
    add_get_block=False,
):
    """
    Sets properties in the runtime of the specified block that is
    required for tests.
    """

    if not user:
        user = Mock(name='get_test_system.user', is_staff=False)
    if not user_location:
        user_location = Mock(name='get_test_system.user_location')
    user_service = StubUserService(
        user=user,
        anonymous_user_id='student',
        deprecated_anonymous_user_id='student',
        user_is_staff=user_is_staff,
        user_role='student',
        request_country_code=user_location,
    )

    mako_service = StubMakoService(render_template=render_template)

    replace_url_service = StubReplaceURLService()

    def get_block(block):
        """Mocks module_system get_block function"""

        prepare_block_runtime(block.runtime)
        block.bind_for_student(user.id)

        return block

    services = {
        'user': user_service,
        'mako': mako_service,
        'replace_urls': replace_url_service,
        'cache': CacheService(DoNothingCache()),
        'field-data': DictFieldData({}),
        'sandbox': SandboxService(contentstore, course_id),
        'video_config': VideoConfigService(),
        'discussion_config_service': DiscussionConfigService()
    }

    if add_overrides:
        runtime.handler_url_override = handler_url
        runtime.local_resource_url = local_resource_url
        runtime.get_asides = get_asides
        runtime.resources_fs = resources_fs

    if add_get_block:
        runtime.get_block_for_descriptor = get_block

    runtime._services.update(services)  # lint-amnesty, pylint: disable=protected-access

    # runtime.load_item=Mock(name='get_test_descriptor_system.load_item')
    # runtime.resources_fs=Mock(name='get_test_descriptor_system.resources_fs')
    # runtime.error_tracker=Mock(name='get_test_descriptor_system.error_tracker')
    # runtime.render_template=render_template or mock_render_template,
    # runtime.mixins=(InheritanceMixin, XModuleMixin)

    return runtime


def get_test_descriptor_system(render_template=None, **kwargs):
    """
    Construct a test ModuleStoreRuntime instance.
    """
    field_data = DictFieldData({})
    video_config = VideoConfigService()

    descriptor_system = TestModuleStoreRuntime(
        load_item=Mock(name='get_test_descriptor_system.load_item'),
        resources_fs=Mock(name='get_test_descriptor_system.resources_fs'),
        error_tracker=Mock(name='get_test_descriptor_system.error_tracker'),
        render_template=render_template or mock_render_template,
        mixins=(InheritanceMixin, XModuleMixin),
        services={'field-data': field_data, 'video_config': video_config},
        **kwargs
    )
    descriptor_system.get_asides = lambda block: []
    return descriptor_system


class ModelsTest(unittest.TestCase):  # lint-amnesty, pylint: disable=missing-class-docstring

    def test_load_class(self):
        vc = XBlock.load_class('sequential')
        vc_str = "<class 'xmodule.seq_block.SequenceBlock'>"
        assert str(vc) == vc_str


def map_references(value, field, actual_course_key):
    """
    Map the references in value to actual_course_key and return value
    """
    if not value:  # if falsey
        return value
    if isinstance(field, Reference):
        return value.map_into_course(actual_course_key)
    if isinstance(field, ReferenceList):
        return [sub.map_into_course(actual_course_key) for sub in value]
    if isinstance(field, ReferenceValueDict):
        return {key: ele.map_into_course(actual_course_key) for key, ele in value.items()}
    return value


class LazyFormat:
    """
    An stringy object that delays formatting until it's put into a string context.
    """
    __slots__ = ('template', 'args', 'kwargs', '_message')

    def __init__(self, template, *args, **kwargs):
        self.template = template
        self.args = args
        self.kwargs = kwargs
        self._message = None

    def __str__(self):
        if self._message is None:
            self._message = self.template.format(*self.args, **self.kwargs)
        return self._message

    def __repr__(self):
        return str(self)

    def __len__(self):
        return len(str(self))

    def __getitem__(self, index):
        return str(self)[index]


class CourseComparisonTest(TransactionTestCase):
    """
    Mixin that has methods for comparing courses for equality.
    """

    def setUp(self):
        super().setUp()
        self.field_exclusions = set()
        self.ignored_asset_keys = set()

    def exclude_field(self, usage_id, field_name):
        """
        Mark field ``field_name`` of expected block usage ``usage_id`` as ignored

        Args:
            usage_id (:class:`opaque_keys.edx.UsageKey` or ``None``). If ``None``, skip, this field in all blocks
            field_name (string): The name of the field to skip
        """
        self.field_exclusions.add((usage_id, field_name))

    def ignore_asset_key(self, key_name):
        """
        Add an asset key to the list of keys to be ignored when comparing assets.

        Args:
            key_name: The name of the key to ignore.
        """
        self.ignored_asset_keys.add(key_name)

    def assertReferenceRelativelyEqual(self, reference_field, expected_block, actual_block):
        """
        Assert that the supplied reference field is identical on the expected_block and actual_block,
        assoming that the references are only relative (that is, comparing only on block_type and block_id,
        not course_key).
        """
        def extract_key(usage_key):
            if usage_key is None:
                return None
            else:
                return (usage_key.block_type, usage_key.block_id)
        expected = reference_field.read_from(expected_block)
        actual = reference_field.read_from(actual_block)
        if isinstance(reference_field, Reference):
            expected = extract_key(expected)
            actual = extract_key(actual)
        elif isinstance(reference_field, ReferenceList):
            expected = [extract_key(key) for key in expected]
            actual = [extract_key(key) for key in actual]
        elif isinstance(reference_field, ReferenceValueDict):
            expected = {key: extract_key(val) for (key, val) in expected.items()}
            actual = {key: extract_key(val) for (key, val) in actual.items()}
        assert expected == actual,\
            LazyFormat("Field {} doesn't match between usages {} and {}: {!r} != {!r}",
                       reference_field.name,
                       expected_block.scope_ids.usage_id,
                       actual_block.scope_ids.usage_id,
                       expected, actual)

    def assertBlocksEqualByFields(self, expected_block, actual_block):
        """
        Compare block fields to check for equivalence.
        """
        assert expected_block.fields == actual_block.fields
        for field in expected_block.fields.values():
            self.assertFieldEqual(field, expected_block, actual_block)

    def assertFieldEqual(self, field, expected_block, actual_block):
        """
        Compare a single block field for equivalence.
        """
        if isinstance(field, (Reference, ReferenceList, ReferenceValueDict)):
            self.assertReferenceRelativelyEqual(field, expected_block, actual_block)
        else:
            expected = field.read_from(expected_block)
            actual = field.read_from(actual_block)
            assert expected == actual,\
                LazyFormat("Field {} doesn't match between usages {} and {}: {!r} != {!r}",
                           field.name,
                           expected_block.scope_ids.usage_id,
                           actual_block.scope_ids.usage_id,
                           expected, actual)

    def assertCoursesEqual(self, expected_store, expected_course_key, actual_store, actual_course_key):
        """
        Assert that the courses identified by ``expected_course_key`` in ``expected_store`` and
        ``actual_course_key`` in ``actual_store`` are identical (ignore differences related
        owing to the course_keys being different).

        Any field value mentioned in ``self.field_exclusions`` by the key (usage_id, field_name)
        will be ignored for the purpose of equality checking.
        """
        # compare published
        with expected_store.branch_setting(ModuleStoreEnum.Branch.published_only, expected_course_key):
            with actual_store.branch_setting(ModuleStoreEnum.Branch.published_only, actual_course_key):
                expected_items = expected_store.get_items(expected_course_key, revision=ModuleStoreEnum.RevisionOption.published_only)  # lint-amnesty, pylint: disable=line-too-long
                actual_items = actual_store.get_items(actual_course_key, revision=ModuleStoreEnum.RevisionOption.published_only)  # lint-amnesty, pylint: disable=line-too-long
                assert len(expected_items) > 0
                self._assertCoursesEqual(expected_items, actual_items, actual_course_key)

        # if the modulestore supports having a draft branch
        if isinstance(expected_store, ModuleStoreDraftAndPublished):
            with expected_store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, expected_course_key):
                with actual_store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, actual_course_key):
                    # compare draft
                    if expected_store.get_modulestore_type(None) == ModuleStoreEnum.Type.split:
                        revision = ModuleStoreEnum.RevisionOption.draft_only
                    else:
                        revision = None
                    expected_items = expected_store.get_items(expected_course_key, revision=revision)
                    if actual_store.get_modulestore_type(None) == ModuleStoreEnum.Type.split:
                        revision = ModuleStoreEnum.RevisionOption.draft_only
                    else:
                        revision = None
                    actual_items = actual_store.get_items(actual_course_key, revision=revision)
                    self._assertCoursesEqual(expected_items, actual_items, actual_course_key, expect_drafts=True)

    def _assertCoursesEqual(self, expected_items, actual_items, actual_course_key, expect_drafts=False):  # lint-amnesty, pylint: disable=unused-argument
        """
        Actual algorithm to compare courses.
        """

        assert len(expected_items) == len(actual_items)

        def map_key(usage_key):
            return (usage_key.block_type, usage_key.block_id)
        actual_item_map = {
            map_key(item.location): item
            for item in actual_items
        }
        # Split Mongo and Old-Mongo disagree about what the block_id of courses is, so skip those in
        # this comparison
        self.assertCountEqual(
            [map_key(item.location) for item in expected_items if item.scope_ids.block_type != 'course'],
            [key for key in actual_item_map.keys() if key[0] != 'course'],
        )
        for expected_item in expected_items:
            actual_item_location = actual_course_key.make_usage_key(expected_item.category, expected_item.location.block_id)  # lint-amnesty, pylint: disable=line-too-long
            # split and old mongo use different names for the course root but we don't know which
            # modulestore actual's come from here; so, assume old mongo and if that fails, assume split
            if expected_item.location.block_type == 'course':
                actual_item_location = actual_item_location.replace(name=actual_item_location.run)
            actual_item = actual_item_map.get(map_key(actual_item_location))
            # must be split
            if actual_item is None and expected_item.location.block_type == 'course':
                actual_item_location = actual_item_location.replace(name='course')
                actual_item = actual_item_map.get(map_key(actual_item_location))
            # Formatting the message slows down tests of large courses significantly, so only do it if it would be used
            assert map_key(actual_item_location) in list(actual_item_map.keys())
            if actual_item is None:
                continue
            # compare fields
            assert expected_item.fields == actual_item.fields
            for field_name, field in expected_item.fields.items():
                if (expected_item.scope_ids.usage_id, field_name) in self.field_exclusions:
                    continue
                if (None, field_name) in self.field_exclusions:
                    continue
                # Children are handled specially
                if field_name == 'children':
                    continue
                self.assertFieldEqual(field, expected_item, actual_item)
            # compare children
            assert expected_item.has_children == actual_item.has_children
            if expected_item.has_children:
                expected_children = [
                    (expected_item_child.location.block_type, expected_item_child.location.block_id)
                    # get_children() rather than children to strip privates from public parents
                    for expected_item_child in expected_item.get_children()
                ]
                actual_children = [
                    (item_child.location.block_type, item_child.location.block_id)
                    # get_children() rather than children to strip privates from public parents
                    for item_child in actual_item.get_children()
                ]
                assert expected_children == actual_children

    def assertAssetEqual(self, expected_course_key, expected_asset, actual_course_key, actual_asset):
        """
        Assert that two assets are equal, allowing for differences related to their being from different courses.
        """
        for key in self.ignored_asset_keys:
            if key in expected_asset:
                del expected_asset[key]
            if key in actual_asset:
                del actual_asset[key]

        expected_key = expected_asset.pop('asset_key')
        actual_key = actual_asset.pop('asset_key')
        assert expected_key.map_into_course(actual_course_key) == actual_key
        assert expected_key == actual_key.map_into_course(expected_course_key)

        expected_filename = expected_asset.pop('filename')
        actual_filename = actual_asset.pop('filename')
        assert str(expected_key) == expected_filename
        assert str(actual_key) == actual_filename
        assert expected_asset == actual_asset

    def _assertAssetsEqual(self, expected_course_key, expected_assets, actual_course_key, actual_assets):  # pylint: disable=invalid-name
        """
        Private helper method for assertAssetsEqual
        """
        assert len(expected_assets) == len(actual_assets)

        actual_assets_map = {asset['asset_key']: asset for asset in actual_assets}
        for expected_item in expected_assets:
            actual_item = actual_assets_map[expected_item['asset_key'].map_into_course(actual_course_key)]
            self.assertAssetEqual(expected_course_key, expected_item, actual_course_key, actual_item)

    def assertAssetsEqual(self, expected_store, expected_course_key, actual_store, actual_course_key):
        """
        Assert that the course assets identified by ``expected_course_key`` in ``expected_store`` and
        ``actual_course_key`` in ``actual_store`` are identical, allowing for differences related
        to their being from different course keys.
        """
        expected_content, expected_count = expected_store.get_all_content_for_course(expected_course_key)
        actual_content, actual_count = actual_store.get_all_content_for_course(actual_course_key)

        assert expected_count == actual_count
        self._assertAssetsEqual(expected_course_key, expected_content, actual_course_key, actual_content)
        expected_thumbs = expected_store.get_all_content_thumbnails_for_course(expected_course_key)
        actual_thumbs = actual_store.get_all_content_thumbnails_for_course(actual_course_key)
        self._assertAssetsEqual(expected_course_key, expected_thumbs, actual_course_key, actual_thumbs)

    def assertAssetsMetadataEqual(self, expected_modulestore, expected_course_key, actual_modulestore, actual_course_key):  # lint-amnesty, pylint: disable=line-too-long
        """
        Assert that the modulestore asset metdata for the ``expected_course_key`` and the ``actual_course_key``
        are equivalent.
        """
        expected_course_assets = expected_modulestore.get_all_asset_metadata(
            expected_course_key, None, sort=('displayname', ModuleStoreEnum.SortOrder.descending)
        )
        actual_course_assets = actual_modulestore.get_all_asset_metadata(
            actual_course_key, None, sort=('displayname', ModuleStoreEnum.SortOrder.descending)
        )
        assert len(expected_course_assets) == len(actual_course_assets)
        for idx, expected_val in enumerate(expected_course_assets):
            for attr in AssetMetadata.ATTRS_ALLOWED_TO_UPDATE:
                if attr in ('edited_on',):
                    # edited_on is updated upon import.
                    continue
                assert getattr(expected_val, attr) == getattr(actual_course_assets[idx], attr)
