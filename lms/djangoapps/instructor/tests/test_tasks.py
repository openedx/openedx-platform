"""
Tests for tasks.py
"""
import json
from unittest import mock

from completion.waffle import ENABLE_COMPLETION_TRACKING_SWITCH
from edx_toggles.toggles.testutils import override_waffle_switch

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.courseware.models import StudentModule
from lms.djangoapps.instructor.tasks import update_exam_completion_task
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import (  # lint-amnesty, pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
)
from xmodule.partitions.partitions import Group, UserPartition  # lint-amnesty, pylint: disable=wrong-import-order


class UpdateCompletionTests(SharedModuleStoreTestCase):
    """
    Test the update_exam_completion_task
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.email = 'escalation@test.com'
        cls.course = CourseFactory.create(proctoring_escalation_email=cls.email)
        cls.section = BlockFactory.create(parent=cls.course, category='chapter')
        cls.subsection = BlockFactory.create(parent=cls.section, category='sequential')
        cls.unit = BlockFactory.create(parent=cls.subsection, category='vertical')
        cls.problem = BlockFactory.create(parent=cls.unit, category='problem')
        cls.unit_2 = BlockFactory.create(parent=cls.subsection, category='vertical')
        cls.problem_2 = BlockFactory.create(parent=cls.unit_2, category='problem')
        cls.complete_error_prefix = ('Error occurred while attempting to complete student attempt for '
                                     'user {user} for content_id {content_id}. ')

    def setUp(self):
        super().setUp()

        self.student = UserFactory()
        CourseEnrollment.enroll(self.student, self.course.id)

        self.module_to_reset = StudentModule.objects.create(
            student=self.student,
            course_id=self.course.id,
            module_state_key=self.problem.location,
            state=json.dumps({'attempts': 2}),
        )

    @mock.patch('completion.handlers.BlockCompletion.objects.submit_completion')
    def test_update_completion_success(self, mock_submit):
        """
        Assert correctly publishes completion for all
        completable children of the given content_id
        """
        # Section, subsection, and unit are all aggregators and not completable so should
        # not be submitted.
        section = BlockFactory.create(parent=self.course, category='chapter')
        subsection = BlockFactory.create(parent=section, category='sequential')
        unit = BlockFactory.create(parent=subsection, category='vertical')

        # should both be submitted
        video = BlockFactory.create(parent=unit, category='video')
        problem = BlockFactory.create(parent=unit, category='problem')

        # Not a completable block
        BlockFactory.create(parent=unit, category='discussion')

        with override_waffle_switch(ENABLE_COMPLETION_TRACKING_SWITCH, True):
            update_exam_completion_task(self.student.username, str(subsection.location), 1.0)

        # Only Completable leaf blocks should have completion published
        assert mock_submit.call_count == 2
        mock_submit.assert_any_call(user=self.student, block_key=video.location, completion=1.0)
        mock_submit.assert_any_call(user=self.student, block_key=problem.location, completion=1.0)

    @mock.patch('completion.handlers.BlockCompletion.objects.submit_completion')
    def test_update_completion_delete(self, mock_submit):
        """
        Test update completion with a value of 0.0
        """
        with override_waffle_switch(ENABLE_COMPLETION_TRACKING_SWITCH, True):
            update_exam_completion_task(self.student.username, str(self.subsection.location), 0.0)

        # Assert we send completion == 0.0 for both problems
        assert mock_submit.call_count == 2
        mock_submit.assert_any_call(user=self.student, block_key=self.problem.location, completion=0.0)
        mock_submit.assert_any_call(user=self.student, block_key=self.problem_2.location, completion=0.0)

    @mock.patch('completion.handlers.BlockCompletion.objects.submit_completion')
    def test_update_completion_split_test(self, mock_submit):
        """
        Asserts correctly publishes completion when a split test is involved

        This test case exists because we ran into a bug about the user_service not existing
        when a split_test existed inside of a subsection. Associated with this change was adding
        in the user state into the module before attempting completion and this ensures that is
        working properly.
        """
        partition = UserPartition(
            0,
            'first_partition',
            'First Partition',
            [
                Group(0, 'alpha'),
                Group(1, 'beta')
            ]
        )
        course = CourseFactory.create(user_partitions=[partition])
        section = BlockFactory.create(parent=course, category='chapter')
        subsection = BlockFactory.create(parent=section, category='sequential')

        c0_url = course.id.make_usage_key('vertical', 'split_test_cond0')
        c1_url = course.id.make_usage_key('vertical', 'split_test_cond1')
        split_test = BlockFactory.create(
            parent=subsection,
            category='split_test',
            user_partition_id=0,
            group_id_to_child={'0': c0_url, '1': c1_url},
        )

        cond0vert = BlockFactory.create(parent=split_test, category='vertical', location=c0_url)
        BlockFactory.create(parent=cond0vert, category='video')
        BlockFactory.create(parent=cond0vert, category='problem')

        cond1vert = BlockFactory.create(parent=split_test, category='vertical', location=c1_url)
        BlockFactory.create(parent=cond1vert, category='video')
        BlockFactory.create(parent=cond1vert, category='html')

        with override_waffle_switch(ENABLE_COMPLETION_TRACKING_SWITCH, True):
            update_exam_completion_task(self.student.username, str(subsection.location), 1.0)

        # Only the group the user was assigned to should have completion published.
        # Either cond0vert's children or cond1vert's children
        assert mock_submit.call_count == 2

    @mock.patch('lms.djangoapps.instructor.tasks.log.error')
    def test_update_completion_bad_user(self, mock_logger):
        """
        Assert a bad user raises error and returns None
        """
        username = 'bad_user'
        block_id = str(self.problem.location)
        update_exam_completion_task(username, block_id, 1.0)
        mock_logger.assert_called_once_with(
            self.complete_error_prefix.format(user=username, content_id=block_id) + 'User does not exist!'
        )

    @mock.patch('lms.djangoapps.instructor.tasks.log.error')
    def test_update_completion_bad_content_id(self, mock_logger):
        """
        Assert a bad content_id raises error and returns None
        """
        username = self.student.username
        update_exam_completion_task(username, 'foo/bar/baz', 1.0)
        mock_logger.assert_called_once_with(
            self.complete_error_prefix.format(user=username, content_id='foo/bar/baz') + 'Invalid content_id!'
        )

    @mock.patch('lms.djangoapps.instructor.tasks.log.error')
    def test_update_completion_nonexisting_item(self, mock_logger):
        """
        Assert nonexisting item in the modulestore
        raises error and returns None
        """
        username = self.student.username
        block = 'i4x://org.0/course_0/problem/fake_problem'
        update_exam_completion_task(username, block, 1.0)
        mock_logger.assert_called_once_with(
            self.complete_error_prefix.format(user=username, content_id=block) + 'Block not found in the modulestore!'
        )

    @mock.patch('lms.djangoapps.instructor.tasks.log.error')
    def test_update_completion_failed_module(self, mock_logger):
        """
        Assert failed get_block raises error and returns None
        """
        username = self.student.username
        with mock.patch('lms.djangoapps.instructor.tasks.get_block_for_descriptor', return_value=None):
            update_exam_completion_task(username, str(self.course.location), 1.0)
        mock_logger.assert_called_once_with(
            self.complete_error_prefix.format(user=username, content_id=self.course.location) +
            'Block unable to be created from descriptor!'
        )
