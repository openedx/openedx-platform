"""
Test credentials tasks
"""

import logging
from unittest import mock
from datetime import datetime, timezone, timedelta

import ddt
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection, reset_queries
from django.test import TestCase, override_settings
from freezegun import freeze_time
from opaque_keys.edx.keys import CourseKey
from testfixtures import LogCapture

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.certificates.api import get_recently_modified_certificates
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.grades.models import PersistentCourseGrade
from lms.djangoapps.grades.models_api import get_recently_modified_grades
from lms.djangoapps.grades.tests.utils import mock_passing_grade
from lms.djangoapps.certificates.tests.factories import CertificateDateOverrideFactory, GeneratedCertificateFactory
from openedx.core.djangoapps.catalog.tests.factories import CourseFactory, CourseRunFactory, ProgramFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.credentials.helpers import is_learner_records_enabled
from openedx.core.djangoapps.credentials.tasks.v1 import tasks
from openedx.core.djangoapps.site_configuration.tests.factories import SiteConfigurationFactory, SiteFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.data import CertificatesDisplayBehaviors


User = get_user_model()
LOGGER_NAME = "openedx.core.djangoapps.credentials.tasks.v1.tasks"
TASKS_MODULE = 'openedx.core.djangoapps.credentials.tasks.v1.tasks'


def boom():
    raise Exception('boom')


@skip_unless_lms
@mock.patch(TASKS_MODULE + '.get_credentials_api_client')
@override_settings(CREDENTIALS_SERVICE_USERNAME='test-service-username')
class TestSendGradeToCredentialTask(TestCase):
    """
    Tests for the 'send_grade_to_credentials' method.
    """
    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(username=settings.CREDENTIALS_SERVICE_USERNAME)

    def test_happy_path(self, mock_get_api_client):
        """
        Test that we actually do check expiration on each entitlement (happy path)
        """
        api_client = mock.MagicMock()
        mock_get_api_client.return_value = api_client

        last_updated = datetime.now()

        tasks.send_grade_to_credentials.delay(
            'user',
            'course-v1:org+course+run',
            True,
            'A',
            1.0,
            last_updated
        ).get()

        assert mock_get_api_client.call_count == 1
        assert mock_get_api_client.call_args[0] == (self.user,)

        assert api_client.post.call_count == 1
        self.assertDictEqual(api_client.post.call_args[1]['data'], {
            'username': 'user',
            'course_run': 'course-v1:org+course+run',
            'letter_grade': 'A',
            'percent_grade': 1.0,
            'verified': True,
            'lms_last_updated_at': last_updated,
        })

    def test_retry(self, mock_get_api_client):
        """
        Test that we retry the appropriate number of times when an exception occurs.
        """
        mock_get_api_client.side_effect = boom

        task = tasks.send_grade_to_credentials.delay('user', 'course-v1:org+course+run', True, 'A', 1.0, None)

        pytest.raises(Exception, task.get)
        assert mock_get_api_client.call_count == 11


@ddt.ddt
@skip_unless_lms
class TestHandleNotifyCredentialsTask(TestCase):
    """
    Tests for the 'handle_notify_credentials' task.
    """
    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.user2 = UserFactory.create()

        with freeze_time(datetime(2017, 1, 1)):
            self.cert1 = GeneratedCertificateFactory(user=self.user, course_id='course-v1:edX+Test+1')
        with freeze_time(datetime(2017, 2, 1, 0)):
            self.cert2 = GeneratedCertificateFactory(user=self.user, course_id='course-v1:edX+Test+2')
        with freeze_time(datetime(2017, 3, 1)):
            self.cert3 = GeneratedCertificateFactory(user=self.user, course_id='course-v1:testX+Test+3')
        with freeze_time(datetime(2017, 2, 1, 5)):
            self.cert4 = GeneratedCertificateFactory(
                user=self.user2, course_id='course-v1:edX+Test+4', status=CertificateStatuses.downloadable
            )
        # No factory for these
        with freeze_time(datetime(2017, 1, 1)):
            self.grade1 = PersistentCourseGrade.objects.create(user_id=self.user.id, course_id='course-v1:edX+Test+1',
                                                               percent_grade=1)
        with freeze_time(datetime(2017, 2, 1)):
            self.grade2 = PersistentCourseGrade.objects.create(user_id=self.user.id, course_id='course-v1:edX+Test+2',
                                                               percent_grade=1)
        with freeze_time(datetime(2017, 3, 1)):
            self.grade3 = PersistentCourseGrade.objects.create(user_id=self.user.id, course_id='course-v1:testX+Test+3',
                                                               percent_grade=1)
        with freeze_time(datetime(2017, 2, 1, 5)):
            self.grade4 = PersistentCourseGrade.objects.create(user_id=self.user2.id, course_id='course-v1:edX+Test+4',
                                                               percent_grade=1)
        self.options = {
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

    @mock.patch(TASKS_MODULE + '.send_notifications')
    def test_course_args(self, mock_send):
        course_keys = ['course-v1:edX+Test+1', 'course-v1:edX+Test+2']
        tasks.handle_notify_credentials(options=self.options, course_keys=course_keys)
        assert mock_send.called
        assert list(mock_send.call_args[0][0]) == [self.cert1, self.cert2]
        assert list(mock_send.call_args[0][1]) == [self.grade1, self.grade2]

    @freeze_time(datetime(2017, 5, 1, 4))
    @mock.patch(TASKS_MODULE + '.send_notifications')
    def test_auto_execution(self, mock_send):
        cert_filter_args = {}

        with freeze_time(datetime(2017, 5, 1, 0)):
            cert1 = GeneratedCertificateFactory(user=self.user, course_id='course-v1:edX+Test+11')
        with freeze_time(datetime(2017, 5, 1, 3)):
            cert2 = GeneratedCertificateFactory(user=self.user, course_id='course-v1:edX+Test+22')

        # `auto` execution should include certificates with date overrides
        # modified within the time range. See the next test
        # (`test_certs_with_modified_date_overrides`) for more.
        with freeze_time(datetime(2017, 4, 30, 0)):
            cert3 = GeneratedCertificateFactory(user=self.user, course_id='course-v1:edX+Test+33')
        with freeze_time(datetime(2017, 5, 1, 2)):
            CertificateDateOverrideFactory(
                generated_certificate=cert3,
                overridden_by=self.user,
            )

        with freeze_time(datetime(2017, 5, 1, 0)):
            grade1 = PersistentCourseGrade.objects.create(user_id=self.user.id, course_id='course-v1:edX+Test+11',
                                                          percent_grade=1)
        with freeze_time(datetime(2017, 5, 1, 3)):
            grade2 = PersistentCourseGrade.objects.create(user_id=self.user.id, course_id='course-v1:edX+Test+22',
                                                          percent_grade=1)

        total_certificates = get_recently_modified_certificates(**cert_filter_args)
        total_grades = PersistentCourseGrade.objects.all()

        self.options['auto'] = True
        self.options['start_date'] = '2017-05-01T00:00:00'
        self.options['end_date'] = '2017-05-01T04:00:00'
        tasks.handle_notify_credentials(options=self.options, course_keys=[])

        assert mock_send.called
        self.assertListEqual(list(mock_send.call_args[0][0]), [cert3, cert1, cert2])
        self.assertListEqual(list(mock_send.call_args[0][1]), [grade1, grade2])

        assert len(list(mock_send.call_args[0][0])) <= len(total_certificates)
        assert len(list(mock_send.call_args[0][1])) <= len(total_grades)

    @mock.patch(TASKS_MODULE + '.send_notifications')
    def test_certs_with_modified_date_overrides(self, mock_send):
        # Set up a time range for the call
        self.options['start_date'] = '2017-05-01T00:00:00'
        self.options['end_date'] = '2017-05-04T04:00:00'

        # First, call the task without adding any overrides to verify that the
        # certs would not be included because of their modified_date
        tasks.handle_notify_credentials(options=self.options, course_keys=[])
        assert mock_send.called
        assert self.cert1 not in mock_send.call_args[0][0]
        assert self.cert2 not in mock_send.call_args[0][0]
        assert self.cert3 not in mock_send.call_args[0][0]

        # ADD
        # cert1 was created January 1, 2017. We add a date override to it on
        # May 1, 2017.
        with freeze_time(datetime(2017, 5, 1, 1)):
            CertificateDateOverrideFactory(
                generated_certificate=self.cert1,
                overridden_by=self.user,
            )

        # UPDATE
        # cert2 was created February 1, 2017. We add a date override to it on
        # April 1, 2017; but edit the date override on May 2, 2017.
        with freeze_time(datetime(2017, 4, 1)):
            cert2_override = CertificateDateOverrideFactory(
                generated_certificate=self.cert2,
                overridden_by=self.user,
            )

        with freeze_time(datetime(2017, 5, 2, 1)):
            cert2_override.date = datetime(2018, 1, 1)
            cert2_override.save()

        # DELETE
        # cert3 was created March 1, 2017. We add a date override to it on April
        # 2, 2017; but delete the override on May 3, 2017.
        with freeze_time(datetime(2017, 4, 2)):
            cert3_override = CertificateDateOverrideFactory(
                generated_certificate=self.cert3,
                overridden_by=self.user,
            )
        with freeze_time(datetime(2017, 5, 3, 1)):
            cert3_override.delete()

        # None of the certs have modified dates within the time range, but they
        # each have date overrides that were added, updated, or deleted within
        # the time range.

        tasks.handle_notify_credentials(options=self.options, course_keys=[])

        # The three certs should now be included in the arguments because of the
        # the altered date overrides.
        assert mock_send.called
        self.assertListEqual(list(mock_send.call_args[0][0]), [self.cert1, self.cert2, self.cert3])
        assert self.cert1 in mock_send.call_args[0][0]
        assert self.cert2 in mock_send.call_args[0][0]
        assert self.cert3 in mock_send.call_args[0][0]

    @mock.patch(TASKS_MODULE + '.send_notifications')
    def test_date_args(self, mock_send):
        self.options['start_date'] = datetime(2017, 1, 31, 0, 0, tzinfo=timezone.utc)
        tasks.handle_notify_credentials(options=self.options, course_keys=[])
        assert mock_send.called
        self.assertListEqual(list(mock_send.call_args[0][0]), [self.cert2, self.cert4, self.cert3])
        self.assertListEqual(list(mock_send.call_args[0][1]), [self.grade2, self.grade4, self.grade3])
        mock_send.reset_mock()

        self.options['start_date'] = datetime(2017, 2, 1, 0, 0, tzinfo=timezone.utc)
        self.options['end_date'] = datetime(2017, 2, 2, 0, 0, tzinfo=timezone.utc)
        tasks.handle_notify_credentials(options=self.options, course_keys=[])
        assert mock_send.called
        self.assertListEqual(list(mock_send.call_args[0][0]), [self.cert2, self.cert4])
        self.assertListEqual(list(mock_send.call_args[0][1]), [self.grade2, self.grade4])
        mock_send.reset_mock()

        self.options['start_date'] = None
        self.options['end_date'] = datetime(2017, 2, 2, 0, 0, tzinfo=timezone.utc)
        tasks.handle_notify_credentials(options=self.options, course_keys=[])
        assert mock_send.called
        self.assertListEqual(list(mock_send.call_args[0][0]), [self.cert1, self.cert2, self.cert4])
        self.assertListEqual(list(mock_send.call_args[0][1]), [self.grade1, self.grade2, self.grade4])
        mock_send.reset_mock()

        self.options['start_date'] = datetime(2017, 2, 1, 0, 0, tzinfo=timezone.utc)
        self.options['end_date'] = datetime(2017, 2, 1, 4, 0, tzinfo=timezone.utc)
        tasks.handle_notify_credentials(options=self.options, course_keys=[])
        assert mock_send.called
        self.assertListEqual(list(mock_send.call_args[0][0]), [self.cert2])
        self.assertListEqual(list(mock_send.call_args[0][1]), [self.grade2])

    @mock.patch(TASKS_MODULE + '.send_notifications')
    def test_username_arg(self, mock_send):
        self.options['start_date'] = datetime(2017, 2, 1, 0, 0, tzinfo=timezone.utc)
        self.options['end_date'] = datetime(2017, 2, 2, 0, 0, tzinfo=timezone.utc)
        self.options['user_ids'] = [str(self.user2.id)]
        tasks.handle_notify_credentials(options=self.options, course_keys=[])
        assert mock_send.called
        self.assertListEqual(list(mock_send.call_args[0][0]), [self.cert4])
        self.assertListEqual(list(mock_send.call_args[0][1]), [self.grade4])
        mock_send.reset_mock()

        self.options['start_date'] = None
        self.options['end_date'] = None
        self.options['user_ids'] = [str(self.user2.id)]
        tasks.handle_notify_credentials(options=self.options, course_keys=[])
        assert mock_send.called
        self.assertListEqual(list(mock_send.call_args[0][0]), [self.cert4])
        self.assertListEqual(list(mock_send.call_args[0][1]), [self.grade4])
        mock_send.reset_mock()

        self.options['start_date'] = datetime(2017, 2, 1, 0, 0, tzinfo=timezone.utc)
        self.options['end_date'] = datetime(2017, 2, 2, 0, 0, tzinfo=timezone.utc)
        self.options['user_ids'] = [str(self.user.id)]
        tasks.handle_notify_credentials(options=self.options, course_keys=[])
        assert mock_send.called
        self.assertListEqual(list(mock_send.call_args[0][0]), [self.cert2])
        self.assertListEqual(list(mock_send.call_args[0][1]), [self.grade2])
        mock_send.reset_mock()

        self.options['start_date'] = None
        self.options['end_date'] = None
        self.options['user_ids'] = [str(self.user.id)]
        tasks.handle_notify_credentials(options=self.options, course_keys=[])
        assert mock_send.called
        self.assertListEqual(list(mock_send.call_args[0][0]), [self.cert1, self.cert2, self.cert3])
        self.assertListEqual(list(mock_send.call_args[0][1]), [self.grade1, self.grade2, self.grade3])
        mock_send.reset_mock()

        self.options['start_date'] = None
        self.options['end_date'] = None
        self.options['user_ids'] = [str(self.user.id), str(self.user2.id)]
        tasks.handle_notify_credentials(options=self.options, course_keys=[])
        assert mock_send.called
        self.assertListEqual(list(mock_send.call_args[0][0]), [self.cert1, self.cert2, self.cert4, self.cert3])
        self.assertListEqual(list(mock_send.call_args[0][1]), [self.grade1, self.grade2, self.grade4, self.grade3])
        mock_send.reset_mock()

    @mock.patch(TASKS_MODULE + '.send_notifications')
    def test_dry_run(self, mock_send):
        self.options['start_date'] = datetime(2017, 2, 1, 0, 0, tzinfo=timezone.utc)
        self.options['dry_run'] = True
        tasks.handle_notify_credentials(options=self.options, course_keys=[])
        assert not mock_send.called

    @mock.patch(TASKS_MODULE + '.handle_course_cert_awarded')
    @mock.patch(TASKS_MODULE + '.send_grade_if_interesting')
    @mock.patch(TASKS_MODULE + '.handle_course_cert_changed')
    def test_hand_off(self, mock_grade_interesting, mock_program_changed, mock_program_awarded):
        self.options['start_date'] = datetime(2017, 2, 1, 0, 0, tzinfo=timezone.utc)
        tasks.handle_notify_credentials(options=self.options, course_keys=[])
        assert mock_grade_interesting.call_count == 3
        assert mock_program_changed.call_count == 3
        assert mock_program_awarded.call_count == 0
        mock_grade_interesting.reset_mock()
        mock_program_changed.reset_mock()
        mock_program_awarded.reset_mock()

        self.options['start_date'] = datetime(2017, 2, 1, 0, 0, tzinfo=timezone.utc)
        self.options['notify_programs'] = True
        tasks.handle_notify_credentials(options=self.options, course_keys=[])
        assert mock_grade_interesting.call_count == 3
        assert mock_program_changed.call_count == 3
        assert mock_program_awarded.call_count == 1

    @mock.patch(TASKS_MODULE + '.time')
    def test_delay(self, mock_time):
        self.options['start_date'] = datetime(2017, 2, 1, 0, 0, tzinfo=timezone.utc)
        self.options['page_size'] = 2
        tasks.handle_notify_credentials(options=self.options, course_keys=[])
        assert mock_time.sleep.call_count == 0
        mock_time.sleep.reset_mock()

        self.options['start_date'] = datetime(2017, 2, 1, 0, 0, tzinfo=timezone.utc)
        self.options['page_size'] = 2
        self.options['delay'] = 0.2
        tasks.handle_notify_credentials(options=self.options, course_keys=[])
        assert mock_time.sleep.call_count == 2
        # Between each page, twice (2 pages, for certs and grades)
        assert mock_time.sleep.call_args[0][0] == 0.2

    @override_settings(DEBUG=True)
    def test_page_size(self):
        self.options['start_date'] = datetime(2017, 1, 1, 0, 0, tzinfo=timezone.utc)
        reset_queries()
        tasks.handle_notify_credentials(options=self.options, course_keys=[])
        baseline = len(connection.queries)

        self.options['start_date'] = datetime(2017, 1, 1, 0, 0, tzinfo=timezone.utc)
        self.options['page_size'] = 1
        reset_queries()
        tasks.handle_notify_credentials(options=self.options, course_keys=[])
        assert len(connection.queries) == (baseline + 6)
        # two extra page queries each for certs & grades

        self.options['start_date'] = datetime(2017, 1, 1, 0, 0, tzinfo=timezone.utc)
        self.options['page_size'] = 2
        reset_queries()
        tasks.handle_notify_credentials(options=self.options, course_keys=[])
        assert len(connection.queries) == (baseline + 2)
        # one extra page query each for certs & grades

    @mock.patch(TASKS_MODULE + '.send_grade_if_interesting')
    def test_site(self, mock_grade_interesting):
        site_config = SiteConfigurationFactory.create(
            site_values={'course_org_filter': ['testX']}
        )

        self.options['start_date'] = datetime(2017, 1, 1, 0, 0, tzinfo=timezone.utc)
        self.options['site'] = site_config.site.domain
        tasks.handle_notify_credentials(options=self.options, course_keys=[])
        assert mock_grade_interesting.call_count == 1

    @mock.patch(TASKS_MODULE + '.send_notifications')
    def test_send_notifications_failure(self, mock_send):
        self.options['start_date'] = datetime(2017, 1, 31, 0, 0, tzinfo=timezone.utc)
        mock_send.side_effect = boom
        with pytest.raises(Exception):
            tasks.handle_notify_credentials(options=self.options, course_keys=[])

    @mock.patch(TASKS_MODULE + '.send_grade_if_interesting')
    def test_send_grade_failure(self, mock_send_grade):
        self.options['start_date'] = datetime(2017, 1, 31, 0, 0, tzinfo=timezone.utc)
        mock_send_grade.side_effect = boom
        with pytest.raises(Exception):
            tasks.handle_notify_credentials(options=self.options, course_keys=[])

    @ddt.data([True], [False])
    @ddt.unpack
    @mock.patch(TASKS_MODULE + '.send_notifications')
    def test_revoke_program_certs(self, revoke_program_certs, mock_send_notifications):
        """
        This test verifies that the `revoke_program_certs` option is forwarded as expected when included in the options.
        """
        self.options['revoke_program_certs'] = revoke_program_certs
        tasks.handle_notify_credentials(options=self.options, course_keys=[])
        assert mock_send_notifications.called
        mock_call_args = mock_send_notifications.call_args_list[0]
        assert mock_call_args.kwargs['revoke_program_certs'] == revoke_program_certs


@ddt.ddt
@skip_unless_lms
@mock.patch(
    'openedx.core.djangoapps.credentials.models.CredentialsApiConfig.is_learner_issuance_enabled',
    new_callable=mock.PropertyMock,
    return_value=True,
)
@mock.patch(TASKS_MODULE + '.send_grade_to_credentials')
@mock.patch(TASKS_MODULE + '.is_course_run_in_a_program')
class TestSendGradeIfInteresting(TestCase):
    """ Tests for send_grade_if_interesting, the main utility function that sends a grade """

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.key = CourseKey.from_string(CourseRunFactory()['key'])

    @ddt.data(
        [True, 'verified', 'downloadable'],
        [True, 'professional', 'downloadable'],
        [True, 'no-id-professional', 'downloadable'],
        [True, 'credit', 'downloadable'],
        [True, 'verified', 'notpassing'],
        [True, 'masters', 'downloadable'],
        [True, 'masters', 'notpassing'],
        [False, 'audit', 'downloadable'],
        [False, 'professional', 'generating'],
        [False, 'no-id-professional', 'generating'],
    )
    @ddt.unpack
    def test_send_grade_if_right_cert(self, called, mode, status, mock_is_course_run_in_a_program,
                                      mock_send_grade_to_credentials, _mock_is_learner_issuance_enabled):
        mock_is_course_run_in_a_program.return_value = True

        # Test direct send
        tasks.send_grade_if_interesting(self.user, self.key, mode, status, 'A', 1.0)
        assert mock_send_grade_to_credentials.delay.called is called
        mock_send_grade_to_credentials.delay.reset_mock()

        # Test query
        GeneratedCertificateFactory(
            user=self.user,
            course_id=self.key,
            status=status,
            mode=mode
        )
        tasks.send_grade_if_interesting(self.user, self.key, None, None, 'A', 1.0)
        assert mock_send_grade_to_credentials.delay.called is called

    def test_send_grade_missing_cert(self, _, mock_send_grade_to_credentials, _mock_is_learner_issuance_enabled):
        tasks.send_grade_if_interesting(self.user, self.key, None, None, 'A', 1.0)
        assert not mock_send_grade_to_credentials.delay.called

    @ddt.data([True], [False])
    @ddt.unpack
    def test_send_grade_if_in_a_program(self, in_program, mock_is_course_run_in_a_program,
                                        mock_send_grade_to_credentials, _mock_is_learner_issuance_enabled):
        mock_is_course_run_in_a_program.return_value = in_program
        tasks.send_grade_if_interesting(self.user, self.key, 'verified', 'downloadable', 'A', 1.0)
        assert mock_send_grade_to_credentials.delay.called is in_program

    def test_send_grade_queries_grade(self, mock_is_course_run_in_a_program, mock_send_grade_to_credentials,
                                      _mock_is_learner_issuance_enabled):
        mock_is_course_run_in_a_program.return_value = True

        last_updated = datetime.now()
        with mock_passing_grade('B', 0.81, last_updated):
            tasks.send_grade_if_interesting(self.user, self.key, 'verified', 'downloadable', None, None)
        assert mock_send_grade_to_credentials.delay.called
        assert mock_send_grade_to_credentials.delay.call_args[0] == (
            self.user.username, str(self.key), True, 'B', 0.81, last_updated
        )
        mock_send_grade_to_credentials.delay.reset_mock()

    def test_send_grade_without_issuance_enabled(self, _mock_is_course_run_in_a_program,
                                                 mock_send_grade_to_credentials, mock_is_learner_issuance_enabled):
        mock_is_learner_issuance_enabled.return_value = False
        tasks.send_grade_if_interesting(self.user, self.key, 'verified', 'downloadable', None, None)
        assert mock_is_learner_issuance_enabled.called
        assert not mock_send_grade_to_credentials.delay.called

    def test_send_grade_records_enabled(self, _mock_is_course_run_in_a_program, mock_send_grade_to_credentials,
                                        _mock_is_learner_issuance_enabled):
        site_config = SiteConfigurationFactory.create(
            site_values={'course_org_filter': [self.key.org]}
        )

        # Correctly sent
        tasks.send_grade_if_interesting(self.user, self.key, 'verified', 'downloadable', None, None)
        assert mock_send_grade_to_credentials.delay.called
        mock_send_grade_to_credentials.delay.reset_mock()

        # Correctly not sent
        site_config.site_values['ENABLE_LEARNER_RECORDS'] = False
        site_config.save()
        tasks.send_grade_if_interesting(self.user, self.key, 'verified', 'downloadable', None, None)
        assert not mock_send_grade_to_credentials.delay.called

    def test_send_grade_records_disabled_globally(
        self, _mock_is_course_run_in_a_program, mock_send_grade_to_credentials,
        _mock_is_learner_issuance_enabled
    ):
        assert is_learner_records_enabled()
        with override_settings(ENABLE_LEARNER_RECORDS=False):
            assert not is_learner_records_enabled()
            tasks.send_grade_if_interesting(self.user, self.key, 'verified', 'downloadable', None, None)
        assert not mock_send_grade_to_credentials.delay.called


@skip_unless_lms
@mock.patch(TASKS_MODULE + '.get_programs')
class TestIsCourseRunInAProgramUtil(TestCase):
    """ Tests helper utility functions in our signal handling. """

    def setUp(self):
        super().setUp()
        self.site = SiteFactory()
        self.course_run = CourseRunFactory()
        course = CourseFactory(course_runs=[self.course_run])
        self.data = [ProgramFactory(courses=[course])]

    def test_is_course_run_in_a_program_success(self, mock_get_programs):
        mock_get_programs.return_value = self.data
        assert tasks.is_course_run_in_a_program(self.course_run['key'])
        assert mock_get_programs.call_args[0] == (self.site,)

    def test_is_course_run_in_a_program_failure(self, mock_get_programs):
        mock_get_programs.return_value = self.data
        course_run2 = CourseRunFactory()
        assert not tasks.is_course_run_in_a_program(course_run2['key'])


@ddt.ddt
@skip_unless_lms
@mock.patch('openedx.core.djangoapps.credentials.tasks.v1.tasks.handle_course_cert_changed')
@mock.patch('openedx.core.djangoapps.credentials.tasks.v1.tasks.handle_course_cert_awarded')
@mock.patch('openedx.core.djangoapps.credentials.tasks.v1.tasks.handle_course_cert_revoked')
@mock.patch('openedx.core.djangoapps.credentials.tasks.v1.tasks.send_grade_if_interesting')
class TestSendNotifications(TestCase):
    """
    Unit Tests for the `send_notifications` function in the `tasks.py` file.
    """
    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.course_run = CourseRunFactory()
        self.course_key = CourseKey.from_string(self.course_run['key'])
        self.course = CourseFactory(course_runs=[self.course_run])
        self.site = SiteConfigurationFactory.create(
            site_values={'course_org_filter': [self.course_key.org]},
        )
        self.certificate = GeneratedCertificateFactory(
            user=self.user,
            course_id=self.course_run['key'],
            status=CertificateStatuses.downloadable
        )
        self.grade = PersistentCourseGrade.objects.create(
            user_id=self.user.id,
            course_id=self.course_run['key'],
            percent_grade=1.0
        )

    def _get_certs_qs(self):
        """
        The function under test expects a QuerySet containing certificate information, this retrieves the data needed
        to invoke the `send_notifications` function in the form that is expected. We leverage a utility function that
        the product code uses.
        """
        return get_recently_modified_certificates([self.course_run['key']], None, None, [self.user.id])

    def _get_grades_qs(self):
        """
        The function under test expects a QuerySet containing grade information, this retrieves the data needed
        to invoke the `send_notifications` function in the form that is expected.  We leverage a utility function that
        the product code uses.
        """
        users = User.objects.filter(id__in=[self.user.id])
        return get_recently_modified_grades([self.course_run['key']], None, None, users)

    def _build_expected_signal_args(self):
        return {
            'sender': None,
            'user': self.user,
            'course_key': self.certificate.course_id,
            'mode': self.certificate.mode,
            'status': self.certificate.status,
            'verbose': False
        }

    def test_send_notifications(self, mock_interesting, mock_revoked, mock_awarded, mock_changed):
        """
        A test case that verifies the `send_notifications` functions behavior when the `notify_programs` flag is False.
        """
        certs = self._get_certs_qs()
        grades = self._get_grades_qs()

        tasks.send_notifications(
            certs,
            grades,
            self.site
        )

        expected_signal_args = self._build_expected_signal_args()
        mock_changed.assert_called_once_with(**expected_signal_args)
        mock_interesting.assert_called_once_with(
            self.user,
            self.grade.course_id,
            self.certificate.mode,
            self.certificate.status,
            self.grade.letter_grade,
            self.grade.percent_grade,
            grade_last_updated=self.grade.modified,
            verbose=False
        )
        mock_revoked.assert_not_called()
        mock_awarded.assert_not_called()

    @ddt.data(
        ['downloadable', True],
        ['unavailable', False]
    )
    @ddt.unpack
    def test_send_notifications_notify_programs(
        self,
        cert_status,
        award_expected,
        mock_interesting,
        mock_revoked,
        mock_awarded,
        mock_changed
    ):
        """
        Test cases that verify the `send_notifications` functions behavior when the `notify_programs` flag is True.
        """
        certs = self._get_certs_qs()
        grades = self._get_grades_qs()

        self.certificate.status = cert_status
        self.certificate.save()

        tasks.send_notifications(
            certs,
            grades,
            self.site,
            notify_programs=True
        )

        expected_signal_args = self._build_expected_signal_args()
        mock_changed.assert_called_once_with(**expected_signal_args)
        mock_interesting.assert_called_once_with(
            self.user,
            self.grade.course_id,
            self.certificate.mode,
            self.certificate.status,
            self.grade.letter_grade,
            self.grade.percent_grade,
            grade_last_updated=self.grade.modified,
            verbose=False
        )
        mock_revoked.assert_not_called()
        if award_expected:
            mock_awarded.assert_called_once_with(**expected_signal_args)
        else:
            mock_awarded.assert_not_called()

    @ddt.data(
        ['downloadable', False],
        ['unavailable', True]
    )
    @ddt.unpack
    def test_send_notifications_revoke_programs(
        self,
        cert_status,
        revoke_expected,
        mock_interesting,
        mock_revoked,
        mock_awarded,
        mock_changed
    ):
        """
        Test cases that verify the `send_notifications` functions behavior when the `revoke_program_certs` flag is True.
        """
        certs = self._get_certs_qs()
        grades = self._get_grades_qs()

        self.certificate.status = cert_status
        self.certificate.save()

        tasks.send_notifications(
            certs,
            grades,
            self.site,
            notify_programs=True,
            revoke_program_certs=True
        )

        expected_signal_args = self._build_expected_signal_args()
        mock_changed.assert_called_once_with(**expected_signal_args)
        mock_interesting.assert_called_once_with(
            self.user,
            self.grade.course_id,
            self.certificate.mode,
            self.certificate.status,
            self.grade.letter_grade,
            self.grade.percent_grade,
            grade_last_updated=self.grade.modified,
            verbose=False
        )
        if revoke_expected:
            mock_revoked.assert_called_once_with(**expected_signal_args)
            mock_awarded.mock_not_called()
        else:
            mock_revoked.assert_not_called()
            mock_awarded.assert_called_once_with(**expected_signal_args)


@skip_unless_lms
class TestBackfillDateForAllCourseRuns(TestCase):
    """
    Unit Tests for the `backfill_date_for_all_course_runs` Celery task.
    """
    def setUp(self):
        super().setUp()
        self.co_instructor_paced_cdb_early_no_info_key = "course-v1:OpenEdX+InstructorPacedEarly+Run1"
        self.co_instructor_paced_cbd_end_key = "course-v1:OpenEdX+InstructorPacedEnd+Run1"
        self.co_instructor_paced_cdb_end_with_date_key = "course-v1:OpenEdX+InstructorPacedCAD+Run1"
        self.co_self_paced_key = "course-v1:OpenEdX+SelfPaced+Run1"
        self.course_run_keys = [
            self.co_instructor_paced_cdb_early_no_info_key,
            self.co_instructor_paced_cbd_end_key,
            self.co_instructor_paced_cdb_end_with_date_key,
            self.co_self_paced_key,
        ]
        self.co_instructor_paced_cdb_early_no_info = CourseOverviewFactory(
            certificate_available_date=None,
            certificates_display_behavior=CertificatesDisplayBehaviors.EARLY_NO_INFO,
            id=CourseKey.from_string(self.co_instructor_paced_cdb_early_no_info_key),
            self_paced=False,
        )
        self.co_instructor_paced_cbd_end = CourseOverviewFactory(
            certificate_available_date=None,
            certificates_display_behavior=CertificatesDisplayBehaviors.END,
            id=CourseKey.from_string(self.co_instructor_paced_cbd_end_key),
            self_paced=False,
        )
        self.co_instructor_paced_cdb_end_with_date = CourseOverviewFactory(
            certificate_available_date=(datetime.now(timezone.utc) + timedelta(days=30)),
            certificates_display_behavior=CertificatesDisplayBehaviors.END_WITH_DATE,
            id=CourseKey.from_string(self.co_instructor_paced_cdb_end_with_date_key),
            self_paced=False,
        )
        self.co_self_paced = CourseOverviewFactory(
            id=CourseKey.from_string(self.co_self_paced_key),
            self_paced=True,
        )

    @mock.patch(
        'openedx.core.djangoapps.credentials.tasks.v1.tasks.update_certificate_available_date_on_course_update.delay'
    )
    def test_backfill_dates(self, mock_update):
        """
        Tests that the `backfill_date_for_all_course_runs` task enqueues the expected number of
        `update_certificate_available_date_on_course_update` subtasks. We also capture and verify the contents of the
        logs to ensure debugging capabilities by humans.
        """
        expected_messages = [
            # instructor-paced, cdb=early_no_info msg
            "Enqueueing an `update_certificate_available_date_on_course_update` task for course run "
            f"`{self.co_instructor_paced_cdb_early_no_info.id}`, "
            f"self_paced={self.co_instructor_paced_cdb_early_no_info.self_paced}, "
            f"end={self.co_instructor_paced_cdb_early_no_info.end}, "
            f"available_date={self.co_instructor_paced_cdb_early_no_info.certificate_available_date}, and "
            f"display_behavior={self.co_instructor_paced_cdb_early_no_info.certificates_display_behavior.value}",
            # instructor-paced, cdb=end msg
            "Enqueueing an `update_certificate_available_date_on_course_update` task for course run "
            f"`{self.co_instructor_paced_cbd_end.id}`, "
            f"self_paced={self.co_instructor_paced_cbd_end.self_paced}, "
            f"end={self.co_instructor_paced_cbd_end.end}, "
            f"available_date={self.co_instructor_paced_cbd_end.certificate_available_date}, and "
            f"display_behavior={self.co_instructor_paced_cbd_end.certificates_display_behavior.value}",
            # instructor-paced, cdb=end_with_date msg
            "Enqueueing an `update_certificate_available_date_on_course_update` task for course run "
            f"`{self.co_instructor_paced_cdb_end_with_date.id}`, "
            f"self_paced={self.co_instructor_paced_cdb_end_with_date.self_paced}, "
            f"end={self.co_instructor_paced_cdb_end_with_date.end}, "
            f"available_date={self.co_instructor_paced_cdb_end_with_date.certificate_available_date}, and "
            f"display_behavior={self.co_instructor_paced_cdb_end_with_date.certificates_display_behavior.value}",
            # self-paced course run msg
            "Enqueueing an `update_certificate_available_date_on_course_update` task for course run "
            f"`{self.co_self_paced.id}`, self_paced={self.co_self_paced.self_paced}, end={self.co_self_paced.end}, "
            f"available_date={self.co_self_paced.certificate_available_date}, and "
            f"display_behavior={self.co_self_paced.certificates_display_behavior}",
        ]

        with LogCapture(LOGGER_NAME, level=logging.INFO) as log:
            tasks.backfill_date_for_all_course_runs.delay()

        # verify the content of captured log messages
        for message in expected_messages:
            log.check_present((LOGGER_NAME, 'INFO', message))

        # verify that our mocked function has the expected calls
        assert mock_update.call_count == len(expected_messages)
        for mock_call in mock_update.call_args_list:
            assert mock_call.args[0] in self.course_run_keys
