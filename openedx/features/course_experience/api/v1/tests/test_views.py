"""
Tests for reset deadlines endpoint.
"""

import datetime
from unittest import mock

import ddt
from django.urls import reverse
from django.utils import timezone
from edx_toggles.toggles.testutils import override_waffle_flag

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.course_home_api.tests.utils import BaseCourseHomeTests
from lms.djangoapps.courseware.tests.helpers import MasqueradeMixin
from openedx.core.djangoapps.schedules.models import Schedule
from openedx.features.course_experience import RELATIVE_DATES_DISABLE_RESET_FLAG, RELATIVE_DATES_FLAG
from xmodule.modulestore.tests.factories import CourseFactory


@ddt.ddt
class ResetCourseDeadlinesViewTests(BaseCourseHomeTests, MasqueradeMixin):
    """
    Tests for reset deadlines endpoint.
    """
    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.course = CourseFactory.create(self_paced=True, start=timezone.now() - datetime.timedelta(days=1000))

    def test_reset_deadlines(self):
        enrollment = CourseEnrollment.enroll(self.user, self.course.id, CourseMode.VERIFIED)
        enrollment.schedule.start_date = timezone.now() - datetime.timedelta(days=100)
        enrollment.schedule.save()
        # Test body with incorrect body param (course_key is required)
        response = self.client.post(reverse('course-experience-reset-course-deadlines'), {'course': self.course.id})
        assert response.status_code == 400
        assert enrollment.schedule == Schedule.objects.get(id=enrollment.schedule.id)

        # Test correct post body
        response = self.client.post(reverse('course-experience-reset-course-deadlines'), {'course_key': self.course.id})
        assert response.status_code == 200
        assert enrollment.schedule.start_date < Schedule.objects.get(id=enrollment.schedule.id).start_date

    @override_waffle_flag(RELATIVE_DATES_FLAG, active=True)
    @override_waffle_flag(RELATIVE_DATES_DISABLE_RESET_FLAG, active=True)
    def test_reset_deadlines_disabled(self):
        enrollment = CourseEnrollment.enroll(self.user, self.course.id, CourseMode.VERIFIED)
        enrollment.schedule.start_date = timezone.now() - datetime.timedelta(days=100)
        enrollment.schedule.save()

        response = self.client.post(reverse('course-experience-reset-course-deadlines'), {'course_key': self.course.id})
        assert response.status_code == 200
        assert enrollment.schedule == Schedule.objects.get(id=enrollment.schedule.id)

    def test_post_unauthenticated_user(self):
        self.client.logout()
        response = self.client.post(reverse('course-experience-reset-course-deadlines'), {'course_key': self.course.id})
        assert response.status_code == 401

    def test_mobile_get_banner_info(self):
        response = self.client.get(reverse('course-experience-course-deadlines-mobile', args=[self.course.id]))
        assert response.status_code == 200
        self.assertContains(response, 'missed_deadlines')
        self.assertContains(response, 'missed_gated_content')
        self.assertContains(response, 'content_type_gating_enabled')
        self.assertContains(response, 'verified_upgrade_link')

    def test_mobile_get_unknown_course(self):
        url = reverse('course-experience-course-deadlines-mobile', args=['course-v1:unknown+course+2T2020'])
        response = self.client.get(url)
        assert response.status_code == 404

    def test_mobile_get_unauthenticated_user(self):
        self.client.logout()
        response = self.client.get(reverse('course-experience-course-deadlines-mobile', args=[self.course.id]))
        assert response.status_code == 401


class ResetAllRelativeCourseDeadlinesViewTests(BaseCourseHomeTests, MasqueradeMixin):
    """
    Tests for reset all relative deadlines endpoint.
    """

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.course = CourseFactory.create(self_paced=True, start=timezone.now() - datetime.timedelta(days=1000))
        self.enrollment = CourseEnrollment.enroll(self.user, self.course.id, CourseMode.VERIFIED)
        self.enrollment.schedule.start_date = timezone.now() - datetime.timedelta(days=100)
        self.enrollment.schedule.save()

    def test_reset_all_course_deadlines(self):
        """
        Test reset all course deadlines endpoint
        """
        response = self.client.post(
            reverse("course-experience-reset-all-course-deadlines"),
            {},
        )
        assert response.status_code == 200
        assert self.enrollment.schedule.start_date < Schedule.objects.get(id=self.enrollment.schedule.id).start_date
        assert str(self.course.id) in response.data.get("success_course_keys")

    def test_reset_all_course_deadlines_failure(self):
        """
        Raise exception on reset_bulk_course_deadlines and assert if failure course id is returned
        """
        with mock.patch(
            "openedx.features.course_experience.api.v1.views.reset_bulk_course_deadlines",
            return_value=([], [self.course.id]),
        ):
            response = self.client.post(reverse("course-experience-reset-all-course-deadlines"), {})

            assert response.status_code == 200
            assert str(self.course.id) in response.data.get("failed_course_keys")

    def test_post_unauthenticated_user(self):
        """
        Test reset all relative course deadlines endpoint for unauthenticated user
        """
        self.client.logout()
        response = self.client.post(
            reverse("course-experience-reset-all-course-deadlines"),
            {},
        )
        assert response.status_code == 401
