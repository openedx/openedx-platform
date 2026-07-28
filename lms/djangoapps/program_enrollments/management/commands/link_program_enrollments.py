""" Management command to link program enrollments and external student_keys to an LMS user """


from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from lms.djangoapps.program_enrollments.api import link_program_enrollments

User = get_user_model()

INCORRECT_PARAMETER_TEMPLATE = (
    "incorrectly formatted argument '{}', "
    "must be in form <external user key>:<lms username>"
)
DUPLICATE_KEY_TEMPLATE = 'external user key {} provided multiple times'


class Command(BaseCommand):
    """
    Management command to manually link ProgramEnrollments without an LMS user to an LMS user by
    username.

    Usage:
        ./manage.py lms link_program_enrollments <program_uuid> <user_item>*
        where a <user_item> is a string formatted as <external_user_key>:<lms_username>

    Normally, program enrollments should be linked by the Django Social Auth post_save signal
    handler `lms.djangoapps.program_enrollments.signals.matriculate_learner`, but in the case that
    a partner does not have an IDP set up for learners to log in through, we need a way to link
    enrollments.

    Provided a program uuid and a list of external_user_key:lms_username, this command will look up
    the matching program enrollments and users, and update the program enrollments with the matching
    user. If the program enrollment has course enrollments, we will enroll the user into their
    waiting program courses.

    If an external user key is specified twice, an exception will be raised and no enrollments will
    be modified.

    For each external_user_key:lms_username, if:
        - The user is not found
        - No enrollment is found for the given program and external_user_key
        - The enrollment already has a user
    An error message will be logged and the input will be skipped. All other inputs will be
    processed and enrollments updated.

    If there is an error while enrolling a user in a waiting program course enrollment, the error
    will be logged, and we will roll back all transactions for that user so that their db state will
    be the same as it was before this command was run. This is to allow the re-running of the same
    command again to correctly enroll the user once the issue preventing the enrollment has been
    resolved.

    No other users will be affected, they will be processed normally.
    """

    help = 'Manually links ProgramEnrollment records to LMS users'

    def add_arguments(self, parser):
        parser.add_argument(
            'program_uuid',
            help='the program in which we are linking enrollments to users',
        )
        parser.add_argument(
            'user_items',
            nargs='*',
            help='specify the users to link, in the format <external_student_key>:<lms_username>*',
        )

    # pylint: disable=arguments-differ
    def handle(self, program_uuid, user_items, *args, **options):
        try:
            parsed_program_uuid = UUID(program_uuid)
        except ValueError:
            raise CommandError("supplied program_uuid '{}' is not a valid UUID")  # pylint: disable=raise-missing-from  # noqa: B904
        ext_keys_to_usernames = self.parse_user_items(user_items)
        try:
            link_program_enrollments(
                parsed_program_uuid, ext_keys_to_usernames
            )
        except Exception as e:
            raise CommandError(str(e))  # pylint: disable=raise-missing-from  # noqa: B904

    def parse_user_items(self, user_items):
        """
        Params:
            list of strings in the format 'external_user_key:lms_username'
        Returns:
            dict mapping external user keys to lms usernames
        Raises:
            CommandError
        """
        result = {}
        for user_item in user_items:
            split_args = user_item.split(':')
            if len(split_args) != 2:
                message = INCORRECT_PARAMETER_TEMPLATE.format(user_item)
                raise CommandError(message)
            external_user_key = split_args[0].strip()
            lms_username = split_args[1].strip()
            if not (external_user_key and lms_username):
                message = INCORRECT_PARAMETER_TEMPLATE.format(user_item)
                raise CommandError(message)
            if external_user_key in result:
                raise CommandError(DUPLICATE_KEY_TEMPLATE.format(external_user_key))
            result[external_user_key] = lms_username
        return result
