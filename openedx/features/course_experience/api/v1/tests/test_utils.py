"""
Tests utils of course expirience feature.
"""
import datetime

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.util.testing import EventTestMixin
from lms.djangoapps.course_home_api.tests.utils import BaseCourseHomeTests
from lms.djangoapps.courseware.tests.helpers import MasqueradeMixin
from openedx.core.djangoapps.schedules.models import Schedule
from openedx.features.course_experience.api.v1.utils import (
    reset_deadlines_for_course,
    reset_course_deadlines_for_user,
    reset_bulk_course_deadlines
)
from xmodule.modulestore.tests.factories import CourseFactory


class TestResetDeadlinesForCourse(EventTestMixin, BaseCourseHomeTests, MasqueradeMixin):
    """
    Tests for reset deadlines endpoint.
    """
    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp("openedx.features.course_experience.api.v1.utils.tracker")
        self.course = CourseFactory.create(self_paced=True, start=timezone.now() - datetime.timedelta(days=1000))

    def test_reset_deadlines_for_course(self):
        enrollment = CourseEnrollment.enroll(self.user, self.course.id, CourseMode.VERIFIED)
        enrollment.schedule.start_date = timezone.now() - datetime.timedelta(days=100)
        enrollment.schedule.save()

        request = APIRequestFactory().post(
            reverse("course-experience-reset-course-deadlines"), {"course_key": self.course.id}
        )
        request.user = self.user

        reset_deadlines_for_course(request, self.course.id, {})

        assert enrollment.schedule.start_date < Schedule.objects.get(id=enrollment.schedule.id).start_date
        self.assert_event_emitted(
            "edx.ui.lms.reset_deadlines.clicked",
            courserun_key=str(self.course.id),
            is_masquerading=False,
            is_staff=False,
            org_key=self.course.org,
            user_id=self.user.id,
        )

    def test_reset_deadlines_with_masquerade(self):
        """Staff users should be able to masquerade as a learner and reset the learner's schedule"""
        student_username = self.user.username
        student_user_id = self.user.id
        student_enrollment = CourseEnrollment.enroll(self.user, self.course.id)
        student_enrollment.schedule.start_date = timezone.now() - datetime.timedelta(days=100)
        student_enrollment.schedule.save()

        staff_enrollment = CourseEnrollment.enroll(self.staff_user, self.course.id)
        staff_enrollment.schedule.start_date = timezone.now() - datetime.timedelta(days=30)
        staff_enrollment.schedule.save()

        self.switch_to_staff()
        self.update_masquerade(course=self.course, username=student_username)

        request = APIRequestFactory().post(
            reverse("course-experience-reset-course-deadlines"), {"course_key": self.course.id}
        )
        request.user = self.staff_user
        request.session = self.client.session

        reset_deadlines_for_course(request, self.course.id, {})

        updated_schedule = Schedule.objects.get(id=student_enrollment.schedule.id)
        assert updated_schedule.start_date.date() == datetime.datetime.today().date()
        updated_staff_schedule = Schedule.objects.get(id=staff_enrollment.schedule.id)
        assert updated_staff_schedule.start_date == staff_enrollment.schedule.start_date
        self.assert_event_emitted(
            "edx.ui.lms.reset_deadlines.clicked",
            courserun_key=str(self.course.id),
            is_masquerading=True,
            is_staff=False,
            org_key=self.course.org,
            user_id=student_user_id,
        )

    def test_reset_course_deadlines_for_user(self):
        """Test the reset_course_deadlines_for_user utility function directly"""
        enrollment = CourseEnrollment.enroll(self.user, self.course.id, CourseMode.VERIFIED)
        enrollment.schedule.start_date = timezone.now() - datetime.timedelta(days=100)
        enrollment.schedule.save()

        result = reset_course_deadlines_for_user(self.user, self.course.id)

        assert result is True
        assert enrollment.schedule.start_date < Schedule.objects.get(id=enrollment.schedule.id).start_date

    def test_reset_bulk_course_deadlines(self):
        """Test the reset_bulk_course_deadlines utility function"""
        enrollment = CourseEnrollment.enroll(self.user, self.course.id, CourseMode.VERIFIED)
        enrollment.schedule.start_date = timezone.now() - datetime.timedelta(days=100)
        enrollment.schedule.save()

        request = APIRequestFactory().post(
            reverse("course-experience-reset-all-course-deadlines"), {}
        )
        request.user = self.user

        success_keys, failed_keys = reset_bulk_course_deadlines(request, [self.course.id], {})

        assert len(success_keys) == 1
        assert self.course.id in success_keys
        assert len(failed_keys) == 0
        assert enrollment.schedule.start_date < Schedule.objects.get(id=enrollment.schedule.id).start_date
