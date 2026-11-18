"""Query-count guards for the version 2 retirement pipeline addresses."""

import pytest
from django.urls import reverse

from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from openedx.core.djangoapps.user_api.accounts.tests.retirement_helpers import (  # pylint: disable=unused-import
    create_retirement_status,
    setup_retirement_states,  # noqa: F401
)
from openedx.core.djangoapps.user_api.models import (
    RetirementState,
    UserRetirementPartnerReportingStatus,
)
from openedx.core.djangolib.testing.utils import skip_unless_lms

PASSWORD = "Password1234"
WORKER_USERNAME = "count-retirement-worker"

#: Row counts a per-row query would show up at.
ROW_COUNTS = [1, 5, 25]

#: How many courses each account in the partner reporting queue studied, which
#: is what the legacy claim reads one query at a time.
ENROLMENTS_PER_ROW = 3

#: The step the rows of the queue stand at, which is one the pipeline works.
WORKING_STATE = "LOCKING_ACCOUNT"

RETIREMENT_LIST_QUERIES = 15
RETIREMENT_DETAIL_QUERIES = 15
PARTNER_REPORT_CLAIM_QUERIES = 16

#: Addresses of the pipeline operations this version replaces, so the cost of
#: the two can be compared.
EARLIER_QUEUE = "/api/user/v1/accounts/retirement_queue/"
EARLIER_CLAIM = "/api/user/v1/accounts/retirement_partner_report/"

#: What the earlier claim costs, measured the same way as the counts above: a
#: constant part plus two queries for every account on it, because both the
#: account and the courses it studied are read one account at a time.
EARLIER_CLAIM_QUERIES = {1: 17, 5: 25, 25: 65}

#: What the earlier queue costs: a constant part plus one query for every
#: retirement on it, because the profile holding each account's full name is
#: read one retirement at a time.
EARLIER_QUEUE_QUERIES = {1: 16, 5: 20, 25: 40}


def sign_the_worker_in(client, settings):
    """Bind the pipeline's capability to a caller and sign that caller in."""
    settings.RETIREMENT_SERVICE_WORKER_USERNAME = WORKER_USERNAME
    worker = UserFactory.create(username=WORKER_USERNAME, password=PASSWORD)
    assert client.login(username=WORKER_USERNAME, password=PASSWORD)
    return worker


@pytest.fixture(name="queue")
@skip_unless_lms
def queue_fixture(request, client, settings):
    """Sign the pipeline's caller in and put ``request.param`` retirements in the queue."""
    request.getfixturevalue("setup_retirement_states")
    sign_the_worker_in(client, settings)
    state = RetirementState.objects.get(state_name=WORKING_STATE)
    rows = [
        create_retirement_status(UserFactory.create(username=f"queued{index}"), state=state)
        for index in range(request.param)
    ]
    # The first authenticated request of a session populates caches the rest of
    # the session reuses; the counts below are the steady-state ones.
    client.get(reverse("user_v2:retirement_list"))
    return rows


@pytest.fixture(name="partner_queue")
@skip_unless_lms
def partner_queue_fixture(request, client, settings):
    """Sign the pipeline's caller in and queue ``request.param`` accounts for partner reporting."""
    request.getfixturevalue("setup_retirement_states")
    sign_the_worker_in(client, settings)
    rows = []
    for index in range(request.param):
        user = UserFactory.create(username=f"reported{index}")
        for course in range(ENROLMENTS_PER_ROW):
            CourseEnrollmentFactory.create(
                user=user, course_id=f"course-v1:Org{course}+Course{index}+Run",
            )
        rows.append(
            UserRetirementPartnerReportingStatus.objects.create(
                user=user,
                original_username=user.username,
                original_email=user.email,
                original_name=user.profile.name,
            ),
        )
    client.get(reverse("user_v2:retirement_list"))
    return rows


@pytest.mark.django_db
@pytest.mark.parametrize("queue", ROW_COUNTS, indirect=True)
@skip_unless_lms
def test_the_queue_costs_the_same_however_many_retirements_are_in_it(
    client, queue, django_assert_num_queries,
):
    with django_assert_num_queries(RETIREMENT_LIST_QUERIES):
        response = client.get(reverse("user_v2:retirement_list"), {"page_size": 100})
    assert len(response.json()["results"]) == len(queue)


@pytest.mark.django_db
@pytest.mark.parametrize("queue", ROW_COUNTS, indirect=True)
@skip_unless_lms
def test_the_minimal_queue_costs_the_same_however_many_retirements_are_in_it(
    client, queue, django_assert_num_queries,
):
    with django_assert_num_queries(RETIREMENT_LIST_QUERIES):
        response = client.get(
            reverse("user_v2:retirement_list"), {"page_size": 100, "view": "minimal"},
        )
    assert len(response.json()["results"]) == len(queue)


@pytest.mark.django_db
@pytest.mark.parametrize("queue", [1], indirect=True)
@skip_unless_lms
def test_one_retirement_costs_a_fixed_number_of_queries(client, queue, django_assert_num_queries):
    address = reverse("user_v2:retirement_detail", kwargs={"username": queue[0].original_username})
    with django_assert_num_queries(RETIREMENT_DETAIL_QUERIES):
        response = client.get(address)
    assert response.json()["original_username"] == queue[0].original_username


@pytest.mark.django_db
@pytest.mark.parametrize("partner_queue", ROW_COUNTS, indirect=True)
@skip_unless_lms
def test_the_claim_costs_the_same_however_many_accounts_are_queued(
    client, partner_queue, django_assert_num_queries,
):
    """The courses each account studied are read with the entries rather than one entry at a time."""
    with django_assert_num_queries(PARTNER_REPORT_CLAIM_QUERIES):
        response = client.post(reverse("user_v2:retirement_partner_report_list"), {}, format="json")
    body = response.json()
    assert len(body) == len(partner_queue)
    assert all(len(entry["orgs"]) == ENROLMENTS_PER_ROW for entry in body)


@pytest.mark.django_db
@pytest.mark.parametrize("partner_queue", ROW_COUNTS, indirect=True)
@skip_unless_lms
def test_the_earlier_claim_reads_the_courses_one_account_at_a_time(
    client, partner_queue, django_assert_num_queries,
):
    """The baseline the count above is measured against, on the address this version replaces."""
    with django_assert_num_queries(EARLIER_CLAIM_QUERIES[len(partner_queue)]):
        response = client.post(EARLIER_CLAIM, {}, format="json", content_type="application/json")
    assert len(response.json()) == len(partner_queue)


@pytest.mark.django_db
@pytest.mark.parametrize("queue", ROW_COUNTS, indirect=True)
@skip_unless_lms
def test_the_earlier_queue_costs_what_it_costs(client, queue, django_assert_num_queries):
    """The baseline the queue count is measured against, on the address this version replaces."""
    with django_assert_num_queries(EARLIER_QUEUE_QUERIES[len(queue)]):
        response = client.get(EARLIER_QUEUE, {"cool_off_days": 0, "states": WORKING_STATE})
    assert len(response.json()) == len(queue)
