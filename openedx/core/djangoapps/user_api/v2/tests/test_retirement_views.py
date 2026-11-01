"""Tests for the version 2 User API views over the account retirement pipeline."""

import contextlib
import datetime
from unittest import mock
from zoneinfo import ZoneInfo

import pytest
from crum.signals import current_user_setter
from django.conf import settings
from django.contrib.auth.models import Permission
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.core.signals import got_request_exception, request_finished, request_started
from django.db import connection
from django.db.backends.signals import connection_created
from django.db.models.signals import class_prepared, post_init, post_save, pre_init, pre_save
from django.dispatch import Signal
from django.test import SimpleTestCase, override_settings
from django.test.signals import setting_changed
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from edx_django_utils.monitoring.signals import (
    monitoring_support_process_request,
    monitoring_support_process_response,
)
from edx_rest_framework_extensions.errors import (
    INTERNAL_ERROR_SLUG,
    INTERNAL_ERROR_TITLE,
    Conflict,
    classify_error,
    error_type_uri,
)
from edx_rest_framework_extensions.permissions import NotJwtRestrictedApplication
from edx_rest_framework_extensions.scoping import FullScopePolicy
from edx_rest_framework_extensions.testing import assert_error_envelope
from opaque_keys.edx.keys import CourseKey
from rest_framework import permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from common.djangoapps.student.models import (
    AccountRecovery,
    CourseEnrollmentAllowed,
    PendingEmailChange,
    PendingNameChange,
    UserProfile,
    get_retired_username_by_username,
)
from common.djangoapps.student.tests.factories import (
    AdminFactory,
    CourseEnrollmentFactory,
    SuperuserFactory,
    UserFactory,
)
from common.djangoapps.util.model_utils import USER_FIELDS_CHANGED
from openedx.core.djangoapps.user_api.accounts.permissions import CanCancelUserRetirement, CanRetireUser
from openedx.core.djangoapps.user_api.accounts.signals import USER_RETIRE_LMS_CRITICAL
from openedx.core.djangoapps.user_api.accounts.tests.retirement_helpers import (  # pylint: disable=unused-import
    create_retirement_status,
    setup_retirement_states,  # noqa: F401
)
from openedx.core.djangoapps.user_api.models import (
    RetirementState,
    UserOrgTag,
    UserPreference,
    UserRetirementPartnerReportingStatus,
    UserRetirementRequest,
    UserRetirementStatus,
)
from openedx.core.djangoapps.user_api.v2 import urls as v2_urls
from openedx.core.djangoapps.user_api.v2 import view_services
from openedx.core.djangoapps.user_api.v2.errors import AmbiguousAccountName, RetirementStateConflict
from openedx.core.djangoapps.user_api.v2.paginators import UserApiPagination
from openedx.core.djangoapps.user_api.v2.serializers import (
    RETIREMENT_MINIMAL_FIELDS,
    RetirementSerializer,
)
from openedx.core.djangoapps.user_api.v2.views import (
    RETIREMENT_ACTION_PERMISSIONS,
    RetirementCleanupViewSet,
    RetirementPartnerReportCleanupViewSet,
    RetirementPartnerReportViewSet,
    RetirementViewSet,
    _PartnerReportAccessMixin,
    _RetirementRecordAccessMixin,
    _RetirementRecordsMixin,
)
from openedx.core.djangolib.testing.utils import skip_unless_lms

from .test_views import (
    MALFORMED_BODY_SLUG,
    NOT_ACCEPTABLE_SLUG,
    REFUSED_METHOD_SLUG,
    UNSUPPORTED_MEDIA_TYPE_SLUG,
    write_queries,
)

PASSWORD = "Password1234"

#: The caller the pipeline runs as, the caller that replaces usernames, and the
#: names the deployment binds each capability to.
WORKER_USERNAME = "retirement-worker"
REPLACEMENT_WORKER_USERNAME = "replacement-worker"

#: The callers that may carry out pipeline work, and the callers that may not.
PIPELINE_CALLERS = ("worker", "requester", "superuser")
PIPELINE_REFUSED = ("learner", "subject", "staff", "canceller", "replacer")

#: The callers that may undo a retirement request, and the callers that may
#: not. The pipeline itself is among the refused: carrying a retirement out and
#: calling it off are different capabilities, and one request cannot hold both.
CANCEL_CALLERS = ("canceller", "superuser")
CANCEL_REFUSED = ("learner", "subject", "staff", "requester", "worker", "replacer")

#: The query parameters each retirement address reads, in the order its refusal
#: lists them.
LIST_QUERY_PARAMS = (
    "cool_off_days, created_after, created_before, fields, format, ordering, page, page_size, state, view"
)
DETAIL_QUERY_PARAMS = "fields, format, view"
WRITE_QUERY_PARAMS = "format"
CLAIM_QUERY_PARAMS = "format, limit"

#: What a move of a retirement cannot be asked for without, which is what the
#: published body of the request says it requires.
MOVE_REQUIRED_VALUES = ["new_state", "response"]

#: What the shortest and the longest page of any collection of this version holds.
DEFAULT_PAGE_SIZE = 10
MAXIMUM_PAGE_SIZE = 100

PAGINATION_KEYS = {"count", "num_pages", "current_page", "start", "next", "previous", "results"}

#: Every property a retirement record carries.
RETIREMENT_FIELDS = {
    "id",
    "user",
    "original_username",
    "original_email",
    "original_name",
    "retired_username",
    "retired_email",
    "current_state",
    "last_state",
    "created",
    "modified",
}

#: The properties of a retiring account's own data, which the ``minimal``
#: preset must not carry.
RETIREMENT_PII_FIELDS = {"original_email", "original_name", "user"}

STATE_CONFLICT_SLUG = "user/retirement-state-conflict"
AMBIGUOUS_NAME_SLUG = "user/ambiguous-account-name"

#: Where the language a user picked is carried between requests, the name it is
#: stored under, and the table it is stored in.
LANGUAGE_COOKIE = settings.LANGUAGE_COOKIE_NAME
LANGUAGE_PREFERENCE = "pref-lang"
PREFERENCE_TABLE = "user_api_userpreference"

#: The signals every request raises because the platform raises them: a row
#: being instantiated, the request beginning and ending, a connection being
#: opened, a model class being prepared, a setting being overridden by a test,
#: the telemetry each request is measured through, and the caller being put
#: where the code below the view reads it from. None of them says anything
#: about what the request did.
FRAMEWORK_SIGNALS = (
    pre_init,
    post_init,
    class_prepared,
    connection_created,
    request_started,
    request_finished,
    got_request_exception,
    setting_changed,
    monitoring_support_process_request,
    monitoring_support_process_response,
    current_user_setter,
)

#: What syncing the language from the cookie announces: the row being stored,
#: and the platform's own announcement that a field of the account changed.
LANGUAGE_SYNC_SIGNALS = [
    (pre_save, UserPreference),
    (post_save, UserPreference),
    (USER_FIELDS_CHANGED, None),
]

#: The addresses this version supersedes, used where the point is that both
#: versions behave alike.
EARLIER_RETIREMENT_QUEUE = "/api/user/v1/accounts/retirement_queue/"
EARLIER_RETIREMENT_CLEANUP = "/api/user/v1/accounts/retirement_cleanup/"
EARLIER_PARTNER_REPORT_CLEANUP = "/api/user/v1/accounts/retirement_partner_report_cleanup/"
EARLIER_PARTNER_REPORT = "/api/user/v1/accounts/retirement_partner_report/"
EARLIER_CORE_ERASURE = "/api/user/v1/accounts/retire/"


@contextlib.contextmanager
def captured_signals():
    """
    Record the signals sent while this block runs, bar the ones the framework sends itself.

    A signal is recorded by the signal object and the sender it was sent
    under, so that a test names the signals it expects rather than counting
    them.
    """
    sent = []
    originals = {name: getattr(Signal, name) for name in ("send", "send_robust")}

    def recording(original):
        """Return a replacement for one of the two ways a signal is sent."""
        def send(self, sender=None, **named):
            # The session row is left out for the same reason its statements
            # are: it is the platform's record of the caller being served,
            # not data this API answers from.
            if self not in FRAMEWORK_SIGNALS and sender is not Session:
                sent.append((self, sender))
            return original(self, sender=sender, **named)
        return send

    with mock.patch.object(Signal, "send", recording(originals["send"])), \
            mock.patch.object(Signal, "send_robust", recording(originals["send_robust"])):
        yield sent

COURSE_KEY = CourseKey.from_string("course-v1:edX+DemoX+Demo")
OTHER_COURSE_KEY = CourseKey.from_string("course-v1:OtherOrg+OtherX+Other")


def grant(user, codename, app_label="user_api"):
    """Give this user the named Django model permission."""
    user.user_permissions.add(
        Permission.objects.get(codename=codename, content_type__app_label=app_label),
    )


@override_settings(
    RETIREMENT_SERVICE_WORKER_USERNAME=WORKER_USERNAME,
    USERNAME_REPLACEMENT_WORKER=REPLACEMENT_WORKER_USERNAME,
)
@pytest.mark.usefixtures("setup_retirement_states")
class RetirementApiTestCase(APITestCase):
    """The callers and the fixtures the retirement pipeline tests share."""

    def setUp(self):
        super().setUp()
        self.worker = UserFactory.create(username=WORKER_USERNAME, password=PASSWORD)
        self.replacer = UserFactory.create(username=REPLACEMENT_WORKER_USERNAME, password=PASSWORD)
        self.superuser = SuperuserFactory.create(username="super-caller", password=PASSWORD)
        self.staff = AdminFactory.create(username="plain-staff", password=PASSWORD)
        self.requester = AdminFactory.create(username="staff-who-requests", password=PASSWORD)
        self.canceller = AdminFactory.create(username="staff-who-cancels", password=PASSWORD)
        self.learner = UserFactory.create(username="learner-caller", password=PASSWORD)
        self.subject = UserFactory.create(
            username="subject", email="subject@example.com", password=PASSWORD,
        )
        grant(self.requester, "add_userretirementrequest")
        grant(self.canceller, "change_userretirementstatus")

    def login(self, caller):
        """Sign the named caller in through the session the platform default accepts."""
        user = getattr(self, caller)
        assert self.client.login(username=user.username, password=PASSWORD)

    def state(self, state_name):
        """Return the configured pipeline step of this name."""
        return RetirementState.objects.get(state_name=state_name)

    def retirement_in(self, state_name, user=None, created=None):
        """Return a retirement standing at this pipeline step."""
        return create_retirement_status(
            user or UserFactory.create(), state=self.state(state_name), create_datetime=created,
        )


class _AccessMatrixMixin:
    """
    Every caller the address admits is served, and every other caller is refused.

    The matrix is the proof that the three capabilities of this area are
    carried over as they stand: the caller the pipeline runs as, the staff
    caller who may add a retirement request and a superuser work the pipeline;
    only the staff caller who may change a retirement status and a superuser
    call one off; and the caller that replaces usernames reaches neither.
    """

    #: The callers the address admits, and the ones it refuses.
    admitted = ()
    refused = ()

    def send(self):
        """Make the request this address serves, building whatever it acts on."""
        raise NotImplementedError

    def test_every_admitted_caller_is_served(self):
        for caller in self.admitted:
            with self.subTest(caller=caller):
                self.client.logout()
                self.login(caller)
                response = self.send()
                assert response.status_code in (
                    status.HTTP_200_OK,
                    status.HTTP_204_NO_CONTENT,
                ), f"{caller} got {response.status_code}: {getattr(response, 'data', None)!r}"

    def test_every_other_caller_is_forbidden(self):
        for caller in self.refused:
            with self.subTest(caller=caller):
                self.client.logout()
                self.login(caller)
                assert_error_envelope(
                    self.send(),
                    expected_status=status.HTTP_403_FORBIDDEN,
                    expected_type_slug="authz",
                )

    def test_an_anonymous_caller_is_unauthenticated(self):
        self.client.logout()
        assert_error_envelope(
            self.send(),
            expected_status=status.HTTP_401_UNAUTHORIZED,
            expected_type_slug="authn",
        )


@skip_unless_lms
class RetirementListTests(_AccessMatrixMixin, RetirementApiTestCase):
    """GET /api/user/v2/retirements/"""

    admitted = PIPELINE_CALLERS
    refused = PIPELINE_REFUSED

    def setUp(self):
        super().setUp()
        self.url = reverse("user_v2:retirement_list")
        day = datetime.datetime(2026, 3, 1, tzinfo=ZoneInfo("UTC"))
        self.pending = self.retirement_in("PENDING", created=day)
        self.locking = self.retirement_in("LOCKING_ACCOUNT", created=day + datetime.timedelta(days=1))
        self.complete = self.retirement_in("COMPLETE", created=day + datetime.timedelta(days=2))
        self.day = day

    def send(self):
        return self.client.get(self.url)

    def names_in(self, response):
        """Return the registered names the page carries, in the order it carries them."""
        return [row["original_username"] for row in response.data["results"]]

    def test_the_page_carries_every_retirement_in_the_order_they_were_requested(self):
        self.login("worker")
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == PAGINATION_KEYS
        assert self.names_in(response) == [
            self.pending.original_username,
            self.locking.original_username,
            self.complete.original_username,
        ]
        assert response.data["count"] == 3
        assert response.data["num_pages"] == 1
        assert response.data["current_page"] == 1
        assert response.data["start"] == 0

    def test_a_row_carries_the_values_the_record_holds(self):
        self.login("worker")
        response = self.client.get(self.url, {"state": "LOCKING_ACCOUNT"})
        row = response.data["results"][0]
        assert set(row) == RETIREMENT_FIELDS
        assert row["id"] == self.locking.id
        assert row["original_username"] == self.locking.original_username
        assert row["original_email"] == self.locking.original_email
        assert row["original_name"] == self.locking.original_name
        assert row["retired_username"] == self.locking.retired_username
        assert row["retired_email"] == self.locking.retired_email
        assert row["current_state"] == "LOCKING_ACCOUNT"
        assert row["last_state"] == "LOCKING_ACCOUNT"
        assert row["user"] == {
            "id": self.locking.user.id,
            "username": self.locking.user.username,
            "email": self.locking.user.email,
            "profile": {"id": self.locking.user.profile.id, "name": self.locking.user.profile.name},
        }

    def test_the_page_holds_ten_rows_unless_a_size_is_asked_for(self):
        for _ in range(9):
            self.retirement_in("PENDING")
        self.login("worker")
        response = self.client.get(self.url)
        assert len(response.data["results"]) == DEFAULT_PAGE_SIZE
        assert response.data["count"] == 12
        assert response.data["num_pages"] == 2

    def test_a_page_larger_than_the_maximum_holds_the_maximum(self):
        for _ in range(MAXIMUM_PAGE_SIZE + 2):
            self.retirement_in("PENDING")
        self.login("worker")
        response = self.client.get(self.url, {"page_size": 500})
        assert len(response.data["results"]) == MAXIMUM_PAGE_SIZE
        assert response.data["count"] == MAXIMUM_PAGE_SIZE + 5

    def test_a_page_beyond_the_last_one_is_not_found(self):
        self.login("worker")
        assert_error_envelope(
            self.client.get(self.url, {"page": 9999}),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )

    def test_one_step_can_be_asked_for(self):
        self.login("worker")
        response = self.client.get(self.url, {"state": "COMPLETE"})
        assert self.names_in(response) == [self.complete.original_username]

    def test_several_steps_can_be_asked_for_at_once(self):
        self.login("worker")
        response = self.client.get(self.url, {"state": "PENDING,COMPLETE"})
        assert self.names_in(response) == [
            self.pending.original_username,
            self.complete.original_username,
        ]

    def test_several_steps_can_be_asked_for_by_repeating_the_parameter(self):
        """The list these rows were read from before named its steps that way."""
        self.login("worker")
        response = self.client.get(f"{self.url}?state=PENDING&state=COMPLETE")
        assert self.names_in(response) == [
            self.pending.original_username,
            self.complete.original_username,
        ]

    def test_a_list_and_a_repetition_name_their_steps_together(self):
        self.login("worker")
        response = self.client.get(f"{self.url}?state=PENDING,COMPLETE&state=LOCKING_ACCOUNT")
        assert self.names_in(response) == [
            self.pending.original_username,
            self.locking.original_username,
            self.complete.original_username,
        ]

    def test_naming_no_step_at_all_leaves_every_retirement_in(self):
        self.login("worker")
        response = self.client.get(f"{self.url}?state=")
        assert self.names_in(response) == [
            self.pending.original_username,
            self.locking.original_username,
            self.complete.original_username,
        ]

    def test_a_step_this_deployment_does_not_run_is_refused(self):
        self.login("worker")
        response = self.client.get(self.url, {"state": "NOPE"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert "No such pipeline step: NOPE." in envelope["errors"]["state"][0]

    def test_a_step_this_deployment_does_not_run_is_refused_among_repeated_ones(self):
        self.login("worker")
        envelope = assert_error_envelope(
            self.client.get(f"{self.url}?state=PENDING&state=NOPE"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert "No such pipeline step: NOPE." in envelope["errors"]["state"][0]

    def test_retirements_requested_recently_can_be_left_out(self):
        recent = self.retirement_in(
            "PENDING", created=datetime.datetime.now(ZoneInfo("UTC")) - datetime.timedelta(days=1),
        )
        self.login("worker")
        response = self.client.get(self.url, {"cool_off_days": 7, "page_size": 100})
        assert recent.original_username not in self.names_in(response)
        assert self.pending.original_username in self.names_in(response)

    def test_no_cooling_off_period_leaves_every_retirement_in(self):
        recent = self.retirement_in(
            "PENDING", created=datetime.datetime.now(ZoneInfo("UTC")) - datetime.timedelta(days=1),
        )
        self.login("worker")
        response = self.client.get(self.url, {"cool_off_days": 0, "page_size": 100})
        assert recent.original_username in self.names_in(response)

    def test_a_cooling_off_period_that_is_not_a_whole_number_of_days_is_refused(self):
        self.login("worker")
        for value in ("1.5", "nope"):
            with self.subTest(value=value):
                envelope = assert_error_envelope(
                    self.client.get(self.url, {"cool_off_days": value}),
                    expected_status=status.HTTP_400_BAD_REQUEST,
                    expected_type_slug="validation",
                )
                assert "cool_off_days" in envelope["errors"]

    def test_a_negative_cooling_off_period_is_refused(self):
        self.login("worker")
        envelope = assert_error_envelope(
            self.client.get(self.url, {"cool_off_days": -1}),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert "cool_off_days" in envelope["errors"]

    def test_the_later_bound_includes_the_moment_it_names(self):
        self.login("worker")
        response = self.client.get(
            self.url, {"created_after": self.locking.created.isoformat(), "page_size": 100},
        )
        assert self.names_in(response) == [
            self.locking.original_username,
            self.complete.original_username,
        ]

    def test_the_earlier_bound_leaves_out_the_moment_it_names(self):
        self.login("worker")
        response = self.client.get(
            self.url, {"created_before": self.locking.created.isoformat(), "page_size": 100},
        )
        assert self.names_in(response) == [self.pending.original_username]

    def test_a_day_as_the_earlier_bound_leaves_that_whole_day_out(self):
        """
        A date alone is the first moment of that day, which the bound excludes.

        The retirements requested during the day named are therefore not in the
        page, which is the one reading of the bound that holds whether the
        value names a day or a moment within one.
        """
        midday = self.retirement_in(
            "PENDING", created=self.locking.created + datetime.timedelta(hours=12),
        )
        self.login("worker")
        response = self.client.get(
            self.url,
            {"created_before": self.locking.created.date().isoformat(), "page_size": 100},
        )
        assert self.names_in(response) == [self.pending.original_username]
        assert midday.original_username not in self.names_in(response)
        later = self.client.get(
            self.url,
            {
                "created_before": (self.locking.created + datetime.timedelta(days=1)).date().isoformat(),
                "page_size": 100,
            },
        )
        assert self.names_in(later) == [
            self.pending.original_username,
            self.locking.original_username,
            midday.original_username,
        ]

    def test_a_bound_that_is_not_a_moment_is_refused(self):
        self.login("worker")
        envelope = assert_error_envelope(
            self.client.get(self.url, {"created_after": "the day before"}),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert "created_after" in envelope["errors"]

    def test_the_page_can_be_sorted_by_every_field_it_offers(self):
        self.login("worker")
        expected = {
            "original_username": sorted(
                [r.original_username for r in (self.pending, self.locking, self.complete)],
            ),
            "created": [
                self.pending.original_username,
                self.locking.original_username,
                self.complete.original_username,
            ],
            "modified": [
                self.pending.original_username,
                self.locking.original_username,
                self.complete.original_username,
            ],
            "current_state": [
                self.pending.original_username,
                self.locking.original_username,
                self.complete.original_username,
            ],
        }
        for field, order in expected.items():
            with self.subTest(field=field):
                response = self.client.get(self.url, {"ordering": field, "page_size": 100})
                assert self.names_in(response) == order
                reversed_response = self.client.get(
                    self.url, {"ordering": f"-{field}", "page_size": 100},
                )
                assert self.names_in(reversed_response) == list(reversed(order))

    def test_a_sort_field_the_page_does_not_offer_is_refused(self):
        self.login("worker")
        envelope = assert_error_envelope(
            self.client.get(self.url, {"ordering": "id"}),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert "ordering" in envelope["errors"]

    def test_a_query_parameter_the_page_does_not_read_is_refused(self):
        self.login("worker")
        envelope = assert_error_envelope(
            self.client.get(self.url, {"states": "PENDING"}),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"]["states"] == [
            f"No such query parameter. Send any of: {LIST_QUERY_PARAMS}.",
        ]

    def test_the_minimal_preset_carries_no_data_of_the_account_owner(self):
        self.login("worker")
        response = self.client.get(self.url, {"view": "minimal"})
        row = response.data["results"][0]
        assert set(row) == set(RETIREMENT_MINIMAL_FIELDS)
        assert set(row) & RETIREMENT_PII_FIELDS == set()
        assert row["original_username"] == self.pending.original_username
        assert row["current_state"] == "PENDING"

    def test_a_preset_the_page_does_not_answer_to_is_refused(self):
        self.login("worker")
        envelope = assert_error_envelope(
            self.client.get(self.url, {"view": "everything"}),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"]["view"] == [
            "Ask for any of: minimal, or omit this parameter for the default response.",
        ]

    def test_properties_can_be_selected(self):
        self.login("worker")
        response = self.client.get(self.url, {"fields": "original_username,current_state"})
        assert response.data["results"][0] == {
            "original_username": self.pending.original_username,
            "current_state": "PENDING",
        }

    def test_a_property_a_row_cannot_carry_is_refused(self):
        self.login("worker")
        envelope = assert_error_envelope(
            self.client.get(self.url, {"fields": "responses"}),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"]["fields"][0].startswith("No such field: responses.")

    def test_every_admitted_caller_sees_the_same_rows(self):
        """The visibility layer narrows nothing for a caller holding the capability."""
        seen = {}
        for caller in PIPELINE_CALLERS:
            self.client.logout()
            self.login(caller)
            seen[caller] = self.names_in(self.client.get(self.url, {"page_size": 100}))
        assert seen == {
            caller: [
                self.pending.original_username,
                self.locking.original_username,
                self.complete.original_username,
            ]
            for caller in PIPELINE_CALLERS
        }

    def test_the_page_is_readable_without_a_body(self):
        self.login("worker")
        response = self.client.head(self.url)
        assert response.status_code == status.HTTP_200_OK


class _NarrowingPolicy:
    """
    A visibility policy that keeps the rows named and records what it was given.

    It stands in for a deployment's own policy, so that the tests below read
    what an address answers with when the policy narrows rather than when it
    leaves the selection whole.
    """

    def __init__(self, column, *keep):
        self.column = column
        self.keep = keep
        #: The caller each application was made for, in order.
        self.subjects = []
        #: The values the selection held each time, before it was narrowed.
        self.offered = []

    def scope(self, queryset, subject):
        """Return the rows named, recording the caller and what was on offer."""
        self.subjects.append(subject)
        self.offered.append(sorted(queryset.values_list(self.column, flat=True)))
        return queryset.filter(**{f"{self.column}__in": self.keep})


#: Stands for a view class that declares no permission classes of its own.
NOT_DECLARED = object()


def _copied_by_the_middleware(view):
    """
    Return whether the classes on this view class are the shared ones with the middleware's added.

    The platform's own middleware appends its class to whatever it finds on
    every view class it serves, which leaves a copy on the view class itself.
    That copy is this and nothing else, so a view that declares classes of its
    own is told apart from one that has merely answered a request.
    """
    own = vars(view).get("permission_classes")
    if own is None:
        return False
    inherited = next(
        vars(base)["permission_classes"]
        for base in view.__mro__[1:]
        if "permission_classes" in vars(base)
    )
    return tuple(own) == (*inherited, NotJwtRestrictedApplication)


@contextlib.contextmanager
def layers_read_from_their_own_declaration(*views):
    """
    Serve these views with the permission classes the layer they share declares.

    The middleware's copy is dropped here and put back afterwards, so that what
    the views read is what is declared for them, as it would be on a service
    that has just started. A view declaring classes of its own keeps them,
    because that declaration is what these tests are here to see.
    """
    on_entry = {view: vars(view).get("permission_classes", NOT_DECLARED) for view in views}
    try:
        for view in views:
            if _copied_by_the_middleware(view):
                del view.permission_classes
        yield
    finally:
        # Each view class is left exactly as it was found, whatever the
        # middleware wrote onto it while these requests were being served.
        for view, declared in on_entry.items():
            if "permission_classes" in vars(view):
                del view.permission_classes
            if declared is not NOT_DECLARED:
                view.permission_classes = declared


class _OnlyASuperuser(permissions.BasePermission):
    """
    A rule a deployment might add to a resource, admitting one caller and refusing the rest.

    It stands in for whatever a deployment adds to the layer the operations on
    a resource share, so the tests below read which of those operations is
    asked for it.
    """

    def has_permission(self, request, view):
        """Return whether this caller is a superuser."""
        return bool(request.user and request.user.is_superuser)


@skip_unless_lms
class RetirementVisibilityTests(RetirementApiTestCase):
    """
    Which retirements a caller may see is decided by the view's own visibility policy.

    The policy this deployment runs keeps the queue whole, because every
    caller these addresses admit holds the capability to work the whole
    pipeline. A deployment that replaces it with one that narrows needs it
    applied where the selection is read, so the policy is replaced here with
    one that keeps two of three retirements and the page is read.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse("user_v2:retirement_list")
        self.pending = self.retirement_in("PENDING")
        self.locking = self.retirement_in("LOCKING_ACCOUNT")
        self.hidden = self.retirement_in("COMPLETE")
        self.policy = _NarrowingPolicy(
            "original_username", self.pending.original_username, self.locking.original_username,
        )

    def names_in(self, response):
        """Return the registered names the page carries."""
        return sorted(row["original_username"] for row in response.data["results"])

    def test_the_page_carries_the_retirements_the_policy_keeps_and_no_others(self):
        self.login("worker")
        with mock.patch.object(RetirementViewSet, "scoping_policy", self.policy):
            response = self.client.get(self.url, {"page_size": 100})
        assert response.status_code == status.HTTP_200_OK
        assert self.names_in(response) == sorted(
            [self.pending.original_username, self.locking.original_username],
        )
        assert response.data["count"] == 2

    def test_the_policy_is_given_the_caller_and_every_retirement_held(self):
        self.login("worker")
        with mock.patch.object(RetirementViewSet, "scoping_policy", self.policy):
            self.client.get(self.url, {"page_size": 100})
        assert [subject.username for subject in self.policy.subjects] == [WORKER_USERNAME]
        assert self.policy.offered == [sorted(
            [
                self.pending.original_username,
                self.locking.original_username,
                self.hidden.original_username,
            ],
        )]

    def test_what_the_policy_leaves_out_is_left_out_of_a_narrowed_page_too(self):
        """A filter narrows what the policy left; it cannot put a row back in."""
        self.login("worker")
        with mock.patch.object(RetirementViewSet, "scoping_policy", self.policy):
            response = self.client.get(self.url, {"state": "COMPLETE", "page_size": 100})
        assert self.names_in(response) == []
        assert response.data["count"] == 0

    def test_the_policy_this_deployment_runs_keeps_the_whole_queue(self):
        self.login("worker")
        response = self.client.get(self.url, {"page_size": 100})
        assert self.names_in(response) == sorted(
            [
                self.pending.original_username,
                self.locking.original_username,
                self.hidden.original_username,
            ],
        )


@skip_unless_lms
class PartnerReportVisibilityTests(RetirementApiTestCase):
    """The entries a caller may take from the partner-report queue are narrowed the same way."""

    def setUp(self):
        super().setUp()
        self.url = reverse("user_v2:retirement_partner_report_list")
        self.taken = self.queued(self.subject)
        self.hidden = self.queued(self.learner)
        self.policy = _NarrowingPolicy("original_username", self.subject.username)

    def queued(self, user):
        """Return a partner report entry waiting to be taken, for this user."""
        return UserRetirementPartnerReportingStatus.objects.create(
            user=user,
            original_username=user.username,
            original_email=user.email,
            original_name=user.profile.name,
        )

    def test_only_the_entries_the_policy_keeps_are_answered_with(self):
        self.login("worker")
        with mock.patch.object(RetirementPartnerReportViewSet, "scoping_policy", self.policy):
            response = self.client.post(self.url, {}, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert [entry["original_username"] for entry in response.data] == [self.subject.username]

    def test_an_entry_the_policy_leaves_out_is_not_marked_as_taken(self):
        self.login("worker")
        with mock.patch.object(RetirementPartnerReportViewSet, "scoping_policy", self.policy):
            self.client.post(self.url, {}, format="json")
        marked = set(
            UserRetirementPartnerReportingStatus.objects
            .filter(is_being_processed=True)
            .values_list("original_username", flat=True),
        )
        assert marked == {self.subject.username}

    def test_the_policy_is_given_the_caller_and_the_entries_nobody_has_taken(self):
        self.login("worker")
        with mock.patch.object(RetirementPartnerReportViewSet, "scoping_policy", self.policy):
            self.client.post(self.url, {}, format="json")
        assert [subject.username for subject in self.policy.subjects] == [WORKER_USERNAME]
        assert self.policy.offered == [
            sorted([self.subject.username, self.learner.username]),
        ]

    def test_the_policy_this_deployment_runs_leaves_every_queued_entry_takeable(self):
        self.login("worker")
        response = self.client.post(self.url, {}, format="json")
        assert sorted(entry["original_username"] for entry in response.data) == sorted(
            [self.subject.username, self.learner.username],
        )


@skip_unless_lms
class RetirementQueueOrderTests(RetirementApiTestCase):
    """
    The queue is read in a total order, so that paging through it sees each retirement once.

    Retirements that compare equal on the column a caller sorts by are left
    in whatever order the database answers with, and two requests need not
    agree on it, so the order the queue is worked in is appended to the sort
    asked for. That order is the retirement's own identifier, which rises as
    retirements are requested: it settles ties without being distinguishable
    from an unsettled read of rows stored in that order, so what these tests
    read is the order the database is asked for.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse("user_v2:retirement_list")
        day = datetime.datetime(2026, 3, 1, tzinfo=ZoneInfo("UTC"))
        self.shared = [self.retirement_in("PENDING", created=day) for _ in range(3)]
        self.login("worker")

    def ordering_asked_for(self, **params):
        """Return the order the page of retirements was read in."""
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(self.url, params)
        assert response.status_code == status.HTTP_200_OK, response.content
        table = UserRetirementStatus._meta.db_table
        reads = [
            query["sql"] for query in captured.captured_queries
            if query["sql"].startswith("SELECT") and "ORDER BY" in query["sql"]
            and connection.ops.quote_name(table) in query["sql"]
        ]
        assert len(reads) == 1, reads
        return reads[0].split("ORDER BY")[1].split("LIMIT")[0].strip()

    def column(self, name, direction="ASC"):
        """Return the one column of the retirement table, as a sort names it."""
        quoted = connection.ops.quote_name
        table = UserRetirementStatus._meta.db_table
        return f"{quoted(table)}.{quoted(name)} {direction}"

    def test_a_sort_a_caller_asks_for_is_completed_by_the_order_the_queue_is_worked_in(self):
        assert self.ordering_asked_for(ordering="created", page_size=100) == ", ".join(
            [self.column("created"), self.column("id")],
        )

    def test_the_order_the_queue_is_worked_in_is_added_to_a_reversed_sort_as_well(self):
        assert self.ordering_asked_for(ordering="-created", page_size=100) == ", ".join(
            [self.column("created", "DESC"), self.column("id")],
        )

    def test_a_page_nobody_asked_a_sort_for_is_read_in_the_order_the_queue_is_worked_in(self):
        """The order a page falls back to is the tie-break, so it is not asked for twice."""
        assert self.ordering_asked_for(page_size=100) == self.column("id")

    def test_paging_through_retirements_requested_at_one_moment_sees_each_once(self):
        listed = []
        for page in (1, 2, 3):
            response = self.client.get(
                self.url, {"ordering": "created", "page_size": 1, "page": page},
            )
            listed.extend(row["original_username"] for row in response.data["results"])
        assert listed == [retirement.original_username for retirement in self.shared]


@skip_unless_lms
class RetirementDetailTests(_AccessMatrixMixin, RetirementApiTestCase):
    """GET /api/user/v2/retirements/{username}/"""

    admitted = PIPELINE_CALLERS
    refused = PIPELINE_REFUSED

    def setUp(self):
        super().setUp()
        self.retirement = self.retirement_in("LOCKING_ACCOUNT", user=self.subject)
        self.url = reverse("user_v2:retirement_detail", kwargs={"username": "subject"})

    def send(self):
        return self.client.get(self.url)

    def test_the_record_comes_back_whole(self):
        self.login("worker")
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == RETIREMENT_FIELDS
        assert response.data["id"] == self.retirement.id
        assert response.data["original_username"] == "subject"
        assert response.data["original_email"] == "subject@example.com"
        assert response.data["current_state"] == "LOCKING_ACCOUNT"

    def test_an_account_holding_no_profile_is_answered_with_a_null_profile(self):
        """The property is answered under its own name either way, as null where there is none."""
        UserProfile.objects.filter(user=self.subject).delete()
        self.login("worker")
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["user"] == {
            "id": self.subject.id,
            "username": "subject",
            "email": "subject@example.com",
            "profile": None,
        }
        page = self.client.get(reverse("user_v2:retirement_list"))
        assert [row["user"]["profile"] for row in page.data["results"]] == [None]

    def test_the_hashed_name_a_retired_account_is_moved_to_addresses_the_same_record(self):
        hashed = get_retired_username_by_username("subject")
        self.subject.username = hashed
        self.subject.save()
        self.login("worker")
        response = self.client.get(
            reverse("user_v2:retirement_detail", kwargs={"username": "subject"}),
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == self.retirement.id
        by_hash = self.client.get(
            reverse("user_v2:retirement_detail", kwargs={"username": hashed}),
        )
        assert by_hash.status_code == status.HTTP_200_OK
        assert by_hash.data["id"] == self.retirement.id

    def test_a_name_no_retirement_is_held_for_is_not_found(self):
        self.login("worker")
        envelope = assert_error_envelope(
            self.client.get(reverse("user_v2:retirement_detail", kwargs={"username": "nobody"})),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )
        assert envelope["detail"] == view_services.NO_SUCH_RETIREMENT

    def test_an_account_with_no_retirement_behind_it_is_not_found(self):
        self.login("worker")
        assert_error_envelope(
            self.client.get(
                reverse("user_v2:retirement_detail", kwargs={"username": "learner-caller"}),
            ),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )

    def test_the_minimal_preset_carries_no_data_of_the_account_owner(self):
        self.login("worker")
        response = self.client.get(self.url, {"view": "minimal"})
        assert set(response.data) == set(RETIREMENT_MINIMAL_FIELDS)
        assert set(response.data) & RETIREMENT_PII_FIELDS == set()

    def test_properties_can_be_selected(self):
        self.login("worker")
        response = self.client.get(self.url, {"fields": "current_state"})
        assert response.data == {"current_state": "LOCKING_ACCOUNT"}

    def test_a_property_asked_for_as_a_path_is_refused(self):
        """
        Selection reaches the top level only, which is what the parameter's description says.

        A name written with a dot in it names no property of the record, so it
        is refused rather than read as the property it starts with: answering
        that would hand back the whole of the embedded account to a caller
        that asked for one property of it, which here is personal data of the
        account being retired.
        """
        self.login("worker")
        response = self.client.get(self.url, {"fields": "user.email"})
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug="validation",
        )
        assert envelope["errors"]["fields"] == [
            "No such field: user.email. Select any of: "
            f"{', '.join(sorted(RetirementSerializer().fields))}.",
        ]
        assert "user" not in response.data

    def test_the_parameters_that_cut_a_page_are_not_read_at_a_member_address(self):
        self.login("worker")
        envelope = assert_error_envelope(
            self.client.get(self.url, {"page": 2, "state": "PENDING"}),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert sorted(envelope["errors"]) == ["page", "state"]
        assert envelope["errors"]["page"] == [
            f"No such query parameter. Send any of: {DETAIL_QUERY_PARAMS}.",
        ]

    def test_a_refusal_is_answered_as_json_rather_than_as_a_page(self):
        self.login("worker")
        response = self.client.get(reverse("user_v2:retirement_detail", kwargs={"username": "nobody"}))
        assert response["Content-Type"] == "application/json"

    def test_the_record_is_readable_without_a_body(self):
        self.login("worker")
        assert self.client.head(self.url).status_code == status.HTTP_200_OK


@skip_unless_lms
class RetirementUpdateTests(_AccessMatrixMixin, RetirementApiTestCase):
    """PATCH /api/user/v2/retirements/{username}/"""

    admitted = PIPELINE_CALLERS
    refused = PIPELINE_REFUSED

    def setUp(self):
        super().setUp()
        self.retirement = self.retirement_in("LOCKING_ACCOUNT", user=self.subject)
        self.url = reverse("user_v2:retirement_detail", kwargs={"username": "subject"})

    def send(self):
        """Move a retirement of its own, so that one caller's move does not decide the next one's."""
        retirement = self.retirement_in("LOCKING_ACCOUNT")
        return self.client.patch(
            reverse("user_v2:retirement_detail", kwargs={"username": retirement.original_username}),
            {"new_state": "LOCKING_COMPLETE", "response": "done"},
            format="json",
        )

    def patch_subject(self, body, **kwargs):
        """Ask for a move of the retirement this address names."""
        return self.client.patch(self.url, body, format="json", **kwargs)

    def test_a_later_step_moves_the_retirement_and_is_logged(self):
        self.login("worker")
        response = self.patch_subject({"new_state": "LOCKING_COMPLETE", "response": "account locked"})
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert response.data is None
        self.retirement.refresh_from_db()
        assert self.retirement.current_state.state_name == "LOCKING_COMPLETE"
        assert self.retirement.last_state.state_name == "LOCKING_ACCOUNT"
        assert "account locked" in self.retirement.responses

    def test_an_earlier_step_is_refused_and_the_retirement_does_not_move(self):
        self.login("worker")
        envelope = assert_error_envelope(
            self.patch_subject({"new_state": "PENDING", "response": "rewind"}),
            expected_status=status.HTTP_409_CONFLICT,
            expected_type_slug=STATE_CONFLICT_SLUG,
        )
        assert envelope["detail"] == view_services.CANNOT_MOVE_TO_STEP
        self.retirement.refresh_from_db()
        assert self.retirement.current_state.state_name == "LOCKING_ACCOUNT"

    def test_an_earlier_step_moves_the_retirement_when_the_move_is_forced(self):
        self.login("worker")
        response = self.patch_subject({"new_state": "PENDING", "response": "rewind", "force": True})
        assert response.status_code == status.HTTP_204_NO_CONTENT
        self.retirement.refresh_from_db()
        assert self.retirement.current_state.state_name == "PENDING"

    def test_a_retirement_that_errored_can_be_moved_on_to_a_later_step(self):
        """An operator rewinds and re-runs a retirement that failed, which is what this allows."""
        self.retirement.current_state = self.state("ERRORED")
        self.retirement.save()
        self.login("worker")
        assert self.patch_subject(
            {"new_state": "COMPLETE", "response": "finished by hand"},
        ).status_code == status.HTTP_204_NO_CONTENT
        self.retirement.refresh_from_db()
        assert self.retirement.current_state.state_name == "COMPLETE"

    def test_a_retirement_that_errored_is_not_moved_back_to_an_earlier_step_unless_forced(self):
        self.retirement.current_state = self.state("ERRORED")
        self.retirement.save()
        self.login("worker")
        assert_error_envelope(
            self.patch_subject({"new_state": "PENDING", "response": "rewind"}),
            expected_status=status.HTTP_409_CONFLICT,
            expected_type_slug=STATE_CONFLICT_SLUG,
        )
        self.retirement.refresh_from_db()
        assert self.retirement.current_state.state_name == "ERRORED"

    def test_a_step_this_deployment_does_not_run_is_refused_before_anything_moves(self):
        self.login("worker")
        envelope = assert_error_envelope(
            self.patch_subject({"new_state": "NOPE", "response": "done"}),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"]["new_state"][0].startswith("No such pipeline step.")
        self.retirement.refresh_from_db()
        assert self.retirement.current_state.state_name == "LOCKING_ACCOUNT"

    def test_a_forced_move_to_a_step_this_deployment_does_not_run_is_refused_too(self):
        self.login("worker")
        assert_error_envelope(
            self.patch_subject({"new_state": "NOPE", "response": "done", "force": True}),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )

    def test_a_body_that_names_no_step_is_refused(self):
        self.login("worker")
        envelope = assert_error_envelope(
            self.patch_subject({"response": "done"}),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert "new_state" in envelope["errors"]

    def test_a_body_that_says_nothing_about_the_step_being_left_is_refused(self):
        self.login("worker")
        envelope = assert_error_envelope(
            self.patch_subject({"new_state": "LOCKING_COMPLETE"}),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert "response" in envelope["errors"]

    def test_an_empty_body_is_refused_for_both_of_the_values_a_move_needs(self):
        """The two it names here are the two the published body says it cannot be sent without."""
        self.login("worker")
        envelope = assert_error_envelope(
            self.patch_subject({}),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert sorted(envelope["errors"]) == MOVE_REQUIRED_VALUES
        self.retirement.refresh_from_db()
        assert self.retirement.current_state.state_name == "LOCKING_ACCOUNT"

    def test_a_name_spelled_differently_from_the_registered_one_is_not_found(self):
        self.login("worker")
        assert_error_envelope(
            self.client.patch(
                reverse("user_v2:retirement_detail", kwargs={"username": "Subject"}),
                {"new_state": "LOCKING_COMPLETE", "response": "done"},
                format="json",
            ),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )
        self.retirement.refresh_from_db()
        assert self.retirement.current_state.state_name == "LOCKING_ACCOUNT"

    def test_the_hashed_name_does_not_address_a_write(self):
        hashed = get_retired_username_by_username("subject")
        self.login("worker")
        assert_error_envelope(
            self.client.patch(
                reverse("user_v2:retirement_detail", kwargs={"username": hashed}),
                {"new_state": "LOCKING_COMPLETE", "response": "done"},
                format="json",
            ),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )

    def test_a_body_in_a_media_type_this_address_cannot_read_is_refused(self):
        self.login("worker")
        assert_error_envelope(
            self.client.patch(self.url, "new_state=LOCKING_COMPLETE", content_type="text/plain"),
            expected_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            expected_type_slug=UNSUPPORTED_MEDIA_TYPE_SLUG,
        )

    def test_a_body_that_is_not_well_formed_is_refused(self):
        self.login("worker")
        assert_error_envelope(
            self.client.patch(self.url, "{not json", content_type="application/json"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug=MALFORMED_BODY_SLUG,
        )

    def test_a_query_parameter_a_write_does_not_read_is_refused(self):
        self.login("worker")
        envelope = assert_error_envelope(
            self.patch_subject({"new_state": "LOCKING_COMPLETE", "response": "done"}, QUERY_STRING="view=minimal"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"]["view"] == [
            f"No such query parameter. Send any of: {WRITE_QUERY_PARAMS}.",
        ]


@skip_unless_lms
class MovedRetirementRecordTests(RetirementApiTestCase):
    """
    A record that has moved names the step it stands at and the step it came from.

    They are two different steps once a retirement has moved, and a caller
    driving the pipeline reads the second to tell what has already been done,
    so the record is read here after a move rather than before one, when both
    name the step the retirement was requested at.
    """

    def setUp(self):
        super().setUp()
        self.retirement = self.retirement_in("LOCKING_ACCOUNT", user=self.subject)
        self.detail_url = reverse("user_v2:retirement_detail", kwargs={"username": "subject"})
        self.list_url = reverse("user_v2:retirement_list")
        self.login("worker")
        moved = self.client.patch(
            self.detail_url,
            {"new_state": "LOCKING_COMPLETE", "response": "the account was locked"},
            format="json",
        )
        assert moved.status_code == status.HTTP_204_NO_CONTENT, moved.content
        self.retirement.refresh_from_db()

    def test_the_record_names_the_step_it_stands_at_and_the_one_it_came_from(self):
        response = self.client.get(self.detail_url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["current_state"] == "LOCKING_COMPLETE"
        assert response.data["last_state"] == "LOCKING_ACCOUNT"

    def test_the_row_in_the_queue_names_both_steps_as_the_record_does(self):
        response = self.client.get(self.list_url, {"page_size": 100})
        row = next(
            row for row in response.data["results"]
            if row["original_username"] == self.retirement.original_username
        )
        assert row["current_state"] == "LOCKING_COMPLETE"
        assert row["last_state"] == "LOCKING_ACCOUNT"

    def test_the_step_a_record_came_from_can_be_asked_for_on_its_own(self):
        response = self.client.get(self.detail_url, {"fields": "last_state"})
        assert response.data == {"last_state": "LOCKING_ACCOUNT"}

    def test_the_minimal_record_names_the_step_it_stands_at(self):
        """What a reader of the queue needs is where the retirement is now."""
        response = self.client.get(self.detail_url, {"view": "minimal"})
        assert response.data["current_state"] == "LOCKING_COMPLETE"
        assert "last_state" not in response.data

    def test_the_queue_is_narrowed_by_the_step_a_retirement_stands_at(self):
        """A retirement is found under the step it is at, not under the one it left."""
        by_current = self.client.get(self.list_url, {"state": "LOCKING_COMPLETE", "page_size": 100})
        assert [row["original_username"] for row in by_current.data["results"]] == [
            self.retirement.original_username,
        ]
        by_previous = self.client.get(self.list_url, {"state": "LOCKING_ACCOUNT", "page_size": 100})
        assert by_previous.data["results"] == []


@skip_unless_lms
class RetirementCancelTests(_AccessMatrixMixin, RetirementApiTestCase):
    """DELETE /api/user/v2/retirements/{username}/"""

    admitted = CANCEL_CALLERS
    refused = CANCEL_REFUSED

    def send(self):
        user = UserFactory.create(email="to-cancel@example.com")
        create_retirement_status(user)
        return self.client.delete(
            reverse("user_v2:retirement_detail", kwargs={"username": user.username}),
        )

    def test_the_request_is_gone_and_the_account_is_usable_again(self):
        retirement = create_retirement_status(self.subject)
        self.subject.email = retirement.retired_email
        self.subject.save()
        self.login("canceller")
        response = self.client.delete(
            reverse("user_v2:retirement_detail", kwargs={"username": "subject"}),
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not UserRetirementStatus.objects.filter(id=retirement.id).exists()
        assert not UserRetirementRequest.objects.filter(user=self.subject).exists()
        self.subject.refresh_from_db()
        assert self.subject.email == "subject@example.com"
        assert not self.subject.check_password(PASSWORD)

    def test_a_retirement_the_pipeline_has_started_on_is_not_cancelled(self):
        retirement = self.retirement_in("LOCKING_ACCOUNT", user=self.subject)
        self.login("canceller")
        envelope = assert_error_envelope(
            self.client.delete(reverse("user_v2:retirement_detail", kwargs={"username": "subject"})),
            expected_status=status.HTTP_409_CONFLICT,
            expected_type_slug=STATE_CONFLICT_SLUG,
        )
        assert envelope["detail"] == view_services.ONLY_PENDING_IS_CANCELLED
        assert UserRetirementStatus.objects.filter(id=retirement.id).exists()

    def test_a_name_no_retirement_is_requested_under_is_not_found(self):
        self.login("canceller")
        assert_error_envelope(
            self.client.delete(reverse("user_v2:retirement_detail", kwargs={"username": "nobody"})),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )

    def test_a_name_spelled_differently_from_the_registered_one_is_not_found(self):
        retirement = create_retirement_status(self.subject)
        self.login("canceller")
        assert_error_envelope(
            self.client.delete(reverse("user_v2:retirement_detail", kwargs={"username": "SUBJECT"})),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )
        assert UserRetirementStatus.objects.filter(id=retirement.id).exists()


@skip_unless_lms
class RetirementErasureTests(_AccessMatrixMixin, RetirementApiTestCase):
    """POST /api/user/v2/retirements/{username}/erasures/"""

    admitted = PIPELINE_CALLERS
    refused = PIPELINE_REFUSED

    def setUp(self):
        super().setUp()
        self.retirement = self.retirement_in("RETIRING_LMS", user=self.subject)
        self.url = reverse("user_v2:retirement_erasure", kwargs={"username": "subject"})

    def send(self, scope="lms_core"):
        return self.client.post(self.url, {"scope": scope}, format="json")

    def test_the_core_scope_erases_the_account_and_its_profile(self):
        self.subject.profile.name = "Subject Name"
        self.subject.profile.city = "Somewhere"
        self.subject.profile.save()
        PendingEmailChange.objects.create(
            user=self.subject, new_email="new@example.com", activation_key="k" * 32,
        )
        UserOrgTag.objects.create(user=self.subject, key="foo", value="bar", org="edX")
        AccountRecovery.objects.create(
            user=self.subject, secondary_email="recovery@example.com", is_active=True,
        )
        CourseEnrollmentAllowed.objects.create(email="subject@example.com", course_id=COURSE_KEY)
        cache.set(UserProfile.country_cache_key_name(self.subject.id), "Timor-leste")

        self.login("worker")
        assert self.send("lms_core").status_code == status.HTTP_204_NO_CONTENT

        self.subject.refresh_from_db()
        assert self.subject.username == self.retirement.retired_username
        assert self.subject.email == self.retirement.retired_email
        assert self.subject.first_name == ""
        assert self.subject.last_name == ""
        assert self.subject.is_active is False
        assert self.subject.profile.name == ""
        assert self.subject.profile.city is None
        assert not PendingEmailChange.objects.filter(user=self.subject).exists()
        assert not UserOrgTag.objects.filter(user=self.subject).exists()
        assert not AccountRecovery.objects.filter(user=self.subject).exists()
        assert not CourseEnrollmentAllowed.objects.filter(email="subject@example.com").exists()
        assert cache.get(UserProfile.country_cache_key_name(self.subject.id)) is None

    def test_the_miscellaneous_scope_erases_what_the_features_around_the_account_hold(self):
        PendingNameChange.objects.create(user=self.subject, new_name="Another Name", rationale="x")
        self.login("worker")
        assert self.send("lms_misc").status_code == status.HTTP_204_NO_CONTENT
        assert not PendingNameChange.objects.filter(user=self.subject).exists()
        self.subject.refresh_from_db()
        # The miscellaneous scope leaves the account itself alone.
        assert self.subject.username == "subject"
        assert self.subject.is_active is True

    def test_a_second_run_works_the_replacement_name_out_from_the_registered_name(self):
        """
        The record holds no replacement name of its own, so the step works one out.

        It works it out from the name the account was registered under, which the record keeps,
        rather than from the name the account holds, which this very step has already replaced.
        """
        UserRetirementStatus.objects.filter(id=self.retirement.id).update(retired_username="")
        announced = []
        USER_RETIRE_LMS_CRITICAL.connect(
            lambda sender, **kwargs: announced.append(kwargs["retired_username"]),
            weak=False,
            dispatch_uid="replacement-name-of-each-run",
        )
        self.addCleanup(
            USER_RETIRE_LMS_CRITICAL.disconnect, dispatch_uid="replacement-name-of-each-run",
        )
        self.login("worker")
        assert self.send("lms_core").status_code == status.HTTP_204_NO_CONTENT
        self.subject.refresh_from_db()
        written = self.subject.username
        assert written == get_retired_username_by_username(self.retirement.original_username)
        assert self.send("lms_core").status_code == status.HTTP_204_NO_CONTENT
        self.subject.refresh_from_db()
        assert self.subject.username == written
        assert announced == [written, written]

    def test_the_erasure_does_not_move_the_retirement(self):
        self.login("worker")
        self.send("lms_core")
        self.retirement.refresh_from_db()
        assert self.retirement.current_state.state_name == "RETIRING_LMS"

    def test_a_body_that_names_no_scope_is_refused_and_nothing_is_erased(self):
        self.login("worker")
        envelope = assert_error_envelope(
            self.client.post(self.url, {}, format="json"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert "scope" in envelope["errors"]
        self.subject.refresh_from_db()
        assert self.subject.username == "subject"

    def test_a_scope_this_address_does_not_erase_is_refused_and_nothing_is_erased(self):
        self.login("worker")
        envelope = assert_error_envelope(
            self.send("everything"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert "scope" in envelope["errors"]
        self.subject.refresh_from_db()
        assert self.subject.username == "subject"

    def test_a_retirement_waiting_for_the_learner_is_not_erased(self):
        self.retirement.current_state = self.state("PENDING")
        self.retirement.save()
        self.login("worker")
        envelope = assert_error_envelope(
            self.send(),
            expected_status=status.HTTP_409_CONFLICT,
            expected_type_slug=STATE_CONFLICT_SLUG,
        )
        assert envelope["detail"] == view_services.NOT_OPEN_TO_ACTION
        self.subject.refresh_from_db()
        assert self.subject.username == "subject"

    def test_a_retirement_at_a_step_already_recorded_as_finished_is_not_erased(self):
        self.retirement.current_state = self.state("LMS_COMPLETE")
        self.retirement.save()
        self.login("worker")
        assert_error_envelope(
            self.send(),
            expected_status=status.HTTP_409_CONFLICT,
            expected_type_slug=STATE_CONFLICT_SLUG,
        )

    def test_a_retirement_at_a_dead_end_step_is_not_erased(self):
        self.retirement.current_state = self.state("ERRORED")
        self.retirement.save()
        self.login("worker")
        assert_error_envelope(
            self.send(),
            expected_status=status.HTTP_409_CONFLICT,
            expected_type_slug=STATE_CONFLICT_SLUG,
        )

    def test_a_name_no_retirement_is_requested_under_is_not_found(self):
        self.login("worker")
        assert_error_envelope(
            self.client.post(
                reverse("user_v2:retirement_erasure", kwargs={"username": "nobody"}),
                {"scope": "lms_core"},
                format="json",
            ),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )

    def test_a_step_that_fails_partway_through_says_nothing_about_why(self):
        self.login("worker")
        with mock.patch(
            "openedx.core.djangoapps.user_api.v2.view_services.AccountRetirementView"
            ".clear_pii_from_certificate_records",
            side_effect=ValueError("the certificate store is on fire"),
        ):
            response = self.client.post(self.url, {"scope": "lms_core"}, format="json")
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        body = response.json()
        # The answer carries the same envelope as every other refusal of this
        # address, which is what the document publishes for it.
        assert body == {
            "type": error_type_uri(INTERNAL_ERROR_SLUG),
            "title": INTERNAL_ERROR_TITLE,
            "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "detail": "An unexpected error occurred. Please try again later.",
            "instance": self.url,
        }
        assert "fire" not in str(body)


@skip_unless_lms
class PartnerReportClaimTests(_AccessMatrixMixin, RetirementApiTestCase):
    """POST /api/user/v2/retirement_partner_reports/"""

    admitted = PIPELINE_CALLERS
    refused = PIPELINE_REFUSED

    def setUp(self):
        super().setUp()
        self.url = reverse("user_v2:retirement_partner_report_list")
        self.entry = self.queued(self.subject)

    def queued(self, user):
        """Return a partner report entry waiting to be taken, for this user."""
        return UserRetirementPartnerReportingStatus.objects.create(
            user=user,
            original_username=user.username,
            original_email=user.email,
            original_name=user.profile.name,
        )

    def send(self):
        return self.client.post(self.url, {}, format="json")

    def test_the_entry_carries_what_a_partner_needs(self):
        CourseEnrollmentFactory.create(user=self.subject, course_id=COURSE_KEY)
        CourseEnrollmentFactory.create(user=self.subject, course_id=OTHER_COURSE_KEY)
        self.login("worker")
        response = self.client.post(self.url, {}, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        entry = response.data[0]
        assert set(entry) == {
            "user_id", "original_username", "original_email", "original_name", "orgs", "created",
        }
        assert entry["user_id"] == self.subject.id
        assert entry["original_username"] == "subject"
        assert entry["original_email"] == "subject@example.com"
        assert entry["orgs"] == ["OtherOrg", "edX"]

    def test_an_account_enrolled_with_nobody_carries_no_organizations(self):
        self.login("worker")
        response = self.client.post(self.url, {}, format="json")
        assert response.data[0]["orgs"] == []

    def test_the_entries_returned_are_exactly_the_entries_taken(self):
        other = self.queued(self.learner)
        self.login("worker")
        response = self.client.post(self.url, {}, format="json")
        returned = {entry["original_username"] for entry in response.data}
        assert returned == {"subject", "learner-caller"}
        taken = set(
            UserRetirementPartnerReportingStatus.objects
            .filter(is_being_processed=True)
            .values_list("original_username", flat=True),
        )
        assert taken == returned
        assert UserRetirementPartnerReportingStatus.objects.get(id=other.id).is_being_processed

    def test_a_second_request_takes_nothing(self):
        self.login("worker")
        self.client.post(self.url, {}, format="json")
        again = self.client.post(self.url, {}, format="json")
        assert again.status_code == status.HTTP_200_OK
        assert again.data == []

    def test_an_entry_added_while_the_answer_is_built_is_left_for_the_next_caller(self):
        added = []

        def add_one_entry_behind_the_reader(report):
            if not added:
                added.append(self.queued(self.learner))
            return original(report)

        original = view_services._partner_report_of  # pylint: disable=protected-access
        self.login("worker")
        with mock.patch.object(
            view_services, "_partner_report_of", side_effect=add_one_entry_behind_the_reader,
        ):
            response = self.client.post(self.url, {}, format="json")

        assert [entry["original_username"] for entry in response.data] == ["subject"]
        latecomer = UserRetirementPartnerReportingStatus.objects.get(id=added[0].id)
        assert latecomer.is_being_processed is False
        assert UserRetirementPartnerReportingStatus.objects.get(id=self.entry.id).is_being_processed

    def test_at_most_the_number_of_entries_asked_for_is_taken(self):
        self.queued(self.learner)
        self.login("worker")
        response = self.client.post(f"{self.url}?limit=1", {}, format="json")
        assert len(response.data) == 1
        assert UserRetirementPartnerReportingStatus.objects.filter(is_being_processed=True).count() == 1

    def test_taking_no_entries_at_all_is_refused(self):
        self.login("worker")
        envelope = assert_error_envelope(
            self.client.post(f"{self.url}?limit=0", {}, format="json"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert "limit" in envelope["errors"]
        assert UserRetirementPartnerReportingStatus.objects.filter(is_being_processed=True).count() == 0

    def test_a_query_parameter_this_address_does_not_read_is_refused(self):
        self.login("worker")
        envelope = assert_error_envelope(
            self.client.post(f"{self.url}?count=1", {}, format="json"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"]["count"] == [
            f"No such query parameter. Send any of: {CLAIM_QUERY_PARAMS}.",
        ]


@skip_unless_lms
class PartnerReportQueueTests(_AccessMatrixMixin, RetirementApiTestCase):
    """PUT /api/user/v2/retirement_partner_reports/{username}/"""

    admitted = PIPELINE_CALLERS
    refused = PIPELINE_REFUSED

    def setUp(self):
        super().setUp()
        self.retirement = self.retirement_in("ADDING_TO_PARTNER_QUEUE", user=self.subject)
        CourseEnrollmentFactory.create(user=self.subject, course_id=COURSE_KEY)
        self.url = reverse("user_v2:retirement_partner_report_detail", kwargs={"username": "subject"})

    def send(self):
        return self.client.put(self.url, {}, format="json")

    def test_an_account_that_studied_with_an_organization_is_queued(self):
        self.login("worker")
        assert self.send().status_code == status.HTTP_204_NO_CONTENT
        entry = UserRetirementPartnerReportingStatus.objects.get(user=self.subject)
        assert entry.original_username == "subject"
        assert entry.original_email == "subject@example.com"
        assert entry.original_name == self.subject.profile.name
        assert entry.is_being_processed is False

    def test_an_account_that_studied_with_nobody_is_not_queued(self):
        learner_retirement = self.retirement_in("ADDING_TO_PARTNER_QUEUE", user=self.learner)
        self.login("worker")
        response = self.client.put(
            reverse(
                "user_v2:retirement_partner_report_detail",
                kwargs={"username": learner_retirement.original_username},
            ),
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not UserRetirementPartnerReportingStatus.objects.filter(user=self.learner).exists()

    def test_queueing_the_same_account_twice_leaves_one_entry(self):
        self.login("worker")
        assert self.send().status_code == status.HTTP_204_NO_CONTENT
        assert self.send().status_code == status.HTTP_204_NO_CONTENT
        assert UserRetirementPartnerReportingStatus.objects.filter(user=self.subject).count() == 1

    def test_a_name_no_retirement_is_requested_under_is_not_found(self):
        self.login("worker")
        assert_error_envelope(
            self.client.put(
                reverse("user_v2:retirement_partner_report_detail", kwargs={"username": "nobody"}),
                {},
                format="json",
            ),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )

    def test_a_retirement_at_a_step_that_admits_no_work_is_refused(self):
        self.retirement.current_state = self.state("PENDING")
        self.retirement.save()
        self.login("worker")
        assert_error_envelope(
            self.send(),
            expected_status=status.HTTP_409_CONFLICT,
            expected_type_slug=STATE_CONFLICT_SLUG,
        )
        assert not UserRetirementPartnerReportingStatus.objects.filter(user=self.subject).exists()

    def test_a_name_spelled_differently_from_the_registered_one_is_not_found(self):
        self.login("worker")
        assert_error_envelope(
            self.client.put(
                reverse("user_v2:retirement_partner_report_detail", kwargs={"username": "Subject"}),
                {},
                format="json",
            ),
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )

    def test_how_many_entries_to_take_is_not_read_where_none_are_taken(self):
        self.login("worker")
        envelope = assert_error_envelope(
            self.client.put(f"{self.url}?limit=1", {}, format="json"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"]["limit"] == [
            f"No such query parameter. Send any of: {WRITE_QUERY_PARAMS}.",
        ]


class _BatchTestsMixin(_AccessMatrixMixin):
    """What both batch disposals answer, whichever records they act on."""

    admitted = PIPELINE_CALLERS
    refused = PIPELINE_REFUSED

    def post(self, usernames):
        """Name these accounts to the address under test."""
        return self.client.post(self.url, {"usernames": usernames}, format="json")

    def test_a_body_naming_no_account_is_refused(self):
        self.login("worker")
        envelope = assert_error_envelope(
            self.post([]),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert "usernames" in envelope["errors"]

    def test_a_body_naming_more_accounts_than_a_batch_carries_is_refused(self):
        self.login("worker")
        envelope = assert_error_envelope(
            self.post([f"name-{index}" for index in range(MAXIMUM_PAGE_SIZE + 1)]),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert "usernames" in envelope["errors"]

    def test_a_body_that_is_not_an_object_is_refused(self):
        self.login("worker")
        assert_error_envelope(
            self.client.post(self.url, [{"original_username": "subject"}], format="json"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )

    def test_a_body_in_a_media_type_this_address_cannot_read_is_refused(self):
        self.login("worker")
        assert_error_envelope(
            self.client.post(self.url, "usernames=subject", content_type="text/plain"),
            expected_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            expected_type_slug=UNSUPPORTED_MEDIA_TYPE_SLUG,
        )

    def test_a_query_parameter_this_address_does_not_read_is_refused(self):
        self.login("worker")
        envelope = assert_error_envelope(
            self.client.post(f"{self.url}?limit=1", {"usernames": ["subject"]}, format="json"),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug="validation",
        )
        assert envelope["errors"]["limit"] == [
            f"No such query parameter. Send any of: {WRITE_QUERY_PARAMS}.",
        ]


@skip_unless_lms
class RetirementCleanupTests(_BatchTestsMixin, RetirementApiTestCase):
    """POST /api/user/v2/retirement_cleanups/"""

    def setUp(self):
        super().setUp()
        self.url = reverse("user_v2:retirement_cleanup_list")
        self.finished = self.retirement_in("COMPLETE", user=self.subject)

    def send(self):
        retirement = self.retirement_in("COMPLETE")
        return self.post([retirement.original_username])

    def test_a_finished_record_is_redacted_and_then_deleted(self):
        self.login("worker")
        response = self.post(["subject"])
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"results": [{"username": "subject", "outcome": "deleted"}]}
        assert not UserRetirementStatus.objects.filter(id=self.finished.id).exists()

    def test_the_record_carries_the_replacement_before_it_goes(self):
        """The redaction and the delete are separate statements, so the replacement is written."""
        self.login("worker")
        seen = []
        original = UserRetirementStatus.objects.filter

        def remember_the_values_the_row_holds(*args, **kwargs):
            selection = original(*args, **kwargs)
            seen.append(list(selection.values_list("original_username", "original_email")))
            return selection

        with mock.patch.object(
            UserRetirementStatus.objects, "filter", side_effect=remember_the_values_the_row_holds,
        ):
            self.post(["subject"])
        # The last selection is the delete, which sees the written-over values.
        assert seen[-1] == [(view_services.REDACTED_VALUE, view_services.REDACTED_EMAIL)]

    def test_a_record_moved_off_that_step_between_the_two_statements_is_left(self):
        """The caller was answered about a finished record, so only a finished record goes."""
        self.login("worker")
        selections = []
        original = UserRetirementStatus.objects.filter

        def move_the_record_before_the_delete_runs(*args, **kwargs):
            selection = original(*args, **kwargs)
            selections.append(selection)
            if len(selections) == 2:
                forced = UserRetirementStatus.objects.get(id=self.finished.id)
                forced.current_state = self.state("ERRORED")
                forced.save()
            return selection

        with mock.patch.object(
            UserRetirementStatus.objects, "filter",
            side_effect=move_the_record_before_the_delete_runs,
        ):
            answered = self.post(["subject"])
        assert answered.status_code == status.HTTP_200_OK
        left = UserRetirementStatus.objects.get(id=self.finished.id)
        assert left.current_state.state_name == "ERRORED"

    def test_a_repeat_of_a_request_already_carried_out_is_answered(self):
        self.login("worker")
        self.post(["subject"])
        again = self.post(["subject"])
        assert again.status_code == status.HTTP_200_OK
        assert again.data == {"results": [{"username": "subject", "outcome": "already_absent"}]}

    def test_each_name_is_reported_in_the_order_it_was_given(self):
        other = self.retirement_in("COMPLETE")
        self.login("worker")
        response = self.post([other.original_username, "nobody", "subject"])
        assert response.data == {
            "results": [
                {"username": other.original_username, "outcome": "deleted"},
                {"username": "nobody", "outcome": "already_absent"},
                {"username": "subject", "outcome": "deleted"},
            ],
        }

    def test_a_name_given_twice_is_acted_on_and_reported_once(self):
        self.login("worker")
        response = self.post(["subject", "subject"])
        assert response.data == {"results": [{"username": "subject", "outcome": "deleted"}]}

    def test_an_unfinished_record_refuses_the_whole_batch_and_nothing_is_deleted(self):
        unfinished = self.retirement_in("LOCKING_ACCOUNT")
        self.login("worker")
        envelope = assert_error_envelope(
            self.post(["subject", unfinished.original_username]),
            expected_status=status.HTTP_409_CONFLICT,
            expected_type_slug=STATE_CONFLICT_SLUG,
        )
        assert envelope["detail"] == view_services.BATCH_LEFT_ALONE
        assert envelope["errors"] == {
            unfinished.original_username: [
                "This retirement stands at LOCKING_ACCOUNT; only a retirement at COMPLETE is deleted here.",
            ],
        }
        assert UserRetirementStatus.objects.filter(id=self.finished.id).exists()
        assert UserRetirementStatus.objects.filter(id=unfinished.id).exists()

    def test_a_name_spelled_differently_from_the_registered_one_leaves_the_record_alone(self):
        self.login("worker")
        response = self.post(["SUBJECT"])
        assert response.data == {"results": [{"username": "SUBJECT", "outcome": "already_absent"}]}
        assert UserRetirementStatus.objects.filter(id=self.finished.id).exists()


@skip_unless_lms
class PartnerReportCleanupTests(_BatchTestsMixin, RetirementApiTestCase):
    """POST /api/user/v2/retirement_partner_report_cleanups/"""

    def setUp(self):
        super().setUp()
        self.url = reverse("user_v2:retirement_partner_report_cleanup_list")
        self.reported = self.queued(self.subject, taken=True)

    def queued(self, user, taken=False):
        """Return a partner report entry for this user, taken for processing or not."""
        return UserRetirementPartnerReportingStatus.objects.create(
            user=user,
            original_username=user.username,
            original_email=user.email,
            original_name=user.profile.name,
            is_being_processed=taken,
        )

    def send(self):
        entry = self.queued(UserFactory.create(), taken=True)
        return self.post([entry.original_username])

    def test_an_entry_that_has_been_reported_on_is_deleted(self):
        self.login("worker")
        response = self.post(["subject"])
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"results": [{"username": "subject", "outcome": "deleted"}]}
        assert not UserRetirementPartnerReportingStatus.objects.filter(id=self.reported.id).exists()

    def test_a_repeat_of_a_request_already_carried_out_is_answered(self):
        self.login("worker")
        self.post(["subject"])
        again = self.post(["subject"])
        assert again.data == {"results": [{"username": "subject", "outcome": "already_absent"}]}

    def test_an_entry_nobody_has_taken_refuses_the_whole_batch_and_nothing_is_deleted(self):
        waiting = self.queued(self.learner)
        self.login("worker")
        envelope = assert_error_envelope(
            self.post(["subject", waiting.original_username]),
            expected_status=status.HTTP_409_CONFLICT,
            expected_type_slug=STATE_CONFLICT_SLUG,
        )
        assert envelope["errors"] == {
            waiting.original_username: [
                "This partner report has not been taken for processing, so it has not been reported on yet.",
            ],
        }
        assert UserRetirementPartnerReportingStatus.objects.filter(id=self.reported.id).exists()
        assert UserRetirementPartnerReportingStatus.objects.filter(id=waiting.id).exists()

    def test_each_name_is_reported_in_the_order_it_was_given(self):
        other = self.queued(UserFactory.create(username="other-subject"), taken=True)
        self.login("worker")
        response = self.post(["other-subject", "nobody", "subject"])
        assert response.data == {
            "results": [
                {"username": "other-subject", "outcome": "deleted"},
                {"username": "nobody", "outcome": "already_absent"},
                {"username": "subject", "outcome": "deleted"},
            ],
        }
        assert not UserRetirementPartnerReportingStatus.objects.filter(id=other.id).exists()


@skip_unless_lms
class RetirementAccessModelTests(RetirementApiTestCase):
    """How the capabilities of this area are combined, and what happens with none of them."""

    def test_an_action_the_mapping_does_not_name_reaches_no_capability(self):
        """A later action cannot inherit the capability every named action shares."""
        viewset = RetirementViewSet()
        viewset.action = "an_action_added_later"
        granted = [
            permission.has_permission(mock.Mock(user=self.superuser), viewset)
            for permission in viewset.get_permissions()
        ]
        assert granted[-1] is False

    def test_each_named_action_carries_the_capability_it_is_mapped_to(self):
        """The classes are read from the view rather than from the mapping, so the mapping can be wrong."""
        expected = {
            "list": CanRetireUser,
            "retrieve": CanRetireUser,
            "partial_update": CanRetireUser,
            "erasures": CanRetireUser,
            "destroy": CanCancelUserRetirement,
            "metadata": CanRetireUser,
        }
        assert set(expected) == set(RETIREMENT_ACTION_PERMISSIONS)
        for action, capability in expected.items():
            with self.subTest(action=action):
                viewset = RetirementViewSet()
                viewset.action = action
                built = [type(permission) for permission in viewset.get_permissions()]
                assert built[-1] is capability
                assert permissions.IsAuthenticated in built

    def test_the_classes_of_a_view_must_all_admit_a_caller(self):
        """Being authenticated is not enough, and holding the capability without being signed in is not either."""
        self.login("staff")
        assert_error_envelope(
            self.client.get(reverse("user_v2:retirement_list")),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )
        self.client.logout()
        assert_error_envelope(
            self.client.get(reverse("user_v2:retirement_list")),
            expected_status=status.HTTP_401_UNAUTHORIZED,
            expected_type_slug="authn",
        )

    def test_no_retirement_view_declares_its_own_authentication(self):
        for view in (
            RetirementViewSet,
            RetirementPartnerReportViewSet,
            RetirementCleanupViewSet,
            RetirementPartnerReportCleanupViewSet,
            _RetirementRecordAccessMixin,
            _PartnerReportAccessMixin,
        ):
            with self.subTest(view=view.__name__):
                assert "authentication_classes" not in vars(view)

    def test_no_retirement_view_declares_its_own_throttles(self):
        """The pipeline is unthrottled on both versions alike, which is left as it stands."""
        for view in (
            RetirementViewSet,
            RetirementPartnerReportViewSet,
            RetirementCleanupViewSet,
            RetirementPartnerReportCleanupViewSet,
            _RetirementRecordAccessMixin,
            _PartnerReportAccessMixin,
        ):
            with self.subTest(view=view.__name__):
                assert "throttle_classes" not in vars(view)


@skip_unless_lms
class RetirementRecordAccessReachTests(RetirementApiTestCase):
    """
    What is asked of the operations on the retirement records is asked of their disposal.

    The records the collection lists, moves and cancels are the records the
    batch disposal deletes, so both resolve who may act on them and which of
    them a caller acts on through one layer. A rule added to that layer is
    read here from the answers each of them gives, rather than from where the
    rule is written.
    """

    def setUp(self):
        super().setUp()
        self.list_url = reverse("user_v2:retirement_list")
        self.disposal_url = reverse("user_v2:retirement_cleanup_list")
        self.finished = self.retirement_in("COMPLETE", user=self.subject)
        self.other = self.retirement_in("COMPLETE")

    def dispose_of(self, usernames):
        """Ask the disposal to delete the records of these accounts."""
        return self.client.post(self.disposal_url, {"usernames": usernames}, format="json")

    @contextlib.contextmanager
    def a_rule_added_to_the_records(self):
        """Add a rule to the layer the operations on these records share."""
        with layers_read_from_their_own_declaration(RetirementViewSet, RetirementCleanupViewSet):
            with mock.patch.object(
                _RetirementRecordAccessMixin, "permission_classes",
                (permissions.IsAuthenticated, _OnlyASuperuser),
            ):
                yield

    def test_a_rule_added_to_the_records_is_asked_of_their_disposal(self):
        self.login("worker")
        with self.a_rule_added_to_the_records():
            refused = self.dispose_of(["subject"])
        assert_error_envelope(
            refused, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
        )
        assert UserRetirementStatus.objects.filter(id=self.finished.id).exists()

    def test_the_same_rule_is_asked_of_the_collection_those_records_are_listed_by(self):
        """The rule is added in one place, so what obeys it is what resolves it there."""
        self.login("worker")
        with self.a_rule_added_to_the_records():
            assert_error_envelope(
                self.client.get(self.list_url),
                expected_status=status.HTTP_403_FORBIDDEN,
                expected_type_slug="authz",
            )

    def test_the_caller_the_rule_admits_is_served_by_both(self):
        """The refusals above are the rule's doing, and the rule admits somebody."""
        self.login("superuser")
        with self.a_rule_added_to_the_records():
            assert self.client.get(self.list_url).status_code == status.HTTP_200_OK
            assert self.dispose_of(["subject"]).status_code == status.HTTP_200_OK

    def test_the_same_caller_is_served_by_the_disposal_without_that_rule(self):
        """The refusal above is the rule's doing and not the caller's."""
        self.login("worker")
        answered = self.dispose_of(["subject"])
        assert answered.status_code == status.HTTP_200_OK
        assert answered.data == {"results": [{"username": "subject", "outcome": "deleted"}]}

    def test_a_record_the_visibility_policy_leaves_out_is_not_deleted_by_the_disposal(self):
        self.login("worker")
        policy = _NarrowingPolicy("original_username", self.other.original_username)
        with mock.patch.object(_RetirementRecordAccessMixin, "scoping_policy", policy):
            answered = self.dispose_of(["subject", self.other.original_username])
        assert answered.data == {
            "results": [
                {"username": "subject", "outcome": "already_absent"},
                {"username": self.other.original_username, "outcome": "deleted"},
            ],
        }
        assert UserRetirementStatus.objects.filter(id=self.finished.id).exists()
        assert not UserRetirementStatus.objects.filter(id=self.other.id).exists()

    def test_the_policy_is_given_the_caller_and_every_record_the_disposal_could_delete(self):
        self.login("worker")
        policy = _NarrowingPolicy("original_username", self.other.original_username)
        with mock.patch.object(_RetirementRecordAccessMixin, "scoping_policy", policy):
            self.dispose_of(["subject"])
        assert [subject.username for subject in policy.subjects] == [WORKER_USERNAME]
        assert policy.offered == [sorted(["subject", self.other.original_username])]

    def test_the_disposal_declares_none_of_these_layers_itself(self):
        """Declaring one again here is what would leave a rule added to the records behind."""
        with layers_read_from_their_own_declaration(RetirementViewSet, RetirementCleanupViewSet):
            for name, declared_by in (
                ("permission_classes", _RetirementRecordAccessMixin),
                ("scoping_policy", _RetirementRecordAccessMixin),
                ("get_permissions", _RetirementRecordAccessMixin),
                ("get_queryset", _RetirementRecordsMixin),
            ):
                with self.subTest(declaration=name):
                    assert name not in vars(RetirementCleanupViewSet)
                    assert name not in vars(RetirementViewSet)
                    assert name in vars(declared_by)

    def test_the_classes_each_view_resolves_are_the_ones_the_shared_layer_declares(self):
        """Neither view declares them itself, so these are the classes the layer resolves to."""
        resolved = {}
        with layers_read_from_their_own_declaration(RetirementViewSet, RetirementCleanupViewSet):
            for view, action in (
                (RetirementViewSet, "list"),
                (RetirementViewSet, "destroy"),
                (RetirementCleanupViewSet, "create"),
                (RetirementCleanupViewSet, "metadata"),
            ):
                viewset = view()
                viewset.action = action
                resolved[(view.__name__, action)] = [
                    type(permission) for permission in viewset.get_permissions()
                ]
        assert resolved == {
            ("RetirementViewSet", "list"): [permissions.IsAuthenticated, CanRetireUser, NotJwtRestrictedApplication],
            ("RetirementViewSet", "destroy"): [
                permissions.IsAuthenticated, CanCancelUserRetirement, NotJwtRestrictedApplication,
            ],
            ("RetirementCleanupViewSet", "create"): [
                permissions.IsAuthenticated, CanRetireUser, NotJwtRestrictedApplication,
            ],
            ("RetirementCleanupViewSet", "metadata"): [
                permissions.IsAuthenticated, CanRetireUser, NotJwtRestrictedApplication,
            ],
        }

    def test_the_policy_declared_for_these_records_is_the_one_each_view_resolves(self):
        """The library mixin declares the same attribute, so where it stands in the order matters."""
        for view in (RetirementViewSet, RetirementCleanupViewSet):
            with self.subTest(view=view.__name__):
                assert isinstance(view().scoping_policy, FullScopePolicy)

    def test_the_disposal_asks_for_the_capability_the_rest_of_the_pipeline_work_asks_for(self):
        assert RetirementCleanupViewSet.action_permissions == {
            "create": (CanRetireUser,),
            "metadata": (CanRetireUser,),
        }
        for action in RetirementCleanupViewSet.action_permissions:
            with self.subTest(action=action):
                viewset = RetirementCleanupViewSet()
                viewset.action = action
                built = [type(permission) for permission in viewset.get_permissions()]
                assert built[-1] is CanRetireUser
                assert permissions.IsAuthenticated in built

    def test_asking_the_disposal_what_it_serves_asks_for_the_same_capability(self):
        """Describing an address is reading it, and this address is read by whoever may work it."""
        self.login("worker")
        described = self.client.options(self.disposal_url)
        assert described.status_code == status.HTTP_200_OK
        assert described.data["renders"] == ["application/json"]
        self.client.logout()
        self.login("learner")
        assert_error_envelope(
            self.client.options(self.disposal_url),
            expected_status=status.HTTP_403_FORBIDDEN,
            expected_type_slug="authz",
        )

    def test_an_action_added_to_the_disposal_later_reaches_no_capability(self):
        viewset = RetirementCleanupViewSet()
        viewset.action = "an_action_added_later"
        assert [
            permission.has_permission(mock.Mock(user=self.superuser), viewset)
            for permission in viewset.get_permissions()
        ][-1] is False


@skip_unless_lms
class PartnerReportAccessReachTests(RetirementApiTestCase):
    """What is asked of the operations on the partner report entries is asked of their disposal."""

    def setUp(self):
        super().setUp()
        self.queue_url = reverse("user_v2:retirement_partner_report_list")
        self.disposal_url = reverse("user_v2:retirement_partner_report_cleanup_list")
        self.reported = self.queued(self.subject, taken=True)
        self.also_reported = self.queued(self.learner, taken=True)

    def queued(self, user, taken=False):
        """Return a partner report entry for this user, taken for processing or not."""
        return UserRetirementPartnerReportingStatus.objects.create(
            user=user,
            original_username=user.username,
            original_email=user.email,
            original_name=user.profile.name,
            is_being_processed=taken,
        )

    def dispose_of(self, usernames):
        """Ask the disposal to delete the entries of these accounts."""
        return self.client.post(self.disposal_url, {"usernames": usernames}, format="json")

    def test_a_rule_added_to_the_entries_is_asked_of_their_disposal(self):
        self.login("worker")
        with layers_read_from_their_own_declaration(
            RetirementPartnerReportViewSet, RetirementPartnerReportCleanupViewSet,
        ), mock.patch.object(
            _PartnerReportAccessMixin, "permission_classes",
            (permissions.IsAuthenticated, _OnlyASuperuser),
        ):
            refused = self.dispose_of(["subject"])
            taking = self.client.post(self.queue_url, {}, format="json")
        for response in (refused, taking):
            assert_error_envelope(
                response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug="authz",
            )
        assert UserRetirementPartnerReportingStatus.objects.filter(id=self.reported.id).exists()

    def test_the_same_caller_is_served_by_the_disposal_without_that_rule(self):
        self.login("worker")
        answered = self.dispose_of(["subject"])
        assert answered.status_code == status.HTTP_200_OK
        assert answered.data == {"results": [{"username": "subject", "outcome": "deleted"}]}

    def test_an_entry_the_visibility_policy_leaves_out_is_not_deleted_by_the_disposal(self):
        self.login("worker")
        policy = _NarrowingPolicy("original_username", self.learner.username)
        with mock.patch.object(_PartnerReportAccessMixin, "scoping_policy", policy):
            answered = self.dispose_of(["subject", self.learner.username])
        assert answered.data == {
            "results": [
                {"username": "subject", "outcome": "already_absent"},
                {"username": self.learner.username, "outcome": "deleted"},
            ],
        }
        assert UserRetirementPartnerReportingStatus.objects.filter(id=self.reported.id).exists()
        assert not UserRetirementPartnerReportingStatus.objects.filter(
            id=self.also_reported.id,
        ).exists()

    def test_the_disposal_resolves_the_entries_the_queue_leaves_out(self):
        """An entry is disposed of once it has been taken, which is when the queue stops offering it."""
        self.login("worker")
        policy = _NarrowingPolicy("original_username", "nobody")
        with mock.patch.object(_PartnerReportAccessMixin, "scoping_policy", policy):
            self.dispose_of(["subject"])
            self.client.post(self.queue_url, {}, format="json")
        assert policy.offered == [
            sorted([self.subject.username, self.learner.username]),
            [],
        ]

    def test_the_disposal_declares_neither_layer_itself(self):
        with layers_read_from_their_own_declaration(
            RetirementPartnerReportViewSet, RetirementPartnerReportCleanupViewSet,
        ):
            for name in ("permission_classes", "scoping_policy"):
                with self.subTest(declaration=name):
                    assert name not in vars(RetirementPartnerReportCleanupViewSet)
                    assert name not in vars(RetirementPartnerReportViewSet)
                    assert name in vars(_PartnerReportAccessMixin)

    def test_the_classes_each_view_resolves_are_the_ones_the_shared_layer_declares(self):
        """Neither view declares them itself, so these are the classes the layer resolves to."""
        with layers_read_from_their_own_declaration(
            RetirementPartnerReportViewSet, RetirementPartnerReportCleanupViewSet,
        ):
            resolved = {
                view.__name__: [type(permission) for permission in view().get_permissions()]
                for view in (
                    RetirementPartnerReportViewSet, RetirementPartnerReportCleanupViewSet,
                )
            }
        assert resolved == {
            "RetirementPartnerReportViewSet": [
                permissions.IsAuthenticated, CanRetireUser, NotJwtRestrictedApplication,
            ],
            "RetirementPartnerReportCleanupViewSet": [
                permissions.IsAuthenticated, CanRetireUser, NotJwtRestrictedApplication,
            ],
        }

    def test_the_policy_declared_for_these_entries_is_the_one_each_view_resolves(self):
        """The library mixin declares the same attribute, so where it stands in the order matters."""
        for view in (RetirementPartnerReportViewSet, RetirementPartnerReportCleanupViewSet):
            with self.subTest(view=view.__name__):
                assert isinstance(view().scoping_policy, FullScopePolicy)


@skip_unless_lms
class RetirementMemberVisibilityTests(RetirementApiTestCase):
    """
    A retirement the visibility policy leaves out is absent from every member address too.

    The policy this deployment runs keeps the records whole, because every
    caller these addresses admit holds the capability to work the whole
    pipeline, so it is replaced here with one that keeps one record of two and
    each member address is asked about the other.
    """

    def setUp(self):
        super().setUp()
        self.kept = self.retirement_in("RETIRING_LMS", user=self.learner)
        self.left_out = self.retirement_in("RETIRING_LMS", user=self.subject)
        self.policy = _NarrowingPolicy("original_username", self.learner.username)
        self.left_out_url = reverse("user_v2:retirement_detail", kwargs={"username": "subject"})
        self.kept_url = reverse(
            "user_v2:retirement_detail", kwargs={"username": self.learner.username},
        )
        self.erasure_url = reverse("user_v2:retirement_erasure", kwargs={"username": "subject"})

    @contextlib.contextmanager
    def policy_in_force(self):
        """Serve the next requests under a policy that keeps one of the two records."""
        with mock.patch.object(_RetirementRecordAccessMixin, "scoping_policy", self.policy):
            yield

    def assert_reported_absent(self, response):
        """Assert the record was reported as absent rather than the caller refused."""
        envelope = assert_error_envelope(
            response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug="not-found",
        )
        assert envelope["detail"] == view_services.NO_SUCH_RETIREMENT

    def test_a_record_the_policy_leaves_out_is_absent_from_its_member_address(self):
        self.login("worker")
        with self.policy_in_force():
            self.assert_reported_absent(self.client.get(self.left_out_url))

    def test_the_record_the_policy_keeps_is_answered_at_its_own_address(self):
        self.login("worker")
        with self.policy_in_force():
            answered = self.client.get(self.kept_url)
        assert answered.status_code == status.HTTP_200_OK
        assert answered.data["original_username"] == self.learner.username

    def test_a_record_the_policy_leaves_out_cannot_be_moved(self):
        self.login("worker")
        with self.policy_in_force():
            self.assert_reported_absent(
                self.client.patch(
                    self.left_out_url,
                    {"new_state": "LMS_COMPLETE", "response": "done"},
                    format="json",
                ),
            )
        self.left_out.refresh_from_db()
        assert self.left_out.current_state.state_name == "RETIRING_LMS"

    def test_a_record_the_policy_leaves_out_cannot_be_cancelled(self):
        pending = self.retirement_in("PENDING", user=UserFactory.create(username="pending-one"))
        self.login("canceller")
        with mock.patch.object(
            _RetirementRecordAccessMixin, "scoping_policy",
            _NarrowingPolicy("original_username", self.learner.username),
        ):
            self.assert_reported_absent(
                self.client.delete(
                    reverse("user_v2:retirement_detail", kwargs={"username": "pending-one"}),
                ),
            )
        assert UserRetirementStatus.objects.filter(id=pending.id).exists()

    def test_a_record_the_policy_leaves_out_has_nothing_erased(self):
        self.subject.profile.city = "Somewhere"
        self.subject.profile.save()
        self.login("worker")
        with self.policy_in_force():
            self.assert_reported_absent(
                self.client.post(self.erasure_url, {"scope": "lms_core"}, format="json"),
            )
        self.subject.profile.refresh_from_db()
        assert self.subject.profile.city == "Somewhere"
        self.subject.refresh_from_db()
        assert self.subject.username == "subject"

    def test_the_policy_is_given_the_caller_and_every_record_held(self):
        self.login("worker")
        with self.policy_in_force():
            self.client.get(self.kept_url)
        assert [subject.username for subject in self.policy.subjects] == [WORKER_USERNAME]
        assert self.policy.offered == [
            sorted([self.learner.username, self.left_out.original_username]),
        ]

    def test_a_name_more_than_one_record_is_held_under_is_still_refused(self):
        """The policy narrows what the name is resolved against; it does not resolve the name."""
        twin = self.retirement_in("RETIRING_LMS", user=UserFactory.create(username="learner-twin"))
        UserRetirementStatus.objects.filter(id=twin.id).update(
            original_username=self.learner.username,
        )
        self.login("worker")
        with self.policy_in_force():
            envelope = assert_error_envelope(
                self.client.patch(
                    self.kept_url,
                    {"new_state": "LMS_COMPLETE", "response": "done"},
                    format="json",
                ),
                expected_status=status.HTTP_400_BAD_REQUEST,
                expected_type_slug=AMBIGUOUS_NAME_SLUG,
            )
        assert envelope["detail"] == view_services.NAME_IS_AMBIGUOUS
        for record in (self.kept, twin):
            record.refresh_from_db()
            assert record.current_state.state_name == "RETIRING_LMS"

    def test_a_policy_leaving_one_record_of_such_a_pair_leaves_one_record_for_the_name(self):
        """The record left out belongs to an account this caller may not act on, so it is not weighed."""
        twin = self.retirement_in("RETIRING_LMS", user=UserFactory.create(username="learner-twin"))
        UserRetirementStatus.objects.filter(id=twin.id).update(
            original_username=self.learner.username,
        )
        self.login("worker")
        with mock.patch.object(
            _RetirementRecordAccessMixin, "scoping_policy",
            _NarrowingPolicy("id", self.kept.id),
        ):
            answered = self.client.patch(
                self.kept_url,
                {"new_state": "LMS_COMPLETE", "response": "done"},
                format="json",
            )
        assert answered.status_code == status.HTTP_204_NO_CONTENT
        self.kept.refresh_from_db()
        twin.refresh_from_db()
        assert self.kept.current_state.state_name == "LMS_COMPLETE"
        assert twin.current_state.state_name == "RETIRING_LMS"


@skip_unless_lms
class SharedNameUnderANarrowingPolicyTests(RetirementApiTestCase):
    """
    Which of two records requested under one name an operation weighs, and which it acts on.

    The name a record keeps of the account it was requested for is not unique,
    so two records can stand under one name. A policy that keeps one of them
    leaves the name addressing a single record, and that record is the one the
    operation is weighed against: the record the policy left out belongs to an
    account this caller does not act on, so the step it stands at decides
    nothing about the account that does.
    """

    SHARED_NAME = "sharedname"

    def setUp(self):
        super().setUp()
        self.in_scope = UserFactory.create(
            username="in-scope-holder", email="in-scope@example.com",
        )
        self.out_of_scope = UserFactory.create(
            username="out-of-scope-holder", email="out-of-scope@example.com",
        )
        for user, name in ((self.in_scope, "In Scope"), (self.out_of_scope, "Out Of Scope")):
            user.profile.name = name
            user.profile.city = f"City of {user.username}"
            user.profile.save()
        self.url = reverse("user_v2:retirement_erasure", kwargs={"username": self.SHARED_NAME})
        # Declared here so the attributes exist before pair() fills them in.
        self.out_of_scope_record = None
        self.in_scope_record = None
        self.in_scope_before = None
        self.out_of_scope_before = None

    def pair(self, in_scope_step, out_of_scope_step):
        """Request a retirement for each account under the one name, standing at these steps."""
        # The record the policy leaves out is requested first, so that it is
        # the one a resolution that reads every record held settles on.
        self.out_of_scope_record = self.retirement_in(out_of_scope_step, user=self.out_of_scope)
        self.in_scope_record = self.retirement_in(in_scope_step, user=self.in_scope)
        UserRetirementStatus.objects.filter(
            id__in=[self.out_of_scope_record.id, self.in_scope_record.id],
        ).update(original_username=self.SHARED_NAME)
        self.in_scope_before = self.account(self.in_scope)
        self.out_of_scope_before = self.account(self.out_of_scope)

    def account(self, user):
        """Return the stored values the core erasure writes over."""
        user.refresh_from_db()
        profile = UserProfile.objects.get(user=user)
        return {
            "username": user.username,
            "email": user.email,
            "is_active": user.is_active,
            "profile_name": profile.name,
            "profile_city": profile.city,
        }

    @contextlib.contextmanager
    def policy_keeping(self, record):
        """Serve the next requests under a policy that keeps this record alone."""
        with mock.patch.object(
            _RetirementRecordAccessMixin, "scoping_policy", _NarrowingPolicy("id", record.id),
        ):
            yield

    def erase(self):
        """Ask for the core personal data of the account this name addresses to be erased."""
        return self.client.post(self.url, {"scope": "lms_core"}, format="json")

    def test_the_step_the_record_the_policy_keeps_stands_at_refuses_the_erasure(self):
        """The other record admits work, which says nothing about the account being acted on."""
        self.pair(in_scope_step="PENDING", out_of_scope_step="RETIRING_LMS")
        self.login("worker")
        with self.policy_keeping(self.in_scope_record):
            answered = self.erase()
        # What survives is read first, so that a failure says what was destroyed.
        assert self.account(self.in_scope) == self.in_scope_before
        assert self.account(self.out_of_scope) == self.out_of_scope_before
        envelope = assert_error_envelope(
            answered,
            expected_status=status.HTTP_409_CONFLICT,
            expected_type_slug=STATE_CONFLICT_SLUG,
        )
        assert envelope["detail"] == view_services.NOT_OPEN_TO_ACTION

    def test_the_step_the_record_the_policy_keeps_stands_at_admits_the_erasure(self):
        """The other record admits no work, which says nothing about the account being acted on."""
        self.pair(in_scope_step="RETIRING_LMS", out_of_scope_step="COMPLETE")
        self.login("worker")
        with self.policy_keeping(self.in_scope_record):
            answered = self.erase()
        # What was erased is read first, so that a failure says what was left.
        assert self.account(self.in_scope) == {
            "username": self.in_scope_record.retired_username,
            "email": self.in_scope_record.retired_email,
            "is_active": False,
            "profile_name": "",
            "profile_city": None,
        }
        assert self.account(self.out_of_scope) == self.out_of_scope_before
        assert answered.status_code == status.HTTP_204_NO_CONTENT

    def test_the_account_erased_is_the_one_whose_record_the_policy_keeps(self):
        """Both records admit work, so what decides is which of them the name addresses here."""
        self.pair(in_scope_step="RETIRING_LMS", out_of_scope_step="RETIRING_LMS")
        self.login("worker")
        with self.policy_keeping(self.in_scope_record):
            assert self.erase().status_code == status.HTTP_204_NO_CONTENT
        assert self.account(self.in_scope)["username"] == self.in_scope_record.retired_username
        assert self.account(self.out_of_scope) == self.out_of_scope_before

    def test_the_policy_this_deployment_runs_refuses_the_name_and_erases_nothing(self):
        """It keeps the records whole, so the name is carried by two of them and addresses neither."""
        self.pair(in_scope_step="PENDING", out_of_scope_step="RETIRING_LMS")
        self.login("worker")
        envelope = assert_error_envelope(
            self.erase(),
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug=AMBIGUOUS_NAME_SLUG,
        )
        assert envelope["detail"] == view_services.NAME_IS_AMBIGUOUS
        assert self.account(self.in_scope) == self.in_scope_before
        assert self.account(self.out_of_scope) == self.out_of_scope_before


@skip_unless_lms
class PartnerQueueRecordVisibilityTests(RetirementApiTestCase):
    """The retirement a queue write resolves is narrowed by the policy this collection declares."""

    def setUp(self):
        super().setUp()
        self.in_scope = self.retirement_in("ADDING_TO_PARTNER_QUEUE", user=self.subject)
        self.out_of_scope = self.retirement_in("ADDING_TO_PARTNER_QUEUE", user=self.learner)
        for user in (self.subject, self.learner):
            CourseEnrollmentFactory.create(user=user, course_id=COURSE_KEY)
        self.policy = _NarrowingPolicy("original_username", self.subject.username)

    def queue(self, username):
        """Ask for the retiring account this name addresses to be queued for partner reporting."""
        return self.client.put(
            reverse("user_v2:retirement_partner_report_detail", kwargs={"username": username}),
            {},
            format="json",
        )

    def queued_names(self):
        """Return the names every entry of the partner report queue carries."""
        return set(
            UserRetirementPartnerReportingStatus.objects.values_list("original_username", flat=True),
        )

    def test_a_record_the_policy_leaves_out_is_not_queued_for_partner_reporting(self):
        self.login("worker")
        with mock.patch.object(_PartnerReportAccessMixin, "scoping_policy", self.policy):
            answered = self.queue(self.learner.username)
        # What the queue holds is read first, so that a failure names whose
        # personal data a partner would have been handed.
        assert self.queued_names() == set()
        envelope = assert_error_envelope(
            answered,
            expected_status=status.HTTP_404_NOT_FOUND,
            expected_type_slug="not-found",
        )
        assert envelope["detail"] == view_services.NO_SUCH_RETIREMENT

    def test_the_record_the_policy_keeps_is_queued(self):
        self.login("worker")
        with mock.patch.object(_PartnerReportAccessMixin, "scoping_policy", self.policy):
            answered = self.queue(self.subject.username)
        assert answered.status_code == status.HTTP_204_NO_CONTENT
        assert self.queued_names() == {self.subject.username}

    def test_the_policy_is_given_the_caller_and_the_retirement_records_it_resolves_from(self):
        self.login("worker")
        with mock.patch.object(_PartnerReportAccessMixin, "scoping_policy", self.policy):
            self.queue(self.subject.username)
        assert [caller.username for caller in self.policy.subjects] == [WORKER_USERNAME]
        assert self.policy.offered == [
            sorted([self.subject.username, self.learner.username]),
        ]

    def test_the_policy_this_deployment_runs_leaves_every_record_queueable(self):
        self.login("worker")
        assert self.queue(self.learner.username).status_code == status.HTTP_204_NO_CONTENT
        assert self.queued_names() == {self.learner.username}


class RetirementErrorCatalogTests(SimpleTestCase):
    """What this area adds to the shared error catalog, and what it leaves alone."""

    def test_the_state_refusal_of_this_area_is_named_for_this_area(self):
        assert classify_error(RetirementStateConflict()) == (
            "user/retirement-state-conflict", "Retirement State Conflict",
        )

    def test_another_area_refusing_a_conflict_is_classified_as_it_was_before(self):
        """Registering this area's refusal reclassifies nothing for any other view in the process."""
        assert classify_error(Conflict()) == ("conflict", "Conflict")

    def test_the_refusal_of_an_ambiguous_name_is_named_for_this_area(self):
        assert classify_error(AmbiguousAccountName()) == (
            "user/ambiguous-account-name", "Ambiguous Account Name",
        )

    def test_another_area_refusing_an_invalid_request_is_classified_as_it_was_before(self):
        """The registry is shared, and this area registers a subclass of a classified error."""
        assert classify_error(ValidationError("anything")) == ("validation", "Validation Error")

    def test_a_refusal_naming_no_records_carries_no_per_record_member(self):
        assert not hasattr(RetirementStateConflict("refused"), "extension_members")


class RetirementPageSizeTests(SimpleTestCase):
    """The page sizes the collections of this version are cut to."""

    def test_the_default_and_the_maximum_page_are_the_sizes_this_version_publishes(self):
        assert UserApiPagination.page_size == DEFAULT_PAGE_SIZE
        assert UserApiPagination.max_page_size == MAXIMUM_PAGE_SIZE
        assert RetirementViewSet.pagination_class is UserApiPagination


class RetirementReadCaptureCase(RetirementApiTestCase):
    """
    What a read of the retirement addresses does, measured statement by statement.

    Three channels are captured, because a read that changes stored data need
    not do so itself: the statements it ran, the callbacks it deferred to the
    end of its transaction, and the signals it sent. A signal is captured
    because the platform's own listeners act on one whether or not the read
    that sent it wrote anything, and the pipeline announces each of its steps
    that way.
    """

    def setUp(self):
        super().setUp()
        self.retirement = self.retirement_in("LOCKING_ACCOUNT", user=self.subject)
        self.list_url = reverse("user_v2:retirement_list")
        self.detail_url = reverse("user_v2:retirement_detail", kwargs={"username": "subject"})
        self.deferred = []
        self.signalled = []

    def captured_writes(self, method, url, **params):
        """Return the statements a read makes that would change the data this API serves."""
        self.login("worker")
        with CaptureQueriesContext(connection) as captured, \
                captured_signals() as signalled, \
                self.captureOnCommitCallbacks(execute=True) as callbacks:
            response = getattr(self.client, method)(url, params)
        assert response.status_code == status.HTTP_200_OK, response.content
        self.deferred = callbacks
        self.signalled = signalled
        return write_queries(captured.captured_queries)

    def targets(self, statements):
        """Return the table each statement writes to."""
        return [statement.split('"')[1] for statement in statements]


@skip_unless_lms
class RetirementReadsAreNotWritesTests(RetirementReadCaptureCase):
    """Reading the queue or one record changes nothing, whichever way it is read."""

    def assert_nothing_was_changed(self, method, url, **params):
        """Assert the read wrote nothing, deferred nothing and announced nothing."""
        statements = self.captured_writes(method, url, **params)
        assert (statements, self.deferred, self.signalled) == ([], [], [])

    def test_reading_the_queue_announces_nothing_and_defers_nothing(self):
        self.assert_nothing_was_changed("get", self.list_url)

    def test_reading_one_record_announces_nothing_and_defers_nothing(self):
        self.assert_nothing_was_changed("get", self.detail_url)

    def test_reading_the_queue_without_a_body_announces_nothing_either(self):
        self.assert_nothing_was_changed("head", self.list_url)

    def test_reading_one_record_without_a_body_announces_nothing_either(self):
        self.assert_nothing_was_changed("head", self.detail_url)

    def test_reading_the_queue_writes_nothing(self):
        assert self.captured_writes("get", self.list_url) == []

    def test_reading_the_queue_without_a_body_writes_nothing(self):
        assert self.captured_writes("head", self.list_url) == []

    def test_reading_one_record_writes_nothing(self):
        assert self.captured_writes("get", self.detail_url) == []

    def test_reading_one_record_without_a_body_writes_nothing(self):
        assert self.captured_writes("head", self.detail_url) == []

    def test_reading_the_queue_with_every_filter_applied_writes_nothing(self):
        assert self.captured_writes(
            "get",
            self.list_url,
            state="LOCKING_ACCOUNT",
            cool_off_days=0,
            created_after="2020-01-01",
            created_before="2099-01-01",
            ordering="created",
            view="minimal",
        ) == []


@skip_unless_lms
class RetirementReadsWithTheLanguageCookieTests(RetirementReadCaptureCase):
    """
    A read carrying the language cookie stores the language it carries.

    The platform keeps the language a user picked in a cookie and syncs it into
    that user's preferences from middleware, which runs before any view and on
    every method, so it is reached by a read of these addresses as it is by a
    read of any other. It is the one write a read of this area makes, and it
    belongs to the platform rather than to this version: the addresses this
    version supersedes make the same write, under the same cookie. The capture
    below pins it -- the one row, by the table it is stored in, the one change
    it announces, and nothing else.
    """

    def setUp(self):
        super().setUp()
        self.client.cookies[LANGUAGE_COOKIE] = "eo"

    def language_rows(self):
        """Return the stored language of every account, by account."""
        return {
            preference.user.username: preference.value
            for preference in UserPreference.objects.filter(key=LANGUAGE_PREFERENCE)
        }

    def assert_only_the_language_was_stored(self, method, url, **params):
        """Assert the one write made, and the one change announced, was the caller's language."""
        statements = self.captured_writes(method, url, **params)
        assert (self.targets(statements), self.deferred) == ([PREFERENCE_TABLE], []), statements
        assert self.language_rows() == {WORKER_USERNAME: "eo"}
        assert self.signalled == LANGUAGE_SYNC_SIGNALS

    def test_reading_the_queue_stores_the_language_the_cookie_carries(self):
        assert self.language_rows() == {}
        self.assert_only_the_language_was_stored("get", self.list_url)

    def test_reading_one_record_stores_it_too(self):
        self.assert_only_the_language_was_stored("get", self.detail_url)

    def test_reading_the_queue_without_a_body_stores_it_as_well(self):
        """A write one frame below the handler is reached by every method, this one included."""
        self.assert_only_the_language_was_stored("head", self.list_url)

    def test_reading_one_record_without_a_body_stores_it_as_well(self):
        self.assert_only_the_language_was_stored("head", self.detail_url)

    def test_reading_the_queue_with_every_filter_applied_stores_only_that(self):
        self.assert_only_the_language_was_stored(
            "get",
            self.list_url,
            state="LOCKING_ACCOUNT",
            cool_off_days=0,
            created_after="2020-01-01",
            created_before="2099-01-01",
            ordering="created",
            view="minimal",
        )

    def test_the_address_this_version_supersedes_stores_the_same_row(self):
        """The write belongs to the platform rather than to either version of this area."""
        self.login("worker")
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(
                EARLIER_RETIREMENT_QUEUE, {"cool_off_days": 0, "states": "LOCKING_ACCOUNT"},
            )
        assert response.status_code == status.HTTP_200_OK, response.content
        assert self.targets(write_queries(captured.captured_queries)) == [PREFERENCE_TABLE]
        assert self.language_rows() == {WORKER_USERNAME: "eo"}

    def test_the_row_is_written_once_and_not_once_a_request(self):
        """It is the language being synced, not the read recording anything."""
        self.assert_only_the_language_was_stored("get", self.list_url)
        statements = self.captured_writes("get", self.list_url)
        assert (statements, self.deferred, self.signalled) == ([], [], [])


@skip_unless_lms
class NameRequestedTwiceTests(RetirementApiTestCase):
    """
    A name more than one record was requested under is refused, and nothing is acted on.

    The name a record keeps of the account it is for is indexed but not
    unique, and in some deployments stored names are compared without regard
    to case, which once let a learner register a retired name again by
    changing its capitalisation. Both records of such a pair are built
    directly here: registering a second account under a name a record holds is
    refused a frame below this API, so the state cannot be reached through
    registration, and deployments hold it from before that refusal existed.

    Every operation that finds its record by that name refuses the request,
    rather than acting on whichever of the two the database offered last.
    """

    def setUp(self):
        super().setUp()
        self.detail_url = reverse("user_v2:retirement_detail", kwargs={"username": "subject"})
        self.erasure_url = reverse("user_v2:retirement_erasure", kwargs={"username": "subject"})
        self.queue_url = reverse(
            "user_v2:retirement_partner_report_detail", kwargs={"username": "subject"},
        )
        self.cleanup_url = reverse("user_v2:retirement_cleanup_list")
        self.partner_cleanup_url = reverse("user_v2:retirement_partner_report_cleanup_list")

    def two_retirements_under_one_name(self, state_name):
        """Return two retirement records requested under the one name ``subject``."""
        held = self.retirement_in(state_name, user=self.subject)
        twin = self.retirement_in(state_name, user=UserFactory.create(username="subject-twin"))
        UserRetirementStatus.objects.filter(id=twin.id).update(original_username="subject")
        return held, twin

    def two_partner_reports_under_one_name(self, taken=True):
        """Return two partner report entries registered under the one name ``subject``."""
        entries = [
            UserRetirementPartnerReportingStatus.objects.create(
                user=user,
                original_username=user.username,
                original_email=user.email,
                original_name=user.profile.name,
                is_being_processed=taken,
            )
            for user in (self.subject, UserFactory.create(username="subject-twin"))
        ]
        UserRetirementPartnerReportingStatus.objects.filter(id=entries[1].id).update(
            original_username="subject",
        )
        return entries

    def assert_the_name_was_refused(self, response):
        """Assert the answer is the refusal of an ambiguous name, and return its envelope."""
        envelope = assert_error_envelope(
            response,
            expected_status=status.HTTP_400_BAD_REQUEST,
            expected_type_slug=AMBIGUOUS_NAME_SLUG,
        )
        assert envelope["detail"] == view_services.NAME_IS_AMBIGUOUS
        assert envelope["instance"] == response.request["PATH_INFO"]
        return envelope

    def test_a_refusal_that_names_one_account_says_so_about_the_request_as_a_whole(self):
        """One name is refused at the address that carries it, so there is no record to key it to."""
        self.two_retirements_under_one_name("LOCKING_ACCOUNT")
        self.login("worker")
        envelope = self.assert_the_name_was_refused(
            self.client.patch(
                self.detail_url, {"new_state": "LOCKING_COMPLETE", "response": "done"}, format="json",
            ),
        )
        assert envelope["errors"] == {"non_field_errors": [view_services.NAME_IS_AMBIGUOUS]}

    def test_moving_the_retirement_is_refused_and_neither_record_moves(self):
        held, twin = self.two_retirements_under_one_name("LOCKING_ACCOUNT")
        self.login("worker")
        self.assert_the_name_was_refused(
            self.client.patch(
                self.detail_url, {"new_state": "LOCKING_COMPLETE", "response": "done"}, format="json",
            ),
        )
        for record in (held, twin):
            record.refresh_from_db()
            assert record.current_state.state_name == "LOCKING_ACCOUNT"

    def test_cancelling_is_refused_and_neither_request_is_undone(self):
        held, twin = self.two_retirements_under_one_name("PENDING")
        self.login("canceller")
        self.assert_the_name_was_refused(self.client.delete(self.detail_url))
        assert set(UserRetirementStatus.objects.values_list("id", flat=True)) == {held.id, twin.id}
        assert UserRetirementRequest.objects.count() == 2

    def test_erasing_is_refused_and_nothing_is_erased(self):
        self.two_retirements_under_one_name("RETIRING_LMS")
        self.subject.profile.city = "Somewhere"
        self.subject.profile.save()
        self.login("worker")
        self.assert_the_name_was_refused(
            self.client.post(self.erasure_url, {"scope": "lms_core"}, format="json"),
        )
        self.subject.refresh_from_db()
        assert (self.subject.username, self.subject.email) == ("subject", "subject@example.com")
        assert self.subject.is_active is True
        assert UserProfile.objects.get(user=self.subject).city == "Somewhere"

    def test_queueing_for_partner_reporting_is_refused_and_nothing_is_queued(self):
        self.two_retirements_under_one_name("RETIRING_LMS")
        CourseEnrollmentFactory.create(user=self.subject, course_id=COURSE_KEY)
        self.login("worker")
        self.assert_the_name_was_refused(self.client.put(self.queue_url, {}, format="json"))
        assert UserRetirementPartnerReportingStatus.objects.count() == 0

    def test_the_batch_disposal_is_refused_whole_and_nothing_is_deleted(self):
        held, twin = self.two_retirements_under_one_name("COMPLETE")
        self.login("worker")
        envelope = self.assert_the_name_was_refused(
            self.client.post(self.cleanup_url, {"usernames": ["subject"]}, format="json"),
        )
        assert envelope["errors"] == {"subject": [view_services.SEVERAL_RECORDS_HOLD_THIS_NAME]}
        for record in (held, twin):
            record.refresh_from_db()
            assert record.original_username == "subject"
            assert record.original_email != view_services.REDACTED_EMAIL

    def test_a_batch_naming_it_alongside_a_name_held_once_deletes_neither(self):
        """A batch is acted on whole or not at all, so one ambiguous name holds the rest back."""
        held, twin = self.two_retirements_under_one_name("COMPLETE")
        alone = self.retirement_in("COMPLETE", user=UserFactory.create(username="held-once"))
        self.login("worker")
        self.assert_the_name_was_refused(
            self.client.post(
                self.cleanup_url, {"usernames": ["subject", "held-once"]}, format="json",
            ),
        )
        assert set(UserRetirementStatus.objects.values_list("id", flat=True)) == {
            held.id, twin.id, alone.id,
        }

    def test_the_earlier_batch_disposal_refuses_the_same_name(self):
        """The address this one replaces refuses it too, and deletes nothing."""
        held, twin = self.two_retirements_under_one_name("COMPLETE")
        self.login("worker")
        response = self.client.post(
            EARLIER_RETIREMENT_CLEANUP, {"usernames": ["subject"]}, format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert set(UserRetirementStatus.objects.values_list("id", flat=True)) == {held.id, twin.id}

    def test_the_partner_report_disposal_is_refused_whole_and_nothing_is_deleted(self):
        entries = self.two_partner_reports_under_one_name()
        self.login("worker")
        envelope = self.assert_the_name_was_refused(
            self.client.post(self.partner_cleanup_url, {"usernames": ["subject"]}, format="json"),
        )
        assert envelope["errors"] == {"subject": [view_services.SEVERAL_RECORDS_HOLD_THIS_NAME]}
        assert set(
            UserRetirementPartnerReportingStatus.objects.values_list("id", flat=True),
        ) == {entry.id for entry in entries}

    def test_the_earlier_partner_report_disposal_refuses_the_same_name(self):
        entries = self.two_partner_reports_under_one_name()
        self.login("worker")
        response = self.client.post(
            EARLIER_PARTNER_REPORT_CLEANUP, [{"original_username": "subject"}], format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert set(
            UserRetirementPartnerReportingStatus.objects.values_list("id", flat=True),
        ) == {entry.id for entry in entries}

    def test_reading_the_record_of_the_account_addressed_still_answers(self):
        """A read addresses the account rather than the name a record keeps, so it is unambiguous."""
        held, _twin = self.two_retirements_under_one_name("LOCKING_ACCOUNT")
        self.login("worker")
        response = self.client.get(self.detail_url)
        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.json()["id"] == held.id

    def test_the_queue_carries_both_records(self):
        """Nothing is hidden by the refusal: the page answers with every record it holds."""
        held, twin = self.two_retirements_under_one_name("LOCKING_ACCOUNT")
        self.login("worker")
        response = self.client.get(reverse("user_v2:retirement_list"))
        assert [row["id"] for row in response.json()["results"]] == [held.id, twin.id]


@skip_unless_lms
class RetirementSelfDescriptionTests(RetirementApiTestCase):
    """
    What each address of this pipeline says about itself to a caller that asks.

    The description a view class carries is what an OPTIONS request is
    answered with, so it is published text: a client and a documentation
    browser read it beside the description of the same address in the schema.
    These hold two of them to being the text the class carries, which the
    checks on that text elsewhere hold to being written for the caller.
    """

    def test_the_queue_answers_with_the_description_it_carries(self):
        self.login("worker")
        answer = self.client.options(reverse("user_v2:retirement_list"))
        assert answer.status_code == status.HTTP_200_OK
        assert answer.json()["description"] == RetirementViewSet().get_view_description()
        assert answer.json()["description"].startswith(
            "The accounts being retired, addressed by the name each was registered under.",
        )

    def test_the_disposal_answers_with_the_description_it_carries(self):
        self.login("worker")
        answer = self.client.options(reverse("user_v2:retirement_cleanup_list"))
        assert answer.status_code == status.HTTP_200_OK
        assert answer.json()["description"] == RetirementCleanupViewSet().get_view_description()
        assert answer.json()["description"].startswith(
            "Disposes of a set of finished retirement records.",
        )


#: The view classes this pipeline is served by, read from the module that
#: routes them so that an address mounted later is covered by the sweep below.
RETIREMENT_VIEW_CLASSES = (
    RetirementViewSet,
    RetirementPartnerReportViewSet,
    RetirementCleanupViewSet,
    RetirementPartnerReportCleanupViewSet,
)


def mounted_retirement_operations():
    """Return every address of this pipeline with the methods it serves, by the name it is routed under."""
    return {
        (entry.name, method)
        for entry in v2_urls.urlpatterns
        if getattr(entry.callback, "cls", None) in RETIREMENT_VIEW_CLASSES
        for method in getattr(entry.callback, "actions", {})
    }


@skip_unless_lms
class UnsatisfiableAcceptTests(RetirementApiTestCase):
    """
    What each address of this pipeline answers a request it can give no answer to.

    Which representations an answer may be given in is settled as the request
    enters, before the record it names is read or written, so the refusal
    reaches an address that answers with a body and one that answers with none
    alike. Every operation here publishes that refusal, and these are the
    measurements it rests on: each address answers it, and the records each of
    them would otherwise have acted on -- including the ones two of them
    delete and the account data one of them erases -- are left as they were.
    """

    UNANSWERABLE = "text/csv"

    def setUp(self):
        super().setUp()
        self.finished = self.retirement_in("COMPLETE", user=self.subject)
        self.working = self.retirement_in(
            "RETIRING_LMS", user=UserFactory.create(username="working-subject"),
        )
        self.pending = self.retirement_in(
            "PENDING", user=UserFactory.create(username="pending-subject"),
        )
        self.subject.profile.city = "Somewhere"
        self.subject.profile.save()
        self.entry = UserRetirementPartnerReportingStatus.objects.create(
            user=self.working.user,
            original_username=self.working.user.username,
            original_email=self.working.user.email,
            original_name=self.working.user.profile.name,
            is_being_processed=True,
        )

    def requests(self):
        """Return a request to every method of every address, by the caller that address admits."""
        return {
            ("retirement_list", "get"): ("worker", reverse("user_v2:retirement_list"), None),
            ("retirement_detail", "get"): (
                "worker", reverse("user_v2:retirement_detail", kwargs={"username": "subject"}), None,
            ),
            # Both reads are mounted for a request that asks for the answer
            # without its body as well, and the refusal is settled before
            # either is read, so each is sent that request too.
            ("retirement_list", "head"): ("worker", reverse("user_v2:retirement_list"), None),
            ("retirement_detail", "head"): (
                "worker", reverse("user_v2:retirement_detail", kwargs={"username": "subject"}), None,
            ),
            ("retirement_detail", "patch"): (
                "worker",
                reverse("user_v2:retirement_detail", kwargs={"username": "working-subject"}),
                {"new_state": "RETIRING_LMS_COMPLETE", "response": "done"},
            ),
            ("retirement_detail", "delete"): (
                "canceller",
                reverse("user_v2:retirement_detail", kwargs={"username": "pending-subject"}),
                None,
            ),
            ("retirement_erasure", "post"): (
                "worker",
                reverse("user_v2:retirement_erasure", kwargs={"username": "working-subject"}),
                {"scope": "lms_core"},
            ),
            ("retirement_cleanup_list", "post"): (
                "worker", reverse("user_v2:retirement_cleanup_list"), {"usernames": ["subject"]},
            ),
            ("retirement_partner_report_list", "post"): (
                "worker", reverse("user_v2:retirement_partner_report_list"), None,
            ),
            ("retirement_partner_report_detail", "put"): (
                "worker",
                reverse(
                    "user_v2:retirement_partner_report_detail",
                    kwargs={"username": "working-subject"},
                ),
                {},
            ),
            ("retirement_partner_report_cleanup_list", "post"): (
                "worker",
                reverse("user_v2:retirement_partner_report_cleanup_list"),
                {"usernames": ["working-subject"]},
            ),
        }

    def send_each(self):
        """Send every request of the table asking for a representation none can answer in."""
        answered = {}
        for (name, method), (caller, url, body) in self.requests().items():
            self.client.logout()
            self.login(caller)
            send = getattr(self.client, method)
            if body is None:
                response = send(url, HTTP_ACCEPT=self.UNANSWERABLE)
            else:
                response = send(url, body, format="json", HTTP_ACCEPT=self.UNANSWERABLE)
            answered[(name, method)] = response.status_code
        return answered

    def test_every_address_of_this_pipeline_is_covered_here(self):
        """A method mounted later is sent one of these requests, or this fails."""
        assert set(self.requests()) == mounted_retirement_operations()

    def test_each_address_refuses_an_accept_it_cannot_satisfy(self):
        assert self.send_each() == {
            address: status.HTTP_406_NOT_ACCEPTABLE for address in self.requests()
        }

    def test_the_refusal_comes_back_in_the_body_this_version_answers_with(self):
        """The refusal is readable even though the caller asked for a representation it is not in."""
        self.login("worker")
        assert_error_envelope(
            self.client.get(reverse("user_v2:retirement_list"), HTTP_ACCEPT=self.UNANSWERABLE),
            expected_status=status.HTTP_406_NOT_ACCEPTABLE,
            expected_type_slug=NOT_ACCEPTABLE_SLUG,
        )

    def test_nothing_is_moved_erased_or_deleted_on_the_way_to_the_refusal(self):
        self.send_each()
        assert set(UserRetirementStatus.objects.values_list("id", flat=True)) == {
            self.finished.id, self.working.id, self.pending.id,
        }
        assert UserRetirementRequest.objects.count() == 3
        for record, state_name in (
            (self.finished, "COMPLETE"),
            (self.working, "RETIRING_LMS"),
            (self.pending, "PENDING"),
        ):
            record.refresh_from_db()
            assert record.current_state.state_name == state_name
            assert record.original_email != view_services.REDACTED_EMAIL
        self.subject.refresh_from_db()
        assert (self.subject.username, self.subject.email) == ("subject", "subject@example.com")
        assert self.subject.is_active is True
        assert UserProfile.objects.get(user=self.subject).city == "Somewhere"
        assert set(
            UserRetirementPartnerReportingStatus.objects.values_list("id", flat=True),
        ) == {self.entry.id}
        self.entry.refresh_from_db()
        assert self.entry.is_being_processed is True


@skip_unless_lms
class FormEncodedBodyTests(RetirementApiTestCase):
    """
    A body sent as a web form is refused wherever a body is read.

    These operations read a body as JSON alone, as the operations they replace
    do. The narrowing is kept rather than widened to every media type the
    platform can parse because one of them erases personal data for good and
    cannot be undone, and a form-encoded request is one a page on another site
    can be made to send.

    The operations that read no body are unaffected, which is why the
    narrowing is read from the view rather than applied to each method: a
    method that never reaches the body never reaches the parser either.
    """

    FORM = "application/x-www-form-urlencoded"

    def setUp(self):
        super().setUp()
        self.retirement = self.retirement_in("RETIRING_LMS", user=self.subject)
        self.detail_url = reverse("user_v2:retirement_detail", kwargs={"username": "subject"})
        self.erasure_url = reverse("user_v2:retirement_erasure", kwargs={"username": "subject"})

    def assert_the_body_was_unreadable(self, response):
        """Assert the answer names the media type as one this endpoint cannot read."""
        assert_error_envelope(
            response,
            expected_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            expected_type_slug=UNSUPPORTED_MEDIA_TYPE_SLUG,
        )

    def test_a_form_body_is_refused_rather_than_erasing_the_account(self):
        self.subject.profile.city = "Somewhere"
        self.subject.profile.save()
        self.login("worker")
        self.assert_the_body_was_unreadable(
            self.client.post(self.erasure_url, "scope=lms_core", content_type=self.FORM),
        )
        self.subject.refresh_from_db()
        assert (self.subject.username, self.subject.email) == ("subject", "subject@example.com")
        assert UserProfile.objects.get(user=self.subject).city == "Somewhere"

    def test_the_erasure_this_one_replaces_refuses_the_same_body(self):
        """Both versions answer the same, which is what keeps a caller's request refused."""
        self.login("worker")
        response = self.client.post(
            EARLIER_CORE_ERASURE, "username=subject", content_type=self.FORM,
        )
        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, response.content
        self.subject.refresh_from_db()
        assert self.subject.username == "subject"

    def test_a_form_body_is_refused_when_moving_a_retirement(self):
        """The body names a step this deployment runs, so only its media type can refuse it."""
        self.login("worker")
        self.assert_the_body_was_unreadable(
            self.client.patch(
                self.detail_url, "new_state=LMS_COMPLETE&response=done",
                content_type=self.FORM,
            ),
        )
        self.retirement.refresh_from_db()
        assert self.retirement.current_state.state_name == "RETIRING_LMS"

    def test_a_form_body_is_refused_by_each_batch_disposal(self):
        self.login("worker")
        for name in (
            "user_v2:retirement_cleanup_list",
            "user_v2:retirement_partner_report_cleanup_list",
        ):
            with self.subTest(address=name):
                self.assert_the_body_was_unreadable(
                    self.client.post(reverse(name), "usernames=subject", content_type=self.FORM),
                )

    def test_taking_partner_reports_reads_no_body_and_is_answered(self):
        """It takes what is queued and reads nothing from the request, as the earlier one does."""
        self.login("worker")
        taken = self.client.post(
            reverse("user_v2:retirement_partner_report_list"), "anything=at-all",
            content_type=self.FORM,
        )
        earlier = self.client.post(EARLIER_PARTNER_REPORT, "anything=at-all", content_type=self.FORM)
        assert (taken.status_code, earlier.status_code) == (
            status.HTTP_200_OK, status.HTTP_200_OK,
        ), (taken.content, earlier.content)

    def test_cancelling_is_answered_whatever_media_type_is_sent(self):
        """It names the retirement in the address and reads no body, so no parser is reached."""
        self.retirement_in("PENDING", user=self.learner)
        self.login("canceller")
        response = self.client.delete(
            reverse("user_v2:retirement_detail", kwargs={"username": "learner-caller"}),
            "nothing=here",
            content_type=self.FORM,
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT, response.content

    def test_queueing_for_partner_reporting_is_answered_whatever_media_type_is_sent(self):
        """The name is in the address, where the earlier version read it from the body."""
        CourseEnrollmentFactory.create(user=self.subject, course_id=COURSE_KEY)
        self.login("worker")
        response = self.client.put(
            reverse("user_v2:retirement_partner_report_detail", kwargs={"username": "subject"}),
            "nothing=here",
            content_type=self.FORM,
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT, response.content
        assert UserRetirementPartnerReportingStatus.objects.count() == 1


@skip_unless_lms
class UnservedMethodTests(RetirementApiTestCase):
    """
    A method an address does not serve is answered as not served, not as refused.

    Which caller may make a request is decided before the method is
    dispatched, and at an address whose capability follows the operation there
    is no operation to follow for a method it does not serve. The answer is
    then the dispatcher's own: the methods the address does serve, and nothing
    about a capability. A caller who is not signed in is still told to
    authenticate, because being signed in is asked of every method alike.
    """

    #: A method each retirement address of this version does not serve, and the
    #: methods it does. The second group's addresses guard every method with
    #: the same classes, so they answer a capability refusal before this one.
    PER_OPERATION = (
        ("post", "user_v2:retirement_detail", {"username": "subject"}),
        ("put", "user_v2:retirement_detail", {"username": "subject"}),
        ("delete", "user_v2:retirement_list", None),
        ("patch", "user_v2:retirement_list", None),
        ("get", "user_v2:retirement_erasure", {"username": "subject"}),
        ("get", "user_v2:retirement_cleanup_list", None),
    )
    PER_VIEW = (
        ("get", "user_v2:retirement_partner_report_list", None),
        ("get", "user_v2:retirement_partner_report_detail", {"username": "subject"}),
        ("get", "user_v2:retirement_partner_report_cleanup_list", None),
    )

    #: What each address whose capability follows the operation serves, as its
    #: refusal reports it.
    SERVED = {
        "user_v2:retirement_list": {"GET", "HEAD", "OPTIONS"},
        "user_v2:retirement_detail": {"GET", "HEAD", "PATCH", "DELETE", "OPTIONS"},
        "user_v2:retirement_erasure": {"POST", "OPTIONS"},
        "user_v2:retirement_cleanup_list": {"POST", "OPTIONS"},
    }

    def setUp(self):
        super().setUp()
        self.retirement_in("LOCKING_ACCOUNT", user=self.subject)

    def send(self, method, name, kwargs):
        """Send a method the named address does not serve."""
        return getattr(self.client, method)(reverse(name, kwargs=kwargs))

    def test_a_caller_holding_the_capability_is_told_the_method_is_not_served(self):
        self.login("worker")
        for method, name, kwargs in self.PER_OPERATION + self.PER_VIEW:
            with self.subTest(method=method, address=name):
                assert_error_envelope(
                    self.send(method, name, kwargs),
                    expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
                    expected_type_slug=REFUSED_METHOD_SLUG,
                )

    def test_the_refusal_names_the_methods_the_address_does_serve(self):
        """The answer comes from the dispatcher, so it says what is served there."""
        self.login("worker")
        for method, name, kwargs in self.PER_OPERATION:
            with self.subTest(method=method, address=name):
                response = self.send(method, name, kwargs)
                assert set(response["Allow"].replace(" ", "").split(",")) == self.SERVED[name]

    def test_a_caller_without_the_capability_is_told_the_same_by_the_collection(self):
        """There is no operation at that address for a capability to be asked for."""
        self.login("learner")
        for method, name, kwargs in self.PER_OPERATION:
            with self.subTest(method=method, address=name):
                assert_error_envelope(
                    self.send(method, name, kwargs),
                    expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
                    expected_type_slug=REFUSED_METHOD_SLUG,
                )

    def test_a_caller_without_the_capability_is_refused_where_every_method_carries_one(self):
        """These addresses ask for the capability whatever the method, so that answer comes first."""
        self.login("learner")
        for method, name, kwargs in self.PER_VIEW:
            with self.subTest(method=method, address=name):
                assert_error_envelope(
                    self.send(method, name, kwargs),
                    expected_status=status.HTTP_403_FORBIDDEN,
                    expected_type_slug="authz",
                )

    def test_an_address_that_serves_one_method_refuses_the_same_caller_instead(self):
        """Whoever may call is decided before the method is, so where every method carries the
        same capability the caller without it is refused rather than told what is served."""
        self.login("learner")
        assert self.client.put(
            reverse("accounts_retirement_cleanup"),
        ).status_code == status.HTTP_403_FORBIDDEN
        assert_error_envelope(
            self.send("put", "user_v2:retirement_detail", {"username": "subject"}),
            expected_status=status.HTTP_405_METHOD_NOT_ALLOWED,
            expected_type_slug=REFUSED_METHOD_SLUG,
        )

    def test_an_anonymous_caller_is_told_to_authenticate(self):
        self.client.logout()
        for method, name, kwargs in self.PER_OPERATION + self.PER_VIEW:
            with self.subTest(method=method, address=name):
                assert_error_envelope(
                    self.send(method, name, kwargs),
                    expected_status=status.HTTP_401_UNAUTHORIZED,
                    expected_type_slug="authn",
                )

    def test_an_action_the_mapping_does_not_name_still_reaches_no_capability(self):
        """An action added later is refused; it is an unserved method that is not."""
        viewset = RetirementViewSet()
        viewset.action = "an_action_added_later"
        assert [
            permission.has_permission(mock.Mock(user=self.superuser), viewset)
            for permission in viewset.get_permissions()
        ][-1] is False
        viewset.action = None
        # Read from the view rather than named here: the platform's own JWT
        # middleware adds a class to every view it serves, so what the classes
        # of this view are is not fixed at import.
        assert [type(permission) for permission in viewset.get_permissions()] == list(
            RetirementViewSet.permission_classes,
        )
