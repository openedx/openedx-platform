"""Query-count guards for the version 2 User API account addresses."""

import json

import pytest
from django.urls import reverse

from common.djangoapps.student.tests.factories import (
    ContentTypeFactory,
    PermissionFactory,
    UserFactory,
)
from openedx.core.djangoapps.user_api.accounts import (
    ACCOUNT_VISIBILITY_PREF_KEY,
    ALL_USERS_VISIBILITY,
    CUSTOM_VISIBILITY,
    VISIBILITY_PREFIX,
)
from openedx.core.djangoapps.user_api.tests.factories import UserPreferenceFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms

PASSWORD = "Password1234"
MERGE_PATCH = "application/merge-patch+json"

#: Collection sizes a per-account query would show up at.
ROW_COUNTS = [1, 5, 25]

#: Page sizes, so that what a page costs can be told apart from what the
#: collection behind it costs.
PAGE_SIZES = [1, 5, 10]

#: Year of birth the measured accounts carry. An account that records none
#: counts as a minor, and what a caller may see of it is settled by that
#: before any preference of its owner is read, which is fewer reads than a
#: populated account costs.
MEASURED_YEAR_OF_BIRTH = 1990

#: What one page of accounts costs a staff caller, by the number of accounts
#: on it: a constant part of sixteen, and six reads for each account the page
#: carries. The account service selects the accounts in one query and then
#: builds each one on its own, reading its recovery address, its languages,
#: its links, its pending name change, one stored preference and the switch
#: that says whether a recovery address is offered at all. The page pays none
#: of that for the accounts it leaves out, which is what these numbers pin.
ACCOUNT_LIST_QUERIES = {1: 22, 5: 46, 10: 76}

#: The same page read by a learner, who may see of each account only what its
#: owner shares: the six reads above and the stored preference that says how
#: much that is, seven an account.
LEARNER_LIST_QUERIES = {1: 23, 5: 51, 10: 86}

#: The same page where every owner has chosen property by property what they
#: share: the seven reads above and the preferences that choice is written in,
#: read once for the account rather than once for each property, eight an
#: account. A page carries at most a hundred accounts, so this is the dearest
#: page the list answers with.
CHOSEN_VISIBILITY_LIST_QUERIES = {1: 24, 5: 56, 10: 96}

ACCOUNT_DETAIL_QUERIES = 20
OWN_ACCOUNT_QUERIES = 20

#: Address of the one this version answers with the caller's own account. It
#: answered with the username alone and read nothing of the account to do it,
#: so what the whole account costs is what this version's answer costs more.
#: Selecting the one property the earlier body carried shapes that answer; it
#: does not make it cheaper, because the account is built before it is shaped.
#: The earlier address is read first below, so the reads a request pays once
#: are counted against it and the difference measured is the smaller one.
EARLIER_OWN_ACCOUNT = "/api/user/v1/me"
EARLIER_OWN_ACCOUNT_QUERIES = 13
#: A search is answered a page at a time, which costs one read for the page of
#: rows and one for the number of rows there are to page through, whatever the
#: addresses asked about.
EMAIL_SEARCH_QUERIES = 15

#: Address of the account list this version replaces, which has no pages: it
#: serves every account named in one unbounded response and pays the per-account
#: cost above for all of them.
EARLIER_ACCOUNT_LIST = "/api/user/v1/accounts"
EARLIER_ACCOUNT_LIST_QUERIES = {1: 20, 5: 44, 25: 164}

#: What a change of one property costs, measured on the version 1 address
#: first. Both versions do the same work and answer with the account as it now
#: stands, so the one number is asserted on both addresses.
CHANGE_QUERIES = 30

#: What a deactivation costs on each address. The earlier one answers with the
#: account it deactivated and pays for reading it back; this one answers with
#: nothing, so it is cheaper by exactly those reads.
EARLIER_DEACTIVATION_QUERIES = 24
DEACTIVATION_QUERIES = 18


def measured_accounts(count):
    """Return ``count`` accounts carrying the profile data a populated account holds."""
    return [
        UserFactory.create(
            username=f"counted{index:03d}",
            email=f"counted{index:03d}@example.com",
            profile__year_of_birth=MEASURED_YEAR_OF_BIRTH,
        )
        for index in range(count)
    ]


def warm_up(client):
    """
    Spend what a session pays once, so the counts are the steady-state ones.

    The first authenticated request of a session populates caches the rest of
    the session reuses.
    """
    client.get(reverse("user_v2:account_list"))


@pytest.fixture(name="accounts")
@skip_unless_lms
def accounts_fixture(request, client, django_user_model):  # pylint: disable=unused-argument
    """Sign a staff caller in and give the site ``request.param`` other accounts."""
    staff = UserFactory.create(
        username="count-staff",
        password=PASSWORD,
        is_staff=True,
        profile__year_of_birth=MEASURED_YEAR_OF_BIRTH,
    )
    users = measured_accounts(request.param)
    assert client.login(username=staff.username, password=PASSWORD)
    warm_up(client)
    return users


@pytest.fixture(name="accounts_read_by_a_learner")
@skip_unless_lms
def learner_accounts_fixture(request, client, django_user_model):  # pylint: disable=unused-argument
    """Sign a learner in beside ``request.param`` accounts belonging to other people."""
    learner = UserFactory.create(
        username="count-learner",
        password=PASSWORD,
        profile__year_of_birth=MEASURED_YEAR_OF_BIRTH,
    )
    users = measured_accounts(request.param)
    assert client.login(username=learner.username, password=PASSWORD)
    warm_up(client)
    return users


@pytest.fixture(name="accounts_shared_by_choice")
@skip_unless_lms
def chosen_visibility_fixture(request, client, django_user_model):  # pylint: disable=unused-argument
    """Sign a learner in beside accounts whose owners chose property by property what they share."""
    learner = UserFactory.create(
        username="count-learner",
        password=PASSWORD,
        profile__year_of_birth=MEASURED_YEAR_OF_BIRTH,
    )
    users = measured_accounts(request.param)
    for user in users:
        UserPreferenceFactory.create(
            user=user, key=ACCOUNT_VISIBILITY_PREF_KEY, value=CUSTOM_VISIBILITY,
        )
        UserPreferenceFactory.create(
            user=user, key=f"{VISIBILITY_PREFIX}bio", value=ALL_USERS_VISIBILITY,
        )
    assert client.login(username=learner.username, password=PASSWORD)
    warm_up(client)
    return users


def selected(users):
    """Return the selector naming every account of the fixture."""
    return ",".join(user.username for user in users)


@pytest.mark.django_db
@pytest.mark.parametrize("accounts", ROW_COUNTS, indirect=True)
@skip_unless_lms
def test_account_list_query_count_does_not_grow_with_the_accounts_selected(
    client, accounts, django_assert_num_queries,
):
    url = reverse("user_v2:account_list")
    with django_assert_num_queries(ACCOUNT_LIST_QUERIES[1]):
        response = client.get(url, {"username": selected(accounts), "page_size": 1})
    assert response.json()["count"] == len(accounts)
    assert len(response.json()["results"]) == 1


@pytest.mark.django_db
@pytest.mark.parametrize("page_size", PAGE_SIZES)
@pytest.mark.parametrize("accounts", [25], indirect=True)
@skip_unless_lms
def test_the_page_costs_what_it_carries_and_not_what_the_collection_holds(
    client, accounts, page_size, django_assert_num_queries,
):
    url = reverse("user_v2:account_list")
    with django_assert_num_queries(ACCOUNT_LIST_QUERIES[page_size]):
        response = client.get(url, {"username": selected(accounts), "page_size": page_size})
    assert response.json()["count"] == 25
    assert len(response.json()["results"]) == page_size


@pytest.mark.django_db
@pytest.mark.parametrize("page_size", PAGE_SIZES)
@pytest.mark.parametrize("accounts_read_by_a_learner", [25], indirect=True)
@skip_unless_lms
def test_a_learner_reading_other_accounts_pays_for_the_page_and_not_the_collection(
    client, accounts_read_by_a_learner, page_size, django_assert_num_queries,
):
    """What a caller may see of an account is worked out for the accounts of the page alone."""
    url = reverse("user_v2:account_list")
    with django_assert_num_queries(LEARNER_LIST_QUERIES[page_size]):
        response = client.get(
            url, {"username": selected(accounts_read_by_a_learner), "page_size": page_size},
        )
    assert response.json()["count"] == 25
    assert len(response.json()["results"]) == page_size
    assert "email" not in response.json()["results"][0]


@pytest.mark.django_db
@pytest.mark.parametrize("page_size", PAGE_SIZES)
@pytest.mark.parametrize("accounts_shared_by_choice", [25], indirect=True)
@skip_unless_lms
def test_an_account_shared_property_by_property_costs_one_read_more(
    client, accounts_shared_by_choice, page_size, django_assert_num_queries,
):
    """An owner who chose what they share is asked for that choice once, not once per property."""
    url = reverse("user_v2:account_list")
    with django_assert_num_queries(CHOSEN_VISIBILITY_LIST_QUERIES[page_size]):
        response = client.get(
            url, {"username": selected(accounts_shared_by_choice), "page_size": page_size},
        )
    assert response.json()["count"] == 25
    assert len(response.json()["results"]) == page_size
    assert "bio" in response.json()["results"][0]


@pytest.mark.django_db
@pytest.mark.parametrize("accounts", ROW_COUNTS, indirect=True)
@skip_unless_lms
def test_the_earlier_account_list_pays_for_every_account_named(
    client, accounts, django_assert_num_queries,
):
    """
    Both lists are measured over the same accounts, so the saving cannot drift apart.

    The earlier address serves every account named in one response and pays
    the per-account cost for all of them; this one pays it for the accounts of
    the page alone.
    """
    # The two pins together say that the earlier cost grows with the accounts
    # named while the page's does not, so neither can be changed on its own.
    assert EARLIER_ACCOUNT_LIST_QUERIES[25] > EARLIER_ACCOUNT_LIST_QUERIES[1]
    assert ACCOUNT_LIST_QUERIES[1] < EARLIER_ACCOUNT_LIST_QUERIES[25]

    with django_assert_num_queries(EARLIER_ACCOUNT_LIST_QUERIES[len(accounts)]):
        earlier = client.get(EARLIER_ACCOUNT_LIST, {"username": selected(accounts)})
    assert len(earlier.json()) == len(accounts)

    with django_assert_num_queries(ACCOUNT_LIST_QUERIES[1]):
        current = client.get(
            reverse("user_v2:account_list"), {"username": selected(accounts), "page_size": 1},
        )
    assert current.json()["count"] == len(accounts)


@pytest.mark.django_db
@pytest.mark.parametrize("accounts", [5], indirect=True)
@skip_unless_lms
def test_member_query_counts(client, accounts, django_assert_num_queries):
    with django_assert_num_queries(ACCOUNT_DETAIL_QUERIES):
        response = client.get(reverse("user_v2:account_detail", kwargs={"username": "counted000"}))
    assert response.json()["username"] == "counted000"

    with django_assert_num_queries(OWN_ACCOUNT_QUERIES):
        own = client.get(reverse("user_v2:account_detail", kwargs={"username": "me"}))
    assert own.json()["username"] == "count-staff"


@pytest.mark.django_db
@pytest.mark.parametrize("accounts", [1], indirect=True)
@skip_unless_lms
def test_the_callers_own_account_costs_more_here_than_the_name_alone_did(
    client, accounts, django_assert_num_queries,
):
    """
    Declared difference: this address answers with the whole account.

    The earlier address answered with the username, which it had already, and
    asking this one for that property alone shapes the answer rather than the
    work behind it: the account is built and then reduced, so the selection
    costs what the whole account costs.
    """
    with django_assert_num_queries(EARLIER_OWN_ACCOUNT_QUERIES):
        earlier = client.get(EARLIER_OWN_ACCOUNT)
    assert earlier.json() == {"username": "count-staff"}

    with django_assert_num_queries(OWN_ACCOUNT_QUERIES):
        whole = client.get(reverse("user_v2:account_detail", kwargs={"username": "me"}))
    assert whole.json()["username"] == "count-staff"
    assert set(whole.json()) > set(earlier.json())

    with django_assert_num_queries(OWN_ACCOUNT_QUERIES):
        one_property = client.get(
            reverse("user_v2:account_detail", kwargs={"username": "me"}), {"fields": "username"},
        )
    assert one_property.json() == earlier.json()
    assert OWN_ACCOUNT_QUERIES > EARLIER_OWN_ACCOUNT_QUERIES


@pytest.mark.django_db
@pytest.mark.parametrize("accounts", ROW_COUNTS, indirect=True)
@skip_unless_lms
def test_email_search_query_count_does_not_grow_with_the_addresses_searched(
    client, accounts, django_assert_num_queries,
):
    with django_assert_num_queries(EMAIL_SEARCH_QUERIES):
        response = client.post(
            reverse("user_v2:email_search_list"),
            {"emails": [user.email for user in accounts]},
            content_type="application/json",
        )
    assert response.json()["count"] == len(accounts)


@pytest.fixture(name="account_owner")
@skip_unless_lms
def account_owner_fixture(client, django_user_model):  # pylint: disable=unused-argument
    """Sign a learner in whose own account is the one changed."""
    owner = UserFactory.create(
        username="count-change-owner",
        password=PASSWORD,
        profile__year_of_birth=MEASURED_YEAR_OF_BIRTH,
    )
    assert client.login(username=owner.username, password=PASSWORD)
    warm_up(client)
    return owner


@pytest.fixture(name="deactivation_targets")
@skip_unless_lms
def deactivation_fixture(client, django_user_model):  # pylint: disable=unused-argument
    """Sign a caller in who may deactivate accounts, beside one account per version to deactivate."""
    deactivator = UserFactory.create(username="count-deactivator", password=PASSWORD)
    deactivator.user_permissions.add(
        PermissionFactory(
            codename="can_deactivate_users",
            content_type=ContentTypeFactory(app_label="student"),
        ),
    )
    targets = (
        UserFactory.create(username="count-target-one", password=PASSWORD),
        UserFactory.create(username="count-target-two", password=PASSWORD),
    )
    assert client.login(username=deactivator.username, password=PASSWORD)
    warm_up(client)
    return targets


@pytest.mark.django_db
@skip_unless_lms
def test_a_change_costs_what_the_earlier_address_costs(
    client, account_owner, django_assert_num_queries,
):
    """A property is changed for the same reads on both addresses, and each answers with the account."""
    with django_assert_num_queries(CHANGE_QUERIES):
        earlier = client.patch(
            f"/api/user/v1/accounts/{account_owner.username}",
            json.dumps({"bio": "Earlier bio"}),
            content_type=MERGE_PATCH,
        )
    assert earlier.json()["bio"] == "Earlier bio"

    with django_assert_num_queries(CHANGE_QUERIES):
        later = client.patch(
            reverse("user_v2:account_detail", kwargs={"username": account_owner.username}),
            json.dumps({"bio": "Later bio"}),
            content_type=MERGE_PATCH,
        )
    assert later.json()["bio"] == "Later bio"


@pytest.mark.django_db
@skip_unless_lms
def test_a_deactivation_costs_what_the_earlier_address_costs(
    client, deactivation_targets, django_assert_num_queries,
):
    """
    Each version is measured over an account of its own, so neither pays for the other's work.

    The earlier address answers with the account it deactivated; this one
    answers with nothing, which is the difference between the two numbers.
    """
    earlier_target, target = deactivation_targets
    # The saving is the account the earlier address reads back to answer with,
    # so neither number can be changed without the other.
    assert DEACTIVATION_QUERIES < EARLIER_DEACTIVATION_QUERIES

    with django_assert_num_queries(EARLIER_DEACTIVATION_QUERIES):
        earlier = client.post(f"/api/user/v1/accounts/{earlier_target.username}/deactivate/")
    assert earlier.status_code == 200
    earlier_target.refresh_from_db()
    assert not earlier_target.has_usable_password()

    with django_assert_num_queries(DEACTIVATION_QUERIES):
        later = client.post(
            reverse("user_v2:account_deactivation", kwargs={"username": target.username}),
        )
    assert later.status_code == 204
    target.refresh_from_db()
    assert not target.has_usable_password()
