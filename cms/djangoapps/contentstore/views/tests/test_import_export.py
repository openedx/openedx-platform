"""
Unit tests for course import and export
"""
import copy
import itertools
import json
import logging
import os
import re
import shutil
import tarfile
import tempfile
from io import BytesIO
from unittest.mock import Mock, patch
from uuid import uuid4
from zipfile import ZipFile

import ddt
import lxml
from bson import ObjectId
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import SuspiciousOperation
from django.core.files.storage import FileSystemStorage
from django.test.utils import override_settings
from edx_toggles.toggles.testutils import override_waffle_flag
from milestones.tests.utils import MilestonesTestCaseMixin
from opaque_keys.edx.locator import LibraryLocator
from openedx_authz.constants.roles import COURSE_DATA_RESEARCHER, COURSE_STAFF
from path import Path as path
from rest_framework import status
from rest_framework.test import APIClient
from storages.backends.s3boto3 import S3Boto3Storage
from user_tasks.models import UserTaskStatus

from cms.djangoapps.contentstore import errors as import_error
from cms.djangoapps.contentstore import toggles
from cms.djangoapps.contentstore.api.tests.base import BaseCourseViewTest
from cms.djangoapps.contentstore.storage import course_import_export_storage
from cms.djangoapps.contentstore.tests.test_libraries import LibraryTestCase
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import reverse_course_url
from cms.djangoapps.models.settings.course_metadata import CourseMetadata
from common.djangoapps.student import auth
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.util import milestones_helpers
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthzTestMixin
from openedx.core.lib.extract_archive import safe_extractall
from xmodule.contentstore.django import contentstore
from xmodule.modulestore import LIBRARY_ROOT, ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import DuplicateCourseError, InvalidProctoringProvider
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory, LibraryFactory
from xmodule.modulestore.tests.utils import SPLIT_MODULESTORE_SETUP, TEST_DATA_DIR, MongoContentstoreBuilder
from xmodule.modulestore.xml_exporter import export_course_to_xml, export_library_to_xml
from xmodule.modulestore.xml_importer import (
    BlockFailedToImport,
    CourseImportManager,
    ErrorReadingFileException,
    import_course_from_xml,
    import_library_from_xml,
)

TASK_LOGGER = 'cms.djangoapps.contentstore.tasks.LOGGER'
TEST_DATA_CONTENTSTORE = copy.deepcopy(settings.CONTENTSTORE)
TEST_DATA_CONTENTSTORE['DOC_STORE_CONFIG']['db'] = 'test_xcontent_%s' % uuid4().hex  # noqa: UP031
TEST_DATA_DIR = settings.COMMON_TEST_DATA_ROOT  # noqa: F811
User = get_user_model()

log = logging.getLogger(__name__)


@override_settings(CONTENTSTORE=TEST_DATA_CONTENTSTORE)
class ImportEntranceExamTestCase(CourseTestCase, MilestonesTestCaseMixin):
    """
    Unit tests for importing a course with entrance exam
    """

    def setUp(self):
        super().setUp()
        self.url = reverse_course_url('import_handler', self.course.id)
        self.content_dir = path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.content_dir)

        # Create tar test file -----------------------------------------------
        # OK course with entrance exam section:
        entrance_exam_dir = tempfile.mkdtemp(dir=self.content_dir)
        # test course being deeper down than top of tar file
        embedded_exam_dir = os.path.join(entrance_exam_dir, "grandparent", "parent")
        os.makedirs(os.path.join(embedded_exam_dir, "course"))
        os.makedirs(os.path.join(embedded_exam_dir, "chapter"))
        with open(os.path.join(embedded_exam_dir, "course.xml"), "w+") as f:
            f.write('<course url_name="2013_Spring" org="EDx" course="0.00x"/>')

        with open(os.path.join(embedded_exam_dir, "course", "2013_Spring.xml"), "w+") as f:
            f.write(
                '<course '
                'entrance_exam_enabled="true" entrance_exam_id="xyz" entrance_exam_minimum_score_pct="0.7">'
                '<chapter url_name="2015_chapter_entrance_exam"/></course>'
            )

        with open(os.path.join(embedded_exam_dir, "chapter", "2015_chapter_entrance_exam.xml"), "w+") as f:
            f.write('<chapter display_name="Entrance Exam" in_entrance_exam="true" is_entrance_exam="true"></chapter>')

        self.entrance_exam_tar = os.path.join(self.content_dir, "entrance_exam.tar.gz")
        with tarfile.open(self.entrance_exam_tar, "w:gz") as gtar:
            gtar.add(entrance_exam_dir)

    def test_import_existing_entrance_exam_course(self):
        """
        Check that course is imported successfully as an entrance exam.
        """
        course = self.store.get_course(self.course.id)
        self.assertIsNotNone(course)  # noqa: PT009
        self.assertEqual(course.entrance_exam_enabled, False)  # noqa: PT009

        with open(self.entrance_exam_tar, 'rb') as gtar:  # pylint: disable=bad-option-value, open-builtin
            args = {"name": self.entrance_exam_tar, "course-data": [gtar]}
            resp = self.client.post(self.url, args)
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        course = self.store.get_course(self.course.id)
        self.assertIsNotNone(course)  # noqa: PT009
        self.assertEqual(course.entrance_exam_enabled, True)  # noqa: PT009
        self.assertEqual(course.entrance_exam_minimum_score_pct, 0.7)  # noqa: PT009

    def test_import_delete_pre_exiting_entrance_exam(self):
        """
        Check that pre existed entrance exam content should be overwrite with the imported course.
        """
        exam_url = f'/course/{str(self.course.id)}/entrance_exam/'
        resp = self.client.post(exam_url, {'entrance_exam_minimum_score_pct': 0.5}, http_accept='application/json')
        self.assertEqual(resp.status_code, 201)  # noqa: PT009

        # Reload the test course now that the exam block has been added
        self.course = modulestore().get_course(self.course.id)
        metadata = CourseMetadata.fetch_all(self.course)
        self.assertTrue(metadata['entrance_exam_enabled'])  # noqa: PT009
        self.assertIsNotNone(metadata['entrance_exam_minimum_score_pct'])  # noqa: PT009
        self.assertEqual(metadata['entrance_exam_minimum_score_pct']['value'], 0.5)  # noqa: PT009
        self.assertTrue(len(milestones_helpers.get_course_milestones(str(self.course.id))))  # noqa: PT009
        content_milestones = milestones_helpers.get_course_content_milestones(
            str(self.course.id),
            metadata['entrance_exam_id']['value'],
            milestones_helpers.get_milestone_relationship_types()['FULFILLS']
        )
        self.assertTrue(len(content_milestones))  # noqa: PT009

        # Now import entrance exam course
        with open(self.entrance_exam_tar, 'rb') as gtar:  # pylint: disable=bad-option-value, open-builtin
            args = {"name": self.entrance_exam_tar, "course-data": [gtar]}
            resp = self.client.post(self.url, args)
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        course = self.store.get_course(self.course.id)
        self.assertIsNotNone(course)  # noqa: PT009
        self.assertEqual(course.entrance_exam_enabled, True)  # noqa: PT009
        self.assertEqual(course.entrance_exam_minimum_score_pct, 0.7)  # noqa: PT009


@ddt.ddt
@override_settings(CONTENTSTORE=TEST_DATA_CONTENTSTORE)
class ImportTestCase(CourseTestCase):
    """
    Unit tests for importing a course or Library
    """
    CREATE_USER = True

    def setUp(self):
        super().setUp()
        self.url = reverse_course_url('import_handler', self.course.id)
        self.content_dir = path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.content_dir)

        def touch(name):
            """ Equivalent to shell's 'touch'"""
            with open(name, 'a'):
                os.utime(name, None)

        # Create tar test files -----------------------------------------------
        # OK course:
        good_dir = tempfile.mkdtemp(dir=self.content_dir)
        # test course being deeper down than top of tar file
        embedded_dir = os.path.join(good_dir, "grandparent", "parent")
        os.makedirs(os.path.join(embedded_dir, "course"))
        with open(os.path.join(embedded_dir, "course.xml"), "w+") as f:
            f.write('<course url_name="2013_Spring" org="EDx" course="0.00x"/>')

        with open(os.path.join(embedded_dir, "course", "2013_Spring.xml"), "w+") as f:
            f.write('<course></course>')

        self.good_tar = os.path.join(self.content_dir, "good.tar.gz")
        with tarfile.open(self.good_tar, "w:gz") as gtar:
            gtar.add(good_dir)

        self.good_zip = os.path.join(self.content_dir, "good.zip")
        with ZipFile(self.good_zip, "w") as gzip:
            for folder_name, subfolders, filenames in os.walk(good_dir):  # noqa: B007
                for filename in filenames:
                    file_path = os.path.join(folder_name, filename)
                    gzip.write(file_path, file_path[len(good_dir) + 1:])

        self.good_archives = {
            'zip': self.good_zip,
            'tar': self.good_tar,
        }

        # Bad course (no 'course.xml' file):
        bad_dir = tempfile.mkdtemp(dir=self.content_dir)
        touch(os.path.join(bad_dir, "bad.xml"))
        self.bad_tar = os.path.join(self.content_dir, "bad.tar.gz")
        with tarfile.open(self.bad_tar, "w:gz") as btar:
            btar.add(bad_dir)

        self.bad_zip = os.path.join(self.content_dir, "bad.zip")
        with ZipFile(self.bad_zip, "w") as bzip:
            for folder_name, subfolders, filenames in os.walk(bad_dir):  # noqa: B007
                for filename in filenames:
                    file_path = os.path.join(folder_name, filename)
                    bzip.write(file_path, file_path[len(good_dir) + 1:])

        self.bad_archives = {
            'zip': self.bad_zip,
            'tar': self.bad_tar,
        }
        self.unsafe_common_dir = path(tempfile.mkdtemp(dir=self.content_dir))
        self.log_prefix = f"Course import {self.course.id}:"

    @classmethod
    def setUpClass(cls):
        """
        Creates data shared by all tests.
        """
        super().setUpClass()
        cls.UnpackingError = -1
        cls.VerifyingError = -2
        cls.UpdatingError = -3

    def assertImportStatusResponse(self, response, expected_status=None, expected_message=None):
        """
        Fail if the import response does not match with the provided status and message.
        """
        self.assertEqual(response["ImportStatus"], expected_status)  # noqa: PT009
        if expected_message:
            self.assertEqual(response['Message'], expected_message)  # noqa: PT009

    def get_import_status(self, course_id, file_path):
        """Helper method to get course import status."""
        resp = self.client.get(
            reverse_course_url(
                'import_status_handler',
                course_id,
                kwargs={'filename': os.path.split(file_path)[1]}
            )
        )
        return json.loads(resp.content)

    def import_file_in_course(self, file_path):
        """Helper method to import provided file in the course."""
        with open(file_path, 'rb') as file_data:
            args = {"name": file_path, "course-data": [file_data]}
            return self.client.post(self.url, args)

    @patch(TASK_LOGGER)
    @ddt.data('tar', 'zip')
    def test_no_coursexml(self, fmt, mocked_log):
        """
        Check that the response for a file import without a course.xml is
        correct.
        """
        bad_file = self.bad_archives[fmt]
        error_msg = import_error.FILE_MISSING.format('course.xml')
        expected_error_mesg = f'{self.log_prefix} {error_msg}'
        response = self.import_file_in_course(bad_file)

        self.assertEqual(response.status_code, 200)  # noqa: PT009
        mocked_log.error.assert_called_once_with(expected_error_mesg)

        # Check that `import_status` returns the appropriate stage (i.e., the
        # stage at which import failed).
        resp_status = self.get_import_status(self.course.id, bad_file)
        self.assertImportStatusResponse(resp_status, self.VerifyingError, error_msg)

    @ddt.data('zip', 'tar')
    def test_with_coursexml(self, fmt):
        """
        Check that the response for a file import with a course.xml is
        correct.
        """
        good_file = self.good_archives[fmt]
        response = self.import_file_in_course(good_file)
        self.assertEqual(response.status_code, 200)  # noqa: PT009

    @ddt.data('zip', 'tar')
    def test_import_in_existing_course(self, fmt):
        """
        Check that course is imported successfully in existing course and users have their access roles
        """
        good_file = self.good_archives[fmt]

        # Create a non_staff user and add it to course staff only
        __, nonstaff_user = self.create_non_staff_authed_user_client()
        auth.add_users(self.user, CourseStaffRole(self.course.id), nonstaff_user)

        course = self.store.get_course(self.course.id)
        self.assertIsNotNone(course)  # noqa: PT009
        display_name_before_import = course.display_name

        # Check that global staff user can import course
        response = self.import_file_in_course(good_file)
        self.assertEqual(response.status_code, 200)  # noqa: PT009

        course = self.store.get_course(self.course.id)
        self.assertIsNotNone(course)  # noqa: PT009
        display_name_after_import = course.display_name

        # Check that course display name have changed after import
        self.assertNotEqual(display_name_before_import, display_name_after_import)  # noqa: PT009

        # Now check that non_staff user has his same role
        self.assertFalse(CourseInstructorRole(self.course.id).has_user(nonstaff_user))  # noqa: PT009
        self.assertTrue(CourseStaffRole(self.course.id).has_user(nonstaff_user))  # noqa: PT009

        # Now course staff user can also successfully import course
        self.client.login(username=nonstaff_user.username, password='foo')
        resp = self.import_file_in_course(good_file)
        self.assertEqual(resp.status_code, 200)  # noqa: PT009

        # Now check that non_staff user has his same role
        self.assertFalse(CourseInstructorRole(self.course.id).has_user(nonstaff_user))  # noqa: PT009
        self.assertTrue(CourseStaffRole(self.course.id).has_user(nonstaff_user))  # noqa: PT009

    ## Unsafe tar methods #####################################################
    # Each of these methods creates a tarfile with a single type of unsafe
    # content.
    def _fifo_tar(self):
        """
        Tar file with FIFO
        """
        fifop = self.unsafe_common_dir / "fifo.file"
        fifo_tar = self.unsafe_common_dir / "fifo.tar.gz"
        os.mkfifo(fifop)
        with tarfile.open(fifo_tar, "w:gz") as tar:
            tar.add(fifop)

        return fifo_tar

    def _symlink_tar(self):
        """
        Tarfile with symlink to path outside directory.
        """
        outsidep = self.unsafe_common_dir / "unsafe_file.txt"
        symlinkp = self.unsafe_common_dir / "symlink.txt"
        symlink_tar = self.unsafe_common_dir / "symlink.tar.gz"
        outsidep.symlink(symlinkp)
        with tarfile.open(symlink_tar, "w:gz") as tar:
            tar.add(symlinkp)

        return symlink_tar

    def _outside_tar(self):
        """
        Tarfile with file that extracts to outside directory.

        Extracting this tarfile in directory <dir> will put its contents
        directly in <dir> (rather than <dir/tarname>).
        """
        outside_tar = self.unsafe_common_dir / "unsafe_file.tar.gz"
        with tarfile.open(outside_tar, "w:gz") as tar:
            tar.addfile(tarfile.TarInfo(str(self.content_dir / "a_file")))

        return outside_tar

    def _outside_tar2(self):
        """
        Tarfile with file that extracts to outside directory.

        The path here matches the basename (`self.unsafe_common_dir`), but
        then "cd's out". E.g. "/usr/../etc" == "/etc", but the naive basename
        of the first (but not the second) is "/usr"

        Extracting this tarfile in directory <dir> will also put its contents
        directly in <dir> (rather than <dir/tarname>).
        """
        outside_tar = self.unsafe_common_dir / "unsafe_file.tar.gz"
        with tarfile.open(outside_tar, "w:gz") as tar:
            tar.addfile(tarfile.TarInfo(str(self.unsafe_common_dir / "../a_file")))

        return outside_tar

    def _edx_platform_tar(self):
        """
        Tarfile with file that extracts to edx-platform directory.

        Extracting this tarfile in directory <dir> will also put its contents
        directly in <dir> (rather than <dir/tarname>).
        """
        outside_tar = self.unsafe_common_dir / "unsafe_file.tar.gz"
        with tarfile.open(outside_tar, "w:gz") as tar:
            tar.addfile(tarfile.TarInfo(os.path.join(os.path.abspath("."), "a_file")))

        return outside_tar

    def test_unsafe_tar(self):
        """
        Check that safety measure work.

        This includes:
            'tarbombs' which include files or symlinks with paths
        outside or directly in the working directory,
            'special files' (character device, block device or FIFOs),

        all raise exceptions/400s.
        """

        def try_tar(tarpath):
            """ Attempt to tar an unacceptable file """
            resp = self.import_file_in_course(tarpath)
            self.assertEqual(resp.status_code, 200)  # noqa: PT009

            resp = self.get_import_status(self.course.id, tarpath)
            self.assertEqual(resp["ImportStatus"], -1)  # noqa: PT009

        try_tar(self._fifo_tar())
        try_tar(self._symlink_tar())
        try_tar(self._outside_tar())
        try_tar(self._outside_tar2())
        try_tar(self._edx_platform_tar())

        # test trying to open a tar outside of the normal data directory
        with self.settings(DATA_DIR='/not/the/data/dir'):
            try_tar(self._edx_platform_tar())

        # Check that `import_status` returns the appropriate stage (i.e.,
        # either 3, indicating all previous steps are completed, or 0,
        # indicating no upload in progress)
        resp_status = self.get_import_status(self.course.id, self.good_tar)
        import_status = resp_status["ImportStatus"]
        self.assertIn(import_status, (0, 3))  # noqa: PT009

    def test_library_import(self):
        """
        Try importing a known good library archive, and verify that the
        contents of the library have completely replaced the old contents.
        """
        # Create some blocks to overwrite
        library = LibraryFactory.create(modulestore=self.store)
        lib_key = library.location.library_key
        test_block = BlockFactory.create(
            category="vertical",
            parent_location=library.location,
            user_id=self.user.id,
            publish_item=False,
        )
        test_block2 = BlockFactory.create(
            category="vertical",
            parent_location=library.location,
            user_id=self.user.id,
            publish_item=False
        )
        # Create a library and blocks that should remain unmolested.
        unchanged_lib = LibraryFactory.create()
        unchanged_key = unchanged_lib.location.library_key
        test_block3 = BlockFactory.create(
            category="vertical",
            parent_location=unchanged_lib.location,
            user_id=self.user.id,
            publish_item=False
        )
        test_block4 = BlockFactory.create(
            category="vertical",
            parent_location=unchanged_lib.location,
            user_id=self.user.id,
            publish_item=False
        )
        # Refresh library.
        library = self.store.get_library(lib_key)
        children = [self.store.get_item(child).url_name for child in library.children]
        self.assertEqual(len(children), 2)  # noqa: PT009
        self.assertIn(test_block.url_name, children)  # noqa: PT009
        self.assertIn(test_block2.url_name, children)  # noqa: PT009

        unchanged_lib = self.store.get_library(unchanged_key)
        children = [self.store.get_item(child).url_name for child in unchanged_lib.children]
        self.assertEqual(len(children), 2)  # noqa: PT009
        self.assertIn(test_block3.url_name, children)  # noqa: PT009
        self.assertIn(test_block4.url_name, children)  # noqa: PT009

        extract_dir = path(tempfile.mkdtemp(dir=settings.DATA_DIR))
        # the extract_dir needs to be passed as a relative dir to
        # import_library_from_xml
        extract_dir_relative = path.relpath(extract_dir, settings.DATA_DIR)

        try:
            safe_extractall(path(TEST_DATA_DIR) / 'imports' / 'library.HhJfPD.tar.gz', extract_dir)
            library_items = import_library_from_xml(
                self.store,
                self.user.id,
                settings.GITHUB_REPO_ROOT,
                [extract_dir_relative / 'library'],
                load_error_blocks=False,
                static_content_store=contentstore(),
                target_id=lib_key
            )
        finally:
            shutil.rmtree(extract_dir)

        self.assertEqual(lib_key, library_items[0].location.library_key)  # noqa: PT009
        library = self.store.get_library(lib_key)
        children = [self.store.get_item(child).url_name for child in library.children]
        self.assertEqual(len(children), 3)  # noqa: PT009
        self.assertNotIn(test_block.url_name, children)  # noqa: PT009
        self.assertNotIn(test_block2.url_name, children)  # noqa: PT009

        unchanged_lib = self.store.get_library(unchanged_key)
        children = [self.store.get_item(child).url_name for child in unchanged_lib.children]
        self.assertEqual(len(children), 2)  # noqa: PT009
        self.assertIn(test_block3.url_name, children)  # noqa: PT009
        self.assertIn(test_block4.url_name, children)  # noqa: PT009

    @ddt.data(
        ModuleStoreEnum.Branch.draft_preferred,
        ModuleStoreEnum.Branch.published_only,
    )
    def test_library_import_branch_settings(self, branch_setting):
        """
        Try importing a known good library archive under either branch setting.
        The branch setting should have no effect on library import.
        """
        with self.store.branch_setting(branch_setting):
            library = LibraryFactory.create(modulestore=self.store)
            lib_key = library.location.library_key
            extract_dir = path(tempfile.mkdtemp(dir=settings.DATA_DIR))
            # the extract_dir needs to be passed as a relative dir to
            # import_library_from_xml
            extract_dir_relative = path.relpath(extract_dir, settings.DATA_DIR)

            try:
                safe_extractall(path(TEST_DATA_DIR) / 'imports' / 'library.HhJfPD.tar.gz', extract_dir)
                import_library_from_xml(
                    self.store,
                    self.user.id,
                    settings.GITHUB_REPO_ROOT,
                    [extract_dir_relative / 'library'],
                    load_error_blocks=False,
                    static_content_store=contentstore(),
                    target_id=lib_key
                )
            finally:
                shutil.rmtree(extract_dir)

    @ddt.data(
        ModuleStoreEnum.Branch.draft_preferred,
        ModuleStoreEnum.Branch.published_only,
    )
    def test_library_import_branch_settings_again(self, branch_setting):
        # Construct the contentstore for storing the import
        with MongoContentstoreBuilder().build() as source_content:
            # Construct the modulestore for storing the import (using the previously created contentstore)
            with SPLIT_MODULESTORE_SETUP.build(contentstore=source_content) as source_store:
                # Use the test branch setting.
                with source_store.branch_setting(branch_setting):  # pylint: disable=no-member
                    source_library_key = LibraryLocator(org='TestOrg', library='TestProbs')

                    extract_dir = path(tempfile.mkdtemp(dir=settings.DATA_DIR))
                    # the extract_dir needs to be passed as a relative dir to
                    # import_library_from_xml
                    extract_dir_relative = path.relpath(extract_dir, settings.DATA_DIR)

                    try:
                        safe_extractall(path(TEST_DATA_DIR) / 'imports' / 'library.HhJfPD.tar.gz', extract_dir)
                        import_library_from_xml(
                            source_store,
                            self.user.id,
                            settings.GITHUB_REPO_ROOT,
                            [extract_dir_relative / 'library'],
                            static_content_store=source_content,
                            target_id=source_library_key,
                            load_error_blocks=False,
                            raise_on_failure=True,
                            create_if_not_present=True,
                        )
                    finally:
                        shutil.rmtree(extract_dir)

    @patch(TASK_LOGGER)
    @ddt.data('zip', 'tar')
    def test_import_failed_with_no_user_permission(self, fmt, mocked_log):
        """
        Tests course import failure when user have no permission
        """
        good_file = self.good_archives[fmt]
        expected_error_mesg = f'{self.log_prefix} User permission denied: {self.user.username}'
        with patch('cms.djangoapps.contentstore.tasks.has_course_author_access', Mock(return_value=False)):
            response = self.import_file_in_course(good_file)
        self.assertEqual(response.status_code, 200)  # noqa: PT009
        mocked_log.error.assert_called_once_with(expected_error_mesg)

        status_response = self.get_import_status(self.course.id, good_file)
        self.assertImportStatusResponse(status_response, self.UnpackingError, import_error.COURSE_PERMISSION_DENIED)

    @patch(TASK_LOGGER)
    @ddt.data('zip', 'tar')
    def test_import_failed_with_unknown_user(self, fmt, mocked_log):
        """
        Tests that course import failure with an unknown user id.
        """
        expected_error_mesg = f'{self.log_prefix} Unknown User: {self.user.id}'

        good_file = self.good_archives[fmt]
        with patch('django.contrib.auth.models.User.objects.get', side_effect=User.DoesNotExist):
            response = self.import_file_in_course(good_file)
            self.assertEqual(response.status_code, 200)  # noqa: PT009
            mocked_log.error.assert_called_once_with(expected_error_mesg)

        status_response = self.get_import_status(self.course.id, good_file)
        self.assertImportStatusResponse(status_response, self.UnpackingError, import_error.USER_PERMISSION_DENIED)

    @patch(TASK_LOGGER)
    @ddt.data('zip', 'tar')
    def test_import_failed_with_unsafe_file(self, fmt, mocked_log):
        """
        Tests course import failure with unsafe file.
        """
        good_file = self.good_archives[fmt]
        expected_error_mesg = f'{self.log_prefix} Unsafe archive file'
        with patch('cms.djangoapps.contentstore.tasks.safe_extractall', side_effect=SuspiciousOperation):
            response = self.import_file_in_course(good_file)

        self.assertEqual(response.status_code, 200)  # noqa: PT009
        mocked_log.error.assert_called_once_with(expected_error_mesg)

        status_response = self.get_import_status(self.course.id, good_file)
        self.assertImportStatusResponse(status_response, self.UnpackingError, import_error.UNSAFE_ARCHIVE_FILE)

    @patch(TASK_LOGGER)
    @ddt.data('zip', 'tar')
    def test_import_failed_with_unknown_unpacking_error(self, fmt, mocked_log):
        """
        Tests that course import failure for unknown error while unpacking
        """
        good_file = self.good_archives[fmt]
        expected_error_mesg = f'{self.log_prefix} Unknown error while unpacking'
        with patch.object(course_import_export_storage, 'open', side_effect=Exception):
            response = self.import_file_in_course(good_file)

        self.assertEqual(response.status_code, 200)  # noqa: PT009
        mocked_log.exception.assert_called_once_with(expected_error_mesg, exc_info=True)

        status_response = self.get_import_status(self.course.id, good_file)
        self.assertImportStatusResponse(status_response, self.UnpackingError, import_error.UNKNOWN_ERROR_IN_UNPACKING)

    @patch(TASK_LOGGER)
    @patch('olxcleaner.validate')
    @patch('cms.djangoapps.contentstore.tasks.report_error_summary')
    @patch('cms.djangoapps.contentstore.tasks.report_errors')
    @ddt.data('zip', 'tar')
    def test_import_failed_with_olx_validations(self, fmt, mocked_report, mocked_summary, mocked_validate,
                                                mocked_log):
        """
        Tests that course import failure for unknown error while unpacking
        """
        good_file = self.good_archives[fmt]
        errors = [Mock(description='DuplicateURLNameError', level_val=3)]
        mocked_summary.return_value = [f'ERROR {error.description} found in content' for error in errors]
        mocked_report.return_value = [f'Errors: {len(errors)}']
        mocked_validate.return_value = [
            Mock(), Mock(errors=errors, return_error=Mock(return_value=True)), Mock()
        ]
        expected_error_mesg = f'{self.log_prefix} CourseOlx validation failed.'
        with patch.dict(settings.FEATURES, ENABLE_COURSE_OLX_VALIDATION=True):
            response = self.import_file_in_course(good_file)

        self.assertEqual(response.status_code, 200)  # noqa: PT009
        mocked_log.error.assert_called_once_with(expected_error_mesg)

        status_response = self.get_import_status(self.course.id, good_file)
        self.assertImportStatusResponse(status_response, self.VerifyingError, import_error.OLX_VALIDATION_FAILED)

    @patch(TASK_LOGGER)
    @patch.object(CourseImportManager, 'import_children')
    @ddt.data(
        (
            InvalidProctoringProvider('foo', ['bar']),
            "The selected proctoring provider, foo, is not a valid provider. Please select from one of ['bar'].",
        ),
        (DuplicateCourseError("foo", "foobar"), 'Cannot create course foo, which duplicates foobar'),
        (ErrorReadingFileException("assets.xml"), "Error while reading assets.xml. Check file for XML errors."),
        (BlockFailedToImport("Unit 1", "foo/bar"), "Failed to import block: Unit 1 at location: foo/bar"),
    )
    @ddt.unpack
    def test_import_failure_is_descriptive_for_known_failures(self, exc, expected_mesg, mocked_import, mocked_log):
        """
        Test that when course import fails with a known failure, user get a descriptive error message.
        """
        for good_file in self.good_archives.values():
            mocked_import.side_effect = exc
            expected_exception_messages = f"{self.log_prefix} Error while importing course: {str(exc)}"
            response = self.import_file_in_course(good_file)
            self.assertEqual(response.status_code, 200)  # noqa: PT009
            mocked_log.exception.assert_called_once_with(expected_exception_messages)
            mocked_log.reset_mock()

            status_response = self.get_import_status(self.course.id, good_file)
            self.assertImportStatusResponse(status_response, self.UpdatingError, expected_mesg)

    @patch(TASK_LOGGER)
    @patch.object(CourseImportManager, 'import_children')
    @ddt.data(
        Exception("foo exbar"),
        KeyError("foo kebar"),
        ValueError("foo vebar"),
    )
    def test_import_failure_for_unknown_failures(self, exception, mocked_import, mocked_log):
        """
        Test that import status and logged exception when course import fails with an unknown failure.
        """
        for good_file in self.good_archives.values():
            mocked_import.side_effect = exception
            expected_exc_mesg = f"{self.log_prefix} Error while importing course: {str(exception)}"
            response = self.import_file_in_course(good_file)
            self.assertEqual(response.status_code, 200)  # noqa: PT009
            mocked_log.exception.assert_called_once_with(expected_exc_mesg)
            mocked_log.reset_mock()

            status_response = self.get_import_status(self.course.id, good_file)
            self.assertImportStatusResponse(status_response, self.UpdatingError, import_error.UNKNOWN_ERROR_IN_IMPORT)

    @ddt.data('zip', 'tar')
    def test_import_status_response_is_not_cached(self, fmt):
        """To test import_status endpoint response is not cached"""
        good_file = self.good_archives[fmt]
        resp = self.client.get(
            reverse_course_url(
                'import_status_handler',
                self.course.id,
                kwargs={'filename': os.path.split(good_file)[1]}
            )
        )
        self.assertEqual(resp.headers['Cache-Control'], 'no-cache, no-store, must-revalidate')  # noqa: PT009


@override_settings(CONTENTSTORE=TEST_DATA_CONTENTSTORE)
@ddt.ddt
class ExportTestCase(CourseTestCase):
    """
    Tests for export_handler.
    """

    def setUp(self):
        """
        Sets up the test course.
        """
        super().setUp()
        self.url = reverse_course_url('export_handler', self.course.id)
        self.status_url = reverse_course_url('export_status_handler', self.course.id)

    @override_waffle_flag(toggles.LEGACY_STUDIO_EXPORT, True)
    def test_export_html(self):
        """
        Get the HTML for the page.
        """
        resp = self.client.get_html(self.url)
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        self.assertContains(resp, "Export My Course Content")

    def test_export_json_unsupported(self):
        """
        JSON is unsupported.
        """
        resp = self.client.get(self.url, HTTP_ACCEPT='application/json')
        self.assertEqual(resp.status_code, 406)  # noqa: PT009

    def test_export_async(self):
        """
        Get tar.gz file, using asynchronous background task

        Return a TarFile of the successful export.
        """
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        resp = self.client.get(self.status_url)
        result = json.loads(resp.content.decode('utf-8'))
        res_status = result['ExportStatus']
        self.assertEqual(res_status, 3)  # noqa: PT009
        self.assertIn('ExportOutput', result)  # noqa: PT009
        output_url = result['ExportOutput']
        resp = self.client.get(output_url)
        self._verify_export_succeeded(resp)
        resp_content = b''
        for item in resp.streaming_content:
            resp_content += item

        buff = BytesIO(resp_content)
        return tarfile.open(fileobj=buff)  # pylint: disable=consider-using-with

    def _verify_export_succeeded(self, resp):
        """ Export success helper method. """
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        self.assertTrue(resp.get('Content-Disposition').startswith('attachment'))  # noqa: PT009

    def test_unknown_xblock_top_level(self):
        """
        Export unknown XBlock type (i.e. we uninstalled the XBlock), top level.
        """
        fake_xblock = BlockFactory.create(
            parent_location=self.course.location,
            category='not_a_real_block_type'
        )
        self.store.publish(fake_xblock.location, self.user.id)

        # Now check the resulting export
        tar_ball = self.test_export_async()
        course_file_path = next(
            path for path in tar_ball.getnames()
            if re.match(r'\w+/course/\w+.xml', path)
        )
        course_file = tar_ball.extractfile(course_file_path)
        course_xml = lxml.etree.parse(course_file)
        course_elem = course_xml.getroot()

        # The course run file still has a child pointer to the unknown type and
        # creates the <not_a_real_block_type url="..."> pointer tag...
        self.assertEqual(course_elem.tag, 'course')  # noqa: PT009
        unknown_elem = course_elem[0]
        self.assertEqual(unknown_elem.tag, 'not_a_real_block_type')  # noqa: PT009
        # Non empty url_name attribute (the generated ID)
        self.assertTrue(unknown_elem.attrib['url_name'])  # noqa: PT009

        # But there should be no file exported for our fake block type. Without
        # the XBlock installed, we don't know how to serialize it properly.
        assert not any(
            '/not_a_real_block_type/' in path
            for path in tar_ball.getnames()
        )

    def test_unknown_xblock_subsection_level(self):
        """
        Export unknown XBlock type deeper in the course.
        """
        vertical = BlockFactory.create(
            parent_location=self.course.location,
            category='vertical',
            display_name='sample_vertical',
        )
        fake_xblock = BlockFactory.create(
            parent_location=vertical.location,
            category='not_a_real_block_type',
        )
        self.store.publish(fake_xblock.location, self.user.id)

        # Now check the resulting export
        tar_ball = self.test_export_async()
        course_file_path = next(
            path for path in tar_ball.getnames()
            if re.match(r'\w+/course/\w+.xml', path)
        )
        course_file = tar_ball.extractfile(course_file_path)
        course_xml = lxml.etree.parse(course_file)
        course_elem = course_xml.getroot()

        # The course run file should have a vertical that points to the
        # non-existant block.
        self.assertEqual(course_elem.tag, 'course')  # noqa: PT009
        self.assertEqual(course_elem[0].tag, 'vertical')  # This is just a reference  # noqa: PT009

        vert_file_path = next(
            path for path in tar_ball.getnames()
            if re.match(r'\w+/vertical/\w+.xml', path)
        )
        vert_file = tar_ball.extractfile(vert_file_path)
        vert_xml = lxml.etree.parse(vert_file)
        vert_elem = vert_xml.getroot()
        self.assertEqual(vert_elem.tag, 'vertical')  # noqa: PT009
        self.assertEqual(len(vert_elem), 1)  # noqa: PT009
        unknown_elem = vert_elem[0]
        self.assertEqual(unknown_elem.tag, 'not_a_real_block_type')  # noqa: PT009
        # Non empty url_name attribute (the generated ID)
        self.assertTrue(unknown_elem.attrib['url_name'])  # noqa: PT009

        # There should be no file exported for our fake block type
        assert not any(
            '/not_a_real_block_type/' in path
            for path in tar_ball.getnames()
        )

    def _verify_export_failure(self, expected_text):
        """ Export failure helper method. """
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        resp = self.client.get(self.status_url)
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        result = json.loads(resp.content.decode('utf-8'))
        self.assertNotIn('ExportOutput', result)  # noqa: PT009
        self.assertIn('ExportError', result)  # noqa: PT009
        error = result['ExportError']
        self.assertIn('Unable to create xml for block', error['raw_error_msg'])  # noqa: PT009
        self.assertIn(expected_text, error['edit_unit_url'])  # noqa: PT009

    def test_library_export(self):
        """
        Verify that useable library data can be exported.
        """
        youtube_id = "qS4NO9MNC6w"
        library = LibraryFactory.create(modulestore=self.store)
        video_block = BlockFactory.create(
            category="video",
            parent_location=library.location,
            user_id=self.user.id,
            publish_item=False,
            youtube_id_1_0=youtube_id
        )
        name = library.url_name
        lib_key = library.location.library_key
        root_dir = path(tempfile.mkdtemp())
        try:
            export_library_to_xml(self.store, contentstore(), lib_key, root_dir, name)
            with open(root_dir / name / LIBRARY_ROOT) as xml_root:
                lib_xml = lxml.etree.XML(xml_root.read())
                self.assertEqual(lib_xml.get('org'), lib_key.org)  # noqa: PT009
                self.assertEqual(lib_xml.get('library'), lib_key.library)  # noqa: PT009
                block = lib_xml.find('video')
                self.assertIsNotNone(block)  # noqa: PT009
                self.assertEqual(block.get('url_name'), video_block.url_name)  # noqa: PT009
            with open(root_dir / name / 'video' / video_block.url_name + '.xml') as xml_block:
                video_xml = lxml.etree.XML(xml_block.read())
                self.assertEqual(video_xml.tag, 'video')  # noqa: PT009
                self.assertEqual(video_xml.get('youtube_id_1_0'), youtube_id)  # noqa: PT009
        finally:
            shutil.rmtree(root_dir / name)

    def test_export_success_with_custom_tag(self):
        """
        Verify that course export with customtag
        """
        xml_string = '<impl>slides</impl>'
        vertical = BlockFactory.create(
            parent_location=self.course.location, category='vertical', display_name='foo'
        )
        BlockFactory.create(
            parent_location=vertical.location,
            category='customtag',
            display_name='custom_tag_foo',
            data=xml_string
        )

        self.test_export_async()

    @ddt.data(
        '/export/non.1/existence_1/Run_1',  # For mongo
        '/export/course-v1:non1+existence1+Run1',  # For split
    )
    def test_export_course_does_not_exist(self, url):
        """
        Export failure if course does not exist
        """
        resp = self.client.get_html(url)
        self.assertEqual(resp.status_code, 404)  # noqa: PT009

    def test_non_course_author(self):
        """
        Verify that users who aren't authors of the course are unable to export it
        """
        client, _ = self.create_non_staff_authed_user_client()
        resp = client.get(self.url)
        self.assertEqual(resp.status_code, 403)  # noqa: PT009

    def test_status_non_course_author(self):
        """
        Verify that users who aren't authors of the course are unable to see the status of export tasks
        """
        client, _ = self.create_non_staff_authed_user_client()
        resp = client.get(self.status_url)
        self.assertEqual(resp.status_code, 403)  # noqa: PT009

    def test_status_missing_record(self):
        """
        Attempting to get the status of an export task which isn't currently
        represented in the database should yield a useful result
        """
        resp = self.client.get(self.status_url)
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        result = json.loads(resp.content.decode('utf-8'))
        self.assertEqual(result['ExportStatus'], 0)  # noqa: PT009

    def test_output_non_course_author(self):
        """
        Verify that users who aren't authors of the course are unable to see the output of export tasks
        """
        client, _ = self.create_non_staff_authed_user_client()
        resp = client.get(reverse_course_url('export_output_handler', self.course.id))
        self.assertEqual(resp.status_code, 403)  # noqa: PT009

    def _mock_artifact(self, spec=None, file_url=None):
        """
        Creates a Mock of the UserTaskArtifact model for testing exports handler
        code without touching the database.
        """
        mock_artifact = Mock()
        mock_artifact.file.name = 'testfile.tar.gz'
        mock_artifact.file.storage = Mock(spec=spec)
        mock_artifact.file.storage.url.return_value = file_url
        return mock_artifact

    @patch('cms.djangoapps.contentstore.views.import_export._latest_task_status')
    @patch('user_tasks.models.UserTaskArtifact.objects.get')
    def test_export_status_handler_other(
        self,
        mock_get_user_task_artifact,
        mock_latest_task_status,
    ):
        """
        Verify that the export status handler generates the correct export path
        for storage providers other than ``FileSystemStorage`` and
        ``S3Boto3Storage``
        """
        mock_latest_task_status.return_value = Mock(state=UserTaskStatus.SUCCEEDED)
        mock_get_user_task_artifact.return_value = self._mock_artifact(
            file_url='/path/to/testfile.tar.gz',
        )
        resp = self.client.get(self.status_url)
        result = json.loads(resp.content.decode('utf-8'))
        self.assertEqual(result['ExportOutput'], '/path/to/testfile.tar.gz')  # noqa: PT009

    @ddt.data(S3Boto3Storage)
    @patch('cms.djangoapps.contentstore.views.import_export._latest_task_status')
    @patch('user_tasks.models.UserTaskArtifact.objects.get')
    def test_export_status_handler_s3(
        self,
        s3_storage,
        mock_get_user_task_artifact,
        mock_latest_task_status,
    ):
        """
        Verify that the export status handler generates the correct export path
        for the ``S3Boto3Storage`` storage provider
        """
        mock_latest_task_status.return_value = Mock(state=UserTaskStatus.SUCCEEDED)
        mock_get_user_task_artifact.return_value = self._mock_artifact(
            spec=s3_storage,
            file_url='/s3/file/path/testfile.tar.gz',
        )
        resp = self.client.get(self.status_url)
        result = json.loads(resp.content.decode('utf-8'))
        self.assertEqual(result['ExportOutput'], '/s3/file/path/testfile.tar.gz')  # noqa: PT009

    @patch('cms.djangoapps.contentstore.views.import_export._latest_task_status')
    @patch('user_tasks.models.UserTaskArtifact.objects.get')
    def test_export_status_handler_filesystem(
        self,
        mock_get_user_task_artifact,
        mock_latest_task_status,
    ):
        """
        Verify that the export status handler generates the correct export path
        for the ``FileSystemStorage`` storage provider
        """
        mock_latest_task_status.return_value = Mock(state=UserTaskStatus.SUCCEEDED)
        mock_get_user_task_artifact.return_value = self._mock_artifact(spec=FileSystemStorage)
        resp = self.client.get(self.status_url)
        result = json.loads(resp.content.decode('utf-8'))
        file_export_output_url = reverse_course_url('export_output_handler', self.course.id)
        self.assertEqual(result['ExportOutput'], file_export_output_url)  # noqa: PT009


@override_settings(CONTENTSTORE=TEST_DATA_CONTENTSTORE)
class TestLibraryImportExport(CourseTestCase):
    """
    Tests for importing content libraries from XML and exporting them to XML.
    """

    def setUp(self):
        super().setUp()
        self.export_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.export_dir, ignore_errors=True)

    def test_content_library_export_import(self):
        library1 = LibraryFactory.create(modulestore=self.store)
        source_library1_key = library1.location.library_key
        library2 = LibraryFactory.create(modulestore=self.store)
        source_library2_key = library2.location.library_key

        import_library_from_xml(
            self.store,
            'test_user',
            TEST_DATA_DIR,
            ['library_empty_problem'],
            static_content_store=contentstore(),
            target_id=source_library1_key,
            load_error_blocks=False,
            raise_on_failure=True,
            create_if_not_present=True,
        )

        export_library_to_xml(
            self.store,
            contentstore(),
            source_library1_key,
            self.export_dir,
            'exported_source_library',
        )

        source_library = self.store.get_library(source_library1_key)
        self.assertEqual(source_library.url_name, 'library')  # noqa: PT009

        # Import the exported library into a different content library.
        import_library_from_xml(
            self.store,
            'test_user',
            self.export_dir,
            ['exported_source_library'],
            static_content_store=contentstore(),
            target_id=source_library2_key,
            load_error_blocks=False,
            raise_on_failure=True,
            create_if_not_present=True,
        )

        # Compare the two content libraries for equality.
        self.assertCoursesEqual(source_library1_key, source_library2_key)


@ddt.ddt
@override_settings(CONTENTSTORE=TEST_DATA_CONTENTSTORE)
class TestCourseExportImport(LibraryTestCase):
    """
    Tests for importing after exporting the course containing content libraries from XML.
    """

    def setUp(self):
        super().setUp()
        self.export_dir = tempfile.mkdtemp()

        # Create a problem in library
        BlockFactory.create(
            category="problem",
            parent_location=self.library.location,
            user_id=self.user.id,
            publish_item=False,
            display_name='Test Problem',
            data="<problem><multiplechoiceresponse></multiplechoiceresponse></problem>",
        )

        # Create a source course.
        self.source_course = CourseFactory.create(default_store=ModuleStoreEnum.Type.split)
        self.addCleanup(shutil.rmtree, self.export_dir, ignore_errors=True)

    def _setup_source_course_with_library_content(self, publish=False, version=None):
        """
        Sets up course with library content.
        """
        chapter = BlockFactory.create(
            parent_location=self.source_course.location,
            category='chapter',
            display_name='Test Section'
        )
        sequential = BlockFactory.create(
            parent_location=chapter.location,
            category='sequential',
            display_name='Test Sequential'
        )
        vertical = BlockFactory.create(
            category='vertical',
            parent_location=sequential.location,
            display_name='Test Unit'
        )
        lc_block = self._add_library_content_block(
            vertical, self.lib_key, publish_item=publish, other_settings=dict(source_library_version=version)
        )
        lc_block.sync_from_library(upgrade_to_latest=True)

    def get_lib_content_block_children(self, block_location):
        """
        Search for library content block to return its immediate children
        """
        if block_location.block_type == 'library_content':
            return self.store.get_item(block_location).children

        return self.get_lib_content_block_children(self.store.get_item(block_location).children[0])

    def assert_problem_display_names(self, source_course_location, dest_course_location, is_published):
        """
        Asserts that problems' display names in both source and destination courses are same.
        """
        source_course_lib_children = self.get_lib_content_block_children(source_course_location)
        dest_course_lib_children = self.get_lib_content_block_children(dest_course_location)

        self.assertEqual(len(source_course_lib_children), len(dest_course_lib_children))  # noqa: PT009

        for source_child_location, dest_child_location in zip(source_course_lib_children, dest_course_lib_children):  # noqa: B905  # pylint: disable=line-too-long
            # Assert problem names on draft branch.
            with self.store.branch_setting(branch_setting=ModuleStoreEnum.Branch.draft_preferred):
                self.assert_names(source_child_location, dest_child_location)

            if is_published:
                # Assert problem names on publish branch.
                with self.store.branch_setting(branch_setting=ModuleStoreEnum.Branch.published_only):
                    self.assert_names(source_child_location, dest_child_location)

    def assert_names(self, source_child_location, dest_child_location):
        """
        Check if blocks have same display_name.
        """
        source_child = self.store.get_item(source_child_location)
        dest_child = self.store.get_item(dest_child_location)
        self.assertEqual(source_child.display_name, dest_child.display_name)  # noqa: PT009

    @ddt.data(*itertools.product([False, True], repeat=2))
    @ddt.unpack
    def test_library_content_on_course_export_import(self, publish_item, generate_version):
        """
        Verify that library contents in destination and source courses are same after importing
        the source course into destination course.

        If a library with the specified version does not exist in the modulestore, the import should not fail.
        """
        self._setup_source_course_with_library_content(
            publish=publish_item, version=str(ObjectId()) if generate_version else None
        )

        # Create a course to import source course.
        dest_course = CourseFactory.create(default_store=ModuleStoreEnum.Type.split)

        # Export the source course.
        export_course_to_xml(
            self.store,
            contentstore(),
            self.source_course.location.course_key,
            self.export_dir,
            'exported_source_course',
        )

        # Now, import it back to dest_course.
        import_course_from_xml(
            self.store,
            self.user.id,
            self.export_dir,
            ['exported_source_course'],
            static_content_store=contentstore(),
            target_id=dest_course.location.course_key,
            load_error_blocks=False,
            raise_on_failure=True,
            create_if_not_present=True,
        )

        self.assert_problem_display_names(
            self.source_course.location,
            dest_course.location,
            publish_item
        )


@ddt.ddt
@override_settings(CONTENTSTORE=TEST_DATA_CONTENTSTORE)
class TestCourseExportImportProblem(CourseTestCase):
    """
    Tests for importing after exporting the course containing problem with pre tags from XML.
    """

    def setUp(self):
        super().setUp()
        self.export_dir = tempfile.mkdtemp()
        self.source_course = CourseFactory.create(default_store=ModuleStoreEnum.Type.split)
        self.addCleanup(shutil.rmtree, self.export_dir, ignore_errors=True)

    def _setup_source_course_with_problem_content(self, data, publish_item=False):
        """
        Sets up course with problem content.
        """
        chapter = BlockFactory.create(
            parent_location=self.source_course.location,
            category='chapter',
            display_name='Test Section'
        )
        sequential = BlockFactory.create(
            parent_location=chapter.location,
            category='sequential',
            display_name='Test Sequential'
        )
        vertical = BlockFactory.create(
            category='vertical',
            parent_location=sequential.location,
            display_name='Test Unit'
        )

        BlockFactory.create(
            parent=vertical,
            category='problem',
            display_name='Test Problem',
            publish_item=publish_item,
            data=data,
        )

    def get_problem_content(self, block_location):
        """
        Get problem content of course.
        """
        if block_location.block_type == 'problem':
            return self.store.get_item(block_location).data

        return self.get_problem_content(self.store.get_item(block_location).children[0])

    def assert_problem_definition(self, course_location, expected_problem_content):
        """
        Asserts that problems' data is as expected with pre-tag content maintained.
        """
        problem_content = self.get_problem_content(course_location)
        self.assertEqual(expected_problem_content, problem_content)  # noqa: PT009

    @ddt.data(
        [
            '<problem><pre><code>x=10 print("hello \n")</code></pre>'
            '<pre><div><pre><code>x=10 print("hello \n")</code></pre></div></pre>'
            '<multiplechoiceresponse></multiplechoiceresponse></problem>',

            '<problem>\n  <pre>\n    <code>x=10 print("hello \n")</code>\n  </pre>\n  <pre>\n    <div>\n      <pre>\n '
            '       <code>x=10 print("hello \n")</code>\n      </pre>\n    </div>\n  </pre>\n  '
            '<multiplechoiceresponse/>\n</problem>\n'
        ],
        [
            '<problem><pre><code>x=10 print("hello \n")</code></pre>'
            '<multiplechoiceresponse></multiplechoiceresponse></problem>',

            '<problem>\n  <pre>\n    <code>x=10 print("hello \n")</code>\n  </pre>\n  '
            '<multiplechoiceresponse/>\n</problem>\n'
        ],
        [
            '<!-- Comment outside of the root (will be deleted). -->'
            '<problem>'
            '<!-- Valid comment -->'
            '<p>'
            '"<!-- String with non-XML structure: >< -->"'
            'Text'
            '</p>'
            '</problem>',

            '<problem>\n  '
            '<!-- Valid comment -->\n  '
            '<p>'
            '"<!-- String with non-XML structure: >< -->"'
            'Text'
            '</p>\n'
            '</problem>\n'
        ],
    )
    @ddt.unpack
    def test_problem_content_on_course_export_import(self, problem_data, expected_problem_content):
        """
        Verify that problem content in destination matches expected problem content,
        specifically concerned with pre tag data with problem.
        """
        self._setup_source_course_with_problem_content(problem_data)

        dest_course = CourseFactory.create(default_store=ModuleStoreEnum.Type.split)

        export_course_to_xml(
            self.store,
            contentstore(),
            self.source_course.location.course_key,
            self.export_dir,
            'exported_source_course',
        )

        import_course_from_xml(
            self.store,
            self.user.id,
            self.export_dir,
            ['exported_source_course'],
            static_content_store=contentstore(),
            target_id=dest_course.location.course_key,
            load_error_blocks=False,
            raise_on_failure=True,
            create_if_not_present=True,
        )

        self.assert_problem_definition(dest_course.location, expected_problem_content)


class ImportAuthzTest(CourseAuthzTestMixin, BaseCourseViewTest):
    """
    Tests Course Import Course authorization using openedx-authz.
    """

    view_name = 'import_handler'
    course_key_arg_name = 'course_key_string'
    authz_roles_to_assign = [COURSE_STAFF.external_key]


    def setUp(self):
        super().setUp()

        self.content_dir = path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.content_dir)

        # Create tar test files -----------------------------------------------
        # OK course:
        good_dir = tempfile.mkdtemp(dir=self.content_dir)
        # test course being deeper down than top of tar file
        embedded_dir = os.path.join(good_dir, "grandparent", "parent")
        os.makedirs(os.path.join(embedded_dir, "course"))
        with open(os.path.join(embedded_dir, "course.xml"), "w+") as f:
            f.write('<course url_name="2013_Spring" org="EDx" course="0.00x"/>')

        with open(os.path.join(embedded_dir, "course", "2013_Spring.xml"), "w+") as f:
            f.write('<course></course>')

        self.file_to_upload = os.path.join(self.content_dir, "good.tar.gz")
        with tarfile.open(self.file_to_upload, "w:gz") as gtar:
            gtar.add(good_dir)

    def import_file_in_course(self, client, course_key: str = None):
        """Helper method to import provided file in the course."""
        with open(self.file_to_upload, 'rb') as file_data:
            args = {"name": self.file_to_upload, "course-data": [file_data]}
            course_key = course_key or str(self.course_key)
            url = self.get_url(course_key)
            return client.post(url, args)

    def test_authorized_user_can_access(self):
        """User with COURSE_STAFF role can access."""
        self.authorized_client.login(username=self.authorized_user.username, password=self.password)
        resp = self.import_file_in_course(self.authorized_client)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)  # noqa: PT009

    def test_unauthorized_user_cannot_access(self):
        """User without role cannot access."""
        self.unauthorized_client.login(username=self.unauthorized_user.username, password=self.password)
        resp = self.import_file_in_course(self.unauthorized_client)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

    def test_role_scoped_to_course(self):
        """Authorization should only apply to the assigned course."""
        other_course = self.store.create_course("OtherOrg", "OtherCourse", "Run", self.staff.id)
        self.authorized_client.login(username=self.authorized_user.username, password=self.password)
        resp = self.import_file_in_course(self.authorized_client, other_course.id)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

    def test_staff_user_allowed_via_legacy(self):
        """
        Staff users should still pass through legacy fallback.
        """
        self.client.login(username=self.staff.username, password=self.password)
        resp = self.import_file_in_course(self.client)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)  # noqa: PT009

    def test_superuser_allowed(self):
        """Superusers should always be allowed."""
        superuser = UserFactory(is_superuser=True, username='superuser', password=self.password)

        client = APIClient()
        client.login(username=superuser.username, password=self.password)

        resp = self.import_file_in_course(client)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)  # noqa: PT009

    def test_non_staff_user_cannot_access(self):
        """
        User without permissions should be denied.
        This case validates that a non-staff user cannot access even
        if they have course author access to the course.
        """
        non_staff_user = UserFactory(password=self.password)
        non_staff_client = APIClient()
        self.add_user_to_role(non_staff_user, COURSE_DATA_RESEARCHER.external_key)
        non_staff_client.login(username=non_staff_user.username, password=self.password)

        resp = non_staff_client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009


class ImportStatusAuthzTest(CourseAuthzTestMixin, BaseCourseViewTest):
    """
    Tests Course Import Course Staus authorization using openedx-authz.
    """

    view_name = 'import_status_handler'
    course_key_arg_name = 'course_key_string'
    extra_request_args = {'filename': 'test.xml'}
    authz_roles_to_assign = [COURSE_STAFF.external_key]

    def test_authorized_user_can_access(self):
        """User with COURSE_STAFF role can access."""
        self.authorized_client.login(username=self.authorized_user.username, password=self.password)
        resp = self.authorized_client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)  # noqa: PT009

    def test_unauthorized_user_cannot_access(self):
        """User without role cannot access."""
        self.unauthorized_client.login(username=self.unauthorized_user.username, password=self.password)
        resp = self.unauthorized_client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

    def test_role_scoped_to_course(self):
        """Authorization should only apply to the assigned course."""
        other_course = self.store.create_course("OtherOrg", "OtherCourse", "Run", self.staff.id)

        self.authorized_client.login(username=self.authorized_user.username, password=self.password)
        resp = self.authorized_client.get(self.get_url(other_course.id))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

    def test_staff_user_allowed_via_legacy(self):
        """
        Staff users should still pass through legacy fallback.
        """
        self.client.login(username=self.staff.username, password=self.password)

        resp = self.client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)  # noqa: PT009

    def test_superuser_allowed(self):
        """Superusers should always be allowed."""
        superuser = UserFactory(is_superuser=True, username='superuser', password=self.password)

        client = APIClient()
        client.login(username=superuser.username, password=self.password)

        resp = client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)  # noqa: PT009

    def test_non_staff_user_cannot_access(self):
        """
        User without permissions should be denied.
        This case validates that a non-staff user cannot access even
        if they have course author access to the course.
        """
        non_staff_user = UserFactory(password=self.password)
        non_staff_client = APIClient()
        self.add_user_to_role(non_staff_user, COURSE_DATA_RESEARCHER.external_key)
        non_staff_client.login(username=non_staff_user.username, password=self.password)

        resp = non_staff_client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009


class ExportStatusAuthzTest(CourseAuthzTestMixin, BaseCourseViewTest):
    """
    Tests Course Export Course Status authorization using openedx-authz.
    """

    view_name = 'export_status_handler'
    course_key_arg_name = 'course_key_string'
    authz_roles_to_assign = [COURSE_STAFF.external_key]

    def test_authorized_user_can_access(self):
        """User with COURSE_STAFF role can access."""
        self.authorized_client.login(username=self.authorized_user.username, password=self.password)
        resp = self.authorized_client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)  # noqa: PT009

    def test_unauthorized_user_cannot_access(self):
        """User without role cannot access."""
        self.unauthorized_client.login(username=self.unauthorized_user.username, password=self.password)
        resp = self.unauthorized_client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

    def test_role_scoped_to_course(self):
        """Authorization should only apply to the assigned course."""
        other_course = self.store.create_course("OtherOrg", "OtherCourse", "Run", self.staff.id)

        self.authorized_client.login(username=self.authorized_user.username, password=self.password)
        resp = self.authorized_client.get(self.get_url(other_course.id))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

    def test_staff_user_allowed_via_legacy(self):
        """
        Staff users should still pass through legacy fallback.
        """
        self.client.login(username=self.staff.username, password=self.password)

        resp = self.client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)  # noqa: PT009

    def test_superuser_allowed(self):
        """Superusers should always be allowed."""
        superuser = UserFactory(is_superuser=True, username='superuser', password=self.password)

        client = APIClient()
        client.login(username=superuser.username, password=self.password)

        resp = client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)  # noqa: PT009

    def test_non_staff_user_cannot_access(self):
        """
        User without permissions should be denied.
        This case validates that a non-staff user cannot access even
        if they have course author access to the course.
        """
        non_staff_user = UserFactory(password=self.password)
        non_staff_client = APIClient()
        self.add_user_to_role(non_staff_user, COURSE_DATA_RESEARCHER.external_key)
        non_staff_client.login(username=non_staff_user.username, password=self.password)

        resp = non_staff_client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009


class ExportAuthzTest(CourseAuthzTestMixin, BaseCourseViewTest):
    """
    Tests Course Export Course authorization using openedx-authz.
    """

    view_name = 'export_handler'
    course_key_arg_name = 'course_key_string'
    authz_roles_to_assign = [COURSE_STAFF.external_key]

    def test_authorized_user_can_access(self):
        """User with COURSE_STAFF role can access."""
        self.authorized_client.login(username=self.authorized_user.username, password=self.password)
        resp = self.authorized_client.post(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)  # noqa: PT009

    def test_unauthorized_user_cannot_access(self):
        """User without role cannot access."""
        self.unauthorized_client.login(username=self.unauthorized_user.username, password=self.password)
        resp = self.unauthorized_client.post(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

    def test_role_scoped_to_course(self):
        """Authorization should only apply to the assigned course."""
        other_course = self.store.create_course("OtherOrg", "OtherCourse", "Run", self.staff.id)

        self.authorized_client.login(username=self.authorized_user.username, password=self.password)
        resp = self.authorized_client.post(self.get_url(other_course.id))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

    def test_staff_user_allowed_via_legacy(self):
        """
        Staff users should still pass through legacy fallback.
        """
        self.client.login(username=self.staff.username, password=self.password)

        resp = self.client.post(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)  # noqa: PT009

    def test_superuser_allowed(self):
        """Superusers should always be allowed."""
        superuser = UserFactory(is_superuser=True, username='superuser', password=self.password)

        client = APIClient()
        client.login(username=superuser.username, password=self.password)

        resp = client.post(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)  # noqa: PT009

    def test_non_staff_user_cannot_access(self):
        """
        User without permissions should be denied.
        This case validates that a non-staff user cannot access even
        if they have course author access to the course.
        """
        non_staff_user = UserFactory(password=self.password)
        non_staff_client = APIClient()
        self.add_user_to_role(non_staff_user, COURSE_DATA_RESEARCHER.external_key)
        non_staff_client.login(username=non_staff_user.username, password=self.password)

        resp = non_staff_client.post(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009


class ExportOutputAuthzTest(CourseAuthzTestMixin, BaseCourseViewTest):
    """
    Tests Course Export Course Output authorization using openedx-authz.
    """

    view_name = 'export_output_handler'
    course_key_arg_name = 'course_key_string'
    authz_roles_to_assign = [COURSE_STAFF.external_key]

    def _mock_artifact(self, spec=None, file_url=None):
        """
        Creates a Mock of the UserTaskArtifact model for testing exports handler
        code without touching the database.
        """
        mock_artifact = Mock()
        mock_artifact.file.name = 'testfile.tar.gz'
        mock_artifact.file.storage = Mock(spec=spec)
        mock_artifact.file.storage.url.return_value = file_url
        return mock_artifact

    def test_authorized_user_can_access(self):
        """User with COURSE_STAFF role can access."""
        self.authorized_client.login(username=self.authorized_user.username, password=self.password)
        self.authorized_client.post(reverse_course_url('export_handler', self.course_key))
        resp = self.authorized_client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)  # noqa: PT009

    def test_unauthorized_user_cannot_access(self):
        """User without role cannot access."""
        self.unauthorized_client.login(username=self.unauthorized_user.username, password=self.password)
        resp = self.unauthorized_client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

    def test_role_scoped_to_course(self):
        """Authorization should only apply to the assigned course."""
        other_course = self.store.create_course("OtherOrg", "OtherCourse", "Run", self.staff.id)
        self.authorized_client.login(username=self.authorized_user.username, password=self.password)
        resp = self.authorized_client.get(self.get_url(other_course.id))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

    def test_staff_user_allowed_via_legacy(self):
        """
        Staff users should still pass through legacy fallback.
        """
        self.client.login(username=self.staff.username, password=self.password)
        self.client.post(reverse_course_url('export_handler', self.course_key))
        resp = self.client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)  # noqa: PT009

    @patch('user_tasks.models.UserTaskArtifact.objects.get')
    @patch('cms.djangoapps.contentstore.views.import_export._latest_task_status')
    @patch('cms.djangoapps.contentstore.views.import_export.course_import_export_storage')
    def test_superuser_allowed(
        self,
        mock_storage,
        mock_latest_task_status,
        mock_get_user_task_artifact,
    ):
        """Superusers should always be allowed."""
        mock_latest_task_status.return_value = Mock(state=UserTaskStatus.SUCCEEDED)
        mock_get_user_task_artifact.return_value = self._mock_artifact(
            file_url='/path/to/testfile.tar.gz',
        )
        mock_tarball = Mock()
        mock_tarball.name = 'testfile.tar.gz'
        mock_storage.open.return_value = mock_tarball
        mock_storage.size.return_value = 0

        superuser = UserFactory(is_superuser=True, username='superuser', password=self.password)

        client = APIClient()
        client.login(username=superuser.username, password=self.password)
        resp = client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)  # noqa: PT009

    def test_non_staff_user_cannot_access(self):
        """
        User without permissions should be denied.
        This case validates that a non-staff user cannot access even
        if they have course author access to the course.
        """
        non_staff_user = UserFactory(password=self.password)
        non_staff_client = APIClient()
        self.add_user_to_role(non_staff_user, COURSE_DATA_RESEARCHER.external_key)
        non_staff_client.login(username=non_staff_user.username, password=self.password)

        resp = non_staff_client.get(self.get_url(self.course_key))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009
