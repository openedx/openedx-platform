"""
Unittests for exporting to git via management command.
"""


import copy
import os
import shutil
import socket
import subprocess
import unittest
from io import StringIO
from unittest.mock import patch
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

    def test_rejects_git_option_injection_urls(self):
        """
        Regression test for CVE-2017-1000117-style attacks: a repo URL that
        begins with ``-`` must be rejected before reaching git, because git
        itself would otherwise interpret it as a command-line option
        (``--upload-pack=...``) regardless of subprocess list-form.

        These inputs are crafted to end in ``.git`` so they pass the legacy
        endswith-based validator and exercise the new ``_validate_git_url``
        path directly.
        """
        course_key = CourseLocator('org', 'course', 'run')
        for bad in (
            '-https://example.com/test.git',
            '--upload-pack=evil/x.git',
            '-u evil.example.com:22/x.git',
        ):
            with self.assertRaisesRegex(GitExportError, str(GitExportError.URL_BAD)):  # noqa: PT027
                git_export_utils.export_to_git(course_key, bad)

    def test_rejects_internal_ip_targets(self):
        """
        SSRF defense: http(s) URLs that resolve to loopback, link-local,
        private, reserved, or multicast addresses must be rejected.
        """
        course_key = CourseLocator('org', 'course', 'run')
        internal_cases = [
            ('https://user:pw@internal.example/test.git', '127.0.0.1'),
            ('https://user:pw@internal.example/test.git', '10.0.0.5'),
            ('https://user:pw@internal.example/test.git', '169.254.169.254'),
            ('https://user:pw@internal.example/test.git', '::1'),
            ('https://user:pw@internal.example/test.git', 'fe80::1'),
        ]
        for url, ip in internal_cases:
            family = socket.AF_INET6 if ':' in ip else socket.AF_INET
            addr_info = [(family, socket.SOCK_STREAM, 0, '', (ip, 0))]
            with patch(
                'cms.djangoapps.contentstore.git_export_utils.socket.getaddrinfo',
                return_value=addr_info,
            ):
                with self.assertRaisesRegex(  # noqa: PT027
                    GitExportError, str(GitExportError.URL_BAD)
                ):
                    git_export_utils.export_to_git(course_key, url)

    def test_validate_git_url_allows_public_https(self):
        """
        Public addresses must still be accepted by the URL validator.
        """
        addr_info = [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('140.82.112.4', 0))]
        with patch(
            'cms.djangoapps.contentstore.git_export_utils.socket.getaddrinfo',
            return_value=addr_info,
        ):
            # Should not raise; the validator is the only thing under test,
            # so call it directly rather than the full export pipeline.
            git_export_utils._validate_git_url(  # pylint: disable=protected-access
                'https://user:pw@github.com/openedx/test.git'
            )

    def test_git_clone_uses_double_dash_separator(self):
        """
        ``git clone`` is invoked with ``--`` before the repo argument so
        the repo is unambiguously positional.
        """
        course_key = CourseLocator('foo', 'blah', '100-')
        with patch(
            'cms.djangoapps.contentstore.git_export_utils.cmd_log'
        ) as mock_cmd_log:
            mock_cmd_log.side_effect = subprocess.CalledProcessError(
                returncode=1, cmd='git', output=b'stop'
            )
            with self.assertRaises(GitExportError):  # noqa: PT027
                git_export_utils.export_to_git(
                    course_key, f'file://{self.bare_repo_dir}_new_clone_path.git'
                )
            clone_calls = [
                call for call in mock_cmd_log.call_args_list
                if call.args and call.args[0][:2] == ['git', 'clone']
            ]
            assert clone_calls, 'expected at least one git clone invocation'
            clone_cmd = clone_calls[0].args[0]
            assert '--' in clone_cmd
            assert clone_cmd.index('--') < clone_cmd.index(
                f'file://{self.bare_repo_dir}_new_clone_path.git'
            )

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
