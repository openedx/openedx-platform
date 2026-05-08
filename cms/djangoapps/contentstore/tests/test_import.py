# pylint: disable=protected-access
"""
Tests for import_course_from_xml using the mongo modulestore.
"""


import copy
from unittest import skip
from unittest.mock import patch
from uuid import uuid4

import ddt
from django.conf import settings
from django.core.files.storage import storages
from django.test.client import Client
from django.test.utils import override_settings
from storages.backends.s3boto3 import S3Boto3Storage

from common.djangoapps.util.storage import resolve_storage_backend
from xmodule.contentstore.django import contentstore
from xmodule.exceptions import NotFoundError
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.xml_importer import import_course_from_xml

TEST_DATA_CONTENTSTORE = copy.deepcopy(settings.CONTENTSTORE)
TEST_DATA_CONTENTSTORE['DOC_STORE_CONFIG']['db'] = 'test_xcontent_%s' % uuid4().hex  # noqa: UP031

TEST_DATA_DIR = settings.COMMON_TEST_DATA_ROOT


@ddt.ddt
@override_settings(CONTENTSTORE=TEST_DATA_CONTENTSTORE, SEARCH_ENGINE=None)
class ContentStoreImportTest(ModuleStoreTestCase):
    """
    Tests that rely on the toy and test_import_course courses.
    NOTE: refactor using CourseFactory so they do not.
    """
    def setUp(self):
        super().setUp()

        self.client = Client()
        self.client.login(username=self.user.username, password=self.user_password)

        # block_structure.update_course_in_cache cannot succeed in tests, as it needs to be run async on an lms worker
        self.task_patcher = patch('openedx.core.djangoapps.content.block_structure.tasks.update_course_in_cache_v2')
        self._mock_lms_task = self.task_patcher.start()

    def tearDown(self):
        self.task_patcher.stop()
        super().tearDown()

    def load_test_import_course(self, target_id=None, create_if_not_present=True, module_store=None):
        '''
        Load the standard course used to test imports
        (for do_import_static=False behavior).
        '''
        content_store = contentstore()
        if module_store is None:
            module_store = modulestore()
        import_course_from_xml(
            module_store,
            self.user.id,
            TEST_DATA_DIR,
            ['test_import_course'],
            static_content_store=content_store,
            do_import_static=False,
            verbose=True,
            target_id=target_id,
            create_if_not_present=create_if_not_present,
        )
        course_id = module_store.make_course_key('edX', 'test_import_course', '2012_Fall')
        course = module_store.get_course(course_id)
        self.assertIsNotNone(course)  # noqa: PT009

        return module_store, content_store, course

    def test_import_course_into_similar_namespace(self):
        # Checks to make sure that a course with an org/course like
        # edx/course can be imported into a namespace with an org/course
        # like edx/course_name
        module_store, __, course = self.load_test_import_course()
        course_items = import_course_from_xml(
            module_store,
            self.user.id,
            TEST_DATA_DIR,
            ['test_import_course_2'],
            target_id=course.id,
            verbose=True,
        )
        self.assertEqual(len(course_items), 1)  # noqa: PT009

    def test_unicode_chars_in_course_name_import(self):
        """
        # Test that importing course with unicode 'id' and 'display name' doesn't give UnicodeEncodeError
        """
        # Test with the split modulestore because store.has_course fails in old mongo with unicode characters.
        with modulestore().default_store(ModuleStoreEnum.Type.split):
            module_store = modulestore()
            course_id = module_store.make_course_key('Юникода', 'unicode_course', 'échantillon')
            import_course_from_xml(
                module_store,
                self.user.id,
                TEST_DATA_DIR,
                ['2014_Uni'],
                target_id=course_id,
                create_if_not_present=True
            )

            course = module_store.get_course(course_id)
            self.assertIsNotNone(course)  # noqa: PT009

            # test that course 'display_name' same as imported course 'display_name'
            self.assertEqual(course.display_name, "Φυσικά το όνομα Unicode")  # noqa: PT009

    def test_static_import(self):
        '''
        Stuff in static_import should always be imported into contentstore
        '''
        _, content_store, course = self.load_test_import_course()

        # make sure we have ONE asset in our contentstore ("should_be_imported.html")
        all_assets, count = content_store.get_all_content_for_course(course.id)
        print("len(all_assets)=%d" % len(all_assets))  # noqa: UP031
        self.assertEqual(len(all_assets), 1)  # noqa: PT009
        self.assertEqual(count, 1)  # noqa: PT009

        content = None
        try:
            location = course.id.make_asset_key('asset', 'should_be_imported.html')
            content = content_store.find(location)
        except NotFoundError:
            pass

        self.assertIsNotNone(content)  # noqa: PT009

        # make sure course.static_asset_path is correct
        print(f"static_asset_path = {course.static_asset_path}")
        self.assertEqual(course.static_asset_path, 'test_import_course')  # noqa: PT009

    def test_asset_import_nostatic(self):
        '''
        This test validates that an image asset is NOT imported when do_import_static=False
        '''
        content_store = contentstore()

        module_store = modulestore()
        import_course_from_xml(
            module_store, self.user.id, TEST_DATA_DIR, ['toy'],
            static_content_store=content_store, do_import_static=False,
            do_import_python_lib=False,  # python_lib.zip is special-cased -- exclude it too
            create_if_not_present=True, verbose=True
        )

        course = module_store.get_course(module_store.make_course_key('edX', 'toy', '2012_Fall'))

        # make sure we have NO assets in our contentstore
        all_assets, count = content_store.get_all_content_for_course(course.id)
        self.assertEqual(all_assets, [])  # noqa: PT009
        self.assertEqual(count, 0)  # noqa: PT009

    def test_no_static_link_rewrites_on_import(self):
        module_store = modulestore()
        courses = import_course_from_xml(
            module_store, self.user.id, TEST_DATA_DIR, ['toy'], do_import_static=False, verbose=True,
            create_if_not_present=True
        )
        course_key = courses[0].id

        handouts = module_store.get_item(course_key.make_usage_key('course_info', 'handouts'))
        self.assertIn('/static/', handouts.data)  # noqa: PT009

        handouts = module_store.get_item(course_key.make_usage_key('html', 'toyhtml'))
        self.assertIn('/static/', handouts.data)  # noqa: PT009

    def test_tab_name_imports_correctly(self):
        _module_store, _content_store, course = self.load_test_import_course()
        print(f"course tabs = {course.tabs}")
        self.assertEqual(course.tabs[1]['name'], 'Syllabus')  # noqa: PT009

    def test_reimport(self):
        __, __, course = self.load_test_import_course(create_if_not_present=True)
        self.load_test_import_course(target_id=course.id)

    @skip("OldMongo Deprecation")
    def test_rewrite_reference_list(self):
        # This test fails with split modulestore (the HTML component is not in "different_course_id" namespace).
        # More investigation needs to be done.
        module_store = modulestore()
        target_id = module_store.make_course_key('testX', 'conditional_copy', 'copy_run')
        import_course_from_xml(
            module_store,
            self.user.id,
            TEST_DATA_DIR,
            ['conditional'],
            target_id=target_id,
            create_if_not_present=True
        )
        conditional_block = module_store.get_item(
            target_id.make_usage_key('conditional', 'condone')
        )
        self.assertIsNotNone(conditional_block)  # noqa: PT009
        different_course_id = module_store.make_course_key('edX', 'different_course', 'course_run')
        self.assertListEqual(  # noqa: PT009
            [
                target_id.make_usage_key('problem', 'choiceprob'),
                different_course_id.make_usage_key('html', 'for_testing_import_rewrites')
            ],
            conditional_block.sources_list
        )
        self.assertListEqual(  # noqa: PT009
            [
                target_id.make_usage_key('html', 'congrats'),
                target_id.make_usage_key('html', 'secret_page')
            ],
            conditional_block.show_tag_list
        )

    def test_rewrite_reference_value_dict_published(self):
        """
        Test rewriting references in ReferenceValueDict, specifically with published content.
        """
        self._verify_split_test_import(
            'split_test_copy',
            'split_test_block',
            'split1',
            {"0": 'sample_0', "2": 'sample_2'},
        )

    def test_rewrite_reference_value_dict_draft(self):
        """
        Test rewriting references in ReferenceValueDict, specifically with draft content.
        """
        self._verify_split_test_import(
            'split_test_copy_with_draft',
            'split_test_block_draft',
            'fb34c21fe64941999eaead421a8711b8',
            {"0": '9f0941d021414798836ef140fb5f6841', "1": '0faf29473cf1497baa33fcc828b179cd'},
        )

    def _verify_split_test_import(self, target_course_name, source_course_name, split_test_name, groups_to_verticals):  # pylint: disable=missing-function-docstring
        module_store = modulestore()
        target_id = module_store.make_course_key('testX', target_course_name, 'copy_run')
        import_course_from_xml(
            module_store,
            self.user.id,
            TEST_DATA_DIR,
            [source_course_name],
            target_id=target_id,
            create_if_not_present=True
        )
        split_test_block = module_store.get_item(
            target_id.make_usage_key('split_test', split_test_name)
        )
        self.assertIsNotNone(split_test_block)  # noqa: PT009

        remapped_verticals = {
            key: target_id.make_usage_key('vertical', value) for key, value in groups_to_verticals.items()
        }

        self.assertEqual(remapped_verticals, split_test_block.group_id_to_child)  # noqa: PT009

    def test_video_components_present_while_import(self):
        """
        Test that video components with same edx_video_id are present while re-importing
        """
        module_store = modulestore()
        course_id = module_store.make_course_key('edX', 'test_import_course', '2012_Fall')

        # Import first time
        __, __, course = self.load_test_import_course(target_id=course_id, module_store=module_store)

        # Re-import
        __, __, re_course = self.load_test_import_course(target_id=course.id, module_store=module_store)

        vertical = module_store.get_item(re_course.id.make_usage_key('vertical', 'vertical_test'))

        video = module_store.get_item(vertical.children[1])
        self.assertEqual(video.display_name, 'default')  # noqa: PT009

    @override_settings(
        COURSE_IMPORT_EXPORT_STORAGE="cms.djangoapps.contentstore.storage.ImportExportS3Storage",
        STORAGES={
            'default': {
                'BACKEND': "django.core.files.storage.FileSystemStorage"
            }
        }
    )
    def test_default_storage(self):
        """ Ensure the default storage is invoked, even if course export storage is configured """
        storage = storages["default"]
        self.assertEqual(storage.__class__.__name__, "FileSystemStorage")  # noqa: PT009

    @override_settings(
        COURSE_IMPORT_EXPORT_STORAGE="cms.djangoapps.contentstore.storage.ImportExportS3Storage",
        STORAGES={
            'default': {
                'BACKEND': "django.core.files.storage.FileSystemStorage"
            }
        },
        COURSE_IMPORT_EXPORT_BUCKET="bucket_name_test"
    )
    def test_resolve_happy_path_storage(self):
        """ Make sure that the correct course export storage is being used """
        storage = resolve_storage_backend(
            storage_key="course_import_export",
            legacy_setting_key="COURSE_IMPORT_EXPORT_STORAGE"
        )
        self.assertEqual(storage.__class__.__name__, "ImportExportS3Storage")  # noqa: PT009
        self.assertEqual(storage.bucket_name, "bucket_name_test")  # noqa: PT009

    @override_settings()
    def test_resolve_storage_with_no_config(self):
        """ If no storage setup is defined, we get FileSystemStorage by default """
        del settings.COURSE_IMPORT_EXPORT_STORAGE
        del settings.COURSE_IMPORT_EXPORT_BUCKET
        storage = resolve_storage_backend(
            storage_key="course_import_export",
            legacy_setting_key="COURSE_IMPORT_EXPORT_STORAGE"
        )
        self.assertEqual(storage.__class__.__name__, "FileSystemStorage")  # noqa: PT009

    @override_settings(
        COURSE_IMPORT_EXPORT_STORAGE=None,
        COURSE_IMPORT_EXPORT_BUCKET="bucket_name_test",
        STORAGES={
            'course_import_export': {
                'BACKEND': 'cms.djangoapps.contentstore.storage.ImportExportS3Storage',
                'OPTIONS': {}
            }
        }
    )
    def test_resolve_storage_using_django5_settings(self):
        """ Simulating a Django 4 environment using Django 5 Storages configuration """
        storage = resolve_storage_backend(
            storage_key="course_import_export",
            legacy_setting_key="COURSE_IMPORT_EXPORT_STORAGE"
        )
        self.assertEqual(storage.__class__.__name__, "ImportExportS3Storage")  # noqa: PT009
        self.assertEqual(storage.bucket_name, "bucket_name_test")  # noqa: PT009

    @override_settings(
        STORAGES={
            'course_import_export': {
                'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage',
                'OPTIONS': {
                    'bucket_name': 'bucket_name_test'
                }
            }
        }
    )
    def test_resolve_storage_using_django5_settings_with_options(self):
        """ Ensure we call the storage class with the correct parameters and Django 5 setup """
        del settings.COURSE_IMPORT_EXPORT_STORAGE
        del settings.COURSE_IMPORT_EXPORT_BUCKET
        storage = resolve_storage_backend(
            storage_key="course_import_export",
            legacy_setting_key="COURSE_IMPORT_EXPORT_STORAGE"
        )
        self.assertEqual(storage.__class__.__name__, S3Boto3Storage.__name__)  # noqa: PT009
        self.assertEqual(storage.bucket_name, "bucket_name_test")  # noqa: PT009
