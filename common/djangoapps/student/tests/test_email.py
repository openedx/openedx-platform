# lint-amnesty, pylint: disable=missing-module-docstring

import json
from string import capwords
from unittest.mock import Mock, patch

import ddt
import pytest
from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core import mail
from django.db import transaction
from django.http import HttpResponse
from django.test import TransactionTestCase, override_settings
from django.test.client import RequestFactory
from django.urls import reverse
from django.utils.html import escape
from testfixtures import LogCapture

from common.djangoapps.edxmako.shortcuts import marketing_link
from common.djangoapps.student.email_helpers import generate_proctoring_requirements_email_context
from common.djangoapps.student.emails import send_proctoring_requirements_email
from common.djangoapps.student.models import PendingEmailChange, Registration, UserProfile
from common.djangoapps.student.tests.factories import PendingEmailChangeFactory, UserFactory
from common.djangoapps.student.views import (
    SETTING_CHANGE_INITIATED,
    confirm_email_change,
    do_email_change_request,
    validate_new_email,
)
from common.djangoapps.third_party_auth.views import inactive_user_view
from common.djangoapps.util.testing import EventTestMixin
from openedx.core.djangoapps.ace_common.tests.mixins import EmailTemplateTagMixin
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangolib.testing.utils import CacheIsolationMixin, CacheIsolationTestCase, skip_unless_lms
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


class TestException(Exception):
    """
    Exception used for testing that nothing will catch explicitly
    """
    pass  # lint-amnesty, pylint: disable=unnecessary-pass


def mock_render_to_string(template_name, context):
    """
    Return a string that encodes template_name and context
    """
    return str((template_name, sorted(context.items())))


def mock_render_to_response(template_name, context):
    """
    Return an HttpResponse with content that encodes template_name and context
    """
    # This simulates any db access in the templates.
    UserProfile.objects.exists()
    return HttpResponse(mock_render_to_string(template_name, context))


class EmailTestMixin:
    """
    Adds useful assertions for testing `email_user`
    """

    def assertEmailUser(self, email_user, subject_template, subject_context, body_template, body_context):
        """
        Assert that `email_user` was used to send and email with the supplied subject and body

        `email_user`: The mock `django.contrib.auth.models.User.email_user` function
            to verify
        `subject_template`: The template to have been used for the subject
        `subject_context`: The context to have been used for the subject
        `body_template`: The template to have been used for the body
        `body_context`: The context to have been used for the body
        """
        email_user.assert_called_with(
            mock_render_to_string(subject_template, subject_context),
            mock_render_to_string(body_template, body_context),
            configuration_helpers.get_value('email_from_address', settings.DEFAULT_FROM_EMAIL)
        )

    def append_allowed_hosts(self, hostname):
        """
        Append hostname to settings.ALLOWED_HOSTS
        """
        settings.ALLOWED_HOSTS.append(hostname)
        self.addCleanup(settings.ALLOWED_HOSTS.pop)


@ddt.ddt
@skip_unless_lms
class ActivationEmailTests(EmailTemplateTagMixin, CacheIsolationTestCase):
    """
    Test sending of the activation email.
    """

    ACTIVATION_SUBJECT = f"Action Required: Activate your {settings.PLATFORM_NAME} account"

    # Text fragments we expect in the body of an email
    # sent from an OpenEdX installation.
    OPENEDX_FRAGMENTS = [
        (
            "Use the link below to activate your account to access engaging, "
            "high-quality {platform_name} courses. Note that you will not be able to log back into your "
            "account until you have activated it.".format(
                platform_name=settings.PLATFORM_NAME
            )
        ),
        f"{settings.LMS_ROOT_URL}/activate/",
        "If you need help, please use our web form at ", (
            settings.ACTIVATION_EMAIL_SUPPORT_LINK or settings.SUPPORT_SITE_LINK
        ),
        settings.CONTACT_EMAIL,
        "This email message was automatically sent by ",
        settings.LMS_ROOT_URL,
        " because someone attempted to create an account on {platform_name}".format(
            platform_name=settings.PLATFORM_NAME
        ),
        " using this email address."
    ]

    @ddt.data('plain_text', 'html')
    def test_activation_email(self, test_body_type):
        self._create_account()
        self._assert_activation_email(self.ACTIVATION_SUBJECT, self.OPENEDX_FRAGMENTS, test_body_type)

    def _create_account(self):
        """
        Create an account, triggering the activation email.
        """
        url = reverse('create_account')
        params = {
            'username': 'test_user',
            'email': 'test_user@example.com',
            'password': 'Password1234',
            'name': 'Test User',
            'honor_code': True,
            'terms_of_service': True
        }
        resp = self.client.post(url, params)
        assert resp.status_code == 200, "Could not create account (status {status}). The response was {response}"\
            .format(status=resp.status_code, response=resp.content)

    def _assert_activation_email(self, subject, body_fragments, test_body_type):
        """
        Verify that the activation email was sent.
        """
        assert len(mail.outbox) == 1
        msg = mail.outbox[0]
        assert msg.subject == subject

        body_text = {
            'plain_text': msg.body,
            'html': msg.alternatives[0][0]
        }
        assert test_body_type in body_text
        body_to_be_tested = body_text[test_body_type]

        for fragment in body_fragments:
            assert fragment in body_to_be_tested

    def test_do_not_send_email_and_do_activate(self):
        """
        Tests that when an inactive user logs-in using the social auth,
        an activation email is not sent.
        """
        pipeline_partial = {
            'kwargs': {
                'social': {
                    'uid': 'fake uid'
                }
            }
        }
        user = UserFactory(is_active=False)
        Registration().register(user)
        request = RequestFactory().get(settings.SOCIAL_AUTH_INACTIVE_USER_URL)
        request.user = user
        with patch('common.djangoapps.student.views.management.compose_and_send_activation_email') as email:
            with patch('common.djangoapps.third_party_auth.provider.Registry.get_from_pipeline') as reg:
                with patch('common.djangoapps.third_party_auth.pipeline.get', return_value=pipeline_partial):
                    with patch('common.djangoapps.third_party_auth.pipeline.running', return_value=True):
                        with patch('common.djangoapps.third_party_auth.is_enabled', return_value=True):
                            reg.skip_email_verification = True
                            inactive_user_view(request)
                            assert user.is_active
                            assert email.called is False, 'method should not have been called'

    @patch('common.djangoapps.student.views.management.send_activation_email.delay')
    def test_activation_email_exception(self, mock_task):
        """
        Test that if an exception occurs within send_activation_email, it is logged
        and not raised.
        """
        mock_task.side_effect = Exception('BOOM!')
        inactive_user = UserFactory(is_active=False)
        Registration().register(inactive_user)
        request = RequestFactory().get(settings.SOCIAL_AUTH_INACTIVE_USER_URL)
        request.user = inactive_user
        with patch('common.djangoapps.edxmako.request_context.get_current_request', return_value=request):
            with patch('common.djangoapps.third_party_auth.pipeline.running', return_value=False):
                with LogCapture() as logger:
                    inactive_user_view(request)
                    assert mock_task.called is True, 'method should have been called'
                    logger.check_present(
                        (
                            'edx.student',
                            'ERROR',
                            f'Activation email task failed for user {inactive_user.id}.'
                        )
                    )

    @patch('common.djangoapps.student.views.management.compose_activation_email')
    def test_send_email_to_inactive_user(self, email):
        """
        Tests that when an inactive user logs-in using the social auth, system
        sends an activation email to the user.
        """
        inactive_user = UserFactory(is_active=False)
        Registration().register(inactive_user)
        request = RequestFactory().get(settings.SOCIAL_AUTH_INACTIVE_USER_URL)
        request.user = inactive_user
        with patch('common.djangoapps.edxmako.request_context.get_current_request', return_value=request):
            with patch('common.djangoapps.third_party_auth.pipeline.running', return_value=False):
                inactive_user_view(request)
                assert email.called is True, 'method should have been called'


@ddt.ddt
@patch.dict('django.conf.settings.FEATURES', {'ENABLE_SPECIAL_EXAMS': True})
@override_settings(ACCOUNT_MICROFRONTEND_URL='http://account-mfe')
@skip_unless_lms
class ProctoringRequirementsEmailTests(EmailTemplateTagMixin, ModuleStoreTestCase):
    """
    Test sending of the proctoring requirements email.
    """

    # pylint: disable=no-member
    def setUp(self):
        super().setUp()
        self.course = None
        self.user = UserFactory()

    @ddt.data('course_run_1', 'matt\'s course', 'matt＇s run')
    def test_send_proctoring_requirements_email(self, course_run_name):
        self.course = CourseFactory(
            display_name=course_run_name,
            enable_proctored_exams=True
        )
        context = generate_proctoring_requirements_email_context(self.user, self.course.id)
        send_proctoring_requirements_email(context)
        self._assert_email()

    def test_send_proctoring_requirements_email_honor(self):
        self.course = CourseFactory(
            display_name='honor code on course',
            enable_proctored_exams=True
        )
        context = generate_proctoring_requirements_email_context(self.user, self.course.id)
        send_proctoring_requirements_email(context)
        self._assert_email()

    def _assert_email(self):
        """
        Verify that the email was sent.
        """
        assert len(mail.outbox) == 1

        message = mail.outbox[0]
        text = message.body
        html = message.alternatives[0][0]

        assert message.subject == f"Proctoring requirements for {self.course.display_name}"

        appears = self._get_fragments()
        for fragment in appears:
            self.assertIn(fragment, text)
            self.assertIn(fragment, html)

    def _get_fragments(self):
        """
        Provide a tuple of string[]s that should be (in, not_in) the email
        """
        course_block = modulestore().get_course(self.course.id)
        proctoring_provider = capwords(course_block.proctoring_provider.replace('_', ' '))
        fragments = [
            (
                "You are enrolled in {} at {}. This course contains proctored exams.".format(
                    self.course.display_name,
                    settings.PLATFORM_NAME
                )
            ),
            (
                "Proctored exams are timed exams that you take while proctoring software monitors "
                "your computer's desktop, webcam video, and audio."
            ),
            proctoring_provider,
            escape(
                "Carefully review the system requirements as well as the steps to take a proctored "
                "exam in order to ensure that you are prepared."
            ),
            settings.PROCTORING_SETTINGS.get('LINK_URLS', {}).get('faq', ''),
        ]
        return fragments


@skip_unless_lms
class EmailChangeRequestTests(EventTestMixin, EmailTemplateTagMixin, CacheIsolationTestCase):
    """
    Test changing a user's email address
    """

    def setUp(self, tracker='common.djangoapps.student.views.management.tracker'):
        super().setUp(tracker)
        self.user = UserFactory.create()
        self.new_email = 'new.email@edx.org'
        self.req_factory = RequestFactory()
        self.request = self.req_factory.post('unused_url', data={
            'password': 'Password1234',
            'new_email': self.new_email
        })
        self.request.user = self.user
        self.user.email_user = Mock()

    def do_email_validation(self, email):
        """
        Executes validate_new_email, returning any resulting error message.
        """
        try:
            validate_new_email(self.request.user, email)
        except ValueError as err:
            return str(err)

    def do_email_change(self, user, email, activation_key=None):
        """
        Executes do_email_change_request, returning any resulting error message.
        """
        with patch('crum.get_current_request', return_value=self.fake_request):
            do_email_change_request(user, email, activation_key)

    def assertFailedRequest(self, response_data, expected_error):
        """
        Assert that `response_data` indicates a failed request that returns `expected_error`
        """
        assert response_data['success'] is False
        assert expected_error == response_data['error']
        assert self.user.email_user.called is False

    @patch('common.djangoapps.student.views.management.render_to_string',
           Mock(side_effect=mock_render_to_string, autospec=True))  # lint-amnesty, pylint: disable=line-too-long
    def test_duplicate_activation_key(self):
        """
        Assert that if two users change Email address simultaneously, no error is thrown
        """

        # New emails for the users
        user1_new_email = "valid_user1_email@example.com"
        user2_new_email = "valid_user2_email@example.com"

        # Create a another user 'user2' & make request for change email
        user2 = UserFactory.create(email=self.new_email, password="test2")

        # Send requests & ensure no error was thrown
        self.do_email_change(self.user, user1_new_email)
        self.do_email_change(user2, user2_new_email)

    def test_invalid_emails(self):
        """
        Assert the expected error message from the email validation method for an invalid
        (improperly formatted) email address.
        """
        for email in ('bad_email', 'bad_email@', '@bad_email'):
            assert self.do_email_validation(email) == 'Valid e-mail address required.'

    def test_change_email_to_existing_value(self):
        """
        Test the error message if user attempts to change email to the existing value.
        """
        assert self.do_email_validation(self.user.email) == 'Old email is the same as the new email.'

    @patch('django.core.mail.EmailMultiAlternatives.send')
    def test_email_failure(self, send_mail):
        """
        Test the return value if sending the email for the user to click fails.
        """
        send_mail.side_effect = [Exception, None]
        with self.assertRaisesRegex(ValueError, 'Unable to send email activation link. Please try again later.'):
            self.do_email_change(self.user, "valid@email.com")

        self.assert_no_events_were_emitted()

    def test_email_success(self):
        """
        Test email was sent if no errors encountered.
        """
        old_email = self.user.email
        new_email = "valid@example.com"
        registration_key = "test-registration-key"

        self.do_email_change(self.user, new_email, registration_key)

        self._assert_email(
            subject='Request to change édX account e-mail',
            body_fragments=[
                'We received a request to change the e-mail associated with',
                'your édX account from {old_email} to {new_email}.'.format(
                    old_email=old_email,
                    new_email=new_email,
                ),
                'If this is correct, please confirm your new e-mail address by visiting:',
                f'http://edx.org/email_confirm/{registration_key}',
                'Please do not reply to this e-mail; if you require assistance,',
                'check the help section of the édX web site.',
            ],
        )

        self.assert_event_emitted(
            SETTING_CHANGE_INITIATED, user_id=self.user.id, setting='email', old=old_email, new=new_email
        )

    def _assert_email(self, subject, body_fragments):
        """
        Verify that the email was sent.
        """
        assert len(mail.outbox) == 1
        assert len(body_fragments) > 1, 'Should provide at least two body fragments'

        message = mail.outbox[0]
        text = message.body
        html = message.alternatives[0][0]

        assert message.subject == subject
        for body in text, html:
            for fragment in body_fragments:
                assert fragment in body


@ddt.ddt
@patch('common.djangoapps.student.views.management.render_to_response',
       Mock(side_effect=mock_render_to_response, autospec=True))  # lint-amnesty, pylint: disable=line-too-long
@patch('common.djangoapps.student.views.management.render_to_string',
       Mock(side_effect=mock_render_to_string, autospec=True))  # lint-amnesty, pylint: disable=line-too-long
class EmailChangeConfirmationTests(EmailTestMixin, EmailTemplateTagMixin, CacheIsolationMixin, TransactionTestCase):
    """
    Test that confirmation of email change requests function even in the face of exceptions thrown while sending email
    """

    def setUp(self):
        super().setUp()
        self.clear_caches()
        self.addCleanup(self.clear_caches)
        self.user = UserFactory.create()
        self.profile = UserProfile.objects.get(user=self.user)
        self.req_factory = RequestFactory()
        self.request = self.req_factory.get('unused_url')
        self.request.user = self.user
        self.pending_change_request = PendingEmailChangeFactory.create(user=self.user)
        self.key = self.pending_change_request.activation_key

        # Expected subject of the email
        self.email_subject = "Email Change Confirmation for {platform_name}".format(
            platform_name=settings.PLATFORM_NAME
        )

        # Text fragments we expect in the body of the confirmation email
        self.email_fragments = [
            "This is to confirm that you changed the e-mail associated with {platform_name}"
            " from {old_email} to {new_email}. If you did not make this request, please contact us immediately."
            " Contact information is listed at:".format(
                platform_name=settings.PLATFORM_NAME,
                old_email=self.user.email,
                new_email=PendingEmailChange.objects.get(activation_key=self.key).new_email
            ),
            "We keep a log of old e-mails, so if this request was unintentional, we can investigate."
        ]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.start_cache_isolation()

    @classmethod
    def tearDownClass(cls):
        cls.end_cache_isolation()
        super().tearDownClass()

    def assertRolledBack(self):
        """
        Assert that no changes to user, profile, or pending email have been made to the db
        """
        assert self.user.email == User.objects.get(username=self.user.username).email
        assert self.profile.meta == UserProfile.objects.get(user=self.user).meta
        assert PendingEmailChange.objects.count() == 1

    def assertFailedBeforeEmailing(self):
        """
        Assert that the function failed before emailing a user
        """
        self.assertRolledBack()
        assert len(mail.outbox) == 0

    def check_confirm_email_change(self, expected_template, expected_context):
        """
        Call `confirm_email_change` and assert that the content was generated as expected

        `expected_template`: The name of the template that should have been used
            to generate the content
        `expected_context`: The context dictionary that should have been used to
            generate the content
        """
        response = confirm_email_change(self.request, self.key)
        assert response.status_code == 200
        assert mock_render_to_response(expected_template, expected_context).content.decode('utf-8') \
               == response.content.decode('utf-8')

    def assertChangeEmailSent(self, test_body_type):
        """
        Assert that the correct email was sent to confirm an email change, the same
        email contents should be sent to both old and new addresses
        """
        self.check_confirm_email_change('email_change_successful.html', {
            'old_email': self.user.email,
            'new_email': self.pending_change_request.new_email
        })

        # Must have two items in outbox: one for old email, another for new email
        assert len(mail.outbox) == 2

        use_https = self.request.is_secure()
        if settings.FEATURES['ENABLE_MKTG_SITE']:
            contact_link = marketing_link('CONTACT')
        else:
            contact_link = '{protocol}://{site}{link}'.format(
                protocol='https' if use_https else 'http',
                site=settings.SITE_NAME,
                link=reverse('contact'),
            )

        # Verifying contents
        for msg in mail.outbox:
            assert msg.subject == self.email_subject

            body_text = {
                'plain_text': msg.body,
                'html': msg.alternatives[0][0]
            }
            assert test_body_type in body_text

            body_to_be_tested = body_text[test_body_type]
            for fragment in self.email_fragments:
                assert fragment in body_to_be_tested

            assert contact_link in body_to_be_tested

    def test_not_pending(self):
        self.key = 'not_a_key'
        self.check_confirm_email_change('invalid_email_key.html', {})
        self.assertFailedBeforeEmailing()

    def test_duplicate_email(self):
        UserFactory.create(email=self.pending_change_request.new_email)
        self.check_confirm_email_change('email_exists.html', {})
        self.assertFailedBeforeEmailing()

    @skip_unless_lms
    @patch('common.djangoapps.student.views.management.ace')
    def test_old_email_fails(self, ace_mail):
        ace_mail.send.side_effect = [Exception, None]
        self.check_confirm_email_change('email_change_failed.html', {
            'email': self.user.email,
        })
        assert ace_mail.send.call_count == 1
        self.assertRolledBack()

    @skip_unless_lms
    @patch('common.djangoapps.student.views.management.ace')
    def test_new_email_fails(self, ace_mail):
        ace_mail.send.side_effect = [None, Exception]
        self.check_confirm_email_change('email_change_failed.html', {
            'email': self.pending_change_request.new_email
        })
        assert ace_mail.send.call_count == 2
        self.assertRolledBack()

    @skip_unless_lms
    @override_settings(MKTG_URLS={'ROOT': 'https://dummy-root', 'CONTACT': '/help/contact-us'})
    @patch('common.djangoapps.student.signals.signals.USER_EMAIL_CHANGED.send')
    @ddt.data(
        ('plain_text', False),
        ('plain_text', True),
        ('html', False),
        ('html', True)
    )
    @ddt.unpack
    def test_successful_email_change(self, test_body_type, test_marketing_enabled, mock_email_change_signal):
        with patch.dict(settings.FEATURES, {'ENABLE_MKTG_SITE': test_marketing_enabled}):
            self.assertChangeEmailSent(test_body_type)
            assert mock_email_change_signal.called

        meta = json.loads(UserProfile.objects.get(user=self.user).meta)
        assert 'old_emails' in meta
        assert self.user.email == meta['old_emails'][0][0]
        assert self.pending_change_request.new_email == User.objects.get(username=self.user.username).email
        assert PendingEmailChange.objects.count() == 0

    @patch('common.djangoapps.student.views.PendingEmailChange.objects.get', Mock(side_effect=TestException))
    def test_always_rollback(self):
        connection = transaction.get_connection()
        with patch.object(connection, 'rollback', wraps=connection.rollback) as mock_rollback:
            with pytest.raises(TestException):
                confirm_email_change(self.request, self.key)

            mock_rollback.assert_called_with()


@skip_unless_lms
class SecondaryEmailChangeRequestTests(EventTestMixin, EmailTemplateTagMixin, CacheIsolationTestCase):
    """
    Test changing a user's email address
    """

    def setUp(self, tracker='common.djangoapps.student.views.management.tracker'):
        super().setUp(tracker)
        self.user = UserFactory.create()
        self.new_secondary_email = 'new.secondary.email@edx.org'
        self.req_factory = RequestFactory()
        self.request = self.req_factory.post('unused_url', data={
            'password': 'Password1234',
            'new_email': self.new_secondary_email
        })
        self.request.user = self.user
        self.user.email_user = Mock()

    def do_email_validation(self, email):
        """
        Executes validate_new_secondary_email, returning any resulting error message.
        """
        try:
            validate_new_email(self.request.user, email)
        except ValueError as err:
            return str(err)

    def do_secondary_email_change(self, user, email, activation_key=None):
        """
        Executes do_secondary_email_change_request, returning any resulting error message.
        """
        with patch('crum.get_current_request', return_value=self.fake_request):
            do_email_change_request(
                user=user,
                new_email=email,
                activation_key=activation_key,
                secondary_email_change_request=True
            )

    def assertFailedRequest(self, response_data, expected_error):
        """
        Assert that `response_data` indicates a failed request that returns `expected_error`
        """
        assert not response_data['success']
        assert expected_error == response_data['error']
        assert not self.user.email_user.called

    def test_invalid_emails(self):
        """
        Assert the expected error message from the email validation method for an invalid
        (improperly formatted) email address.
        """
        for email in ('bad_email', 'bad_email@', '@bad_email'):
            assert self.do_email_validation(email) == 'Valid e-mail address required.'

    @patch('django.core.mail.EmailMultiAlternatives.send')
    def test_email_failure(self, send_mail):
        """
        Test the return value if sending the email for the user to click fails.
        """
        send_mail.side_effect = [Exception, None]
        with self.assertRaisesRegex(ValueError, 'Unable to send email activation link. Please try again later.'):
            self.do_secondary_email_change(self.user, "valid@email.com")

        self.assert_no_events_were_emitted()

    def test_email_success(self):
        """
        Test email was sent if no errors encountered.
        """
        new_email = "valid@example.com"
        registration_key = "test-registration-key"

        self.do_secondary_email_change(self.user, new_email, registration_key)

        self._assert_email(
            subject='Confirm your recovery email for édX',
            body_fragments=[
                'You\'ve registered this recovery email address for édX.',
                'If you set this email address, click "confirm email."',
                'If you didn\'t request this change, you can disregard this email.',
                f'http://edx.org/activate_secondary_email/{registration_key}',

            ],
        )

    def _assert_email(self, subject, body_fragments):
        """
        Verify that the email was sent.
        """
        assert len(mail.outbox) == 1
        assert len(body_fragments) > 1, 'Should provide at least two body fragments'

        message = mail.outbox[0]
        text = message.body
        html = message.alternatives[0][0]

        assert message.subject == subject

        for fragment in body_fragments:
            assert fragment in text
            assert escape(fragment) in html
