"""
 Test contentstore.mongo functionality
"""


import logging
import mimetypes
import shutil
import unittest
from tempfile import mkdtemp
from uuid import uuid4

import ddt
import path
import pytest
from opaque_keys.edx.keys import AssetKey
from opaque_keys.edx.locator import AssetLocator, CourseLocator

from xmodule.contentstore.content import StaticContent
from xmodule.contentstore.mongo import MongoContentStore
from xmodule.exceptions import NotFoundError
from xmodule.modulestore.tests.mongo_connection import MONGO_HOST, MONGO_PORT_NUM
from xmodule.tests import DATA_DIR

log = logging.getLogger(__name__)

HOST = MONGO_HOST
PORT = MONGO_PORT_NUM
DB = 'test_mongo_%s' % uuid4().hex[:5]


@ddt.ddt
class TestContentstore(unittest.TestCase):
    """
    Test the methods in contentstore.mongo using deprecated and non-deprecated keys
    """

    # don't use these 2 class vars as they restore behavior once the tests are done
    asset_deprecated = None
    ssck_deprecated = None

    @classmethod
    def tearDownClass(cls):
        """
        Restores deprecated values
        """
        if cls.asset_deprecated is not None:
            AssetLocator.deprecated = cls.asset_deprecated
        else:
            del AssetLocator.deprecated
        if cls.ssck_deprecated is not None:
            CourseLocator.deprecated = cls.ssck_deprecated
        else:
            del CourseLocator.deprecated
        return super().tearDownClass()

    def set_up_assets(self, deprecated):
        """
        Setup contentstore w/ proper overriding of deprecated.
        """
        # since MongoModuleStore and MongoContentStore are basically assumed to be together, create this class
        # as well
        self.contentstore = MongoContentStore(HOST, DB, port=PORT)  # lint-amnesty, pylint: disable=attribute-defined-outside-init
        self.addCleanup(self.contentstore._drop_database)  # pylint: disable=protected-access

        AssetLocator.deprecated = deprecated
        CourseLocator.deprecated = deprecated

        self.course1_key = CourseLocator('test', 'asset_test', '2014_07')  # lint-amnesty, pylint: disable=attribute-defined-outside-init
        self.course2_key = CourseLocator('test', 'asset_test2', '2014_07')  # lint-amnesty, pylint: disable=attribute-defined-outside-init

        self.course1_files = ['contains.sh', 'picture1.jpg', 'picture2.jpg']  # lint-amnesty, pylint: disable=attribute-defined-outside-init
        self.course2_files = ['picture1.jpg', 'picture3.jpg', 'door_2.ogg']  # lint-amnesty, pylint: disable=attribute-defined-outside-init

        def load_assets(course_key, files):
            locked = False
            for filename in files:
                asset_key = course_key.make_asset_key('asset', filename)
                self.save_asset(filename, asset_key, filename, locked)
                locked = not locked

        load_assets(self.course1_key, self.course1_files)
        load_assets(self.course2_key, self.course2_files)

    def save_asset(self, filename, asset_key, displayname, locked):
        """
        Load and save the given file.
        """
        with open(f"{DATA_DIR}/static/{filename}", "rb") as f:
            content = StaticContent(
                asset_key, displayname, mimetypes.guess_type(filename)[0], f.read(),
                locked=locked
            )
            self.contentstore.save(content)

    @ddt.data(True, False)
    def test_delete(self, deprecated):
        """
        Test that deleting assets works
        """
        self.set_up_assets(deprecated)
        asset_key = self.course1_key.make_asset_key('asset', self.course1_files[0])
        self.contentstore.delete(asset_key)

        with pytest.raises(NotFoundError):
            self.contentstore.find(asset_key)

        # ensure deleting a non-existent file is a noop
        self.contentstore.delete(asset_key)

    @ddt.data(True, False)
    def test_find(self, deprecated):
        """
        Test using find
        """
        self.set_up_assets(deprecated)
        asset_key = self.course1_key.make_asset_key('asset', self.course1_files[0])
        assert self.contentstore.find(asset_key) is not None, f'Could not find {asset_key}'

        assert self.contentstore.find(asset_key, as_stream=True) is not None, f'Could not find {asset_key}'

        unknown_asset = self.course1_key.make_asset_key('asset', 'no_such_file.gif')
        with pytest.raises(NotFoundError):
            self.contentstore.find(unknown_asset)
        assert self.contentstore.find(unknown_asset, throw_on_not_found=False) is None,\
            f'Found unknown asset {unknown_asset}'

    @ddt.data(True, False)
    def test_export_for_course(self, deprecated):
        """
        Test export
        """
        self.set_up_assets(deprecated)
        root_dir = path.Path(mkdtemp())
        try:
            self.contentstore.export_all_for_course(
                self.course1_key, root_dir,
                path.Path(root_dir / "policy.json"),
            )
            for filename in self.course1_files:
                filepath = path.Path(root_dir / filename)
                assert filepath.isfile(), f'{filepath} is not a file'
            for filename in self.course2_files:
                if filename not in self.course1_files:
                    filepath = path.Path(root_dir / filename)
                    assert not filepath.isfile(), f'{filepath} is unexpected exported a file'
        finally:
            shutil.rmtree(root_dir)

    @ddt.data(True, False)
    def test_get_all_content(self, deprecated):
        """
        Test get_all_content_for_course
        """
        self.set_up_assets(deprecated)
        course1_assets, count = self.contentstore.get_all_content_for_course(self.course1_key)
        assert count == len(self.course1_files), course1_assets
        for asset in course1_assets:
            parsed = AssetKey.from_string(asset['filename'])
            assert parsed.block_id in self.course1_files

        course1_assets, __ = self.contentstore.get_all_content_for_course(self.course1_key, 1, 1)
        assert len(course1_assets) == 1, course1_assets

        fake_course = CourseLocator('test', 'fake', 'non')
        course_assets, count = self.contentstore.get_all_content_for_course(fake_course)
        assert count == 0
        assert not course_assets

    @ddt.data(True, False)
    def test_attrs(self, deprecated):
        """
        Test setting and getting attrs
        """
        self.set_up_assets(deprecated)
        for filename in self.course1_files:
            asset_key = self.course1_key.make_asset_key('asset', filename)
            prelocked = self.contentstore.get_attr(asset_key, 'locked', False)
            self.contentstore.set_attr(asset_key, 'locked', not prelocked)
            assert self.contentstore.get_attr(asset_key, 'locked', False) == (not prelocked)

    @ddt.data(True, False)
    def test_copy_assets(self, deprecated):
        """
        copy_all_course_assets
        """
        self.set_up_assets(deprecated)
        dest_course = CourseLocator('test', 'destination', 'copy')
        self.contentstore.copy_all_course_assets(self.course1_key, dest_course)
        for filename in self.course1_files:
            asset_key = self.course1_key.make_asset_key('asset', filename)
            dest_key = dest_course.make_asset_key('asset', filename)
            source = self.contentstore.find(asset_key)
            copied = self.contentstore.find(dest_key)
            for propname in ['name', 'content_type', 'length', 'locked']:
                assert getattr(source, propname) == getattr(copied, propname)

        __, count = self.contentstore.get_all_content_for_course(dest_course)
        assert count == len(self.course1_files)

    @ddt.data(True, False)
    def test_copy_assets_with_duplicates(self, deprecated):
        """
        Copy all assets even if the destination has some duplicate assets
        """
        self.set_up_assets(deprecated)
        dest_course = self.course2_key
        self.contentstore.copy_all_course_assets(self.course1_key, dest_course)

        __, count = self.contentstore.get_all_content_for_course(dest_course)
        assert count == 5

    @ddt.data(True, False)
    def test_delete_assets(self, deprecated):
        """
        delete_all_course_assets
        """
        self.set_up_assets(deprecated)
        self.contentstore.delete_all_course_assets(self.course1_key)
        __, count = self.contentstore.get_all_content_for_course(self.course1_key)
        assert count == 0
        # ensure it didn't remove any from other course
        __, count = self.contentstore.get_all_content_for_course(self.course2_key)
        assert count == len(self.course2_files)
