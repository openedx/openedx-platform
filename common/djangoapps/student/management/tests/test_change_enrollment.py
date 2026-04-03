""" Test the change_enrollment command line script."""


from unittest.mock import patch

import ddt
from django.core.management import call_command

from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@ddt.ddt
class ChangeEnrollmentTests(SharedModuleStoreTestCase):
    """ Test the enrollment change functionality of the change_enrollment script."""
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.audit_mode = CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug='audit',
            mode_display_name='Audit',
        )
        self.honor_mode = CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug='honor',
            mode_display_name='Honor',
        )

        self.user_info = [
            ('amy', 'amy@pond.com', 'password'),
            ('rory', 'rory@theroman.com', 'password'),
            ('river', 'river@song.com', 'password')
        ]
        self.enrollments = []
        self.users = []
        for username, email, password in self.user_info:
            user = UserFactory.create(username=username, email=email, password=password)
            self.users.append(user)
            self.enrollments.append(CourseEnrollment.enroll(user, self.course.id, mode='audit'))

    @patch('common.djangoapps.student.management.commands.change_enrollment.logger')
    @ddt.data(
        ('email', False, 3),
        ('username', False, 3),
        ('email', True, 0),
        ('username', True, 0),
    )
    @ddt.unpack
    def test_convert_users(self, method, noop, expected_conversions, mock_logger):
        """ The command should update the user's enrollment. """
        user_str = ','.join([getattr(user, method) for user in self.users])
        user_ids = [u.id for u in self.users]

        # Verify users are not in honor mode yet
        assert len(CourseEnrollment.objects.filter(mode='honor', user_id__in=user_ids)) == 0

        noop = " --noop" if noop else ""

        # Hack around call_command bugs dealing with required options see:
        # https://stackoverflow.com/questions/32036562/call-command-argument-is-required
        command_args = '--course {course} --to honor --from audit --{method} {user_str}{noop}'.format(
            course=str(self.course.id),
            noop=noop,
            method=method,
            user_str=user_str
        )

        call_command('change_enrollment', *command_args.split(' '))

        # Verify correct number of users are now in honor mode
        assert len(CourseEnrollment.objects.filter(mode='honor', user_id__in=user_ids)) == expected_conversions

        mock_logger.info.assert_called_with(
            'Successfully updated %i out of %i users',
            len(self.users),
            len(self.users)
        )

    @patch('common.djangoapps.student.management.commands.change_enrollment.logger')
    @ddt.data(
        ('email', 'dtennant@thedoctor.com', 3),
        ('username', 'dtennant', 3),
    )
    @ddt.unpack
    def test_user_not_found(self, method, fake_user, expected_success, mock_logger):
        all_users = [getattr(user, method) for user in self.users]
        all_users.append(fake_user)
        user_str = ','.join(all_users)
        real_user_ids = [u.id for u in self.users]

        # Verify users are not in honor mode yet
        assert len(CourseEnrollment.objects.filter(mode='honor', user_id__in=real_user_ids)) == 0

        command_args = '--course {course} --to honor --from audit --{method} {user_str}'.format(
            course=str(self.course.id),
            method=method,
            user_str=user_str
        )

        call_command('change_enrollment', *command_args.split(' '))

        # Verify correct number of users are now in honor mode
        assert len(CourseEnrollment.objects.filter(mode='honor', user_id__in=real_user_ids)) == expected_success

        mock_logger.info.assert_called_with(
            'user: [%s] reason: [%s] %s', fake_user, 'DoesNotExist', 'User matching query does not exist.'
        )
