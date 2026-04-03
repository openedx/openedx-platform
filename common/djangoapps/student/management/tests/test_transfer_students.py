"""
Tests the transfer student management command
"""

from unittest.mock import call, patch

import ddt
from django.core.management import call_command
from opaque_keys.edx import locator

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import (
    EVENT_NAME_ENROLLMENT_ACTIVATED,
    EVENT_NAME_ENROLLMENT_DEACTIVATED,
    EVENT_NAME_ENROLLMENT_MODE_CHANGED,
    CourseEnrollment,
)
from common.djangoapps.student.signals import UNENROLL_DONE
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@skip_unless_lms
@ddt.ddt
class TestTransferStudents(ModuleStoreTestCase):
    """
    Tests for transferring students between courses.
    """

    PASSWORD = 'test'
    signal_fired = False

    def setUp(self, **kwargs):  # lint-amnesty, pylint: disable=unused-argument
        """
        Connect a stub receiver, and analytics event tracking.
        """
        super().setUp()

        UNENROLL_DONE.connect(self.assert_unenroll_signal)
        patcher = patch('common.djangoapps.student.models.course_enrollment.tracker')
        self.mock_tracker = patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(UNENROLL_DONE.disconnect, self.assert_unenroll_signal)

    def assert_unenroll_signal(self, skip_refund=False, **kwargs):   # pylint: disable=unused-argument
        """
        Signal Receiver stub for testing that the unenroll signal was fired.
        """
        assert not self.signal_fired
        assert skip_refund
        self.signal_fired = True

    def test_transfer_students(self):
        """
        Verify the transfer student command works as intended.
        """
        student = UserFactory.create()
        student.set_password(self.PASSWORD)
        student.save()
        mode = 'verified'
        # Original Course
        original_course_location = locator.CourseLocator('Org0', 'Course0', 'Run0')
        course = self._create_course(original_course_location)
        # Enroll the student in 'verified'
        CourseEnrollment.enroll(student, course.id, mode='verified')

        # Create and purchase a verified cert for the original course.
        self._create_and_purchase_verified(student, course.id)

        # New Course 1
        course_location_one = locator.CourseLocator('Org1', 'Course1', 'Run1')
        new_course_one = self._create_course(course_location_one)

        # New Course 2
        course_location_two = locator.CourseLocator('Org2', 'Course2', 'Run2')
        new_course_two = self._create_course(course_location_two)
        original_key = str(course.id)
        new_key_one = str(new_course_one.id)
        new_key_two = str(new_course_two.id)

        # Run the actual management command
        call_command(
            'transfer_students',
            '--from', original_key,
            '--to', new_key_one, new_key_two,
        )
        assert self.signal_fired

        # Confirm the analytics event was emitted.
        self.mock_tracker.emit.assert_has_calls(
            [
                call(
                    EVENT_NAME_ENROLLMENT_ACTIVATED,
                    {'course_id': original_key, 'user_id': student.id, 'mode': mode}
                ),
                call(
                    EVENT_NAME_ENROLLMENT_MODE_CHANGED,
                    {'course_id': original_key, 'user_id': student.id, 'mode': mode}
                ),
                call(
                    EVENT_NAME_ENROLLMENT_DEACTIVATED,
                    {'course_id': original_key, 'user_id': student.id, 'mode': mode}
                ),
                call(
                    EVENT_NAME_ENROLLMENT_ACTIVATED,
                    {'course_id': new_key_one, 'user_id': student.id, 'mode': mode}
                ),
                call(
                    EVENT_NAME_ENROLLMENT_MODE_CHANGED,
                    {'course_id': new_key_one, 'user_id': student.id, 'mode': mode}
                ),
                call(
                    EVENT_NAME_ENROLLMENT_ACTIVATED,
                    {'course_id': new_key_two, 'user_id': student.id, 'mode': mode}
                ),
                call(
                    EVENT_NAME_ENROLLMENT_MODE_CHANGED,
                    {'course_id': new_key_two, 'user_id': student.id, 'mode': mode}
                )
            ]
        )
        self.mock_tracker.reset_mock()

        # Confirm the enrollment mode is verified on the new courses, and enrollment is enabled as appropriate.
        assert (mode, False) == CourseEnrollment.enrollment_mode_for_user(student, course.id)
        assert (mode, True) == CourseEnrollment.enrollment_mode_for_user(student, new_course_one.id)
        assert (mode, True) == CourseEnrollment.enrollment_mode_for_user(student, new_course_two.id)

    def _create_course(self, course_location):
        """
        Creates a course
        """
        return CourseFactory.create(
            org=course_location.org,
            number=course_location.course,
            run=course_location.run
        )

    def _create_and_purchase_verified(self, student, course_id):  # lint-amnesty, pylint: disable=unused-argument
        """
        Creates a verified mode for the course and purchases it for the student.
        """
        course_mode = CourseMode(course_id=course_id,
                                 mode_slug='verified',
                                 mode_display_name='verified cert',
                                 min_price=50)
        course_mode.save()
