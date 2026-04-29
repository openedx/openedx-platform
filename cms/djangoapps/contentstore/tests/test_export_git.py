"""
Test the ability to export courses to xml from studio
"""


import copy
import os
import shutil
import subprocess
from uuid import uuid4

from django.conf import settings
from django.test.utils import override_settings

import cms.djangoapps.contentstore.git_export_utils as git_export_utils
from cms.djangoapps.contentstore.utils import reverse_course_url
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

from .utils import CourseTestCase

TEST_DATA_CONTENTSTORE = copy.deepcopy(settings.CONTENTSTORE)
TEST_DATA_CONTENTSTORE['DOC_STORE_CONFIG']['db'] = 'test_xcontent_%s' % uuid4().hex  # noqa: UP031


@override_settings(CONTENTSTORE=TEST_DATA_CONTENTSTORE)
class TestExportGit(CourseTestCase):
    """
    Tests pushing a course to a git repository
    """

    def setUp(self):
        """
        Setup test course, user, and url.
        """
        super().setUp()
        self.course_block = modulestore().get_course(self.course.id)
        self.test_url = reverse_course_url('export_git', self.course.id)

    def make_bare_repo_with_course(self, repo_name):
        """
        Make a local bare repo suitable for exporting to in
        tests
        """
        # Build out local bare repo, and set course git url to it
        repo_dir = os.path.abspath(git_export_utils.GIT_REPO_EXPORT_DIR)
        os.mkdir(repo_dir)
        self.addCleanup(shutil.rmtree, repo_dir)

        bare_repo_dir = '{}/{}.git'.format(  # noqa: UP032
            os.path.abspath(git_export_utils.GIT_REPO_EXPORT_DIR),
            repo_name
        )
        os.mkdir(bare_repo_dir)
        self.addCleanup(shutil.rmtree, bare_repo_dir)

        subprocess.check_output(['git', '--bare', 'init', ], cwd=bare_repo_dir)
        self.populate_course()
        self.course_block.giturl = f'file://{bare_repo_dir}'
        modulestore().update_item(self.course_block, self.user.id)

    def test_giturl_missing(self):
        """
        Test to make sure an appropriate error is displayed
        if course hasn't set giturl.
        """
        response = self.client.get(self.test_url)
        self.assertContains(
            response,
            ('giturl must be defined in your '
             'course settings before you can export to git.'),
        )

        response = self.client.get(f'{self.test_url}?action=push')
        self.assertContains(
            response,
            ('giturl must be defined in your '
             'course settings before you can export to git.'),
        )

    def test_course_export_failures(self):
        """
        Test failed course export response.
        """
        self.course_block.giturl = 'foobar'
        modulestore().update_item(self.course_block, self.user.id)

        response = self.client.get(f'{self.test_url}?action=push')
        self.assertContains(response, 'Export Failed:')

    def test_exception_translation(self):
        """
        Regression test for making sure errors are properly stringified
        """
        self.course_block.giturl = 'foobar'
        modulestore().update_item(self.course_block, self.user.id)

        response = self.client.get(f'{self.test_url}?action=push')
        self.assertNotContains(response, 'django.utils.functional.__proxy__')

    def test_course_export_success(self):
        """
        Test successful course export response.
        """

        self.make_bare_repo_with_course('test_repo')
        response = self.client.get(f'{self.test_url}?action=push')
        self.assertContains(response, 'Export Succeeded')

    def test_repo_with_dots(self):
        """
        Regression test for a bad directory pathing of repo's that have dots.
        """
        self.make_bare_repo_with_course('test.repo')
        response = self.client.get(f'{self.test_url}?action=push')
        self.assertContains(response, 'Export Succeeded')

    def test_dirty_repo(self):
        """
        Add additional items not in the repo and make sure they aren't
        there after the export. This allows old content to removed
        in the repo.
        """
        repo_name = 'dirty_repo1'
        self.make_bare_repo_with_course(repo_name)
        git_export_utils.export_to_git(self.course.id,
                                       self.course_block.giturl, self.user)

        # Make arbitrary change to course to make diff
        self.course_block.matlab_api_key = 'something'
        modulestore().update_item(self.course_block, self.user.id)
        # Touch a file in the directory, export again, and make sure
        # the test file is gone
        repo_dir = os.path.join(
            os.path.abspath(git_export_utils.GIT_REPO_EXPORT_DIR),
            repo_name
        )
        test_file = os.path.join(repo_dir, 'test.txt')
        open(test_file, 'a').close()
        self.assertTrue(os.path.isfile(test_file))  # noqa: PT009
        git_export_utils.export_to_git(self.course.id,
                                       self.course_block.giturl, self.user)
        self.assertFalse(os.path.isfile(test_file))  # noqa: PT009
