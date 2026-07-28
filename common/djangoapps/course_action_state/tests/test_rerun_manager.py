"""
Tests specific to the CourseRerunState Model and Manager.
"""


from django.test import TestCase
from opaque_keys.edx.locations import CourseLocator

from common.djangoapps.course_action_state.managers import CourseRerunUIStateManager
from common.djangoapps.course_action_state.models import CourseRerunState
from common.djangoapps.student.tests.factories import UserFactory


class TestCourseRerunStateManager(TestCase):
    """
    Test class for testing the CourseRerunUIStateManager.
    """
    def setUp(self):
        super().setUp()
        self.source_course_key = CourseLocator("source_org", "source_course_num", "source_run")
        self.course_key = CourseLocator("test_org", "test_course_num", "test_run")
        self.created_user = UserFactory()
        self.display_name = "destination course name"
        self.expected_rerun_state = {
            'created_user': self.created_user,
            'updated_user': self.created_user,
            'course_key': self.course_key,
            'source_course_key': self.source_course_key,
            "display_name": self.display_name,
            'action': CourseRerunUIStateManager.ACTION,
            'should_display': True,
            'message': "",
        }

    def verify_rerun_state(self):
        """
        Gets the rerun state object for self.course_key and verifies that the values
        of its fields equal self.expected_rerun_state.
        """
        found_rerun = CourseRerunState.objects.find_first(course_key=self.course_key)
        found_rerun_state = {key: getattr(found_rerun, key) for key in self.expected_rerun_state}
        self.assertDictEqual(found_rerun_state, self.expected_rerun_state)  # noqa: PT009
        return found_rerun

    def dismiss_ui_and_verify(self, rerun):
        """
        Updates the should_display field of the rerun state object for self.course_key
        and verifies its new state.
        """
        user_who_dismisses_ui = UserFactory()
        CourseRerunState.objects.update_should_display(
            entry_id=rerun.id,
            user=user_who_dismisses_ui,
            should_display=False,
        )
        self.expected_rerun_state.update({
            'updated_user': user_who_dismisses_ui,
            'should_display': False,
        })
        self.verify_rerun_state()

    def initiate_rerun(self):
        CourseRerunState.objects.initiated(
            source_course_key=self.source_course_key,
            destination_course_key=self.course_key,
            user=self.created_user,
            display_name=self.display_name,
        )

    def test_rerun_initiated(self):
        self.initiate_rerun()
        self.expected_rerun_state.update(
            {'state': CourseRerunUIStateManager.State.IN_PROGRESS}
        )
        self.verify_rerun_state()

    def test_rerun_succeeded(self):
        # initiate
        self.initiate_rerun()

        # set state to succeed
        CourseRerunState.objects.succeeded(course_key=self.course_key)
        self.expected_rerun_state.update({
            'state': CourseRerunUIStateManager.State.SUCCEEDED,
        })
        rerun = self.verify_rerun_state()

        # dismiss ui and verify
        self.dismiss_ui_and_verify(rerun)

    def test_rerun_failed(self):
        # initiate
        self.initiate_rerun()

        # set state to fail
        exception = Exception("failure in rerunning")
        try:
            raise exception
        except:  # pylint: disable=bare-except
            CourseRerunState.objects.failed(course_key=self.course_key)

        self.expected_rerun_state.update(
            {'state': CourseRerunUIStateManager.State.FAILED}
        )
        self.expected_rerun_state.pop('message')
        rerun = self.verify_rerun_state()
        assert str(exception) in rerun.message

        # dismiss ui and verify
        self.dismiss_ui_and_verify(rerun)
