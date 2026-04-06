"""
Tests for the migrate_saml_uids management command.
"""
from unittest.mock import mock_open, patch

from django.core.management import call_command
from django.test import TestCase
from social_django.models import UserSocialAuth

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.program_enrollments.management.commands import migrate_saml_uids
from lms.djangoapps.program_enrollments.management.commands.tests.utils import UserSocialAuthFactory

_COMMAND_PATH = 'lms.djangoapps.program_enrollments.management.commands.migrate_saml_uids'


class TestMigrateSamlUids(TestCase):
    """
    Test migrate_saml_uids command.
    """
    provider_slug = 'gatech'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.command = migrate_saml_uids.Command()

    def _format_email_uid_pair(self, email, uid):
        return f'{{"email":"{email}","student_key":"{uid}"}}'

    def _format_single_email_uid_pair_json(self, email, uid):
        return '[{obj}]'.format(  # noqa: UP032
            obj=self._format_email_uid_pair(email, uid)
        )

    def _call_command(self, data):
        """
        Call management command with `data` as contents of input file.
        """
        with patch(
                _COMMAND_PATH + '.py3_open',
                mock_open(read_data=data)
        ) as _:
            call_command(
                self.command,
                uid_mapping='./foo.json',
                saml_provider_slug=self.provider_slug
            )

    def _format_slug_urn_pair(self, slug, urn):
        return f'{slug}:{urn}'

    def test_single_mapping(self):
        new_urn = '9001'
        auth = UserSocialAuthFactory.create(slug=self.provider_slug)
        email = auth.user.email
        old_uid = auth.uid

        self._call_command(self._format_single_email_uid_pair_json(email, new_urn))

        auth.refresh_from_db()
        assert auth.uid == self._format_slug_urn_pair(self.provider_slug, new_urn)
        assert not auth.uid == old_uid

    def test_post_save_occurs(self):
        """
        Test the signals downstream of this update are called with appropriate arguments
        """
        auth = UserSocialAuthFactory.create(slug=self.provider_slug)
        new_urn = '9001'
        email = auth.user.email

        with patch('lms.djangoapps.program_enrollments.signals.matriculate_learner') as signal_handler_mock:
            self._call_command(self._format_single_email_uid_pair_json(email, new_urn))
            assert signal_handler_mock.called
            # first positional arg matches the user whose auth was updated
            assert signal_handler_mock.call_args[0][0].id == auth.user.id
            # second positional arg matches the urn we changed
            assert signal_handler_mock.call_args[0][1] == self._format_slug_urn_pair(self.provider_slug, new_urn)

    def test_multiple_social_auth_records(self):
        """
        Test we only alter one UserSocialAuth record if a learner has two
        """
        auth1 = UserSocialAuthFactory.create(slug=self.provider_slug)
        auth2 = UserSocialAuthFactory.create(
            slug=self.provider_slug,
            user=auth1.user
        )
        new_urn = '9001'
        email = auth1.user.email

        assert email == auth2.user.email

        self._call_command(self._format_single_email_uid_pair_json(email, new_urn))
        auths = UserSocialAuth.objects.filter(
            user__email=email,
            uid=self._format_slug_urn_pair(self.provider_slug, new_urn)
        )
        assert auths.count() == 1

    @patch(_COMMAND_PATH + '.log')
    def test_learner_without_social_auth_records(self, mock_log):
        user = UserFactory()
        email = user.email
        new_urn = '9001'

        mock_info = mock_log.info

        self._call_command(self._format_single_email_uid_pair_json(email, new_urn))
        mock_info.assert_any_call(
            'Number of users identified in the mapping file without'  # noqa: UP032
            ' {slug} UserSocialAuth records: 1'.format(
                slug=self.provider_slug
            )
        )

    @patch(_COMMAND_PATH + '.log')
    def test_learner_missed_by_mapping_file(self, mock_log):
        auth = UserSocialAuthFactory()
        # pylint disable required b/c this lint rule is confused about subfactories
        email = auth.user.email
        new_urn = '9001'

        mock_info = mock_log.info

        self._call_command(self._format_single_email_uid_pair_json('different' + email, new_urn))
        mock_info.assert_any_call(
            'Number of users with {slug} UserSocialAuth records '  # noqa: UP032
            'for which there was no mapping in the provided file: 1'.format(
                slug=self.provider_slug
            )
        )

    @patch(_COMMAND_PATH + '.log')
    def test_several_learners(self, mock_log):
        auths = [UserSocialAuthFactory() for _ in range(5)]
        new_urn = '9001'

        mock_info = mock_log.info

        self._call_command('[{}]'.format(
            ','.join(
                [
                    self._format_email_uid_pair(
                        auth.user.email,
                        new_urn + str(ind)
                    )
                    for ind, auth
                    in enumerate(auths)
                ]
            )
        ))

        for ind, auth in enumerate(auths):
            auth.refresh_from_db()
            assert auth.uid == self._format_slug_urn_pair(self.provider_slug, new_urn + str(ind))
        mock_info.assert_any_call('Number of mappings in the mapping file updated: 5')

    @patch(_COMMAND_PATH + '.log')
    def test_learner_duplicated_in_mapping(self, mock_log):
        auth = UserSocialAuthFactory()
        email = auth.user.email
        new_urn = '9001'

        mock_info = mock_log.info

        self._call_command('[{}]'.format(
            ','.join([self._format_email_uid_pair(email, new_urn) for _ in range(5)])
        ))
        mock_info.assert_any_call('Number of mappings in the mapping file where the '
                                  'identified user has already been processed: 4')
