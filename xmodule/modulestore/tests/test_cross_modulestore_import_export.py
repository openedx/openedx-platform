"""
This suite of tests verifies that courses exported from one modulestore can be imported into
another modulestore and the result will be identical (ignoring changes to identifiers that are
the result of being imported into a course with a different course id).

It does this by providing facilities for creating and cleaning up each of the modulestore types,
and then for each combination of modulestores, performing the sequence:
    1) use xml_importer to read a course from xml from disk into the first modulestore (called the source)
    2) use xml_exporter to dump the course from the source modulestore to disk
    3) use xml_importer to read the dumped course into a second modulestore (called the destination)
    4) Compare all modules in the source and destination modulestores to make sure that they line up

"""


import itertools
import os
from shutil import rmtree
from tempfile import mkdtemp
from unittest.mock import patch

import ddt
from path import Path as path

from openedx.core.lib.tests import attr
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.utils import (
    CONTENTSTORE_SETUPS,
    SPLIT_MODULESTORE_SETUP,
    TEST_DATA_DIR,
    MongoContentstoreBuilder,
)
from xmodule.modulestore.xml_exporter import export_course_to_xml
from xmodule.modulestore.xml_importer import import_course_from_xml
from xmodule.partitions.tests.test_partitions import PartitionTestCase
from xmodule.tests import CourseComparisonTest

COURSE_DATA_NAMES = (
    'toy',
    'manual-testing-complete',
    'split_test_block',
    'split_test_block_draft',
)

EXPORTED_COURSE_DIR_NAME = 'exported_source_course'


@ddt.ddt
@attr('mongo')
class CrossStoreXMLRoundtrip(CourseComparisonTest, PartitionTestCase):
    """
    This class exists to test XML import and export between different modulestore
    classes.
    """

    def setUp(self):
        super().setUp()
        self.export_dir = mkdtemp()
        self.addCleanup(rmtree, self.export_dir, ignore_errors=True)

    @patch('xmodule.video_block.video_block.edxval_api', None)
    @ddt.data(*itertools.product(
        (SPLIT_MODULESTORE_SETUP,),
        (SPLIT_MODULESTORE_SETUP,),
        CONTENTSTORE_SETUPS,
        CONTENTSTORE_SETUPS,
        COURSE_DATA_NAMES,
    ))
    @ddt.unpack
    def test_round_trip(
            self, source_builder, dest_builder, source_content_builder,
            dest_content_builder, course_data_name,
    ):
        # Construct the contentstore for storing the first import
        with source_content_builder.build() as source_content:
            # Construct the modulestore for storing the first import (using the previously created contentstore)
            with source_builder.build(contentstore=source_content) as source_store:
                # Construct the contentstore for storing the second import
                with dest_content_builder.build() as dest_content:
                    # Construct the modulestore for storing the second import (using the second contentstore)
                    with dest_builder.build(contentstore=dest_content) as dest_store:
                        source_course_key = source_store.make_course_key('a', 'course', 'course')
                        dest_course_key = dest_store.make_course_key('a', 'course', 'course')

                        import_course_from_xml(
                            source_store,
                            ModuleStoreEnum.UserID.test,
                            TEST_DATA_DIR,
                            source_dirs=[course_data_name],
                            static_content_store=source_content,
                            target_id=source_course_key,
                            raise_on_failure=True,
                            create_if_not_present=True,
                        )

                        export_course_to_xml(
                            source_store,
                            source_content,
                            source_course_key,
                            self.export_dir,
                            EXPORTED_COURSE_DIR_NAME,
                        )

                        import_course_from_xml(
                            dest_store,
                            ModuleStoreEnum.UserID.test,
                            self.export_dir,
                            source_dirs=[EXPORTED_COURSE_DIR_NAME],
                            static_content_store=dest_content,
                            target_id=dest_course_key,
                            raise_on_failure=True,
                            create_if_not_present=True,
                        )

                        # NOT CURRENTLY USED
                        # export_course_to_xml(
                        #     dest_store,
                        #     dest_content,
                        #     dest_course_key,
                        #     self.export_dir,
                        #     'exported_dest_course',
                        # )

                        self.exclude_field(None, 'wiki_slug')
                        self.exclude_field(None, 'xml_attributes')
                        self.exclude_field(None, 'parent')
                        # discussion_ids are auto-generated based on usage_id, so they should change across
                        # modulestores - see TNL-5001
                        self.exclude_field(None, 'discussion_id')
                        self.ignore_asset_key('_id')
                        self.ignore_asset_key('uploadDate')
                        self.ignore_asset_key('content_son')
                        self.ignore_asset_key('thumbnail_location')

                        self.assertCoursesEqual(
                            source_store,
                            source_course_key,
                            dest_store,
                            dest_course_key,
                        )

                        self.assertAssetsEqual(
                            source_content,
                            source_course_key,
                            dest_content,
                            dest_course_key,
                        )

                        self.assertAssetsMetadataEqual(
                            source_store,
                            source_course_key,
                            dest_store,
                            dest_course_key,
                        )

    def test_split_course_export_import(self):
        # Construct the contentstore for storing the first import
        with MongoContentstoreBuilder().build() as source_content:
            # Construct the modulestore for storing the first import (using the previously created contentstore)
            with SPLIT_MODULESTORE_SETUP.build(contentstore=source_content) as source_store:
                # Construct the contentstore for storing the second import
                with MongoContentstoreBuilder().build() as dest_content:
                    # Construct the modulestore for storing the second import (using the second contentstore)
                    with SPLIT_MODULESTORE_SETUP.build(contentstore=dest_content) as dest_store:
                        source_course_key = source_store.make_course_key('a', 'source', '2015_Fall')  # lint-amnesty, pylint: disable=no-member
                        dest_course_key = dest_store.make_course_key('a', 'dest', '2015_Fall')  # lint-amnesty, pylint: disable=no-member

                        import_course_from_xml(
                            source_store,
                            ModuleStoreEnum.UserID.test,
                            TEST_DATA_DIR,
                            source_dirs=['split_course_with_static_tabs'],
                            static_content_store=source_content,
                            target_id=source_course_key,
                            raise_on_failure=True,
                            create_if_not_present=True,
                        )

                        export_course_to_xml(
                            source_store,
                            source_content,
                            source_course_key,
                            self.export_dir,
                            EXPORTED_COURSE_DIR_NAME,
                        )

                        source_course = source_store.get_course(source_course_key, depth=None, lazy=False)  # lint-amnesty, pylint: disable=no-member

                        assert source_course.url_name == 'course'

                        export_dir_path = path(self.export_dir)
                        policy_dir = export_dir_path / 'exported_source_course' / 'policies' / source_course_key.run
                        policy_path = policy_dir / 'policy.json'
                        assert os.path.exists(policy_path)

                        import_course_from_xml(
                            dest_store,
                            ModuleStoreEnum.UserID.test,
                            self.export_dir,
                            source_dirs=[EXPORTED_COURSE_DIR_NAME],
                            static_content_store=dest_content,
                            target_id=dest_course_key,
                            raise_on_failure=True,
                            create_if_not_present=True,
                        )

                        dest_course = dest_store.get_course(dest_course_key, depth=None, lazy=False)  # lint-amnesty, pylint: disable=no-member

                        assert dest_course.url_name == 'course'
