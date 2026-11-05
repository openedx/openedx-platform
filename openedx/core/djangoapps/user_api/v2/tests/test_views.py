"""Tests for the version 2 User API views."""

import re
from datetime import UTC, datetime
from math import ceil
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import reverse
from edx_rest_framework_extensions.errors import classify_error
from edx_rest_framework_extensions.testing import assert_error_envelope
from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from rest_framework.exceptions import MethodNotAllowed, NotAcceptable, ParseError, UnsupportedMediaType
from rest_framework.test import APIClient, APITestCase

from common.djangoapps.student.models import PendingNameChange, UserProfile
from common.djangoapps.student.tests.factories import AdminFactory, UserFactory
from openedx.core.djangoapps.django_comment_common.models import Role
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.user_api.models import UserPreference
from openedx.core.djangoapps.user_api.preferences.api import get_country_time_zones
from openedx.core.djangoapps.user_api.tests.factories import UserPreferenceFactory
from openedx.core.djangoapps.user_api.v2 import urls as v2_urls
from openedx.core.djangoapps.user_api.v2.filters import UserFilterSet
from openedx.core.djangoapps.user_api.v2.serializers import UserPreferenceRecordSerializer
from openedx.core.djangoapps.user_api.v2.views import UserPreferenceViewSet, UserViewSet
from openedx.core.djangolib.testing.utils import skip_unless_lms

PASSWORD = "Password1234"
API_KEY = "directory-test-api-key"
COURSE_KEY = CourseKey.from_string("course-v1:edX+DemoX+Demo")
OTHER_COURSE_KEY = CourseKey.from_string("course-v1:edX+OtherX+Other")

#: What the shared catalog calls each failure a request meets before a handler
#: runs; this API answers them as every other standardized API does.
REFUSED_METHOD_SLUG, REFUSED_METHOD_TITLE = classify_error(MethodNotAllowed("GET"))
NOT_ACCEPTABLE_SLUG, NOT_ACCEPTABLE_TITLE = classify_error(NotAcceptable())
UNSUPPORTED_MEDIA_TYPE_SLUG = classify_error(UnsupportedMediaType("text/plain"))[0]
MALFORMED_BODY_SLUG, MALFORMED_BODY_TITLE = classify_error(ParseError())

PAGINATION_KEYS = {"count", "num_pages", "current_page", "start", "next", "previous", "results"}

#: The query parameters each endpoint reads, in the order its refusal lists them.
USER_QUERY_PARAMS = (
    "course_key, email, fields, format, forum_role, is_active, ordering, page, page_size, "
    "preference_key, username, view"
)
PREFERENCE_QUERY_PARAMS = "fields, format, key, ordering, page, page_size, username, view"
TIME_ZONE_QUERY_PARAMS = "country_code, format, page, page_size"

WRITE_VERB = re.compile(r"\s*(?:INSERT|UPDATE|DELETE)\b", re.IGNORECASE)

#: The table a statement changes is the identifier right after the verb; the
#: quoting character around it differs between database backends, and a
#: conflict clause may sit between the verb and ``INTO``.
WRITE_TARGET = re.compile(
    r"\s*(?:INSERT\s+(?:\w+\s+)*?INTO|UPDATE|DELETE\s+FROM)\s+[`\"\[]?(?P<table>\w+)",
    re.IGNORECASE,
)

#: The platform's ``SafeSessionMiddleware`` puts the signed-in caller's e-mail
#: into the session the first time it finds it missing, which rewrites that one
#: row once per session; the deployed configuration keeps sessions in the cache
#: and only the test settings put them in a table at all. It is framework
#: bookkeeping rather than a change to the data this API serves, and it is the
#: only table left out of the captures below.
SESSION_TABLE = "django_session"


def write_target(sql):
    """Return the table a statement writes to, or ``None`` if it cannot be read."""
    match = WRITE_TARGET.match(sql)
    return match.group("table") if match else None


def write_queries(captured):
    """
    Return the captured statements that would change the data this API serves.

    Only the statement's own target table decides: a write to a domain table is
    reported however it mentions the session table elsewhere, and a write whose
    target cannot be read is reported rather than assumed harmless.
    """
    return [
        query["sql"]
        for query in captured
        if WRITE_VERB.match(query["sql"]) and write_target(query["sql"]) != SESSION_TABLE
    ]


class WriteCaptureFilterTests(SimpleTestCase):
    """The capture filter reports writes by their target table and nothing else."""

    PREFERENCE_INSERT = (
        'INSERT INTO "user_api_userpreference" ("user_id", "key", "value") '
        "VALUES (1, 'pref-lang', 'django_session')"
    )
    PREFERENCE_DELETE = (
        'DELETE FROM "user_api_userpreference" WHERE "user_id" IN '
        '(SELECT "user_id" FROM "django_session" WHERE "session_key" = \'abc\')'
    )
    ROLE_INSERT = 'INSERT INTO "django_comment_common_role" ("name") VALUES (\'Moderator\')'
    SESSION_UPDATE = 'UPDATE "django_session" SET "session_data" = \'x\' WHERE "session_key" = \'abc\''
    SESSION_INSERT = 'INSERT INTO "django_session" ("session_key", "session_data") VALUES (\'abc\', \'x\')'
    SESSION_DELETE = 'DELETE FROM "django_session" WHERE "session_key" = \'abc\''
    USER_SELECT = 'SELECT "auth_user"."id" FROM "auth_user" ORDER BY "auth_user"."username" ASC'
    UNREADABLE_WRITE = 'UPDATE /* hint */ "user_api_userpreference" SET "value" = \'en\''

    def captured(self, *statements):
        """Return the statements in the shape ``CaptureQueriesContext`` records them."""
        return [{"sql": statement, "time": "0.000"} for statement in statements]

    def test_domain_writes_are_reported_even_when_they_name_the_session_table(self):
        reported = write_queries(
            self.captured(self.PREFERENCE_INSERT, self.PREFERENCE_DELETE, self.ROLE_INSERT),
        )
        assert reported == [self.PREFERENCE_INSERT, self.PREFERENCE_DELETE, self.ROLE_INSERT]

    def test_session_bookkeeping_is_the_only_statement_dropped(self):
        reported = write_queries(
            self.captured(
                self.SESSION_INSERT,
                self.SESSION_UPDATE,
                self.SESSION_DELETE,
                self.USER_SELECT,
                self.ROLE_INSERT,
            ),
        )
        assert reported == [self.ROLE_INSERT]

    def test_reads_are_not_writes(self):
        assert write_queries(self.captured(self.USER_SELECT)) == []

    def test_a_write_whose_target_cannot_be_read_is_still_reported(self):
        assert write_target(self.UNREADABLE_WRITE) is None
        assert write_queries(self.captured(self.UNREADABLE_WRITE)) == [self.UNREADABLE_WRITE]

    def test_the_target_table_is_read_through_any_backend_quoting(self):
        assert write_target(self.ROLE_INSERT) == "django_comment_common_role"
        assert write_target("UPDATE `django_session` SET `session_data` = 'x'") == SESSION_TABLE
        assert write_target("  insert into user_api_userpreference (value) values ('x')") == (
            "user_api_userpreference"
        )
        assert write_target("INSERT OR IGNORE INTO `user_api_userpreference` (`value`) VALUES ('en')") == (
            "user_api_userpreference"
        )
        assert write_target(self.USER_SELECT) is None


@override_settings(EDX_API_KEY=API_KEY)
class UserApiV2TestCase(APITestCase):
    """Fixtures shared by the version 2 User API tests."""

    def setUp(self):
        super().setUp()
        self.admin = AdminFactory.create(username="admin-caller", password=PASSWORD)
        self.learner = UserFactory.create(username="learner-caller", password=PASSWORD)
        self.alice = UserFactory.create(username="alice", email="alice@example.com", password=PASSWORD)
        self.bob = UserFactory.create(username="bob", email="bob@example.com", password=PASSWORD)
        UserPreferenceFactory.create(user=self.alice, key="pref-lang", value="en")
        UserPreferenceFactory.create(user=self.bob, key="time_zone", value="UTC")

    def login(self, user):
        """Sign the given user in through the session the platform default accepts."""
        assert self.client.login(username=user.username, password=PASSWORD)

    def send_api_key(self):
        """Send the site's API key with every later request, which is how the directory is read."""
        self.client.credentials(HTTP_X_EDX_API_KEY=API_KEY)

    def usernames_in(self, response):
        """Return the usernames carried by a paginated directory page."""
        return [row["username"] for row in response.data["results"]]


@skip_unless_lms
class UserListViewTests(UserApiV2TestCase):
    """GET /api/user/v2/users/"""

    def setUp(self):
        super().setUp()
        self.url = reverse("user_v2:user_list")

    def test_anonymous_caller_is_unauthenticated(self):
        response = self.client.get(self.url)
        assert_error_envelope(response, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn")

    def test_a_caller_without_the_key_is_forbidden(self):
        self.login(self.learner)
        response = self.client.get(self.url)
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz")

    def test_a_caller_without_the_key_cannot_list_another_users_preference_holders(self):
        self.login(self.learner)
        response = self.client.get(self.url, {"preference_key": "pref-lang"})
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz")

    def test_a_caller_with_the_key_receives_the_directory(self):
        self.send_api_key()
        response = self.client.get(self.url, {"page_size": 100})
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == PAGINATION_KEYS
        assert self.usernames_in(response) == ["admin-caller", "alice", "bob", "learner-caller"]
        assert response.data["count"] == 4
        assert response.data["num_pages"] == 1
        assert response.data["current_page"] == 1
        assert response.data["start"] == 0

    def test_row_shape_matches_the_declared_fields(self):
        self.send_api_key()
        response = self.client.get(self.url, {"username": "alice"})
        row = response.data["results"][0]
        assert set(row) == {"id", "url", "email", "name", "username", "preferences"}
        assert row["id"] == self.alice.id
        assert row["email"] == "alice@example.com"
        assert row["name"] == self.alice.profile.name
        assert row["preferences"] == {"pref-lang": "en"}
        assert row["url"].endswith("/api/user/v2/users/alice/")

    def test_second_page_reports_its_position(self):
        self.send_api_key()
        response = self.client.get(self.url, {"page_size": 2, "page": 2})
        assert response.status_code == status.HTTP_200_OK
        assert self.usernames_in(response) == ["bob", "learner-caller"]
        assert response.data["current_page"] == 2
        assert response.data["num_pages"] == 2
        assert response.data["start"] == 2
        assert response.data["previous"] is not None
        assert response.data["next"] is None

    def test_a_page_beyond_the_last_one_is_not_found(self):
        self.send_api_key()
        response = self.client.get(self.url, {"page": 9999})
        assert_error_envelope(response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found")

    def test_a_page_number_that_is_not_a_number_is_not_found(self):
        self.send_api_key()
        response = self.client.get(self.url, {"page": "abc"})
        assert_error_envelope(response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found")

    def test_ordering_can_be_reversed(self):
        self.send_api_key()
        response = self.client.get(self.url, {"ordering": "-username", "page_size": 100})
        assert self.usernames_in(response) == ["learner-caller", "bob", "alice", "admin-caller"]

    def test_ordering_by_join_date(self):
        self.alice.date_joined = datetime(2020, 1, 1, tzinfo=UTC)
        self.alice.save()
        self.bob.date_joined = datetime(2019, 1, 1, tzinfo=UTC)
        self.bob.save()
        self.send_api_key()
        response = self.client.get(self.url, {"ordering": "date_joined", "page_size": 100})
        assert self.usernames_in(response)[-2:] == ["bob", "alice"]

    def test_unknown_ordering_is_refused(self):
        self.send_api_key()
        response = self.client.get(self.url, {"ordering": "password", "page_size": 100})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "ordering": ["Sort by any of: date_joined, username, each optionally prefixed with '-'."],
        }

    def test_one_unknown_field_refuses_the_whole_sort(self):
        self.send_api_key()
        response = self.client.get(self.url, {"ordering": "username,password", "page_size": 100})
        assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )

    def test_filter_by_username(self):
        self.send_api_key()
        response = self.client.get(self.url, {"username": "bob", "page_size": 100})
        assert self.usernames_in(response) == ["bob"]

    def test_several_names_select_the_entries_of_each(self):
        """
        The value is a list of names, as it is on the account collection.

        A caller that learned the list on one collection of this version and
        sent it to another was answered with an empty page, which reads as a
        page of the accounts asked for rather than as a value this collection
        does not take.
        """
        self.send_api_key()
        response = self.client.get(self.url, {"username": "alice,bob", "page_size": 100})
        assert response.status_code == status.HTTP_200_OK
        assert self.usernames_in(response) == ["alice", "bob"]
        assert response.data["count"] == 2

    def test_a_name_in_the_list_that_no_account_holds_is_left_out_of_the_page(self):
        self.send_api_key()
        response = self.client.get(self.url, {"username": "alice,nobody", "page_size": 100})
        assert self.usernames_in(response) == ["alice"]
        assert response.data["count"] == 1

    def test_a_position_in_the_list_that_carries_nothing_names_nothing(self):
        self.send_api_key()
        response = self.client.get(self.url, {"username": ",alice,,bob,", "page_size": 100})
        assert self.usernames_in(response) == ["alice", "bob"]

    def test_an_empty_name_narrows_nothing(self):
        self.send_api_key()
        response = self.client.get(self.url, {"username": "", "page_size": 100})
        assert response.status_code == status.HTTP_200_OK
        assert self.usernames_in(response) == ["admin-caller", "alice", "bob", "learner-caller"]

    def test_a_name_sent_with_spaces_around_it_is_read_as_sent(self):
        """A name is matched exactly as it was sent, so a space before it names no user."""
        self.send_api_key()
        for asked, expected in ((" alice", []), ("bob, alice", ["bob"]), ("  ", [])):
            with self.subTest(username=asked):
                response = self.client.get(self.url, {"username": asked, "page_size": 100})
                assert response.status_code == status.HTTP_200_OK, response.content
                assert self.usernames_in(response) == expected

    def test_filter_by_email(self):
        self.send_api_key()
        response = self.client.get(self.url, {"email": "bob@example.com"})
        assert self.usernames_in(response) == ["bob"]

    def test_filter_by_is_active(self):
        self.alice.is_active = False
        self.alice.save()
        self.send_api_key()
        response = self.client.get(self.url, {"is_active": "false", "page_size": 100})
        assert self.usernames_in(response) == ["alice"]

    def test_filter_by_is_active_keeps_only_the_active_accounts(self):
        self.alice.is_active = False
        self.alice.save()
        self.send_api_key()
        response = self.client.get(self.url, {"is_active": "true", "page_size": 100})
        assert self.usernames_in(response) == ["admin-caller", "bob", "learner-caller"]

    def test_an_is_active_value_that_names_no_side_is_refused(self):
        """A mistyped value must be refused, not read as "no preference" and answered unfiltered."""
        self.alice.is_active = False
        self.alice.save()
        self.send_api_key()
        response = self.client.get(self.url, {"is_active": "maybe", "page_size": 100})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {"is_active": ["Enter true or false."]}

    def test_an_empty_is_active_value_narrows_nothing(self):
        self.alice.is_active = False
        self.alice.save()
        self.send_api_key()
        response = self.client.get(self.url, {"is_active": "", "page_size": 100})
        assert response.status_code == status.HTTP_200_OK
        assert self.usernames_in(response) == ["admin-caller", "alice", "bob", "learner-caller"]

    def test_filter_by_preference_key(self):
        self.send_api_key()
        response = self.client.get(self.url, {"preference_key": "pref-lang", "page_size": 100})
        assert self.usernames_in(response) == ["alice"]

    def test_filter_by_unknown_preference_key_returns_no_rows(self):
        self.send_api_key()
        response = self.client.get(self.url, {"preference_key": "nope", "page_size": 100})
        assert response.data["results"] == []
        assert response.data["count"] == 0

    def give_alice_the_moderator_role(self):
        """Make alice a moderator of the course the forum-role tests narrow by."""
        role = Role.objects.create(course_id=COURSE_KEY, name="Moderator")
        role.users.add(self.alice)
        return role

    def test_filter_by_forum_role_and_course(self):
        self.give_alice_the_moderator_role()
        other_role = Role.objects.create(course_id=OTHER_COURSE_KEY, name="Moderator")
        other_role.users.add(self.bob)
        self.send_api_key()
        response = self.client.get(
            self.url, {"forum_role": "Moderator", "course_key": str(COURSE_KEY), "page_size": 100},
        )
        assert self.usernames_in(response) == ["alice"]

    def test_anonymous_caller_cannot_list_a_courses_forum_role_holders(self):
        self.give_alice_the_moderator_role()
        response = self.client.get(
            self.url, {"forum_role": "Moderator", "course_key": str(COURSE_KEY)},
        )
        assert_error_envelope(response, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn")

    def test_a_caller_without_the_key_cannot_list_a_courses_forum_role_holders(self):
        self.give_alice_the_moderator_role()
        self.login(self.learner)
        response = self.client.get(
            self.url, {"forum_role": "Moderator", "course_key": str(COURSE_KEY)},
        )
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz")

    def test_holding_the_role_does_not_let_a_learner_list_its_holders(self):
        self.give_alice_the_moderator_role()
        self.login(self.alice)
        response = self.client.get(
            self.url, {"forum_role": "Moderator", "course_key": str(COURSE_KEY)},
        )
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz")

    def test_the_published_course_key_example_can_be_pasted_into_a_query_string(self):
        """A caller that copies the documented key into a query string reaches the role holders."""
        example = re.search(
            r"for example (course-v1:[^\s.]+)",
            UserFilterSet.base_filters["course_key"].extra["help_text"],
        ).group(1)
        role = Role.objects.create(course_id=COURSE_KEY, name="Moderator")
        role.users.add(self.alice)
        self.send_api_key()
        response = self.client.get(f"{self.url}?forum_role=Moderator&course_key={example}")
        assert response.status_code == status.HTTP_200_OK
        assert self.usernames_in(response) == ["alice"]

    def test_a_course_key_whose_plus_signs_are_not_encoded_is_refused(self):
        """An unencoded plus sign arrives as a space, which no course key may contain."""
        self.send_api_key()
        response = self.client.get(
            f"{self.url}?forum_role=Moderator&course_key=course-v1:edX+DemoX+Demo",
        )
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {"course_key": ["Enter a valid course key."]}

    def test_a_user_holding_the_role_through_several_rows_is_listed_once(self):
        """Nothing stops a course from carrying one role name under several rows."""
        for _ in range(2):
            role = Role.objects.create(course_id=COURSE_KEY, name="Moderator")
            role.users.add(self.alice)
        self.send_api_key()
        response = self.client.get(
            self.url, {"forum_role": "Moderator", "course_key": str(COURSE_KEY), "page_size": 100},
        )
        assert self.usernames_in(response) == ["alice"]
        assert response.data["count"] == 1

    def test_forum_role_without_course_key_is_rejected(self):
        self.send_api_key()
        response = self.client.get(self.url, {"forum_role": "Moderator"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "course_key": ["This parameter is required when forum_role is supplied."],
        }

    def test_course_key_without_forum_role_is_rejected(self):
        self.send_api_key()
        response = self.client.get(self.url, {"course_key": str(COURSE_KEY)})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "forum_role": ["This parameter is required when course_key is supplied."],
        }

    def test_malformed_course_key_is_rejected(self):
        self.send_api_key()
        response = self.client.get(self.url, {"forum_role": "Moderator", "course_key": "not a course"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {"course_key": ["Enter a valid course key."]}

    def test_unknown_forum_role_returns_no_rows_and_creates_nothing(self):
        self.send_api_key()
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(
                self.url, {"forum_role": "Inquisitor", "course_key": str(COURSE_KEY)},
            )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["results"] == []
        assert Role.objects.filter(name="Inquisitor").count() == 0
        assert write_queries(captured.captured_queries) == []

    def test_minimal_view_returns_only_identifying_fields(self):
        self.send_api_key()
        response = self.client.get(self.url, {"username": "alice", "view": "minimal"})
        row = response.data["results"][0]
        assert set(row) == {"id", "username", "email"}
        assert row["username"] == "alice"

    def test_fields_parameter_projects_the_row(self):
        self.send_api_key()
        response = self.client.get(self.url, {"username": "alice", "fields": "username,email"})
        assert response.data["results"] == [{"username": "alice", "email": "alice@example.com"}]

    def test_fields_parameter_narrows_the_minimal_view(self):
        self.send_api_key()
        response = self.client.get(
            self.url, {"username": "alice", "view": "minimal", "fields": "username"},
        )
        assert response.data["results"] == [{"username": "alice"}]

    def test_a_field_name_the_row_does_not_carry_is_refused(self):
        self.send_api_key()
        response = self.client.get(self.url, {"fields": "user_name"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "fields": [
                "No such field: user_name. "
                "Select any of: email, id, name, preferences, url, username.",
            ],
        }

    def test_a_field_the_minimal_preset_drops_is_refused(self):
        """The preset decides what the row carries, so the selection is judged against it."""
        self.send_api_key()
        response = self.client.get(self.url, {"view": "minimal", "fields": "name"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "fields": ["No such field: name. Select any of: email, id, username."],
        }

    def test_every_unknown_field_name_is_named_at_once(self):
        self.send_api_key()
        response = self.client.get(self.url, {"fields": "username,e_mail,joined"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"]["fields"][0].startswith("No such field: e_mail, joined.")

    def test_a_field_name_with_surrounding_space_is_accepted(self):
        self.send_api_key()
        response = self.client.get(self.url, {"username": "alice", "fields": " username , email "})
        assert response.data["results"] == [{"username": "alice", "email": "alice@example.com"}]

    def test_an_unknown_field_does_not_answer_an_anonymous_caller(self):
        response = self.client.get(self.url, {"fields": "user_name"})
        assert_error_envelope(response, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn")

    def test_an_unknown_response_preset_is_refused(self):
        self.send_api_key()
        response = self.client.get(self.url, {"view": "nonsense"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "view": ["Ask for any of: minimal, or omit this parameter for the default response."],
        }

    def test_the_preset_that_belongs_to_the_preference_list_is_refused_here(self):
        self.send_api_key()
        response = self.client.get(self.url, {"view": "full"})
        assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )

    def test_an_empty_response_preset_returns_the_default_shape(self):
        """A query string built from an empty variable narrows nothing rather than failing."""
        self.send_api_key()
        response = self.client.get(self.url, {"username": "alice", "view": ""})
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data["results"][0]) == {
            "id", "url", "email", "name", "username", "preferences",
        }

    def test_an_unknown_preset_does_not_answer_an_anonymous_caller(self):
        response = self.client.get(self.url, {"view": "nonsense"})
        assert_error_envelope(response, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn")

    def test_listing_writes_nothing(self):
        self.send_api_key()
        with CaptureQueriesContext(connection) as captured:
            self.client.get(self.url, {"page_size": 100})
        assert write_queries(captured.captured_queries) == []

    def test_head_writes_nothing(self):
        self.send_api_key()
        with CaptureQueriesContext(connection) as captured:
            response = self.client.head(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert write_queries(captured.captured_queries) == []


@skip_unless_lms
class UserDetailViewTests(UserApiV2TestCase):
    """GET /api/user/v2/users/{username}/"""

    def url_for(self, username):
        """Return the member address of the named user."""
        return reverse("user_v2:user_detail", kwargs={"username": username})

    def test_anonymous_caller_is_unauthenticated(self):
        response = self.client.get(self.url_for("alice"))
        assert_error_envelope(response, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn")

    def test_a_caller_without_the_key_is_forbidden(self):
        self.login(self.learner)
        response = self.client.get(self.url_for("alice"))
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz")

    def test_the_owner_without_the_key_is_forbidden(self):
        self.login(self.alice)
        response = self.client.get(self.url_for("alice"))
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz")

    def test_a_caller_with_the_key_receives_the_entry(self):
        self.send_api_key()
        response = self.client.get(self.url_for("alice"))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["username"] == "alice"
        assert response.data["email"] == "alice@example.com"
        assert response.data["preferences"] == {"pref-lang": "en"}

    def test_unknown_username_is_not_found(self):
        self.send_api_key()
        response = self.client.get(self.url_for("nobody"))
        assert_error_envelope(response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found")

    def test_username_containing_a_dot_resolves(self):
        UserFactory.create(username="ada.lovelace", email="ada@example.com", password=PASSWORD)
        self.send_api_key()
        response = self.client.get(self.url_for("ada.lovelace"))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["username"] == "ada.lovelace"

    def test_minimal_view_returns_only_identifying_fields(self):
        self.send_api_key()
        response = self.client.get(self.url_for("alice"), {"view": "minimal"})
        assert set(response.data) == {"id", "username", "email"}

    def test_a_field_name_the_entry_does_not_carry_is_refused(self):
        self.send_api_key()
        response = self.client.get(self.url_for("alice"), {"fields": "user_name"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "fields": [
                "No such field: user_name. "
                "Select any of: email, id, name, preferences, url, username.",
            ],
        }

    def test_fields_parameter_projects_the_entry(self):
        self.send_api_key()
        response = self.client.get(self.url_for("alice"), {"fields": "username"})
        assert response.data == {"username": "alice"}

    def test_an_unknown_response_preset_is_refused(self):
        self.send_api_key()
        response = self.client.get(self.url_for("alice"), {"view": "nonsense"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "view": ["Ask for any of: minimal, or omit this parameter for the default response."],
        }

    def test_retrieval_writes_nothing(self):
        self.send_api_key()
        with CaptureQueriesContext(connection) as captured:
            self.client.get(self.url_for("alice"))
        assert write_queries(captured.captured_queries) == []

    def test_head_writes_nothing(self):
        self.send_api_key()
        with CaptureQueriesContext(connection) as captured:
            response = self.client.head(self.url_for("alice"))
        assert response.status_code == status.HTTP_200_OK
        assert write_queries(captured.captured_queries) == []


@skip_unless_lms
class ProfilelessUserTests(UserApiV2TestCase):
    """A user with no profile row is listed with a null name rather than failing."""

    def test_user_without_a_profile_has_a_null_name(self):
        User.objects.create_user("profileless", "profileless@example.com", PASSWORD)
        self.send_api_key()
        response = self.client.get(
            reverse("user_v2:user_detail", kwargs={"username": "profileless"}),
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] is None


@skip_unless_lms
class UserPreferenceListViewTests(UserApiV2TestCase):
    """GET /api/user/v2/user_preferences/"""

    def setUp(self):
        super().setUp()
        self.url = reverse("user_v2:user_preference_list")

    def keys_in(self, response):
        """Return the (username, key) pairs carried by a page of preferences."""
        return [(row["username"], row["key"]) for row in response.data["results"]]

    def test_anonymous_caller_is_unauthenticated(self):
        response = self.client.get(self.url)
        assert_error_envelope(response, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn")

    def test_a_caller_without_the_key_is_forbidden(self):
        self.login(self.learner)
        response = self.client.get(self.url)
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz")

    def test_the_owner_without_the_key_is_forbidden(self):
        self.login(self.alice)
        response = self.client.get(self.url, {"username": "alice"})
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz")

    def test_a_caller_with_the_key_receives_every_preference(self):
        self.send_api_key()
        response = self.client.get(self.url, {"page_size": 100})
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == PAGINATION_KEYS
        assert self.keys_in(response) == [("alice", "pref-lang"), ("bob", "time_zone")]

    def test_row_shape_is_flat_by_default(self):
        self.send_api_key()
        response = self.client.get(self.url, {"username": "alice"})
        row = response.data["results"][0]
        assert set(row) == {"username", "key", "value", "url", "user"}
        assert row["username"] == "alice"
        assert row["key"] == "pref-lang"
        assert row["value"] == "en"
        assert row["user"] is None
        assert row["url"].endswith("/api/user/v2/user_preferences/alice,pref-lang/")

    def test_full_view_embeds_the_owning_user(self):
        self.send_api_key()
        response = self.client.get(self.url, {"username": "alice", "view": "full"})
        row = response.data["results"][0]
        assert set(row["user"]) == {"id", "url", "email", "name", "username", "preferences"}
        assert row["user"]["username"] == "alice"
        assert row["user"]["preferences"] == {"pref-lang": "en"}

    def test_minimal_view_drops_the_address_and_owner(self):
        self.send_api_key()
        response = self.client.get(self.url, {"username": "alice", "view": "minimal"})
        assert response.data["results"] == [{"username": "alice", "key": "pref-lang", "value": "en"}]

    def test_a_field_name_the_row_does_not_carry_is_refused(self):
        self.send_api_key()
        response = self.client.get(self.url, {"fields": "preference_key"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "fields": [
                "No such field: preference_key. "
                "Select any of: key, url, user, username, value.",
            ],
        }

    def test_a_page_beyond_the_last_one_is_not_found(self):
        self.send_api_key()
        response = self.client.get(self.url, {"page": 9999})
        assert_error_envelope(response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found")

    def test_fields_parameter_projects_the_row(self):
        self.send_api_key()
        response = self.client.get(self.url, {"username": "alice", "fields": "key,value"})
        assert response.data["results"] == [{"key": "pref-lang", "value": "en"}]

    def test_filter_by_key(self):
        UserPreferenceFactory.create(user=self.bob, key="pref-lang", value="fr")
        self.send_api_key()
        response = self.client.get(self.url, {"key": "pref-lang", "page_size": 100})
        assert self.keys_in(response) == [("alice", "pref-lang"), ("bob", "pref-lang")]

    def test_filter_by_username(self):
        self.send_api_key()
        response = self.client.get(self.url, {"username": "bob", "page_size": 100})
        assert self.keys_in(response) == [("bob", "time_zone")]

    def test_several_names_select_the_preferences_of_each(self):
        """The value is a list of names, as it is on the sibling collections of this version."""
        self.send_api_key()
        response = self.client.get(self.url, {"username": "alice,bob", "page_size": 100})
        assert response.status_code == status.HTTP_200_OK
        assert self.keys_in(response) == [("alice", "pref-lang"), ("bob", "time_zone")]
        assert response.data["count"] == 2

    def test_a_name_in_the_list_that_no_account_holds_is_left_out_of_the_page(self):
        self.send_api_key()
        response = self.client.get(self.url, {"username": "alice,nobody", "page_size": 100})
        assert self.keys_in(response) == [("alice", "pref-lang")]

    def test_an_empty_name_narrows_nothing(self):
        self.send_api_key()
        response = self.client.get(self.url, {"username": "", "page_size": 100})
        assert response.status_code == status.HTTP_200_OK
        assert self.keys_in(response) == [("alice", "pref-lang"), ("bob", "time_zone")]

    def test_a_name_sent_with_spaces_around_it_is_read_as_sent(self):
        """A name is matched exactly as it was sent, so a space before it names no owner."""
        self.send_api_key()
        for asked, expected in ((" alice", []), ("bob, alice", [("bob", "time_zone")]), ("  ", [])):
            with self.subTest(username=asked):
                response = self.client.get(self.url, {"username": asked, "page_size": 100})
                assert response.status_code == status.HTTP_200_OK, response.content
                assert self.keys_in(response) == expected

    def test_the_owner_id_the_earlier_list_took_is_refused_rather_than_ignored(self):
        """A caller that ported by changing the version segment is told, not quietly unfiltered."""
        self.send_api_key()
        response = self.client.get(self.url, {"user": self.alice.id, "page_size": 100})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "user": [f"No such query parameter. Send any of: {PREFERENCE_QUERY_PARAMS}."],
        }

    def test_an_answered_page_announces_no_deprecated_parameter(self):
        self.send_api_key()
        response = self.client.get(self.url, {"username": "alice"})
        assert response.status_code == status.HTTP_200_OK
        assert "Deprecation" not in response

    def test_ordering_by_key_descending(self):
        self.send_api_key()
        response = self.client.get(self.url, {"ordering": "-key", "page_size": 100})
        assert self.keys_in(response) == [("bob", "time_zone"), ("alice", "pref-lang")]

    def test_ordering_takes_the_fields_in_the_order_they_are_named(self):
        UserPreferenceFactory.create(user=self.bob, key="pref-lang", value="fr")
        self.send_api_key()
        response = self.client.get(self.url, {"ordering": "key,-username", "page_size": 100})
        assert self.keys_in(response) == [
            ("bob", "pref-lang"), ("alice", "pref-lang"), ("bob", "time_zone"),
        ]

    def test_unknown_ordering_is_refused(self):
        self.send_api_key()
        response = self.client.get(self.url, {"ordering": "value", "page_size": 100})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "ordering": ["Sort by any of: key, username, each optionally prefixed with '-'."],
        }

    def test_listing_writes_nothing(self):
        self.send_api_key()
        with CaptureQueriesContext(connection) as captured:
            self.client.get(self.url, {"page_size": 100})
        assert write_queries(captured.captured_queries) == []

    def test_full_view_writes_nothing(self):
        self.send_api_key()
        with CaptureQueriesContext(connection) as captured:
            self.client.get(self.url, {"page_size": 100, "view": "full"})
        assert write_queries(captured.captured_queries) == []

    def test_head_writes_nothing(self):
        self.send_api_key()
        with CaptureQueriesContext(connection) as captured:
            response = self.client.head(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert write_queries(captured.captured_queries) == []

    def test_an_unknown_response_preset_is_refused(self):
        self.send_api_key()
        response = self.client.get(self.url, {"view": "nonsense"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "view": [
                "Ask for any of: full, minimal, or omit this parameter for the default response.",
            ],
        }


@skip_unless_lms
class UserPreferenceDetailViewTests(UserApiV2TestCase):
    """GET /api/user/v2/user_preferences/{username},{key}/"""

    def url_for(self, username, key):
        """Return the member address of one stored preference."""
        return reverse("user_v2:user_preference_detail", kwargs={"username": username, "key": key})

    def test_anonymous_caller_is_unauthenticated(self):
        response = self.client.get(self.url_for("alice", "pref-lang"))
        assert_error_envelope(response, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn")

    def test_a_caller_without_the_key_is_forbidden(self):
        self.login(self.learner)
        response = self.client.get(self.url_for("alice", "pref-lang"))
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz")

    def test_the_owner_without_the_key_is_forbidden(self):
        self.login(self.alice)
        response = self.client.get(self.url_for("alice", "pref-lang"))
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz")

    def test_a_caller_with_the_key_receives_the_preference(self):
        self.send_api_key()
        response = self.client.get(self.url_for("alice", "pref-lang"))
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == {"username", "key", "value", "url", "user"}
        assert response.data["username"] == "alice"
        assert response.data["key"] == "pref-lang"
        assert response.data["value"] == "en"
        assert response.data["user"] is None

    def test_full_view_embeds_the_owning_user(self):
        self.send_api_key()
        response = self.client.get(self.url_for("alice", "pref-lang"), {"view": "full"})
        assert response.data["user"]["username"] == "alice"

    def test_a_field_name_the_preference_does_not_carry_is_refused(self):
        self.send_api_key()
        response = self.client.get(self.url_for("alice", "pref-lang"), {"fields": "preference_key"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "fields": [
                "No such field: preference_key. "
                "Select any of: key, url, user, username, value.",
            ],
        }

    def test_unknown_key_is_not_found(self):
        self.send_api_key()
        response = self.client.get(self.url_for("alice", "nope"))
        assert_error_envelope(response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found")

    def test_key_belonging_to_another_user_is_not_found(self):
        self.send_api_key()
        response = self.client.get(self.url_for("alice", "time_zone"))
        assert_error_envelope(response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found")

    def test_an_unknown_response_preset_is_refused(self):
        self.send_api_key()
        response = self.client.get(self.url_for("alice", "pref-lang"), {"view": "nonsense"})
        assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )

    def test_retrieval_writes_nothing(self):
        self.send_api_key()
        with CaptureQueriesContext(connection) as captured:
            self.client.get(self.url_for("alice", "pref-lang"))
        assert write_queries(captured.captured_queries) == []

    def test_head_writes_nothing(self):
        self.send_api_key()
        with CaptureQueriesContext(connection) as captured:
            response = self.client.head(self.url_for("alice", "pref-lang"))
        assert response.status_code == status.HTTP_200_OK
        assert write_queries(captured.captured_queries) == []


@skip_unless_lms
class NameSelectorTests(UserApiV2TestCase):
    """``?username=`` on the collections of this version that select accounts by name."""

    def test_every_collection_that_selects_accounts_by_name_reads_one_list(self):
        """
        One value names the same accounts wherever it is sent.

        The member address of the stored preferences already reads a comma as
        the separator between the two names that identify a preference, so a
        collection of this version that read the comma as part of a username
        would be answering a value its own addresses spell differently.
        """
        self.login(self.admin)
        self.send_api_key()
        selection = {"username": "alice,bob", "page_size": 100}
        pages = {
            name: self.client.get(reverse(f"user_v2:{name}"), selection)
            for name in ("user_list", "user_preference_list", "account_list")
        }
        assert {name: page.status_code for name, page in pages.items()} == {
            "user_list": status.HTTP_200_OK,
            "user_preference_list": status.HTTP_200_OK,
            "account_list": status.HTTP_200_OK,
        }
        assert {
            name: sorted({row["username"] for row in page.data["results"]})
            for name, page in pages.items()
        } == {
            "user_list": ["alice", "bob"],
            "user_preference_list": ["alice", "bob"],
            "account_list": ["alice", "bob"],
        }


@skip_unless_lms
class TimeZoneListViewTests(UserApiV2TestCase):
    """GET /api/user/v2/time_zones/"""

    def setUp(self):
        super().setUp()
        self.url = reverse("user_v2:time_zone_list")

    def test_anonymous_caller_receives_the_list(self):
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == PAGINATION_KEYS
        assert len(response.data["results"]) == 10
        assert set(response.data["results"][0]) == {"time_zone", "description"}

    def test_authenticated_caller_receives_the_same_count(self):
        anonymous_count = self.client.get(self.url).data["count"]
        self.login(self.learner)
        response = self.client.get(self.url)
        assert response.data["count"] == anonymous_count

    def test_country_code_narrows_the_list(self):
        response = self.client.get(self.url, {"country_code": "FR", "page_size": 100})
        assert [row["time_zone"] for row in response.data["results"]] == ["Europe/Paris"]

    def test_country_code_is_case_insensitive(self):
        upper = self.client.get(self.url, {"country_code": "FR", "page_size": 100}).data["results"]
        lower = self.client.get(self.url, {"country_code": "fr", "page_size": 100}).data["results"]
        assert upper == lower

    def test_country_without_time_zones_falls_back_to_every_zone(self):
        every = self.client.get(self.url).data["count"]
        response = self.client.get(self.url, {"country_code": "BV"})
        assert response.data["count"] == every

    def test_an_empty_country_code_returns_every_zone(self):
        """A query string built from an empty variable narrows nothing rather than failing."""
        every = self.client.get(self.url).data["count"]
        response = self.client.get(self.url, {"country_code": ""})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == every

    def test_a_page_beyond_the_last_one_is_not_found(self):
        """The list is public, so its most common failure is answered to anyone."""
        response = self.client.get(self.url, {"page": 9999})
        assert_error_envelope(response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found")

    def test_malformed_country_code_is_rejected(self):
        response = self.client.get(self.url, {"country_code": "FRA"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "country_code": ["Enter a two-letter ISO 3166-1 alpha-2 country code, for example FR."],
        }

    def test_listing_writes_nothing(self):
        with CaptureQueriesContext(connection) as captured:
            self.client.get(self.url, {"page_size": 100})
        assert write_queries(captured.captured_queries) == []

    def test_head_writes_nothing(self):
        with CaptureQueriesContext(connection) as captured:
            response = self.client.head(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert write_queries(captured.captured_queries) == []


@skip_unless_lms
class PageSizeCapTests(UserApiV2TestCase):
    """
    No caller can ask any of the lists for more rows than one maximum page.

    The time-zone list is the one a caller reaches without signing in, and it
    is long enough to be worth asking for whole, so the cap is asserted against
    live rows on every list rather than read off the paginator.
    """

    MAX_PAGE_SIZE = 100

    def test_the_directory_page_is_capped(self):
        User.objects.bulk_create(
            User(username=f"extra-{index:03d}", email=f"extra-{index:03d}@example.com")
            for index in range(self.MAX_PAGE_SIZE)
        )
        self.send_api_key()
        response = self.client.get(reverse("user_v2:user_list"), {"page_size": 1000})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == self.MAX_PAGE_SIZE + 4
        assert len(response.data["results"]) == self.MAX_PAGE_SIZE
        assert response.data["num_pages"] == 2
        assert response.data["next"] is not None

    def test_the_stored_preference_page_is_capped(self):
        UserPreference.objects.bulk_create(
            UserPreference(user=self.alice, key=f"extra-{index:03d}", value="x")
            for index in range(self.MAX_PAGE_SIZE)
        )
        self.send_api_key()
        response = self.client.get(reverse("user_v2:user_preference_list"), {"page_size": 1000})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == self.MAX_PAGE_SIZE + 2
        assert len(response.data["results"]) == self.MAX_PAGE_SIZE
        assert response.data["num_pages"] == 2

    def test_the_time_zone_page_is_capped_for_an_anonymous_caller(self):
        every_zone = len(get_country_time_zones())
        assert every_zone > self.MAX_PAGE_SIZE, every_zone
        response = self.client.get(reverse("user_v2:time_zone_list"), {"page_size": 1000})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == every_zone
        assert len(response.data["results"]) == self.MAX_PAGE_SIZE
        assert response.data["num_pages"] == ceil(every_zone / self.MAX_PAGE_SIZE)


@skip_unless_lms
class RefusedRequestTests(UserApiV2TestCase):
    """A method this API does not serve, and an Accept header it cannot answer."""

    #: Public, standardized, and served by another API, which answers the same
    #: failures with the same type.
    OTHER_API_URL = "/api/enrollment/v2/courses/course-v1:edX+DemoX+Demo/"

    def setUp(self):
        super().setUp()
        self.public_url = reverse("user_v2:time_zone_list")

    def test_a_method_this_address_does_not_serve_is_refused_for_an_anonymous_caller(self):
        response = self.client.post(self.public_url, {}, format="json")
        envelope = assert_error_envelope(
            response,
            expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
            expected_type_slug=REFUSED_METHOD_SLUG,
        )
        assert envelope["title"] == REFUSED_METHOD_TITLE
        assert envelope["detail"] == 'Method "POST" not allowed.'
        assert response["Content-Type"] == "application/json"

    def test_an_accept_header_this_api_cannot_answer_is_refused(self):
        response = self.client.get(self.public_url, HTTP_ACCEPT="application/xml")
        envelope = assert_error_envelope(
            response,
            expected_status=status.HTTP_406_NOT_ACCEPTABLE,
            expected_type_slug=NOT_ACCEPTABLE_SLUG,
        )
        assert envelope["title"] == NOT_ACCEPTABLE_TITLE
        assert envelope["detail"] == "Could not satisfy the request Accept header."
        assert response["Content-Type"] == "application/json"

    def test_a_method_the_gated_collections_do_not_serve_is_refused(self):
        self.send_api_key()
        for url_name in ("user_v2:user_list", "user_v2:user_preference_list"):
            response = self.client.delete(reverse(url_name))
            envelope = assert_error_envelope(
                response,
                expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
                expected_type_slug=REFUSED_METHOD_SLUG,
            )
            assert envelope["detail"] == 'Method "DELETE" not allowed.'

    def test_a_method_the_gated_members_do_not_serve_is_refused(self):
        self.send_api_key()
        response = self.client.put(reverse("user_v2:user_detail", args=[self.alice.username]))
        envelope = assert_error_envelope(
            response,
            expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
            expected_type_slug=REFUSED_METHOD_SLUG,
        )
        assert envelope["detail"] == 'Method "PUT" not allowed.'

    def test_no_other_collection_of_this_version_learns_a_method_from_this_one(self):
        """The document beneath an account is merge-patched; the collections above it are not."""
        self.login(self.admin)
        for address in (reverse("user_v2:account_list"), reverse("user_v2:user_list")):
            with self.subTest(address=address):
                response = self.client.patch(
                    address, "{}", content_type="application/merge-patch+json", HTTP_X_EDX_API_KEY=API_KEY,
                )
                envelope = assert_error_envelope(
                    response,
                    expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
                    expected_type_slug=REFUSED_METHOD_SLUG,
                )
                assert envelope["detail"] == 'Method "PATCH" not allowed.'

    def test_another_api_is_classified_exactly_as_the_shared_catalog_classifies_it(self):
        """The catalog names no refused method, and nothing here changes that for anyone else."""
        response = self.client.post(self.OTHER_API_URL, {}, format="json")
        envelope = assert_error_envelope(
            response,
            expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
            expected_type_slug="internal",
        )
        assert envelope["title"] == "Internal Server Error"

    def test_a_refused_method_is_typed_here_as_another_api_types_it(self):
        ours = self.client.post(self.public_url, {}, format="json").json()
        theirs = self.client.post(self.OTHER_API_URL, {}, format="json").json()
        assert (ours["status"], ours["type"], ours["title"]) == (theirs["status"], theirs["type"], theirs["title"])


@skip_unless_lms
class UnknownQueryParameterTests(UserApiV2TestCase):
    """Each endpoint refuses a query parameter it reads nothing from, and takes the rest."""

    def setUp(self):
        super().setUp()
        self.user_list = reverse("user_v2:user_list")
        self.user_detail = reverse("user_v2:user_detail", kwargs={"username": "alice"})
        self.preference_list = reverse("user_v2:user_preference_list")
        self.preference_detail = reverse(
            "user_v2:user_preference_detail", kwargs={"username": "alice", "key": "pref-lang"},
        )
        self.time_zone_list = reverse("user_v2:time_zone_list")

    def assert_refused(self, response, name, offered):
        """Assert the response named the parameter it would not take, and the ones it takes."""
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {name: [f"No such query parameter. Send any of: {offered}."]}

    def assert_each_is_answered(self, address, parameter_sets):
        """Assert the endpoint answers each of these query strings."""
        for params in parameter_sets:
            with self.subTest(params=params):
                response = self.client.get(address, params)
                assert response.status_code == status.HTTP_200_OK, response.content

    def test_the_directory_refuses_a_parameter_it_does_not_read(self):
        self.send_api_key()
        response = self.client.get(self.user_list, {"lms_user_id": self.alice.id, "page_size": 100})
        self.assert_refused(response, "lms_user_id", USER_QUERY_PARAMS)

    def test_a_mistyped_filter_refuses_the_page_instead_of_returning_every_row(self):
        self.send_api_key()
        response = self.client.get(self.user_list, {"usernme": "alice", "page_size": 100})
        self.assert_refused(response, "usernme", USER_QUERY_PARAMS)

    def test_every_unrecognised_parameter_is_named_at_once(self):
        self.send_api_key()
        response = self.client.get(self.user_list, {"sort": "username", "limit": "10"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert sorted(envelope["errors"]) == ["limit", "sort"]

    def test_a_directory_entry_refuses_a_parameter_it_does_not_read(self):
        self.send_api_key()
        response = self.client.get(self.user_detail, {"expand": "profile"})
        self.assert_refused(response, "expand", USER_QUERY_PARAMS)

    def test_the_preference_list_refuses_a_parameter_it_does_not_read(self):
        self.send_api_key()
        response = self.client.get(self.preference_list, {"value": "en", "page_size": 100})
        self.assert_refused(response, "value", PREFERENCE_QUERY_PARAMS)

    def test_a_stored_preference_refuses_a_parameter_it_does_not_read(self):
        self.send_api_key()
        response = self.client.get(self.preference_detail, {"value": "en"})
        self.assert_refused(response, "value", PREFERENCE_QUERY_PARAMS)

    def test_the_time_zone_list_refuses_a_parameter_it_does_not_read(self):
        response = self.client.get(self.time_zone_list, {"country": "FR"})
        self.assert_refused(response, "country", TIME_ZONE_QUERY_PARAMS)

    def test_the_time_zone_list_refuses_a_sort_it_does_not_offer(self):
        """This list is ordered by its display form and reads no sort parameter."""
        response = self.client.get(self.time_zone_list, {"ordering": "country_code"})
        self.assert_refused(response, "ordering", TIME_ZONE_QUERY_PARAMS)

    def test_an_unknown_parameter_does_not_answer_an_anonymous_caller(self):
        response = self.client.get(self.user_list, {"lms_user_id": self.alice.id})
        assert_error_envelope(
            response, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn",
        )

    def test_an_unknown_parameter_does_not_answer_a_caller_without_the_key(self):
        self.login(self.learner)
        response = self.client.get(self.preference_list, {"lms_user_id": self.alice.id})
        assert_error_envelope(
            response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
        )

    def test_the_directory_answers_every_parameter_it_reads(self):
        self.send_api_key()
        self.assert_each_is_answered(self.user_list, [
            {"page": 1},
            {"page_size": 2},
            {"ordering": "-username"},
            {"view": "minimal"},
            {"fields": "username"},
            {"format": "json"},
            {"username": "alice"},
            {"email": "alice@example.com"},
            {"is_active": "true"},
            {"preference_key": "pref-lang"},
            {"forum_role": "Moderator", "course_key": str(COURSE_KEY)},
        ])

    def test_a_directory_entry_answers_every_parameter_it_reads(self):
        self.send_api_key()
        self.assert_each_is_answered(self.user_detail, [
            {"view": "minimal"}, {"fields": "username"}, {"format": "json"}, {"ordering": "username"},
        ])

    def test_the_preference_list_answers_every_parameter_it_reads(self):
        self.send_api_key()
        self.assert_each_is_answered(self.preference_list, [
            {"page": 1},
            {"page_size": 2},
            {"ordering": "-key"},
            {"view": "full"},
            {"fields": "key"},
            {"format": "json"},
            {"key": "pref-lang"},
            {"username": "alice"},
        ])

    def test_a_stored_preference_answers_every_parameter_it_reads(self):
        self.send_api_key()
        self.assert_each_is_answered(self.preference_detail, [
            {"view": "full"}, {"fields": "key"}, {"format": "json"},
        ])

    def test_the_time_zone_list_answers_every_parameter_it_reads(self):
        self.assert_each_is_answered(self.time_zone_list, [
            {"country_code": "FR"}, {"page": 1}, {"page_size": 5}, {"format": "json"},
        ])

    def test_a_page_of_the_directory_is_still_narrowed_by_the_filters_it_takes(self):
        """The refusal guards the parameters, not the filtering they ask for."""
        self.send_api_key()
        response = self.client.get(self.user_list, {"username": "alice", "page_size": 100})
        assert self.usernames_in(response) == ["alice"]


@skip_unless_lms
class TokenCallerTests(UserApiV2TestCase):
    """
    The directory addresses answer a request carrying the site's API key and refuse every other.

    The key is what admits a caller here, whoever sends it: a token or a
    session identifies the account a request comes from, and no account,
    staff included, reads the directory without the key.
    """

    def setUp(self):
        super().setUp()
        self.addresses = {
            "user_list": reverse("user_v2:user_list"),
            "user_detail": reverse("user_v2:user_detail", kwargs={"username": "alice"}),
            "preference_list": reverse("user_v2:user_preference_list"),
            "preference_detail": reverse(
                "user_v2:user_preference_detail", kwargs={"username": "alice", "key": "pref-lang"},
            ),
        }

    def token_headers(self, user):
        """Return the request headers that carry a token identifying the given user."""
        return {"HTTP_AUTHORIZATION": f"JWT {create_jwt_for_user(user)}"}

    def test_the_key_alone_reaches_the_directory(self):
        response = self.client.get(self.addresses["user_list"], {"page_size": 100}, HTTP_X_EDX_API_KEY=API_KEY)
        assert response.status_code == status.HTTP_200_OK
        assert self.usernames_in(response) == ["admin-caller", "alice", "bob", "learner-caller"]
        assert response.data["count"] == 4

    def test_the_key_alone_reaches_every_directory_address(self):
        self.send_api_key()
        detail = self.client.get(self.addresses["user_detail"])
        assert detail.status_code == status.HTTP_200_OK
        assert detail.data["username"] == "alice"

        preferences = self.client.get(self.addresses["preference_list"], {"username": "alice"})
        assert preferences.status_code == status.HTTP_200_OK
        assert [(row["username"], row["key"]) for row in preferences.data["results"]] == [
            ("alice", "pref-lang"),
        ]

        preference = self.client.get(self.addresses["preference_detail"])
        assert preference.status_code == status.HTTP_200_OK
        assert preference.data["value"] == "en"

    def test_the_key_admits_whichever_account_sends_it(self):
        response = self.client.get(
            self.addresses["user_list"], {"username": "alice"},
            HTTP_X_EDX_API_KEY=API_KEY, **self.token_headers(self.learner),
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.wsgi_request.user == self.learner
        assert self.usernames_in(response) == ["alice"]

    def test_a_staff_token_without_the_key_is_forbidden_on_every_directory_address(self):
        headers = self.token_headers(self.admin)
        for name, address in self.addresses.items():
            with self.subTest(address=name):
                assert_error_envelope(
                    self.client.get(address, **headers),
                    expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
                )

    def test_a_staff_session_without_the_key_is_forbidden_on_every_directory_address(self):
        self.login(self.admin)
        for name, address in self.addresses.items():
            with self.subTest(address=name):
                assert_error_envelope(
                    self.client.get(address),
                    expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
                )

    def test_a_learners_token_is_forbidden_on_every_directory_address(self):
        headers = self.token_headers(self.learner)
        for name, address in self.addresses.items():
            with self.subTest(address=name):
                response = self.client.get(address, **headers)
                assert_error_envelope(
                    response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
                )

    def test_the_owner_of_the_record_is_still_refused_without_the_key(self):
        response = self.client.get(
            self.addresses["preference_detail"], **self.token_headers(self.alice),
        )
        assert_error_envelope(
            response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
        )

    def test_a_key_other_than_the_sites_is_unauthenticated(self):
        for name, address in self.addresses.items():
            with self.subTest(address=name):
                assert_error_envelope(
                    self.client.get(address, HTTP_X_EDX_API_KEY=f"{API_KEY}-other"),
                    expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn",
                )

    @override_settings(EDX_API_KEY=None)
    def test_no_key_admits_a_caller_on_a_site_that_configures_none(self):
        assert_error_envelope(
            self.client.get(self.addresses["user_list"], HTTP_X_EDX_API_KEY="None"),
            expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn",
        )

    def test_a_token_that_does_not_decode_is_unauthenticated(self):
        response = self.client.get(
            self.addresses["user_list"], HTTP_AUTHORIZATION="JWT not.a.token", HTTP_X_EDX_API_KEY=API_KEY,
        )
        assert_error_envelope(
            response, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn",
        )


@skip_unless_lms
class SharedSortValueTests(UserApiV2TestCase):
    """Users that compare equal on the sorted column are ordered the same way on every page."""

    def setUp(self):
        super().setUp()
        self.url = reverse("user_v2:user_list")
        joined = datetime(2024, 3, 1, tzinfo=UTC)
        for username in ("zoe-shared", "mia-shared", "ada-shared"):
            UserFactory.create(username=username, date_joined=joined)
        self.send_api_key()

    def test_users_sharing_a_join_date_are_ordered_by_username(self):
        response = self.client.get(self.url, {"ordering": "date_joined", "page_size": 100})
        shared = [name for name in self.usernames_in(response) if name.endswith("-shared")]
        assert shared == ["ada-shared", "mia-shared", "zoe-shared"]

    def test_the_sort_the_caller_asked_for_still_decides_the_order(self):
        """The tie-break settles rows that compare equal; the requested sort places them."""
        response = self.client.get(self.url, {"ordering": "-date_joined", "page_size": 100})
        assert self.usernames_in(response)[:3] == ["ada-shared", "mia-shared", "zoe-shared"]

    def test_paging_through_a_shared_join_date_lists_each_user_once(self):
        listed = []
        for page in (1, 2, 3):
            response = self.client.get(
                self.url, {"ordering": "date_joined", "page_size": 3, "page": page},
            )
            listed.extend(self.usernames_in(response))
        assert sorted(listed) == [
            "ada-shared", "admin-caller", "alice", "bob", "learner-caller", "mia-shared", "zoe-shared",
        ]


@skip_unless_lms
class DeactivatedStaffCallerTests(UserApiV2TestCase):
    """
    A staff account is refused here without the key whether or not it is still active.

    Deactivating the account changes only which half of the default
    authentication pair refuses it: the session half stops accepting it, while
    a token issued before the account was deactivated is still read and then
    refused for the missing key. The key admits the request whatever state the
    account sending it is in.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse("user_v2:user_list")
        self.staff = AdminFactory.create(username="deactivated-admin", password=PASSWORD)
        self.directory = {
            "user_list": self.url,
            "user_detail": reverse("user_v2:user_detail", kwargs={"username": "alice"}),
            "preference_list": reverse("user_v2:user_preference_list"),
            "preference_detail": reverse(
                "user_v2:user_preference_detail", kwargs={"username": "alice", "key": "pref-lang"},
            ),
        }

    def deactivate(self, user):
        """Clear an account's active flag the way an operator does, leaving its staff flag."""
        user.is_active = False
        user.save()

    def test_a_session_held_by_a_staff_account_is_refused_before_and_after_it_is_deactivated(self):
        self.login(self.staff)
        assert_error_envelope(
            self.client.get(self.url), expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
        )
        self.deactivate(self.staff)
        response = self.client.get(self.url)
        assert_error_envelope(
            response, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn",
        )

    def test_a_token_held_by_a_staff_account_is_refused_before_and_after_it_is_deactivated(self):
        headers = {"HTTP_AUTHORIZATION": f"JWT {create_jwt_for_user(self.staff)}"}
        assert_error_envelope(
            self.client.get(self.url, **headers),
            expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
        )
        self.deactivate(self.staff)
        response = self.client.get(self.url, {"page_size": 100}, **headers)
        assert_error_envelope(
            response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
        )

    def test_a_deactivated_staff_token_is_refused_at_every_directory_address(self):
        self.deactivate(self.staff)
        headers = {"HTTP_AUTHORIZATION": f"JWT {create_jwt_for_user(self.staff)}"}
        for name, address in self.directory.items():
            with self.subTest(address=name):
                response = self.client.get(address, **headers)
                assert_error_envelope(
                    response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
                )

    def test_a_deactivated_staff_session_is_refused_at_every_directory_address(self):
        self.login(self.staff)
        self.deactivate(self.staff)
        for name, address in self.directory.items():
            with self.subTest(address=name):
                response = self.client.get(address)
                assert_error_envelope(
                    response, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn",
                )

    def test_the_directory_does_not_list_the_account_that_was_refused(self):
        """The refusal is the whole response: no row of the page leaks past it."""
        self.deactivate(self.staff)
        response = self.client.get(
            self.url, {"page_size": 100}, HTTP_AUTHORIZATION=f"JWT {create_jwt_for_user(self.staff)}",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "results" not in response.data

    def test_the_key_admits_a_deactivated_staff_account_as_it_admits_anyone(self):
        self.deactivate(self.staff)
        response = self.client.get(
            self.url, {"page_size": 100},
            HTTP_AUTHORIZATION=f"JWT {create_jwt_for_user(self.staff)}", HTTP_X_EDX_API_KEY=API_KEY,
        )
        assert response.status_code == status.HTTP_200_OK
        assert "deactivated-admin" in self.usernames_in(response)

    def test_a_token_held_by_a_deactivated_learner_is_still_forbidden(self):
        self.deactivate(self.learner)
        response = self.client.get(
            self.url, HTTP_AUTHORIZATION=f"JWT {create_jwt_for_user(self.learner)}",
        )
        assert_error_envelope(
            response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
        )


@skip_unless_lms
class PreferenceSerializerTests(UserApiV2TestCase):
    """The preference address is built with or without a request to make it absolute."""

    def test_url_is_relative_without_a_request(self):
        preference = self.alice.preferences.get(key="pref-lang")
        data = UserPreferenceRecordSerializer(preference).data
        assert data["url"] == "/api/user/v2/user_preferences/alice,pref-lang/"
        assert data["user"] is None


class RecordingScopePolicy:
    """A record-visibility policy that drops one owner's rows and records each call."""

    def __init__(self, hidden_username):
        self.hidden_username = hidden_username
        self.calls = []

    def scope(self, queryset, subject):
        """Return the rows of ``queryset`` that do not belong to the hidden owner."""
        self.calls.append((queryset.model, subject))
        owner_field = "username" if queryset.model is User else "user__username"
        return queryset.exclude(**{owner_field: self.hidden_username})


@skip_unless_lms
class QuerysetScopingSeamTests(UserApiV2TestCase):
    """
    The record-visibility layer of the two collections backed by a queryset.

    Each of these collections declares a policy that decides which rows a
    caller may see, and the rows it leaves out are absent from the answer
    rather than refused. The policy that ships keeps every row, because every
    caller holding the site's API key may read all of them, so these tests
    put a narrowing policy in its place and read the answer: a collection
    that stopped consulting its policy would hand back the rows the policy
    left out, and a collection that no longer declared one would narrow
    nothing whatever policy it was given.
    """

    def scoped(self, view_class, hidden_username):
        """Run the view class with a policy that hides one owner's rows."""
        policy = RecordingScopePolicy(hidden_username)
        return patch.object(view_class, "scoping_policy", policy, create=True), policy

    def test_user_list_leaves_out_the_rows_its_policy_excludes(self):
        self.login(self.admin)
        self.send_api_key()
        patched, policy = self.scoped(UserViewSet, "alice")
        with patched:
            response = self.client.get(reverse("user_v2:user_list"))
        assert response.status_code == status.HTTP_200_OK
        assert self.usernames_in(response) == ["admin-caller", "bob", "learner-caller"]
        assert policy.calls == [(User, self.admin)]

    def test_user_detail_of_an_excluded_row_is_not_found(self):
        self.send_api_key()
        patched, _ = self.scoped(UserViewSet, "alice")
        with patched:
            response = self.client.get(reverse("user_v2:user_detail", kwargs={"username": "alice"}))
        assert_error_envelope(
            response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found",
        )

    def test_user_detail_of_an_included_row_is_returned(self):
        self.send_api_key()
        patched, _ = self.scoped(UserViewSet, "alice")
        with patched:
            response = self.client.get(reverse("user_v2:user_detail", kwargs={"username": "bob"}))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["username"] == "bob"

    def test_preference_list_leaves_out_the_rows_its_policy_excludes(self):
        self.login(self.admin)
        self.send_api_key()
        patched, policy = self.scoped(UserPreferenceViewSet, "alice")
        with patched:
            response = self.client.get(reverse("user_v2:user_preference_list"))
        assert response.status_code == status.HTTP_200_OK
        assert [(row["username"], row["key"]) for row in response.data["results"]] == [("bob", "time_zone")]
        assert policy.calls == [(UserPreference, self.admin)]

    def test_preference_detail_of_an_excluded_row_is_not_found(self):
        self.send_api_key()
        patched, _ = self.scoped(UserPreferenceViewSet, "alice")
        with patched:
            response = self.client.get(
                reverse("user_v2:user_preference_detail", kwargs={"username": "alice", "key": "pref-lang"}),
            )
        assert_error_envelope(
            response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found",
        )

    def test_preference_detail_of_an_included_row_is_returned(self):
        self.send_api_key()
        patched, _ = self.scoped(UserPreferenceViewSet, "alice")
        with patched:
            response = self.client.get(
                reverse("user_v2:user_preference_detail", kwargs={"username": "bob", "key": "time_zone"}),
            )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["value"] == "UTC"

    def test_each_scoped_collection_declares_a_policy_of_its_own(self):
        """
        Each of these two views names the policy that governs its rows.

        A view that inherited the default instead of naming one would be left
        with no policy at all, so the declaration is read from the view's own
        attributes rather than from anything it inherits.
        """
        for view_class in (UserViewSet, UserPreferenceViewSet):
            declared = vars(view_class).get("scoping_policy")
            assert declared is not None, f"{view_class.__name__} declares no record-visibility policy"
            assert callable(getattr(declared, "scope", None)), (
                f"{view_class.__name__} declares a record-visibility policy that cannot narrow rows"
            )

    def test_the_rows_are_fetched_through_the_declared_policy(self):
        """
        The policy is what the rows are fetched through, not a step that can be skipped.

        A view left without a usable policy hands back no rows at all rather
        than every row, which is how a collection that stopped consulting its
        policy is told apart from one whose policy happens to keep every row.
        """
        for view_class in (UserViewSet, UserPreferenceViewSet):
            with patch.object(view_class, "scoping_policy", None, create=True):
                with pytest.raises(ImproperlyConfigured):
                    view_class().get_queryset()


def routed_view_classes():
    """Return every view class this version mounts, by the name it is routed under."""
    classes = {}
    for entry in v2_urls.urlpatterns:
        view_class = getattr(entry.callback, "cls", None)
        if view_class is not None:
            classes[view_class.__name__] = view_class
    return classes


#: Words that name how an address is built rather than what it answers. A
#: description carrying one of these was written for somebody reading this
#: module, and the body of an OPTIONS request is not where they will read it.
IMPLEMENTATION_TERMS = (
    "ViewSet",
    "queryset",
    "permission_classes",
    "serializer",
    "filter set",
    "filter backend",
    "mixin",
    "scoping policy",
    "router",
    "selectors",
    "class attribute",
)

#: The longest description an address gives of itself. The client-facing text
#: is a short paragraph; the maintainer-facing text these replaced ran to four.
DESCRIPTION_LIMIT = 700


@skip_unless_lms
class AddressSelfDescriptionTests(UserApiV2TestCase):
    """
    What each address of this version says about itself to a caller that asks.

    An OPTIONS request is answered with the description the view class
    carries, so that description is published text and is read by clients and
    by documentation browsers alongside the description of the same address in
    the schema. These tests hold it to being written for the caller: no markup
    that renders as itself in a JSON body, no vocabulary from the
    implementation, and no essay.
    """

    def test_every_mounted_address_is_described(self):
        """Each view this version mounts says something about itself."""
        described = {
            name: view_class().get_view_description()
            for name, view_class in routed_view_classes().items()
        }
        assert len(described) == 21, sorted(described)
        assert [name for name, text in described.items() if not text.strip()] == []

    def test_no_address_describes_itself_in_implementation_terms(self):
        offenders = {
            name: [term for term in IMPLEMENTATION_TERMS if term in view_class().get_view_description()]
            for name, view_class in routed_view_classes().items()
        }
        assert {name: terms for name, terms in offenders.items() if terms} == {}

    def test_no_address_describes_itself_with_markup_a_caller_sees_as_itself(self):
        """Double backticks are markup in this module's own documentation and text in a JSON body."""
        carrying_markup = [
            name for name, view_class in routed_view_classes().items()
            if "``" in view_class().get_view_description()
        ]
        assert carrying_markup == []

    def test_no_address_describes_itself_at_length(self):
        too_long = {
            name: len(view_class().get_view_description())
            for name, view_class in routed_view_classes().items()
            if len(view_class().get_view_description()) > DESCRIPTION_LIMIT
        }
        assert too_long == {}

    def test_options_answers_with_the_description_the_view_carries(self):
        """
        The description a view carries is the one a caller is handed.

        This is what makes the text above published rather than internal: the
        framework answers an OPTIONS request with it verbatim.
        """
        self.send_api_key()
        answer = self.client.options(reverse("user_v2:user_list"))
        assert answer.status_code == status.HTTP_200_OK
        assert answer.json()["description"] == UserViewSet().get_view_description()
        assert answer.json()["description"].startswith(
            "Directory entries of the platform's users, each addressed by username.",
        )


@skip_unless_lms
class SelectedPropertyNameTests(UserApiV2TestCase):
    """
    What a selected property name may be, at the collections that take one.

    The parameter takes the names of properties the answer carries. A name
    written with a dot in it names no such property: the answer carries the
    property the name starts with, whole, and the rest of the name selects
    nothing. Reading such a name as its first part would hand a caller the
    whole of a property it asked one piece of, and would accept a second part
    that names nothing at all.
    """

    def setUp(self):
        super().setUp()
        self.list_url = reverse("user_v2:user_list")
        self.member_url = reverse("user_v2:user_detail", kwargs={"username": "alice"})
        self.preference_list_url = reverse("user_v2:user_preference_list")
        self.send_api_key()

    def test_a_name_written_with_a_dot_is_refused_on_the_directory_list(self):
        response = self.client.get(f"{self.list_url}?fields=username.first")
        assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert response.json()["errors"]["fields"] == [
            "No such field: username.first. Select any of: email, id, name, preferences, url, "
            "username.",
        ]

    def test_a_name_written_with_a_dot_is_refused_on_a_directory_entry(self):
        response = self.client.get(f"{self.member_url}?fields=preferences.pref-lang")
        assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert "No such field: preferences.pref-lang." in response.json()["errors"]["fields"][0]

    def test_a_name_written_with_a_dot_is_refused_on_the_preference_list(self):
        response = self.client.get(f"{self.preference_list_url}?fields=user.email")
        assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert "No such field: user.email." in response.json()["errors"]["fields"][0]

    def test_a_plain_name_still_selects_that_property_alone(self):
        response = self.client.get(f"{self.member_url}?fields=username")
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"username": "alice"}

    def test_a_plain_name_still_selects_that_property_alone_on_a_list(self):
        response = self.client.get(f"{self.list_url}?fields=username,email&ordering=username")
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["results"] == [
            {"username": "admin-caller", "email": self.admin.email},
            {"username": "alice", "email": "alice@example.com"},
            {"username": "bob", "email": "bob@example.com"},
            {"username": "learner-caller", "email": self.learner.email},
        ]


@skip_unless_lms
class UnsatisfiableAcceptTests(UserApiV2TestCase):
    """
    What an address answers a request it can give no acceptable answer to.

    Which representations an answer may be given in is settled as the request
    enters, so the refusal reaches an address that answers with a body and one
    that answers with none alike. It is published by every operation of this
    version, and these are the measurements that publication rests on.
    """

    def addresses(self):
        """Return a representative request to each kind of address of this version, by method."""
        return [
            ("get", reverse("user_v2:user_list"), self.admin),
            ("get", reverse("user_v2:user_detail", kwargs={"username": "alice"}), self.admin),
            ("get", reverse("user_v2:user_preference_list"), self.admin),
            ("get", reverse("user_v2:time_zone_list"), self.learner),
            ("get", reverse("user_v2:account_list"), self.learner),
            ("get", reverse("user_v2:account_detail", kwargs={"username": "me"}), self.learner),
            ("get", reverse("user_v2:account_preference_list", kwargs={"username": "alice"}), self.alice),
            (
                "get",
                reverse("user_v2:account_preference_detail", kwargs={"username": "alice", "key": "pref-lang"}),
                self.alice,
            ),
            ("delete", reverse("user_v2:account_profile_image", kwargs={"username": "alice"}), self.alice),
        ]

    def test_an_accept_header_naming_nothing_the_address_answers_in_is_refused(self):
        answered = {}
        for method, url, caller in self.addresses():
            self.client.logout()
            self.login(caller)
            response = getattr(self.client, method)(url, HTTP_ACCEPT="text/csv")
            answered[f"{method.upper()} {url}"] = response.status_code
        assert set(answered.values()) == {status.HTTP_406_NOT_ACCEPTABLE}, answered

    def test_the_refusal_comes_back_in_the_body_this_version_answers_with(self):
        """The refusal is readable even though the caller asked for a representation it is not in."""
        self.send_api_key()
        response = self.client.get(reverse("user_v2:user_list"), HTTP_ACCEPT="text/csv")
        assert_error_envelope(
            response, expected_status=status.HTTP_406_NOT_ACCEPTABLE, expected_type_slug=NOT_ACCEPTABLE_SLUG,
        )

    def test_an_address_that_answers_with_no_body_refuses_it_too(self):
        """
        Nothing is read or changed on the way to the refusal.

        Clearing an image answers with no body at all, so the representation
        asked for could make no difference to what comes back; the refusal is
        settled before the handler runs, and the stored image is left alone.
        """
        UserProfile.objects.filter(user=self.alice).update(profile_image_uploaded_at=datetime.now(UTC))
        self.login(self.alice)
        response = self.client.delete(
            reverse("user_v2:account_profile_image", kwargs={"username": "alice"}),
            HTTP_ACCEPT="text/csv",
        )
        assert response.status_code == status.HTTP_406_NOT_ACCEPTABLE
        self.alice.profile.refresh_from_db()
        assert self.alice.profile.has_profile_image is True

    def test_an_accept_header_the_address_can_satisfy_is_answered(self):
        """The refusal is about what was asked for, not about asking at all."""
        self.send_api_key()
        response = self.client.get(reverse("user_v2:user_list"), HTTP_ACCEPT="application/json")
        assert response.status_code == status.HTTP_200_OK
        assert self.usernames_in(response) == ["admin-caller", "alice", "bob", "learner-caller"]


@skip_unless_lms
class SessionWithoutItsTokenTests(UserApiV2TestCase):
    """
    What a write answers a session the site cannot tie to a page it served.

    Both of these collections record something for the caller and are reached
    from the site's own pages, and both refuse such a session. Only one of
    them published that refusal, so they are measured together here.
    """

    def test_asking_for_a_name_change_with_a_session_without_its_token_is_refused(self):
        client = APIClient(enforce_csrf_checks=True)
        assert client.login(username=self.alice.username, password=PASSWORD)
        assert_error_envelope(
            client.post(reverse("user_v2:name_change_list"), {"name": "Alice Example"}, format="json"),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )
        assert PendingNameChange.objects.filter(user=self.alice).count() == 0

    def test_recording_an_organizations_messages_with_the_same_session_is_refused_the_same_way(self):
        """The refusal the sibling collection publishes is the one this one answers with."""
        client = APIClient(enforce_csrf_checks=True)
        assert client.login(username=self.alice.username, password=PASSWORD)
        assert_error_envelope(
            client.post(
                reverse("user_v2:email_opt_in_list"),
                {"course_key": str(COURSE_KEY), "email_opt_in": True},
                format="json",
            ),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )

    def test_a_name_change_asked_for_with_the_token_the_session_carries_is_recorded(self):
        """The refusal is about the missing token, not about the address."""
        self.login(self.alice)
        response = self.client.post(
            reverse("user_v2:name_change_list"), {"name": "Alice Example"}, format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert PendingNameChange.objects.get(user=self.alice).new_name == "Alice Example"


class HiddenKeyPolicy:
    """A record-visibility policy that drops the preferences stored under one name."""

    def __init__(self, hidden_key):
        self.hidden_key = hidden_key
        self.calls = 0

    def scope(self, queryset, subject):  # pylint: disable=unused-argument
        """Return the rows of ``queryset`` stored under any other name."""
        self.calls += 1
        return queryset.exclude(key=self.hidden_key)


@skip_unless_lms
class EmbeddedRowScopingTests(UserApiV2TestCase):
    """
    Rows of one resource carried inside another come through the policy that governs them.

    A directory entry carries the preferences stored against its account, and
    a stored preference asked for whole carries its owner's entry, which
    carries those preferences again. Those rows belong to the other
    collection, so the policy that decides which of them a caller may see is
    that collection's and not the one the entry came from. Applying it is
    arranged where the page is fetched, so it costs no query per row.
    """

    def directory_of(self, rows):
        """Create ``rows`` users, each with two stored preferences."""
        made = []
        for index in range(rows):
            user = UserFactory.create(username=f"row-{index:03d}", password=PASSWORD)
            UserPreferenceFactory.create(user=user, key="pref-lang", value="en")
            UserPreferenceFactory.create(user=user, key="time_zone", value="UTC")
            made.append(user)
        return made

    def test_the_preferences_inside_a_directory_entry_come_through_their_own_policy(self):
        policy = HiddenKeyPolicy("pref-lang")
        self.send_api_key()
        with patch.object(UserViewSet, "embedded_preference_policy", policy, create=True):
            response = self.client.get(reverse("user_v2:user_detail", kwargs={"username": "alice"}))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["preferences"] == {}
        assert policy.calls == 1

    def test_a_preference_the_embedded_policy_keeps_is_still_carried(self):
        policy = HiddenKeyPolicy("pref-lang")
        self.send_api_key()
        with patch.object(UserViewSet, "embedded_preference_policy", policy, create=True):
            response = self.client.get(reverse("user_v2:user_detail", kwargs={"username": "bob"}))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["preferences"] == {"time_zone": "UTC"}

    def test_the_preferences_inside_a_page_of_entries_come_through_their_own_policy(self):
        self.directory_of(5)
        policy = HiddenKeyPolicy("pref-lang")
        self.send_api_key()
        with patch.object(UserViewSet, "embedded_preference_policy", policy, create=True):
            response = self.client.get(reverse("user_v2:user_list"), {"page_size": 100})
        assert response.status_code == status.HTTP_200_OK
        carried = {row["username"]: row["preferences"] for row in response.data["results"]}
        assert [name for name, preferences in carried.items() if "pref-lang" in preferences] == []
        assert carried["row-000"] == {"time_zone": "UTC"}

    def test_narrowing_the_embedded_preferences_costs_no_query_per_row(self):
        """
        The policy is applied where the page is fetched, so it is run once however many rows there are.

        One row would hide a per-row query that cancels a prefetch, so this is
        measured at five rows and again at twenty-five.
        """
        self.send_api_key()
        measured = {}
        for rows in (5, 25):
            User.objects.filter(username__startswith="row-").delete()
            self.directory_of(rows)
            policy = HiddenKeyPolicy("pref-lang")
            with patch.object(UserViewSet, "embedded_preference_policy", policy, create=True):
                # The first request of a signed-in client pays for its session,
                # which is not part of what is being measured here.
                self.client.get(reverse("user_v2:user_list"), {"page_size": 100})
                with CaptureQueriesContext(connection) as captured:
                    response = self.client.get(reverse("user_v2:user_list"), {"page_size": 100})
                assert response.status_code == status.HTTP_200_OK
                assert len(response.data["results"]) == rows + 4
            measured[rows] = len(captured)
        assert measured[5] == measured[25], measured

    def test_the_preferences_inside_an_embedded_owner_come_through_their_own_policy(self):
        """A preference asked for whole carries its owner, whose own preferences are narrowed too."""
        policy = HiddenKeyPolicy("pref-lang")
        self.send_api_key()
        with patch.object(UserPreferenceViewSet, "embedded_preference_policy", policy, create=True):
            response = self.client.get(
                reverse("user_v2:user_preference_detail", kwargs={"username": "alice", "key": "pref-lang"}),
                {"view": "full"},
            )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["user"]["preferences"] == {}

    def test_narrowing_an_embedded_owners_preferences_costs_no_query_per_row(self):
        self.send_api_key()
        measured = {}
        for rows in (5, 25):
            User.objects.filter(username__startswith="row-").delete()
            self.directory_of(rows)
            policy = HiddenKeyPolicy("pref-lang")
            address = reverse("user_v2:user_preference_list")
            with patch.object(UserPreferenceViewSet, "embedded_preference_policy", policy, create=True):
                self.client.get(address, {"view": "full", "page_size": 100})
                with CaptureQueriesContext(connection) as captured:
                    response = self.client.get(address, {"view": "full", "page_size": 100})
                assert response.status_code == status.HTTP_200_OK
                assert len(response.data["results"]) == rows * 2 + 2
            measured[rows] = len(captured)
        assert measured[5] == measured[25], measured


class HiddenOwnerPolicy:
    """A record-visibility policy over owners that hides one of them and counts its calls."""

    def __init__(self, hidden_username):
        self.hidden_username = hidden_username
        self.calls = 0

    def scope(self, queryset, subject):  # pylint: disable=unused-argument
        """Return the rows of ``queryset`` that are not the hidden owner."""
        self.calls += 1
        return queryset.exclude(username=self.hidden_username)


@skip_unless_lms
class EmbeddedOwnerScopingTests(UserApiV2TestCase):
    """
    The owner carried inside a stored preference comes through the policy that governs owners.

    A preference asked for whole carries its owner's directory entry, which is
    a row of the directory and not of the preference collection, so which
    owners a caller may see is the directory's decision. An entry the caller
    may not see is left out of the answer; the preference itself is still
    returned, named by the owner it is stored against, because which
    preferences come back is the preference collection's own decision.
    """

    def preferences_of(self, rows):
        """Create ``rows`` users, each with two stored preferences."""
        for index in range(rows):
            user = UserFactory.create(username=f"row-{index:03d}", password=PASSWORD)
            UserPreferenceFactory.create(user=user, key="pref-lang", value="en")
            UserPreferenceFactory.create(user=user, key="time_zone", value="UTC")

    def test_an_owner_the_embedded_policy_leaves_out_is_not_carried_inside_a_page(self):
        policy = HiddenOwnerPolicy("alice")
        self.send_api_key()
        with patch.object(UserPreferenceViewSet, "embedded_user_policy", policy, create=True):
            response = self.client.get(
                reverse("user_v2:user_preference_list"), {"view": "full", "page_size": 100},
            )
        assert response.status_code == status.HTTP_200_OK
        carried = {row["username"]: row["user"] for row in response.data["results"]}
        assert set(carried) == {"alice", "bob"}
        assert carried["alice"] is None
        assert carried["bob"]["username"] == "bob"
        assert carried["bob"]["email"] == "bob@example.com"

    def test_an_owner_the_embedded_policy_leaves_out_is_not_carried_inside_one_preference(self):
        policy = HiddenOwnerPolicy("alice")
        self.send_api_key()
        with patch.object(UserPreferenceViewSet, "embedded_user_policy", policy, create=True):
            response = self.client.get(
                reverse("user_v2:user_preference_detail", kwargs={"username": "alice", "key": "pref-lang"}),
                {"view": "full"},
            )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["user"] is None
        assert response.data["username"] == "alice"
        assert response.data["key"] == "pref-lang"
        assert response.data["value"] == "en"

    def test_an_owner_the_embedded_policy_keeps_is_still_carried(self):
        """
        An owner the policy keeps comes back whole.

        This is a control rather than a guard: it reads a preference whose
        owner the policy keeps, so it is green whether or not the policy is
        consulted. It is here to show the answer is not narrowed too far.
        """
        policy = HiddenOwnerPolicy("alice")
        self.send_api_key()
        with patch.object(UserPreferenceViewSet, "embedded_user_policy", policy, create=True):
            response = self.client.get(
                reverse("user_v2:user_preference_detail", kwargs={"username": "bob", "key": "time_zone"}),
                {"view": "full"},
            )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["user"]["username"] == "bob"
        assert response.data["user"]["preferences"] == {"time_zone": "UTC"}

    def test_the_owners_of_a_page_are_resolved_once_however_many_rows_it_carries(self):
        """
        The owners a page may carry are settled for the whole page, so the cost does not grow with rows.

        One row would hide a per-row lookup, so this is measured at five rows
        and again at twenty-five, and the policy is asked once either way.
        """
        self.send_api_key()
        measured = {}
        address = reverse("user_v2:user_preference_list")
        for rows in (5, 25):
            User.objects.filter(username__startswith="row-").delete()
            self.preferences_of(rows)
            policy = HiddenOwnerPolicy("alice")
            with patch.object(UserPreferenceViewSet, "embedded_user_policy", policy, create=True):
                # The first request of a signed-in client pays for its session,
                # which is not part of what is being measured here.
                self.client.get(address, {"view": "full", "page_size": 100})
                with CaptureQueriesContext(connection) as captured:
                    response = self.client.get(address, {"view": "full", "page_size": 100})
                assert response.status_code == status.HTTP_200_OK
                assert len(response.data["results"]) == rows * 2 + 2
                assert policy.calls == 2, "the owners of a page are resolved once per request"
            measured[rows] = len(captured)
        assert measured[5] == measured[25], measured

    def test_no_owner_is_resolved_for_a_page_that_carries_none(self):
        """A page that does not carry its owners does not ask which owners it could carry."""
        self.send_api_key()
        policy = HiddenOwnerPolicy("alice")
        with patch.object(UserPreferenceViewSet, "embedded_user_policy", policy, create=True):
            response = self.client.get(reverse("user_v2:user_preference_list"), {"page_size": 100})
        assert response.status_code == status.HTTP_200_OK
        assert [row["username"] for row in response.data["results"]] == ["alice", "bob"]
        assert policy.calls == 0

    def test_a_policy_answering_with_a_collection_of_its_own_still_narrows_what_is_carried(self):
        """
        A policy need not narrow the collection it is handed for its answer to hold.

        The owners of the page are taken out of what the policy answers with,
        so a policy that builds its own collection instead of narrowing the
        one it is given narrows the page just the same.
        """
        class OwnCollectionPolicy:
            """A policy that answers with a collection of its own."""

            def scope(self, queryset, subject):  # pylint: disable=unused-argument
                """Return the one owner this policy keeps, built without the given collection."""
                return User.objects.filter(username="bob")

        self.send_api_key()
        with patch.object(UserPreferenceViewSet, "embedded_user_policy", OwnCollectionPolicy(), create=True):
            response = self.client.get(
                reverse("user_v2:user_preference_list"), {"view": "full", "page_size": 100},
            )
        assert response.status_code == status.HTTP_200_OK
        carried = {row["username"]: row["user"] for row in response.data["results"]}
        assert carried["alice"] is None
        assert carried["bob"]["username"] == "bob"

    def test_the_embedded_owner_policy_is_declared_by_the_view_that_embeds(self):
        """
        The view names the policy that governs the owners it carries, rather than inheriting one.

        A view that inherited the default would be left with no policy over
        the entries it embeds, which is how a view that stopped declaring one
        is told apart from a view whose policy happens to keep every owner.
        """
        declared = vars(UserPreferenceViewSet).get("embedded_user_policy")
        assert declared is not None, "UserPreferenceViewSet declares no policy over the owners it carries"
        assert callable(getattr(declared, "scope", None)), (
            "UserPreferenceViewSet declares a policy over the owners it carries that cannot narrow rows"
        )
