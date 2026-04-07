"""
Unit tests for course_goals djangoapp
"""

import json
import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.test.utils import override_settings
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag  # lint-amnesty, pylint: disable=wrong-import-order
from rest_framework.test import APIClient

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.course_goals.models import CourseGoal
from lms.djangoapps.course_home_api.tests.utils import BaseCourseHomeTests
from openedx.features.course_experience import ENABLE_COURSE_GOALS
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

EVENT_NAME_ADDED = 'edx.course.goal.added'
EVENT_NAME_UPDATED = 'edx.course.goal.updated'

User = get_user_model()


@override_waffle_flag(ENABLE_COURSE_GOALS, active=True)
class TestCourseGoalsAPI(SharedModuleStoreTestCase):
    """
    Testing the Course Goals API.
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(emit_signals=True)

        self.user = UserFactory.create(
            username='john', email='lennon@thebeatles.com', password='password',
        )
        CourseEnrollment.enroll(self.user, self.course.id)

        self.client = APIClient(enforce_csrf_checks=True)
        self.client.login(username=self.user.username, password=self.user.password)
        self.client.force_authenticate(user=self.user)

        self.apiUrl = reverse('course-home:save-course-goal')

    def save_course_goal(self, number, subscribed):
        """
        Sends a post request to set a course goal and returns the response.
        """
        post_data = {
            'course_id': str(self.course.id),
            'user': self.user.username,
        }
        if number is not None:
            post_data['days_per_week'] = number
        if subscribed is not None:
            post_data['subscribed_to_reminders'] = subscribed

        response = self.client.post(self.apiUrl, json.dumps(post_data), content_type='application/json')
        return response

    @mock.patch('lms.djangoapps.course_goals.handlers.segment.track')
    @override_settings(LMS_SEGMENT_KEY="foobar")
    def test_add_goal(self, segment_call):
        """ Ensures a correctly formatted post succeeds."""
        self.save_course_goal(1, True)
        segment_call.assert_called_once_with(self.user.id, EVENT_NAME_ADDED, {
            'courserun_key': str(self.course.id),
            'days_per_week': 1,
            'subscribed_to_reminders': True,
        })

        current_goals = CourseGoal.objects.filter(user=self.user, course_key=self.course.id)
        assert len(current_goals) == 1
        assert current_goals[0].days_per_week == 1
        assert current_goals[0].subscribed_to_reminders is True

    @mock.patch('lms.djangoapps.course_goals.handlers.segment.track')
    @override_settings(LMS_SEGMENT_KEY="foobar")
    def test_update_goal(self, segment_call):
        """ Ensures that repeatedly saving a course goal does not create new instances of the goal. """
        self.save_course_goal(1, True)
        segment_call.assert_called_with(self.user.id, EVENT_NAME_ADDED, {
            'courserun_key': str(self.course.id),
            'days_per_week': 1,
            'subscribed_to_reminders': True,
        })

        self.save_course_goal(3, True)
        segment_call.assert_called_with(self.user.id, EVENT_NAME_UPDATED, {
            'courserun_key': str(self.course.id),
            'days_per_week': 3,
            'subscribed_to_reminders': True,
        })

        self.save_course_goal(5, False)
        segment_call.assert_called_with(self.user.id, EVENT_NAME_UPDATED, {
            'courserun_key': str(self.course.id),
            'days_per_week': 5,
            'subscribed_to_reminders': False,
        })

        current_goals = CourseGoal.objects.filter(user=self.user, course_key=self.course.id)
        assert len(current_goals) == 1
        assert current_goals[0].days_per_week == 5
        assert current_goals[0].subscribed_to_reminders is False

    def test_add_without_subscribed_to_reminders(self):
        """ Ensures if required arguments are not provided, post does not succeed. """
        response = self.save_course_goal(1, None)
        assert len(CourseGoal.objects.filter(user=self.user, course_key=self.course.id)) == 0
        self.assertContains(
            response=response,
            text="'subscribed_to_reminders' is required.",
            status_code=400
        )

    def test_add_without_days_per_week(self):
        """ Allow unsubscribing without providing the days_per_week argument """
        response = self.save_course_goal(1, True)

        current_goals = CourseGoal.objects.filter(user=self.user, course_key=self.course.id)
        assert len(current_goals) == 1
        assert current_goals[0].subscribed_to_reminders is True

        response = self.save_course_goal(None, False)
        current_goals = CourseGoal.objects.filter(user=self.user, course_key=self.course.id)
        assert current_goals[0].subscribed_to_reminders is False

    def test_add_invalid_goal(self):
        """ Ensures an incorrectly formatted post does not succeed. """
        response = self.save_course_goal('notnumber', False)
        assert response.status_code == 400
        assert len(CourseGoal.objects.filter(user=self.user, course_key=self.course.id)) == 0


class TestUnsubscribeAPI(BaseCourseHomeTests):
    """
    Testing the unsubscribe API.
    """
    def unsubscribe(self, token):
        url = reverse('course-home:unsubscribe-from-course-goal', kwargs={'token': token})
        return self.client.post(url)

    def make_goal(self, course_key, **kwargs) -> CourseGoal:
        return CourseGoal.objects.create(user=self.user, course_key=course_key, **kwargs)

    def test_happy_path(self):
        goal = self.make_goal(self.course.id, subscribed_to_reminders=True)
        goal2 = self.make_goal('course-v1:foo+bar+2T2020', subscribed_to_reminders=True)  # a control group

        def unsubscribe_and_check():
            response = self.unsubscribe(goal.unsubscribe_token)
            goal.refresh_from_db()
            goal2.refresh_from_db()
            assert response.status_code == 200
            assert not goal.subscribed_to_reminders
            assert goal2.subscribed_to_reminders
            assert response.json() == {'course_title': self.course.display_name}

        unsubscribe_and_check()

        # Unsubscribe again to confirm that we're not like, toggling the subscription status or anything
        unsubscribe_and_check()

    def test_bad_token(self):
        response = self.unsubscribe(uuid.uuid4())
        assert response.status_code == 404

    def test_bad_course(self):
        goal = self.make_goal('course-v1:foo+bar+2T2020')
        response = self.unsubscribe(goal.unsubscribe_token)
        assert response.status_code == 404
