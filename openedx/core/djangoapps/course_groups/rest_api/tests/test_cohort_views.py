"""
Tests for the v2 cohorts REST API.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import ddt
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import NoReverseMatch, Resolver404, resolve, reverse
from edx_rest_framework_extensions.testing import assert_error_envelope
from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from rest_framework.test import APIClient

from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.course_groups import cohorts
from openedx.core.djangoapps.course_groups.models import (
    CourseCohort,
    CourseCohortsSettings,
    CourseUserGroup,
    CourseUserGroupPartitionGroup,
)
from openedx.core.djangoapps.course_groups.rest_api.cohort_permissions import (
    CanReadCohortsAsDiscussionModerator,
    IsCohortCourseAdmin,
)
from openedx.core.djangoapps.course_groups.rest_api.cohort_views import (
    DUPLICATE_NAME_MESSAGE,
    LAST_RANDOM_COHORT_MESSAGE,
)
from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_MODERATOR,
    Role,
)
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, ToyCourseFactory

User = get_user_model()

PAGINATION_FIELDS = {"count", "num_pages", "current_page", "start", "next", "previous", "results"}
MISSING_COURSE_KEY = CourseKey.from_string("course-v1:NoSuch+Course+Run")
DISCUSSION_ROLES = (FORUM_ROLE_MODERATOR, FORUM_ROLE_COMMUNITY_TA, FORUM_ROLE_ADMINISTRATOR)
MODERATOR_READABLE = {"list cohorts", "read cohort"}


def create_cohort(course_key, name, assignment_type=CourseCohort.MANUAL):
    """
    Create a cohort without CohortFactory.

    CohortFactory names cohorts from a sequence shared by every test in the run,
    and some cohort tests expect those generated names to sort in creation order.
    """
    return CourseCohort.create(
        cohort_name=name, course_id=course_key, assignment_type=assignment_type,
    ).course_user_group


def create_legacy_cohort(course_key, name):
    """
    Create a cohort as courses from before ``CourseCohort`` existed hold them.

    Such a row has no ``CourseCohort`` until ``migrate_cohort_settings`` backfills
    one, which the v2 read paths never call.
    """
    return CourseUserGroup.objects.create(course_id=course_key, name=name, group_type=CourseUserGroup.COHORT)


def grant_forum_role(user, course_key, role_name):
    """Give ``user`` the named discussion role in the course."""
    role, _ = Role.objects.get_or_create(name=role_name, course_id=course_key)
    role.users.add(user)


def cohort_names(course_key):
    """Return the names of the course's cohorts, sorted."""
    return sorted(
        CourseUserGroup.objects.filter(course_id=course_key, group_type=CourseUserGroup.COHORT)
        .values_list("name", flat=True)
    )


@skip_unless_lms
class CohortV2TestCase(SharedModuleStoreTestCase):
    """Shared fixtures for the v2 cohort endpoints."""

    password = "password"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = ToyCourseFactory.create()
        cls.course_key = cls.course.id
        cls.unstarted_course = CourseFactory.create(start=datetime.now(ZoneInfo("UTC")) + timedelta(days=30))

    def setUp(self):
        super().setUp()
        self.staff_user = UserFactory.create(password=self.password)
        CourseStaffRole(self.course_key).add_users(self.staff_user)
        self.outsider = UserFactory.create(password=self.password)
        self.cohort = create_cohort(self.course_key, "Alpha")
        self.member = UserFactory.create()
        cohorts.add_user_to_cohort(self.cohort, self.member.username)
        self.learner = UserFactory.create()
        self.client = APIClient()
        self.login(self.staff_user)

    def login(self, user):
        """Log the test client in as ``user``."""
        self.client.logout()
        self.client.login(username=user.username, password=self.password)

    def make_global_staff(self):
        """Create and log in a global staff user who holds no course role."""
        user = UserFactory.create(password=self.password, is_staff=True)
        self.login(user)
        return user

    def list_url(self, course_key=None):
        """Return the cohort collection address."""
        return reverse("api_cohorts:cohort_list", kwargs={"course_key": str(course_key or self.course_key)})

    def detail_url(self, cohort_id=None):
        """Return the address of one cohort."""
        return reverse("api_cohorts:cohort_detail", kwargs={
            "course_key": str(self.course_key),
            "cohort_id": cohort_id or self.cohort.id,
        })

    def members_url(self, cohort_id=None):
        """Return the member collection address of a cohort."""
        return reverse("api_cohorts:cohort_member_list", kwargs={
            "course_key": str(self.course_key),
            "cohort_id": cohort_id or self.cohort.id,
        })

    def member_url(self, username):
        """Return the address of one member of the fixture cohort."""
        return reverse("api_cohorts:cohort_member_detail", kwargs={
            "course_key": str(self.course_key),
            "cohort_id": self.cohort.id,
            "username": username,
        })

    def settings_url(self, course_key=None):
        """Return the cohort settings address of a course."""
        return reverse("api_cohorts:course_cohort_settings",
                       kwargs={"course_key": str(course_key or self.course_key)})

    def endpoint_calls(self):
        """Return one request per endpoint and method, keyed by a readable label."""
        return {
            "list cohorts": lambda: self.client.get(self.list_url()),
            "read cohort": lambda: self.client.get(self.detail_url()),
            "create cohort": lambda: self.client.post(
                self.list_url(), {"name": "New", "assignment_type": "manual"}, format="json"),
            "update cohort": lambda: self.client.patch(self.detail_url(), {"name": "Renamed"}, format="json"),
            "list members": lambda: self.client.get(self.members_url()),
            "add members": lambda: self.client.post(
                self.members_url(), {"users": [self.learner.username]}, format="json"),
            "remove member": lambda: self.client.delete(self.member_url(self.member.username)),
            "read settings": lambda: self.client.get(self.settings_url()),
            "update settings": lambda: self.client.put(self.settings_url(), {"is_cohorted": True}, format="json"),
        }

    def assert_nothing_written(self, settings_before):
        """Assert that no request in a denied run changed cohorts, membership or settings."""
        assert cohort_names(self.course_key) == ["Alpha"]
        assert list(self.cohort.users.values_list("username", flat=True)) == [self.member.username]
        assert list(
            CourseCohortsSettings.objects.filter(course_id=self.course_key).values_list("is_cohorted", flat=True)
        ) == settings_before

    def current_settings(self):
        """Return the stored is_cohorted values for the course, to compare after a run."""
        return list(
            CourseCohortsSettings.objects.filter(course_id=self.course_key).values_list("is_cohorted", flat=True)
        )


class TestCohortV2Routing(CohortV2TestCase):
    """Each route resolves exactly one address."""

    def test_reverse_literals(self):
        """The published addresses, with the course key captured as ``course_key``."""
        key = str(self.course_key)
        cohort_id = self.cohort.id
        assert self.list_url() == f"/api/cohorts/v2/courses/{key}/cohorts/"
        assert self.detail_url() == f"/api/cohorts/v2/courses/{key}/cohorts/{cohort_id}/"
        assert self.members_url() == f"/api/cohorts/v2/courses/{key}/cohorts/{cohort_id}/users/"
        assert self.member_url("bob") == f"/api/cohorts/v2/courses/{key}/cohorts/{cohort_id}/users/bob/"
        assert self.settings_url() == f"/api/cohorts/v2/courses/{key}/cohort_settings/"
        assert resolve(self.list_url()).kwargs == {"course_key": key}

    def test_slashless_list_address_is_not_the_list(self):
        """The trailing slash is required, not optional."""
        try:
            match = resolve(self.list_url().rstrip("/"))
        except Resolver404:
            return
        assert match.url_name != "cohort_list"

    def test_singular_users_segment_does_not_resolve(self):
        """
        v1 spells the members path 'users?', so /user resolves as well as
        /users. The v2 path must not.
        """
        with pytest.raises(Resolver404):
            resolve(self.members_url().replace("/users/", "/user/"))

    def test_username_segment_does_not_span_path_separators(self):
        """
        v1 captures the username with '.+', so /users/bob/extra yields the
        username 'bob/extra'. The v2 pattern must reject it.
        """
        with pytest.raises(NoReverseMatch):
            reverse("api_cohorts:cohort_member_detail", kwargs={
                "course_key": str(self.course_key),
                "cohort_id": self.cohort.id,
                "username": "bob/extra",
            })

    def test_list_and_detail_are_separate_routes(self):
        """A single pattern with an optional id must not serve both."""
        assert resolve(self.list_url()).url_name == "cohort_list"
        assert resolve(self.detail_url()).url_name == "cohort_detail"


@ddt.ddt
class TestCohortV2Access(CohortV2TestCase):
    """Authentication and authorization on every endpoint and method."""

    def test_anonymous_is_unauthenticated_everywhere(self):
        """Every endpoint answers 401 to an anonymous caller and writes nothing."""
        self.client.logout()
        settings_before = self.current_settings()
        for label, call in self.endpoint_calls().items():
            with self.subTest(endpoint=label):
                assert_error_envelope(call(), expected_status=401, expected_type_slug="authn")
        self.assert_nothing_written(settings_before)

    def test_unprivileged_user_is_forbidden_everywhere(self):
        """A logged-in user with no course role gets 403 on every endpoint and writes nothing."""
        self.login(self.outsider)
        settings_before = self.current_settings()
        for label, call in self.endpoint_calls().items():
            with self.subTest(endpoint=label):
                assert_error_envelope(call(), expected_status=403, expected_type_slug="authz")
        self.assert_nothing_written(settings_before)

    @ddt.data(*DISCUSSION_ROLES)
    def test_discussion_role_reads_cohorts_only(self, role_name):
        """
        Discussion staff may list and read cohorts, as in v1, and nothing else.

        The member list and the settings carry learner PII and need course
        staff access, which v1 also required for them.
        """
        moderator = UserFactory.create(password=self.password)
        grant_forum_role(moderator, self.course_key, role_name)
        self.login(moderator)
        settings_before = self.current_settings()
        for label, call in self.endpoint_calls().items():
            with self.subTest(endpoint=label):
                response = call()
                if label in MODERATOR_READABLE:
                    assert response.status_code == status.HTTP_200_OK
                else:
                    assert_error_envelope(response, expected_status=403, expected_type_slug="authz")
        self.assert_nothing_written(settings_before)

    def test_discussion_role_reads_the_cohort_contents(self):
        """The reads a moderator is allowed return the course's cohorts."""
        moderator = UserFactory.create(password=self.password)
        grant_forum_role(moderator, self.course_key, FORUM_ROLE_MODERATOR)
        self.login(moderator)
        listing = self.client.get(self.list_url())
        assert [row["name"] for row in listing.data["results"]] == ["Alpha"]
        detail = self.client.get(self.detail_url())
        assert (detail.data["id"], detail.data["name"]) == (self.cohort.id, "Alpha")

    @override_settings(DISABLE_START_DATES=False)
    def test_discussion_role_needs_a_loadable_course(self):
        """A moderator is refused in a course they cannot load yet; course staff are not."""
        moderator = UserFactory.create(password=self.password)
        grant_forum_role(moderator, self.course_key, FORUM_ROLE_MODERATOR)
        grant_forum_role(moderator, self.unstarted_course.id, FORUM_ROLE_MODERATOR)
        self.login(moderator)
        assert self.client.get(self.list_url()).status_code == status.HTTP_200_OK
        assert_error_envelope(
            self.client.get(self.list_url(self.unstarted_course.id)),
            expected_status=403, expected_type_slug="authz",
        )
        self.make_global_staff()
        assert self.client.get(self.list_url(self.unstarted_course.id)).status_code == status.HTTP_200_OK

    def test_discussion_role_in_a_missing_course_is_forbidden_not_not_found(self):
        """A moderator role row for a course that does not exist does not reveal that it is missing."""
        moderator = UserFactory.create(password=self.password)
        grant_forum_role(moderator, MISSING_COURSE_KEY, FORUM_ROLE_MODERATOR)
        self.login(moderator)
        assert_error_envelope(
            self.client.get(self.list_url(MISSING_COURSE_KEY)),
            expected_status=403, expected_type_slug="authz",
        )

    def test_discussion_role_in_another_course_is_forbidden(self):
        """A moderator of one course has no access to another course's cohorts."""
        moderator = UserFactory.create(password=self.password)
        grant_forum_role(moderator, self.unstarted_course.id, FORUM_ROLE_MODERATOR)
        self.login(moderator)
        assert_error_envelope(self.client.get(self.list_url()), expected_status=403, expected_type_slug="authz")

    def test_staff_of_another_course_is_forbidden(self):
        """Course staff access does not carry over to another course."""
        other_staff = UserFactory.create(password=self.password)
        CourseStaffRole(self.unstarted_course.id).add_users(other_staff)
        self.login(other_staff)
        assert_error_envelope(
            self.client.get(self.members_url()), expected_status=403, expected_type_slug="authz",
        )

    def test_instructor_may_manage_members_and_settings(self):
        """Course instructors have the same access as course staff."""
        instructor = UserFactory.create(password=self.password)
        CourseInstructorRole(self.course_key).add_users(instructor)
        self.login(instructor)
        assert self.client.get(self.members_url()).status_code == status.HTTP_200_OK
        response = self.client.put(self.settings_url(), {"is_cohorted": True}, format="json")
        assert response.status_code == status.HTTP_200_OK

    def test_global_staff_may_manage_members(self):
        """Global staff need no course role."""
        self.make_global_staff()
        assert self.client.get(self.members_url()).status_code == status.HTTP_200_OK

    def test_superuser_without_staff_flag_may_manage_members(self):
        """A superuser is admitted even when the staff flag is off."""
        superuser = UserFactory.create(password=self.password, is_superuser=True)
        User.objects.filter(pk=superuser.pk).update(is_staff=False)
        superuser.refresh_from_db()
        assert (superuser.is_superuser, superuser.is_staff) == (True, False)
        self.login(superuser)
        assert self.client.get(self.members_url()).status_code == status.HTTP_200_OK

    def test_bearer_authentication_is_not_accepted(self):
        """The deprecated Bearer class is absent from the v2 views."""
        from openedx.core.djangoapps.course_groups.rest_api.cohort_views import CohortViewSet
        declared = [cls.__name__ for cls in CohortViewSet.authentication_classes]
        assert declared == ["JwtAuthentication", "SessionAuthenticationAllowInactiveUser"]

    def test_a_path_segment_that_is_not_a_course_key_is_not_found(self):
        """
        A segment without the shape of a course key matches no route, so it is
        the site's 404 rather than a server error. A key with the shape that
        still does not parse is refused by the views; see
        TestCohortV2PermissionClasses.
        """
        response = self.client.get("/api/cohorts/v2/courses/not-a-key/cohorts/")
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestCohortV2PermissionClasses(CohortV2TestCase):
    """Branches of the permission classes that the URL routing keeps out of reach."""

    def check(self, permission, user, course_key="not-a-key", method="GET"):
        """Evaluate ``permission`` for a stub request and view."""
        request = SimpleNamespace(user=user, method=method)
        view = SimpleNamespace(kwargs={"course_key": course_key})
        return permission.has_permission(request, view)

    def test_unparseable_course_key_is_denied(self):
        """A key that does not parse is refused rather than raising."""
        grant_forum_role(self.outsider, self.course_key, FORUM_ROLE_MODERATOR)
        assert self.check(IsCohortCourseAdmin(), self.outsider) is False
        assert self.check(CanReadCohortsAsDiscussionModerator(), self.outsider) is False

    def test_anonymous_user_is_denied(self):
        """Both classes refuse an anonymous user on their own."""
        key = str(self.course_key)
        assert self.check(IsCohortCourseAdmin(), AnonymousUser(), key) is False
        assert self.check(CanReadCohortsAsDiscussionModerator(), AnonymousUser(), key) is False

    def test_moderator_read_methods(self):
        """GET and HEAD are reads; OPTIONS and every write are not."""
        grant_forum_role(self.outsider, self.course_key, FORUM_ROLE_MODERATOR)
        key = str(self.course_key)
        allowed = {
            method: self.check(CanReadCohortsAsDiscussionModerator(), self.outsider, key, method)
            for method in ("GET", "HEAD", "OPTIONS", "POST", "PATCH", "PUT", "DELETE")
        }
        assert allowed == {
            "GET": True, "HEAD": True, "OPTIONS": False,
            "POST": False, "PATCH": False, "PUT": False, "DELETE": False,
        }


class TestCohortV2ReadsDoNotWrite(CohortV2TestCase):
    """Read paths do not write cohort state."""

    def test_reads_issue_no_cohort_writes(self):
        """
        No read path writes to the cohort tables.

        The v1 list endpoint reaches migrate_cohort_settings() and creates a
        CourseCohortsSettings row plus CourseCohort rows; the v1 settings read
        reaches the same migration.
        """
        CourseCohortsSettings.objects.filter(course_id=self.course_key).delete()
        reads = (self.list_url(), self.detail_url(), self.members_url(), self.settings_url())
        for url in reads:
            with self.subTest(url=url):
                with CaptureQueriesContext(connection) as queries:
                    for method in (self.client.get, self.client.head):
                        assert method(url).status_code == status.HTTP_200_OK
                writes = [
                    query["sql"] for query in queries.captured_queries
                    if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
                    and "course_groups_" in query["sql"]
                ]
                assert writes == []
        assert CourseCohortsSettings.objects.filter(course_id=self.course_key).count() == 0

    def test_settings_read_reports_the_authored_value_without_a_row(self):
        """With no stored settings the authored value is returned and no row is created."""
        CourseCohortsSettings.objects.filter(course_id=self.course_key).delete()
        response = self.client.get(self.settings_url())
        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] is None
        assert response.data["is_cohorted"] is False
        assert CourseCohortsSettings.objects.filter(course_id=self.course_key).count() == 0


class TestCohortV2List(CohortV2TestCase):
    """The cohort list: envelope, ordering and query count."""

    def setUp(self):
        super().setUp()
        # Created after Alpha so that id order differs from name order.
        self.zulu = create_cohort(self.course_key, "Zulu")
        self.mike = create_cohort(self.course_key, "Mike")

    def names(self, **params):
        """Return the cohort names of the first page, in response order."""
        response = self.client.get(self.list_url(), params)
        assert response.status_code == status.HTTP_200_OK
        return [row["name"] for row in response.data["results"]]

    def test_envelope(self):
        """
        The list is the seven-field pagination envelope.

        The v1 list endpoint paginates and then discards the paginator,
        returning a bare array with no count and no next link.
        """
        response = self.client.get(self.list_url())
        assert set(response.data) == PAGINATION_FIELDS
        assert response.data["count"] == 3
        assert response.data["results"][0] == {
            "id": self.cohort.id,
            "name": "Alpha",
            "assignment_type": "manual",
            "user_count": 0,
            "user_partition_id": None,
            "group_id": None,
        }

    def test_default_ordering_is_by_name(self):
        """With no ordering parameter the list is sorted by name."""
        assert self.names() == ["Alpha", "Mike", "Zulu"]

    def test_ordering_by_name_both_directions(self):
        """Ordering by name ascending and descending."""
        assert self.names(ordering="name") == ["Alpha", "Mike", "Zulu"]
        assert self.names(ordering="-name") == ["Zulu", "Mike", "Alpha"]

    def test_ordering_by_id_both_directions(self):
        """Ordering by id sorts on the id, not the name."""
        assert self.names(ordering="id") == ["Alpha", "Zulu", "Mike"]
        assert self.names(ordering="-id") == ["Mike", "Zulu", "Alpha"]

    def test_cohort_without_an_assignment_row_is_manual(self):
        """
        A cohort from before CourseCohort existed is listed and read as manual.

        v1 reaches such a row through get_course_cohorts, which backfills the
        missing CourseCohort as manual on the way; the v2 reads write nothing
        and report the same type.
        """
        legacy = create_legacy_cohort(self.course_key, "Legacy")
        listed = self.client.get(self.list_url())
        assert listed.status_code == status.HTTP_200_OK
        assert [(row["name"], row["assignment_type"]) for row in listed.data["results"]] == [
            ("Alpha", "manual"), ("Legacy", "manual"), ("Mike", "manual"), ("Zulu", "manual"),
        ]
        read = self.client.get(self.detail_url(legacy.id))
        assert read.status_code == status.HTTP_200_OK
        assert read.data["assignment_type"] == "manual"
        assert not CourseCohort.objects.filter(course_user_group=legacy).exists()

    def test_unsupported_ordering_field_is_rejected(self):
        """An ordering field outside the allow-list is a 400."""
        response = self.client.get(self.list_url(), {"ordering": "secret"})
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"ordering": ["Unsupported ordering field. Valid fields: name, id."]}

    def test_paging(self):
        """Two pages of two partition the three cohorts in name order."""
        first = self.client.get(self.list_url(), {"page_size": 2})
        second = self.client.get(self.list_url(), {"page_size": 2, "page": 2})
        assert [row["name"] for row in first.data["results"]] == ["Alpha", "Mike"]
        assert [row["name"] for row in second.data["results"]] == ["Zulu"]
        assert (first.data["num_pages"], second.data["current_page"]) == (2, 2)

    def test_unknown_course_is_a_not_found_envelope(self):
        """Listing the cohorts of a course that does not exist is a 404 envelope."""
        self.make_global_staff()
        assert_error_envelope(
            self.client.get(self.list_url(MISSING_COURSE_KEY)), expected_status=404, expected_type_slug="not-found",
        )

    def test_query_count_per_cohort(self):
        """
        Each further cohort on the page costs two queries: its content group and its learner count.

        The assignment type is read through the joined CourseCohort row, so it
        adds nothing per cohort.
        """
        CourseUserGroup.objects.filter(pk__in=[self.zulu.pk, self.mike.pk]).delete()
        self.client.get(self.list_url())
        with CaptureQueriesContext(connection) as one_cohort:
            assert self.client.get(self.list_url()).data["count"] == 1
        create_cohort(self.course_key, "Bravo")
        create_cohort(self.course_key, "Charlie")
        with CaptureQueriesContext(connection) as three_cohorts:
            assert self.client.get(self.list_url()).data["count"] == 3
        assert len(three_cohorts.captured_queries) - len(one_cohort.captured_queries) == 4


class TestCohortV2Create(CohortV2TestCase):
    """Creating a cohort."""

    def test_create(self):
        """A valid payload creates the cohort and returns it."""
        response = self.client.post(self.list_url(), {"name": "Beta", "assignment_type": "random"}, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        created = CourseUserGroup.objects.get(course_id=self.course_key, name="Beta")
        assert response.data == {
            "id": created.id,
            "name": "Beta",
            "assignment_type": "random",
            "user_count": 0,
            "user_partition_id": None,
            "group_id": None,
        }
        assert created.cohort.assignment_type == CourseCohort.RANDOM

    def test_create_with_content_group(self):
        """A cohort created with a content group is linked to it."""
        response = self.client.post(
            self.list_url(),
            {"name": "Gamma", "assignment_type": "manual", "user_partition_id": 50, "group_id": 2},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert (response.data["user_partition_id"], response.data["group_id"]) == (50, 2)
        link = CourseUserGroupPartitionGroup.objects.get(course_user_group_id=response.data["id"])
        assert (link.partition_id, link.group_id) == (50, 2)

    def test_group_id_without_partition_creates_nothing(self):
        """A content group without its configuration is a 400 and leaves no cohort behind."""
        response = self.client.post(
            self.list_url(), {"name": "Beta", "assignment_type": "manual", "group_id": 1}, format="json",
        )
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"user_partition_id": ["Required when group_id is supplied."]}
        assert cohort_names(self.course_key) == ["Alpha"]

    def test_duplicate_name_is_rejected(self):
        """A name already used in the course is a 400 with a fixed message."""
        response = self.client.post(self.list_url(), {"name": "Alpha", "assignment_type": "manual"}, format="json")
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"name": [DUPLICATE_NAME_MESSAGE]}
        assert cohort_names(self.course_key) == ["Alpha"]

    def test_unknown_course_is_a_not_found_envelope(self):
        """Creating a cohort in a course that does not exist is a 404, not a server error."""
        self.make_global_staff()
        response = self.client.post(
            self.list_url(MISSING_COURSE_KEY), {"name": "Beta", "assignment_type": "manual"}, format="json",
        )
        assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
        assert cohort_names(MISSING_COURSE_KEY) == []


class TestCohortV2Update(CohortV2TestCase):
    """Updating a cohort."""

    def test_rename(self):
        """A new name is stored and returned."""
        response = self.client.patch(self.detail_url(), {"name": "Renamed"}, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Renamed"
        self.cohort.refresh_from_db()
        assert self.cohort.name == "Renamed"

    def test_refused_assignment_type_leaves_the_rename_unsaved(self):
        """
        Changing the only random cohort to manual is refused, and the rename in
        the same request is not kept.
        """
        random_cohort = create_cohort(self.course_key, "Random", CourseCohort.RANDOM)
        response = self.client.patch(
            self.detail_url(random_cohort.id), {"name": "Renamed", "assignment_type": "manual"}, format="json",
        )
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"assignment_type": [LAST_RANDOM_COHORT_MESSAGE]}
        random_cohort.refresh_from_db()
        assert random_cohort.name == "Random"
        assert CourseCohort.objects.get(course_user_group=random_cohort).assignment_type == CourseCohort.RANDOM

    def test_assignment_type_of_a_cohort_without_an_assignment_row(self):
        """
        Setting the assignment type of a cohort from before CourseCohort existed
        stores the row the type lives on; a rename alone does not.
        """
        legacy = create_legacy_cohort(self.course_key, "Legacy")
        renamed = self.client.patch(self.detail_url(legacy.id), {"name": "Still legacy"}, format="json")
        assert renamed.status_code == status.HTTP_200_OK
        assert renamed.data["assignment_type"] == "manual"
        assert not CourseCohort.objects.filter(course_user_group=legacy).exists()

        response = self.client.patch(self.detail_url(legacy.id), {"assignment_type": "random"}, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["assignment_type"] == "random"
        assert CourseCohort.objects.get(course_user_group=legacy).assignment_type == CourseCohort.RANDOM

    def test_duplicate_name_is_rejected(self):
        """Renaming onto another cohort's name is a 400 and changes nothing."""
        create_cohort(self.course_key, "Taken")
        response = self.client.patch(self.detail_url(), {"name": "Taken"}, format="json")
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"name": [DUPLICATE_NAME_MESSAGE]}
        self.cohort.refresh_from_db()
        assert self.cohort.name == "Alpha"

    def test_empty_update_is_rejected(self):
        """A payload naming no field is a 400."""
        response = self.client.patch(self.detail_url(), {}, format="json")
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {
            "non_field_errors": ["Supply at least one of name, assignment_type or group_id."]
        }

    def test_link_relink_and_unlink_content_group(self):
        """A content group can be linked, replaced, kept as is and unlinked."""
        def patch(payload):
            """PATCH the fixture cohort and return the content group it reports."""
            response = self.client.patch(self.detail_url(), payload, format="json")
            assert response.status_code == status.HTTP_200_OK
            return response.data["user_partition_id"], response.data["group_id"]

        def stored():
            """Return the stored content-group link rows of the fixture cohort."""
            return list(
                CourseUserGroupPartitionGroup.objects.filter(course_user_group=self.cohort)
                .values_list("pk", "partition_id", "group_id")
            )

        assert patch({"user_partition_id": 50, "group_id": 1}) == (50, 1)
        [(first_pk, _, _)] = stored()
        assert patch({"user_partition_id": 50, "group_id": 2}) == (50, 2)
        [(second_pk, _, _)] = stored()
        assert second_pk != first_pk
        assert patch({"user_partition_id": 50, "group_id": 2}) == (50, 2)
        assert stored() == [(second_pk, 50, 2)]
        assert patch({"group_id": None}) == (None, None)
        assert not stored()
        assert patch({"group_id": None}) == (None, None)

    def test_group_id_without_partition_changes_nothing(self):
        """A content group without its configuration is a 400, and a rename alongside it is not applied."""
        response = self.client.patch(self.detail_url(), {"name": "Renamed", "group_id": 1}, format="json")
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"user_partition_id": ["Required when group_id is supplied."]}
        self.cohort.refresh_from_db()
        assert self.cohort.name == "Alpha"

    def test_missing_cohort_is_a_not_found_envelope(self):
        """An unknown cohort id is a 404 envelope without the legacy error fields."""
        response = self.client.patch(self.detail_url(cohort_id=99999), {"name": "X"}, format="json")
        assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")
        response = self.client.get(self.detail_url(cohort_id=99999))
        assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")


class TestCohortV2Members(CohortV2TestCase):
    """Listing, adding and removing cohort members."""

    def test_list(self):
        """The member list is an envelope of username, email and full name."""
        response = self.client.get(self.members_url())
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == PAGINATION_FIELDS
        assert response.data["results"] == [{
            "username": self.member.username,
            "email": self.member.email,
            "name": f"{self.member.first_name} {self.member.last_name}".strip(),
        }]

    def test_pages_partition_the_members_in_id_order(self):
        """Members are ordered by id, so consecutive pages neither repeat nor skip anyone."""
        extra = [UserFactory.create() for _ in range(3)]
        for user in reversed(extra):
            cohorts.add_user_to_cohort(self.cohort, user.username)
        expected = [user.username for user in sorted([self.member, *extra], key=lambda user: user.id)]
        first = self.client.get(self.members_url(), {"page_size": 2})
        second = self.client.get(self.members_url(), {"page_size": 2, "page": 2})
        seen = [row["username"] for row in first.data["results"] + second.data["results"]]
        assert seen == expected

    def test_add(self):
        """Each entry is reported in the bucket matching what happened to it."""
        other = create_cohort(self.course_key, "Other")
        mover = UserFactory.create()
        cohorts.add_user_to_cohort(other, mover.username)
        response = self.client.post(self.members_url(), {"users": [
            self.learner.username, mover.username, self.member.username,
            "nobody-by-this-name", "future@example.com", "bad@",
        ]}, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {
            "added": [{"username": self.learner.username, "email": self.learner.email}],
            "changed": [{"username": mover.username, "email": mover.email, "previous_cohort": "Other"}],
            "present": [self.member.username],
            "unknown": ["nobody-by-this-name"],
            "preassigned": ["future@example.com"],
            "invalid": ["bad@"],
        }
        assert sorted(self.cohort.users.values_list("username", flat=True)) == sorted(
            [self.member.username, self.learner.username, mover.username]
        )

    def test_add_without_users_is_rejected(self):
        """An empty list is a 400."""
        response = self.client.post(self.members_url(), {"users": []}, format="json")
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert list(envelope["errors"]) == ["users"]

    def test_remove(self):
        """Removing a member deletes the membership."""
        response = self.client.delete(self.member_url(self.member.username))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not self.cohort.users.exists()

    def test_remove_non_member_is_rejected(self):
        """Removing a learner who is not in the cohort is a 400."""
        response = self.client.delete(self.member_url(self.learner.username))
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert envelope["errors"] == {"username": ["The learner is not a member of this cohort."]}

    def test_remove_unknown_user_is_not_found(self):
        """Removing a username that does not exist is a 404."""
        response = self.client.delete(self.member_url("nobody-by-this-name"))
        assert_error_envelope(response, expected_status=404, expected_type_slug="not-found")


class TestCohortV2Settings(CohortV2TestCase):
    """Reading and updating the course's cohort settings."""

    def test_put(self):
        """Enabling cohorts stores the setting and returns it."""
        response = self.client.put(self.settings_url(), {"is_cohorted": True}, format="json")
        assert response.status_code == status.HTTP_200_OK
        stored = CourseCohortsSettings.objects.get(course_id=self.course_key)
        assert response.data == {"id": stored.id, "is_cohorted": True}
        assert stored.is_cohorted is True
        assert self.client.get(self.settings_url()).data == {"id": stored.id, "is_cohorted": True}

    def test_put_invalid_payload_is_rejected(self):
        """A value that is not a boolean is a 400."""
        response = self.client.put(self.settings_url(), {"is_cohorted": "sometimes"}, format="json")
        envelope = assert_error_envelope(response, expected_status=400, expected_type_slug="validation")
        assert list(envelope["errors"]) == ["is_cohorted"]

    def test_unknown_course_is_a_not_found_envelope(self):
        """Settings of a course that does not exist are a 404 for both methods."""
        self.make_global_staff()
        url = self.settings_url(MISSING_COURSE_KEY)
        assert_error_envelope(self.client.get(url), expected_status=404, expected_type_slug="not-found")
        assert_error_envelope(
            self.client.put(url, {"is_cohorted": True}, format="json"),
            expected_status=404, expected_type_slug="not-found",
        )
        assert CourseCohortsSettings.objects.filter(course_id=MISSING_COURSE_KEY).count() == 0
