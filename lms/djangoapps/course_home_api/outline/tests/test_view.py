"""
Tests for Outline Tab API in the Course Home API
"""

import itertools
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import ddt
from completion.models import BlockCompletion
from django.conf import settings
from django.test import override_settings
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag

from cms.djangoapps.contentstore.outlines import update_outline_from_modulestore
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import CourseInstructorRole
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.course_home_api.tests.utils import BaseCourseHomeTests
from lms.djangoapps.course_home_api.toggles import COURSE_HOME_SEND_COURSE_PROGRESS_ANALYTICS_FOR_STUDENT
from lms.djangoapps.grades.course_grade_factory import CourseGradeFactory
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.learning_sequences.api import replace_course_outline
from openedx.core.djangoapps.content.learning_sequences.data import CourseOutlineData, CourseVisibility
from openedx.core.djangoapps.course_date_signals.utils import MIN_DURATION
from openedx.core.djangoapps.user_api.preferences.api import set_user_preference
from openedx.core.djangoapps.user_api.tests.factories import UserCourseTagFactory
from openedx.features.course_duration_limits.models import CourseDurationLimitConfig
from openedx.features.course_experience import COURSE_ENABLE_UNENROLLED_ACCESS_FLAG, ENABLE_COURSE_GOALS
from openedx.features.discounts.applicability import DISCOUNT_APPLICABILITY_FLAG, FIRST_PURCHASE_DISCOUNT_OVERRIDE_FLAG
from xmodule.course_block import COURSE_VISIBILITY_PUBLIC, COURSE_VISIBILITY_PUBLIC_OUTLINE
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


@ddt.ddt
class OutlineTabTestViews(BaseCourseHomeTests):
    """
    Tests for the Outline Tab API
    """

    def setUp(self):
        super().setUp()
        self.url = reverse('course-home:outline-tab', args=[self.course.id])

    def update_course_and_overview(self):
        self.update_course(self.course, self.user.id)
        CourseOverview.load_from_module_store(self.course.id)

    @override_waffle_flag(ENABLE_COURSE_GOALS, active=True)
    @ddt.data(CourseMode.AUDIT, CourseMode.VERIFIED)
    def test_get_authenticated_enrolled_user(self, enrollment_mode):
        CourseEnrollment.enroll(self.user, self.course.id, enrollment_mode)
        response = self.client.get(self.url)
        assert response.status_code == 200

        course_tools = response.data.get('course_tools')
        assert course_tools
        assert course_tools[0]['analytics_id'] == 'edx.bookmarks'

        dates_widget = response.data.get('dates_widget')
        assert dates_widget
        date_blocks = dates_widget.get('course_date_blocks')
        assert all((block.get('title') != '') for block in date_blocks)
        assert all(block.get('date') for block in date_blocks)

        resume_course = response.data.get('resume_course')
        resume_course_url = resume_course.get('url')
        if resume_course_url:
            assert 'http://' in resume_course_url

    @ddt.data(True, False)
    def test_get_authenticated_user_not_enrolled(self, has_previously_enrolled):
        if has_previously_enrolled:
            # Create an enrollment, then unenroll to set is_active to False
            CourseEnrollment.enroll(self.user, self.course.id)
            CourseEnrollment.unenroll(self.user, self.course.id)
        response = self.client.get(self.url)
        assert response.status_code == 200

        course_tools = response.data.get('course_tools')
        assert len(course_tools) == 0

        dates_widget = response.data.get('dates_widget')
        assert dates_widget
        date_blocks = dates_widget.get('course_date_blocks')
        assert all((block.get('title') != '') for block in date_blocks)
        assert all(block.get('date') for block in date_blocks)

    def test_get_unauthenticated_user(self):
        self.client.logout()
        response = self.client.get(self.url)
        assert response.status_code == 200

        course_blocks = response.data.get('course_blocks')
        assert course_blocks is None

        course_tools = response.data.get('course_tools')
        assert len(course_tools) == 0

        dates_widget = response.data.get('dates_widget')
        assert dates_widget
        date_blocks = dates_widget.get('course_date_blocks')
        assert len(date_blocks) == 0

    def test_masquerade(self):
        user = UserFactory()
        set_user_preference(user, 'time_zone', 'Asia/Tokyo')
        CourseEnrollment.enroll(user, self.course.id)

        self.switch_to_staff()  # needed for masquerade

        # Sanity check on our normal user
        assert self.client.get(self.url).data['dates_widget']['user_timezone'] is None

        # Now switch users and confirm we get a different result
        self.update_masquerade(username=user.username)
        assert self.client.get(self.url).data['dates_widget']['user_timezone'] == 'Asia/Tokyo'

    def test_course_staff_can_see_non_user_specific_content_in_masquerade(self):
        """
        Test that course staff can see the outline and other non-user-specific content when masquerading as a learner
        """
        self.store.create_item(
            self.user.id, self.course.id, 'course_info', 'handouts', fields={'data': '<p>Handouts</p>'}
        )

        instructor = UserFactory(
            username='instructor',
            email='instructor@example.com',
            password='foo',
            is_staff=False
        )
        CourseInstructorRole(self.course.id).add_users(instructor)
        self.client.login(username=instructor, password='foo')
        self.update_masquerade(role="student")
        response = self.client.get(self.url)
        assert response.data['course_blocks'] is not None
        assert response.data['handouts_html'] is not None

    @override_waffle_flag(COURSE_ENABLE_UNENROLLED_ACCESS_FLAG, active=True)
    def test_handouts(self):
        CourseEnrollment.enroll(self.user, self.course.id)
        self.store.create_item(self.user.id, self.course.id, 'course_info', 'handouts', fields={'data': '<p>Hi</p>'})
        assert self.client.get(self.url).data['handouts_html'] == '<p>Hi</p>'

    def test_get_unknown_course(self):
        url = reverse('course-home:outline-tab', args=['course-v1:unknown+course+2T2020'])
        response = self.client.get(url)
        assert response.status_code == 404

    @ddt.data(True, False)
    def test_welcome_message(self, welcome_message_is_dismissed):
        CourseEnrollment.enroll(self.user, self.course.id)
        self.store.create_item(
            self.user.id, self.course.id,
            'course_info',
            'updates',
            fields={
                'items': [{
                    'content': '<p>Welcome</p>',
                    'status': 'visible',
                    'date': 'July 23, 2020',
                    'id': 1
                }]
            }
        )
        UserCourseTagFactory(
            user=self.user,
            course_id=self.course.id,
            key='view-welcome-message',
            value=not welcome_message_is_dismissed
        )
        welcome_message_html = self.client.get(self.url).data['welcome_message_html']
        assert welcome_message_html == (None if welcome_message_is_dismissed else '<p>Welcome</p>')

    @ddt.data(
        (False, 'EDXWELCOME', 15),
        (True, 'NOTEDXWELCOME', 30),
    )
    @ddt.unpack
    def test_offer(self, is_fpd_override_waffle_flag_on, fpd_code, fpd_percentage):
        """
        Test that the offer data contains the correct code for the first purchase discount,
        which can be overriden via a waffle flag from the default EDXWELCOME.
        """
        CourseEnrollment.enroll(self.user, self.course.id)

        response = self.client.get(self.url)
        assert response.data['offer'] is None

        with override_settings(FIRST_PURCHASE_DISCOUNT_OVERRIDE_CODE='NOTEDXWELCOME'):
            with override_settings(FIRST_PURCHASE_DISCOUNT_OVERRIDE_PERCENTAGE=fpd_percentage):
                with override_waffle_flag(DISCOUNT_APPLICABILITY_FLAG, active=True):
                    with override_waffle_flag(
                        FIRST_PURCHASE_DISCOUNT_OVERRIDE_FLAG, active=is_fpd_override_waffle_flag_on
                    ):
                        response = self.client.get(self.url)

                        # Just a quick spot check that the dictionary looks like what we expect
                        assert response.data['offer']['code'] == fpd_code
                        assert response.data['offer']['percentage'] == fpd_percentage

    def test_access_expiration(self):
        enrollment = CourseEnrollment.enroll(self.user, self.course.id, CourseMode.VERIFIED)
        CourseDurationLimitConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1))

        response = self.client.get(self.url)
        assert response.data['access_expiration'] is None

        enrollment.update_enrollment(CourseMode.AUDIT)
        response = self.client.get(self.url)

        # Just a quick spot check that the dictionary looks like what we expect
        deadline = enrollment.created + MIN_DURATION
        assert response.data['access_expiration']['expiration_date'] == deadline

    @override_waffle_flag(ENABLE_COURSE_GOALS, active=True)
    def test_post_course_goal(self):
        """ Test that the api returns the correct response when saving a goal """
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.AUDIT)

        post_data = json.dumps({
            'course_id': str(self.course.id),
            'days_per_week': 1,
            'subscribed_to_reminders': True,
        })
        post_course_goal_response = self.client.post(
            reverse('course-home:save-course-goal'),
            post_data,
            content_type='application/json',
        )
        assert post_course_goal_response.status_code == 200

        response = self.client.get(self.url)
        assert response.status_code == 200

        course_goals = response.json()['course_goals']
        expected_course_goals = {
            'selected_goal': {
                'days_per_week': 1,
                'subscribed_to_reminders': True,
            },
            'weekly_learning_goal_enabled': True,
        }
        assert course_goals == expected_course_goals

    @patch.dict('django.conf.settings.FEATURES', {'ENABLE_SPECIAL_EXAMS': True})
    @patch('lms.djangoapps.course_api.blocks.transformers.milestones.get_attempt_status_summary')
    def test_proctored_exam(self, mock_summary):
        course = CourseFactory.create(
            org='edX',
            course='900',
            run='test_run',
            enable_proctored_exams=True,
            proctoring_provider=settings.PROCTORING_BACKENDS['DEFAULT'],
        )
        chapter = BlockFactory.create(parent=course, category='chapter', display_name='Test Section')
        sequence = BlockFactory.create(
            parent=chapter,
            category='sequential',
            display_name='Test Proctored Exam',
            graded=True,
            is_time_limited=True,
            default_time_limit_minutes=10,
            is_practice_exam=True,
            due=datetime.now(),
            exam_review_rules='allow_use_of_paper',
            hide_after_due=False,
            is_onboarding_exam=False,
        )
        sequence.is_proctored_exam = True
        update_outline_from_modulestore(course.id)
        mock_summary.return_value = {
            'short_description': 'My Exam',
            'suggested_icon': 'fa-foo-bar',
        }
        url = reverse('course-home:outline-tab', args=[course.id])

        CourseEnrollment.enroll(self.user, course.id)
        response = self.client.get(url)
        assert response.status_code == 200

        exam_data = response.data['course_blocks']['blocks'][str(sequence.location)]
        assert not exam_data['complete']
        assert exam_data['description'] == 'My Exam'
        assert exam_data['display_name'] == 'Test Proctored Exam'
        assert exam_data['due'] is not None
        assert exam_data['icon'] == 'fa-foo-bar'

    def test_assignment(self):
        course = CourseFactory.create()
        with self.store.bulk_operations(course.id):
            chapter = BlockFactory.create(category='chapter', parent_location=course.location)
            sequential = BlockFactory.create(display_name='Test', category='sequential', graded=True, has_score=True,
                                             parent_location=chapter.location)
            BlockFactory.create(category='problem', graded=True, has_score=True, parent_location=sequential.location)
            BlockFactory.create(category='problem', graded=True, has_score=True, parent_location=sequential.location)
            sequential2 = BlockFactory.create(display_name='Ungraded', category='sequential',
                                              parent_location=chapter.location)
            BlockFactory.create(category='problem', parent_location=sequential2.location)
        update_outline_from_modulestore(course.id)
        url = reverse('course-home:outline-tab', args=[course.id])

        CourseEnrollment.enroll(self.user, course.id)
        response = self.client.get(url)
        assert response.status_code == 200

        exam_data = response.data['course_blocks']['blocks'][str(sequential.location)]
        assert exam_data['display_name'] == 'Test (2 Questions)'
        assert exam_data['icon'] == 'fa-pencil-square-o'

        ungraded_data = response.data['course_blocks']['blocks'][str(sequential2.location)]
        assert ungraded_data['display_name'] == 'Ungraded'
        assert ungraded_data['icon'] is None

    @override_waffle_flag(COURSE_ENABLE_UNENROLLED_ACCESS_FLAG, active=True)
    @patch('lms.djangoapps.course_home_api.outline.views.generate_offer_data', new=Mock(return_value={'a': 1}))
    @patch('lms.djangoapps.course_home_api.outline.views.get_access_expiration_data', new=Mock(return_value={'b': 1}))
    @ddt.data(*itertools.product([True, False], [True, False],
                                 [None, COURSE_VISIBILITY_PUBLIC, COURSE_VISIBILITY_PUBLIC_OUTLINE]))
    @ddt.unpack
    def test_visibility(self, is_enrolled, is_staff, course_visibility):
        if is_enrolled:
            CourseEnrollment.enroll(self.user, self.course.id)
        if is_staff:
            self.user.is_staff = True
            self.user.save()
        if course_visibility:
            self.course.course_visibility = course_visibility
            self.update_course_and_overview()

        self.store.create_item(
            self.user.id, self.course.id, 'course_info', 'handouts', fields={'data': '<p>Handouts</p>'}
        )
        self.store.create_item(self.user.id, self.course.id, 'course_info', 'updates', fields={
            'items': [{
                'content': '<p>Welcome</p>',
                'status': 'visible',
                'date': 'July 23, 2020',
                'id': 1,
            }]
        })

        show_enrolled = is_enrolled or is_staff
        is_public = course_visibility == COURSE_VISIBILITY_PUBLIC
        is_public_outline = course_visibility == COURSE_VISIBILITY_PUBLIC_OUTLINE

        data = self.client.get(self.url).data
        assert (data['course_blocks'] is not None) == (show_enrolled or is_public or is_public_outline)
        assert (data['handouts_html'] is not None) == (show_enrolled or is_public)
        assert (data['offer'] is not None) == show_enrolled
        assert (data['access_expiration'] is not None) == show_enrolled
        assert (data['resume_course']['url'] is not None) == show_enrolled

    def test_verified_mode(self):
        enrollment = CourseEnrollment.enroll(self.user, self.course.id)
        CourseDurationLimitConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1))

        response = self.client.get(self.url)
        assert response.data['verified_mode'] == {
            'access_expiration_date': (enrollment.created + MIN_DURATION),
            'currency': 'USD',
            'currency_symbol': '$',
            'price': 149,
            'sku': 'ABCD1234',
            'upgrade_url': '/dashboard'
        }

    def test_hide_learning_sequences(self):
        """
        Check that Learning Sequences filters out sequences.
        """
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.VERIFIED)

        # Normal behavior: the sequence exists
        response = self.client.get(self.url)
        assert response.status_code == 200
        blocks = response.data['course_blocks']['blocks']
        seq_block_id = next(
            block_id
            for block_id, block in blocks.items()
            if block['type'] == 'sequential'
        )

        # With a course outline loaded, the same sequence is removed.
        new_learning_seq_outline = CourseOutlineData(
            course_key=self.course.id,
            title="Test Course Outline!",
            published_at=datetime(2021, 6, 14, tzinfo=timezone.utc),  # noqa: UP017
            published_version="5ebece4b69dd593d82fe2022",
            entrance_exam_id=None,
            days_early_for_beta=None,
            sections=[],
            self_paced=False,
            course_visibility=CourseVisibility.PRIVATE  # pylint: disable=protected-access
        )
        replace_course_outline(new_learning_seq_outline)
        response = self.client.get(self.url)
        blocks = response.data['course_blocks']['blocks']
        assert seq_block_id not in blocks

    def test_user_has_passing_grade(self):
        CourseEnrollment.enroll(self.user, self.course.id)
        self.course._grading_policy['GRADE_CUTOFFS']['Pass'] = 0  # pylint: disable=protected-access
        self.update_course_and_overview()
        CourseGradeFactory().update(self.user, self.course)
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert response.data['user_has_passing_grade'] is True

    def test_hide_from_toc_field(self):
        """
        Test that the hide_from_toc field is returned in the response.
        """
        CourseEnrollment.enroll(self.user, self.course.id)
        response = self.client.get(self.url)
        assert response.status_code == 200
        for block in response.data["course_blocks"]["blocks"].values():
            assert "hide_from_toc" in block

    def assert_can_enroll(self, can_enroll):
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert response.data['enroll_alert']['can_enroll'] == can_enroll

    def test_can_enroll_basic(self):
        self.assert_can_enroll(True)

    def test_cannot_enroll_invitation_only(self):
        self.course.invitation_only = True
        self.update_course_and_overview()
        self.assert_can_enroll(False)

    def test_cannot_enroll_masters_only(self):
        CourseMode.objects.all().delete()
        CourseModeFactory(course_id=self.course.id, mode_slug=CourseMode.MASTERS)
        self.assert_can_enroll(False)

    def test_cannot_enroll_before_enrollment(self):
        self.course.enrollment_start = datetime.now(timezone.utc) + timedelta(days=1)  # noqa: UP017
        self.update_course_and_overview()
        self.assert_can_enroll(False)

    def test_cannot_enroll_after_enrollment(self):
        self.course.enrollment_end = datetime.now(timezone.utc) - timedelta(days=1)  # noqa: UP017
        self.update_course_and_overview()
        self.assert_can_enroll(False)

    def test_cannot_enroll_if_full(self):
        self.course.max_student_enrollments_allowed = 1
        self.update_course_and_overview()
        CourseEnrollment.enroll(UserFactory(), self.course.id)  # grr, some rando took our spot!
        self.assert_can_enroll(False)

    @override_waffle_flag(COURSE_HOME_SEND_COURSE_PROGRESS_ANALYTICS_FOR_STUDENT, active=True)
    @patch("lms.djangoapps.course_home_api.outline.views.collect_progress_for_user_in_course.delay")
    def test_course_progress_analytics_enabled(self, mock_task):
        """
        Ensures that the `calculate_course_progress_for_user_in_course` task is enqueued, with the correct args, only
        if the feature is enabled.
        """
        self.client.get(self.url)
        mock_task.assert_called_once_with(str(self.course.id), self.user.id)

    @override_waffle_flag(COURSE_HOME_SEND_COURSE_PROGRESS_ANALYTICS_FOR_STUDENT, active=False)
    @patch("lms.djangoapps.course_home_api.outline.views.collect_progress_for_user_in_course.delay")
    def test_course_progress_analytics_disabled(self, mock_task):
        """
        Ensures that the `calculate_course_progress_for_user_in_course` task is not run if the feature is disabled.
        """
        self.client.get(self.url)
        mock_task.assert_not_called()


@ddt.ddt
class SidebarBlocksTestViews(BaseCourseHomeTests):
    """
    Tests for the Course Sidebar Blocks API
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.chapter = ''
        self.sequential = ''
        self.vertical = ''
        self.ungraded_sequential = ''
        self.ungraded_vertical = ''
        self.url = ''

    def setUp(self):
        super().setUp()
        self.url = reverse('course-home:course-navigation', args=[self.course.id])

    def update_course_and_overview(self):
        """
        Update the course and course overview records.
        """
        self.update_course(self.course, self.user.id)
        CourseOverview.load_from_module_store(self.course.id)

    def add_blocks_to_course(self):
        """
        Add test blocks to the self course.
        """
        with self.store.bulk_operations(self.course.id):
            self.chapter = BlockFactory.create(category='chapter', parent_location=self.course.location)
            self.sequential = BlockFactory.create(
                display_name='Test',
                category='sequential',
                graded=True,
                has_score=True,
                parent_location=self.chapter.location
            )
            self.vertical = BlockFactory.create(
                category='vertical',
                graded=True,
                has_score=True,
                parent_location=self.sequential.location
            )
            self.ungraded_sequential = BlockFactory.create(
                display_name='Ungraded',
                category='sequential',
                parent_location=self.chapter.location
            )
            self.ungraded_vertical = BlockFactory.create(
                category='vertical',
                parent_location=self.ungraded_sequential.location
            )
        update_outline_from_modulestore(self.course.id)

    def create_completion(self, problem, completion):
        return BlockCompletion.objects.create(
            user=self.user,
            context_key=problem.context_key,
            block_type='problem',
            block_key=problem.location,
            completion=completion,
        )

    @ddt.data(CourseMode.AUDIT, CourseMode.VERIFIED)
    def test_get_authenticated_enrolled_user(self, enrollment_mode):
        """
        Test that the API returns the correct data for an authenticated, enrolled user.
        """
        self.add_blocks_to_course()
        CourseEnrollment.enroll(self.user, self.course.id, enrollment_mode)

        response = self.client.get(self.url)
        assert response.status_code == 200

        chapter_data = response.data['blocks'][str(self.chapter.location)]
        assert str(self.sequential.location) in chapter_data['children']

        sequential_data = response.data['blocks'][str(self.sequential.location)]
        assert str(self.vertical.location) in sequential_data['children']

        vertical_data = response.data['blocks'][str(self.vertical.location)]
        assert vertical_data['children'] == []

    @ddt.data(True, False)
    def test_get_authenticated_user_not_enrolled(self, has_previously_enrolled):
        """
        Test that the API returns an empty response for an authenticated user who is not enrolled in the course.
        """
        if has_previously_enrolled:
            CourseEnrollment.enroll(self.user, self.course.id)
            CourseEnrollment.unenroll(self.user, self.course.id)

        response = self.client.get(self.url)
        assert response.status_code == 200
        assert response.data == {}

    def test_get_unauthenticated_user(self):
        """
        Test that the API returns an empty response for an unauthenticated user.
        """
        self.client.logout()
        response = self.client.get(self.url)

        assert response.status_code == 200
        assert response.data.get('blocks') is None

    def test_course_staff_can_see_non_user_specific_content_in_masquerade(self):
        """
        Test that course staff can see the outline and other non-user-specific content when masquerading as a learner
        """
        instructor = UserFactory(username='instructor', email='instructor@example.com', password='foo', is_staff=False)
        CourseInstructorRole(self.course.id).add_users(instructor)
        self.client.login(username=instructor, password='foo')
        self.update_masquerade(role='student')
        response = self.client.get(self.url)
        assert response.data['blocks'] is not None

    def test_get_unknown_course(self):
        """
        Test that the API returns a 404 when the course is not found.
        """
        url = reverse('course-home:course-navigation', args=['course-v1:unknown+course+2T2020'])
        response = self.client.get(url)
        assert response.status_code == 404

    @patch.dict('django.conf.settings.FEATURES', {'ENABLE_SPECIAL_EXAMS': True})
    @patch('lms.djangoapps.course_api.blocks.transformers.milestones.get_attempt_status_summary')
    def test_proctored_exam(self, mock_summary):
        """
        Test that the API returns the correct data for a proctored exam.
        """
        course = CourseFactory.create(
            org='edX',
            course='900',
            run='test_run',
            enable_proctored_exams=True,
            proctoring_provider=settings.PROCTORING_BACKENDS['DEFAULT'],
        )
        chapter = BlockFactory.create(parent=course, category='chapter', display_name='Test Section')
        sequence = BlockFactory.create(
            parent=chapter,
            category='sequential',
            display_name='Test Proctored Exam',
            graded=True,
            is_time_limited=True,
            default_time_limit_minutes=10,
            is_practice_exam=True,
            due=datetime.now(),
            exam_review_rules='allow_use_of_paper',
            hide_after_due=False,
            is_onboarding_exam=False,
        )
        vertical = BlockFactory.create(
            parent=sequence,
            category='vertical',
            graded=True,
            has_score=True,
        )
        BlockFactory.create(
            parent=vertical,
            category='problem',
            graded=True,
            has_score=True,
        )
        sequence.is_proctored_exam = True
        update_outline_from_modulestore(course.id)
        CourseEnrollment.enroll(self.user, course.id)
        mock_summary.return_value = {
            'short_description': 'My Exam',
            'suggested_icon': 'fa-foo-bar',
        }

        url = reverse('course-home:course-navigation', args=[course.id])
        response = self.client.get(url)
        assert response.status_code == 200

        exam_data = response.data['blocks'][str(sequence.location)]
        assert not exam_data['complete']
        assert exam_data['display_name'] == 'Test Proctored Exam (1 Question)'
        assert exam_data['special_exam_info'] == 'My Exam'
        assert exam_data['due'] is not None

    def test_assignment(self):
        """
        Test that the API returns the correct data for an assignment.
        """
        self.add_blocks_to_course()
        CourseEnrollment.enroll(self.user, self.course.id)

        response = self.client.get(self.url)
        assert response.status_code == 200

        exam_data = response.data['blocks'][str(self.sequential.location)]
        assert exam_data['display_name'] == 'Test'
        assert exam_data['icon'] is None
        assert str(self.vertical.location) in exam_data['children']

        ungraded_data = response.data['blocks'][str(self.ungraded_sequential.location)]
        assert ungraded_data['display_name'] == 'Ungraded'
        assert ungraded_data['icon'] is None
        assert str(self.ungraded_vertical.location) in ungraded_data['children']

    @override_waffle_flag(COURSE_ENABLE_UNENROLLED_ACCESS_FLAG, active=True)
    @ddt.data(*itertools.product(
        [True, False], [True, False], [None, COURSE_VISIBILITY_PUBLIC, COURSE_VISIBILITY_PUBLIC_OUTLINE]
    ))
    @ddt.unpack
    def test_visibility(self, is_enrolled, is_staff, course_visibility):
        """
        Test that the API returns the correct data based on the user's enrollment status and the course's visibility.
        """
        if is_enrolled:
            CourseEnrollment.enroll(self.user, self.course.id)
        if is_staff:
            self.user.is_staff = True
            self.user.save()
        if course_visibility:
            self.course.course_visibility = course_visibility
            self.update_course_and_overview()

        show_enrolled = is_enrolled or is_staff
        is_public = course_visibility == COURSE_VISIBILITY_PUBLIC
        is_public_outline = course_visibility == COURSE_VISIBILITY_PUBLIC_OUTLINE

        data = self.client.get(self.url).data
        if not (show_enrolled or is_public or is_public_outline):
            assert data == {}
        else:
            assert (data['blocks'] is not None) == (show_enrolled or is_public or is_public_outline)

    def test_hide_learning_sequences(self):
        """
        Check that Learning Sequences filters out sequences.
        """
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.VERIFIED)
        response = self.client.get(self.url)
        assert response.status_code == 200

        blocks = response.data['blocks']
        seq_block_id = next(block_id for block_id, block in blocks.items() if block['type'] in ('sequential', 'lock'))

        # With a course outline loaded, the same sequence is removed.
        new_learning_seq_outline = CourseOutlineData(
            course_key=self.course.id,
            title='Test Course Outline!',
            published_at=datetime(2021, 6, 14, tzinfo=timezone.utc),  # noqa: UP017
            published_version='5ebece4b69dd593d82fe2022',
            entrance_exam_id=None,
            days_early_for_beta=None,
            sections=[],
            self_paced=False,
            course_visibility=CourseVisibility.PRIVATE
        )
        replace_course_outline(new_learning_seq_outline)
        blocks = self.client.get(self.url).data['blocks']
        assert seq_block_id not in blocks

    def test_empty_blocks_complete(self):
        """
        Test that the API returns the correct complete state for empty blocks.
        """
        self.add_blocks_to_course()
        CourseEnrollment.enroll(self.user, self.course.id)
        url = reverse('course-home:course-navigation', args=[self.course.id])
        response = self.client.get(url)
        assert response.status_code == 200

        sequence_data = response.data['blocks'][str(self.sequential.location)]
        vertical_data = response.data['blocks'][str(self.vertical.location)]
        assert sequence_data['complete']
        assert vertical_data['complete']

    @ddt.data(True, False)
    def test_blocks_complete_with_problem(self, problem_complete):
        self.add_blocks_to_course()
        problem = BlockFactory.create(parent=self.vertical, category='problem', graded=True, has_score=True)
        CourseEnrollment.enroll(self.user, self.course.id)
        self.create_completion(problem, int(problem_complete))

        response = self.client.get(reverse('course-home:course-navigation', args=[self.course.id]))

        sequence_data = response.data['blocks'][str(self.sequential.location)]
        vertical_data = response.data['blocks'][str(self.vertical.location)]

        assert sequence_data['complete'] == problem_complete
        assert vertical_data['complete'] == problem_complete

    @ddt.data(
        # In the following tests, the library is treated as an aggregate block. The library completion does not matter.
        (False, False, False, False),  # Nothing is completed.
        (True, False, False, True),  # Only the problem is completed.
        (False, True, False, False),  # Only the library is completed.
        (True, True, False, True),  # Both the library and the problem are completed.
        # In the following tests, the library is treated as a completable block. The problem completion does not matter.
        (False, False, True, False),  # Nothing is completed.
        (True, False, True, False),  # Only the problem is completed.
        (False, True, True, True),  # Only the library is completed.
        (True, True, True, True),  # Both the library and the problem are completed.
    )
    @ddt.unpack
    def test_blocks_complete_with_library_content_block(
        self, problem_complete, library_complete, library_complete_on_view, expected
    ):
        """
        Test that the API checks the children completion only when the XBlock's completion mode is `AGGREGATOR`.

        The completion of the `COMPLETABLE` XBlocks should not depend on the completion of their children.
        """
        self.add_blocks_to_course()
        library = BlockFactory.create(parent=self.vertical, category='library_content', graded=True, has_score=True)
        problem = BlockFactory.create(parent=library, category='problem', graded=True, has_score=True)
        CourseEnrollment.enroll(self.user, self.course.id)
        self.create_completion(problem, int(problem_complete))
        self.create_completion(library, int(library_complete))

        with override_settings(
            FEATURES={**settings.FEATURES, 'MARK_LIBRARY_CONTENT_BLOCK_COMPLETE_ON_VIEW': library_complete_on_view}
        ):
            response = self.client.get(reverse('course-home:course-navigation', args=[self.course.id]))

        sequence_data = response.data['blocks'][str(self.sequential.location)]
        vertical_data = response.data['blocks'][str(self.vertical.location)]

        assert sequence_data['complete'] == expected
        assert vertical_data['complete'] == expected

    def test_blocks_completion_stat(self):
        """
        Test that the API returns the correct completion statistics for the blocks.
        """
        self.add_blocks_to_course()
        completed_problem = BlockFactory.create(parent=self.vertical, category='problem', graded=True, has_score=True)
        uncompleted_problem = BlockFactory.create(parent=self.vertical, category='problem', graded=True, has_score=True)
        update_outline_from_modulestore(self.course.id)
        CourseEnrollment.enroll(self.user, self.course.id)
        self.create_completion(completed_problem, 1)
        self.create_completion(uncompleted_problem, 0)
        response = self.client.get(reverse('course-home:course-navigation', args=[self.course.id]))

        expected_sequence_completion_stat = {
            'completion': 0,
            'completable_children': 1,
        }
        expected_vertical_completion_stat = {
            'completion': 1,
            'completable_children': 2,
        }
        sequence_data = response.data['blocks'][str(self.sequential.location)]
        vertical_data = response.data['blocks'][str(self.vertical.location)]

        assert not sequence_data['complete']
        assert not vertical_data['complete']
        assert sequence_data['completion_stat'] == expected_sequence_completion_stat
        assert vertical_data['completion_stat'] == expected_vertical_completion_stat

    def test_blocks_completion_stat_all_problem_completed(self):
        """
        Test that the API returns the correct completion statistics for the blocks when all problems are completed.
        """
        self.add_blocks_to_course()
        problem1 = BlockFactory.create(parent=self.vertical, category='problem', graded=True, has_score=True)
        problem2 = BlockFactory.create(parent=self.vertical, category='problem', graded=True, has_score=True)
        update_outline_from_modulestore(self.course.id)
        CourseEnrollment.enroll(self.user, self.course.id)
        self.create_completion(problem1, 1)
        self.create_completion(problem2, 1)
        response = self.client.get(reverse('course-home:course-navigation', args=[self.course.id]))

        expected_sequence_completion_stat = {
            'completion': 1,
            'completable_children': 1,
        }
        expected_vertical_completion_stat = {
            'completion': 2,
            'completable_children': 2,
        }
        sequence_data = response.data['blocks'][str(self.sequential.location)]
        vertical_data = response.data['blocks'][str(self.vertical.location)]

        assert sequence_data['complete']
        assert vertical_data['complete']
        assert sequence_data['completion_stat'] == expected_sequence_completion_stat
        assert vertical_data['completion_stat'] == expected_vertical_completion_stat

    @ddt.data(
        (['html'], 'other'),
        (['html', 'video'], 'video'),
        (['html', 'video', 'problem'], 'problem'),
    )
    @ddt.unpack
    def test_vertical_icon(self, block_categories, expected_icon):
        """Test that the API checks the children `category` to determine the icon for the unit."""
        self.add_blocks_to_course()
        CourseEnrollment.enroll(self.user, self.course.id)

        for category in block_categories:
            BlockFactory.create(parent=self.vertical, category=category)

        response = self.client.get(reverse('course-home:course-navigation', args=[self.course.id]))
        vertical_data = response.data['blocks'][str(self.vertical.location)]

        assert vertical_data['icon'] == expected_icon

    @patch('xmodule.html_block.HtmlBlock.icon_class', 'video')
    def test_vertical_icon_determined_by_icon_class(self):
        """Test that the API checks the children `icon_class` to determine the icon for the unit."""
        self.add_blocks_to_course()
        CourseEnrollment.enroll(self.user, self.course.id)

        BlockFactory.create(parent=self.vertical, category='html')
        response = self.client.get(reverse('course-home:course-navigation', args=[self.course.id]))
        vertical_data = response.data['blocks'][str(self.vertical.location)]
        assert vertical_data['icon'] == 'video'
