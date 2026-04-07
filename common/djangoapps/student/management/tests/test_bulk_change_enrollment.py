"""Tests for the bulk_change_enrollment command."""


from unittest.mock import call, patch

import ddt
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import EVENT_NAME_ENROLLMENT_MODE_CHANGED, CourseEnrollment
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@ddt.ddt
class BulkChangeEnrollmentTests(SharedModuleStoreTestCase):
    """Tests for the bulk_change_enrollment command."""

    def setUp(self):
        super().setUp()
        self.org = 'testX'
        self.course = CourseFactory.create(org=self.org)
        self.users = UserFactory.create_batch(5)
        CourseOverview.load_from_module_store(self.course.id)

    @patch('common.djangoapps.student.models.course_enrollment.tracker')
    @ddt.data(('audit', 'honor'), ('honor', 'audit'))
    @ddt.unpack
    def test_bulk_convert(self, from_mode, to_mode, mock_tracker):
        """Verify that enrollments are changed correctly."""
        self._enroll_users(self.course, self.users, from_mode)
        CourseModeFactory(course_id=self.course.id, mode_slug=to_mode)

        # Verify that no users are in the `from` mode yet.
        assert len(CourseEnrollment.objects.filter(mode=to_mode, course_id=self.course.id)) == 0

        args = '--course {course} --from_mode {from_mode} --to_mode {to_mode} --commit'.format(
            course=str(self.course.id),
            from_mode=from_mode,
            to_mode=to_mode
        )

        call_command(
            'bulk_change_enrollment',
            *args.split(' ')
        )

        # Verify that all users have been moved -- if not, this will
        # raise CourseEnrollment.DoesNotExist
        for user in self.users:
            CourseEnrollment.objects.get(mode=to_mode, course_id=self.course.id, user=user)
            self._assert_mode_changed(mock_tracker, self.course, user, to_mode)

    @patch('common.djangoapps.student.models.course_enrollment.tracker')
    @ddt.data(('audit', 'no-id-professional'), ('no-id-professional', 'audit'))
    @ddt.unpack
    def test_bulk_convert_with_org(self, from_mode, to_mode, mock_tracker):
        """Verify that enrollments are changed correctly when org was given."""
        self._enroll_users(self.course, self.users, from_mode)
        CourseModeFactory(course_id=self.course.id, mode_slug=to_mode)

        # Create a second course under the same org
        course_2 = CourseFactory.create(org=self.org)
        CourseModeFactory(course_id=course_2.id, mode_slug=to_mode)
        CourseOverview.load_from_module_store(course_2.id)
        self._enroll_users(course_2, self.users, from_mode)

        # Verify that no users are in the `to` mode yet.
        assert len(CourseEnrollment.objects.filter(mode=to_mode, course_id=self.course.id)) == 0
        assert len(CourseEnrollment.objects.filter(mode=to_mode, course_id=course_2.id)) == 0

        args = '--org {org} --from_mode {from_mode} --to_mode {to_mode} --commit'.format(
            org=self.org,
            from_mode=from_mode,
            to_mode=to_mode
        )

        call_command(
            'bulk_change_enrollment',
            *args.split(' ')
        )

        # Verify that all users have been moved -- if not, this will
        # raise CourseEnrollment.DoesNotExist
        for user in self.users:
            for course in [self.course, course_2]:
                CourseEnrollment.objects.get(mode=to_mode, course_id=course.id, user=user)
                self._assert_mode_changed(mock_tracker, course, user, to_mode)

    def test_with_org_and_course_key(self):
        """Verify that command raises CommandError when `org` and `course_key` both are given."""
        self._enroll_users(self.course, self.users, 'audit')
        CourseModeFactory(course_id=self.course.id, mode_slug='no-id-professional')

        with pytest.raises(CommandError) as err:
            call_command(
                'bulk_change_enrollment',
                org=self.org,
                course=str(self.course.id),
                from_mode='audit',
                to_mode='no-id-professional',
                commit=True,
            )

        assert 'Error: argument -o/--org: not allowed with argument -c/--course' == str(err.value)

    @patch('common.djangoapps.student.models.course_enrollment.tracker')
    def test_with_org_and_invalid_to_mode(self, mock_tracker):
        """Verify that enrollments are changed correctly when org was given."""
        from_mode = 'audit'
        to_mode = 'no-id-professional'
        self._enroll_users(self.course, self.users, from_mode)

        # Create a second course under the same org
        course_2 = CourseFactory.create(org=self.org)
        CourseModeFactory(course_id=course_2.id, mode_slug=to_mode)
        CourseOverview.load_from_module_store(course_2.id)
        self._enroll_users(course_2, self.users, from_mode)

        # Verify that no users are in the `to` mode yet.
        assert len(CourseEnrollment.objects.filter(mode=to_mode, course_id=self.course.id)) == 0
        assert len(CourseEnrollment.objects.filter(mode=to_mode, course_id=course_2.id)) == 0

        args = '--org {org} --from_mode {from_mode} --to_mode {to_mode} --commit'.format(
            org=self.org,
            from_mode=from_mode,
            to_mode=to_mode
        )

        call_command(
            'bulk_change_enrollment',
            *args.split(' ')
        )

        # Verify that users were not moved for the invalid course/mode combination
        for user in self.users:
            with pytest.raises(CourseEnrollment.DoesNotExist):
                CourseEnrollment.objects.get(mode=to_mode, course_id=self.course.id, user=user)

        # Verify that all users have been moved -- if not, this will
        # raise CourseEnrollment.DoesNotExist
        for user in self.users:
            CourseEnrollment.objects.get(mode=to_mode, course_id=course_2.id, user=user)
            self._assert_mode_changed(mock_tracker, course_2, user, to_mode)

    def test_with_invalid_org(self):
        """Verify that command raises CommandError when invalid `org` is given."""
        self._enroll_users(self.course, self.users, 'audit')
        CourseModeFactory(course_id=self.course.id, mode_slug='no-id-professional')

        with pytest.raises(CommandError) as err:
            args = '--org {org} --from_mode {from_mode} --to_mode {to_mode} --commit'.format(
                org='fakeX',
                from_mode='audit',
                to_mode='no-id-professional',
            )

            call_command(
                'bulk_change_enrollment', *args.split(' ')
            )

        assert 'No courses exist for the org "fakeX".' == str(err.value)

    def test_without_commit(self):
        """Verify that nothing happens when the `commit` flag is not given."""
        self._enroll_users(self.course, self.users, 'audit')
        CourseModeFactory(course_id=self.course.id, mode_slug='honor')

        args = '--course {course} --from_mode {from_mode} --to_mode {to_mode}'.format(
            course=str(self.course.id),
            from_mode='audit',
            to_mode='honor'
        )

        call_command(
            'bulk_change_enrollment',
            *args.split(' ')
        )

        # Verify that no users are in the honor mode.
        assert len(CourseEnrollment.objects.filter(mode='honor', course_id=self.course.id)) == 0

    def test_without_to_mode(self):
        """Verify that the command fails when the `to_mode` argument does not exist."""
        self._enroll_users(self.course, self.users, 'audit')
        CourseModeFactory(course_id=self.course.id, mode_slug='audit')

        args = '--course {course} --from_mode {from_mode}'.format(
            course='yolo',
            from_mode='audit'
        )

        with pytest.raises(CommandError) as err:
            call_command(
                'bulk_change_enrollment',
                *args.split(' ')
            )

        assert 'Error: the following arguments are required: -t/--to_mode' == str(err.value)

    @ddt.data('from_mode', 'to_mode', 'course')
    def test_without_options(self, option):
        """Verify that the command fails when some options are not given."""
        command_options = {
            'from_mode': 'audit',
            'to_mode': 'honor',
            'course': str(self.course.id),
        }
        command_options.pop(option)

        with pytest.raises(CommandError):
            call_command('bulk_change_enrollment', **command_options)

    def test_bad_course_id(self):
        """Verify that the command fails when the given course ID does not parse."""
        args = '--course {course} --from_mode {from_mode} --to_mode {to_mode}'.format(
            course='yolo',
            from_mode='audit',
            to_mode='honor'
        )

        with pytest.raises(CommandError) as err:
            call_command('bulk_change_enrollment', *args.split(' '))

        assert 'Course ID yolo is invalid.' == str(err.value)

    def test_nonexistent_course_id(self):
        """Verify that the command fails when the given course does not exist."""
        args = '--course {course} --from_mode {from_mode} --to_mode {to_mode}'.format(
            course='course-v1:testX+test+2016',
            from_mode='audit',
            to_mode='honor'
        )

        with pytest.raises(CommandError) as err:
            call_command(
                'bulk_change_enrollment',
                *args.split(' ')
            )
        assert 'The given course course-v1:testX+test+2016 does not exist.' == str(err.value)

    def _assert_mode_changed(self, mock_tracker, course, user, to_mode):
        """Confirm the analytics event was emitted."""
        mock_tracker.emit.assert_has_calls(
            [
                call(
                    EVENT_NAME_ENROLLMENT_MODE_CHANGED,
                    {'course_id': str(course.id), 'user_id': user.id, 'mode': to_mode}
                ),
            ]
        )

    def _enroll_users(self, course, users, mode):
        """Enroll users in the given mode."""
        for user in users:
            CourseEnrollmentFactory(mode=mode, course_id=course.id, user=user)
