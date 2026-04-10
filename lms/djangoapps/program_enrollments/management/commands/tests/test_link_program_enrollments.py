"""
Tests for the link_program_enrollments management command.
"""


from unittest import mock
from uuid import UUID

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ..link_program_enrollments import DUPLICATE_KEY_TEMPLATE, INCORRECT_PARAMETER_TEMPLATE, Command

_COMMAND_PATH = 'lms.djangoapps.program_enrollments.management.commands.link_program_enrollments'


class TestLinkProgramEnrollmentManagementCommand(TestCase):
    """
    Test that the command calls link_program_enrollments
    correctly and handles exceptional input correctly.
    """

    program_uuid = 'a32c5da8-fb89-4f1e-97a7-b13de9e6dfa2'

    _LINKING_FUNCTION_MOCK_PATH = _COMMAND_PATH + ".link_program_enrollments"

    @mock.patch(_LINKING_FUNCTION_MOCK_PATH, autospec=True)
    def test_good_input_calls_linking(self, mock_link):
        call_command(
            Command(), self.program_uuid, 'learner-01:user-01', 'learner-02:user-02'
        )
        mock_link.assert_called_once_with(
            UUID(self.program_uuid),
            {
                'learner-01': 'user-01',
                'learner-02': 'user-02',
            },
        )

    def test_incorrectly_formatted_input_exception(self):
        with self.assertRaisesRegex(  # noqa: PT027
                CommandError,
                INCORRECT_PARAMETER_TEMPLATE.format('whoops')
        ):
            call_command(
                Command(), self.program_uuid, 'learner-01:user-01', 'whoops', 'learner-03:user-03'
            )

    def test_missing_external_user_key(self):
        with self.assertRaisesRegex(  # noqa: PT027
                CommandError,
                INCORRECT_PARAMETER_TEMPLATE.format('whoops: ')
        ):
            call_command(
                Command(), self.program_uuid, 'learner-01:user-01', 'whoops: ', 'learner-03:user-03'
            )

    def test_missing_username(self):
        with self.assertRaisesRegex(  # noqa: PT027
                CommandError,
                INCORRECT_PARAMETER_TEMPLATE.format(' :whoops')
        ):
            call_command(
                Command(), self.program_uuid, 'learner-01:user-01', ' :whoops', 'learner-03:user-03'
            )

    def test_repeated_user_key_exception(self):
        with self.assertRaisesRegex(  # noqa: PT027
                CommandError,
                DUPLICATE_KEY_TEMPLATE.format('learner-01'),
        ):
            call_command(
                Command(), self.program_uuid, 'learner-01:user-01', 'learner-01:user-02'
            )

    def test_invalid_uuid(self):
        error_regex = r"supplied program_uuid '.*' is not a valid UUID"
        with self.assertRaisesRegex(CommandError, error_regex):  # noqa: PT027
            call_command(
                Command(), 'notauuid::thisisntauuid', 'learner-0:user-01'
            )
