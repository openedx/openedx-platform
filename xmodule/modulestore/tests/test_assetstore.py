"""
Tests for assetstore using any of the modulestores for metadata. May extend to testing the storage options
too.
"""


import unittest
from datetime import datetime, timedelta
import pytest
import ddt
from zoneinfo import ZoneInfo

from django.test import TestCase
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import CourseLocator

from openedx.core.lib.tests import attr
from xmodule.assetstore import AssetMetadata
from xmodule.modulestore import IncorrectlySortedList, ModuleStoreEnum, SortedAssetList
from xmodule.modulestore.exceptions import ItemNotFoundError
from xmodule.modulestore.tests.factories import CourseFactory
from xmodule.modulestore.tests.utils import (
    SPLIT_MODULESTORE_SETUP,
    MixedModulestoreBuilder,
    XmlModulestoreBuilder
)


class AssetStoreTestData:
    """
    Shared data for constructing test assets.
    """
    now = datetime.now(ZoneInfo("UTC"))
    user_id = 144
    user_id_long = int(user_id)

    user_email = "me@example.com"

    asset_fields = (
        AssetMetadata.ASSET_BASENAME_ATTR, 'internal_name', 'pathname', 'locked',
        'edited_by', 'edited_by_email', 'edited_on', 'created_by', 'created_by_email', 'created_on',
        'curr_version', 'prev_version'
    )
    all_asset_data = (
        ('pic1.jpg', 'EKMND332DDBK', 'pix/archive', False,
            user_id_long, user_email, now + timedelta(seconds=10 * 1), user_id_long, user_email, now, '14', '13'),
        ('shout.ogg', 'KFMDONSKF39K', 'sounds', True,
            user_id, user_email, now + timedelta(seconds=10 * 2), user_id, user_email, now, '1', None),
        ('code.tgz', 'ZZB2333YBDMW', 'exercises/14', False,
            user_id * 2, user_email, now + timedelta(seconds=10 * 3), user_id * 2, user_email, now, 'AB', 'AA'),
        ('dog.png', 'PUPY4242X', 'pictures/animals', True,
            user_id_long * 3, user_email, now + timedelta(seconds=10 * 4), user_id_long * 3, user_email, now, '5', '4'),
        ('not_here.txt', 'JJJCCC747', '/dev/null', False,
            user_id * 4, user_email, now + timedelta(seconds=10 * 5), user_id * 4, user_email, now, '50', '49'),
        ('asset.txt', 'JJJCCC747858', '/dev/null', False,
            user_id * 4, user_email, now + timedelta(seconds=10 * 6), user_id * 4, user_email, now, '50', '49'),
        ('roman_history.pdf', 'JASDUNSADK', 'texts/italy', True,
            user_id * 7, user_email, now + timedelta(seconds=10 * 7), user_id * 7, user_email, now, '1.1', '1.01'),
        ('weather_patterns.bmp', '928SJXX2EB', 'science', False,
            user_id * 8, user_email, now + timedelta(seconds=10 * 8), user_id * 8, user_email, now, '52', '51'),
        ('demo.swf', 'DFDFGGGG14', 'demos/easy', False,
            user_id * 9, user_email, now + timedelta(seconds=10 * 9), user_id * 9, user_email, now, '5', '4'),
    )


class TestSortedAssetList(unittest.TestCase):
    """
    Tests the SortedAssetList class.
    """

    def setUp(self):
        super().setUp()
        asset_list = [dict(list(zip(AssetStoreTestData.asset_fields, asset))) for asset in AssetStoreTestData.all_asset_data]  # lint-amnesty, pylint: disable=line-too-long
        self.sorted_asset_list_by_filename = SortedAssetList(iterable=asset_list)
        self.sorted_asset_list_by_last_edit = SortedAssetList(iterable=asset_list, key=lambda x: x['edited_on'])
        self.course_key = CourseLocator('org', 'course', 'run')

    def test_exception_on_bad_sort(self):
        asset_key = self.course_key.make_asset_key('asset', 'pic1.jpg')
        with pytest.raises(IncorrectlySortedList):
            __ = self.sorted_asset_list_by_last_edit.find(asset_key)

    def test_find(self):
        asset_key = self.course_key.make_asset_key('asset', 'asset.txt')
        assert self.sorted_asset_list_by_filename.find(asset_key) == 0
        asset_key_last = self.course_key.make_asset_key('asset', 'weather_patterns.bmp')
        assert self.sorted_asset_list_by_filename.find(asset_key_last) == (len(AssetStoreTestData.all_asset_data) - 1)


@attr('mongo')
@ddt.ddt
class TestMongoAssetMetadataStorage(TestCase):
    """
    Tests for storing/querying course asset metadata.
    """
    XML_MODULESTORE_MAP = {
        'XML_MODULESTORE_BUILDER': XmlModulestoreBuilder(),
        'MIXED_MODULESTORE_BUILDER': MixedModulestoreBuilder([('xml', XmlModulestoreBuilder())])
    }

    def setUp(self):
        super().setUp()

        self.differents = (('different', 'burn.jpg'),)
        self.vrmls = (
            ('vrml', 'olympus_mons.vrml'),
            ('vrml', 'ponte_vecchio.vrml'),
        )
        self.regular_assets = (('asset', 'zippy.png'),)
        self.alls = self.differents + self.vrmls + self.regular_assets

    def _assert_metadata_equal(self, mdata1, mdata2):
        """
        So we can use the below date comparison
        """
        for attr in mdata1.ATTRS_ALLOWED_TO_UPDATE:  # lint-amnesty, pylint: disable=redefined-outer-name
            if attr == "edited_on":
                continue  # The edited_on gets updated during save, so comparing it makes tests flaky.
            if isinstance(getattr(mdata1, attr), datetime):
                self._assert_datetimes_equal(getattr(mdata1, attr), getattr(mdata2, attr))
            else:
                assert getattr(mdata1, attr) == getattr(mdata2, attr)

    def _assert_datetimes_equal(self, datetime1, datetime2):
        """
        Don't compare microseconds as mongo doesn't encode below milliseconds
        """
        assert datetime1.replace(microsecond=0) == datetime2.replace(microsecond=0)

    def _make_asset_metadata(self, asset_loc):
        """
        Make a single test asset metadata.
        """
        now = datetime.now(ZoneInfo("UTC"))
        return AssetMetadata(
            asset_loc, internal_name='EKMND332DDBK',
            pathname='pictures/historical', contenttype='image/jpeg',
            locked=False, fields={'md5': '77631ca4f0e08419b70726a447333ab6'},
            edited_by=ModuleStoreEnum.UserID.test, edited_on=now,
            created_by=ModuleStoreEnum.UserID.test, created_on=now,
            curr_version='v1.0', prev_version='v0.95'
        )

    def _make_asset_thumbnail_metadata(self, asset_md):
        """
        Add thumbnail to the asset_md
        """
        asset_md.thumbnail = 'ABC39XJUDN2'
        return asset_md

    def setup_assets(self, course1_key, course2_key, store=None):
        """
        Setup assets. Save in store if given
        """
        for i, asset in enumerate(AssetStoreTestData.all_asset_data):
            asset_dict = dict(list(zip(AssetStoreTestData.asset_fields[1:], asset[1:])))
            if i in (0, 1) and course1_key:
                asset_key = course1_key.make_asset_key('asset', asset[0])
                asset_md = AssetMetadata(asset_key, **asset_dict)
                if store is not None:
                    store.save_asset_metadata(asset_md, asset[4])
            elif course2_key:
                asset_key = course2_key.make_asset_key('asset', asset[0])
                asset_md = AssetMetadata(asset_key, **asset_dict)
                # Don't save assets 5 and 6.
                if store is not None and i not in (4, 5):
                    store.save_asset_metadata(asset_md, asset[4])

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_save_one_and_confirm(self, storebuilder):
        """
        Save the metadata in each store and retrieve it singularly, as all assets, and after deleting all.
        """
        with storebuilder.build() as (__, store):
            course = CourseFactory.create(modulestore=store)
            asset_filename = 'burnside.jpg'
            new_asset_loc = course.id.make_asset_key('asset', asset_filename)
            # Save the asset's metadata.
            new_asset_md = self._make_asset_metadata(new_asset_loc)
            store.save_asset_metadata(new_asset_md, ModuleStoreEnum.UserID.test)
            # Find the asset's metadata and confirm it's the same.
            found_asset_md = store.find_asset_metadata(new_asset_loc)
            assert found_asset_md is not None
            self._assert_metadata_equal(new_asset_md, found_asset_md)
            assert len(store.get_all_asset_metadata(course.id, 'asset')) == 1

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_delete(self, storebuilder):
        """
        Delete non-existent and existent metadata
        """
        with storebuilder.build() as (__, store):
            course = CourseFactory.create(modulestore=store)
            new_asset_loc = course.id.make_asset_key('asset', 'burnside.jpg')
            # Attempt to delete an asset that doesn't exist.
            assert store.delete_asset_metadata(new_asset_loc, ModuleStoreEnum.UserID.test) == 0
            assert len(store.get_all_asset_metadata(course.id, 'asset')) == 0

            new_asset_md = self._make_asset_metadata(new_asset_loc)
            store.save_asset_metadata(new_asset_md, ModuleStoreEnum.UserID.test)
            assert store.delete_asset_metadata(new_asset_loc, ModuleStoreEnum.UserID.test) == 1
            assert len(store.get_all_asset_metadata(course.id, 'asset')) == 0

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_find_non_existing_assets(self, storebuilder):
        """
        Find a non-existent asset in an existing course.
        """
        with storebuilder.build() as (__, store):
            course = CourseFactory.create(modulestore=store)
            new_asset_loc = course.id.make_asset_key('asset', 'burnside.jpg')
            # Find existing asset metadata.
            asset_md = store.find_asset_metadata(new_asset_loc)
            assert asset_md is None

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_get_all_non_existing_assets(self, storebuilder):
        """
        Get all assets in an existing course when no assets exist.
        """
        with storebuilder.build() as (__, store):
            course = CourseFactory.create(modulestore=store)
            # Find existing asset metadata.
            asset_md = store.get_all_asset_metadata(course.id, 'asset')
            assert asset_md == []

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_find_assets_in_non_existent_course(self, storebuilder):
        """
        Find asset metadata from a non-existent course.
        """
        with storebuilder.build() as (__, store):
            course = CourseFactory.create(modulestore=store)
            fake_course_id = CourseKey.from_string("{}nothere/{}nothere/{}nothere".format(
                course.id.org, course.id.course, course.id.run
            ))
            new_asset_loc = fake_course_id.make_asset_key('asset', 'burnside.jpg')
            # Find asset metadata from non-existent course.
            with pytest.raises(ItemNotFoundError):
                store.find_asset_metadata(new_asset_loc)
            with pytest.raises(ItemNotFoundError):
                store.get_all_asset_metadata(fake_course_id, 'asset')

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_add_same_asset_twice(self, storebuilder):
        """
        Add an asset's metadata, then add it again.
        """
        with storebuilder.build() as (__, store):
            course = CourseFactory.create(modulestore=store)
            new_asset_loc = course.id.make_asset_key('asset', 'burnside.jpg')
            new_asset_md = self._make_asset_metadata(new_asset_loc)
            # Add asset metadata.
            store.save_asset_metadata(new_asset_md, ModuleStoreEnum.UserID.test)
            assert len(store.get_all_asset_metadata(course.id, 'asset')) == 1
            # Add *the same* asset metadata.
            store.save_asset_metadata(new_asset_md, ModuleStoreEnum.UserID.test)
            # Still one here?
            assert len(store.get_all_asset_metadata(course.id, 'asset')) == 1

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_different_asset_types(self, storebuilder):
        """
        Test saving assets with other asset types.
        """
        with storebuilder.build() as (__, store):
            course = CourseFactory.create(modulestore=store)
            new_asset_loc = course.id.make_asset_key('vrml', 'pyramid.vrml')
            new_asset_md = self._make_asset_metadata(new_asset_loc)
            # Add asset metadata.
            store.save_asset_metadata(new_asset_md, ModuleStoreEnum.UserID.test)
            assert len(store.get_all_asset_metadata(course.id, 'vrml')) == 1
            assert len(store.get_all_asset_metadata(course.id, 'asset')) == 0

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_asset_types_with_other_field_names(self, storebuilder):
        """
        Test saving assets using an asset type of 'course_id'.
        """
        with storebuilder.build() as (__, store):
            course = CourseFactory.create(modulestore=store)
            new_asset_loc = course.id.make_asset_key('course_id', 'just_to_see_if_it_still_works.jpg')
            new_asset_md = self._make_asset_metadata(new_asset_loc)
            # Add asset metadata.
            store.save_asset_metadata(new_asset_md, ModuleStoreEnum.UserID.test)
            assert len(store.get_all_asset_metadata(course.id, 'course_id')) == 1
            assert len(store.get_all_asset_metadata(course.id, 'asset')) == 0
            all_assets = store.get_all_asset_metadata(course.id, 'course_id')
            assert all_assets[0].asset_id.path == new_asset_loc.path

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_lock_unlock_assets(self, storebuilder):
        """
        Save multiple metadata in each store and retrieve it singularly, as all assets, and after deleting all.
        """
        with storebuilder.build() as (__, store):
            course = CourseFactory.create(modulestore=store)
            new_asset_loc = course.id.make_asset_key('asset', 'burnside.jpg')
            new_asset_md = self._make_asset_metadata(new_asset_loc)
            store.save_asset_metadata(new_asset_md, ModuleStoreEnum.UserID.test)

            locked_state = new_asset_md.locked
            # Flip the course asset's locked status.
            store.set_asset_metadata_attr(new_asset_loc, "locked", not locked_state, ModuleStoreEnum.UserID.test)
            # Find the same course and check its locked status.
            updated_asset_md = store.find_asset_metadata(new_asset_loc)
            assert updated_asset_md is not None
            assert updated_asset_md.locked == (not locked_state)
            # Now flip it back.
            store.set_asset_metadata_attr(new_asset_loc, "locked", locked_state, ModuleStoreEnum.UserID.test)
            reupdated_asset_md = store.find_asset_metadata(new_asset_loc)
            assert reupdated_asset_md is not None
            assert reupdated_asset_md.locked == locked_state

    ALLOWED_ATTRS = (
        ('pathname', '/new/path'),
        ('internal_name', 'new_filename.txt'),
        ('locked', True),
        ('contenttype', 'image/png'),
        ('thumbnail', 'new_filename_thumb.jpg'),
        ('fields', {'md5': '5346682d948cc3f683635b6918f9b3d0'}),
        ('curr_version', 'v1.01'),
        ('prev_version', 'v1.0'),
        ('edited_by', 'Mork'),
        ('edited_on', datetime(1969, 1, 1, tzinfo=ZoneInfo("UTC"))),
    )

    DISALLOWED_ATTRS = (
        ('asset_id', 'IAmBogus'),
        ('created_by', 'Smith'),
        ('created_on', datetime.now(ZoneInfo("UTC"))),
    )

    UNKNOWN_ATTRS = (
        ('lunch_order', 'burger_and_fries'),
        ('villain', 'Khan')
    )

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_set_all_attrs(self, storebuilder):
        """
        Save setting each attr one at a time
        """
        with storebuilder.build() as (__, store):
            course = CourseFactory.create(modulestore=store)
            new_asset_loc = course.id.make_asset_key('asset', 'burnside.jpg')
            new_asset_md = self._make_asset_metadata(new_asset_loc)
            store.save_asset_metadata(new_asset_md, ModuleStoreEnum.UserID.test)
            for attribute, value in self.ALLOWED_ATTRS:
                # Set the course asset's attribute.
                store.set_asset_metadata_attr(new_asset_loc, attribute, value, ModuleStoreEnum.UserID.test)
                # Find the same course asset and check its changed attribute.
                updated_asset_md = store.find_asset_metadata(new_asset_loc)
                assert updated_asset_md is not None
                assert getattr(updated_asset_md, attribute, None) is not None
                assert getattr(updated_asset_md, attribute, None) == value

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_set_disallowed_attrs(self, storebuilder):
        """
        setting disallowed attrs should fail
        """
        with storebuilder.build() as (__, store):
            course = CourseFactory.create(modulestore=store)
            new_asset_loc = course.id.make_asset_key('asset', 'burnside.jpg')
            new_asset_md = self._make_asset_metadata(new_asset_loc)
            store.save_asset_metadata(new_asset_md, ModuleStoreEnum.UserID.test)
            for attribute, value in self.DISALLOWED_ATTRS:
                original_attr_val = getattr(new_asset_md, attribute)
                # Set the course asset's attribute.
                store.set_asset_metadata_attr(new_asset_loc, attribute, value, ModuleStoreEnum.UserID.test)
                # Find the same course and check its changed attribute.
                updated_asset_md = store.find_asset_metadata(new_asset_loc)
                assert updated_asset_md is not None

                updated_attr_val = getattr(updated_asset_md, attribute, None)
                assert updated_attr_val is not None
                # Make sure that the attribute is unchanged from its original value.
                if isinstance(original_attr_val, datetime):
                    self._assert_datetimes_equal(updated_attr_val, original_attr_val)
                else:
                    assert updated_attr_val == original_attr_val

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_set_unknown_attrs(self, storebuilder):
        """
        setting unknown attrs should fail
        """
        with storebuilder.build() as (__, store):
            course = CourseFactory.create(modulestore=store)
            new_asset_loc = course.id.make_asset_key('asset', 'burnside.jpg')
            new_asset_md = self._make_asset_metadata(new_asset_loc)
            store.save_asset_metadata(new_asset_md, ModuleStoreEnum.UserID.test)
            for attribute, value in self.UNKNOWN_ATTRS:
                # Set the course asset's attribute.
                store.set_asset_metadata_attr(new_asset_loc, attribute, value, ModuleStoreEnum.UserID.test)
                # Find the same course and check its changed attribute.
                updated_asset_md = store.find_asset_metadata(new_asset_loc)
                assert updated_asset_md is not None
                # Make sure the unknown field was *not* added.
                with pytest.raises(AttributeError):
                    assert getattr(updated_asset_md, attribute) == value

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_save_one_different_asset(self, storebuilder):
        """
        saving and deleting things which are not 'asset'
        """
        with storebuilder.build() as (__, store):
            course = CourseFactory.create(modulestore=store)
            asset_key = course.id.make_asset_key('different', 'burn.jpg')
            new_asset_thumbnail = self._make_asset_thumbnail_metadata(
                self._make_asset_metadata(asset_key)
            )
            store.save_asset_metadata(new_asset_thumbnail, ModuleStoreEnum.UserID.test)
            assert len(store.get_all_asset_metadata(course.id, 'different')) == 1
            assert store.delete_asset_metadata(asset_key, ModuleStoreEnum.UserID.test) == 1
            assert len(store.get_all_asset_metadata(course.id, 'different')) == 0

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_find_different(self, storebuilder):
        """
        finding things which are of type other than 'asset'
        """
        with storebuilder.build() as (__, store):
            course = CourseFactory.create(modulestore=store)
            asset_key = course.id.make_asset_key('different', 'burn.jpg')
            new_asset_thumbnail = self._make_asset_thumbnail_metadata(
                self._make_asset_metadata(asset_key)
            )
            store.save_asset_metadata(new_asset_thumbnail, ModuleStoreEnum.UserID.test)

            assert store.find_asset_metadata(asset_key) is not None
            unknown_asset_key = course.id.make_asset_key('different', 'nosuchfile.jpg')
            assert store.find_asset_metadata(unknown_asset_key) is None

    def _check_asset_values(self, assets, orig):
        """
        Check asset type/path values.
        """
        for idx, asset in enumerate(orig):
            assert assets[idx].asset_id.asset_type == asset[0]
            assert assets[idx].asset_id.path == asset[1]

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_get_multiple_types(self, storebuilder):
        """
        getting all things which are of type other than 'asset'
        """
        with storebuilder.build() as (__, store):
            course = CourseFactory.create(modulestore=store)

            # Save 'em.
            for asset_type, filename in self.alls:
                asset_key = course.id.make_asset_key(asset_type, filename)
                new_asset = self._make_asset_thumbnail_metadata(
                    self._make_asset_metadata(asset_key)
                )
                store.save_asset_metadata(new_asset, ModuleStoreEnum.UserID.test)

            # Check 'em.
            for asset_type, asset_list in (
                ('different', self.differents),
                ('vrml', self.vrmls),
                ('asset', self.regular_assets),
            ):
                assets = store.get_all_asset_metadata(course.id, asset_type)
                assert len(assets) == len(asset_list)
                self._check_asset_values(assets, asset_list)

            assert len(store.get_all_asset_metadata(course.id, 'not_here')) == 0
            assert len(store.get_all_asset_metadata(course.id, None)) == 4

            assets = store.get_all_asset_metadata(
                course.id, None, start=0, maxresults=-1,
                sort=('displayname', ModuleStoreEnum.SortOrder.ascending)
            )
            assert len(assets) == len(self.alls)
            self._check_asset_values(assets, self.alls)

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_save_metadata_list(self, storebuilder):
        """
        Save a list of asset metadata all at once.
        """
        with storebuilder.build() as (__, store):
            course = CourseFactory.create(modulestore=store)

            # Make a list of AssetMetadata objects.
            md_list = []
            for asset_type, filename in self.alls:
                asset_key = course.id.make_asset_key(asset_type, filename)
                md_list.append(self._make_asset_thumbnail_metadata(
                    self._make_asset_metadata(asset_key)
                ))

            # Save 'em.
            store.save_asset_metadata_list(md_list, ModuleStoreEnum.UserID.test)

            # Check 'em.
            for asset_type, asset_list in (
                ('different', self.differents),
                ('vrml', self.vrmls),
                ('asset', self.regular_assets),
            ):
                assets = store.get_all_asset_metadata(course.id, asset_type)
                assert len(assets) == len(asset_list)
                self._check_asset_values(assets, asset_list)

            assert len(store.get_all_asset_metadata(course.id, 'not_here')) == 0
            assert len(store.get_all_asset_metadata(course.id, None)) == 4

            assets = store.get_all_asset_metadata(
                course.id, None, start=0, maxresults=-1,
                sort=('displayname', ModuleStoreEnum.SortOrder.ascending)
            )
            assert len(assets) == len(self.alls)
            self._check_asset_values(assets, self.alls)

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_save_metadata_list_with_mismatched_asset(self, storebuilder):
        """
        Save a list of asset metadata all at once - but with one asset's metadata from a different course.
        """
        with storebuilder.build() as (__, store):
            course1 = CourseFactory.create(modulestore=store)
            course2 = CourseFactory.create(modulestore=store)

            # Make a list of AssetMetadata objects.
            md_list = []
            for asset_type, filename in self.alls:
                if asset_type == 'asset':
                    asset_key = course2.id.make_asset_key(asset_type, filename)
                else:
                    asset_key = course1.id.make_asset_key(asset_type, filename)
                md_list.append(self._make_asset_thumbnail_metadata(
                    self._make_asset_metadata(asset_key)
                ))

            # Save 'em.
            store.save_asset_metadata_list(md_list, ModuleStoreEnum.UserID.test)

            # Check 'em.
            for asset_type, asset_list in (
                ('different', self.differents),
                ('vrml', self.vrmls),
            ):
                assets = store.get_all_asset_metadata(course1.id, asset_type)
                assert len(assets) == len(asset_list)
                self._check_asset_values(assets, asset_list)

            assert len(store.get_all_asset_metadata(course1.id, 'asset')) == 0
            assert len(store.get_all_asset_metadata(course1.id, None)) == 3

            assets = store.get_all_asset_metadata(
                course1.id, None, start=0, maxresults=-1,
                sort=('displayname', ModuleStoreEnum.SortOrder.ascending)
            )
            assert len(assets) == len(self.differents + self.vrmls)
            self._check_asset_values(assets, self.differents + self.vrmls)

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_delete_all_different_type(self, storebuilder):
        """
        deleting all assets of a given but not 'asset' type
        """
        with storebuilder.build() as (__, store):
            course = CourseFactory.create(modulestore=store)
            asset_key = course.id.make_asset_key('different', 'burn_thumb.jpg')
            new_asset_thumbnail = self._make_asset_thumbnail_metadata(
                self._make_asset_metadata(asset_key)
            )
            store.save_asset_metadata(new_asset_thumbnail, ModuleStoreEnum.UserID.test)

            assert len(store.get_all_asset_metadata(course.id, 'different')) == 1

    @ddt.data('XML_MODULESTORE_BUILDER', 'MIXED_MODULESTORE_BUILDER')
    def test_xml_not_yet_implemented(self, storebuilderName):
        """
        Test coverage which shows that for now xml read operations are not implemented
        """
        storebuilder = self.XML_MODULESTORE_MAP[storebuilderName]
        with storebuilder.build(contentstore=None) as (__, store):
            course_key = store.make_course_key("org", "course", "run")
            asset_key = course_key.make_asset_key('asset', 'foo.jpg')
            assert store.find_asset_metadata(asset_key) is None
            assert store.get_all_asset_metadata(course_key, 'asset') == []

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_copy_all_assets_same_modulestore(self, storebuilder):
        """
        Create a course with assets, copy them all to another course in the same modulestore, and check on it.
        """
        with storebuilder.build() as (__, store):
            course1 = CourseFactory.create(modulestore=store)
            course2 = CourseFactory.create(modulestore=store)
            self.setup_assets(course1.id, None, store)
            assert len(store.get_all_asset_metadata(course1.id, 'asset')) == 2
            assert len(store.get_all_asset_metadata(course2.id, 'asset')) == 0
            store.copy_all_asset_metadata(course1.id, course2.id, ModuleStoreEnum.UserID.test * 101)
            assert len(store.get_all_asset_metadata(course1.id, 'asset')) == 2
            all_assets = store.get_all_asset_metadata(
                course2.id, 'asset', sort=('displayname', ModuleStoreEnum.SortOrder.ascending)
            )
            assert len(all_assets) == 2
            assert all_assets[0].asset_id.path == 'pic1.jpg'
            assert all_assets[1].asset_id.path == 'shout.ogg'

    @ddt.data(SPLIT_MODULESTORE_SETUP)
    def test_copy_all_assets_from_course_with_no_assets(self, storebuilder):
        """
        Create a course with *no* assets, and try copy them all to another course in the same modulestore.
        """
        with storebuilder.build() as (__, store):
            course1 = CourseFactory.create(modulestore=store)
            course2 = CourseFactory.create(modulestore=store)
            store.copy_all_asset_metadata(course1.id, course2.id, ModuleStoreEnum.UserID.test * 101)
            assert len(store.get_all_asset_metadata(course1.id, 'asset')) == 0
            assert len(store.get_all_asset_metadata(course2.id, 'asset')) == 0
            all_assets = store.get_all_asset_metadata(
                course2.id, 'asset', sort=('displayname', ModuleStoreEnum.SortOrder.ascending)
            )
