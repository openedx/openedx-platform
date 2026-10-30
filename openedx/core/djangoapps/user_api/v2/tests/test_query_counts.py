"""Query-count guards for the version 2 User API list and member addresses."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import reverse

from common.djangoapps.student.tests.factories import AdminFactory, UserFactory
from openedx.core.djangoapps.django_comment_common.models import Role
from openedx.core.djangoapps.user_api.models import UserOrgTag
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


#: Address of one account's preferences in the version this one supersedes.
#: Both versions call the same service over the same rows, so what each of the
#: addresses below costs is measured at the superseded address in the same run
#: and this version is held to that measurement rather than to a number
#: written down here. A change in what every request of the site costs then
#: moves both measurements together instead of turning these red.
EARLIER_PREFERENCE_DOCUMENT = "/api/user/v1/preferences/{username}"

#: How many more preferences are stored before a cost is measured again, so
#: that a query paid for each preference stored shows up as a rise.
MORE_PREFERENCES = 5


def cost_of(request):
    """Return what one request cost in queries, and what it answered with."""
    with CaptureQueriesContext(connection) as queries:
        answer = request()
    return len(queries), answer


@pytest.fixture(name="account_with_preferences")
@skip_unless_lms
def account_with_preferences_fixture(request, client, django_user_model):  # pylint: disable=unused-argument
    """Sign a learner in and give their own account ``request.param`` preferences besides a time zone."""
    owner = UserFactory.create(username="count-owner", password=PASSWORD)
    UserPreferenceFactory.create(user=owner, key="time_zone", value="UTC")
    for index in range(request.param):
        UserPreferenceFactory.create(user=owner, key=f"counted_{index:03d}", value="stored")
    assert client.login(username=owner.username, password=PASSWORD)
    # The first authenticated request of a session populates caches the rest
    # of the session reuses; the counts below are the steady-state ones.
    client.get(reverse("user_v2:account_preference_list", kwargs={"username": owner.username}))
    return owner, request.param


@pytest.mark.django_db
@pytest.mark.parametrize("account_with_preferences", ROW_COUNTS, indirect=True)
@skip_unless_lms
def test_the_preference_document_costs_the_same_at_either_address(
    client, account_with_preferences,
):
    """Neither version pays per preference: only the body grows with them."""
    owner, stored = account_with_preferences
    address = reverse("user_v2:account_preference_list", kwargs={"username": owner.username})
    earlier_cost, earlier = cost_of(
        lambda: client.get(EARLIER_PREFERENCE_DOCUMENT.format(username=owner.username)),
    )
    current_cost, current = cost_of(lambda: client.get(address))
    assert current_cost == earlier_cost
    assert earlier.json() == current.json()
    assert set(current.json()) == {"time_zone"} | {
        f"counted_{index:03d}" for index in range(stored)
    }

    for index in range(stored, stored + MORE_PREFERENCES):
        UserPreferenceFactory.create(user=owner, key=f"counted_{index:03d}", value="stored")
    with_more_cost, with_more = cost_of(lambda: client.get(address))
    assert with_more_cost == current_cost
    assert len(with_more.json()) == len(current.json()) + MORE_PREFERENCES


@pytest.mark.django_db
@pytest.mark.parametrize("account_with_preferences", ROW_COUNTS, indirect=True)
@skip_unless_lms
def test_one_preference_costs_the_same_at_either_address(client, account_with_preferences):
    """Reading one preference costs what it costs at the superseded address, stored rows aside."""
    owner, stored = account_with_preferences
    address = reverse(
        "user_v2:account_preference_detail",
        kwargs={"username": owner.username, "key": "time_zone"},
    )
    earlier_cost, earlier = cost_of(
        lambda: client.get(
            f"{EARLIER_PREFERENCE_DOCUMENT.format(username=owner.username)}/time_zone",
        ),
    )
    current_cost, current = cost_of(lambda: client.get(address))
    assert current_cost == earlier_cost
    assert earlier.json() == "UTC"
    assert current.json() == {"key": "time_zone", "value": "UTC"}

    for index in range(stored, stored + MORE_PREFERENCES):
        UserPreferenceFactory.create(user=owner, key=f"counted_{index:03d}", value="stored")
    with_more_cost, with_more = cost_of(lambda: client.get(address))
    assert with_more_cost == current_cost
    assert with_more.json() == {"key": "time_zone", "value": "UTC"}


@pytest.mark.django_db
@pytest.mark.parametrize("account_with_preferences", [1], indirect=True)
@skip_unless_lms
def test_recording_an_email_choice_costs_what_the_earlier_address_costs(
    client, account_with_preferences,
):
    """
    Each address is measured from the same starting state as the other.

    Recording the first choice creates the row it is kept in and recording a
    later one updates it, so a count taken after the other address has already
    recorded the choice would be the cheaper path measured against the dearer
    one. Both are measured here, and the rise between them is what makes the
    starting state matter.
    """
    owner, _stored = account_with_preferences
    choices = UserOrgTag.objects.filter(user=owner, key="email-optin")
    earlier_body = {"course_id": COURSE_KEY, "email_opt_in": "True"}
    current_body = {"course_key": COURSE_KEY, "email_opt_in": True}

    def record_at_the_earlier_address():
        """Record the choice the way the pages calling the earlier address do."""
        return client.post("/api/user/v1/preferences/email_opt_in/", earlier_body)

    def record():
        """Record the choice at this version's address."""
        return client.post(
            reverse("user_v2:email_opt_in_list"), current_body, content_type="application/json",
        )

    costs = {}
    for address, record_choice, expected_status in (
        ("the earlier address", record_at_the_earlier_address, 200),
        ("this version", record, 204),
    ):
        choices.delete()
        opening_cost, opened = cost_of(record_choice)
        assert opened.status_code == expected_status, address
        changing_cost, changed = cost_of(record_choice)
        assert changed.status_code == expected_status, address
        assert choices.count() == 1, address
        costs[address] = (opening_cost, changing_cost)

    assert costs["this version"] == costs["the earlier address"]
    opening_cost, changing_cost = costs["this version"]
    assert opening_cost > changing_cost
