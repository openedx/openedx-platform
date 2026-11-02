"""
Tests for the Course Home Course Metadata API in the Course Home API
"""
import json
from unittest.mock import patch

import ddt
from django.db import transaction
from django.test import override_settings
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag
from openedx_filters import PipelineStep
from openedx_filters.learning.filters import CoursewareAccessChecksRequested

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import (
    CourseBetaTesterRole,
    CourseInstructorRole,
    CourseLimitedStaffRole,
    CourseStaffRole
)
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.course_home_api.tests.utils import BaseCourseHomeTests
from lms.djangoapps.courseware.toggles import (
    COURSEWARE_MFE_MILESTONES_STREAK_DISCOUNT,
    COURSEWARE_MICROFRONTEND_PROGRESS_MILESTONES,
    COURSEWARE_MICROFRONTEND_PROGRESS_MILESTONES_STREAK_CELEBRATION
)
from openedx.core.djangoapps.discussions.models import DiscussionsConfiguration


class TestCourseHomeMetadataAPIRenderStartedPipelineStep(PipelineStep):
    """
    Utility pipeline step that adds a field to the course home metadata.
    """

    def run_filter(self, user, course_key, course_home_metadata):  # pylint: disable=arguments-differ
        """Pipeline step that adds a field built from the learner and the course."""
        course_home_metadata['plugin_field'] = {'course_id': str(course_key), 'username': user.username}
        return {'course_home_metadata': course_home_metadata}


TEST_RENDER_FILTERS_CONFIG = {
    'org.openedx.learning.course_home.metadata.api.rendered.started.v1': {
        'pipeline': [f'{__name__}.{TestCourseHomeMetadataAPIRenderStartedPipelineStep.__name__}'],
        'fail_silently': False,
    },
}


@ddt.ddt
@override_waffle_flag(COURSEWARE_MICROFRONTEND_PROGRESS_MILESTONES, active=True)
@override_waffle_flag(COURSEWARE_MICROFRONTEND_PROGRESS_MILESTONES_STREAK_CELEBRATION, active=True)
class CourseHomeMetadataTests(BaseCourseHomeTests):
    """
    Tests for the Course Home Course Metadata API
    """
    def setUp(self):
        super().setUp()
        self.url = reverse('course-home:course-metadata', args=[self.course.id])
        self.staff_user = UserFactory(
            username='staff',
            email='staff@example.com',
            password='bar',
            is_staff=True
        )

    def test_get_authenticated_user(self):
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.VERIFIED)
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert not response.data.get('is_staff')
        # 'Course', and 'Progress' tabs
        assert len(response.data.get('tabs', [])) == 3

    @ddt.data(True, False)
    def test_get_authenticated_not_enrolled(self, has_previously_enrolled):
        if has_previously_enrolled:
            # Create an enrollment, then unenroll to set is_active to False
            CourseEnrollment.enroll(self.user, self.course.id)
            CourseEnrollment.unenroll(self.user, self.course.id)
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert response.data['is_enrolled'] is False

    def test_get_authenticated_staff_user(self):
        self.client.logout()
        self.client.login(username=self.staff_user.username, password='bar')
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert response.data['is_staff']
        # This differs for a staff user because they also receive the Instructor tab
        # 'Course', 'Progress', and 'Instructor' tabs
        assert len(response.data.get('tabs', [])) == 4

    def test_get_masqueraded_user(self):
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.VERIFIED)

        self.client.logout()
        self.client.login(username=self.staff_user.username, password='bar')

        # Sanity check on our normal staff user
        assert self.client.get(self.url).data['username'] == self.staff_user.username

        # Now switch users and confirm we get a different result
        self.update_masquerade(username=self.user.username)
        assert self.client.get(self.url).data['username'] == self.user.username

    def test_get_unknown_course(self):
        self.client.logout()
        url = reverse('course-home:course-metadata', args=['course-v1:unknown+course+2T2020'])
        # Django TestCase wraps every test in a transaction, so we must specifically wrap this when we expect an error
        with transaction.atomic():
            response = self.client.get(url)
        assert response.status_code == 404

    def _assert_course_access_response(self, response, expect_course_access, expected_error_code):
        """
        Responsible to asset the course_access response with expected values.
        """
        assert response.status_code == 200
        assert response.data['course_access']['has_access'] == expect_course_access
        assert response.data['course_access']['error_code'] == expected_error_code
        # Start date is used when handling some errors, so make sure it is present too
        assert response.data['start'] == self.course.start.isoformat() + 'Z'

    def test_streak_data_in_response(self):
        """ Test that metadata endpoint returns data for the streak celebration """
        CourseEnrollment.enroll(self.user, self.course.id, 'audit')
        with override_waffle_flag(COURSEWARE_MFE_MILESTONES_STREAK_DISCOUNT, active=True):
            UPDATES_METHOD_NAME = 'common.djangoapps.student.models.user.UserCelebration.perform_streak_updates'
            with patch(UPDATES_METHOD_NAME, return_value=3):
                response = self.client.get(self.url, content_type='application/json')
                celebrations = response.json()['celebrations']
                assert celebrations['streak_length_to_celebrate'] == 3
                assert celebrations['streak_discount_enabled'] is True

    @ddt.data(
        # Who has access to MFE courseware?
        {
            # Enrolled learners should have access.
            'enroll_user': True,
            'instructor_role': False,
            'masquerade_role': None,
            'filter_denies_access': False,
            'expect_course_access': True,
            'error_code': None,
        },
        {
            # Un-enrolled learners should NOT have access.
            'enroll_user': False,
            'instructor_role': False,
            'masquerade_role': None,
            'filter_denies_access': False,
            'expect_course_access': False,
            'error_code': 'enrollment_required'
        },
        {
            # Un-enrolled instructors should have access.
            'enroll_user': False,
            'instructor_role': True,
            'masquerade_role': None,
            'filter_denies_access': False,
            'expect_course_access': True,
            'error_code': None
        },
        {
            # Un-enrolled instructors masquerading as students should have access.
            'enroll_user': False,
            'instructor_role': True,
            'masquerade_role': 'student',
            'filter_denies_access': False,
            'expect_course_access': True,
            'error_code': None
        },
        {
            # Learners denied by an access-checks pipeline step should NOT have access.
            'enroll_user': True,
            'instructor_role': False,
            'masquerade_role': None,
            'filter_denies_access': True,
            'expect_course_access': False,
            'error_code': 'access_denied_by_filter'
        },
        {
            # Staff denied by an access-checks pipeline step should NOT have access.
            'enroll_user': True,
            'instructor_role': True,
            'masquerade_role': None,
            'filter_denies_access': True,
            'expect_course_access': False,
            'error_code': 'access_denied_by_filter'
        }
    )
    @ddt.unpack
    def test_course_access(
        self, enroll_user, instructor_role, masquerade_role, filter_denies_access, expect_course_access, error_code
    ):
        """
        Test that course_access is calculated correctly based on
        access to MFE and access to the course itself.
        """
        if enroll_user:
            CourseEnrollment.enroll(self.user, self.course.id, 'audit')
        if instructor_role:
            CourseInstructorRole(self.course.id).add_users(self.user)
        if masquerade_role:
            self.update_masquerade(role=masquerade_role)

        if filter_denies_access:
            mock_side_effect = CoursewareAccessChecksRequested.PreventCoursewareAccess(
                message='Access denied by a courseware access-checks pipeline step',
                error_code='access_denied_by_filter',
                developer_message='https://example.com/redirect',
                user_message='You are not allowed to access this course',
            )
            with patch(
                'openedx_filters.learning.filters.CoursewareAccessChecksRequested.run_filter',
                side_effect=mock_side_effect,
            ):
                response = self.client.get(self.url)
        else:
            response = self.client.get(self.url)

        self._assert_course_access_response(response, expect_course_access, error_code)

    @patch.dict("django.conf.settings.FEATURES", {"ENABLE_DISCUSSION_SERVICE": True})
    @ddt.data(True, False)
    def test_discussion_tab_visible(self, visible):
        """
        Tests if discussion tab is visible based on Configuration
        """
        CourseInstructorRole(self.course.id).add_users(self.user)
        configuration = DiscussionsConfiguration.get(context_key=self.course.id)
        configuration.enabled = visible
        configuration.save()
        response = self.client.get(self.url)
        data = json.loads(response.content.decode())
        tab_ids = [tab['tab_id'] for tab in data['tabs']]
        if visible:
            assert 'discussion' in tab_ids
        else:
            assert 'discussion' not in tab_ids

    @ddt.data(
        {
            'course_team_role': None,
            'has_course_author_access': False
        },
        {
            'course_team_role': CourseBetaTesterRole,
            'has_course_author_access': False
        },
        {
            'course_team_role': CourseStaffRole,
            'has_course_author_access': True
        },
        {
            'course_team_role': CourseLimitedStaffRole,
            'has_course_author_access': False
        },
    )
    @ddt.unpack
    def test_has_course_author_access_for_staff_roles(self, course_team_role, has_course_author_access):
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.VERIFIED)

        if course_team_role:
            course_team_role(self.course.id).add_users(self.user)

        response = self.client.get(self.url)
        assert response.status_code == 200
        assert response.data['has_course_author_access'] == has_course_author_access

    @override_settings(OPEN_EDX_FILTERS_CONFIG=TEST_RENDER_FILTERS_CONFIG)
    def test_course_home_metadata_render_filter(self):
        """
        A configured CourseHomeMetadataAPIRenderStarted step can add fields to the response.
        """
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert response.data['plugin_field'] == {
            'course_id': str(self.course.id),
            'username': self.user.username,
        }
        assert response.data['course_id'] == str(self.course.id)

    @override_settings(OPEN_EDX_FILTERS_CONFIG=TEST_RENDER_FILTERS_CONFIG)
    def test_course_home_metadata_render_filter_gets_masqueraded_user(self):
        """
        The filter gets the masqueraded learner, not the staff user.
        """
        CourseEnrollment.enroll(self.user, self.course.id, CourseMode.VERIFIED)
        self.client.logout()
        self.client.login(username=self.staff_user.username, password='bar')
        self.update_masquerade(username=self.user.username)
        response = self.client.get(self.url)
        assert response.data['plugin_field']['username'] == self.user.username
