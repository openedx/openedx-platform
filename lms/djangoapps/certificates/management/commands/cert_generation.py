"""
Management command to generate course certificates for one or more users in a given course run.
"""

import logging
import shlex

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from lms.djangoapps.certificates.generation_handler import CertificateGenerationNotAllowed, generate_certificate_task
from lms.djangoapps.certificates.models import CertificateGenerationCommandConfiguration

User = get_user_model()
log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Management command to generate course certificates for one or more users in a given course run.

    Example usage:
    ./manage.py lms cert_generation -u 123 456 -c course-v1:edX+DemoX+Demo_Course
    """

    help = """
    Generate course certificates for one or more users in a given course run.
    """

    def add_arguments(self, parser):
        parser.add_argument(
            '-u', '--user',
            nargs='+',
            metavar='USER',
            dest='user',
            help='user_id or space-separated list of user_ids for whom to generate course certificates'
        )
        parser.add_argument(
            '-c', '--course-key',
            metavar='COURSE_KEY',
            dest='course_key',
            help='course run key'
        )
        parser.add_argument(
            '--args-from-database',
            action='store_true',
            help='Use arguments from the CertificateGenerationCommandConfiguration model instead of the command line'
        )

    def get_args_from_database(self):
        """
        Returns an options dictionary from the current CertificateGenerationCommandConfiguration model.
        """
        config = CertificateGenerationCommandConfiguration.current()
        if not config.enabled:
            raise CommandError(
                "CertificateGenerationCommandConfiguration is disabled, but --args-from-database was requested"
            )

        args = shlex.split(config.arguments)
        parser = self.create_parser("manage.py", "cert_generation")

        return vars(parser.parse_args(args))

    def handle(self, *args, **options):
        # database args will override cmd line args
        if options['args_from_database']:
            options = self.get_args_from_database()

        if not options.get('user'):
            raise CommandError('You must specify a list of users')

        course_key = options.get('course_key')
        if not course_key:
            raise CommandError('You must specify a course-key')

        # Parse the serialized course key into a CourseKey
        try:
            course_key = CourseKey.from_string(course_key)
        except InvalidKeyError as e:
            raise CommandError('You must specify a valid course-key') from e

        # Loop over each user, and ask that a cert be generated for them
        users_str = options['user']
        for user_id in users_str:
            user = None
            try:
                user = User.objects.get(id=user_id)
            except User.DoesNotExist:
                log.warning(f'User {user_id} could not be found')
            if user is not None:
                log.info(
                    'Calling generate_certificate_task for {user} : {course}'.format(
                        user=user.id,
                        course=course_key
                    ))
                try:
                    generate_certificate_task(user, course_key)
                except CertificateGenerationNotAllowed as e:
                    log.exception(
                        "Certificate generation not allowed for user %s in course %s",
                        user.id,
                        course_key,
                    )
