"""
Tests for the edx_proctoring integration into Studio
"""


from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import ddt
from django.conf import settings
from edx_proctoring.api import get_all_exams_for_course, get_review_policy_by_exam_id
from pytz import UTC

from cms.djangoapps.contentstore.signals.handlers import listen_for_course_publish
from common.djangoapps.student.tests.factories import UserFactory
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


@ddt.ddt
@patch.dict('django.conf.settings.FEATURES', {'ENABLE_SPECIAL_EXAMS': True})
@patch('cms.djangoapps.contentstore.signals.handlers.transaction.on_commit',
       new=Mock(side_effect=lambda func: func()),)  # run right away
class TestProctoredExams(ModuleStoreTestCase):
    """
    Tests for the publishing of proctored exams
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        """
        Initial data setup
        """
        super().setUp()

        self.course = CourseFactory.create(
            org='edX',
            course='900',
            run='test_run',
            enable_proctored_exams=True,
            proctoring_provider=settings.PROCTORING_BACKENDS['DEFAULT'],
        )

        # create object to avoid tests failures in proctoring.
        UserFactory(id=ModuleStoreEnum.UserID.test)

    def _verify_exam_data(self, sequence, expected_active):
        """
        Helper method to compare the sequence with the stored exam,
        which should just be a single one
        """
        exams = get_all_exams_for_course(str(self.course.id))

        self.assertEqual(len(exams), 1)

        exam = exams[0]

        if exam['is_proctored'] and not exam['is_practice_exam']:
            # get the review policy object
            exam_review_policy = get_review_policy_by_exam_id(exam['id'])
            self.assertEqual(exam_review_policy['review_policy'], sequence.exam_review_rules)

        if not exam['is_proctored'] and not exam['is_practice_exam']:
            # the hide after due value only applies to timed exams
            self.assertEqual(exam['hide_after_due'], sequence.hide_after_due)

        self.assertEqual(exam['course_id'], str(self.course.id))
        self.assertEqual(exam['content_id'], str(sequence.location))
        self.assertEqual(exam['exam_name'], sequence.display_name)
        self.assertEqual(exam['time_limit_mins'], sequence.default_time_limit_minutes)
        self.assertEqual(exam['is_proctored'], sequence.is_proctored_exam)
        self.assertEqual(exam['is_practice_exam'], sequence.is_practice_exam or sequence.is_onboarding_exam)
        self.assertEqual(exam['is_active'], expected_active)
        self.assertEqual(exam['backend'], self.course.proctoring_provider)

    @ddt.data(
        (False, True),
        (True, False),
    )
    @ddt.unpack
    def test_onboarding_exam_is_practice_exam(self, is_practice_exam, is_onboarding_exam):
        """
        Check that an onboarding exam is treated as a practice exam when
        communicating with the edx-proctoring subsystem.
        """
        default_time_limit_minutes = 10
        is_proctored_exam = True

        chapter = BlockFactory.create(parent=self.course, category='chapter', display_name='Test Section')
        sequence = BlockFactory.create(
            parent=chapter,
            category='sequential',
            display_name='Test Proctored Exam',
            graded=True,
            is_time_limited=True,
            default_time_limit_minutes=default_time_limit_minutes,
            is_proctored_enabled=is_proctored_exam,
            is_practice_exam=is_practice_exam,
            due=datetime.now(UTC) + timedelta(minutes=default_time_limit_minutes + 1),
            exam_review_rules="allow_use_of_paper",
            hide_after_due=True,
            is_onboarding_exam=is_onboarding_exam,
        )

        listen_for_course_publish(self, self.course.id)

        self._verify_exam_data(sequence, True)

    @ddt.data(
        (True, False, True, False, False),
        (False, False, True, False, False),
        (False, False, True, False, True),
        (True, True, True, True, False),
    )
    @ddt.unpack
    def test_publishing_exam(self, is_proctored_exam,
                             is_practice_exam, expected_active, republish, hide_after_due):
        """
        Happy path testing to see that when a course is published which contains
        a proctored exam, it will also put an entry into the exam tables
        """
        default_time_limit_minutes = 10

        chapter = BlockFactory.create(parent=self.course, category='chapter', display_name='Test Section')
        sequence = BlockFactory.create(
            parent=chapter,
            category='sequential',
            display_name='Test Proctored Exam',
            graded=True,
            is_time_limited=True,
            default_time_limit_minutes=default_time_limit_minutes,
            is_proctored_enabled=is_proctored_exam,
            is_practice_exam=is_practice_exam,
            due=datetime.now(UTC) + timedelta(minutes=default_time_limit_minutes + 1),
            exam_review_rules="allow_use_of_paper",
            hide_after_due=hide_after_due,
            is_onboarding_exam=False,
        )

        listen_for_course_publish(self, self.course.id)

        self._verify_exam_data(sequence, expected_active)

        if republish:
            # update the sequence
            sequence.default_time_limit_minutes += sequence.default_time_limit_minutes
            self.store.update_item(sequence, self.user.id)

            # simulate a publish
            listen_for_course_publish(self, self.course.id)

            # reverify
            self._verify_exam_data(sequence, expected_active,)

    def test_unpublishing_proctored_exam(self):
        """
        Make sure that if we publish and then unpublish a proctored exam,
        the exam record stays, but is marked as is_active=False
        """
        chapter = BlockFactory.create(parent=self.course, category='chapter', display_name='Test Section')
        sequence = BlockFactory.create(
            parent=chapter,
            category='sequential',
            display_name='Test Proctored Exam',
            graded=True,
            is_time_limited=True,
            default_time_limit_minutes=10,
            is_proctored_enabled=True,
            hide_after_due=False,
            is_onboarding_exam=False,
        )

        listen_for_course_publish(self, self.course.id)

        exams = get_all_exams_for_course(str(self.course.id))
        self.assertEqual(len(exams), 1)

        sequence.is_time_limited = False
        sequence.is_proctored_exam = False

        self.store.update_item(sequence, self.user.id)

        listen_for_course_publish(self, self.course.id)

        self._verify_exam_data(sequence, False)

    def test_dangling_exam(self):
        """
        Make sure we filter out all dangling items
        """

        chapter = BlockFactory.create(parent=self.course, category='chapter', display_name='Test Section')
        BlockFactory.create(
            parent=chapter,
            category='sequential',
            display_name='Test Proctored Exam',
            graded=True,
            is_time_limited=True,
            default_time_limit_minutes=10,
            is_proctored_enabled=True,
            hide_after_due=False,
        )

        listen_for_course_publish(self, self.course.id)

        exams = get_all_exams_for_course(str(self.course.id))
        self.assertEqual(len(exams), 1)

        self.store.delete_item(chapter.location, self.user.id)

        # republish course
        listen_for_course_publish(self, self.course.id)

        # look through exam table, the dangling exam
        # should be disabled
        exams = get_all_exams_for_course(str(self.course.id))
        self.assertEqual(len(exams), 1)

        exam = exams[0]
        self.assertEqual(exam['is_active'], False)

    @patch.dict('django.conf.settings.FEATURES', {'ENABLE_SPECIAL_EXAMS': False})
    def test_feature_flag_off(self):
        """
        Make sure the feature flag is honored
        """
        chapter = BlockFactory.create(parent=self.course, category='chapter', display_name='Test Section')
        BlockFactory.create(
            parent=chapter,
            category='sequential',
            display_name='Test Proctored Exam',
            graded=True,
            is_time_limited=True,
            default_time_limit_minutes=10,
            is_proctored_enabled=True,
            hide_after_due=False,
        )

        listen_for_course_publish(self, self.course.id)

        exams = get_all_exams_for_course(str(self.course.id))
        self.assertEqual(len(exams), 0)

    @ddt.data(
        (True, False, 1),
        (False, True, 1),
        (False, False, 0),
    )
    @ddt.unpack
    def test_advanced_settings(self, enable_timed_exams, enable_proctored_exams, expected_count):
        """
        Make sure the feature flag is honored
        """

        self.course = CourseFactory.create(
            org='edX',
            course='901',
            run='test_run2',
            enable_proctored_exams=enable_proctored_exams,
            enable_timed_exams=enable_timed_exams
        )

        chapter = BlockFactory.create(parent=self.course, category='chapter', display_name='Test Section')
        BlockFactory.create(
            parent=chapter,
            category='sequential',
            display_name='Test Proctored Exam',
            graded=True,
            is_time_limited=True,
            default_time_limit_minutes=10,
            is_proctored_enabled=True,
            exam_review_rules="allow_use_of_paper",
            hide_after_due=False,
        )

        listen_for_course_publish(self, self.course.id)

        # there shouldn't be any exams because we haven't enabled that
        # advanced setting flag
        exams = get_all_exams_for_course(str(self.course.id))
        self.assertEqual(len(exams), expected_count)

    def test_self_paced_no_due_dates(self):
        self.course = CourseFactory.create(
            org='edX',
            course='901',
            run='test_run2',
            enable_proctored_exams=True,
            enable_timed_exams=True,
            self_paced=True,
        )
        chapter = BlockFactory.create(parent=self.course, category='chapter', display_name='Test Section')
        BlockFactory.create(
            parent=chapter,
            category='sequential',
            display_name='Test Proctored Exam',
            graded=True,
            is_time_limited=True,
            default_time_limit_minutes=60,
            is_proctored_enabled=False,
            is_practice_exam=False,
            due=datetime.now(UTC) + timedelta(minutes=60),
            exam_review_rules="allow_use_of_paper",
            hide_after_due=True,
            is_onboarding_exam=False,
        )
        listen_for_course_publish(self, self.course.id)
        exams = get_all_exams_for_course(str(self.course.id))
        # self-paced courses should ignore due dates
        assert exams[0]['due_date'] is None

        # now switch to instructor paced
        # the exam will be updated with a due date
        self.course.self_paced = False
        self.course = self.update_course(self.course, 1)
        listen_for_course_publish(self, self.course.id)
        exams = get_all_exams_for_course(str(self.course.id))
        assert exams[0]['due_date'] is not None

    def test_async_waffle_flag_publishes(self):
        chapter = BlockFactory.create(parent=self.course, category='chapter', display_name='Test Section')
        sequence = BlockFactory.create(
            parent=chapter,
            category='sequential',
            display_name='Test Proctored Exam',
            graded=True,
            is_time_limited=True,
            default_time_limit_minutes=10,
            is_proctored_enabled=True,
            hide_after_due=False,
            is_onboarding_exam=False,
            exam_review_rules="allow_use_of_paper",
        )

        listen_for_course_publish(self, self.course.id)

        exams = get_all_exams_for_course(str(self.course.id))
        self.assertEqual(len(exams), 1)
        self._verify_exam_data(sequence, True)

    def test_async_waffle_flag_task(self):
        chapter = BlockFactory.create(parent=self.course, category='chapter', display_name='Test Section')
        BlockFactory.create(
            parent=chapter,
            category='sequential',
            display_name='Test Proctored Exam',
            graded=True,
            is_time_limited=True,
            default_time_limit_minutes=10,
            is_proctored_enabled=True,
            hide_after_due=False,
            is_onboarding_exam=False,
            exam_review_rules="allow_use_of_paper",
        )

        with patch('cms.djangoapps.contentstore.tasks.update_special_exams_and_publish') as mock_task:
            listen_for_course_publish(self, self.course.id)
            mock_task.delay.assert_called()
