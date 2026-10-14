"""Query-count guards for the version 2 User API list and member addresses."""

import pytest
from django.test.utils import override_settings
from django.urls import reverse

from common.djangoapps.student.tests.factories import AdminFactory, UserFactory
from openedx.core.djangoapps.django_comment_common.models import Role
from openedx.core.djangoapps.user_api.tests.factories import UserPreferenceFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms

PASSWORD = "Password1234"
COURSE_KEY = "course-v1:edX+DemoX+Demo"

#: Row counts a per-row query would show up at.
ROW_COUNTS = [1, 5, 25]

#: Page sizes a per-item query would show up at, for the list whose rows are
#: not database rows and whose size is therefore set by the page alone.
PAGE_SIZES = [1, 10, 100]

USER_LIST_QUERIES = 16
USER_DETAIL_QUERIES = 15
PREFERENCE_LIST_QUERIES = 15
PREFERENCE_LIST_FULL_VIEW_QUERIES = 16
PREFERENCE_DETAIL_QUERIES = 14
TIME_ZONE_LIST_QUERIES = 13

#: Address of the stored-preference list this version replaces, and the key it
#: is gated by, so the cost of the two can be compared in one measurement.
EARLIER_PREFERENCE_LIST = "/api/user/v1/user_prefs/"
EARLIER_API_KEY = "query-count-api-key"

#: What that list costs for a page holding two rows per user, measured the same
#: way as the counts above: a constant part plus five queries for every user on
#: the page, because each row fetches its owner, the owner's profile and the
#: owner's own preferences again.
EARLIER_PREFERENCE_LIST_QUERIES = {1: 15, 5: 35, 25: 135}


@pytest.fixture(name="directory")
@skip_unless_lms
def directory_fixture(request, client, django_user_model):  # pylint: disable=unused-argument
    """Sign an administrator in and populate the directory with ``request.param`` users."""
    admin = AdminFactory.create(username="count-admin", password=PASSWORD)
    users = [UserFactory.create(username=f"counted{index}") for index in range(request.param)]
    for user in users:
        UserPreferenceFactory.create(user=user, key="pref-lang", value="en")
        UserPreferenceFactory.create(user=user, key="time_zone", value="UTC")
    role = Role.objects.create(course_id=COURSE_KEY, name="Moderator")
    role.users.add(*users)
    assert client.login(username=admin.username, password=PASSWORD)
    # The first authenticated request of a session populates caches the rest
    # of the session reuses; the counts below are the steady-state ones.
    client.get(reverse("user_v2:user_list"))
    return users


@pytest.mark.django_db
@pytest.mark.parametrize("directory", ROW_COUNTS, indirect=True)
@skip_unless_lms
def test_user_list_query_count_does_not_grow_with_rows(client, directory, django_assert_num_queries):
    url = reverse("user_v2:user_list")
    with django_assert_num_queries(USER_LIST_QUERIES):
        response = client.get(url, {"page_size": 100})
    assert len(response.json()["results"]) == len(directory) + 1


@pytest.mark.django_db
@pytest.mark.parametrize("directory", ROW_COUNTS, indirect=True)
@skip_unless_lms
def test_preference_key_filter_query_count_does_not_grow_with_rows(
    client, directory, django_assert_num_queries,
):
    url = reverse("user_v2:user_list")
    with django_assert_num_queries(USER_LIST_QUERIES):
        response = client.get(url, {"preference_key": "pref-lang", "page_size": 100})
    assert len(response.json()["results"]) == len(directory)


@pytest.mark.django_db
@pytest.mark.parametrize("directory", ROW_COUNTS, indirect=True)
@skip_unless_lms
def test_forum_role_filter_query_count_does_not_grow_with_rows(
    client, directory, django_assert_num_queries,
):
    url = reverse("user_v2:user_list")
    with django_assert_num_queries(USER_LIST_QUERIES):
        response = client.get(
            url, {"forum_role": "Moderator", "course_key": COURSE_KEY, "page_size": 100},
        )
    assert len(response.json()["results"]) == len(directory)


@pytest.mark.django_db
@pytest.mark.parametrize("directory", ROW_COUNTS, indirect=True)
@skip_unless_lms
def test_preference_list_query_count_does_not_grow_with_rows(
    client, directory, django_assert_num_queries,
):
    url = reverse("user_v2:user_preference_list")
    with django_assert_num_queries(PREFERENCE_LIST_QUERIES):
        response = client.get(url, {"page_size": 100})
    assert len(response.json()["results"]) == 2 * len(directory)


@pytest.mark.django_db
@pytest.mark.parametrize("directory", ROW_COUNTS, indirect=True)
@skip_unless_lms
def test_preference_list_full_view_query_count_does_not_grow_with_rows(
    client, directory, django_assert_num_queries,
):
    url = reverse("user_v2:user_preference_list")
    with django_assert_num_queries(PREFERENCE_LIST_FULL_VIEW_QUERIES):
        response = client.get(url, {"page_size": 100, "view": "full"})
    rows = response.json()["results"]
    assert len(rows) == 2 * len(directory)
    assert rows[0]["user"]["preferences"] == {"pref-lang": "en", "time_zone": "UTC"}


@pytest.mark.django_db
@pytest.mark.parametrize("page_size", PAGE_SIZES)
@pytest.mark.parametrize("directory", [1], indirect=True)
@skip_unless_lms
def test_time_zone_list_query_count_does_not_grow_with_page_size(
    client, directory, django_assert_num_queries, page_size,
):  # pylint: disable=unused-argument
    """The rows come from the time-zone database, so only the page size varies them."""
    url = reverse("user_v2:time_zone_list")
    with django_assert_num_queries(TIME_ZONE_LIST_QUERIES):
        response = client.get(url, {"page_size": page_size})
    assert len(response.json()["results"]) == page_size


@pytest.mark.django_db
@pytest.mark.parametrize("directory", [5], indirect=True)
@skip_unless_lms
def test_member_query_counts(client, directory, django_assert_num_queries):
    with django_assert_num_queries(USER_DETAIL_QUERIES):
        response = client.get(reverse("user_v2:user_detail", kwargs={"username": "counted0"}))
    assert response.json()["username"] == "counted0"

    with django_assert_num_queries(PREFERENCE_DETAIL_QUERIES):
        response = client.get(
            reverse(
                "user_v2:user_preference_detail",
                kwargs={"username": "counted0", "key": "pref-lang"},
            ),
        )
    assert response.json()["value"] == "en"


@pytest.mark.django_db
@pytest.mark.parametrize("directory", ROW_COUNTS, indirect=True)
@skip_unless_lms
def test_the_earlier_preference_list_pays_per_row_where_this_one_does_not(
    client, directory, django_assert_num_queries,
):
    """
    Both lists are measured over the same rows, so the saving cannot drift apart.

    The earlier row embeds its owner without a join and re-reads that owner's
    own preferences, so a page of it costs five queries per user; this one
    reads the same page at a count that does not move with it.
    """
    with django_assert_num_queries(PREFERENCE_LIST_QUERIES):
        current = client.get(reverse("user_v2:user_preference_list"), {"page_size": 100})
    assert len(current.json()["results"]) == 2 * len(directory)

    client.logout()
    with override_settings(EDX_API_KEY=EARLIER_API_KEY):
        with django_assert_num_queries(EARLIER_PREFERENCE_LIST_QUERIES[len(directory)]):
            earlier = client.get(
                EARLIER_PREFERENCE_LIST,
                {"page_size": 100},
                HTTP_X_EDX_API_KEY=EARLIER_API_KEY,
            )
    assert len(earlier.json()["results"]) == 2 * len(directory)
