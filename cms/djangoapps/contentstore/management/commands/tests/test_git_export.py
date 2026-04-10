"""
Unittests for exporting to git via management command.
"""


import copy
import os
import shutil
import subprocess
import unittest
from io import StringIO
from uuid import uuid4

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test.utils import override_settings
from opaque_keys.edx.locator import CourseLocator

import cms.djangoapps.contentstore.git_export_utils as git_export_utils
from cms.djangoapps.contentstore.git_export_utils import GitExportError
from cms.djangoapps.contentstore.tests.utils import CourseTestCase

FEATURES_WITH_EXPORT_GIT = settings.FEATURES.copy()
FEATURES_WITH_EXPORT_GIT['ENABLE_EXPORT_GIT'] = True
TEST_DATA_CONTENTSTORE = copy.deepcopy(settings.CONTENTSTORE)
TEST_DATA_CONTENTSTORE['DOC_STORE_CONFIG']['db'] = 'test_xcontent_%s' % uuid4().hex  # noqa: UP031


@override_settings(CONTENTSTORE=TEST_DATA_CONTENTSTORE)
@override_settings(FEATURES=FEATURES_WITH_EXPORT_GIT)
class TestGitExport(CourseTestCase):
    """
    Excercise the git_export django management command with various inputs.
    """

    def setUp(self):
        """
        Create/reinitialize bare repo and folders needed
        """
        super().setUp()

        if not os.path.isdir(git_export_utils.GIT_REPO_EXPORT_DIR):
            os.mkdir(git_export_utils.GIT_REPO_EXPORT_DIR)
            self.addCleanup(shutil.rmtree, git_export_utils.GIT_REPO_EXPORT_DIR)

        self.bare_repo_dir = '{}/data/test_bare.git'.format(  # noqa: UP032
            os.path.abspath(settings.TEST_ROOT))
        if not os.path.isdir(self.bare_repo_dir):
            os.mkdir(self.bare_repo_dir)
            self.addCleanup(shutil.rmtree, self.bare_repo_dir)
        subprocess.check_output(['git', '--bare', 'init'],
                                cwd=self.bare_repo_dir)

    def test_command(self):
        """
        Test that the command interface works. Ignore stderr for clean
        test output.
        """
        with self.assertRaisesRegex(CommandError, 'Error: unrecognized arguments:*'):  # noqa: PT027
            call_command('git_export', 'blah', 'blah', 'blah', stderr=StringIO())

        with self.assertRaisesMessage(
            CommandError,
            'Error: the following arguments are required: course_loc, git_url'
        ):
            call_command('git_export', stderr=StringIO())

        # Send bad url to get course not exported
        with self.assertRaisesRegex(CommandError, str(GitExportError.URL_BAD)):  # noqa: PT027
            call_command('git_export', 'foo/bar/baz', 'silly', stderr=StringIO())

        # Send bad course_id to get course not exported
        with self.assertRaisesRegex(CommandError, str(GitExportError.BAD_COURSE)):  # noqa: PT027
            call_command('git_export', 'foo/bar:baz', 'silly', stderr=StringIO())

    def test_error_output(self):
        """
        Verify that error output is actually resolved as the correct string
        """
        with self.assertRaisesRegex(CommandError, str(GitExportError.BAD_COURSE)):  # noqa: PT027
            call_command(
                'git_export', 'foo/bar:baz', 'silly'
            )

        with self.assertRaisesRegex(CommandError, str(GitExportError.URL_BAD)):  # noqa: PT027
            call_command(
                'git_export', 'foo/bar/baz', 'silly'
            )

    def test_bad_git_url(self):
        """
        Test several bad URLs for validation
        """
        course_key = CourseLocator('org', 'course', 'run')
        with self.assertRaisesRegex(GitExportError, str(GitExportError.URL_BAD)):  # noqa: PT027
            git_export_utils.export_to_git(course_key, 'Sillyness')

        with self.assertRaisesRegex(GitExportError, str(GitExportError.URL_BAD)):  # noqa: PT027
            git_export_utils.export_to_git(course_key, 'example.com:edx/notreal')

        with self.assertRaisesRegex(GitExportError, str(GitExportError.URL_NO_AUTH)):  # noqa: PT027
            git_export_utils.export_to_git(course_key, 'http://blah')

    def test_bad_git_repos(self):
        """
        Test invalid git repos
        """
        test_repo_path = f'{git_export_utils.GIT_REPO_EXPORT_DIR}/test_repo'
        self.assertFalse(os.path.isdir(test_repo_path))  # noqa: PT009
        course_key = CourseLocator('foo', 'blah', '100-')
        # Test bad clones
        with self.assertRaisesRegex(GitExportError, str(GitExportError.CANNOT_PULL)):  # noqa: PT027
            git_export_utils.export_to_git(
                course_key,
                'https://user:blah@example.com/test_repo.git')
        self.assertFalse(os.path.isdir(test_repo_path))  # noqa: PT009

        # Setup good repo with bad course to test xml export
        with self.assertRaisesRegex(GitExportError, str(GitExportError.XML_EXPORT_FAIL)):  # noqa: PT027
            git_export_utils.export_to_git(
                course_key,
                f'file://{self.bare_repo_dir}')

        # Test bad git remote after successful clone
        with self.assertRaisesRegex(GitExportError, str(GitExportError.CANNOT_PULL)):  # noqa: PT027
            git_export_utils.export_to_git(
                course_key,
                'https://user:blah@example.com/r.git')

    @unittest.skipIf(os.environ.get('GIT_CONFIG') or
                     os.environ.get('GIT_AUTHOR_EMAIL') or
                     os.environ.get('GIT_AUTHOR_NAME') or
                     os.environ.get('GIT_COMMITTER_EMAIL') or
                     os.environ.get('GIT_COMMITTER_NAME'),
                     'Global git override set')
    def test_git_ident(self):
        """
        Test valid course with and without user specified.

        Test skipped if git global config override environment variable GIT_CONFIG
        is set.
        """
        git_export_utils.export_to_git(
            self.course.id,
            f'file://{self.bare_repo_dir}',
            'enigma'
        )
        expect_string = '{}|{}\n'.format(
            git_export_utils.GIT_EXPORT_DEFAULT_IDENT['name'],
            git_export_utils.GIT_EXPORT_DEFAULT_IDENT['email']
        )
        cwd = os.path.abspath(git_export_utils.GIT_REPO_EXPORT_DIR / 'test_bare')
        git_log = subprocess.check_output(['git', 'log', '-1',
                                           '--format=%an|%ae'], cwd=cwd).decode('utf-8')
        self.assertEqual(expect_string, git_log)  # noqa: PT009

        # Make changes to course so there is something to commit
        self.populate_course()
        git_export_utils.export_to_git(
            self.course.id,
            f'file://{self.bare_repo_dir}',
            self.user.username
        )
        expect_string = '{}|{}\n'.format(  # noqa: UP032
            self.user.username,
            self.user.email,
        )
        git_log = subprocess.check_output(
            ['git', 'log', '-1', '--format=%an|%ae'], cwd=cwd).decode('utf-8')
        self.assertEqual(expect_string, git_log)  # noqa: PT009

    def test_no_change(self):
        """
        Test response if there are no changes
        """
        git_export_utils.export_to_git(
            self.course.id,
            f'file://{self.bare_repo_dir}'
        )

        with self.assertRaisesRegex(GitExportError, str(GitExportError.CANNOT_COMMIT)):  # noqa: PT027
            git_export_utils.export_to_git(
                self.course.id, f'file://{self.bare_repo_dir}')
