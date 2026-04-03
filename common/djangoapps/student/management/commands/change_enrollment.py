""" Command line script to change user enrollments. """


import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models import CourseEnrollment, CourseEnrollmentAttribute, User
from openedx.core.djangoapps.credit.email_utils import get_credit_provider_attribute_values

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


class RollbackException(Exception):
    """
    Exception raised explicitly to cause a database transaction rollback.
    """
    pass  # lint-amnesty, pylint: disable=unnecessary-pass


class Command(BaseCommand):  # lint-amnesty, pylint: disable=missing-class-docstring

    help = """
    Changes the enrollment status for students that meet
    the criteria specified by the parameters to this command.

    Example:

        Change enrollment for users joe, frank, and bill from audit to honor:

          $ ... change_enrollment -u joe,frank,bill -c some/course/id --from audit --to honor

        Or

          $ ... change_enrollment -e "joe@example.com,frank@example.com,..." -c some/course/id --from audit --to honor

        See what would have been changed from audit to honor without making that change

          $ ... change_enrollment -u joe,frank,bill -c some/course/id --from audit --to honor -n
    """

    enrollment_modes = ('audit', 'verified', 'honor', 'credit')

    def add_arguments(self, parser):
        parser.add_argument('-f', '--from',
                            metavar='FROM_MODE',
                            dest='from_mode',
                            required=True,
                            choices=self.enrollment_modes,
                            help='Move from this enrollment mode')
        parser.add_argument('-t', '--to',
                            metavar='TO_MODE',
                            dest='to_mode',
                            required=True,
                            choices=self.enrollment_modes,
                            help='Move to this enrollment mode')
        parser.add_argument('-u', '--username',
                            metavar='USERNAME',
                            help='Comma-separated list of usernames to move in the course')
        parser.add_argument('-e', '--email',
                            metavar='EMAIL',
                            help='Comma-separated list of email addresses to move in the course')
        parser.add_argument('-c', '--course',
                            metavar='COURSE_ID',
                            dest='course_id',
                            required=True,
                            help='Course id to use for transfer')
        parser.add_argument('-n', '--noop',
                            action='store_true',
                            help='Display what will be done but do not actually do anything')

    def handle(self, *args, **options):
        try:
            course_key = CourseKey.from_string(options['course_id'])
        except InvalidKeyError:
            raise CommandError('Invalid or non-existant course id {}'.format(options['course_id']))  # lint-amnesty, pylint: disable=raise-missing-from

        if not options['username'] and not options['email']:
            raise CommandError('You must include usernames (-u) or emails (-e) to select users to update')

        enrollment_args = dict(
            course_id=course_key,
            mode=options['from_mode']
        )

        error_users = []
        success_users = []

        if options['username']:
            self.update_enrollments('username', enrollment_args, options, error_users, success_users)

        if options['email']:
            self.update_enrollments('email', enrollment_args, options, error_users, success_users)

        self.report(error_users, success_users)

    def update_enrollments(self, identifier, enrollment_args, options, error_users, success_users, enrollment_attrs=None):  # lint-amnesty, pylint: disable=line-too-long
        """ Update enrollments for a specific user identifier (email or username). """
        users = options[identifier].split(",")

        credit_provider_attr = {}
        if options['to_mode'] == 'credit':
            provider_ids = get_credit_provider_attribute_values(
                enrollment_args.get('course_id'), 'id'
            )
            credit_provider_attr = {
                'namespace': 'credit',
                'name': 'provider_id',
                'value': provider_ids[0],
            }

        for identified_user in users:
            logger.info(identified_user)

            try:
                user_args = {
                    identifier: identified_user
                }

                enrollment_args['user'] = User.objects.get(**user_args)
                enrollments = CourseEnrollment.objects.filter(**enrollment_args)

                enrollment_attrs = []
                with transaction.atomic():
                    for enrollment in enrollments:
                        enrollment.update_enrollment(mode=options['to_mode'])
                        enrollment.save()
                        if options['to_mode'] == 'credit':
                            enrollment_attrs.append(credit_provider_attr)
                            CourseEnrollmentAttribute.add_enrollment_attr(
                                enrollment=enrollment, data_list=enrollment_attrs
                            )

                    if options['noop']:
                        raise RollbackException('Forced rollback.')

            except RollbackException:
                success_users.append(identified_user)
                continue
            except Exception as exception:  # pylint: disable=broad-except
                error_users.append((identified_user, exception))
                continue

            success_users.append(identified_user)
            logger.info('Updated user [%s] to mode [%s]', identified_user, options['to_mode'])

    def report(self, error_users, success_users):
        """ Log and overview of the results of the command. """
        total_users = len(success_users) + len(error_users)
        logger.info('Successfully updated %i out of %i users', len(success_users), total_users)
        if len(error_users) > 0:
            logger.info('The following %i user(s) not saved:', len(error_users))
            for user, error in error_users:
                logger.info('user: [%s] reason: [%s] %s', user, type(error).__name__, str(error))
