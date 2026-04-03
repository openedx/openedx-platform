""" Test the change_eligibility_deadline command line script."""


from datetime import datetime, timedelta

import pytest
from django.core.management import call_command
from opaque_keys import InvalidKeyError
from testfixtures import LogCapture

from common.djangoapps.course_modes.tests.factories import CourseMode
from common.djangoapps.student.models import CourseEnrollment, User
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.credit.models import CreditCourse, CreditEligibility
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

LOGGER_NAME = 'common.djangoapps.student.management.commands.change_eligibility_deadline'
command_args = '--username {username} --course {course} --date {date}'


class ChangeEligibilityDeadlineTests(SharedModuleStoreTestCase):
    """ Test the deadline change functionality of the change_eligibility_deadline script."""

    def setUp(self):
        """ Initial set up for tests """
        super().setUp()
        self.course = CourseFactory.create()

        self.enrolled_user = UserFactory.create(username='amy', email='amy@pond.com', password='password')
        CourseEnrollment.enroll(self.enrolled_user, self.course.id, mode=CourseMode.CREDIT_MODE).save()

        credit_course = CreditCourse.objects.create(course_key=self.course.id)
        self.credit_eligibility = CreditEligibility.objects.create(
            username=self.enrolled_user.username,
            course=credit_course,
        )
        self.credit_eligibility.deadline = datetime.strptime('2013-12-30', '%Y-%m-%d')
        self.credit_eligibility.save()

    def test_invalid_command_arguments(self):
        """ Test command with invalid arguments """
        course_id_str = str(self.course.id)
        username = self.enrolled_user.username

        # Incorrect username
        with pytest.raises(User.DoesNotExist):
            call_command('change_eligibility_deadline',
                         *command_args.format(username='XYZ', course=course_id_str, date='2018-12-30').split(' ')
                         )
        # Incorrect course id
        with pytest.raises(InvalidKeyError):
            call_command('change_eligibility_deadline',
                         *command_args.format(username=username, course='XYZ', date='2018-12-30').split(' ')
                         )
        # Student not enrolled
        with pytest.raises(CourseEnrollment.DoesNotExist):
            unenrolled_user = UserFactory.create()
            call_command('change_eligibility_deadline',
                         *command_args.format(username=unenrolled_user.username, course=course_id_str,
                                              date='2018-12-30').split(' ')
                         )
        # Date format Invalid
        with pytest.raises(ValueError):
            call_command('change_eligibility_deadline',
                         *command_args.format(username=username, course=course_id_str, date='30-12-2018').split(' ')
                         )
        # Date not provided
        with pytest.raises(KeyError):
            call_command('change_eligibility_deadline',
                         *command_args.format(username=username, course=course_id_str,).split(' '))

    def test_invalid_date(self):
        """
        Tests the command when the date is prior to today

        In case the date given as deadline is prior to today it sets the deadline to
        default value which is one month from today. It then continues to run the code
        to change eligibility deadline.
        """
        course_key = str(self.course.id)
        username = self.enrolled_user.username

        # Test Date set prior to today
        with LogCapture(LOGGER_NAME) as logger:
            call_command(
                'change_eligibility_deadline',
                *command_args.format(username=username, course=course_key, date='2000-12-30').split(' ')
            )
            logger.check(
                (LOGGER_NAME, 'WARNING', 'Invalid date or date not provided. Setting deadline to one month from now'),
                (LOGGER_NAME, 'INFO', f'Successfully updated credit eligibility deadline for {username}')
            )

    def test_valid_command_arguments(self):
        """ Test command with valid arguments """
        course_key = str(self.course.id)
        username = self.enrolled_user.username
        new_deadline = datetime.utcnow() + timedelta(days=30)

        call_command('change_eligibility_deadline',
                     *command_args.format(username=username, course=course_key, date=new_deadline.date()).split(' ')
                     )

        credit_eligibility = CreditEligibility.objects.get(username=username,
                                                           course__course_key=self.course.id)
        credit_deadline = credit_eligibility.deadline.date()
        assert credit_deadline == new_deadline.date()
