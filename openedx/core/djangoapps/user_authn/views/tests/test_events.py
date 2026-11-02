"""
Test classes for the events sent in the registration process.

Classes:
    RegistrationEventTest: Test event sent after registering a user through the
    user API.
    LoginSessionEventTest: Test event sent after creating the user's login session
    user through the user API.
"""
from unittest.mock import Mock

from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.urls import reverse
from openedx_events.learning.data import UserData, UserPersonalData
from openedx_events.learning.signals import SESSION_LOGIN_COMPLETED, STUDENT_REGISTRATION_COMPLETED
from openedx_events.tests.utils import OpenEdxEventsTestMixin

from common.djangoapps.student.tests.factories import UserFactory, UserProfileFactory
from openedx.core.djangoapps.user_api.tests.test_views import UserAPITestCase
from openedx.core.djangolib.testing.utils import skip_unless_lms
from common.test.utils import assert_dict_contains_subset


@skip_unless_lms
class RegistrationEventTest(UserAPITestCase, OpenEdxEventsTestMixin):
    """
    Tests for the Open edX Events associated with the registration process through
    the registration view.

    This class guarantees that the following events are sent after registering
    a user, with the exact Data Attributes as the event definition stated:

        - STUDENT_REGISTRATION_COMPLETED: after the user's registration has been
        completed.
    """

    ENABLED_OPENEDX_EVENTS = ["org.openedx.learning.student.registration.completed.v1"]

    @classmethod
    def setUpClass(cls):
        """
        Set up class method for the Test class.

        So the Open edX Events Isolation starts, the setUpClass must be explicitly
        called with the method that executes the isolation. We do this to avoid
        MRO resolution conflicts with other sibling classes while ensuring the
        isolation process begins.
        """
        super().setUpClass()
        cls.start_events_isolation()

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.url = reverse("user_api_registration")
        self.user_info = {
            "email": "user@example.com",
            "name": "Test User",
            "username": "test",
            "password": "password",
            "honor_code": "true",
        }
        self.receiver_called = False

    def _event_receiver_side_effect(self, **kwargs):  # pylint: disable=unused-argument
        """
        Used show that the Open edX Event was called by the Django signal handler.
        """
        self.receiver_called = True

    def test_send_registration_event(self):
        """
        Test whether the student registration event is sent during the user's
        registration process.

        Expected result:
            - STUDENT_REGISTRATION_COMPLETED is sent and received by the mocked receiver.
            - The arguments that the receiver gets are the arguments sent by the event
            except the metadata generated on the fly.
        """
        event_receiver = Mock(side_effect=self._event_receiver_side_effect)
        STUDENT_REGISTRATION_COMPLETED.connect(event_receiver)

        self.client.post(self.url, self.user_info)

        user = User.objects.get(username=self.user_info.get("username"))
        self.assertTrue(self.receiver_called)
        assert_dict_contains_subset(
            self,
            {
                "signal": STUDENT_REGISTRATION_COMPLETED,
                "sender": None,
                "user": UserData(
                    pii=UserPersonalData(
                        username=user.username,
                        email=user.email,
                        name=user.profile.name,
                    ),
                    id=user.id,
                    is_active=user.is_active,
                ),
            },
            event_receiver.call_args.kwargs,
        )


@skip_unless_lms
class LoginSessionEventTest(UserAPITestCase, OpenEdxEventsTestMixin):
    """
    Tests for the Open edX Events associated with the login process through the
    login_user view.

    This class guarantees that the following events are sent after the user's
    session creation, with the exact Data Attributes as the event definition
    stated:

        - SESSION_LOGIN_COMPLETED: after login has been completed.
    """

    ENABLED_OPENEDX_EVENTS = ["org.openedx.learning.auth.session.login.completed.v1"]

    @classmethod
    def setUpClass(cls):
        """
        Set up class method for the Test class.

        This method starts manually events isolation. Explanation here:
        openedx/core/djangoapps/user_authn/views/tests/test_events.py#L44
        """
        super().setUpClass()
        cls.start_events_isolation()

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.url = reverse("user_api_login_session", kwargs={"api_version": "v1"})
        self.user = UserFactory.create(
            username="test",
            email="test@example.com",
            password="password",
        )
        self.user_profile = UserProfileFactory.create(user=self.user, name="Test Example")
        self.receiver_called = True

    def _event_receiver_side_effect(self, **kwargs):  # pylint: disable=unused-argument
        """
        Used show that the Open edX Event was called by the Django signal handler.
        """
        self.receiver_called = True

    def test_send_login_event(self):
        """
        Test whether the student login event is sent after the user's
        login process.

        Expected result:
            - SESSION_LOGIN_COMPLETED is sent and received by the mocked receiver.
            - The arguments that the receiver gets are the arguments sent by the event
            except the metadata generated on the fly.
        """
        event_receiver = Mock(side_effect=self._event_receiver_side_effect)
        SESSION_LOGIN_COMPLETED.connect(event_receiver)
        data = {
            "email": "test@example.com",
            "password": "password",
        }

        self.client.post(self.url, data)

        user = User.objects.get(username=self.user.username)
        self.assertTrue(self.receiver_called)
        assert_dict_contains_subset(
            self,
            {
                "signal": SESSION_LOGIN_COMPLETED,
                "sender": None,
                "user": UserData(
                    pii=UserPersonalData(
                        username=user.username,
                        email=user.email,
                        name=user.profile.name,
                    ),
                    id=user.id,
                    is_active=user.is_active,
                ),
            },
            event_receiver.call_args.kwargs,
        )
