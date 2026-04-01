# lint-amnesty, pylint: disable=missing-module-docstring

# TODO: Rewrite several of these assertions so that they check the output of the REST or Python
# APIs rather than parsing HTML from the deprecated legacy frontend pages. In particular, any
# test case using override_waffle_flag(toggles.LEGACY_STUDIO_*, True) will need to be fixed.
# Part of https://github.com/openedx/edx-platform/issues/36275.

import copy
import re
import shutil
from datetime import timedelta
from functools import wraps
from json import loads
from textwrap import dedent
from unittest import SkipTest, mock
from uuid import uuid4

import ddt
from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_switch, override_waffle_flag
from edxval.api import create_video, get_videos_for_course
from fs.osfs import OSFS
from lxml import etree
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import AssetKey, CourseKey, UsageKey
from opaque_keys.edx.locations import CourseLocator
from path import Path as path
from xmodule.capa_block import ProblemBlock
from xmodule.contentstore.content import StaticContent
from xmodule.contentstore.django import contentstore
from xmodule.contentstore.utils import empty_asset_trashcan, restore_asset_from_trashcan
from xmodule.course_block import CourseBlock, Textbook
from xmodule.exceptions import InvalidVersionError
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError
from xmodule.modulestore.inheritance import own_metadata
from xmodule.modulestore.split_mongo import BlockKey
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory, check_mongo_calls
from xmodule.modulestore.xml_exporter import export_course_to_xml
from xmodule.modulestore.xml_importer import import_course_from_xml, perform_xlint
from xmodule.seq_block import SequenceBlock
from xmodule.video_block import VideoBlock

from cms.djangoapps.contentstore import toggles
from cms.djangoapps.contentstore.config import waffle
from cms.djangoapps.contentstore.tests.utils import AjaxEnabledTestClient, CourseTestCase, get_url, parse_json
from cms.djangoapps.contentstore.utils import (
    delete_course,
    reverse_course_url,
    reverse_url,
)
from cms.djangoapps.contentstore.views.component import ADVANCED_COMPONENT_TYPES
from common.djangoapps.course_action_state.managers import CourseActionStateItemNotFoundError
from common.djangoapps.course_action_state.models import CourseRerunState, CourseRerunUIStateManager
from common.djangoapps.student import auth
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import CourseCreatorRole, CourseInstructorRole
from openedx.core.djangoapps.django_comment_common.utils import are_permissions_roles_seeded
from openedx.core.lib.tempdir import mkdtemp_clean

TEST_DATA_CONTENTSTORE = copy.deepcopy(settings.CONTENTSTORE)
TEST_DATA_CONTENTSTORE['DOC_STORE_CONFIG']['db'] = 'test_xcontent_%s' % uuid4().hex

TEST_DATA_DIR = settings.COMMON_TEST_DATA_ROOT


def requires_pillow_jpeg(func):
    """
    A decorator to indicate that the function requires JPEG support for Pillow,
    otherwise it cannot be run
    """
    @wraps(func)
    def decorated_func(*args, **kwargs):
        """
        Execute the function if we have JPEG support in Pillow.
        """
        try:
            from PIL import Image
        except ImportError:
            raise SkipTest("Pillow is not installed (or not found)")  # lint-amnesty, pylint: disable=raise-missing-from
        if not getattr(Image.core, "jpeg_decoder", False):
            raise SkipTest("Pillow cannot open JPEG files")
        return func(*args, **kwargs)
    return decorated_func


@override_settings(CONTENTSTORE=TEST_DATA_CONTENTSTORE)
class ContentStoreTestCase(CourseTestCase):
    """
    Base class for Content Store Test Cases
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE


class ImportRequiredTestCases(ContentStoreTestCase):
    """
    Tests which legitimately need to import a course
    """

    def test_no_static_link_rewrites_on_import(self):
        course_items = import_course_from_xml(
            self.store, self.user.id, TEST_DATA_DIR, ['toy'], create_if_not_present=True
        )
        course = course_items[0]

        handouts_usage_key = course.id.make_usage_key('course_info', 'handouts')
        handouts = self.store.get_item(handouts_usage_key)
        self.assertIn('/static/', handouts.data)

        handouts_usage_key = course.id.make_usage_key('html', 'toyhtml')
        handouts = self.store.get_item(handouts_usage_key)
        self.assertIn('/static/', handouts.data)

    def test_xlint_fails(self):
        err_cnt = perform_xlint(TEST_DATA_DIR, ['toy'])
        self.assertGreater(err_cnt, 0)

    def test_invalid_asset_overwrite(self):
        """
        Tests that an asset with invalid displayname can be overwritten if multiple assets have same displayname.
        It Verifies that:
            During import, if ('/') or ('\') is present in displayname of an asset, it is replaced with underscores '_'.
            Export does not fail when an asset has '/' in its displayname. If the converted display matches with
            any other asset, then it will be replaced.

        Asset name in XML: "/invalid\\displayname/subs-esLhHcdKGWvKs.srt"
        """
        content_store = contentstore()
        expected_displayname = '_invalid_displayname_subs-esLhHcdKGWvKs.srt'

        import_course_from_xml(
            self.store,
            self.user.id,
            TEST_DATA_DIR,
            ['import_draft_order'],
            static_content_store=content_store,
            verbose=True,
            create_if_not_present=True
        )

        # Verify the course has imported successfully
        course = self.store.get_course(self.store.make_course_key(
            'test_org',
            'import_draft_order',
            'import_draft_order'
        ))
        self.assertIsNotNone(course)

        # Add a new asset in the course, and make sure to name it such that it overwrite the one existing
        # asset in the course. (i.e. _invalid_displayname_subs-esLhHcdKGWvKs.srt)
        asset_key = course.id.make_asset_key('asset', 'sample_asset.srt')
        content = StaticContent(
            asset_key, expected_displayname, 'application/text', b'test',
        )
        content_store.save(content)

        # Get & verify that course actually has two assets
        assets, count = content_store.get_all_content_for_course(course.id)
        self.assertEqual(count, 2)

        # Verify both assets have similar `displayname` after saving.
        for asset in assets:
            self.assertEqual(asset['displayname'], expected_displayname)

        # Test course export does not fail
        root_dir = path(mkdtemp_clean())
        print(f'Exporting to tempdir = {root_dir}')
        export_course_to_xml(self.store, content_store, course.id, root_dir, 'test_export')

        filesystem = OSFS(str(root_dir / 'test_export/static'))
        exported_static_files = filesystem.listdir('/')

        # Verify that asset have been overwritten during export.
        self.assertEqual(len(exported_static_files), 1)
        self.assertTrue(filesystem.exists(expected_displayname))
        self.assertEqual(exported_static_files[0], expected_displayname)

        # Remove exported course
        shutil.rmtree(root_dir)

    def test_about_overrides(self):
        """
        This test case verifies that a course can use specialized override for about data,
        e.g. /about/Fall_2012/effort.html
        while there is a base definition in /about/effort.html
        """
        course_items = import_course_from_xml(
            self.store, self.user.id, TEST_DATA_DIR, ['toy'], create_if_not_present=True
        )
        course_key = course_items[0].id
        effort = self.store.get_item(course_key.make_usage_key('about', 'effort'))
        self.assertEqual(effort.data, '6 hours')

        # this one should be in a non-override folder
        effort = self.store.get_item(course_key.make_usage_key('about', 'end_date'))
        self.assertEqual(effort.data, 'TBD')

    @requires_pillow_jpeg
    def test_asset_import(self):
        """
        This test validates that an image asset is imported and a thumbnail was generated for a .gif
        """
        content_store = contentstore()

        import_course_from_xml(
            self.store, self.user.id, TEST_DATA_DIR, ['toy'], static_content_store=content_store, verbose=True,
            create_if_not_present=True
        )

        course = self.store.get_course(self.store.make_course_key('edX', 'toy', '2012_Fall'))

        self.assertIsNotNone(course)

        # make sure we have some assets in our contentstore
        all_assets, __ = content_store.get_all_content_for_course(course.id)
        self.assertGreater(len(all_assets), 0)

        # make sure we have some thumbnails in our contentstore
        all_thumbnails = content_store.get_all_content_thumbnails_for_course(course.id)
        self.assertGreater(len(all_thumbnails), 0)

        location = AssetKey.from_string('asset-v1:edX+toy+2012_Fall+type@asset+block@just_a_test.jpg')
        content = content_store.find(location)
        self.assertIsNotNone(content)

        self.assertIsNotNone(content.thumbnail_location)
        thumbnail = content_store.find(content.thumbnail_location)
        self.assertIsNotNone(thumbnail)

    def test_course_info_updates_import_export(self):
        """
        Test that course info updates are imported and exported with all content fields ('data', 'items')
        """
        content_store = contentstore()
        data_dir = TEST_DATA_DIR
        courses = import_course_from_xml(
            self.store, self.user.id, data_dir, ['course_info_updates'],
            static_content_store=content_store, verbose=True, create_if_not_present=True
        )

        course = courses[0]
        self.assertIsNotNone(course)

        course_updates = self.store.get_item(course.id.make_usage_key('course_info', 'updates'))
        self.assertIsNotNone(course_updates)

        # check that course which is imported has files 'updates.html' and 'updates.items.json'
        filesystem = OSFS(str(data_dir + '/course_info_updates/info'))
        self.assertTrue(filesystem.exists('updates.html'))
        self.assertTrue(filesystem.exists('updates.items.json'))

        # verify that course info update module has same data content as in data file from which it is imported
        # check 'data' field content
        with filesystem.open('updates.html', 'r') as course_policy:
            on_disk = course_policy.read()
            self.assertEqual(course_updates.data, on_disk)

        # check 'items' field content
        with filesystem.open('updates.items.json', 'r') as course_policy:
            on_disk = loads(course_policy.read())
            self.assertEqual(course_updates.items, on_disk)

        # now export the course to a tempdir and test that it contains files 'updates.html' and 'updates.items.json'
        # with same content as in course 'info' directory
        root_dir = path(mkdtemp_clean())
        print(f'Exporting to tempdir = {root_dir}')
        export_course_to_xml(self.store, content_store, course.id, root_dir, 'test_export')

        # check that exported course has files 'updates.html' and 'updates.items.json'
        filesystem = OSFS(str(root_dir / 'test_export/info'))
        self.assertTrue(filesystem.exists('updates.html'))
        self.assertTrue(filesystem.exists('updates.items.json'))

        # verify that exported course has same data content as in course_info_update module
        with filesystem.open('updates.html', 'r') as grading_policy:
            on_disk = grading_policy.read()
            self.assertEqual(on_disk, course_updates.data)

        with filesystem.open('updates.items.json', 'r') as grading_policy:
            on_disk = loads(grading_policy.read())
            self.assertEqual(on_disk, course_updates.items)

    def test_rewrite_nonportable_links_on_import(self):
        content_store = contentstore()

        import_course_from_xml(
            self.store, self.user.id, TEST_DATA_DIR, ['toy'],
            static_content_store=content_store, create_if_not_present=True
        )

        # first check a static asset link
        course_key = self.store.make_course_key('edX', 'toy', '2012_Fall')
        html_block_location = course_key.make_usage_key('html', 'nonportable')
        html_block = self.store.get_item(html_block_location)
        self.assertIn('/static/foo.jpg', html_block.data)

        # then check a intra courseware link
        html_block_location = course_key.make_usage_key('html', 'nonportable_link')
        html_block = self.store.get_item(html_block_location)
        self.assertIn('/jump_to_id/nonportable_link', html_block.data)

    def verify_content_existence(self, store, root_dir, course_id, dirname, category_name, filename_suffix=''):  # lint-amnesty, pylint: disable=missing-function-docstring
        filesystem = OSFS(root_dir / 'test_export')
        self.assertTrue(filesystem.exists(dirname))

        items = store.get_items(course_id, qualifiers={'category': category_name})

        for item in items:
            filesystem = OSFS(root_dir / ('test_export/' + dirname))
            self.assertTrue(filesystem.exists(item.location.block_id + filename_suffix))

    def test_export_course_with_metadata_only_video(self):
        content_store = contentstore()

        import_course_from_xml(self.store, self.user.id, TEST_DATA_DIR, ['toy'], create_if_not_present=True)
        course_id = self.store.make_course_key('edX', 'toy', '2012_Fall')

        # create a new video block and add it as a child to a vertical
        # this re-creates a bug whereby since the video template doesn't have
        # anything in 'data' field, the export was blowing up
        verticals = self.store.get_items(course_id, qualifiers={'category': 'vertical'})

        self.assertGreater(len(verticals), 0)

        parent = verticals[0]

        BlockFactory.create(parent_location=parent.location, category="video", display_name="untitled")

        root_dir = path(mkdtemp_clean())

        print(f'Exporting to tempdir = {root_dir}')

        # export out to a tempdir
        export_course_to_xml(self.store, content_store, course_id, root_dir, 'test_export')

        shutil.rmtree(root_dir)

    def test_export_course_with_metadata_only_word_cloud(self):
        """
        Similar to `test_export_course_with_metadata_only_video`.
        """
        content_store = contentstore()

        import_course_from_xml(self.store, self.user.id, TEST_DATA_DIR, ['word_cloud'], create_if_not_present=True)
        course_id = self.store.make_course_key('HarvardX', 'ER22x', '2013_Spring')

        verticals = self.store.get_items(course_id, qualifiers={'category': 'vertical'})

        self.assertGreater(len(verticals), 0)

        parent = verticals[0]

        BlockFactory.create(parent_location=parent.location, category="word_cloud", display_name="untitled")

        root_dir = path(mkdtemp_clean())

        print(f'Exporting to tempdir = {root_dir}')

        # export out to a tempdir
        export_course_to_xml(self.store, content_store, course_id, root_dir, 'test_export')

        shutil.rmtree(root_dir)

    def test_import_after_renaming_xml_data(self):
        """
        Test that import works fine on split mongo after renaming the blocks url.
        """
        split_store = modulestore()._get_modulestore_by_type(ModuleStoreEnum.Type.split)  # pylint: disable=W0212
        import_course_from_xml(
            split_store, self.user.id, TEST_DATA_DIR,
            ['course_before_rename'],
            create_if_not_present=True
        )
        course_after_rename = import_course_from_xml(
            split_store, self.user.id, TEST_DATA_DIR,
            ['course_after_rename'],
            create_if_not_present=True
        )
        all_items = split_store.get_items(course_after_rename[0].id, qualifiers={'category': 'chapter'})
        renamed_chapter = [item for item in all_items if item.location.block_id == 'renamed_chapter'][0]
        self.assertIsNotNone(renamed_chapter.published_on)
        self.assertIsNotNone(renamed_chapter.parent)
        self.assertIn(renamed_chapter.location, course_after_rename[0].children)
        original_chapter = [item for item in all_items
                            if item.location.block_id == 'b9870b9af59841a49e6e02765d0e3bbf'][0]
        self.assertIsNone(original_chapter.published_on)
        self.assertIsNone(original_chapter.parent)
        self.assertNotIn(original_chapter.location, course_after_rename[0].children)

    def test_empty_data_roundtrip(self):
        """
        Test that an empty `data` field is preserved through
        export/import.
        """
        content_store = contentstore()

        import_course_from_xml(self.store, self.user.id, TEST_DATA_DIR, ['toy'], create_if_not_present=True)
        course_id = self.store.make_course_key('edX', 'toy', '2012_Fall')

        verticals = self.store.get_items(course_id, qualifiers={'category': 'vertical'})

        self.assertGreater(len(verticals), 0)

        parent = verticals[0]

        # Create a block, and ensure that its `data` field is empty
        word_cloud = BlockFactory.create(
            parent_location=parent.location, category="word_cloud", display_name="untitled")
        del word_cloud.data
        self.assertEqual(word_cloud.data, '')

        # Export the course
        root_dir = path(mkdtemp_clean())
        export_course_to_xml(self.store, content_store, course_id, root_dir, 'test_roundtrip')

        # Reimport and get the video back
        import_course_from_xml(self.store, self.user.id, root_dir)
        imported_word_cloud = self.store.get_item(course_id.make_usage_key('word_cloud', 'untitled'))

        # It should now contain empty data
        self.assertEqual(imported_word_cloud.data, '')

    def test_html_export_roundtrip(self):
        """
        Test that a course which has HTML that has style formatting is preserved in export/import
        """
        content_store = contentstore()

        import_course_from_xml(self.store, self.user.id, TEST_DATA_DIR, ['toy'], create_if_not_present=True)

        course_id = self.store.make_course_key('edX', 'toy', '2012_Fall')

        # Export the course
        root_dir = path(mkdtemp_clean())
        export_course_to_xml(self.store, content_store, course_id, root_dir, 'test_roundtrip')

        # Reimport and get the video back
        import_course_from_xml(self.store, self.user.id, root_dir, create_if_not_present=True)

        # get the sample HTML with styling information
        html_block = self.store.get_item(course_id.make_usage_key('html', 'with_styling'))
        self.assertIn('<p style="font:italic bold 72px/30px Georgia, serif; color: red; ">', html_block.data)

        # get the sample HTML with just a simple <img> tag information
        html_block = self.store.get_item(course_id.make_usage_key('html', 'just_img'))
        self.assertIn('<img src="/static/foo_bar.jpg" />', html_block.data)

    def test_export_course_without_content_store(self):
        # Create toy course

        course_items = import_course_from_xml(
            self.store, self.user.id, TEST_DATA_DIR, ['toy'], create_if_not_present=True
        )
        course_id = course_items[0].id

        root_dir = path(mkdtemp_clean())

        print(f'Exporting to tempdir = {root_dir}')
        export_course_to_xml(self.store, None, course_id, root_dir, 'test_export_no_content_store')

        # Delete the course from module store and reimport it

        self.store.delete_course(course_id, self.user.id)

        import_course_from_xml(
            self.store, self.user.id, root_dir, ['test_export_no_content_store'],
            static_content_store=None,
            target_id=course_id,
            create_if_not_present=True
        )

        # Verify reimported course

        items = self.store.get_items(
            course_id,
            qualifiers={
                'name': 'vertical_sequential',
            }
        )
        self.assertEqual(len(items), 1)

    def test_export_course_no_xml_attributes(self):
        """
        Test that a block without an `xml_attributes` attr will still be
        exported successfully
        """
        content_store = contentstore()
        import_course_from_xml(self.store, self.user.id, TEST_DATA_DIR, ['toy'], create_if_not_present=True)
        course_id = self.store.make_course_key('edX', 'toy', '2012_Fall')
        verticals = self.store.get_items(course_id, qualifiers={'category': 'vertical'})
        vertical = verticals[0]

        # create OpenAssessmentBlock:
        open_assessment = BlockFactory.create(
            parent_location=vertical.location,
            category="openassessment",
            display_name="untitled",
        )
        # convert it to draft
        draft_open_assessment = self.store.convert_to_draft(
            open_assessment.location, self.user.id
        )

        # note that it has no `xml_attributes` attribute
        self.assertFalse(hasattr(draft_open_assessment, "xml_attributes"))

        # export should still complete successfully
        root_dir = path(mkdtemp_clean())
        export_course_to_xml(
            self.store,
            content_store,
            course_id,
            root_dir,
            'test_no_xml_attributes'
        )


@ddt.ddt
class MiscCourseTests(ContentStoreTestCase):
    """
    Tests that rely on the toy courses.
    """

    def setUp(self):
        super().setUp()
        # save locs not items b/c the items won't have the subsequently created children in them until refetched
        self.chapter_loc = self.store.create_child(
            self.user.id, self.course.location, 'chapter', 'test_chapter'
        ).location
        self.seq_loc = self.store.create_child(
            self.user.id, self.chapter_loc, 'sequential', 'test_seq'
        ).location
        self.vert_loc = self.store.create_child(self.user.id, self.seq_loc, 'vertical', 'test_vert').location
        # now create some things quasi like the toy course had
        self.problem = self.store.create_child(
            self.user.id, self.vert_loc, 'problem', 'test_problem', fields={
                "data": "<problem>Test</problem>"
            }
        )
        self.store.create_child(
            self.user.id, self.vert_loc, 'video', fields={
                "youtube_id_0_75": "JMD_ifUUfsU",
                "youtube_id_1_0": "OEoXaMPEzfM",
                "youtube_id_1_25": "AKqURZnYqpk",
                "youtube_id_1_5": "DYpADpL7jAY",
                "name": "sample_video",
            }
        )
        self.store.create_child(
            self.user.id, self.vert_loc, 'video', fields={
                "youtube_id_0_75": "JMD_ifUUfsU",
                "youtube_id_1_0": "OEoXaMPEzfM",
                "youtube_id_1_25": "AKqURZnYqpk",
                "youtube_id_1_5": "DYpADpL7jAY",
                "name": "truncated_video",
                "end_time": timedelta(hours=10),
            }
        )
        self.store.create_child(
            self.user.id, self.vert_loc, 'poll_question', fields={
                "name": "T1_changemind_poll_foo_2",
                "display_name": "Change your answer",
                "question": "Have you changed your mind?",
                "answers": [{"id": "yes", "text": "Yes"}, {"id": "no", "text": "No"}],
            }
        )
        self.course = self.store.publish(self.course.location, self.user.id)

    def check_components_on_page(self, component_types, expected_types):
        """
        Ensure that the right types end up on the page.

        component_types is the list of advanced components.

        expected_types is the list of elements that should appear on the page.

        expected_types and component_types should be similar, but not
        exactly the same -- for example, 'video' in
        component_types should cause 'Video' to be present.
        """
        self.course.advanced_modules = component_types
        self.store.update_item(self.course, self.user.id)

        # just pick one vertical
        resp = self.client.get_html(get_url('container_handler', self.vert_loc))
        for expected in expected_types:
            self.assertContains(resp, expected)

    @override_waffle_flag(toggles.LEGACY_STUDIO_UNIT_EDITOR, True)
    @ddt.data("<script>alert(1)</script>", "alert('hi')", "</script><script>alert(1)</script>")
    def test_container_handler_xss_prevent(self, malicious_code):
        """
        Test that XSS attack is prevented
        """
        resp = self.client.get_html(get_url('container_handler', self.vert_loc) + '?action=' + malicious_code)
        # Test that malicious code does not appear in html
        self.assertNotContains(resp, malicious_code)

    @override_waffle_flag(toggles.LEGACY_STUDIO_UNIT_EDITOR, True)
    def test_advanced_components_in_edit_unit(self):
        # This could be made better, but for now let's just assert that we see the advanced modules mentioned in the
        # page response HTML
        self.check_components_on_page(
            ADVANCED_COMPONENT_TYPES,
            ['Word cloud', 'Annotation', 'split_test'],
        )

    @ddt.data('/Fake/asset/displayname', '\\Fake\\asset\\displayname')
    def test_export_on_invalid_displayname(self, invalid_displayname):
        """ Tests that assets with invalid 'displayname' does not cause export to fail """
        content_store = contentstore()
        exported_asset_name = '_Fake_asset_displayname'

        # Create an asset with slash `invalid_displayname` '
        asset_key = self.course.id.make_asset_key('asset', "fake_asset.txt")
        content = StaticContent(
            asset_key, invalid_displayname, 'application/text', b'test',
        )
        content_store.save(content)

        # Verify that the course has only one asset and it has been added with an invalid asset name.
        assets, count = content_store.get_all_content_for_course(self.course.id)
        self.assertEqual(count, 1)
        display_name = assets[0]['displayname']
        self.assertEqual(display_name, invalid_displayname)

        # Now export the course to a tempdir and test that it contains assets. The export should pass
        root_dir = path(mkdtemp_clean())
        print(f'Exporting to tempdir = {root_dir}')
        export_course_to_xml(self.store, content_store, self.course.id, root_dir, 'test_export')

        filesystem = OSFS(root_dir / 'test_export/static')
        exported_static_files = filesystem.listdir('/')

        # Verify that only single asset has been exported with the expected asset name.
        self.assertTrue(filesystem.exists(exported_asset_name))
        self.assertEqual(len(exported_static_files), 1)

        # Remove tempdir
        shutil.rmtree(root_dir)

    @mock.patch(
        'lms.djangoapps.ccx.modulestore.CCXModulestoreWrapper.get_item',
        mock.Mock(return_value=mock.Mock(children=[]))
    )
    def test_export_with_orphan_vertical(self):
        """
        Tests that, export does not fail when a parent xblock does not have draft child xblock
        information but the draft child xblock has parent information.
        """
        # Make an existing unit a draft
        self.problem = self.store.unpublish(self.problem.location, self.user.id)
        root_dir = path(mkdtemp_clean())
        export_course_to_xml(self.store, None, self.course.id, root_dir, 'test_export')

        # Verify that problem is exported in the drafts. This is expected because we are
        # mocking get_item to for drafts. Expect no draft is exported.
        # Specifically get_item is used in `xmodule.modulestore.xml_exporter._export_drafts`
        export_draft_dir = OSFS(root_dir / 'test_export/drafts')
        self.assertEqual(len(export_draft_dir.listdir('/')), 0)

        # Remove tempdir
        shutil.rmtree(root_dir)

    def test_assets_overwrite(self):
        """ Tests that assets will similar 'displayname' will be overwritten during export """
        content_store = contentstore()
        asset_displayname = 'Fake_asset.txt'

        # Create two assets with similar 'displayname'
        for i in range(2):
            asset_path = f'sample_asset_{i}.txt'
            asset_key = self.course.id.make_asset_key('asset', asset_path)
            content = StaticContent(
                asset_key, asset_displayname, 'application/text', b'test',
            )
            content_store.save(content)

        # Fetch & verify course assets to be equal to 2.
        assets, count = content_store.get_all_content_for_course(self.course.id)
        self.assertEqual(count, 2)

        # Verify both assets have similar 'displayname' after saving.
        for asset in assets:
            self.assertEqual(asset['displayname'], asset_displayname)

        # Now export the course to a tempdir and test that it contains assets.
        root_dir = path(mkdtemp_clean())
        print(f'Exporting to tempdir = {root_dir}')
        export_course_to_xml(self.store, content_store, self.course.id, root_dir, 'test_export')

        # Verify that asset have been overwritten during export.
        filesystem = OSFS(root_dir / 'test_export/static')
        exported_static_files = filesystem.listdir('/')
        self.assertTrue(filesystem.exists(asset_displayname))
        self.assertEqual(len(exported_static_files), 1)

        # Remove tempdir
        shutil.rmtree(root_dir)

    @override_waffle_flag(toggles.LEGACY_STUDIO_UNIT_EDITOR, True)
    def test_advanced_components_require_two_clicks(self):
        self.check_components_on_page(['word_cloud'], ['Word cloud'])

    @override_waffle_flag(toggles.LEGACY_STUDIO_UNIT_EDITOR, True)
    def test_edit_unit(self):
        """Verifies rendering the editor in all the verticals in the given test course"""
        self._check_verticals([self.vert_loc])

    def _get_draft_counts(self, item):  # lint-amnesty, pylint: disable=missing-function-docstring
        cnt = 1 if not self.store.has_published_version(item) else 0
        for child in item.get_children():
            cnt = cnt + self._get_draft_counts(child)

        return cnt

    def test_get_items(self):
        """
        This verifies a bug we had where the None setting in get_items() meant 'wildcard'
        Unfortunately, None = published for the revision field, so get_items() would return
        both draft and non-draft copies.
        """
        self.problem = self.store.unpublish(self.problem.location, self.user.id)

        # Query get_items() and find the html item. This should just return back a single item (not 2).
        direct_store_items = self.store.get_items(
            self.course.id, revision=ModuleStoreEnum.RevisionOption.published_only
        )
        items_from_direct_store = [item for item in direct_store_items if item.location == self.problem.location]
        self.assertEqual(len(items_from_direct_store), 0)

        # Fetch from the draft store.
        draft_store_items = self.store.get_items(
            self.course.id, revision=ModuleStoreEnum.RevisionOption.draft_only
        )
        items_from_draft_store = [item for item in draft_store_items if item.location == self.problem.location]
        self.assertEqual(len(items_from_draft_store), 1)

    def test_draft_metadata(self):
        """
        This verifies a bug we had where inherited metadata was getting written to the
        block as 'own-metadata' when publishing. Also verifies the metadata inheritance is
        properly computed
        """
        # refetch course so it has all the children correct
        course = self.store.update_item(self.course, self.user.id)
        course.graceperiod = timedelta(days=1, hours=5, minutes=59, seconds=59)
        course = self.store.update_item(course, self.user.id)
        problem = self.store.get_item(self.problem.location)

        self.assertEqual(problem.graceperiod, course.graceperiod)
        self.assertNotIn('graceperiod', own_metadata(problem))

        self.store.convert_to_draft(problem.location, self.user.id)

        # refetch to check metadata
        problem = self.store.get_item(problem.location)

        self.assertEqual(problem.graceperiod, course.graceperiod)
        self.assertNotIn('graceperiod', own_metadata(problem))

        # publish block
        self.store.publish(problem.location, self.user.id)

        # refetch to check metadata
        problem = self.store.get_item(problem.location)

        self.assertEqual(problem.graceperiod, course.graceperiod)
        self.assertNotIn('graceperiod', own_metadata(problem))

        # put back in draft and change metadata and see if it's now marked as 'own_metadata'
        self.store.convert_to_draft(problem.location, self.user.id)
        problem = self.store.get_item(problem.location)

        new_graceperiod = timedelta(hours=1)

        self.assertNotIn('graceperiod', own_metadata(problem))
        problem.graceperiod = new_graceperiod
        # Save the data that we've just changed to the underlying
        # MongoKeyValueStore before we update the mongo datastore.
        problem.save()
        self.assertIn('graceperiod', own_metadata(problem))
        self.assertEqual(problem.graceperiod, new_graceperiod)

        self.store.update_item(problem, self.user.id)

        # read back to make sure it reads as 'own-metadata'
        problem = self.store.get_item(problem.location)

        self.assertIn('graceperiod', own_metadata(problem))
        self.assertEqual(problem.graceperiod, new_graceperiod)

        # republish
        self.store.publish(problem.location, self.user.id)

        # and re-read and verify 'own-metadata'
        self.store.convert_to_draft(problem.location, self.user.id)
        problem = self.store.get_item(problem.location)

        self.assertIn('graceperiod', own_metadata(problem))
        self.assertEqual(problem.graceperiod, new_graceperiod)

    def test_get_depth_with_drafts(self):
        # make sure no draft items have been returned
        num_drafts = self._get_draft_counts(self.course)
        self.assertEqual(num_drafts, 0)

        # put into draft
        self.problem = self.store.unpublish(self.problem.location, self.user.id)

        # make sure we can query that item and verify that it is a draft
        draft_problem = self.store.get_item(self.problem.location)
        self.assertEqual(self.store.has_published_version(draft_problem), False)

        # now requery with depth
        course = self.store.get_course(self.course.id, depth=None)

        # make sure just one draft item have been returned
        num_drafts = self._get_draft_counts(course)
        self.assertEqual(num_drafts, 1)

    @mock.patch('xmodule.course_block.requests.get')
    def test_import_textbook_as_content_element(self, mock_get):
        mock_get.return_value.text = dedent("""
            <?xml version="1.0"?><table_of_contents>
            <entry page="5" page_label="ii" name="Table of Contents"/>
            </table_of_contents>
        """).strip()
        self.course.textbooks = [Textbook("Textbook", "https://s3.amazonaws.com/edx-textbooks/guttag_computation_v3/")]
        course = self.store.update_item(self.course, self.user.id)
        self.assertGreater(len(course.textbooks), 0)

    def test_import_polls(self):
        items = self.store.get_items(self.course.id, qualifiers={'category': 'poll_question'})
        self.assertGreater(len(items), 0)
        # check that there's actually content in the 'question' field
        self.assertGreater(len(items[0].question), 0)

    def test_module_preview_in_whitelist(self):
        """
        Tests the ajax callback to render an XBlock
        """
        with override_settings(COURSES_WITH_UNSAFE_CODE=[str(self.course.id)]):
            # also try a custom response which will trigger the 'is this course in whitelist' logic
            resp = self.client.get_json(
                get_url('xblock_view_handler', self.vert_loc, kwargs={'view_name': 'container_preview'})
            )
            self.assertEqual(resp.status_code, 200)

            vertical = self.store.get_item(self.vert_loc)
            for child in vertical.children:
                self.assertContains(resp, str(child))

    def test_delete(self):
        # make sure the parent points to the child object which is to be deleted
        # need to refetch chapter b/c at the time it was assigned it had no children
        chapter = self.store.get_item(self.chapter_loc)
        self.assertIn(self.seq_loc, chapter.children)

        self.client.delete(get_url('xblock_handler', self.seq_loc))

        with self.assertRaises(ItemNotFoundError):
            self.store.get_item(self.seq_loc)

        chapter = self.store.get_item(self.chapter_loc)

        # make sure the parent no longer points to the child object which was deleted
        self.assertNotIn(self.seq_loc, chapter.children)

    def test_asset_delete_and_restore(self):
        """
        This test will exercise the soft delete/restore functionality of the assets
        """
        asset_key = self._delete_asset_in_course()

        # now try to find it in store, but they should not be there any longer
        content = contentstore().find(asset_key, throw_on_not_found=False)
        self.assertIsNone(content)

        # now try to find it and the thumbnail in trashcan - should be in there
        content = contentstore('trashcan').find(asset_key, throw_on_not_found=False)
        self.assertIsNotNone(content)

        # let's restore the asset
        restore_asset_from_trashcan(str(asset_key))

        # now try to find it in courseware store, and they should be back after restore
        content = contentstore('trashcan').find(asset_key, throw_on_not_found=False)
        self.assertIsNotNone(content)

    def _delete_asset_in_course(self):
        """
        Helper method for:
          1) importing course from xml
          2) finding asset in course (verifying non-empty)
          3) computing thumbnail location of asset
          4) deleting the asset from the course
        """
        asset_key = self.course.id.make_asset_key('asset', 'sample_static.html')
        content = StaticContent(
            asset_key, "Fake asset", "application/text", b"test",
        )
        contentstore().save(content)

        # go through the website to do the delete, since the soft-delete logic is in the view
        url = reverse_course_url(
            'assets_handler',
            self.course.id,
            kwargs={'asset_key_string': str(asset_key)}
        )
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, 204)

        return asset_key

    def test_empty_trashcan(self):
        """
        This test will exercise the emptying of the asset trashcan
        """
        self._delete_asset_in_course()

        # make sure there's something in the trashcan
        all_assets, __ = contentstore('trashcan').get_all_content_for_course(self.course.id)
        self.assertGreater(len(all_assets), 0)

        # empty the trashcan
        empty_asset_trashcan([self.course.id])

        # make sure trashcan is empty
        all_assets, count = contentstore('trashcan').get_all_content_for_course(self.course.id)
        self.assertEqual(len(all_assets), 0)
        self.assertEqual(count, 0)

    def test_illegal_draft_crud_ops(self):
        chapter = self.store.get_item(self.chapter_loc)
        chapter.data = 'chapter data'
        self.store.update_item(chapter, self.user.id)

        with self.assertRaises(InvalidVersionError):
            self.store.unpublish(self.chapter_loc, self.user.id)

    def test_bad_contentstore_request(self):
        """
        Test that user get proper responses for urls with invalid url or
        asset/course key
        """
        resp = self.client.get_html('asset-v1:CDX+123123+2012_Fall+type@asset+block@&invalid.png')
        self.assertEqual(resp.status_code, 404)

        resp = self.client.get_html('asset-v1:CDX+123123+2012_Fall+type@asset+block@invalid.png')
        self.assertEqual(resp.status_code, 404)

    @override_waffle_switch(waffle.ENABLE_ACCESSIBILITY_POLICY_PAGE, active=False)
    def test_disabled_accessibility_page(self):
        """
        Test that accessibility page returns 404 when waffle switch is disabled
        """
        resp = self.client.get_html('/accessibility')
        self.assertEqual(resp.status_code, 404)

    def test_delete_course(self):
        """
        This test creates a course, makes a draft item, and deletes the course. This will also assert that the
        draft content is also deleted
        """
        # add an asset
        asset_key = self.course.id.make_asset_key('asset', 'sample_static.html')
        content = StaticContent(
            asset_key, "Fake asset", "application/text", b"test",
        )
        contentstore().save(content)
        assets, count = contentstore().get_all_content_for_course(self.course.id)
        self.assertGreater(len(assets), 0)
        self.assertGreater(count, 0)

        self.store.unpublish(self.vert_loc, self.user.id)

        # delete the course
        self.store.delete_course(self.course.id, self.user.id)

        # assert that there's absolutely no non-draft blocks in the course
        # this should also include all draft items
        with self.assertRaises(ItemNotFoundError):
            self.store.get_items(self.course.id)

        # assert that all content in the asset library is keeped
        # in case the course is later restored.
        assets, count = contentstore().get_all_content_for_course(self.course.id)
        self.assertGreater(len(assets), 0)
        self.assertGreater(count, 0)

    def test_course_handouts_rewrites(self):
        """
        Test that the xblock_handler rewrites static handout links
        """
        handouts = self.store.create_item(
            self.user.id, self.course.id, 'course_info', 'handouts', fields={
                "data": "<a href='/static/handouts/sample_handout.txt'>Sample</a>",
            }
        )

        # get block info (json)
        resp = self.client.get(get_url('xblock_handler', handouts.location))

        # make sure we got a successful response
        self.assertEqual(resp.status_code, 200)
        # check that /static/ has been converted to the full path
        # note, we know the link it should be because that's what in the 'toy' course in the test data
        asset_key = self.course.id.make_asset_key('asset', 'handouts_sample_handout.txt')
        asset_url = '/'.join(str(asset_key).rsplit('@', 1))
        self.assertContains(resp, asset_url)

    def test_prefetch_children(self):
        # make sure we haven't done too many round trips to DB:
        # 1) the course,
        # 2 & 3) for the chapters and sequentials
        # Because we're querying from the top of the tree, we cache information needed for inheritance,
        # so we don't need to make an extra query to compute it.
        # set the branch to 'publish' in order to prevent extra lookups of draft versions
        with self.store.branch_setting(ModuleStoreEnum.Branch.published_only, self.course.id):
            with check_mongo_calls(2):
                course = self.store.get_course(self.course.id, depth=2, lazy=False)

            # make sure we pre-fetched a known sequential which should be at depth=2
            self.assertIn(BlockKey.from_usage_key(self.seq_loc), course.runtime.module_data)

            # make sure we don't have a specific vertical which should be at depth=3
            self.assertNotIn(BlockKey.from_usage_key(self.vert_loc), course.runtime.module_data)

        # Now, test with the branch set to draft. No extra round trips b/c it doesn't go deep enough to get
        # beyond direct only categories
        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, self.course.id):
            with check_mongo_calls(2):
                self.store.get_course(self.course.id, depth=2)

    def _check_verticals(self, locations):
        """ Test getting the editing HTML for each vertical. """
        # Assert is here to make sure that the course being tested actually has verticals (units) to check.
        self.assertGreater(len(locations), 0)
        for loc in locations:
            resp = self.client.get_html(get_url('container_handler', loc))
            self.assertEqual(resp.status_code, 200)


@ddt.ddt
class ContentStoreTest(ContentStoreTestCase):
    """
    Tests for the CMS ContentStore application.
    """
    duplicate_course_error = ("There is already a course defined with the same organization and course number. "
                              "Please change either organization or course number to be unique.")

    def setUp(self):
        super().setUp()

        self.course_data = {
            'org': 'MITx',
            'number': '111',
            'display_name': 'Robot Super Course',
            'run': '2013_Spring'
        }

    def assert_created_course(self, number_suffix=None):
        """
        Checks that the course was created properly.
        """
        test_course_data = {}
        test_course_data.update(self.course_data)
        if number_suffix:
            test_course_data['number'] = '{}_{}'.format(test_course_data['number'], number_suffix)
        course_key = _get_course_id(self.store, test_course_data)
        _create_course(self, course_key, test_course_data)
        # Verify that the creator is now registered in the course.
        self.assertTrue(CourseEnrollment.is_enrolled(self.user, course_key))
        return test_course_data

    def assert_create_course_failed(self, error_message):
        """
        Checks that the course not created.
        """
        resp = self.client.ajax_post('/course/', self.course_data)
        self.assertEqual(resp.status_code, 400)
        data = parse_json(resp)
        self.assertEqual(data['error'], error_message)

    def test_create_course(self):
        """Test new course creation - happy path"""
        self.assert_created_course()

    @ddt.data(True, False)
    @mock.patch(
        'cms.djangoapps.contentstore.views.course.default_enable_flexible_peer_openassessments'
    )
    def test_create_course__default_enable_flexible_peer_openassessments(
        self,
        mock_toggle_state,
        mock_default_enable_flexible_peer_openassessments
    ):
        """
        Test that flex peer grading is forced on, when enabled
        """
        # Given a new course run
        test_course_data = {}
        test_course_data.update(self.course_data)
        course_key = _get_course_id(self.store, test_course_data)

        # ... with org configured to / not to enable flex grading
        mock_default_enable_flexible_peer_openassessments.return_value = mock_toggle_state

        # When I create a new course
        new_course_data = _create_course(self, course_key, test_course_data)

        # Then the process completes successfully
        new_course_key = CourseKey.from_string(new_course_data['course_key'])
        new_course = self.store.get_course(new_course_key)

        # ... and our setting got toggled appropriately on the course
        self.assertEqual(new_course.force_on_flexible_peer_openassessments, mock_toggle_state)

    @override_settings(DEFAULT_COURSE_LANGUAGE='hr')
    def test_create_course_default_language(self):
        """Test new course creation and verify default language"""
        test_course_data = self.assert_created_course()
        course_id = _get_course_id(self.store, test_course_data)
        course_block = self.store.get_course(course_id)
        self.assertEqual(course_block.language, 'hr')

    def test_create_course_with_dots(self):
        """Test new course creation with dots in the name"""
        self.course_data['org'] = 'org.foo.bar'
        self.course_data['number'] = 'course.number'
        self.course_data['run'] = 'run.name'
        self.assert_created_course()

    def test_course_with_different_cases(self):
        """
        Tests that course can not be created with different case using an AJAX request to
        course handler.
        """
        course_number = '99x'
        # Verify create a course passes with lower case.
        self.course_data['number'] = course_number.lower()
        self.assert_created_course()

        # Verify create a course fail when same course number is provided with different case.
        self.course_data['number'] = course_number.upper()
        self.assert_course_creation_failed(self.duplicate_course_error)

    def test_create_course_check_forum_seeding(self):
        """Test new course creation and verify forum seeding """
        test_course_data = self.assert_created_course(number_suffix=uuid4().hex)
        self.assertTrue(are_permissions_roles_seeded(_get_course_id(self.store, test_course_data)))

    def test_forum_unseeding_on_delete(self):
        """Test new course creation and verify forum unseeding """
        test_course_data = self.assert_created_course(number_suffix=uuid4().hex)
        course_id = _get_course_id(self.store, test_course_data)
        self.assertTrue(are_permissions_roles_seeded(course_id))
        delete_course(course_id, self.user.id)
        # should raise an exception for checking permissions on deleted course
        with self.assertRaises(ItemNotFoundError):
            are_permissions_roles_seeded(course_id)

    def test_forum_unseeding_with_multiple_courses(self):
        """Test new course creation and verify forum unseeding when there are multiple courses"""
        test_course_data = self.assert_created_course(number_suffix=uuid4().hex)
        second_course_data = self.assert_created_course(number_suffix=uuid4().hex)

        # unseed the forums for the first course
        course_id = _get_course_id(self.store, test_course_data)
        delete_course(course_id, self.user.id)
        # should raise an exception for checking permissions on deleted course
        with self.assertRaises(ItemNotFoundError):
            are_permissions_roles_seeded(course_id)

        second_course_id = _get_course_id(self.store, second_course_data)
        # permissions should still be there for the other course
        self.assertTrue(are_permissions_roles_seeded(second_course_id))

    def test_course_enrollments_and_roles_on_delete(self):
        """
        Test that course deletion doesn't remove course enrollments or user's roles
        """
        test_course_data = self.assert_created_course(number_suffix=uuid4().hex)
        course_id = _get_course_id(self.store, test_course_data)

        # test that a user gets his enrollment and its 'student' role as default on creating a course
        self.assertTrue(CourseEnrollment.is_enrolled(self.user, course_id))
        self.assertTrue(self.user.roles.filter(name="Student", course_id=course_id))

        delete_course(course_id, self.user.id)
        # check that user's enrollment for this course is not deleted
        self.assertTrue(CourseEnrollment.is_enrolled(self.user, course_id))
        # check that user has form role "Student" for this course even after deleting it
        self.assertTrue(self.user.roles.filter(name="Student", course_id=course_id))

    def test_course_access_groups_on_delete(self):
        """
        Test that course deletion removes users from 'instructor' and 'staff' groups of this course
        of all format e.g, 'instructor_edX/Course/Run', 'instructor_edX.Course.Run', 'instructor_Course'
        """
        test_course_data = self.assert_created_course(number_suffix=uuid4().hex)
        course_id = _get_course_id(self.store, test_course_data)

        # Add user in possible groups and check that user in instructor groups of this course
        instructor_role = CourseInstructorRole(course_id)

        auth.add_users(self.user, instructor_role, self.user)

        self.assertGreater(len(instructor_role.users_with_role()), 0)

        # Now delete course and check that user not in instructor groups of this course
        delete_course(course_id, self.user.id)

        # Update our cached user since its roles have changed
        self.user = User.objects.get_by_natural_key(self.user.natural_key()[0])

        self.assertFalse(instructor_role.has_user(self.user))
        self.assertEqual(len(instructor_role.users_with_role()), 0)

    def test_delete_course_with_keep_instructors(self):
        """
        Tests that when you delete a course with 'keep_instructors',
        it does not remove any permissions of users/groups from the course
        """
        test_course_data = self.assert_created_course(number_suffix=uuid4().hex)
        course_id = _get_course_id(self.store, test_course_data)

        # Add and verify instructor role for the course
        instructor_role = CourseInstructorRole(course_id)
        instructor_role.add_users(self.user)
        self.assertTrue(instructor_role.has_user(self.user))

        delete_course(course_id, self.user.id, keep_instructors=True)

        # Update our cached user so if any change in roles can be captured
        self.user = User.objects.get_by_natural_key(self.user.natural_key()[0])

        self.assertTrue(instructor_role.has_user(self.user))

    def test_create_course_after_delete(self):
        """
        Test that course creation works after deleting a course with the same URL
        """
        test_course_data = self.assert_created_course()
        course_id = _get_course_id(self.store, test_course_data)

        delete_course(course_id, self.user.id)

        self.assert_created_course()

    def test_create_course_duplicate_course(self):
        """Test new course creation - error path"""
        self.client.ajax_post('/course/', self.course_data)
        self.assert_course_creation_failed(self.duplicate_course_error)

    def assert_course_creation_failed(self, error_message):
        """
        Checks that the course did not get created
        """
        test_enrollment = False
        try:
            course_id = _get_course_id(self.store, self.course_data)
            initially_enrolled = CourseEnrollment.is_enrolled(self.user, course_id)
            test_enrollment = True
        except InvalidKeyError:
            # b/c the intent of the test with bad chars isn't to test auth but to test the handler, ignore
            pass
        resp = self.client.ajax_post('/course/', self.course_data)
        self.assertEqual(resp.status_code, 200)
        data = parse_json(resp)
        assert 'ErrMsg' in data, "Expected the course creation to fail"
        self.assertRegex(data['ErrMsg'], error_message)
        if test_enrollment:
            # One test case involves trying to create the same course twice. Hence for that course,
            # the user will be enrolled. In the other cases, initially_enrolled will be False.
            self.assertEqual(initially_enrolled, CourseEnrollment.is_enrolled(self.user, course_id))

    def test_create_course_duplicate_number(self):
        """Test new course creation - error path"""
        self.client.ajax_post('/course/', self.course_data)
        self.course_data['display_name'] = 'Robot Super Course Two'
        self.course_data['run'] = '2013_Summer'

        self.assert_created_course()

    def test_create_course_case_change(self):
        """Test new course creation - error path due to case insensitive name equality"""
        self.course_data['number'] = '99x'

        # Verify that the course was created properly.
        self.assert_created_course()

        # Keep the copy of original org
        cache_current = self.course_data['org']

        # Change `org` to lower case and verify that course did not get created
        self.course_data['org'] = self.course_data['org'].lower()
        self.assert_course_creation_failed(self.duplicate_course_error)

        # Replace the org with its actual value, and keep the copy of course number.
        self.course_data['org'] = cache_current
        cache_current = self.course_data['number']

        self.course_data['number'] = self.course_data['number'].upper()
        self.assert_course_creation_failed(self.duplicate_course_error)

        # Replace the org with its actual value, and keep the copy of course number.
        self.course_data['number'] = cache_current
        __ = self.course_data['run']

        self.course_data['run'] = self.course_data['run'].upper()
        self.assert_course_creation_failed(self.duplicate_course_error)

    def test_course_substring(self):
        """
        Test that a new course can be created whose name is a substring of an existing course
        """
        self.client.ajax_post('/course/', self.course_data)
        cache_current = self.course_data['number']
        self.course_data['number'] = '{}a'.format(self.course_data['number'])
        resp = self.client.ajax_post('/course/', self.course_data)
        self.assertEqual(resp.status_code, 200)
        self.course_data['number'] = cache_current
        self.course_data['org'] = 'a{}'.format(self.course_data['org'])
        resp = self.client.ajax_post('/course/', self.course_data)
        self.assertEqual(resp.status_code, 200)

    def test_create_course_with_bad_organization(self):
        """Test new course creation - error path for bad organization name"""
        self.course_data['org'] = 'University of California, Berkeley'
        self.assert_course_creation_failed(r"(?s)Unable to create course 'Robot Super Course'.*")

    def test_create_course_with_course_creation_disabled_staff(self):
        """Test new course creation -- course creation disabled, but staff access."""
        with mock.patch.dict('django.conf.settings.FEATURES', {'DISABLE_COURSE_CREATION': True}):
            self.assert_created_course()

    def test_create_course_with_course_creation_disabled_not_staff(self):
        """Test new course creation -- error path for course creation disabled, not staff access."""
        with mock.patch.dict('django.conf.settings.FEATURES', {'DISABLE_COURSE_CREATION': True}):
            self.user.is_staff = False
            self.user.save()
            self.assert_course_permission_denied()

    def test_create_course_no_course_creators_staff(self):
        """Test new course creation -- course creation group enabled, staff, group is empty."""
        with mock.patch.dict('django.conf.settings.FEATURES', {'ENABLE_CREATOR_GROUP': True}):
            self.assert_created_course()

    def test_create_course_no_course_creators_not_staff(self):
        """Test new course creation -- error path for course creator group enabled, not staff, group is empty."""
        with mock.patch.dict('django.conf.settings.FEATURES', {"ENABLE_CREATOR_GROUP": True}):
            self.user.is_staff = False
            self.user.save()
            self.assert_course_permission_denied()

    def test_create_course_with_course_creator(self):
        """Test new course creation -- use course creator group"""
        with mock.patch.dict('django.conf.settings.FEATURES', {"ENABLE_CREATOR_GROUP": True}):
            auth.add_users(self.user, CourseCreatorRole(), self.user)
            self.assert_created_course()

    def test_create_course_with_unicode_in_id_disabled(self):
        """
        Test new course creation with feature setting: ALLOW_UNICODE_COURSE_ID disabled.
        """
        with mock.patch.dict('django.conf.settings.FEATURES', {'ALLOW_UNICODE_COURSE_ID': False}):
            error_message = "Special characters not allowed in organization, course number, and course run."
            self.course_data['org'] = '��������������'
            self.assert_create_course_failed(error_message)

            self.course_data['number'] = '��chantillon'
            self.assert_create_course_failed(error_message)

            self.course_data['run'] = '����������'
            self.assert_create_course_failed(error_message)

    def assert_course_permission_denied(self):
        """
        Checks that the course did not get created due to a PermissionError.
        """
        resp = self.client.ajax_post('/course/', self.course_data)
        self.assertEqual(resp.status_code, 403)

    def test_course_factory(self):
        """Test that the course factory works correctly."""
        course = CourseFactory.create()
        self.assertIsInstance(course, CourseBlock)

    def test_item_factory(self):
        """Test that the item factory works correctly."""
        course = CourseFactory.create()
        item = BlockFactory.create(parent_location=course.location)
        self.assertIsInstance(item, SequenceBlock)

    def test_create_block(self):
        """Test creating a new xblock instance."""
        course = CourseFactory.create()

        section_data = {
            'parent_locator': str(course.location),
            'category': 'chapter',
            'display_name': 'Section One',
        }

        resp = self.client.ajax_post(reverse_url('xblock_handler'), section_data)

        self.assertEqual(resp.status_code, 200)
        data = parse_json(resp)
        retarget = re.escape(
            str(course.id.make_usage_key('chapter', 'REPLACE'))
        ).replace('REPLACE', r'([0-9]|[a-f]){3,}')
        self.assertRegex(data['locator'], retarget)

    @ddt.data(True, False)
    def test_hide_xblock_from_toc_via_handler(self, hide_from_toc):
        """Test that the hide_from_toc field can be set via the xblock_handler."""
        course = CourseFactory.create()
        sequential = BlockFactory.create(parent_location=course.location)
        data = {
            "metadata": {
                "hide_from_toc": hide_from_toc
            }
        }

        response = self.client.ajax_post(get_url("xblock_handler", sequential.location), data)
        sequential = self.store.get_item(sequential.location)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(hide_from_toc, sequential.hide_from_toc)

    def test_capa_block(self):
        """Test that a problem treats markdown specially."""
        course = CourseFactory.create()

        problem_data = {
            'parent_locator': str(course.location),
            'category': 'problem'
        }

        resp = self.client.ajax_post(reverse_url('xblock_handler'), problem_data)
        self.assertEqual(resp.status_code, 200)
        payload = parse_json(resp)
        problem_loc = UsageKey.from_string(payload['locator'])
        problem = self.store.get_item(problem_loc)
        self.assertIsInstance(problem, ProblemBlock, "New problem is not a ProblemBlock")
        self.assertNotIn('markdown', problem.editable_metadata_fields, "Markdown slipped into the editable metadata fields")  # lint-amnesty, pylint: disable=line-too-long

    def test_cms_imported_course_walkthrough(self):
        """
        Import and walk through some common URL endpoints. This just verifies non-500 and no other
        correct behavior, so it is not a deep test
        """
        def test_get_html(handler):
            # Helper function for getting HTML for a page in Studio and
            # checking that it does not error.
            resp = self.client.get_html(
                get_url(handler, course_key, 'course_key_string')
            )
            self.assertEqual(resp.status_code, 200)

        def test_get_json(handler):
            # Helper function for getting HTML for a page in Studio and
            # checking that it does not error.
            resp = self.client.get(
                get_url(handler, course_key, 'course_key_string'),
                HTTP_ACCEPT="application/json",
            )
            self.assertEqual(resp.status_code, 200)

        course_items = import_course_from_xml(
            self.store, self.user.id, TEST_DATA_DIR, ['simple'], create_if_not_present=True
        )
        course_key = course_items[0].id

        resp = self._show_course_overview(course_key)

        # course_handler raise 404 for old mongo course
        if course_key.deprecated:
            self.assertEqual(resp.status_code, 404)
            return

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Chapter 2')

        # go to various pages
        with override_waffle_flag(toggles.LEGACY_STUDIO_IMPORT, True):
            test_get_html('import_handler')
        with override_waffle_flag(toggles.LEGACY_STUDIO_EXPORT, True):
            test_get_html('export_handler')
        with override_waffle_flag(toggles.LEGACY_STUDIO_COURSE_TEAM, True):
            test_get_html('course_team_handler')
        with override_waffle_flag(toggles.LEGACY_STUDIO_SCHEDULE_DETAILS, True):
            test_get_html('settings_handler')
        with override_waffle_flag(toggles.LEGACY_STUDIO_GRADING, True):
            test_get_html('grading_handler')
        with override_waffle_flag(toggles.LEGACY_STUDIO_ADVANCED_SETTINGS, True):
            test_get_html('advanced_settings_handler')
        test_get_json('textbooks_list_handler')

        # Test that studio updates load
        course_updates_url = reverse(
            'course_info_update_handler',
            kwargs={
                'course_key_string': str(course_key),
            }
        )
        resp = self.client.get(course_updates_url)
        assert resp.status_code == 200

        resp = self.client.get(
            get_url('cms.djangoapps.contentstore:v0:course_tab_list', course_key, 'course_id')
        )
        self.assertEqual(resp.status_code, 200)

        # go look at the Edit page
        unit_key = course_key.make_usage_key('vertical', 'test_vertical')
        with override_waffle_flag(toggles.LEGACY_STUDIO_UNIT_EDITOR, True):
            resp = self.client.get_html(get_url('container_handler', unit_key))
        self.assertEqual(resp.status_code, 200)

        def delete_item(category, name):
            """ Helper method for testing the deletion of an xblock item. """
            item_key = course_key.make_usage_key(category, name)
            resp = self.client.delete(get_url('xblock_handler', item_key))
            self.assertEqual(resp.status_code, 204)

        # delete a component
        delete_item(category='html', name='test_html')

        # delete a unit
        delete_item(category='vertical', name='test_vertical')

        # delete a unit
        delete_item(category='sequential', name='test_sequence')

        # delete a chapter
        delete_item(category='chapter', name='chapter_2')

    def test_import_into_new_course_id(self):
        target_id = _get_course_id(self.store, self.course_data)
        _create_course(self, target_id, self.course_data)

        import_course_from_xml(self.store, self.user.id, TEST_DATA_DIR, ['toy'], target_id=target_id)

        blocks = self.store.get_items(target_id)

        # we should have a number of blocks in there
        # we can't specify an exact number since it'll always be changing
        self.assertGreater(len(blocks), 10)

        #
        # test various re-namespacing elements
        #

        # first check PDF textbooks, to make sure the url paths got updated
        course_block = self.store.get_course(target_id)

        self.assertEqual(len(course_block.pdf_textbooks), 1)
        self.assertEqual(len(course_block.pdf_textbooks[0]["chapters"]), 2)
        self.assertEqual(course_block.pdf_textbooks[0]["chapters"][0]["url"], '/static/Chapter1.pdf')
        self.assertEqual(course_block.pdf_textbooks[0]["chapters"][1]["url"], '/static/Chapter2.pdf')

    def test_import_into_new_course_id_wiki_slug_renamespacing(self):
        # If reimporting into the same course change the wiki_slug.
        target_id = self.store.make_course_key('edX', 'toy', '2012_Fall')
        course_data = {
            'org': target_id.org,
            'number': target_id.course,
            'display_name': 'Robot Super Course',
            'run': target_id.run
        }
        _create_course(self, target_id, course_data)
        course_block = self.store.get_course(target_id)
        course_block.wiki_slug = 'toy'
        course_block.save()

        # Import a course with wiki_slug == location.course
        import_course_from_xml(self.store, self.user.id, TEST_DATA_DIR, ['toy'], target_id=target_id)
        course_block = self.store.get_course(target_id)
        self.assertEqual(course_block.wiki_slug, 'edX.toy.2012_Fall')

        # But change the wiki_slug if it is a different course.
        target_id = self.store.make_course_key('MITx', '111', '2013_Spring')
        course_data = {
            'org': target_id.org,
            'number': target_id.course,
            'display_name': 'Robot Super Course',
            'run': target_id.run
        }
        _create_course(self, target_id, course_data)

        # Import a course with wiki_slug == location.course
        import_course_from_xml(self.store, self.user.id, TEST_DATA_DIR, ['toy'], target_id=target_id)
        course_block = self.store.get_course(target_id)
        self.assertEqual(course_block.wiki_slug, 'MITx.111.2013_Spring')

        # Now try importing a course with wiki_slug == '{0}.{1}.{2}'.format(location.org, location.course, location.run)
        import_course_from_xml(self.store, self.user.id, TEST_DATA_DIR, ['two_toys'], target_id=target_id)
        course_block = self.store.get_course(target_id)
        self.assertEqual(course_block.wiki_slug, 'MITx.111.2013_Spring')

    def test_import_metadata_with_attempts_empty_string(self):
        import_course_from_xml(self.store, self.user.id, TEST_DATA_DIR, ['simple'], create_if_not_present=True)
        did_load_item = False
        try:
            course_key = self.store.make_course_key('edX', 'simple', '2012_Fall')
            usage_key = course_key.make_usage_key('problem', 'ps01-simple')
            self.store.get_item(usage_key)
            did_load_item = True
        except ItemNotFoundError:
            pass

        # make sure we found the item (e.g. it didn't error while loading)
        self.assertTrue(did_load_item)

    def test_forum_id_generation(self):
        """
        Test that a discussion item, even if it doesn't set its discussion_id,
        consistently generates the same one
        """
        course = CourseFactory.create()

        # create a discussion item
        discussion_item = self.store.create_item(self.user.id, course.id, 'discussion', 'new_component')

        # now fetch it from the modulestore to instantiate its descriptor
        fetched = self.store.get_item(discussion_item.location)

        # refetch it to be safe
        refetched = self.store.get_item(discussion_item.location)

        # and make sure the same discussion items have the same discussion ids
        self.assertEqual(fetched.discussion_id, discussion_item.discussion_id)
        self.assertEqual(fetched.discussion_id, refetched.discussion_id)

        # and make sure that the id isn't the old "$$GUID$$"
        self.assertNotEqual(discussion_item.discussion_id, '$$GUID$$')

    def test_metadata_inheritance(self):
        course_items = import_course_from_xml(
            self.store, self.user.id, TEST_DATA_DIR, ['toy'], create_if_not_present=True
        )

        course = course_items[0]
        verticals = self.store.get_items(course.id, qualifiers={'category': 'vertical'})

        # let's assert on the metadata_inheritance on an existing vertical
        for vertical in verticals:
            self.assertEqual(course.xqa_key, vertical.xqa_key)
            self.assertEqual(course.start, vertical.start)

        self.assertGreater(len(verticals), 0)

        # crate a new block and add it as a child to a vertical
        parent = verticals[0]
        new_block = self.store.create_child(
            self.user.id, parent.location, 'html', 'new_component'
        )

        # flush the cache
        new_block = self.store.get_item(new_block.location)

        # check for grace period definition which should be defined at the course level
        self.assertEqual(parent.graceperiod, new_block.graceperiod)
        self.assertEqual(parent.start, new_block.start)
        self.assertEqual(course.start, new_block.start)

        self.assertEqual(course.xqa_key, new_block.xqa_key)

        #
        # now let's define an override at the leaf node level
        #
        new_block.graceperiod = timedelta(1)
        self.store.update_item(new_block, self.user.id)

        # flush the cache and refetch
        new_block = self.store.get_item(new_block.location)

        self.assertEqual(timedelta(1), new_block.graceperiod)

    def test_default_metadata_inheritance(self):
        course = CourseFactory.create()
        vertical = BlockFactory.create(parent_location=course.location)
        course.children.append(vertical)
        # in memory
        self.assertIsNotNone(course.start)
        self.assertEqual(course.start, vertical.start)
        self.assertEqual(course.textbooks, [])
        self.assertIn('GRADER', course.grading_policy)
        self.assertIn('GRADE_CUTOFFS', course.grading_policy)

        # by fetching
        fetched_course = self.store.get_item(course.location)
        fetched_item = self.store.get_item(vertical.location)
        self.assertIsNotNone(fetched_course.start)
        self.assertEqual(course.start, fetched_course.start)
        self.assertEqual(fetched_course.start, fetched_item.start)
        self.assertEqual(course.textbooks, fetched_course.textbooks)

    def test_image_import(self):
        """Test backwards compatibilty of course image."""
        content_store = contentstore()

        # Use conditional_and_poll, as it's got an image already
        courses = import_course_from_xml(
            self.store,
            self.user.id,
            TEST_DATA_DIR,
            ['conditional_and_poll'],
            static_content_store=content_store,
            create_if_not_present=True
        )

        course = courses[0]

        # Make sure the course image is set to the right place
        self.assertEqual(course.course_image, 'images_course_image.jpg')

        # Ensure that the imported course image is present -- this shouldn't raise an exception
        asset_key = course.id.make_asset_key('asset', course.course_image)
        content_store.find(asset_key)

    def _show_course_overview(self, course_key):
        """
        Show the course overview page.
        """
        resp = self.client.get(get_url('course_handler', course_key, 'course_key_string'),
                               content_type='application/json')
        return resp

    def test_wiki_slug(self):
        """When creating a course a unique wiki_slug should be set."""

        course_key = _get_course_id(self.store, self.course_data)
        _create_course(self, course_key, self.course_data)
        course_block = self.store.get_course(course_key)
        self.assertEqual(course_block.wiki_slug, 'MITx.111.2013_Spring')

    def test_course_handler_with_invalid_course_key_string(self):
        """Test viewing the course overview page with invalid course id"""

        response = self.client.get_html('/course/edX/test')
        self.assertEqual(response.status_code, 404)


class MetadataSaveTestCase(ContentStoreTestCase):
    """Test that metadata is correctly cached and decached."""

    def setUp(self):
        super().setUp()

        course = CourseFactory.create()

        video_sample_xml = """
        <video display_name="Test Video"
                youtube="1.0:p2Q6BrNhdh8,0.75:izygArpw-Qo,1.25:1EeWXzPdhSA,1.5:rABDYkeK0x8"
                show_captions="false"
                from="1.0"
                to="60.0">
            <source src="http://www.example.com/file.mp4"/>
            <track src="http://www.example.com/track"/>
        </video>
        """
        video_data = VideoBlock.parse_video_xml(video_sample_xml)
        video_data.pop('source')
        self.video_block = BlockFactory.create(
            parent_location=course.location, category='video',
            **video_data
        )

    def test_metadata_not_persistence(self):
        """
        Test that blocks which set metadata fields in their
        constructor are correctly deleted.
        """
        self.assertIn('html5_sources', own_metadata(self.video_block))
        attrs_to_strip = {
            'show_captions',
            'youtube_id_1_0',
            'youtube_id_0_75',
            'youtube_id_1_25',
            'youtube_id_1_5',
            'start_time',
            'end_time',
            'html5_sources',
            'track'
        }

        location = self.video_block.location

        for field_name in attrs_to_strip:
            delattr(self.video_block, field_name)

        self.assertNotIn('html5_sources', own_metadata(self.video_block))
        self.store.update_item(self.video_block, self.user.id)
        block = self.store.get_item(location)

        self.assertNotIn('html5_sources', own_metadata(block))

    def test_metadata_persistence(self):
        # TODO: create the same test as `test_metadata_not_persistence`,
        # but check persistence for some other block.
        pass


class RerunCourseTest(ContentStoreTestCase):
    """
    Tests for Rerunning a course via the view handler
    """

    def setUp(self):
        super().setUp()
        self.destination_course_data = {
            'org': 'MITx',
            'number': '111',
            'display_name': 'Robot Super Course',
            'run': '2013_Spring'
        }

    def post_rerun_request(
            self, source_course_key, destination_course_data=None, response_code=200, expect_error=False
    ):
        """Create and send an ajax post for the rerun request"""

        # create data to post
        rerun_course_data = {'source_course_key': str(source_course_key)}
        if not destination_course_data:
            destination_course_data = self.destination_course_data
        rerun_course_data.update(destination_course_data)
        destination_course_key = _get_course_id(self.store, destination_course_data)

        # post the request
        course_url = get_url('course_handler', destination_course_key, 'course_key_string')
        response = self.client.ajax_post(course_url, rerun_course_data)

        # verify response
        self.assertEqual(response.status_code, response_code)
        if not expect_error:
            json_resp = parse_json(response)
            self.assertNotIn('ErrMsg', json_resp)
            destination_course_key = CourseKey.from_string(json_resp['destination_course_key'])
        return destination_course_key

    def get_unsucceeded_course_action_elements(self, html, course_key):
        """Returns the elements in the unsucceeded course action section that have the given course_key"""
        return html.cssselect(f'.courses-processing li[data-course-key="{str(course_key)}"]')

    def assertInCourseListing(self, course_key):
        """
        Asserts that the given course key is NOT in the unsucceeded course action section of the html.
        """
        response = self.client.get(reverse('cms.djangoapps.contentstore:v2:courses'))
        assert str(course_key) not in [
            course["course_key"]
            for course in response.json()["results"]["in_process_course_actions"]
        ]

    def assertInUnsucceededCourseActions(self, course_key):
        """
        Asserts that the given course key is in the unsucceeded course action section of the html.
        """
        response = self.client.get(reverse('cms.djangoapps.contentstore:v2:courses'))
        assert str(course_key) in [
            course["course_key"]
            for course in response.json()["results"]["in_process_course_actions"]
        ]

    def verify_rerun_course(self, source_course_key, destination_course_key, destination_display_name):
        """
        Verify the contents of the course rerun action
        """

        rerun_state = CourseRerunState.objects.find_first(course_key=destination_course_key)
        expected_states = {
            'state': CourseRerunUIStateManager.State.SUCCEEDED,
            'display_name': destination_display_name,
            'source_course_key': source_course_key,
            'course_key': destination_course_key,
            'should_display': True,
        }
        for field_name, expected_value in expected_states.items():
            self.assertEqual(getattr(rerun_state, field_name), expected_value)

        # Verify that the creator is now enrolled in the course.
        self.assertTrue(CourseEnrollment.is_enrolled(self.user, destination_course_key))

        # Verify both courses are in the course listing section
        self.assertInCourseListing(source_course_key)
        self.assertInCourseListing(destination_course_key)

    def test_rerun_course_no_videos_in_val(self):
        """
        Test when rerunning a course with no videos, VAL copies nothing
        """
        source_course = CourseFactory.create(default_store=ModuleStoreEnum.Type.split)
        destination_course_key = self.post_rerun_request(source_course.id)
        self.verify_rerun_course(source_course.id, destination_course_key, self.destination_course_data['display_name'])
        videos, __ = get_videos_for_course(str(destination_course_key))
        videos = list(videos)
        self.assertEqual(0, len(videos))
        self.assertInCourseListing(destination_course_key)

    def test_rerun_course_video_upload_token(self):
        """
        Test when rerunning a course with video upload token, video upload token is not copied to new course.
        """
        # Create a course with video upload token.
        source_course = CourseFactory.create(
            video_upload_pipeline={"course_video_upload_token": 'test-token'},
            default_store=ModuleStoreEnum.Type.split
        )

        destination_course_key = self.post_rerun_request(source_course.id)
        self.verify_rerun_course(source_course.id, destination_course_key, self.destination_course_data['display_name'])
        self.assertInCourseListing(destination_course_key)

        # Verify video upload pipeline is empty.
        source_course = self.store.get_course(source_course.id)
        new_course = self.store.get_course(destination_course_key)
        self.assertDictEqual(source_course.video_upload_pipeline, {"course_video_upload_token": 'test-token'})
        self.assertEqual(new_course.video_upload_pipeline, {})

    def test_rerun_course_success(self):
        source_course = CourseFactory.create(default_store=ModuleStoreEnum.Type.split)
        create_video(
            dict(
                edx_video_id="tree-hugger",
                courses=[str(source_course.id)],
                status='test',
                duration=2,
                encoded_videos=[]
            )
        )
        destination_course_key = self.post_rerun_request(source_course.id)
        self.verify_rerun_course(source_course.id, destination_course_key, self.destination_course_data['display_name'])

        # Verify that the VAL copies videos to the rerun
        videos, __ = get_videos_for_course(str(source_course.id))
        source_videos = list(videos)
        videos, __ = get_videos_for_course(str(destination_course_key))
        target_videos = list(videos)
        self.assertEqual(1, len(source_videos))
        self.assertEqual(source_videos, target_videos)

        # Verify that video upload token is empty for rerun.
        new_course = self.store.get_course(destination_course_key)
        self.assertEqual(new_course.video_upload_pipeline, {})

    def test_rerun_course_resets_advertised_date(self):
        source_course = CourseFactory.create(
            advertised_start="01-12-2015",
            default_store=ModuleStoreEnum.Type.split
        )
        destination_course_key = self.post_rerun_request(source_course.id)
        destination_course = self.store.get_course(destination_course_key)

        self.assertEqual(None, destination_course.advertised_start)

    def test_rerun_of_rerun(self):
        source_course = CourseFactory.create(default_store=ModuleStoreEnum.Type.split)
        rerun_course_key = self.post_rerun_request(source_course.id)
        rerun_of_rerun_data = {
            'org': rerun_course_key.org,
            'number': rerun_course_key.course,
            'display_name': 'rerun of rerun',
            'run': 'rerun2'
        }
        rerun_of_rerun_course_key = self.post_rerun_request(rerun_course_key, rerun_of_rerun_data)
        self.verify_rerun_course(rerun_course_key, rerun_of_rerun_course_key, rerun_of_rerun_data['display_name'])

    def test_rerun_course_fail_no_source_course(self):
        existent_course_key = CourseFactory.create(default_store=ModuleStoreEnum.Type.split).id
        non_existent_course_key = CourseLocator("org", "non_existent_course", "non_existent_run")
        destination_course_key = self.post_rerun_request(non_existent_course_key)

        # Verify that the course rerun action is marked failed
        rerun_state = CourseRerunState.objects.find_first(course_key=destination_course_key)
        self.assertEqual(rerun_state.state, CourseRerunUIStateManager.State.FAILED)
        self.assertIn("Cannot find a course at", rerun_state.message)

        # Verify that the creator is not enrolled in the course.
        self.assertFalse(CourseEnrollment.is_enrolled(self.user, non_existent_course_key))

        # Verify that the existing course continues to be in the course listings
        self.assertInCourseListing(existent_course_key)

        # Verify that the failed course is NOT in the course listings
        self.assertInUnsucceededCourseActions(destination_course_key)

    def test_rerun_course_fail_duplicate_course(self):
        existent_course_key = CourseFactory.create().id
        destination_course_data = {
            'org': existent_course_key.org,
            'number': existent_course_key.course,
            'display_name': 'existing course',
            'run': existent_course_key.run
        }
        destination_course_key = self.post_rerun_request(
            existent_course_key, destination_course_data, expect_error=True
        )

        # Verify that the course rerun action doesn't exist
        with self.assertRaises(CourseActionStateItemNotFoundError):
            CourseRerunState.objects.find_first(course_key=destination_course_key)

        # Verify that the existing course continues to be in the course listing
        self.assertInCourseListing(existent_course_key)

    def test_rerun_with_permission_denied(self):
        with mock.patch.dict('django.conf.settings.FEATURES', {"ENABLE_CREATOR_GROUP": True}):
            source_course = CourseFactory.create(default_store=ModuleStoreEnum.Type.split)
            auth.add_users(self.user, CourseCreatorRole(), self.user)
            self.user.is_staff = False
            self.user.save()
            self.post_rerun_request(source_course.id, response_code=403, expect_error=True)

    def test_rerun_error(self):
        error_message = "Mock Error Message"
        with mock.patch(
                'xmodule.modulestore.mixed.MixedModuleStore.clone_course',
                mock.Mock(side_effect=Exception(error_message))
        ):
            source_course = CourseFactory.create()
            destination_course_key = self.post_rerun_request(source_course.id)
            rerun_state = CourseRerunState.objects.find_first(course_key=destination_course_key)
            self.assertEqual(rerun_state.state, CourseRerunUIStateManager.State.FAILED)
            self.assertIn(error_message, rerun_state.message)

    def test_rerun_error_trunc_message(self):
        """
        CourseActionUIState.message is sometimes populated with the contents
        of Python tracebacks. This test ensures we don't crash when attempting
        to insert a value exceeding its max_length (note that sqlite does not
        complain if this happens, but MySQL throws an error).
        """
        with mock.patch(
            'xmodule.modulestore.mixed.MixedModuleStore.clone_course',
            mock.Mock(side_effect=Exception()),
        ):
            source_course = CourseFactory.create(default_store=ModuleStoreEnum.Type.split)
            message_too_long = "traceback".rjust(CourseRerunState.MAX_MESSAGE_LENGTH * 2, '-')
            with mock.patch('traceback.format_exc', return_value=message_too_long):
                destination_course_key = self.post_rerun_request(source_course.id)
            rerun_state = CourseRerunState.objects.find_first(course_key=destination_course_key)
            self.assertEqual(rerun_state.state, CourseRerunUIStateManager.State.FAILED)
            self.assertTrue(rerun_state.message.endswith("traceback"))
            self.assertEqual(len(rerun_state.message), CourseRerunState.MAX_MESSAGE_LENGTH)

    def test_rerun_course_wiki_slug(self):
        """
        Test that unique wiki_slug is assigned to rerun course.
        """
        with self.store.default_store(ModuleStoreEnum.Type.split):
            course_data = {
                'org': 'edX',
                'number': '123',
                'display_name': 'Rerun Course',
                'run': '2013'
            }

            source_wiki_slug = '{}.{}.{}'.format(course_data['org'], course_data['number'], course_data['run'])

            source_course_key = _get_course_id(self.store, course_data)
            _create_course(self, source_course_key, course_data)
            source_course = self.store.get_course(source_course_key)

            # Verify created course's wiki_slug.
            self.assertEqual(source_course.wiki_slug, source_wiki_slug)

            destination_course_data = course_data
            destination_course_data['run'] = '2013_Rerun'

            destination_course_key = self.post_rerun_request(
                source_course.id, destination_course_data=destination_course_data
            )
            self.verify_rerun_course(source_course.id, destination_course_key, destination_course_data['display_name'])
            destination_course = self.store.get_course(destination_course_key)

            destination_wiki_slug = '{}.{}.{}'.format(
                destination_course.id.org, destination_course.id.course, destination_course.id.run
            )

            # Verify rerun course's wiki_slug.
            self.assertEqual(destination_course.wiki_slug, destination_wiki_slug)


class ContentLicenseTest(ContentStoreTestCase):
    """
    Tests around content licenses
    """

    def test_course_license_export(self):
        content_store = contentstore()
        root_dir = path(mkdtemp_clean())
        self.course.license = "creative-commons: BY SA"
        self.store.update_item(self.course, None)
        export_course_to_xml(self.store, content_store, self.course.id, root_dir, 'test_license')
        fname = f"{self.course.id.run}.xml"
        run_file_path = root_dir / "test_license" / "course" / fname
        with run_file_path.open() as f:
            run_xml = etree.parse(f)
            self.assertEqual(run_xml.getroot().get("license"), "creative-commons: BY SA")

    def test_video_license_export(self):
        content_store = contentstore()
        root_dir = path(mkdtemp_clean())
        video_block = BlockFactory.create(
            parent_location=self.course.location, category='video',
            license="all-rights-reserved"
        )
        export_course_to_xml(self.store, content_store, self.course.id, root_dir, 'test_license')
        fname = f"{video_block.scope_ids.usage_id.block_id}.xml"
        video_file_path = root_dir / "test_license" / "video" / fname
        with video_file_path.open() as f:
            video_xml = etree.parse(f)
            self.assertEqual(video_xml.getroot().get("license"), "all-rights-reserved")

    def test_license_import(self):
        course_items = import_course_from_xml(
            self.store, self.user.id, TEST_DATA_DIR, ['toy'], create_if_not_present=True
        )
        course = course_items[0]
        self.assertEqual(course.license, "creative-commons: BY")
        videos = self.store.get_items(course.id, qualifiers={'category': 'video'})
        self.assertEqual(videos[0].license, "all-rights-reserved")


class EntryPageTestCase(TestCase):
    """
    Tests entry pages that aren't specific to a course.
    """

    def setUp(self):
        super().setUp()
        self.client = AjaxEnabledTestClient()

    def _test_page(self, page, status_code=200):
        resp = self.client.get_html(page)
        self.assertEqual(resp.status_code, status_code)

    @override_waffle_flag(toggles.LEGACY_STUDIO_LOGGED_OUT_HOME, True)
    def test_how_it_works_legacy(self):
        self._test_page("/howitworks")

    def test_how_it_works_redirect_to_signin(self):
        self._test_page("/howitworks", 302)

    def test_signup(self):
        # deprecated signup url redirects to LMS register.
        self._test_page("/signup", 301)

    def test_login(self):
        # deprecated signin url redirects to LMS login.
        self._test_page("/signin", 302)

    def test_logout(self):
        # Logout redirects.
        self._test_page("/logout", 200)

    @override_waffle_switch(waffle.ENABLE_ACCESSIBILITY_POLICY_PAGE, active=True)
    def test_accessibility(self):
        self._test_page('/accessibility', 302)


def _create_course(test, course_key, course_data):
    """
    Creates a course via an AJAX request and verifies the URL returned in the response.

    Returns the data of the POST response
    """
    course_url = get_url('course_handler', course_key, 'course_key_string')
    response = test.client.ajax_post(course_url, course_data)
    test.assertEqual(response.status_code, 200)
    data = parse_json(response)
    test.assertNotIn('ErrMsg', data)
    test.assertEqual(data['url'], course_url)

    return data


def _get_course_id(store, course_data):
    """Returns the course ID."""
    return store.make_course_key(course_data['org'], course_data['number'], course_data['run'])
