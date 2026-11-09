"""
Tests for Dates Tab API in the Course Home API
"""

from datetime import datetime

import ddt
from django.test import override_settings
from django.urls import reverse
from openedx_filters import PipelineStep

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.course_home_api.tests.utils import BaseCourseHomeTests
from openedx.features.content_type_gating.models import ContentTypeGatingConfig

filter_users = []


class TestReplaceDatesDataStep(PipelineStep):
    """
    Pipeline step that records the user and replaces the response data.
    """

    def run_filter(self, course_key, user, data):  # pylint: disable=arguments-differ
        filter_users.append(user)
        return {'data': {'course_key': str(course_key)}}


@ddt.ddt
class DatesTabTestViews(BaseCourseHomeTests):
    """
    Tests for the Dates Tab API
    """
    def setUp(self):
        super().setUp()
        self.url = reverse('course-home:dates-tab', args=[self.course.id])
        ContentTypeGatingConfig.objects.create(enabled=True, enabled_as_of=datetime(2017, 1, 1))

    @ddt.data(CourseMode.AUDIT, CourseMode.VERIFIED)
    def test_get_authenticated_enrolled_user(self, enrollment_mode):
        CourseEnrollment.enroll(self.user, self.course.id, enrollment_mode)
        response = self.client.get(self.url)
        assert response.status_code == 200

        # Pulling out the date blocks to check learner has access.
        date_blocks = response.data.get('course_date_blocks')
        assert response.data.get('learner_is_full_access') == (enrollment_mode == CourseMode.VERIFIED)
        assert all(block.get('learner_has_access') for block in date_blocks)

    @ddt.data(True, False)
    def test_get_authenticated_user_not_enrolled(self, has_previously_enrolled):
        if has_previously_enrolled:
            # Create an enrollment, then unenroll to set is_active to False
            CourseEnrollment.enroll(self.user, self.course.id)
            CourseEnrollment.unenroll(self.user, self.course.id)
        response = self.client.get(self.url)
        assert response.status_code == 401

    def test_get_unauthenticated_user(self):
        self.client.logout()
        response = self.client.get(self.url)
        assert response.status_code == 401

    def test_get_unknown_course(self):
        url = reverse('course-home:dates-tab', args=['course-v1:unknown+course+2T2020'])
        response = self.client.get(url)
        assert response.status_code == 404

    def test_banner_data_is_returned(self):
        CourseEnrollment.enroll(self.user, self.course.id)
        response = self.client.get(self.url)
        assert response.status_code == 200
        self.assertContains(response, 'missed_deadlines')
        self.assertContains(response, 'missed_gated_content')
        self.assertContains(response, 'content_type_gating_enabled')
        self.assertContains(response, 'verified_upgrade_link')

    def test_masquerade(self):
        self.switch_to_staff()
        CourseEnrollment.enroll(self.user, self.course.id, 'audit')
        assert self.client.get(self.url).data.get('learner_is_full_access')

        self.update_masquerade(role='student')
        assert not self.client.get(self.url).data.get('learner_is_full_access')

    @override_settings(OPEN_EDX_FILTERS_CONFIG={
        'org.openedx.learning.course_home_dates.api.render.started.v1': {
            'pipeline': [f'{__name__}.TestReplaceDatesDataStep'],
            'fail_silently': False,
        },
    })
    def test_render_filter_receives_masqueraded_user(self):
        """
        Test that the render filter can replace the response and receives the masqueraded user.
        """
        learner = UserFactory()
        CourseEnrollment.enroll(learner, self.course.id)
        self.switch_to_staff()
        self.update_masquerade(username=learner.username)
        filter_users.clear()

        response = self.client.get(self.url)

        assert response.status_code == 200
        assert response.data == {'course_key': str(self.course.id)}
        assert filter_users == [learner]
