"""Tests for the version 2 User API email opt-in address."""

import datetime
from unittest import mock
from urllib.parse import urlencode

from django.conf import settings
from django.urls import reverse
from edx_rest_framework_extensions.testing import assert_error_envelope
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.user_api.errors import UserAPIInternalError
from openedx.core.djangoapps.user_api.models import UserOrgTag
from openedx.core.djangoapps.user_api.v2.serializers import (
    NOT_A_COURSE,
    ORGANIZATION_MAX_LENGTH,
    ORGANIZATION_TOO_LONG,
)
from openedx.core.djangolib.testing.utils import skip_unless_lms

from .test_preference_views import DEFAULT_AUTH_RECORDER, requests_served_outside_a_transaction

PASSWORD = "Password1234"

#: The address the choice is recorded at, and the one it supersedes.
EARLIER_ADDRESS = "/api/user/v1/preferences/email_opt_in/"
UNVERSIONED_EARLIER_ADDRESS = "/user_api/v1/preferences/email_opt_in/"

COURSE_KEY = "course-v1:edX+DemoX+Demo"
OTHER_COURSE_KEY = "course-v1:edX+OtherX+Other"
OTHER_ORG_COURSE_KEY = "course-v1:MITx+6.002x+2026"

#: The form a course key was written in before the current one, which the
#: platform still reads.
EARLIER_COURSE_KEY = "edX/DemoX/Demo"

#: A key the platform's course key reader reads, and that names a library
#: rather than a course. It carries an organization like a course key does,
#: which is the whole of what this address takes a key for.
LIBRARY_KEY = "library-v1:edX+lib"

#: Where the choice is stored: one row for each user and organization.
OPT_IN_KEY = "email-optin"

#: What such a row holds when it was opened and no choice was written into it.
NO_CHOICE_AT_ALL = ""

#: An age below the one a guardian's consent is needed under.
MINOR_AGE = 10

#: Values that suggest a choice without saying which one, each of which the
#: earlier address recorded as a choice not to receive messages.
SUGGESTED_CHOICES = ("1", "0", "on", "off", "yes", "no", "t", "f", "y", "n", 1, 0)

#: Refusal of a value that does not say which choice it is.
CHOICE_REFUSAL = "Send the choice as true or false."

#: A course whose organization is as long as the column that records a choice
#: against it holds, and one whose organization is a character longer.
LONGEST_ORG = "o" * ORGANIZATION_MAX_LENGTH
TOO_LONG_ORG = "o" * (ORGANIZATION_MAX_LENGTH + 1)


@skip_unless_lms
class EmailOptInTestCase(APITestCase):
    """Fixtures shared by the version 2 email opt-in tests."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(
            username="optin-user", email="optin-user@example.com", password=PASSWORD,
        )
        self.other = UserFactory.create(
            username="optin-other", email="optin-other@example.com", password=PASSWORD,
        )
        self.url = reverse("user_v2:email_opt_in_list")

    def login(self, user):
        """Sign the given user in through the session this view accepts."""
        assert self.client.login(username=user.username, password=PASSWORD)

    def token_headers(self, user):
        """Return the request headers that carry a token identifying the given user."""
        return {"HTTP_AUTHORIZATION": f"JWT {create_jwt_for_user(user)}"}

    def choose(self, course_key=COURSE_KEY, opt_in=True, address=None, **kwargs):
        """Record a choice at the conforming address."""
        return self.client.post(
            address or self.url,
            {"course_key": course_key, "email_opt_in": opt_in},
            format="json",
            **kwargs,
        )

    def choices(self, user=None):
        """Return the choices one account has made, by organization."""
        return {
            tag.org: tag.value
            for tag in UserOrgTag.objects.filter(user=user or self.user, key=OPT_IN_KEY)
        }

    def make_minor(self, user):
        """Record the given account as belonging to someone a guardian answers for."""
        user.profile.year_of_birth = datetime.date.today().year - MINOR_AGE
        user.profile.save()
        return user


class EmailOptInViewTests(EmailOptInTestCase):
    """POST /api/user/v2/email_opt_ins/"""

    def test_a_choice_to_receive_messages_is_recorded_for_the_organization(self):
        self.login(self.user)
        response = self.choose()
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert response.content == b""
        assert self.choices() == {"edX": "True"}

    def test_a_choice_not_to_receive_them_is_recorded_too(self):
        self.login(self.user)
        assert self.choose(opt_in=False).status_code == status.HTTP_204_NO_CONTENT
        assert self.choices() == {"edX": "False"}

    def test_a_choice_replaces_the_one_that_account_made_before(self):
        self.login(self.user)
        self.choose(opt_in=True)
        assert self.choose(opt_in=False).status_code == status.HTTP_204_NO_CONTENT
        assert self.choices() == {"edX": "False"}
        assert UserOrgTag.objects.filter(user=self.user, key=OPT_IN_KEY).count() == 1

    def test_the_choice_covers_every_course_the_organization_runs(self):
        self.login(self.user)
        self.choose(course_key=COURSE_KEY, opt_in=True)
        assert self.choose(
            course_key=OTHER_COURSE_KEY, opt_in=False,
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.choices() == {"edX": "False"}

    def test_a_choice_is_kept_for_each_organization_on_its_own(self):
        self.login(self.user)
        self.choose(course_key=COURSE_KEY, opt_in=True)
        self.choose(course_key=OTHER_ORG_COURSE_KEY, opt_in=False)
        assert self.choices() == {"edX": "True", "MITx": "False"}

    def test_a_course_key_written_in_the_earlier_form_is_still_read(self):
        self.login(self.user)
        assert self.choose(
            course_key=EARLIER_COURSE_KEY,
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.choices() == {"edX": "True"}

    def test_a_choice_submitted_as_a_form_is_read(self):
        """The page a learner makes this choice on submits a form."""
        self.login(self.user)
        response = self.client.post(
            self.url, {"course_key": COURSE_KEY, "email_opt_in": "true"}, format="multipart",
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert self.choices() == {"edX": "True"}

    def test_an_account_a_guardian_answers_for_is_recorded_as_not_receiving_them(self):
        """Whatever the body asks for, consent decides, as it does at the earlier address."""
        self.make_minor(self.user)
        self.login(self.user)
        assert self.choose(opt_in=True).status_code == status.HTTP_204_NO_CONTENT
        assert self.choices() == {"edX": "False"}

    def test_the_choice_is_recorded_for_the_caller_and_nobody_else(self):
        """No account is named in the address or read from the body."""
        self.login(self.user)
        response = self.client.post(
            self.url,
            {
                "course_key": COURSE_KEY,
                "email_opt_in": True,
                "username": self.other.username,
                "user": self.other.id,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert self.choices() == {"edX": "True"}
        assert self.choices(self.other) == {}

    def test_a_value_that_is_neither_true_nor_false_is_refused(self):
        """The earlier address read anything other than ``True`` as a choice not to receive them."""
        self.login(self.user)
        envelope = assert_error_envelope(
            self.choose(opt_in="Banana"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {"email_opt_in": [CHOICE_REFUSAL]}
        assert self.choices() == {}

    def test_the_choice_is_read_from_the_word_for_it_in_any_case(self):
        self.login(self.user)
        for sent, recorded in (
            ("true", "True"), ("True", "True"), ("TRUE", "True"),
            ("false", "False"), ("False", "False"), ("FALSE", "False"),
        ):
            assert self.choose(opt_in=sent).status_code == status.HTTP_204_NO_CONTENT, sent
            assert self.choices() == {"edX": recorded}, sent

    def test_a_value_that_only_suggests_a_choice_is_refused_rather_than_read(self):
        """
        A value a boolean field would read one way and the earlier address the
        other records nothing: it is refused, and a choice already made stands.
        """
        self.login(self.user)
        assert self.choose(opt_in=True).status_code == status.HTTP_204_NO_CONTENT
        for suggestion in SUGGESTED_CHOICES:
            envelope = assert_error_envelope(
                self.choose(opt_in=suggestion),
                expected_status=status.HTTP_400_BAD_REQUEST,
                expected_type_slug="validation",
            )
            assert envelope["errors"] == {"email_opt_in": [CHOICE_REFUSAL]}, suggestion
            assert self.choices() == {"edX": "True"}, suggestion

    def test_a_form_that_leaves_the_choice_out_is_refused(self):
        """An absent form member is a missing choice here, not an unticked checkbox."""
        self.login(self.user)
        assert self.choose(opt_in=True).status_code == status.HTTP_204_NO_CONTENT
        bodies = (
            self.client.post(self.url, {"course_key": COURSE_KEY}, format="multipart"),
            self.client.post(
                self.url,
                urlencode({"course_key": COURSE_KEY}),
                content_type="application/x-www-form-urlencoded",
            ),
        )
        for answer in bodies:
            envelope = assert_error_envelope(
                answer,
                expected_status=status.HTTP_400_BAD_REQUEST,
                expected_type_slug="validation",
            )
            assert envelope["errors"] == {"email_opt_in": ["This field is required."]}
            assert self.choices() == {"edX": "True"}

    def test_a_form_carrying_the_choice_empty_is_refused(self):
        self.login(self.user)
        envelope = assert_error_envelope(
            self.client.post(
                self.url, {"course_key": COURSE_KEY, "email_opt_in": ""}, format="multipart",
            ),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {"email_opt_in": [CHOICE_REFUSAL]}
        assert self.choices() == {}

    def test_a_course_whose_organization_fills_the_column_is_recorded(self):
        self.login(self.user)
        assert self.choose(
            course_key=f"course-v1:{LONGEST_ORG}+DemoX+Demo",
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.choices() == {LONGEST_ORG: "True"}

    def test_a_course_whose_organization_is_longer_than_the_column_is_refused(self):
        """The organization is the one part of the body no key reader bounds."""
        self.login(self.user)
        envelope = assert_error_envelope(
            self.choose(course_key=f"course-v1:{TOO_LONG_ORG}+DemoX+Demo"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {"course_key": [ORGANIZATION_TOO_LONG]}
        assert str(ORGANIZATION_MAX_LENGTH) in envelope["errors"]["course_key"][0]
        assert self.choices() == {}

    def test_a_course_key_that_cannot_be_read_is_refused(self):
        self.login(self.user)
        envelope = assert_error_envelope(
            self.choose(course_key="not a key"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {"course_key": ["Invalid course key"]}
        assert self.choices() == {}

    def test_a_key_that_names_something_other_than_a_course_is_refused(self):
        """A choice is recorded against the organization running a course, so a key names one."""
        self.login(self.user)
        envelope = assert_error_envelope(
            self.choose(course_key=LIBRARY_KEY),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {"course_key": [NOT_A_COURSE]}
        assert self.choices() == {}

    def test_a_course_named_by_something_other_than_text_is_refused(self):
        """A key is read by splitting it, which only text can be, so it is refused as a body fault."""
        self.login(self.user)
        for named_by in (5, 2.5, True, [COURSE_KEY], {"id": COURSE_KEY}):
            envelope = assert_error_envelope(
                self.choose(course_key=named_by),
                expected_status=status.HTTP_400_BAD_REQUEST,
                expected_type_slug="validation",
            )
            assert envelope["errors"] == {
                "course_key": ["Send the course key as the text that writes it."],
            }, named_by
        assert self.choices() == {}

    def test_a_course_named_by_nothing_at_all_is_refused(self):
        self.login(self.user)
        envelope = assert_error_envelope(
            self.choose(course_key=None),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {"course_key": ["This field may not be null."]}
        assert self.choices() == {}

    def test_a_body_missing_either_half_of_the_choice_is_refused(self):
        self.login(self.user)
        envelope = assert_error_envelope(
            self.client.post(self.url, {}, format="json"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "course_key": ["This field is required."],
            "email_opt_in": ["This field is required."],
        }

    def test_a_body_in_a_media_type_this_address_does_not_read_is_refused(self):
        self.login(self.user)
        assert_error_envelope(
            self.client.post(
                self.url, "{}", content_type="application/merge-patch+json",
            ),
            expected_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            expected_type_slug="unsupported-media-type",
        )

    def test_a_body_that_is_not_well_formed_is_refused(self):
        self.login(self.user)
        assert_error_envelope(
            self.client.post(self.url, "{not json", content_type="application/json"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="malformed-body",
        )

    def test_the_choices_made_are_not_listed_here(self):
        """This collection records a choice; nothing reads it back."""
        self.login(self.user)
        for method in ("get", "put", "patch", "delete"):
            assert_error_envelope(
                getattr(self.client, method)(self.url),
                expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
                expected_type_slug="method-not-allowed",
            )

    def test_a_query_parameter_this_address_does_not_read_is_refused(self):
        self.login(self.user)
        envelope = assert_error_envelope(
            self.choose(address=f"{self.url}?username={self.other.username}"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "username": ["No such query parameter. Send any of: format."],
        }
        assert self.choices() == {}
        assert self.choices(self.other) == {}

    def test_an_account_that_carries_no_profile_is_reported_absent(self):
        """
        The consent rule is read from the profile, and there is nothing to read it from.

        The row a choice is kept in is opened before that rule is read, and
        the refusal undoes it: nothing is stored where each request is served
        in a transaction, which is how the platform serves them.
        """
        self.login(self.user)
        self.user.profile.delete()
        assert_error_envelope(
            self.choose(),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )
        assert self.choices() == {}

    def test_the_row_a_refusal_opened_stands_where_a_statement_stands_on_its_own(self):
        """
        A refused choice leaves a row behind on a database that commits each statement.

        The row is opened before the consent rule is read, so where the
        request is not served in a transaction there is nothing to undo it and
        the account is left holding a row carrying no choice at all. What such
        a row holds is pinned because a reader of it compares it against the
        word for wanting the messages and so reads it as not wanting them.
        """
        self.login(self.user)
        self.user.profile.delete()
        with requests_served_outside_a_transaction():
            assert_error_envelope(
                self.choose(),
                expected_status=status.HTTP_404_NOT_FOUND,
                expected_type_slug="not-found",
            )
        assert self.choices() == {"edX": NO_CHOICE_AT_ALL}

    def test_a_choice_that_could_not_be_recorded_is_a_server_fault(self):
        self.login(self.user)
        with mock.patch(
            "openedx.core.djangoapps.user_api.v2.views.update_email_opt_in",
            side_effect=UserAPIInternalError("the tag table is gone"),
        ):
            response = self.choose()
        envelope = assert_error_envelope(
            response,
            expected_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            expected_type_slug="internal",
        )
        assert envelope["detail"] == "The choice could not be recorded."
        assert "tag table is gone" not in response.content.decode()


class EmailOptInAccessTests(EmailOptInTestCase):
    """Who may record a choice, and whose choice is recorded."""

    def test_an_anonymous_caller_is_refused_with_the_standard_body(self):
        """The earlier address refuses them as somebody else's request instead."""
        assert_error_envelope(
            self.choose(),
            expected_status=status.HTTP_401_UNAUTHORIZED,
            expected_type_slug="authn",
        )
        assert self.choices() == {}

    def test_a_choice_presented_with_a_token_is_recorded_for_the_account_it_names(self):
        assert self.choose(**self.token_headers(self.other)).status_code == (
            status.HTTP_204_NO_CONTENT
        )
        assert self.choices(self.other) == {"edX": "True"}
        assert self.choices() == {}

    def test_a_token_is_read_here_without_recording_the_address_as_one_on_the_defaults(self):
        """
        This address names its own classes, so it is not one the default pair serves.

        The pair the platform configures records every request it reads a
        token for, so that an operator can list the addresses it serves. This
        one replaces the session half of it and is therefore not among them,
        and nothing here is recorded as if it were. The address read afterwards
        names no classes of its own and is recorded, which is what shows the
        record is reachable from a request made this way at all.
        """
        with mock.patch(DEFAULT_AUTH_RECORDER) as recorded:
            answer = self.choose(**self.token_headers(self.other))
        assert answer.status_code == status.HTTP_204_NO_CONTENT
        assert self.choices(self.other) == {"edX": "True"}
        assert recorded.call_args_list == []

        administrator = UserFactory.create(
            username="optin-admin", email="optin-admin@example.com", password=PASSWORD, is_staff=True,
        )
        with mock.patch(DEFAULT_AUTH_RECORDER) as recorded:
            on_the_default_pair = self.client.get(
                reverse("user_v2:user_preference_list"), **self.token_headers(administrator),
            )
        assert on_the_default_pair.status_code == status.HTTP_200_OK
        assert [call.args for call in recorded.call_args_list] == [
            ("using_default_auth_classes", True),
        ]

    def test_an_account_with_an_unconfirmed_address_records_a_choice(self):
        """A learner makes this choice while enrolling, before confirming their address."""
        self.user.is_active = False
        self.user.save()
        self.login(self.user)
        assert self.choose().status_code == status.HTTP_204_NO_CONTENT
        assert self.choices() == {"edX": "True"}

    def test_a_session_without_the_token_that_goes_with_it_is_refused(self):
        """The session class still holds a request to the token the site hands out with it."""
        client = APIClient(enforce_csrf_checks=True)
        assert client.login(username=self.user.username, password=PASSWORD)
        assert_error_envelope(
            client.post(
                self.url, {"course_key": COURSE_KEY, "email_opt_in": True}, format="json",
            ),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )
        assert self.choices() == {}

    def test_a_session_carrying_the_token_the_sign_in_handed_out_records_the_choice(self):
        """
        The token a session is held to comes from signing in, not from this address.

        A page reaches this address with a session it got by signing in, so the
        sign-in is what hands out the token the session is held to. The whole
        way round is driven here, because nothing this version serves sets that
        cookie and a caller presenting only a session would otherwise be
        refused at every write.
        """
        client = APIClient(enforce_csrf_checks=True)
        sign_in_address = reverse("user_api_login_session", kwargs={"api_version": "v2"})
        form = client.get(sign_in_address)
        assert form.status_code == status.HTTP_200_OK, form.content
        assert settings.CSRF_COOKIE_NAME in form.cookies
        signed_in = client.post(
            sign_in_address,
            {"email_or_username": self.user.email, "password": PASSWORD},
            HTTP_X_CSRFTOKEN=client.cookies[settings.CSRF_COOKIE_NAME].value,
        )
        assert signed_in.status_code == status.HTTP_200_OK, signed_in.content
        recorded = client.post(
            self.url,
            {"course_key": COURSE_KEY, "email_opt_in": True},
            format="json",
            HTTP_X_CSRFTOKEN=client.cookies[settings.CSRF_COOKIE_NAME].value,
        )
        assert recorded.status_code == status.HTTP_204_NO_CONTENT, recorded.content
        assert self.choices() == {"edX": "True"}


class EmailOptInParityTests(EmailOptInTestCase):
    """The same choice through the earlier address and through this one."""

    def earlier(self, course_id=COURSE_KEY, email_opt_in="True", address=EARLIER_ADDRESS):
        """Record a choice at the address this version supersedes, as its clients do."""
        return self.client.post(
            address, {"course_id": course_id, "email_opt_in": email_opt_in},
        )

    def test_both_addresses_record_the_same_row(self):
        self.login(self.user)
        assert self.earlier().status_code == status.HTTP_200_OK
        earlier = self.choices()
        UserOrgTag.objects.filter(user=self.user, key=OPT_IN_KEY).delete()
        assert self.choose().status_code == status.HTTP_204_NO_CONTENT
        assert self.choices() == earlier == {"edX": "True"}

    def test_both_addresses_record_the_same_row_for_an_account_a_guardian_answers_for(self):
        self.make_minor(self.user)
        self.login(self.user)
        self.earlier(email_opt_in="True")
        earlier = self.choices()
        UserOrgTag.objects.filter(user=self.user, key=OPT_IN_KEY).delete()
        self.choose(opt_in=True)
        assert self.choices() == earlier == {"edX": "False"}

    def test_both_addresses_read_a_course_key_written_in_the_earlier_form(self):
        self.login(self.user)
        self.earlier(course_id=EARLIER_COURSE_KEY)
        earlier = self.choices()
        UserOrgTag.objects.filter(user=self.user, key=OPT_IN_KEY).delete()
        self.choose(course_key=EARLIER_COURSE_KEY)
        assert self.choices() == earlier == {"edX": "True"}

    def test_the_earlier_address_answers_with_an_empty_body_and_this_one_with_no_body(self):
        """Declared difference: the choice is recorded, and there is nothing to return."""
        self.login(self.user)
        earlier = self.earlier()
        assert earlier.status_code == status.HTTP_200_OK
        assert earlier.content == b""
        current = self.choose()
        assert current.status_code == status.HTTP_204_NO_CONTENT
        assert current.content == b""

    def test_a_value_the_earlier_address_read_as_a_refusal_is_refused_here(self):
        """Declared difference: a value that is neither true nor false is not read as false."""
        self.login(self.user)
        assert self.earlier(email_opt_in="Banana").status_code == status.HTTP_200_OK
        assert self.choices() == {"edX": "False"}
        UserOrgTag.objects.filter(user=self.user, key=OPT_IN_KEY).delete()
        assert self.choose(opt_in="Banana").status_code == status.HTTP_400_BAD_REQUEST
        assert self.choices() == {}

    def test_a_value_that_only_suggests_a_choice_is_refused_here_and_not_read_as_one(self):
        """
        Declared difference: the earlier address recorded a choice not to
        receive messages for each of these, and a boolean field would have
        recorded the opposite for half of them, so they are refused instead.
        """
        self.login(self.user)
        for suggestion in SUGGESTED_CHOICES:
            assert self.earlier(email_opt_in=suggestion).status_code == status.HTTP_200_OK
            assert self.choices() == {"edX": "False"}, suggestion
            UserOrgTag.objects.filter(user=self.user, key=OPT_IN_KEY).delete()
            assert self.choose(opt_in=suggestion).status_code == (
                status.HTTP_400_BAD_REQUEST
            ), suggestion
            assert self.choices() == {}, suggestion

    def test_a_form_that_leaves_the_choice_out_is_refused_by_both_addresses(self):
        """Both answer a form body that names a course and no choice as a fault to correct."""
        self.login(self.user)
        self.choose(opt_in=True)
        earlier = self.client.post(EARLIER_ADDRESS, {"course_id": COURSE_KEY})
        assert earlier.status_code == status.HTTP_400_BAD_REQUEST
        assert self.choices() == {"edX": "True"}
        current = self.client.post(self.url, {"course_key": COURSE_KEY}, format="multipart")
        assert current.status_code == status.HTTP_400_BAD_REQUEST
        assert self.choices() == {"edX": "True"}

    def test_a_key_that_names_no_course_is_refused_by_both_addresses(self):
        """Neither address records a choice for the organization of something that is not a course."""
        self.login(self.user)
        earlier = self.earlier(course_id=LIBRARY_KEY)
        assert earlier.status_code == status.HTTP_400_BAD_REQUEST
        assert earlier.content == f"No course '{LIBRARY_KEY}' found".encode()
        assert self.choices() == {}
        current = self.choose(course_key=LIBRARY_KEY)
        assert current.status_code == status.HTTP_400_BAD_REQUEST
        assert self.choices() == {}

    def test_neither_address_records_a_choice_for_an_account_carrying_no_profile(self):
        """
        Declared difference: the earlier address fails outright where this one reports it absent.

        Neither address writes a choice, and each leaves the same thing behind
        on the same database: nothing where the request is served in a
        transaction, and a row carrying no choice where each statement stands
        on its own. The row is opened before the consent rule is read, which
        is why it is there to be left at all.
        """
        self.login(self.user)
        self.user.profile.delete()
        self.client.raise_request_exception = False

        assert self.earlier().status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert self.choices() == {}
        assert self.choose().status_code == status.HTTP_404_NOT_FOUND
        assert self.choices() == {}

        with requests_served_outside_a_transaction():
            assert self.earlier().status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            left_by_the_earlier_address = self.choices()
            UserOrgTag.objects.filter(user=self.user, key=OPT_IN_KEY).delete()
            assert self.choose().status_code == status.HTTP_404_NOT_FOUND
        assert self.choices() == left_by_the_earlier_address == {"edX": NO_CHOICE_AT_ALL}

    def test_the_unversioned_earlier_address_records_the_same_row(self):
        """It keeps serving the clients that call it, and this version adds no third address."""
        self.login(self.user)
        assert self.earlier(
            address=UNVERSIONED_EARLIER_ADDRESS,
        ).status_code == status.HTTP_200_OK
        assert self.choices() == {"edX": "True"}
