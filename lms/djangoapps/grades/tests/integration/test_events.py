"""
Test grading events across apps.
"""
from unittest.mock import call as mock_call
from unittest.mock import patch

import ddt
from crum import set_current_request
from xblocks_contrib.problem.capa.tests.response_xml_factory import MultipleChoiceResponseXMLFactory

import openedx.core.djangoapps.content.block_structure.api as bs_api
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.courseware.tests.test_submitting_problems import ProblemSubmissionTestMixin
from lms.djangoapps.instructor.enrollment import reset_student_attempts
from lms.djangoapps.instructor_task.api import submit_rescore_problem_for_student
from openedx.core.djangolib.testing.utils import get_mock_request
from xmodule.modulestore import ModuleStoreEnum  # pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import (  # pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
)

from ... import events


@ddt.ddt
class GradesEventIntegrationTest(ProblemSubmissionTestMixin, SharedModuleStoreTestCase):
    """
    Tests integration between the eventing in various layers
    of the grading infrastructure.
    """
    ENABLED_SIGNALS = ['course_published']

    @classmethod
    def reset_course(cls):
        """
        Sets up the course anew.
        """
        with cls.store.default_store(ModuleStoreEnum.Type.split):
            cls.course = CourseFactory.create()
            cls.chapter = BlockFactory.create(
                parent=cls.course,
                category="chapter",
                display_name="Test Chapter"
            )
            cls.sequence = BlockFactory.create(
                parent=cls.chapter,
                category='sequential',
                display_name="Test Sequential 1",
                graded=True,
                format="Homework"
            )
            cls.vertical = BlockFactory.create(
                parent=cls.sequence,
                category='vertical',
                display_name='Test Vertical 1'
            )
            problem_xml = MultipleChoiceResponseXMLFactory().build_xml(
                question_text='The correct answer is Choice 2',
                choices=[False, False, True, False],
                choice_names=['choice_0', 'choice_1', 'choice_2', 'choice_3']
            )
            cls.problem = BlockFactory.create(
                parent=cls.vertical,
                category="problem",
                display_name="p1",
                data=problem_xml,
                metadata={'weight': 2}
            )

    def setUp(self):
        self.reset_course()
        super().setUp()
        self.addCleanup(set_current_request, None)
        self.request = get_mock_request(UserFactory())
        self.student = self.request.user
        self.client.login(username=self.student.username, password=self.TEST_PASSWORD)
        CourseEnrollment.enroll(self.student, self.course.id)
        self.instructor = UserFactory.create(is_staff=True, username='test_instructor', password=self.TEST_PASSWORD)
        self.refresh_course()
        # Since this doesn't happen automatically and we don't want to run all the publish signal handlers
        # Just make sure we have the latest version of the course in cache before we test the problem.
        bs_api.update_course_in_cache(self.course.id)

    @patch('lms.djangoapps.grades.events.tracker')
    def test_submit_answer(self, events_tracker):
        self.submit_question_answer('p1', {'2_1': 'choice_choice_2'})

        event_transaction_id = events_tracker.emit.mock_calls[0][1][1]['event_transaction_id']
        expected_calls = [
            mock_call(
                events.PROBLEM_SUBMITTED_EVENT_TYPE,
                {
                    'user_id': str(self.student.id),
                    'event_transaction_id': event_transaction_id,
                    'event_transaction_type': events.PROBLEM_SUBMITTED_EVENT_TYPE,
                    'course_id': str(self.course.id),
                    'problem_id': str(self.problem.location),
                    'weighted_earned': 2.0,
                    'weighted_possible': 2.0,
                },
            ),
            mock_call(
                events.COURSE_GRADE_CALCULATED,
                {
                    'course_version': str(self.course.course_version),
                    'percent_grade': 0.02,
                    'grading_policy_hash': 'ChVp0lHGQGCevD0t4njna/C44zQ=',
                    'user_id': str(self.student.id),
                    'letter_grade': '',
                    'event_transaction_id': event_transaction_id,
                    'event_transaction_type': events.PROBLEM_SUBMITTED_EVENT_TYPE,
                    'course_id': str(self.course.id),
                    'course_edited_timestamp': str(self.course.subtree_edited_on),
                }
            ),
        ]

        events_tracker.emit.assert_has_calls(expected_calls, any_order=True)

    @ddt.data(True, False)
    def test_delete_student_state(self, emit_signals):
        self.submit_question_answer('p1', {'2_1': 'choice_choice_2'})

        with patch('lms.djangoapps.instructor.enrollment.tracker') as enrollment_tracker:
            with patch('lms.djangoapps.grades.events.tracker') as events_tracker:
                reset_student_attempts(
                    self.course.id,
                    self.student,
                    self.problem.location,
                    self.instructor,
                    delete_module=True,
                    emit_signals_and_events=emit_signals
                )
        course = self.store.get_course(self.course.id, depth=0)

        if not emit_signals:
            enrollment_tracker.assert_not_called()
            enrollment_tracker.emit.assert_not_called()
            events_tracker.emit.assert_not_called()
        else:
            event_transaction_id = enrollment_tracker.method_calls[0][1][1]['event_transaction_id']
            enrollment_tracker.emit.assert_called_with(
                events.STATE_DELETED_EVENT_TYPE,
                {
                    'user_id': str(self.student.id),
                    'course_id': str(self.course.id),
                    'problem_id': str(self.problem.location),
                    'instructor_id': str(self.instructor.id),
                    'event_transaction_id': event_transaction_id,
                    'event_transaction_type': events.STATE_DELETED_EVENT_TYPE,
                }
            )
            events_tracker.emit.assert_has_calls(
                [
                    mock_call(
                        events.COURSE_GRADE_CALCULATED,
                        {
                            'percent_grade': 0.0,
                            'grading_policy_hash': 'ChVp0lHGQGCevD0t4njna/C44zQ=',
                            'user_id': str(self.student.id),
                            'letter_grade': '',
                            'event_transaction_id': event_transaction_id,
                            'event_transaction_type': events.STATE_DELETED_EVENT_TYPE,
                            'course_id': str(self.course.id),
                            'course_edited_timestamp': str(course.subtree_edited_on),
                            'course_version': str(course.course_version),
                        }
                    ),
                    mock_call(
                        events.COURSE_GRADE_NOW_FAILED_EVENT_TYPE,
                        {
                            'user_id': str(self.student.id),
                            'event_transaction_id': event_transaction_id,
                            'event_transaction_type': events.STATE_DELETED_EVENT_TYPE,
                            'course_id': str(self.course.id),
                        }
                    ),
                ],
                any_order=True,
            )

    def test_rescoring_events(self):
        self.submit_question_answer('p1', {'2_1': 'choice_choice_3'})
        new_problem_xml = MultipleChoiceResponseXMLFactory().build_xml(
            question_text='The correct answer is Choice 3',
            choices=[False, False, False, True],
            choice_names=['choice_0', 'choice_1', 'choice_2', 'choice_3']
        )
        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, self.course.id):
            self.problem.data = new_problem_xml
            self.store.update_item(self.problem, self.instructor.id)
        self.store.publish(self.problem.location, self.instructor.id)

        with patch('lms.djangoapps.grades.events.tracker') as events_tracker:
            submit_rescore_problem_for_student(
                request=get_mock_request(self.instructor),
                usage_key=self.problem.location,
                student=self.student,
                only_if_higher=False
            )
        course = self.store.get_course(self.course.id, depth=0)

        # make sure the tracker's context is updated with course info
        for args in events_tracker.get_tracker().context.call_args_list:
            assert args[0][1] == {
                'course_id': str(self.course.id),
                'enterprise_uuid': '',
                'org_id': str(self.course.org)
            }

        event_transaction_id = events_tracker.emit.mock_calls[0][1][1]['event_transaction_id']
        events_tracker.emit.assert_has_calls(
            [
                mock_call(
                    events.GRADES_RESCORE_EVENT_TYPE,
                    {
                        'course_id': str(self.course.id),
                        'user_id': str(self.student.id),
                        'problem_id': str(self.problem.location),
                        'new_weighted_earned': 2,
                        'new_weighted_possible': 2,
                        'only_if_higher': False,
                        'instructor_id': str(self.instructor.id),
                        'event_transaction_id': event_transaction_id,
                        'event_transaction_type': events.GRADES_RESCORE_EVENT_TYPE,
                    },
                ),
                mock_call(
                    events.COURSE_GRADE_CALCULATED,
                    {
                        'course_version': str(course.course_version),
                        'percent_grade': 0.02,
                        'grading_policy_hash': 'ChVp0lHGQGCevD0t4njna/C44zQ=',
                        'user_id': str(self.student.id),
                        'letter_grade': '',
                        'event_transaction_id': event_transaction_id,
                        'event_transaction_type': events.GRADES_RESCORE_EVENT_TYPE,
                        'course_id': str(self.course.id),
                        'course_edited_timestamp': str(course.subtree_edited_on),
                    },
                ),
            ],
            any_order=True,
        )
