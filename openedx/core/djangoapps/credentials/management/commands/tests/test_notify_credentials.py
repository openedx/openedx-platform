"""  # pylint: disable=django-not-configured
Tests the ``notify_credentials`` management command.
"""

from datetime import datetime, timezone
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from freezegun import freeze_time

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.catalog.tests.factories import CourseFactory, CourseRunFactory, ProgramFactory
from openedx.core.djangoapps.credentials.models import NotifyCredentialsConfig
from openedx.core.djangoapps.site_configuration.tests.factories import SiteConfigurationFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms

from ..notify_credentials import Command

NOTIFY_CREDENTIALS_TASK = 'openedx.core.djangoapps.credentials.tasks.v1.tasks.handle_notify_credentials.run'


@skip_unless_lms
@mock.patch(NOTIFY_CREDENTIALS_TASK)
class TestNotifyCredentials(TestCase):
    """
    Tests the ``notify_credentials`` management command.
    """
    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.user2 = UserFactory.create()
        self.expected_options = {
            'args_from_database': False,
            'auto': False,
            'courses': None,
            'delay': 0,
            'dry_run': False,
            'end_date': None,
            'force_color': False,
            'no_color': False,
            'notify_programs': False,
            'page_size': 100,
            'program_uuids': None,
            'pythonpath': None,
            'settings': None,
            'site': None,
            'start_date': None,
            'traceback': False,
            'user_ids': None,
            'verbose': False,
            'verbosity': 1,
            'skip_checks': True,
            'revoke_program_certs': False,
        }

    def test_course_args(self, mock_task):
        course_1_id = 'course-v1:edX+Test+1'
        course_2_id = 'course-v1:edX+Test+2'
        self.expected_options['courses'] = [course_1_id, course_2_id]

        call_command(Command(), '--course', course_1_id, course_2_id)
        assert mock_task.called
        assert mock_task.call_args[0][0] == self.expected_options

    @mock.patch(
        'openedx.core.djangoapps.credentials.management.commands.notify_credentials.get_programs_from_cache_by_uuid'
    )
    def test_program_uuid_args(self, mock_get_programs, mock_task):
        course_1_id = 'course-v1:edX+Test+1'
        course_2_id = 'course-v1:edX+Test+2'
        program = ProgramFactory(
            courses=[
                CourseFactory(
                    course_runs=[
                        CourseRunFactory(key=course_1_id),
                        CourseRunFactory(key=course_2_id)
                    ]
                )
            ],
            curricula=[],
        )
        self.expected_options['program_uuids'] = [program['uuid']]
        mock_get_programs.return_value = [program]
        call_command(Command(), '--program_uuids', program['uuid'])
        assert mock_task.called
        assert mock_task.call_args[0][0] == self.expected_options
        assert mock_task.call_args[0][1].sort() == [course_1_id, course_2_id].sort()

    @mock.patch(
        'openedx.core.djangoapps.credentials.management.commands.notify_credentials.get_programs_from_cache_by_uuid'
    )
    def test_multiple_programs_uuid_args(self, mock_get_programs, mock_task):
        course_1_id = 'course-v1:edX+Test+1'
        course_2_id = 'course-v1:edX+Test+2'
        program = ProgramFactory(
            courses=[
                CourseFactory(
                    course_runs=[
                        CourseRunFactory(key=course_1_id),
                    ]
                )
            ],
            curricula=[],
        )

        program2 = ProgramFactory(
            courses=[
                CourseFactory(
                    course_runs=[
                        CourseRunFactory(key=course_2_id)
                    ]
                )
            ],
            curricula=[],
        )
        program_list = [program['uuid'], program2['uuid']]
        self.expected_options['program_uuids'] = program_list
        mock_get_programs.return_value = [program, program2]
        call_command(Command(), '--program_uuids', program['uuid'], program2['uuid'])
        assert mock_task.called
        assert mock_task.call_args[0][0] == self.expected_options
        assert mock_task.call_args[0][1].sort() == [course_1_id, course_2_id].sort()

    @freeze_time(datetime(2017, 5, 1, 4))
    def test_auto_execution(self, mock_task):
        """Verify that an automatic execution designed for scheduled windows works correctly"""
        self.expected_options['auto'] = True
        self.expected_options['start_date'] = datetime(2017, 5, 1, 0, 0)
        self.expected_options['end_date'] = datetime(2017, 5, 1, 4, 0)

        call_command(Command(), '--auto')
        assert mock_task.called
        assert mock_task.call_args[0][0] == self.expected_options

    @override_settings(NOTIFY_CREDENTIALS_FREQUENCY=3600)
    @freeze_time(datetime(2017, 5, 1, 4))
    def test_auto_execution_different_schedule(self, mock_task):
        """Verify that an automatic execution designed for scheduled windows
        works correctly if the window frequency has been changed"""
        self.expected_options["auto"] = True
        self.expected_options["start_date"] = datetime(2017, 5, 1, 3, 0)
        self.expected_options["end_date"] = datetime(2017, 5, 1, 4, 0)

        call_command(Command(), "--auto")
        assert mock_task.called
        assert mock_task.call_args[0][0] == self.expected_options

    def test_date_args(self, mock_task):
        self.expected_options['start_date'] = datetime(2017, 1, 31, 0, 0, tzinfo=timezone.utc)  # noqa: UP017
        call_command(Command(), '--start-date', '2017-01-31')
        assert mock_task.called
        assert mock_task.call_args[0][0] == self.expected_options
        mock_task.reset_mock()

        self.expected_options['start_date'] = datetime(2017, 2, 1, 0, 0, tzinfo=timezone.utc)  # noqa: UP017
        self.expected_options['end_date'] = datetime(2017, 2, 2, 0, 0, tzinfo=timezone.utc)  # noqa: UP017
        call_command(Command(), '--start-date', '2017-02-01', '--end-date', '2017-02-02')
        assert mock_task.called
        assert mock_task.call_args[0][0] == self.expected_options
        mock_task.reset_mock()

        self.expected_options['start_date'] = None
        self.expected_options['end_date'] = datetime(2017, 2, 2, 0, 0, tzinfo=timezone.utc)  # noqa: UP017
        call_command(Command(), '--end-date', '2017-02-02')
        assert mock_task.called
        assert mock_task.call_args[0][0] == self.expected_options
        mock_task.reset_mock()

        self.expected_options['start_date'] = datetime(2017, 2, 1, 0, 0, tzinfo=timezone.utc)  # noqa: UP017
        self.expected_options['end_date'] = datetime(2017, 2, 1, 4, 0, tzinfo=timezone.utc)  # noqa: UP017
        call_command(Command(), '--start-date', "2017-02-01 00:00:00", '--end-date', '2017-02-01 04:00:00')
        assert mock_task.called
        assert mock_task.call_args[0][0] == self.expected_options

    def test_username_arg(self, mock_task):
        self.expected_options['start_date'] = datetime(2017, 2, 1, 0, 0, tzinfo=timezone.utc)  # noqa: UP017
        self.expected_options['end_date'] = datetime(2017, 2, 2, 0, 0, tzinfo=timezone.utc)  # noqa: UP017
        self.expected_options['user_ids'] = [str(self.user2.id)]
        call_command(
            'notify_credentials', '--start-date', '2017-02-01', '--end-date', '2017-02-02', '--user_ids', self.user2.id
        )
        assert mock_task.called
        assert mock_task.call_args[0][0] == self.expected_options
        mock_task.reset_mock()

        self.expected_options['start_date'] = None
        self.expected_options['end_date'] = None
        self.expected_options['user_ids'] = [str(self.user2.id)]
        call_command(
            'notify_credentials', '--user_ids', self.user2.id
        )
        assert mock_task.called
        assert mock_task.call_args[0][0] == self.expected_options
        mock_task.reset_mock()

        self.expected_options['start_date'] = datetime(2017, 2, 1, 0, 0, tzinfo=timezone.utc)  # noqa: UP017
        self.expected_options['end_date'] = datetime(2017, 2, 2, 0, 0, tzinfo=timezone.utc)  # noqa: UP017
        self.expected_options['user_ids'] = [str(self.user.id)]
        call_command(
            'notify_credentials', '--start-date', '2017-02-01', '--end-date', '2017-02-02', '--user_ids', self.user.id
        )
        assert mock_task.called
        assert mock_task.call_args[0][0] == self.expected_options
        mock_task.reset_mock()

        self.expected_options['start_date'] = None
        self.expected_options['end_date'] = None
        self.expected_options['user_ids'] = [str(self.user.id)]
        call_command(
            'notify_credentials', '--user_ids', self.user.id
        )
        assert mock_task.called
        assert mock_task.call_args[0][0] == self.expected_options
        mock_task.reset_mock()

        self.expected_options['start_date'] = None
        self.expected_options['end_date'] = None
        self.expected_options['user_ids'] = [str(self.user.id), str(self.user2.id)]
        call_command(
            'notify_credentials', '--user_ids', self.user.id, self.user2.id
        )
        assert mock_task.called
        assert mock_task.call_args[0][0] == self.expected_options
        mock_task.reset_mock()

    def test_no_args(self, mock_task):
        with self.assertRaisesRegex(CommandError, 'You must specify a filter.*'):  # noqa: PT027
            call_command(Command())
        assert not mock_task.called

    def test_dry_run(self, mock_task):
        self.expected_options['start_date'] = datetime(2017, 2, 1, 0, 0, tzinfo=timezone.utc)  # noqa: UP017
        self.expected_options['dry_run'] = True
        call_command(Command(), '--dry-run', '--start-date', '2017-02-01')
        assert mock_task.called
        assert mock_task.call_args[0][0] == self.expected_options

    def test_hand_off(self, mock_task):
        self.expected_options['start_date'] = datetime(2017, 2, 1, 0, 0, tzinfo=timezone.utc)  # noqa: UP017
        self.expected_options['notify_programs'] = True
        call_command(Command(), '--start-date', '2017-02-01', '--notify_programs')
        assert mock_task.called
        assert mock_task.call_args[0][0] == self.expected_options

    def test_delay(self, mock_task):
        self.expected_options['start_date'] = datetime(2017, 2, 1, 0, 0, tzinfo=timezone.utc)  # noqa: UP017
        self.expected_options['delay'] = 0.2
        call_command(Command(), '--start-date', '2017-02-01', '--delay', '0.2')
        assert mock_task.called
        assert mock_task.call_args[0][0] == self.expected_options

    def test_page_size(self, mock_task):
        self.expected_options['start_date'] = datetime(2017, 2, 1, 0, 0, tzinfo=timezone.utc)  # noqa: UP017
        self.expected_options['page_size'] = 2
        call_command(Command(), '--start-date', '2017-02-01', '--page-size=2')
        assert mock_task.called
        assert mock_task.call_args[0][0] == self.expected_options

    def test_site(self, mock_task):
        site_config = SiteConfigurationFactory.create(
            site_values={'course_org_filter': ['testX']}
        )
        self.expected_options['site'] = site_config.site.domain
        self.expected_options['start_date'] = datetime(2017, 1, 1, 0, 0, tzinfo=timezone.utc)  # noqa: UP017

        call_command(Command(), '--site', site_config.site.domain, '--start-date', '2017-01-01')
        assert mock_task.called
        assert mock_task.call_args[0][0] == self.expected_options

    def test_args_from_database(self, mock_task):
        # Nothing in the database, should default to disabled
        with self.assertRaisesRegex(CommandError, 'NotifyCredentialsConfig is disabled.*'):  # noqa: PT027
            call_command(Command(), '--start-date', '2017-01-01', '--args-from-database')

        # Add a config
        config = NotifyCredentialsConfig.current()
        config.arguments = '--start-date "2017-03-01 00:00:00"'
        config.enabled = True
        config.save()

        # Not told to use config, should ignore it
        self.expected_options['start_date'] = datetime(2017, 1, 1, 0, 0, tzinfo=timezone.utc)  # noqa: UP017
        call_command(Command(), '--start-date', '2017-01-01')
        assert mock_task.called
        assert mock_task.call_args[0][0] == self.expected_options

        # Told to use it, and enabled. Should use config in preference of command line
        self.expected_options['start_date'] = datetime(2017, 3, 1, 0, 0, tzinfo=timezone.utc)  # noqa: UP017
        self.expected_options['skip_checks'] = False
        call_command(Command(), '--start-date', '2017-01-01', '--args-from-database')
        assert mock_task.called
        assert mock_task.call_args[0][0] == self.expected_options

        config.enabled = False
        config.save()

        # Explicitly disabled
        with self.assertRaisesRegex(CommandError, 'NotifyCredentialsConfig is disabled.*'):  # noqa: PT027
            call_command(Command(), '--start-date', '2017-01-01', '--args-from-database')

    def test_args_revoke_program_cert(self, mock_task):
        self.expected_options['user_ids'] = [str(self.user.id)]
        self.expected_options['revoke_program_certs'] = True
        call_command(Command(), '--user_ids', self.user.id, '--revoke_program_certs')
        assert mock_task.called
        assert mock_task.call_args[0][0] == self.expected_options
