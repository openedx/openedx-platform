"""Tests for the version 2 User API views."""

import re
from datetime import UTC, datetime
from math import ceil

from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from edx_rest_framework_extensions.testing import assert_error_envelope
from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.student.tests.factories import AdminFactory, UserFactory
from openedx.core.djangoapps.django_comment_common.models import Role
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.djangoapps.user_api.models import UserPreference
from openedx.core.djangoapps.user_api.preferences.api import get_country_time_zones
from openedx.core.djangoapps.user_api.tests.factories import UserPreferenceFactory
from openedx.core.djangoapps.user_api.v2.filters import UserFilterSet
from openedx.core.djangoapps.user_api.v2.serializers import UserPreferenceRecordSerializer
from openedx.core.djangolib.testing.utils import skip_unless_lms

PASSWORD = "Password1234"
COURSE_KEY = CourseKey.from_string("course-v1:edX+DemoX+Demo")
OTHER_COURSE_KEY = CourseKey.from_string("course-v1:edX+OtherX+Other")

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

    def test_non_admin_caller_is_forbidden(self):
        self.login(self.learner)
        response = self.client.get(self.url)
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz")

    def test_non_admin_cannot_list_another_users_preference_holders(self):
        self.login(self.learner)
        response = self.client.get(self.url, {"preference_key": "pref-lang"})
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz")

    def test_admin_caller_receives_the_directory(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"page_size": 100})
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == PAGINATION_KEYS
        assert self.usernames_in(response) == ["admin-caller", "alice", "bob", "learner-caller"]
        assert response.data["count"] == 4
        assert response.data["num_pages"] == 1
        assert response.data["current_page"] == 1
        assert response.data["start"] == 0

    def test_row_shape_matches_the_declared_fields(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"username": "alice"})
        row = response.data["results"][0]
        assert set(row) == {"id", "url", "email", "name", "username", "preferences"}
        assert row["id"] == self.alice.id
        assert row["email"] == "alice@example.com"
        assert row["name"] == self.alice.profile.name
        assert row["preferences"] == {"pref-lang": "en"}
        assert row["url"].endswith("/api/user/v2/users/alice/")

    def test_second_page_reports_its_position(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"page_size": 2, "page": 2})
        assert response.status_code == status.HTTP_200_OK
        assert self.usernames_in(response) == ["bob", "learner-caller"]
        assert response.data["current_page"] == 2
        assert response.data["num_pages"] == 2
        assert response.data["start"] == 2
        assert response.data["previous"] is not None
        assert response.data["next"] is None

    def test_a_page_beyond_the_last_one_is_not_found(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"page": 9999})
        assert_error_envelope(response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found")

    def test_a_page_number_that_is_not_a_number_is_not_found(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"page": "abc"})
        assert_error_envelope(response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found")

    def test_ordering_can_be_reversed(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"ordering": "-username", "page_size": 100})
        assert self.usernames_in(response) == ["learner-caller", "bob", "alice", "admin-caller"]

    def test_ordering_by_join_date(self):
        self.alice.date_joined = datetime(2020, 1, 1, tzinfo=UTC)
        self.alice.save()
        self.bob.date_joined = datetime(2019, 1, 1, tzinfo=UTC)
        self.bob.save()
        self.login(self.admin)
        response = self.client.get(self.url, {"ordering": "date_joined", "page_size": 100})
        assert self.usernames_in(response)[-2:] == ["bob", "alice"]

    def test_unknown_ordering_is_refused(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"ordering": "password", "page_size": 100})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "ordering": ["Sort by any of: date_joined, username, each optionally prefixed with '-'."],
        }

    def test_one_unknown_field_refuses_the_whole_sort(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"ordering": "username,password", "page_size": 100})
        assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )

    def test_filter_by_email(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"email": "bob@example.com"})
        assert self.usernames_in(response) == ["bob"]

    def test_filter_by_is_active(self):
        self.alice.is_active = False
        self.alice.save()
        self.login(self.admin)
        response = self.client.get(self.url, {"is_active": "false", "page_size": 100})
        assert self.usernames_in(response) == ["alice"]

    def test_filter_by_is_active_keeps_only_the_active_accounts(self):
        self.alice.is_active = False
        self.alice.save()
        self.login(self.admin)
        response = self.client.get(self.url, {"is_active": "true", "page_size": 100})
        assert self.usernames_in(response) == ["admin-caller", "bob", "learner-caller"]

    def test_an_is_active_value_that_names_no_side_is_refused(self):
        """A mistyped value must be refused, not read as "no preference" and answered unfiltered."""
        self.alice.is_active = False
        self.alice.save()
        self.login(self.admin)
        response = self.client.get(self.url, {"is_active": "maybe", "page_size": 100})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {"is_active": ["Enter true or false."]}

    def test_an_empty_is_active_value_narrows_nothing(self):
        self.alice.is_active = False
        self.alice.save()
        self.login(self.admin)
        response = self.client.get(self.url, {"is_active": "", "page_size": 100})
        assert response.status_code == status.HTTP_200_OK
        assert self.usernames_in(response) == ["admin-caller", "alice", "bob", "learner-caller"]

    def test_filter_by_preference_key(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"preference_key": "pref-lang", "page_size": 100})
        assert self.usernames_in(response) == ["alice"]

    def test_filter_by_unknown_preference_key_returns_no_rows(self):
        self.login(self.admin)
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
        self.login(self.admin)
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

    def test_non_admin_cannot_list_a_courses_forum_role_holders(self):
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
        self.login(self.admin)
        response = self.client.get(f"{self.url}?forum_role=Moderator&course_key={example}")
        assert response.status_code == status.HTTP_200_OK
        assert self.usernames_in(response) == ["alice"]

    def test_a_course_key_whose_plus_signs_are_not_encoded_is_refused(self):
        """An unencoded plus sign arrives as a space, which no course key may contain."""
        self.login(self.admin)
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
        self.login(self.admin)
        response = self.client.get(
            self.url, {"forum_role": "Moderator", "course_key": str(COURSE_KEY), "page_size": 100},
        )
        assert self.usernames_in(response) == ["alice"]
        assert response.data["count"] == 1

    def test_forum_role_without_course_key_is_rejected(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"forum_role": "Moderator"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "course_key": ["This parameter is required when forum_role is supplied."],
        }

    def test_course_key_without_forum_role_is_rejected(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"course_key": str(COURSE_KEY)})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "forum_role": ["This parameter is required when course_key is supplied."],
        }

    def test_malformed_course_key_is_rejected(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"forum_role": "Moderator", "course_key": "not a course"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {"course_key": ["Enter a valid course key."]}

    def test_unknown_forum_role_returns_no_rows_and_creates_nothing(self):
        self.login(self.admin)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(
                self.url, {"forum_role": "Inquisitor", "course_key": str(COURSE_KEY)},
            )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["results"] == []
        assert Role.objects.filter(name="Inquisitor").count() == 0
        assert write_queries(captured.captured_queries) == []

    def test_minimal_view_returns_only_identifying_fields(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"username": "alice", "view": "minimal"})
        row = response.data["results"][0]
        assert set(row) == {"id", "username", "email"}
        assert row["username"] == "alice"

    def test_fields_parameter_projects_the_row(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"username": "alice", "fields": "username,email"})
        assert response.data["results"] == [{"username": "alice", "email": "alice@example.com"}]

    def test_fields_parameter_narrows_the_minimal_view(self):
        self.login(self.admin)
        response = self.client.get(
            self.url, {"username": "alice", "view": "minimal", "fields": "username"},
        )
        assert response.data["results"] == [{"username": "alice"}]

    def test_a_field_name_the_row_does_not_carry_is_refused(self):
        self.login(self.admin)
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
        self.login(self.admin)
        response = self.client.get(self.url, {"view": "minimal", "fields": "name"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "fields": ["No such field: name. Select any of: email, id, username."],
        }

    def test_every_unknown_field_name_is_named_at_once(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"fields": "username,e_mail,joined"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"]["fields"][0].startswith("No such field: e_mail, joined.")

    def test_a_field_name_with_surrounding_space_is_accepted(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"username": "alice", "fields": " username , email "})
        assert response.data["results"] == [{"username": "alice", "email": "alice@example.com"}]

    def test_an_unknown_field_does_not_answer_an_anonymous_caller(self):
        response = self.client.get(self.url, {"fields": "user_name"})
        assert_error_envelope(response, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn")

    def test_an_unknown_response_preset_is_refused(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"view": "nonsense"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "view": ["Ask for any of: minimal, or omit this parameter for the default response."],
        }

    def test_the_preset_that_belongs_to_the_preference_list_is_refused_here(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"view": "full"})
        assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )

    def test_an_empty_response_preset_returns_the_default_shape(self):
        """A query string built from an empty variable narrows nothing rather than failing."""
        self.login(self.admin)
        response = self.client.get(self.url, {"username": "alice", "view": ""})
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data["results"][0]) == {
            "id", "url", "email", "name", "username", "preferences",
        }

    def test_an_unknown_preset_does_not_answer_an_anonymous_caller(self):
        response = self.client.get(self.url, {"view": "nonsense"})
        assert_error_envelope(response, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn")

    def test_listing_writes_nothing(self):
        self.login(self.admin)
        with CaptureQueriesContext(connection) as captured:
            self.client.get(self.url, {"page_size": 100})
        assert write_queries(captured.captured_queries) == []

    def test_head_writes_nothing(self):
        self.login(self.admin)
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

    def test_non_admin_caller_is_forbidden(self):
        self.login(self.learner)
        response = self.client.get(self.url_for("alice"))
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz")

    def test_owner_who_is_not_an_admin_is_forbidden(self):
        self.login(self.alice)
        response = self.client.get(self.url_for("alice"))
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz")

    def test_admin_caller_receives_the_entry(self):
        self.login(self.admin)
        response = self.client.get(self.url_for("alice"))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["username"] == "alice"
        assert response.data["email"] == "alice@example.com"
        assert response.data["preferences"] == {"pref-lang": "en"}

    def test_unknown_username_is_not_found(self):
        self.login(self.admin)
        response = self.client.get(self.url_for("nobody"))
        assert_error_envelope(response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found")

    def test_username_containing_a_dot_resolves(self):
        UserFactory.create(username="ada.lovelace", email="ada@example.com", password=PASSWORD)
        self.login(self.admin)
        response = self.client.get(self.url_for("ada.lovelace"))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["username"] == "ada.lovelace"

    def test_minimal_view_returns_only_identifying_fields(self):
        self.login(self.admin)
        response = self.client.get(self.url_for("alice"), {"view": "minimal"})
        assert set(response.data) == {"id", "username", "email"}

    def test_a_field_name_the_entry_does_not_carry_is_refused(self):
        self.login(self.admin)
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
        self.login(self.admin)
        response = self.client.get(self.url_for("alice"), {"fields": "username"})
        assert response.data == {"username": "alice"}

    def test_an_unknown_response_preset_is_refused(self):
        self.login(self.admin)
        response = self.client.get(self.url_for("alice"), {"view": "nonsense"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "view": ["Ask for any of: minimal, or omit this parameter for the default response."],
        }

    def test_retrieval_writes_nothing(self):
        self.login(self.admin)
        with CaptureQueriesContext(connection) as captured:
            self.client.get(self.url_for("alice"))
        assert write_queries(captured.captured_queries) == []

    def test_head_writes_nothing(self):
        self.login(self.admin)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.head(self.url_for("alice"))
        assert response.status_code == status.HTTP_200_OK
        assert write_queries(captured.captured_queries) == []


@skip_unless_lms
class ProfilelessUserTests(UserApiV2TestCase):
    """A user with no profile row is listed with a null name rather than failing."""

    def test_user_without_a_profile_has_a_null_name(self):
        User.objects.create_user("profileless", "profileless@example.com", PASSWORD)
        self.login(self.admin)
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

    def test_non_admin_caller_is_forbidden(self):
        self.login(self.learner)
        response = self.client.get(self.url)
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz")

    def test_owner_who_is_not_an_admin_is_forbidden(self):
        self.login(self.alice)
        response = self.client.get(self.url, {"username": "alice"})
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz")

    def test_admin_caller_receives_every_preference(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"page_size": 100})
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == PAGINATION_KEYS
        assert self.keys_in(response) == [("alice", "pref-lang"), ("bob", "time_zone")]

    def test_row_shape_is_flat_by_default(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"username": "alice"})
        row = response.data["results"][0]
        assert set(row) == {"username", "key", "value", "url", "user"}
        assert row["username"] == "alice"
        assert row["key"] == "pref-lang"
        assert row["value"] == "en"
        assert row["user"] is None
        assert row["url"].endswith("/api/user/v2/user_preferences/alice,pref-lang/")

    def test_full_view_embeds_the_owning_user(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"username": "alice", "view": "full"})
        row = response.data["results"][0]
        assert set(row["user"]) == {"id", "url", "email", "name", "username", "preferences"}
        assert row["user"]["username"] == "alice"
        assert row["user"]["preferences"] == {"pref-lang": "en"}

    def test_minimal_view_drops_the_address_and_owner(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"username": "alice", "view": "minimal"})
        assert response.data["results"] == [{"username": "alice", "key": "pref-lang", "value": "en"}]

    def test_a_field_name_the_row_does_not_carry_is_refused(self):
        self.login(self.admin)
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
        self.login(self.admin)
        response = self.client.get(self.url, {"page": 9999})
        assert_error_envelope(response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found")

    def test_fields_parameter_projects_the_row(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"username": "alice", "fields": "key,value"})
        assert response.data["results"] == [{"key": "pref-lang", "value": "en"}]

    def test_filter_by_key(self):
        UserPreferenceFactory.create(user=self.bob, key="pref-lang", value="fr")
        self.login(self.admin)
        response = self.client.get(self.url, {"key": "pref-lang", "page_size": 100})
        assert self.keys_in(response) == [("alice", "pref-lang"), ("bob", "pref-lang")]

    def test_filter_by_username(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"username": "bob", "page_size": 100})
        assert self.keys_in(response) == [("bob", "time_zone")]

    def test_the_owner_id_the_earlier_list_took_is_refused_rather_than_ignored(self):
        """A caller that ported by changing the version segment is told, not quietly unfiltered."""
        self.login(self.admin)
        response = self.client.get(self.url, {"user": self.alice.id, "page_size": 100})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "user": [f"No such query parameter. Send any of: {PREFERENCE_QUERY_PARAMS}."],
        }

    def test_an_answered_page_announces_no_deprecated_parameter(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"username": "alice"})
        assert response.status_code == status.HTTP_200_OK
        assert "Deprecation" not in response

    def test_ordering_by_key_descending(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"ordering": "-key", "page_size": 100})
        assert self.keys_in(response) == [("bob", "time_zone"), ("alice", "pref-lang")]

    def test_ordering_takes_the_fields_in_the_order_they_are_named(self):
        UserPreferenceFactory.create(user=self.bob, key="pref-lang", value="fr")
        self.login(self.admin)
        response = self.client.get(self.url, {"ordering": "key,-username", "page_size": 100})
        assert self.keys_in(response) == [
            ("bob", "pref-lang"), ("alice", "pref-lang"), ("bob", "time_zone"),
        ]

    def test_unknown_ordering_is_refused(self):
        self.login(self.admin)
        response = self.client.get(self.url, {"ordering": "value", "page_size": 100})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"] == {
            "ordering": ["Sort by any of: key, username, each optionally prefixed with '-'."],
        }

    def test_listing_writes_nothing(self):
        self.login(self.admin)
        with CaptureQueriesContext(connection) as captured:
            self.client.get(self.url, {"page_size": 100})
        assert write_queries(captured.captured_queries) == []

    def test_full_view_writes_nothing(self):
        self.login(self.admin)
        with CaptureQueriesContext(connection) as captured:
            self.client.get(self.url, {"page_size": 100, "view": "full"})
        assert write_queries(captured.captured_queries) == []

    def test_head_writes_nothing(self):
        self.login(self.admin)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.head(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert write_queries(captured.captured_queries) == []

    def test_an_unknown_response_preset_is_refused(self):
        self.login(self.admin)
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

    def test_non_admin_caller_is_forbidden(self):
        self.login(self.learner)
        response = self.client.get(self.url_for("alice", "pref-lang"))
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz")

    def test_owner_who_is_not_an_admin_is_forbidden(self):
        self.login(self.alice)
        response = self.client.get(self.url_for("alice", "pref-lang"))
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz")

    def test_admin_caller_receives_the_preference(self):
        self.login(self.admin)
        response = self.client.get(self.url_for("alice", "pref-lang"))
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == {"username", "key", "value", "url", "user"}
        assert response.data["username"] == "alice"
        assert response.data["key"] == "pref-lang"
        assert response.data["value"] == "en"
        assert response.data["user"] is None

    def test_full_view_embeds_the_owning_user(self):
        self.login(self.admin)
        response = self.client.get(self.url_for("alice", "pref-lang"), {"view": "full"})
        assert response.data["user"]["username"] == "alice"

    def test_a_field_name_the_preference_does_not_carry_is_refused(self):
        self.login(self.admin)
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
        self.login(self.admin)
        response = self.client.get(self.url_for("alice", "nope"))
        assert_error_envelope(response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found")

    def test_key_belonging_to_another_user_is_not_found(self):
        self.login(self.admin)
        response = self.client.get(self.url_for("alice", "time_zone"))
        assert_error_envelope(response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found")

    def test_an_unknown_response_preset_is_refused(self):
        self.login(self.admin)
        response = self.client.get(self.url_for("alice", "pref-lang"), {"view": "nonsense"})
        assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )

    def test_retrieval_writes_nothing(self):
        self.login(self.admin)
        with CaptureQueriesContext(connection) as captured:
            self.client.get(self.url_for("alice", "pref-lang"))
        assert write_queries(captured.captured_queries) == []

    def test_head_writes_nothing(self):
        self.login(self.admin)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.head(self.url_for("alice", "pref-lang"))
        assert response.status_code == status.HTTP_200_OK
        assert write_queries(captured.captured_queries) == []


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
        self.login(self.admin)
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
        self.login(self.admin)
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

    #: Public, standardized, and served by another API: what its refused method
    #: is called must not change because this one names its own.
    OTHER_API_URL = "/api/enrollment/v2/courses/course-v1:edX+DemoX+Demo/"

    def setUp(self):
        super().setUp()
        self.public_url = reverse("user_v2:time_zone_list")

    def test_a_refused_method_is_named_for_an_anonymous_caller(self):
        response = self.client.post(self.public_url, {}, format="json")
        envelope = assert_error_envelope(
            response,
            expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
            expected_type_slug="method-not-allowed",
        )
        assert envelope["title"] == "Method Not Allowed"
        assert envelope["detail"] == 'Method "POST" not allowed.'
        assert response["Content-Type"] == "application/json"

    def test_an_accept_header_this_api_cannot_answer_is_named(self):
        response = self.client.get(self.public_url, HTTP_ACCEPT="application/xml")
        envelope = assert_error_envelope(
            response,
            expected_status=status.HTTP_406_NOT_ACCEPTABLE,
            expected_type_slug="not-acceptable",
        )
        assert envelope["title"] == "Not Acceptable"
        assert envelope["detail"] == "Could not satisfy the request Accept header."
        assert response["Content-Type"] == "application/json"

    def test_a_refused_method_is_named_on_the_gated_collections(self):
        self.login(self.admin)
        for url_name in ("user_v2:user_list", "user_v2:user_preference_list"):
            response = self.client.delete(reverse(url_name))
            envelope = assert_error_envelope(
                response,
                expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
                expected_type_slug="method-not-allowed",
            )
            assert envelope["detail"] == 'Method "DELETE" not allowed.'

    def test_a_refused_method_is_named_on_the_gated_members(self):
        self.login(self.admin)
        response = self.client.put(reverse("user_v2:user_detail", args=[self.alice.username]))
        envelope = assert_error_envelope(
            response,
            expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
            expected_type_slug="method-not-allowed",
        )
        assert envelope["detail"] == 'Method "PUT" not allowed.'

    def test_another_api_is_classified_exactly_as_the_shared_catalog_classifies_it(self):
        """The catalog names no refused method, and nothing here changes that for anyone else."""
        response = self.client.post(self.OTHER_API_URL, {}, format="json")
        envelope = assert_error_envelope(
            response,
            expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
            expected_type_slug="internal",
        )
        assert envelope["title"] == "Internal Server Error"


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
        self.login(self.admin)
        response = self.client.get(self.user_list, {"lms_user_id": self.alice.id, "page_size": 100})
        self.assert_refused(response, "lms_user_id", USER_QUERY_PARAMS)

    def test_a_mistyped_filter_refuses_the_page_instead_of_returning_every_row(self):
        self.login(self.admin)
        response = self.client.get(self.user_list, {"usernme": "alice", "page_size": 100})
        self.assert_refused(response, "usernme", USER_QUERY_PARAMS)

    def test_every_unrecognised_parameter_is_named_at_once(self):
        self.login(self.admin)
        response = self.client.get(self.user_list, {"sort": "username", "limit": "10"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert sorted(envelope["errors"]) == ["limit", "sort"]

    def test_a_directory_entry_refuses_a_parameter_it_does_not_read(self):
        self.login(self.admin)
        response = self.client.get(self.user_detail, {"expand": "profile"})
        self.assert_refused(response, "expand", USER_QUERY_PARAMS)

    def test_the_preference_list_refuses_a_parameter_it_does_not_read(self):
        self.login(self.admin)
        response = self.client.get(self.preference_list, {"value": "en", "page_size": 100})
        self.assert_refused(response, "value", PREFERENCE_QUERY_PARAMS)

    def test_a_stored_preference_refuses_a_parameter_it_does_not_read(self):
        self.login(self.admin)
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

    def test_an_unknown_parameter_does_not_answer_a_non_admin_caller(self):
        self.login(self.learner)
        response = self.client.get(self.preference_list, {"lms_user_id": self.alice.id})
        assert_error_envelope(
            response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
        )

    def test_the_directory_answers_every_parameter_it_reads(self):
        self.login(self.admin)
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
        self.login(self.admin)
        self.assert_each_is_answered(self.user_detail, [
            {"view": "minimal"}, {"fields": "username"}, {"format": "json"}, {"ordering": "username"},
        ])

    def test_the_preference_list_answers_every_parameter_it_reads(self):
        self.login(self.admin)
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
        self.login(self.admin)
        self.assert_each_is_answered(self.preference_detail, [
            {"view": "full"}, {"fields": "key"}, {"format": "json"},
        ])

    def test_the_time_zone_list_answers_every_parameter_it_reads(self):
        self.assert_each_is_answered(self.time_zone_list, [
            {"country_code": "FR"}, {"page": 1}, {"page_size": 5}, {"format": "json"},
        ])

    def test_a_page_of_the_directory_is_still_narrowed_by_the_filters_it_takes(self):
        """The refusal guards the parameters, not the filtering they ask for."""
        self.login(self.admin)
        response = self.client.get(self.user_list, {"username": "alice", "page_size": 100})
        assert self.usernames_in(response) == ["alice"]


@skip_unless_lms
class TokenCallerTests(UserApiV2TestCase):
    """The staff-gated endpoints answer a bearer of a staff token and refuse everyone else."""

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

    def test_a_staff_token_reaches_the_directory(self):
        response = self.client.get(
            self.addresses["user_list"], {"page_size": 100}, **self.token_headers(self.admin),
        )
        assert response.status_code == status.HTTP_200_OK
        assert self.usernames_in(response) == ["admin-caller", "alice", "bob", "learner-caller"]
        assert response.data["count"] == 4

    def test_a_staff_token_reaches_every_staff_gated_address(self):
        headers = self.token_headers(self.admin)
        detail = self.client.get(self.addresses["user_detail"], **headers)
        assert detail.status_code == status.HTTP_200_OK
        assert detail.data["username"] == "alice"

        preferences = self.client.get(
            self.addresses["preference_list"], {"username": "alice"}, **headers,
        )
        assert preferences.status_code == status.HTTP_200_OK
        assert [(row["username"], row["key"]) for row in preferences.data["results"]] == [
            ("alice", "pref-lang"),
        ]

        preference = self.client.get(self.addresses["preference_detail"], **headers)
        assert preference.status_code == status.HTTP_200_OK
        assert preference.data["value"] == "en"

    def test_the_token_and_not_a_session_identifies_the_caller(self):
        token = create_jwt_for_user(self.admin)
        response = self.client.get(
            self.addresses["user_list"], {"username": "alice"}, HTTP_AUTHORIZATION=f"JWT {token}",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.wsgi_request.user == self.admin
        assert response.wsgi_request.auth == token
        assert "sessionid" not in response.wsgi_request.COOKIES

    def test_a_learners_token_is_forbidden_on_every_staff_gated_address(self):
        headers = self.token_headers(self.learner)
        for name, address in self.addresses.items():
            with self.subTest(address=name):
                response = self.client.get(address, **headers)
                assert_error_envelope(
                    response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
                )

    def test_the_owner_of_the_record_is_still_not_staff(self):
        response = self.client.get(
            self.addresses["preference_detail"], **self.token_headers(self.alice),
        )
        assert_error_envelope(
            response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
        )

    def test_a_token_that_does_not_decode_is_unauthenticated(self):
        response = self.client.get(
            self.addresses["user_list"], HTTP_AUTHORIZATION="JWT not.a.token",
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
        self.login(self.admin)

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
    A staff account whose user record has been deactivated is refused by both halves of
    the default authentication pair: the session half stops accepting it, and the active
    account these views require refuses a token issued before the account was deactivated.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse("user_v2:user_list")
        self.staff = AdminFactory.create(username="deactivated-admin", password=PASSWORD)
        self.staff_gated = {
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

    def test_a_session_held_by_a_deactivated_staff_account_is_refused(self):
        self.login(self.staff)
        assert self.client.get(self.url).status_code == status.HTTP_200_OK
        self.deactivate(self.staff)
        response = self.client.get(self.url)
        assert_error_envelope(
            response, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug="authn",
        )

    def test_a_token_held_by_a_deactivated_staff_account_is_refused(self):
        headers = {"HTTP_AUTHORIZATION": f"JWT {create_jwt_for_user(self.staff)}"}
        assert self.client.get(self.url, **headers).status_code == status.HTTP_200_OK
        self.deactivate(self.staff)
        response = self.client.get(self.url, {"page_size": 100}, **headers)
        assert_error_envelope(
            response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
        )

    def test_a_deactivated_staff_token_is_refused_at_every_staff_gated_address(self):
        self.deactivate(self.staff)
        headers = {"HTTP_AUTHORIZATION": f"JWT {create_jwt_for_user(self.staff)}"}
        for name, address in self.staff_gated.items():
            with self.subTest(address=name):
                response = self.client.get(address, **headers)
                assert_error_envelope(
                    response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
                )

    def test_a_deactivated_staff_session_is_refused_at_every_staff_gated_address(self):
        self.login(self.staff)
        self.deactivate(self.staff)
        for name, address in self.staff_gated.items():
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

    def test_an_active_staff_token_still_reaches_the_directory(self):
        response = self.client.get(
            self.url, {"page_size": 100}, HTTP_AUTHORIZATION=f"JWT {create_jwt_for_user(self.staff)}",
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
