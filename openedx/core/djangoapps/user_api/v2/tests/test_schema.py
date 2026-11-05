"""Tests for what version 2 of the User API publishes in the OpenAPI schema."""

import copy
import json
from importlib import import_module
from unittest import mock

import jsonschema
import pytest
from django.conf import settings
from django.urls import reverse
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.plumbing import get_override
from drf_spectacular.settings import patched_settings
from rest_framework import serializers
from rest_framework.test import APIClient
from waffle.testutils import override_switch

from common.djangoapps.student.models import PendingNameChange, UserProfile
from common.djangoapps.student.tests.factories import AccountRecoveryFactory, AdminFactory, UserFactory
from lms.djangoapps.verify_student.models import IDVerificationAttempt, VerificationAttempt
from lms.lib.spectacular import lms_mark_legacy_paths_deprecated
from openedx.core.apidocs import get_api_docs_settings
from openedx.core.djangoapps.profile_images.tests.helpers import make_image_file
from openedx.core.djangoapps.site_configuration.tests.test_util import with_site_configuration_context
from openedx.core.djangoapps.user_api.accounts.image_helpers import (
    get_profile_image_names,
    get_profile_image_storage,
)
from openedx.core.djangoapps.user_api.accounts.utils import ENABLE_SECONDARY_EMAIL_FEATURE_SWITCH
from openedx.core.djangoapps.user_api.models import UserRetirementPartnerReportingStatus
from openedx.core.djangoapps.user_api.schema import (
    RENAMED_OPERATION_IDS,
    SUPERSEDED_USER_PATHS,
    mark_superseded_user_paths,
)
from openedx.core.djangoapps.user_api.tests.factories import UserPreferenceFactory
from openedx.core.djangoapps.user_api.v2.serializers import (
    ACCOUNT_DEFAULT_SORT,
    ACCOUNT_MINIMAL_FIELDS,
    BATCH_OUTCOMES,
    ERASURE_SCOPES,
    EXPIRATION_DAY_PATTERN,
    PREFERENCE_NAME_MAX_LENGTH,
    REPLACEMENT_MAX_MAPPINGS,
    REPLACEMENT_OUTCOMES,
    AccountPreferenceChangesField,
    AccountPreferenceDocumentField,
    AccountQuerySerializer,
    RetirementBatchSerializer,
    TimeZoneQuerySerializer,
    VerificationQuerySerializer,
)
from openedx.core.djangoapps.user_api.v2.view_services import claim_partner_reports
from openedx.core.djangoapps.user_api.v2.views import (
    PREFERENCE_KEY_PATTERN_PARTIAL,
    RETIREMENT_SORT_FIELDS,
)
from openedx.core.djangolib.testing.utils import skip_unless_lms

PASSWORD = "Password1234"
#: The site's API key, which the user directory and the stored preferences are read with.
API_KEY = "schema-test-api-key"

#: Stands for a property a response does not carry, where that is the assertion.
ABSENT = "absent"

ACCOUNT_LIST_PATH = "/api/user/v2/accounts/"
ACCOUNT_MEMBER_PATH = "/api/user/v2/accounts/{username}/"
ACCOUNT_DEACTIVATION_PATH = "/api/user/v2/accounts/{username}/deactivation/"
PROFILE_IMAGE_PATH = "/api/user/v2/accounts/{username}/profile_image/"
VERIFICATION_STATUS_PATH = "/api/user/v2/accounts/{username}/verification_status/"
VERIFICATION_LIST_PATH = "/api/user/v2/accounts/{username}/verifications/"
EMAIL_SEARCH_PATH = "/api/user/v2/email_searches/"
PREFERENCE_DOCUMENT_PATH = "/api/user/v2/accounts/{username}/preferences/"
PREFERENCE_NAME_PATH = "/api/user/v2/accounts/{username}/preferences/{key}/"
EMAIL_OPT_IN_PATH = "/api/user/v2/email_opt_ins/"

#: What both preference addresses say the name in them is read as.
ACCOUNT_PREFERENCES_USERNAME_DESCRIPTION = (
    "Unique name of the user whose preferences are addressed. The name is read as it is stored, "
    "so `me` here names an account holding that name rather than the account of the caller "
    "making the request."
)
NAME_CHANGE_PATH = "/api/user/v2/name_changes/"
NAME_CHANGE_CONFIRMATION_PATH = "/api/user/v2/name_changes/{username}/confirmations/"

USER_LIST_PATH = "/api/user/v2/users/"
USER_MEMBER_PATH = "/api/user/v2/users/{username}/"
PREFERENCE_LIST_PATH = "/api/user/v2/user_preferences/"
PREFERENCE_MEMBER_PATH = "/api/user/v2/user_preferences/{username},{key}/"
TIME_ZONE_LIST_PATH = "/api/user/v2/time_zones/"
RETIREMENT_LIST_PATH = "/api/user/v2/retirements/"
RETIREMENT_MEMBER_PATH = "/api/user/v2/retirements/{username}/"
RETIREMENT_ERASURE_PATH = "/api/user/v2/retirements/{username}/erasures/"
RETIREMENT_CLEANUP_PATH = "/api/user/v2/retirement_cleanups/"
PARTNER_REPORT_LIST_PATH = "/api/user/v2/retirement_partner_reports/"
PARTNER_REPORT_MEMBER_PATH = "/api/user/v2/retirement_partner_reports/{username}/"
PARTNER_REPORT_CLEANUP_PATH = "/api/user/v2/retirement_partner_report_cleanups/"
USERNAME_REPLACEMENT_PATH = "/api/user/v2/username_replacements/"

#: The meaning published for the part of a retirement's address that carries
#: the name the account was registered under.
RETIREMENT_USERNAME_DESCRIPTION = (
    "Name the account being retired was registered under. A retired account is stored under a "
    "hashed name, and either name addresses the same retirement when one is read; a write "
    "addresses the registered name alone, spelled exactly as it was registered."
)
PARTNER_REPORT_USERNAME_DESCRIPTION = (
    "Name the account was registered under, spelled exactly as it was registered."
)

#: The addresses that answer with a page of rows, and the ones that answer with
#: a single record.
LIST_PATHS = {
    ACCOUNT_LIST_PATH,
    VERIFICATION_LIST_PATH,
    USER_LIST_PATH,
    PREFERENCE_LIST_PATH,
    TIME_ZONE_LIST_PATH,
    RETIREMENT_LIST_PATH,
}
MEMBER_PATHS = {
    ACCOUNT_MEMBER_PATH,
    VERIFICATION_STATUS_PATH,
    USER_MEMBER_PATH,
    PREFERENCE_MEMBER_PATH,
    PREFERENCE_DOCUMENT_PATH,
    PREFERENCE_NAME_PATH,
    RETIREMENT_MEMBER_PATH,
    PARTNER_REPORT_MEMBER_PATH,
}

#: The addresses that act on a resource rather than answer with one.
ACTION_PATHS = {
    EMAIL_SEARCH_PATH,
    EMAIL_OPT_IN_PATH,
    ACCOUNT_DEACTIVATION_PATH,
    PROFILE_IMAGE_PATH,
    NAME_CHANGE_PATH,
    NAME_CHANGE_CONFIRMATION_PATH,
    RETIREMENT_ERASURE_PATH,
    RETIREMENT_CLEANUP_PATH,
    PARTNER_REPORT_LIST_PATH,
    PARTNER_REPORT_CLEANUP_PATH,
    USERNAME_REPLACEMENT_PATH,
}

#: The addresses a generated client library is built from. The retirement
#: pipeline's work is deliberately not among them: one service calls those
#: addresses by path literal, and a library for irreversible destruction of
#: personal data invites a caller who has not read what the steps do. The
#: request an account's owner makes for their own account is in, because it is
#: not that work: it reaches no account but the caller's, it is made by the
#: applications shipped to learners, and it is the sibling of the other
#: account-family operations below. The replacement of several names is out
#: for the same reason as the pipeline: its one caller addresses it by literal.
SDK_PATHS = {
    ACCOUNT_LIST_PATH,
    ACCOUNT_MEMBER_PATH,
    VERIFICATION_STATUS_PATH,
    VERIFICATION_LIST_PATH,
    PREFERENCE_DOCUMENT_PATH,
    PREFERENCE_NAME_PATH,
    EMAIL_SEARCH_PATH,
    EMAIL_OPT_IN_PATH,
    ACCOUNT_DEACTIVATION_PATH,
    PROFILE_IMAGE_PATH,
    NAME_CHANGE_PATH,
    NAME_CHANGE_CONFIRMATION_PATH,
    RETIREMENT_LIST_PATH,
}

VERSION_TWO_PATHS = LIST_PATHS | MEMBER_PATHS | ACTION_PATHS

#: The addresses the retirement pipeline is served at.
RETIREMENT_PATHS = {
    RETIREMENT_LIST_PATH,
    RETIREMENT_MEMBER_PATH,
    RETIREMENT_ERASURE_PATH,
    RETIREMENT_CLEANUP_PATH,
    PARTNER_REPORT_LIST_PATH,
    PARTNER_REPORT_MEMBER_PATH,
    PARTNER_REPORT_CLEANUP_PATH,
}

PAGINATION_MEMBERS = {"count", "num_pages", "current_page", "start", "next", "previous", "results"}

#: The prefix every mount serving a user address is configured under.
USER_MOUNT = "api/user/"

#: The components this version contributes to the document. The two names
#: ending in ``Enum`` describe sets of choices and are configured in the
#: service's settings rather than derived from the fields carrying them, so
#: that another field of the same name cannot move them.
VERSION_TWO_COMPONENTS = (
    "Account",
    "AccountExtendedField",
    "AccountLanguage",
    "AccountMinimal",
    "AccountPreference",
    "AccountPreferenceValue",
    "AccountProfileImage",
    "AccountProjection",
    "AccountResponse",
    "AccountSocialLink",
    "EmailOptIn",
    "EmailSearch",
    "EmailSearchResult",
    "ErrorResponse",
    "NameChange",
    "PaginatedAccountResponseList",
    "PaginatedEmailSearchResultList",
    "PaginatedVerificationList",
    "PatchedAccountUpdate",
    "ReplacementOutcomeEnum",
    "PaginatedRetirementResponseList",
    "Retirement",
    "RetirementAccount",
    "RetirementAccountProfile",
    "RetirementBatch",
    "RetirementBatchOutcome",
    "RetirementBatchResult",
    "RetirementBatchResultOutcomeEnum",
    "RetirementErasure",
    "RetirementErasureScopeEnum",
    "RetirementMinimal",
    "RetirementPartnerReportRecord",
    "RetirementProjection",
    "RetirementRequest",
    "RetirementResponse",
    "RetirementUpdate",
    "PaginatedTimeZoneList",
    "PaginatedUserDirectoryResponseList",
    "PaginatedUserPreferenceRecordResponseList",
    "ProfileImageUpload",
    "TimeZone",
    "UserDirectory",
    "UserDirectoryMinimal",
    "UserDirectoryProjection",
    "UserDirectoryResponse",
    "UserPreferenceRecord",
    "UserPreferenceRecordMinimal",
    "UserPreferenceRecordProjection",
    "UserPreferenceRecordResponse",
    "UsernameReplacementOutcome",
    "UsernameReplacementRequest",
    "UsernameReplacementResult",
    "Verification",
    "VerificationStatus",
)

#: Every body these endpoints return: the published path it is documented
#: under, the address and parameters that produce it, and the component that
#: describes each record the body carries.
SHAPE_CASES = (
    ("/api/user/v2/users/", "/api/user/v2/users/", {}, "UserDirectory"),
    ("/api/user/v2/users/", "/api/user/v2/users/", {"view": "minimal"}, "UserDirectoryMinimal"),
    ("/api/user/v2/users/", "/api/user/v2/users/", {"fields": "username,email"}, "UserDirectoryProjection"),
    (
        "/api/user/v2/users/",
        "/api/user/v2/users/",
        {"view": "minimal", "fields": "username"},
        "UserDirectoryProjection",
    ),
    ("/api/user/v2/users/{username}/", "/api/user/v2/users/shape-admin/", {}, "UserDirectory"),
    (
        "/api/user/v2/users/{username}/",
        "/api/user/v2/users/shape-admin/",
        {"view": "minimal"},
        "UserDirectoryMinimal",
    ),
    (
        "/api/user/v2/users/{username}/",
        "/api/user/v2/users/shape-admin/",
        {"fields": "id,url"},
        "UserDirectoryProjection",
    ),
    ("/api/user/v2/user_preferences/", "/api/user/v2/user_preferences/", {}, "UserPreferenceRecord"),
    ("/api/user/v2/user_preferences/", "/api/user/v2/user_preferences/", {"view": "full"}, "UserPreferenceRecord"),
    (
        "/api/user/v2/user_preferences/",
        "/api/user/v2/user_preferences/",
        {"view": "minimal"},
        "UserPreferenceRecordMinimal",
    ),
    (
        "/api/user/v2/user_preferences/",
        "/api/user/v2/user_preferences/",
        {"fields": "key,value"},
        "UserPreferenceRecordProjection",
    ),
    (
        "/api/user/v2/user_preferences/{username},{key}/",
        "/api/user/v2/user_preferences/shape-admin,pref-lang/",
        {},
        "UserPreferenceRecord",
    ),
    (
        "/api/user/v2/user_preferences/{username},{key}/",
        "/api/user/v2/user_preferences/shape-admin,pref-lang/",
        {"view": "full"},
        "UserPreferenceRecord",
    ),
    (
        "/api/user/v2/user_preferences/{username},{key}/",
        "/api/user/v2/user_preferences/shape-admin,pref-lang/",
        {"view": "minimal"},
        "UserPreferenceRecordMinimal",
    ),
    (
        "/api/user/v2/user_preferences/{username},{key}/",
        "/api/user/v2/user_preferences/shape-admin,pref-lang/",
        {"fields": "value"},
        "UserPreferenceRecordProjection",
    ),
    (
        PREFERENCE_NAME_PATH,
        "/api/user/v2/accounts/shape-admin/preferences/pref-lang/",
        {},
        "AccountPreference",
    ),
    ("/api/user/v2/time_zones/", "/api/user/v2/time_zones/", {"country_code": "FR"}, "TimeZone"),
    ("/api/user/v2/accounts/", "/api/user/v2/accounts/", {}, "Account"),
    ("/api/user/v2/accounts/", "/api/user/v2/accounts/", {"view": "minimal"}, "AccountMinimal"),
    ("/api/user/v2/accounts/", "/api/user/v2/accounts/", {"view": "shared"}, "Account"),
    (
        "/api/user/v2/accounts/",
        "/api/user/v2/accounts/",
        {"fields": "username,email"},
        "AccountProjection",
    ),
    ("/api/user/v2/accounts/{username}/", "/api/user/v2/accounts/me/", {}, "Account"),
    ("/api/user/v2/accounts/{username}/", "/api/user/v2/accounts/shape-admin/", {}, "Account"),
    (
        "/api/user/v2/accounts/{username}/",
        "/api/user/v2/accounts/shape-admin/",
        {"view": "minimal"},
        "AccountMinimal",
    ),
    (
        "/api/user/v2/accounts/{username}/",
        "/api/user/v2/accounts/shape-admin/",
        {"fields": "username"},
        "AccountProjection",
    ),
)


def as_json_schema(node):
    """
    Return a published schema node as a JSON Schema document.

    The document describes an absent value with ``nullable``, which JSON
    Schema spells as an alternative type, so a validator reading the published
    node verbatim would reject a member the API is documented to omit.
    """
    if isinstance(node, list):
        return [as_json_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    converted = {key: as_json_schema(value) for key, value in node.items() if key != "nullable"}
    if node.get("nullable"):
        return {"anyOf": [converted, {"type": "null"}]}
    return converted


def published_ordering(schema, path):
    """Return the sort parameter a list publishes."""
    return next(
        published for published in schema["paths"][path]["get"]["parameters"]
        if published["name"] == "ordering"
    )


def published_body_schema(schema, path):
    """Return what a successful body of ``path`` must satisfy, components included."""
    body = schema["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    return as_json_schema({**body, "components": schema["components"]})


def published_request_schema(schema, path, method, media_type):
    """Return what a request body of ``path`` must satisfy, components included."""
    body = schema["paths"][path][method]["requestBody"]["content"][media_type]["schema"]
    return as_json_schema({**body, "components": schema["components"]})


def published_component_schema(schema, name):
    """Return one published component as a document a validator can read on its own."""
    return as_json_schema({
        "$ref": f"#/components/schemas/{name}",
        "components": schema["components"],
    })


def records_of(body):
    """Return the records a body carries, whether it is a page of them or a single one."""
    return body["results"] if "results" in body else [body]


def assert_records_match(schema, body, component, case=None):
    """
    Assert every record of ``body`` is what the named component says it is.

    A body is checked against the one component that describes it rather than
    against the alternatives its address may answer with, because any of those
    alternatives accepts a body the others would reject, and a property no
    alternative describes is as much a difference as one typed wrongly.
    """
    validator = jsonschema.Draft202012Validator(published_component_schema(schema, component))
    published = set(schema["components"]["schemas"][component]["properties"])
    for record in records_of(body):
        assert [error.message for error in validator.iter_errors(record)] == [], (case, component, record)
        assert set(record) <= published, (case, component, set(record) - published)

#: The version 1 addresses this change supersedes, written out independently of
#: the constant the hook reads so that changing either one shows up here.
SUPERSEDED_USER_ADDRESSES = {
    "/api/user/v1/me": ACCOUNT_MEMBER_PATH,
    "/api/user/v1/accounts": ACCOUNT_LIST_PATH,
    "/api/user/v1/accounts/search_emails": EMAIL_SEARCH_PATH,
    "/api/user/v1/accounts/{username}": ACCOUNT_MEMBER_PATH,
    "/api/user/v1/accounts/{username}/deactivate/": ACCOUNT_DEACTIVATION_PATH,
    "/api/user/v1/accounts/{username}/image": PROFILE_IMAGE_PATH,
    "/api/user/v1/accounts/{username}/verification_status/": VERIFICATION_STATUS_PATH,
    "/api/user/v1/accounts/{username}/verifications/": VERIFICATION_LIST_PATH,
    "/api/user/v1/accounts/name_change/": NAME_CHANGE_PATH,
    "/api/user/v1/accounts/name_change/{username}/confirm/": NAME_CHANGE_CONFIRMATION_PATH,
    "/api/user/v1/users/": "/api/user/v2/users/",
    "/api/user/v1/users/{id}/": "/api/user/v2/users/{username}/",
    "/api/user/v1/user_prefs/": "/api/user/v2/user_preferences/",
    "/api/user/v1/user_prefs/{id}/": "/api/user/v2/user_preferences/{username},{key}/",
    "/api/user/v1/preferences/{username}": PREFERENCE_DOCUMENT_PATH,
    "/api/user/v1/preferences/{username}/{preference_key}": PREFERENCE_NAME_PATH,
    "/api/user/v1/preferences/email_opt_in/": EMAIL_OPT_IN_PATH,
    "/api/user/v1/preferences/{pref_key}/users/": "/api/user/v2/users/",
    "/api/user/v1/forum_roles/{name}/users/": "/api/user/v2/users/",
    "/api/user/v1/preferences/time_zones/": "/api/user/v2/time_zones/",
    "/api/user/v1/accounts/{username}/retirement_status/": RETIREMENT_MEMBER_PATH,
    "/api/user/v1/accounts/retirement_queue/": RETIREMENT_LIST_PATH,
    "/api/user/v1/accounts/retirements_by_status_and_date/": RETIREMENT_LIST_PATH,
    "/api/user/v1/accounts/update_retirement_status/": RETIREMENT_MEMBER_PATH,
    "/api/user/v1/accounts/retirement_cleanup/": RETIREMENT_CLEANUP_PATH,
    "/api/user/v1/accounts/retirement_partner_report/": PARTNER_REPORT_LIST_PATH,
    "/api/user/v1/accounts/retirement_partner_report_cleanup/": PARTNER_REPORT_CLEANUP_PATH,
    "/api/user/v1/accounts/cancel_retirement/": RETIREMENT_MEMBER_PATH,
    "/api/user/v1/accounts/retire/": RETIREMENT_ERASURE_PATH,
    "/api/user/v1/accounts/retire_misc/": RETIREMENT_ERASURE_PATH,
    "/api/user/v1/accounts/deactivate_logout/": RETIREMENT_LIST_PATH,
    "/api/user/v1/accounts/replace_usernames/": USERNAME_REPLACEMENT_PATH,
}

#: The method each superseded address's replacement is served with, where the
#: address replacing it serves more than one: a replacement has to be mounted
#: for the operation that is superseded, not only at the address that replaces
#: it, or a client is sent to an address that refuses the request.
SUPERSEDED_USER_METHODS = {
    "/api/user/v1/accounts": "get",
    "/api/user/v1/accounts/{username}": "get",
    "/api/user/v1/me": "get",
    "/api/user/v1/accounts/search_emails": "post",
    "/api/user/v1/accounts/{username}/deactivate/": "post",
    "/api/user/v1/accounts/{username}/image": "post",
    "/api/user/v1/accounts/{username}/verification_status/": "get",
    "/api/user/v1/accounts/{username}/verifications/": "get",
    "/api/user/v1/accounts/name_change/": "post",
    "/api/user/v1/accounts/name_change/{username}/confirm/": "post",
    "/api/user/v1/users/": "get",
    "/api/user/v1/users/{id}/": "get",
    "/api/user/v1/user_prefs/": "get",
    "/api/user/v1/user_prefs/{id}/": "get",
    "/api/user/v1/preferences/{username}": "get",
    "/api/user/v1/preferences/{username}/{preference_key}": "get",
    "/api/user/v1/preferences/email_opt_in/": "post",
    "/api/user/v1/preferences/{pref_key}/users/": "get",
    "/api/user/v1/forum_roles/{name}/users/": "get",
    "/api/user/v1/preferences/time_zones/": "get",
    "/api/user/v1/accounts/{username}/retirement_status/": "get",
    "/api/user/v1/accounts/retirement_queue/": "get",
    "/api/user/v1/accounts/retirements_by_status_and_date/": "get",
    "/api/user/v1/accounts/update_retirement_status/": "patch",
    "/api/user/v1/accounts/retirement_cleanup/": "post",
    "/api/user/v1/accounts/retirement_partner_report/": "post",
    "/api/user/v1/accounts/retirement_partner_report_cleanup/": "post",
    "/api/user/v1/accounts/cancel_retirement/": "delete",
    "/api/user/v1/accounts/retire/": "post",
    "/api/user/v1/accounts/retire_misc/": "post",
    "/api/user/v1/accounts/deactivate_logout/": "post",
    "/api/user/v1/accounts/replace_usernames/": "post",
}

def user_patterns():
    """Return the mounts of the service's URL configuration that serve user addresses."""
    configured = import_module(settings.ROOT_URLCONF).urlpatterns
    return [entry for entry in configured if str(entry.pattern).startswith(USER_MOUNT)]


@pytest.fixture(name="generated_documents", scope="module")
@skip_unless_lms
def generated_documents_fixture(django_db_setup, django_db_blocker):  # pylint: disable=unused-argument
    """
    Both documents the service publishes, each generated once and from the user mounts only.

    Each is generated under the settings it is published with, so what it says
    about a user address is what the whole document says about it.
    """
    with django_db_blocker.unblock():
        published = SchemaGenerator(patterns=user_patterns()).get_schema(request=None, public=True)
        # The whole document derives its path prefix from every address the
        # service serves, which share nothing past the root.
        with patched_settings({**get_api_docs_settings(), "SCHEMA_PATH_PREFIX": "/"}):
            service_wide = SchemaGenerator(patterns=user_patterns()).get_schema(request=None, public=True)
    return {"published": published, "service-wide": service_wide}


@pytest.fixture(name="schema")
def schema_fixture(generated_documents):
    """The schema published under the service's own drf-spectacular settings."""
    return copy.deepcopy(generated_documents["published"])


@pytest.fixture(name="service_wide")
def service_wide_fixture(generated_documents):
    """The document the service publishes for browsing its whole surface."""
    return copy.deepcopy(generated_documents["service-wide"])


def service_wide_from_user_mounts(**overrides):
    """Generate the service-wide document again from the user mounts, with some of its settings replaced."""
    with patched_settings({**get_api_docs_settings(), "SCHEMA_PATH_PREFIX": "/", **overrides}):
        return SchemaGenerator(patterns=user_patterns()).get_schema(request=None, public=True)


def operations(schema):
    """Yield every (path, method, operation) the schema documents."""
    for path, path_item in schema["paths"].items():
        for method, operation in path_item.items():
            if isinstance(operation, dict) and method in {"get", "post", "put", "patch", "delete"}:
                yield path, method, operation


@skip_unless_lms
def test_version_two_addresses_are_published(schema):
    assert VERSION_TWO_PATHS <= set(schema["paths"])


@skip_unless_lms
def test_every_operation_of_this_version_is_published_under_one_group(schema):
    """
    An operation a client library is generated from carries that mark alone.

    A client takes one class per group, so an operation in two of them is
    written into both; every operation the platform generates a library from
    carries the one mark, and the rest name the area they belong to.
    """
    tags = {
        operation["operationId"]: operation.get("tags")
        for path, _method, operation in operations(schema)
        if path in VERSION_TWO_PATHS
    }
    assert tags == {
        "v2_accounts_list": ["openedx-platform-sdk"],
        "v2_accounts_retrieve": ["openedx-platform-sdk"],
        "v2_accounts_partial_update": ["openedx-platform-sdk"],
        "v2_email_searches_create": ["openedx-platform-sdk"],
        "v2_accounts_deactivation_create": ["openedx-platform-sdk"],
        "v2_accounts_profile_image_create": ["openedx-platform-sdk"],
        "v2_accounts_profile_image_destroy": ["openedx-platform-sdk"],
        "v2_accounts_verification_status_retrieve": ["openedx-platform-sdk"],
        "v2_accounts_verifications_list": ["openedx-platform-sdk"],
        "v2_name_changes_create": ["openedx-platform-sdk"],
        "v2_name_changes_confirmations_create": ["openedx-platform-sdk"],
        "v2_accounts_preferences_retrieve": ["openedx-platform-sdk"],
        "v2_accounts_preferences_partial_update": ["openedx-platform-sdk"],
        "v2_accounts_preferences_retrieve_one": ["openedx-platform-sdk"],
        "v2_accounts_preferences_update": ["openedx-platform-sdk"],
        "v2_accounts_preferences_destroy": ["openedx-platform-sdk"],
        "v2_email_opt_ins_create": ["openedx-platform-sdk"],
        "v2_users_list": ["user"],
        "v2_users_retrieve": ["user"],
        "v2_user_preferences_list": ["user"],
        "v2_user_preferences_retrieve": ["user"],
        "v2_time_zones_list": ["user"],
        "v2_retirements_create": ["openedx-platform-sdk"],
        "v2_retirements_list": ["user"],
        "v2_retirements_retrieve": ["user"],
        "v2_retirements_partial_update": ["user"],
        "v2_retirements_destroy": ["user"],
        "v2_retirements_erasures_create": ["user"],
        "v2_retirement_cleanups_create": ["user"],
        "v2_retirement_partner_reports_create": ["user"],
        "v2_retirement_partner_reports_update": ["user"],
        "v2_retirement_partner_report_cleanups_create": ["user"],
        "v2_username_replacements_create": ["user"],
    }


@skip_unless_lms
def test_only_superseded_addresses_are_marked_deprecated(schema):
    deprecated = {
        path for path, _method, operation in operations(schema) if operation.get("deprecated")
    }
    assert deprecated == set(SUPERSEDED_USER_ADDRESSES)


@skip_unless_lms
def test_only_this_version_and_the_addresses_it_supersedes_are_published(schema):
    """A user address this change neither serves nor replaces stays out of the document."""
    published = {path for path in schema["paths"] if path.startswith("/api/user/")}
    assert published == VERSION_TWO_PATHS | set(SUPERSEDED_USER_ADDRESSES)


@skip_unless_lms
def test_pagination_envelope_is_published_in_full(schema, service_wide):
    """
    Every paged body this version publishes carries the whole envelope, not DRF's four members.

    Each member is in every page, the links as null where there is no page
    to link to, so each one is published as required in both documents.
    """
    paged = [name for name in VERSION_TWO_COMPONENTS if name.startswith("Paginated")]
    assert len(paged) == 7
    for document in (schema, service_wide):
        for component in paged:
            published = document["components"]["schemas"][component]
            assert set(published["properties"]) == PAGINATION_MEMBERS, component
            assert set(published["required"]) == PAGINATION_MEMBERS, component
            assert published["properties"]["next"]["nullable"] is True, component
            assert published["properties"]["previous"]["nullable"] is True, component


@pytest.mark.django_db
@skip_unless_lms
def test_published_pagination_envelope_matches_the_response_body(client, schema, settings):
    settings.EDX_API_KEY = API_KEY
    admin = AdminFactory.create(username="schema-admin", password=PASSWORD)
    UserPreferenceFactory.create(user=admin, key="pref-lang", value="en")
    body = client.get(reverse("user_v2:user_preference_list"), HTTP_X_EDX_API_KEY=API_KEY).json()
    published = schema["components"]["schemas"]["PaginatedUserPreferenceRecordResponseList"]
    assert set(body) == set(published["properties"])


@skip_unless_lms
def test_error_responses_reference_the_shared_envelope(schema):
    """One refusal carries more than the envelope, and it is the only one that may."""
    extends_the_envelope = {(ACCOUNT_LIST_PATH, "get", "404"): "RetiredAccountErrorResponse"}
    for path, method, operation in operations(schema):
        if path not in VERSION_TWO_PATHS:
            continue
        for code, response in operation["responses"].items():
            if not code.startswith(("4", "5")):
                continue
            component = extends_the_envelope.get((path, method, code), "ErrorResponse")
            schema_ref = response["content"]["application/json"]["schema"]
            assert schema_ref == {"$ref": f"#/components/schemas/{component}"}, (path, method, code)


@skip_unless_lms
def test_a_refusal_that_does_not_depend_on_the_resource_reads_the_same_at_every_address(schema):
    """Each of these codes is published with one text, whichever resource of this version answers it."""
    published = {"401": set(), "415": set()}
    for path, _method, operation in operations(schema):
        if path not in VERSION_TWO_PATHS:
            continue
        for code, response in operation["responses"].items():
            if code in published:
                published[code].add(response["description"])
    assert published == {
        "401": {"The requester is not authenticated."},
        "415": {"The request body is in a media type this endpoint cannot read."},
    }


@skip_unless_lms
def test_an_operation_that_takes_a_response_preset_publishes_the_refusal(schema):
    """A preset the operation does not answer to is a 400, so each of them documents one."""
    takes_a_preset = [
        (path, method)
        for path, method, operation in operations(schema)
        if path in VERSION_TWO_PATHS
        and any(parameter["name"] == "view" for parameter in operation.get("parameters", []))
        and "400" in operation["responses"]
    ]
    assert sorted(takes_a_preset) == [
        (ACCOUNT_LIST_PATH, "get"),
        (ACCOUNT_MEMBER_PATH, "get"),
        (ACCOUNT_MEMBER_PATH, "patch"),
        ("/api/user/v2/retirements/", "get"),
        ("/api/user/v2/retirements/{username}/", "get"),
        ("/api/user/v2/user_preferences/", "get"),
        ("/api/user/v2/user_preferences/{username},{key}/", "get"),
        ("/api/user/v2/users/", "get"),
        ("/api/user/v2/users/{username}/", "get"),
    ]


@skip_unless_lms
def test_user_list_publishes_its_filters(schema):
    parameters = {
        parameter["name"]: parameter
        for parameter in schema["paths"]["/api/user/v2/users/"]["get"]["parameters"]
    }
    assert set(parameters) == {
        "course_key", "email", "fields", "forum_role", "is_active", "ordering",
        "page", "page_size", "preference_key", "username", "view",
    }
    # The filter that refuses an unparsable value is still published as a boolean.
    assert parameters["is_active"]["schema"] == {"type": "boolean"}


@skip_unless_lms
def test_preference_list_publishes_its_filters(schema):
    parameters = {
        parameter["name"]: parameter
        for parameter in schema["paths"]["/api/user/v2/user_preferences/"]["get"]["parameters"]
    }
    assert set(parameters) == {"fields", "key", "ordering", "page", "page_size", "username", "view"}
    # Every published parameter is one the list reads, and one it keeps.
    assert [name for name, parameter in parameters.items() if parameter.get("deprecated")] == []


@skip_unless_lms
def test_every_collection_that_selects_accounts_by_name_publishes_the_same_kind_of_value(schema):
    """
    A value learned on one of these collections selects the same accounts on the others.

    Each of the three reads a list of names, and a name no account holds is
    left out of the page rather than refused, so the published description
    opens with the same sentence and the value is published as the text that
    carries the list.
    """
    published = {
        path: next(
            parameter for parameter in operation["parameters"] if parameter["name"] == "username"
        )
        for path, _method, operation in operations(schema)
        if path in {ACCOUNT_LIST_PATH, USER_LIST_PATH, PREFERENCE_LIST_PATH}
    }
    assert {path: parameter["schema"] for path, parameter in published.items()} == {
        ACCOUNT_LIST_PATH: {"type": "string"},
        USER_LIST_PATH: {"type": "string"},
        PREFERENCE_LIST_PATH: {"type": "string"},
    }
    assert {
        path: parameter["description"].split(".")[0] for path, parameter in published.items()
    } == {
        ACCOUNT_LIST_PATH: "Comma-separated usernames",
        USER_LIST_PATH: "Comma-separated usernames",
        PREFERENCE_LIST_PATH: "Comma-separated usernames",
    }


@skip_unless_lms
def test_enum_post_processing_runs_before_the_deprecation_hook():
    hooks = settings.SPECTACULAR_SETTINGS["POSTPROCESSING_HOOKS"]
    assert hooks[0] == "drf_spectacular.hooks.postprocess_schema_enums"
    assert "lms.lib.spectacular.lms_mark_legacy_paths_deprecated" in hooks[1:]
    assert "openedx.core.djangoapps.user_api.schema.mark_superseded_user_paths" in hooks[1:]


@skip_unless_lms
def test_hook_leaves_other_resources_alone():
    """The hook marks only the addresses it lists, even among their neighbours."""
    untouched = [
        "/api/profile_images/v1/{username}/upload",
        "/api/profile_images/v1/{username}/remove",
        "/api/user/v2/account/registration/",
        "/api/user/v2/accounts/{username}/preferences/",
        "/user_api/v1/users/",
        "/user_api/v1/accounts/{username}",
        "/user_api/v1/preferences/time_zones/",
        "/api/enrollment/v2/enrollments/",
        "/api/courses/v1/courses/",
    ]
    marked = [
        "/api/user/v1/users/",
        "/api/user/v1/accounts",
        "/api/user/v1/accounts/{username}",
        "/api/user/v1/accounts/{username}/image",
        "/api/user/v1/accounts/name_change/",
        "/api/user/v1/preferences/{username}",
        "/api/user/v1/preferences/{username}/{preference_key}",
        "/api/user/v1/preferences/email_opt_in/",
        "/api/user/v1/accounts/deactivate_logout/",
        "/api/user/v1/accounts/replace_usernames/",
        "/api/enrollment/v2/enrollments",
    ]
    result = {"paths": {path: {"get": {"operationId": path}} for path in untouched + marked}}

    lms_mark_legacy_paths_deprecated(result, generator=None, request=None, public=True)
    mark_superseded_user_paths(result, generator=None, request=None, public=True)

    for path in untouched:
        assert "deprecated" not in result["paths"][path]["get"], path
    for path in marked:
        assert result["paths"][path]["get"]["deprecated"] is True, path


@skip_unless_lms
def test_every_superseded_address_has_a_published_replacement(schema):
    """
    The replacement serves the operation, not only the address.

    An address replacing several operations serves a method for each of them,
    and a client told to move to one that does not serve the method it was
    using has nowhere to go: the method is held here as well as the path.
    """
    assert set(SUPERSEDED_USER_ADDRESSES) == set(SUPERSEDED_USER_PATHS)
    assert set(SUPERSEDED_USER_METHODS) == set(SUPERSEDED_USER_PATHS)
    for superseded, replacement in SUPERSEDED_USER_ADDRESSES.items():
        assert superseded in schema["paths"]
        assert replacement in schema["paths"]
        assert SUPERSEDED_USER_METHODS[superseded] in schema["paths"][replacement], superseded


#: The media types each superseded body is published in, by address and method.
SUPERSEDED_REQUEST_MEDIA_TYPES = {
    ("/api/user/v1/accounts/{username}", "patch"): {"application/merge-patch+json"},
    ("/api/user/v1/accounts/search_emails", "post"): {
        "application/json", "application/merge-patch+json",
    },
    ("/api/user/v1/preferences/{username}", "patch"): {"application/merge-patch+json"},
    ("/api/user/v1/preferences/{username}/{preference_key}", "put"): {"application/json"},
    ("/api/user/v1/preferences/email_opt_in/", "post"): {"application/x-www-form-urlencoded"},
    ("/api/user/v1/accounts/replace_usernames/", "post"): {"application/json"},
}

#: The forms the request to close one's own account is published in. It is
#: pinned on its own because driving it carries the account away with it: both
#: forms are sent to both addresses in the parity tests of that operation,
#: where each case has an account of its own, and the form this document
#: leaves out is sent there too and is refused.
REQUEST_TO_BE_RETIRED_MEDIA_TYPES = {
    "application/x-www-form-urlencoded",
    "multipart/form-data",
}


@pytest.mark.django_db
@skip_unless_lms
def test_each_superseded_body_is_published_in_the_media_types_that_address_reads(client, schema, settings):
    """
    Every media type published for a superseded body is sent to that address here.

    Those bodies are named for addresses whose views name no serializer, and a
    shape named on its own is published in every media type the view's parsers
    accept, which can be more than the handler reads. A generated client sends
    the first type the document offers, so each one is answered by the running
    endpoint here, and a type the document leaves out is sent as well.
    """
    staff = AdminFactory.create(username="media_type_staff", password=PASSWORD)
    # One of these addresses serves the one caller a deployment names for
    # replacing the names accounts are stored under, and this is that caller.
    settings.USERNAME_REPLACEMENT_WORKER = "media_type_staff"
    assert client.login(username=staff.username, password=PASSWORD)
    member = f"/api/user/v1/accounts/{staff.username}"
    preferences = f"/api/user/v1/preferences/{staff.username}"
    requests = {
        ("/api/user/v1/accounts/{username}", "patch"): (member, "{}", 200),
        ("/api/user/v1/accounts/search_emails", "post"): (
            "/api/user/v1/accounts/search_emails", json.dumps({"emails": [staff.email]}), 200,
        ),
        ("/api/user/v1/preferences/{username}", "patch"): (
            preferences, json.dumps({"pref-lang": "en"}), 204,
        ),
        # The key half of that route carries neither a hyphen nor a dot, so the
        # name sent here is one it can address.
        ("/api/user/v1/preferences/{username}/{preference_key}", "put"): (
            f"{preferences}/account_privacy", json.dumps("private"), 204,
        ),
        ("/api/user/v1/preferences/email_opt_in/", "post"): (
            "/api/user/v1/preferences/email_opt_in/",
            "course_id=course-v1%3AedX%2BDemoX%2BDemo&email_opt_in=True",
            200,
        ),
        ("/api/user/v1/accounts/replace_usernames/", "post"): (
            "/api/user/v1/accounts/replace_usernames/",
            json.dumps({"username_mappings": [{staff.username: "renamed-by-the-document"}]}),
            200,
        ),
    }
    for (path, method), media_types in SUPERSEDED_REQUEST_MEDIA_TYPES.items():
        assert set(schema["paths"][path][method]["requestBody"]["content"]) == media_types, path
        address, body, expected_status = requests[(path, method)]
        for media_type in media_types:
            answer = getattr(client, method)(address, data=body, content_type=media_type)
            assert answer.status_code == expected_status, (address, media_type, answer.content)
    left_out = client.patch(member, data="{}", content_type="application/json")
    assert left_out.status_code == 415


@skip_unless_lms
def test_the_request_to_be_retired_is_published_in_the_forms_that_address_reads(schema):
    """The form it is published without is the one its handler parses and then refuses."""
    published = schema["paths"]["/api/user/v1/accounts/deactivate_logout/"]["post"]["requestBody"]
    assert set(published["content"]) == REQUEST_TO_BE_RETIRED_MEDIA_TYPES
    assert published["required"] is True
    assert "application/json" not in published["content"]


#: The sets of choices this version publishes as a model of their own, with
#: the name each is published under and the values it offers.
PINNED_CHOICE_SETS = {
    "RetirementErasureScopeEnum": list(ERASURE_SCOPES),
    "RetirementBatchResultOutcomeEnum": list(BATCH_OUTCOMES),
    "ReplacementOutcomeEnum": list(REPLACEMENT_OUTCOMES),
}

#: A second field of each of those names, with the value it would offer and
#: the name it would be published under: a set of choices sharing a field name
#: with another is named after the body carrying it.
CROWDED_CHOICE_SETS = {
    "scope": ("somewhere_else", "RetirementBatchScopeEnum"),
    "outcome": ("something_else", "RetirementBatchOutcomeEnum"),
}


@pytest.mark.django_db
@skip_unless_lms
def test_each_published_set_of_choices_keeps_its_name_when_another_field_shares_the_name():
    """
    A set of choices is published as a model named for the field that carries it.

    That name holds only while one field of the whole service carries it: a
    second field of the same name offering different values renames both
    models after the bodies they sit in, so the name a client already
    generates a class from disappears. Both documents are therefore read with
    such a second field in place, and each set has to still be published
    under its own name, with the values it offers.
    """
    intruders = {
        name: serializers.ChoiceField(choices=[(value, f"Another set of choices, under {name}.")])
        for name, (value, _published_as) in CROWDED_CHOICE_SETS.items()
    }
    with mock.patch.dict(RetirementBatchSerializer._declared_fields, intruders):  # pylint: disable=protected-access
        crowded = {
            "this version": SchemaGenerator(patterns=user_patterns()).get_schema(request=None, public=True),
            "service-wide": service_wide_from_user_mounts(),
        }
    for which, document in crowded.items():
        components = document["components"]["schemas"]
        published = {
            name: components[name]["enum"] for name in PINNED_CHOICE_SETS if name in components
        }
        assert published == PINNED_CHOICE_SETS, which
        # The second field really was published, so the collision the names
        # are held against did happen in this document.
        for field, (value, crowded_name) in CROWDED_CHOICE_SETS.items():
            assert components["RetirementBatch"]["properties"][field] == {
                "$ref": f"#/components/schemas/{crowded_name}",
            }, (which, field)
            assert components[crowded_name]["enum"] == [value], (which, field)


@skip_unless_lms
def test_every_response_shape_is_published(schema):
    """The reduced bodies the presets return are described, not only the full one."""
    components = schema["components"]["schemas"]
    assert components["UserDirectoryResponse"]["anyOf"] == [
        {"$ref": "#/components/schemas/UserDirectory"},
        {"$ref": "#/components/schemas/UserDirectoryMinimal"},
        {"$ref": "#/components/schemas/UserDirectoryProjection"},
    ]
    assert components["UserPreferenceRecordResponse"]["anyOf"] == [
        {"$ref": "#/components/schemas/UserPreferenceRecord"},
        {"$ref": "#/components/schemas/UserPreferenceRecordMinimal"},
        {"$ref": "#/components/schemas/UserPreferenceRecordProjection"},
    ]
    assert sorted(components["UserDirectory"]["required"]) == [
        "email", "id", "name", "preferences", "url", "username",
    ]
    assert sorted(components["UserDirectoryMinimal"]["required"]) == ["email", "id", "username"]
    assert sorted(components["UserPreferenceRecordMinimal"]["required"]) == ["key", "username", "value"]
    for component in ("UserDirectoryProjection", "UserPreferenceRecordProjection"):
        published = components[component]
        assert "required" not in published, component
        assert set(published["properties"]) == set(
            components[component.replace("Projection", "")]["properties"]
        ), component


#: A value for every property of a profile an account carries, so that a body
#: checked against its component carries a value the component has to accept
#: rather than a null every type accepts.
FILLED_IN_PROFILE = {
    "bio": "Teaches mathematics.",
    "city": "Cambridge",
    "country": "US",
    "gender": "f",
    "goals": "Finish the course.",
    "level_of_education": "m",
    "mailing_address": "12 Park Avenue",
    "phone_number": "+18005555555",
    "state": "MA",
    "year_of_birth": 1985,
}

#: The properties of an account that only a filled-in one carries: each is held
#: outside the profile, or is left null by a user who never gave it.
FILLED_IN_ONLY_PROPERTIES = (
    "country",
    "level_of_education",
    "mailing_address",
    "pending_name_change",
    "phone_number",
    "secondary_email",
    "state",
    "verified_name",
)


def fill_in(user):
    """Give the user's profile a value for each property it holds, and a name change outstanding."""
    for name, value in FILLED_IN_PROFILE.items():
        setattr(user.profile, name, value)
    user.profile.save()
    user.profile.language_proficiencies.create(code="fr")
    user.profile.social_links.create(platform="twitter", social_link="https://www.twitter.com/example")
    PendingNameChange.objects.create(
        user=user, new_name="Changed Name", rationale="Asked for in a test",
    )
    return user


def name_affirmation_answering(verified_name):
    """
    Return a patch installing a name affirmation service that holds one accepted name.

    The service is an optional install, and the property it fills is null
    wherever it is absent, so the account that carries one is built with a
    stand-in rather than left untested on the sites that do not have it.
    """
    service = mock.Mock()
    service.get_verified_name.return_value = mock.Mock(verified_name=verified_name)
    return mock.patch(
        "openedx.core.djangoapps.user_api.accounts.serializers.get_name_affirmation_service",
        return_value=service,
    )


@pytest.mark.django_db
@skip_unless_lms
def test_published_shapes_accept_every_body_the_endpoints_return(client, schema, settings):
    """Every preset and selection the parameters offer returns a body the document describes."""
    settings.EDX_API_KEY = API_KEY
    admin = AdminFactory.create(username="shape-admin", password=PASSWORD)
    UserPreferenceFactory.create(user=admin, key="pref-lang", value="en")
    fill_in(admin)
    assert client.login(username=admin.username, password=PASSWORD)
    # The account addresses read the signed-in administrator; the directory
    # addresses read the key.
    client.defaults["HTTP_X_EDX_API_KEY"] = API_KEY
    for published_path, address, params, component in SHAPE_CASES:
        case = (address, params)
        response = client.get(address, params)
        assert response.status_code == 200, case
        body = response.json()
        if "results" in body:
            assert body["count"] == 1, case
        validator = jsonschema.Draft202012Validator(published_body_schema(schema, published_path))
        assert [error.message for error in validator.iter_errors(body)] == [], case
        assert_records_match(schema, body, component, case)


@skip_unless_lms
def test_sort_parameter_publishes_the_fields_it_accepts(schema):
    """The sort allow-list and the order used when none is asked for are published."""
    for path, accepted, default in (
        ("/api/user/v2/users/", ["-date_joined", "-username", "date_joined", "username"], ["username"]),
        ("/api/user/v2/user_preferences/", ["-key", "-username", "key", "username"], ["username", "key"]),
    ):
        parameter = next(
            published for published in schema["paths"][path]["get"]["parameters"]
            if published["name"] == "ordering"
        )
        assert parameter["schema"]["items"]["enum"] == accepted, path
        assert parameter["schema"]["default"] == default, path


@skip_unless_lms
def test_every_list_publishes_the_page_that_does_not_exist(schema):
    """Asking for a page past the end of a list is answered 404, so each list documents it."""
    assert LIST_PATHS | MEMBER_PATHS | ACTION_PATHS == set(VERSION_TWO_PATHS)
    documented = {
        path: sorted(operation["responses"])
        for path, method, operation in operations(schema)
        # The read is what carries a page, so it is the read that is held to
        # publishing the refusal: a collection may serve a write as well.
        if path in LIST_PATHS and method == "get"
    }
    assert documented == {
        ACCOUNT_LIST_PATH: ["200", "400", "401", "403", "404", "406"],
        VERIFICATION_LIST_PATH: ["200", "400", "401", "403", "404", "406"],
        "/api/user/v2/users/": ["200", "400", "401", "403", "404", "406"],
        "/api/user/v2/user_preferences/": ["200", "400", "401", "403", "404", "406"],
        TIME_ZONE_LIST_PATH: ["200", "400", "404", "406"],
        RETIREMENT_LIST_PATH: ["200", "400", "401", "403", "404", "406"],
    }


@skip_unless_lms
def test_member_addresses_describe_the_parts_they_are_built_from(schema):
    """Each part of a member address is published with its meaning and what its route matches."""
    username_schema = {"type": "string", "pattern": rf"^{settings.USERNAME_REGEX_PARTIAL}$"}
    published = {
        (path, parameter["name"]): parameter
        for path, _method, operation in operations(schema)
        if path in MEMBER_PATHS
        for parameter in operation["parameters"]
        if parameter["in"] == "path"
    }
    assert published == {
        (ACCOUNT_MEMBER_PATH, "username"): {
            "in": "path",
            "name": "username",
            "required": True,
            "schema": username_schema,
            "description": (
                "Unique name of the user whose account is addressed. `me` names the account of "
                "the caller, so an account holding that name is read through the collection, "
                "with `username=` naming it."
            ),
        },
        (VERIFICATION_STATUS_PATH, "username"): {
            "in": "path",
            "name": "username",
            "required": True,
            "schema": username_schema,
            "description": (
                "Unique name of the user whose identity verification is addressed. The name is "
                "read as it is stored, so `me` here names an account holding that name rather "
                "than the account of the caller making the request."
            ),
        },
        (USER_MEMBER_PATH, "username"): {
            "in": "path",
            "name": "username",
            "required": True,
            "schema": username_schema,
            "description": "Unique name of the user whose directory entry is returned.",
        },
        (PREFERENCE_MEMBER_PATH, "username"): {
            "in": "path",
            "name": "username",
            "required": True,
            "schema": username_schema,
            "description": (
                "Username of the user the preference belongs to: the part of the address "
                "before the comma."
            ),
        },
        (PREFERENCE_MEMBER_PATH, "key"): {
            "in": "path",
            "name": "key",
            "required": True,
            "schema": {"type": "string", "pattern": rf"^{PREFERENCE_KEY_PATTERN_PARTIAL}$"},
            "description": (
                "Name the preference is stored under: the part of the address after the comma."
            ),
        },
        (PREFERENCE_DOCUMENT_PATH, "username"): {
            "in": "path",
            "name": "username",
            "required": True,
            "schema": username_schema,
            "description": ACCOUNT_PREFERENCES_USERNAME_DESCRIPTION,
        },
        (PREFERENCE_NAME_PATH, "username"): {
            "in": "path",
            "name": "username",
            "required": True,
            "schema": username_schema,
            "description": ACCOUNT_PREFERENCES_USERNAME_DESCRIPTION,
        },
        (RETIREMENT_MEMBER_PATH, "username"): {
            "in": "path",
            "name": "username",
            "required": True,
            "schema": username_schema,
            "description": RETIREMENT_USERNAME_DESCRIPTION,
        },
        (PARTNER_REPORT_MEMBER_PATH, "username"): {
            "in": "path",
            "name": "username",
            "required": True,
            "schema": username_schema,
            "description": PARTNER_REPORT_USERNAME_DESCRIPTION,
        },
        (PREFERENCE_NAME_PATH, "key"): {
            "in": "path",
            "name": "key",
            "required": True,
            "schema": {"type": "string", "pattern": rf"^{PREFERENCE_KEY_PATTERN_PARTIAL}$"},
            "description": (
                "Name the preference is stored under, which is the whole segment, punctuation "
                "included. A name carrying a slash has no address of its own, because the "
                "segment ends at the slash; such a name is read with the whole document and "
                "removed by sending a null for it there. Nothing is stored under a name longer "
                f"than {PREFERENCE_NAME_MAX_LENGTH} characters, nor under one carrying no "
                "letter, digit, hyphen or underscore. A name with whitespace at either end names "
                "no preference: one is stored under its name without the whitespace around it."
            ),
        },
    }


@skip_unless_lms
def test_the_addresses_that_work_on_one_account_describe_the_name_they_carry(schema):
    """
    An address that works on one account publishes what the name in it is read as.

    Neither of these addresses takes the shorthand the account collection
    takes for the caller's own account, so what each of them does with a
    request sent under that name is published rather than left for a caller to
    find out by sending one.
    """
    published = {
        (path, method): parameter["description"]
        for path, method, operation in operations(schema)
        if path in (ACCOUNT_DEACTIVATION_PATH, PROFILE_IMAGE_PATH)
        for parameter in operation["parameters"]
        if parameter["in"] == "path" and parameter["name"] == "username"
    }
    image_description = (
        "Unique name of the user whose image is addressed. The name is read as it is stored, so "
        "`me` here names an account holding that name rather than the account of the caller "
        "making the request."
    )
    assert published == {
        (ACCOUNT_DEACTIVATION_PATH, "post"): (
            "Unique name of the user whose account is deactivated. The name is read as it is "
            "stored, `me` included: this action disables the account the address names, never "
            "the account of the caller making the request."
        ),
        (PROFILE_IMAGE_PATH, "post"): image_description,
        (PROFILE_IMAGE_PATH, "delete"): image_description,
    }


#: Every part of an address of this version that carries a username. A client
#: built from the document holds a value to what the route matches before it
#: sends a request, so an address left off this list is one whose caller learns
#: from a refusal what every sibling address refuses for it.
USERNAME_ADDRESS_PARTS = (
    ACCOUNT_MEMBER_PATH,
    ACCOUNT_DEACTIVATION_PATH,
    PROFILE_IMAGE_PATH,
    VERIFICATION_STATUS_PATH,
    VERIFICATION_LIST_PATH,
    PREFERENCE_DOCUMENT_PATH,
    PREFERENCE_NAME_PATH,
    NAME_CHANGE_CONFIRMATION_PATH,
    USER_MEMBER_PATH,
    PREFERENCE_MEMBER_PATH,
    RETIREMENT_MEMBER_PATH,
    RETIREMENT_ERASURE_PATH,
    PARTNER_REPORT_MEMBER_PATH,
)


@skip_unless_lms
def test_every_part_of_an_address_that_carries_a_username_publishes_what_the_route_matches(schema):
    """
    One resource does not admit a name at one of its addresses and refuse it at the next.

    The addresses of this version are matched on the same spelling of a
    username, whichever app serves them, so each of them is published with
    that spelling and a client refuses the same names the routes do.
    """
    published = {
        (path, parameter["name"]): parameter["schema"]
        for path, _method, operation in operations(schema)
        if path in VERSION_TWO_PATHS
        for parameter in operation.get("parameters", [])
        if parameter["in"] == "path" and parameter["name"] == "username"
    }
    assert published == {
        (path, "username"): {"type": "string", "pattern": rf"^{settings.USERNAME_REGEX_PARTIAL}$"}
        for path in USERNAME_ADDRESS_PARTS
    }


@skip_unless_lms
def test_the_country_parameter_publishes_the_constraint_it_is_held_to(schema):
    """The parameter comes from the serializer that validates it, so the two cannot disagree."""
    field = TimeZoneQuerySerializer().fields["country_code"]
    enforced = next(
        validator.regex.pattern for validator in field.validators if hasattr(validator, "regex")
    )
    parameter = next(
        published for published in schema["paths"][TIME_ZONE_LIST_PATH]["get"]["parameters"]
        if published["name"] == "country_code"
    )
    assert parameter["description"] == field.help_text
    assert parameter["schema"] == {
        "oneOf": [{"type": "string", "pattern": enforced}, {"type": "string", "maxLength": 0}],
    }
    assert "required" not in parameter


@skip_unless_lms
def test_published_text_is_written_for_the_clients_that_read_it(schema):
    """Descriptions reach every consumer, so they carry no governance references."""
    published = json.dumps({
        "paths": {path: schema["paths"][path] for path in VERSION_TWO_PATHS},
        "components": {
            name: schema["components"]["schemas"][name] for name in VERSION_TWO_COMPONENTS
        },
    })
    for reference in ("ADR", "OEP-", "drf-spectacular", "extend_schema"):
        assert reference not in published, reference
    assert schema["components"]["schemas"]["ErrorResponse"]["description"].startswith(
        "The body of every unsuccessful response."
    )


@skip_unless_lms
def test_the_library_tag_marks_the_addresses_a_client_is_built_from_and_nothing_else(schema):
    """The operations a generated client library is built from say so, one by one."""
    tagged = {
        path for path, _method, operation in operations(schema)
        if path.startswith("/api/user/") and "openedx-platform-sdk" in (operation.get("tags") or [])
    }
    assert tagged == SDK_PATHS


@skip_unless_lms
def test_account_list_publishes_its_selectors(schema):
    parameters = {
        parameter["name"]: parameter
        for parameter in schema["paths"][ACCOUNT_LIST_PATH]["get"]["parameters"]
    }
    assert set(parameters) == {
        "email", "fields", "lms_user_id", "ordering", "page", "page_size", "username", "view",
    }
    assert parameters["lms_user_id"]["schema"] == {"type": "integer"}
    # An empty value narrows nothing, the way an empty value of any other
    # parameter does, and is published as one the parameter accepts.
    assert parameters["view"]["schema"]["enum"] == ["minimal", "shared", ""]
    assert parameters["ordering"]["schema"]["enum"] == ["username", "-username", ""]
    assert parameters["ordering"]["schema"]["default"] == ACCOUNT_DEFAULT_SORT == "username"
    assert [name for name, parameter in parameters.items() if parameter.get("deprecated")] == []


@skip_unless_lms
def test_the_selectors_are_published_from_the_serializer_that_validates_them(schema):
    """The constraint a caller is held to is the one the published parameter carries."""
    declared = AccountQuerySerializer().fields
    published = {
        parameter["name"]: parameter
        for parameter in schema["paths"][ACCOUNT_LIST_PATH]["get"]["parameters"]
    }
    for name, field in declared.items():
        assert published[name]["description"].startswith(field.help_text), name
        assert "required" not in published[name], name
    assert published["view"]["description"].endswith(
        "* `minimal` - The properties that identify the account's owner\n"
        "* `shared` - The properties the owner shares with everybody",
    )


@skip_unless_lms
def test_the_education_levels_published_are_the_ones_a_profile_stores(schema):
    """Every code the profile offers is named where the property is published, both read and written."""
    offered = [code for code, _label in UserProfile.LEVEL_OF_EDUCATION_CHOICES]
    assert offered == ["p", "m", "b", "a", "hs", "jhs", "el", "none", "other"]
    for component in ("Account", "PatchedAccountUpdate"):
        published = schema["components"]["schemas"][component]["properties"]["level_of_education"]
        assert [code for code in offered if f"`{code}`" in published["description"]] == offered, component


@skip_unless_lms
def test_a_change_publishes_the_body_it_reads_and_the_media_type_it_reads_it_in(schema):
    request = schema["paths"][ACCOUNT_MEMBER_PATH]["patch"]["requestBody"]
    assert set(request["content"]) == {"application/merge-patch+json"}
    assert request["content"]["application/merge-patch+json"]["schema"] == {
        "$ref": "#/components/schemas/PatchedAccountUpdate",
    }
    changeable = schema["components"]["schemas"]["PatchedAccountUpdate"]
    assert "required" not in changeable
    assert {"email", "name", "bio", "social_links", "extended_profile"} <= set(changeable["properties"])
    assert "username" not in changeable["properties"]


@pytest.mark.django_db
@skip_unless_lms
def test_the_order_the_account_list_publishes_as_its_default_is_the_one_it_answers_with(client, schema):
    """
    The default is read off the document and asked of the running list.

    An order stated in the document and an order applied in the handler are
    two declarations of one thing, and a client that omits the parameter is
    answered by the second while it was built from the first.
    """
    parameter = next(
        published for published in schema["paths"][ACCOUNT_LIST_PATH]["get"]["parameters"]
        if published["name"] == "ordering"
    )
    default = parameter["schema"]["default"]
    staff = AdminFactory.create(username="sorting_staff", password=PASSWORD)
    for name in ("sorted_c", "sorted_a", "sorted_b"):
        UserFactory.create(username=name)
    assert client.login(username=staff.username, password=PASSWORD)
    selection = {"username": "sorted_c,sorted_a,sorted_b"}

    omitted = client.get(ACCOUNT_LIST_PATH, selection)
    assert [row["username"] for row in omitted.json()["results"]] == [
        "sorted_a", "sorted_b", "sorted_c",
    ]
    asked_for = client.get(ACCOUNT_LIST_PATH, {**selection, "ordering": default})
    assert [row["username"] for row in asked_for.json()["results"]] == [
        "sorted_a", "sorted_b", "sorted_c",
    ]
    the_other_way = client.get(ACCOUNT_LIST_PATH, {**selection, "ordering": f"-{default}"})
    assert [row["username"] for row in the_other_way.json()["results"]] == [
        "sorted_c", "sorted_b", "sorted_a",
    ]


@pytest.mark.django_db
@skip_unless_lms
def test_every_property_the_change_publishes_a_null_for_is_cleared_by_one(client, schema):
    """
    A null is sent for each property published as taking one, and is answered.

    A generated client offers a null exactly where the body publishes one, so
    each of those is a change this endpoint has to carry out rather than fail
    on, and a property that cannot be cleared this way belongs outside the set.
    """
    published = schema["components"]["schemas"]["PatchedAccountUpdate"]["properties"]
    takes_a_null = sorted(name for name, shape in published.items() if shape.get("nullable"))
    assert takes_a_null == [
        "account_privacy", "city", "country", "gender", "goals", "level_of_education",
        "mailing_address", "state", "year_of_birth",
    ]
    owner = UserFactory.create(username="clearing_owner", password=PASSWORD)
    assert client.login(username=owner.username, password=PASSWORD)
    address = f"/api/user/v2/accounts/{owner.username}/"
    for name in takes_a_null:
        answer = client.patch(
            address, data=json.dumps({name: None}), content_type="application/merge-patch+json",
        )
        assert answer.status_code == 200, (name, answer.content)
    # The account the last change answered with holds every one of them cleared,
    # bar the city, which a change carries and no response does.
    account = answer.json()
    assert {name: account.get(name, ABSENT) for name in takes_a_null} == {
        # Clearing the visibility choice hands the account back to the visibility
        # the site gives an account whose profile does not say how old its owner
        # is, the year of birth having been cleared here as well.
        "account_privacy": "private",
        "city": ABSENT,
        "country": None,
        "gender": None,
        "goals": None,
        "level_of_education": None,
        "mailing_address": None,
        "state": None,
        "year_of_birth": None,
    }


@skip_unless_lms
def test_a_change_publishes_the_refusals_only_it_can_answer_with(schema):
    assert sorted(schema["paths"][ACCOUNT_MEMBER_PATH]["patch"]["responses"]) == [
        "200", "400", "401", "403", "404", "406", "415", "429", "500",
    ]
    assert schema["paths"][ACCOUNT_MEMBER_PATH]["patch"]["responses"]["500"]["description"] == (
        "The change was asked for correctly and could not be carried out. Sending it again "
        "with something else in it does not help; sending the same request later may."
    )
    assert sorted(schema["paths"][ACCOUNT_MEMBER_PATH]["get"]["responses"]) == [
        "200", "400", "401", "403", "404", "406",
    ]


@skip_unless_lms
def test_the_operations_that_read_a_body_publish_the_refusal_of_one_they_cannot_read(schema):
    """A body the endpoint cannot parse is the caller's fault, and every such address says so."""
    for path, method in (
        (ACCOUNT_MEMBER_PATH, "patch"),
        (EMAIL_SEARCH_PATH, "post"),
        (NAME_CHANGE_PATH, "post"),
    ):
        assert schema["paths"][path][method]["responses"]["400"]["description"] == (
            "The request body is not well formed in the media type it was sent in, or carries a "
            "value this endpoint will not take. A query parameter this endpoint does not read is "
            "refused here too."
        ), path
    assert schema["paths"][PROFILE_IMAGE_PATH]["post"]["responses"]["400"]["description"] == (
        "The request carries no usable image: none was sent, the body is not well formed in the "
        "media type it was sent in, one sent as the whole body was sent without a name or under a "
        "name whose extension names another media type, the image is of a type or a size this "
        "site does not take, or no picture can be drawn from the file, whose bytes may stop "
        "early, carry metadata that does not parse, or declare more pixels than this site draws. "
        "A query parameter this endpoint does not read is refused here too."
    )


@skip_unless_lms
def test_the_operations_only_an_owner_reaches_publish_the_cause_their_refusal_really_has(schema):
    """
    Where only the owner is admitted, nothing is published as missing that is refused instead.

    A name nobody holds is not the caller's own, so these operations refuse it
    as another account's rather than report it absent. What is left for the
    absent answer is an account whose profile is gone, and that is what the
    document describes, so a client reading one of these branches meets it.
    """
    assert schema["paths"][ACCOUNT_MEMBER_PATH]["patch"]["responses"]["404"]["description"] == (
        "The account that was addressed is no longer there. Only the caller's own account is "
        "changed here, so a name nobody holds is refused as somebody else's account instead of "
        "being reported as missing."
    )
    for method in ("post", "delete"):
        assert schema["paths"][PROFILE_IMAGE_PATH][method]["responses"]["404"]["description"] == (
            "The account at this address carries no profile to record an image on. A name no "
            "account holds is refused as somebody else's image instead of being reported here."
        ), method
    assert schema["paths"][NAME_CHANGE_PATH]["post"]["responses"]["404"]["description"] == (
        "The account of the caller carries no profile to put a name on."
    )


@skip_unless_lms
def test_the_search_publishes_the_addresses_it_reads_and_the_page_it_answers_with(schema):
    operation = schema["paths"][EMAIL_SEARCH_PATH]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/EmailSearch",
    }
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/PaginatedEmailSearchResultList",
    }
    assert set(
        schema["components"]["schemas"]["PaginatedEmailSearchResultList"]["properties"],
    ) == PAGINATION_MEMBERS
    # The most addresses one search may name is published, so a client knows
    # the bound before it sends a body the search will refuse.
    addresses = schema["components"]["schemas"]["EmailSearch"]["properties"]["emails"]
    assert addresses["type"] == "array"
    assert addresses["maxItems"] == 100
    assert sorted(operation["responses"]) == ["200", "400", "401", "403", "404", "406", "415"]
    # The page is the only thing it reads from the query string.
    assert [parameter["name"] for parameter in operation["parameters"]] == ["page", "page_size"]


@skip_unless_lms
def test_a_deactivation_publishes_that_it_answers_with_nothing(schema):
    operation = schema["paths"][ACCOUNT_DEACTIVATION_PATH]["post"]
    assert "requestBody" not in operation
    assert sorted(operation["responses"]) == ["204", "400", "401", "403", "404", "406"]
    assert "content" not in operation["responses"]["204"]
    assert operation["responses"]["204"]["description"] == "The account was deactivated."


@skip_unless_lms
def test_the_name_a_deactivation_disables_is_published_as_a_stored_one(schema):
    """``me`` names the caller where an account is read, and a stored account where one is disabled."""
    disabled = {
        parameter["name"]: parameter
        for parameter in schema["paths"][ACCOUNT_DEACTIVATION_PATH]["post"]["parameters"]
    }
    assert disabled["username"]["description"] == (
        "Unique name of the user whose account is deactivated. The name is read as it is "
        "stored, `me` included: this action disables the account the address names, never the "
        "account of the caller making the request."
    )


#: The properties of an account that always carry a value when they are sent.
ALWAYS_VALUED_ACCOUNT_PROPERTIES = {"account_privacy", "date_joined", "email", "id", "is_active", "username"}

#: The properties a profile holds, which an account without one answers with null.
PROFILE_HELD_PROPERTIES = (
    "extended_profile",
    "language_proficiencies",
    "profile_image",
    "requires_parental_consent",
    "social_links",
)


@skip_unless_lms
def test_an_account_is_published_with_the_properties_visibility_decides(schema):
    """Only the properties every caller may see are published as always present."""
    components = schema["components"]["schemas"]
    assert components["AccountResponse"]["anyOf"] == [
        {"$ref": "#/components/schemas/Account"},
        {"$ref": "#/components/schemas/AccountMinimal"},
        {"$ref": "#/components/schemas/AccountProjection"},
    ]
    assert sorted(components["Account"]["required"]) == [
        "account_privacy", "profile_image", "username",
    ]
    assert set(components["Account"]["properties"]) == set(
        settings.ACCOUNT_VISIBILITY_CONFIGURATION["admin_fields"],
    )
    assert sorted(components["AccountMinimal"]["required"]) == ["profile_image", "username"]
    assert set(components["AccountMinimal"]["properties"]) == {
        "username", "email", "name", "profile_image",
    }
    assert "required" not in components["AccountProjection"]


@skip_unless_lms
def test_an_account_publishes_which_of_its_properties_can_come_back_without_a_value(schema):
    """A property that is only sometimes held has to be published as one that can be null."""
    components = schema["components"]["schemas"]
    properties = components["Account"]["properties"]
    assert {
        name for name, published in properties.items() if not published.get("nullable")
    } == ALWAYS_VALUED_ACCOUNT_PROPERTIES
    extra_field_value = components["AccountExtendedField"]["properties"]["field_value"]
    assert "type" not in extra_field_value, "an extra field answers with whatever the site stored in it"


def account_owner(client, username, **profile_values):
    """Create a user old enough to share a profile, sign them in, and return them."""
    user = UserFactory.create(username=username, password=PASSWORD)
    user.profile.year_of_birth = 1985
    for name, value in profile_values.items():
        setattr(user.profile, name, value)
    user.profile.save()
    assert client.login(username=username, password=PASSWORD)
    return user


@pytest.mark.django_db
@skip_unless_lms
def test_an_owner_reading_their_account_where_no_recovery_address_is_collected(client, schema):
    """The site collects no recovery address, which the account says by carrying none."""
    account_owner(client, "shape-owner")
    with override_switch(ENABLE_SECONDARY_EMAIL_FEATURE_SWITCH, active=False):
        body = client.get("/api/user/v2/accounts/me/").json()
    assert body["secondary_email_enabled"] is None
    assert body["secondary_email"] is None
    assert_records_match(schema, body, "Account")


@pytest.mark.django_db
@skip_unless_lms
def test_an_owner_reading_their_account_where_a_recovery_address_is_collected(client, schema):
    """The other side of the same choice: the account says the site collects one."""
    account_owner(client, "shape-owner")
    with override_switch(ENABLE_SECONDARY_EMAIL_FEATURE_SWITCH, active=True):
        body = client.get("/api/user/v2/accounts/me/").json()
    assert body["secondary_email_enabled"] is True
    assert body["secondary_email"] is None
    assert_records_match(schema, body, "Account")


@pytest.mark.django_db
@skip_unless_lms
def test_a_staff_caller_reading_an_account_that_has_no_profile(client, schema):
    """Every property a profile holds comes back null for an account that has none."""
    staff = AdminFactory.create(username="shape-staff", password=PASSWORD)
    profileless = UserFactory.create(username="shape-profileless", password=PASSWORD)
    profileless.profile.delete()
    assert client.login(username=staff.username, password=PASSWORD)
    body = client.get("/api/user/v2/accounts/shape-profileless/").json()
    assert {name: body[name] for name in PROFILE_HELD_PROPERTIES} == dict.fromkeys(PROFILE_HELD_PROPERTIES)
    assert_records_match(schema, body, "Account")


@pytest.mark.django_db
@skip_unless_lms
def test_a_learner_reading_an_account_whose_owner_shares_it_with_everybody(client, schema):
    """A profile shared with all users carries more than the properties everybody may see."""
    shared = UserFactory.create(username="shape-shared", password=PASSWORD)
    shared.profile.year_of_birth = 1985
    shared.profile.bio = "Teaches mathematics."
    shared.profile.save()
    account_owner(client, "shape-reader")
    body = client.get("/api/user/v2/accounts/shape-shared/").json()
    assert body["account_privacy"] == "all_users"
    assert body["bio"] == "Teaches mathematics."
    assert body["course_certificates"] is None
    assert_records_match(schema, body, "Account")


@pytest.mark.django_db
@skip_unless_lms
def test_an_owner_reading_their_account_on_a_site_that_collects_extra_profile_fields(client, schema):
    """An extra field answers with the value the site stored, which is not always text."""
    owner = account_owner(client, "shape-owner")
    owner.profile.meta = json.dumps({"department": None, "age": 41})
    owner.profile.save()
    with with_site_configuration_context(configuration={"extended_profile_fields": ["department", "age"]}):
        body = client.get("/api/user/v2/accounts/me/").json()
    assert body["extended_profile"] == [
        {"field_name": "department", "field_value": None},
        {"field_name": "age", "field_value": 41},
    ]
    assert_records_match(schema, body, "Account")


@pytest.mark.django_db
@skip_unless_lms
def test_an_account_holding_a_value_for_every_property_matches_its_component(client, schema):
    """
    An account with every property valued is checked against the component.

    A property a body only ever carries as null is one no validator can tell
    the published type of, so this account holds what a profile, an
    outstanding name change, a recovery address and an accepted name give it.

    This is also the one body the component is held to in both directions.
    Elsewhere a record may carry fewer properties than the component
    describes, because an owner shares only some of theirs; here the account
    is the caller's own and carries every property the answer can hold, so a
    property the component promises and this account no longer carries is a
    promise to a client that nothing else would catch.
    """
    owner = account_owner(client, "shape-filled")
    fill_in(owner)
    AccountRecoveryFactory.create(user=owner, secondary_email="shape-filled-recovery@example.com")
    with override_switch(ENABLE_SECONDARY_EMAIL_FEATURE_SWITCH, active=True):
        with name_affirmation_answering("Checked Name"):
            body = client.get("/api/user/v2/accounts/me/").json()
    assert {name: body[name] for name in FILLED_IN_ONLY_PROPERTIES} == {
        "country": "US",
        "level_of_education": "m",
        "mailing_address": "12 Park Avenue",
        "pending_name_change": "Changed Name",
        "phone_number": "+18005555555",
        "secondary_email": "shape-filled-recovery@example.com",
        "state": "MA",
        "verified_name": "Checked Name",
    }
    assert body["bio"] == "Teaches mathematics."
    assert body["gender"] == "f"
    assert body["goals"] == "Finish the course."
    assert body["year_of_birth"] == 1985
    assert body["language_proficiencies"] == [{"code": "fr"}]
    assert body["social_links"] == [
        {"platform": "twitter", "social_link": "https://www.twitter.com/example"},
    ]
    assert_records_match(schema, body, "Account")
    assert set(body) == set(schema["components"]["schemas"]["Account"]["properties"])


@pytest.mark.django_db
@skip_unless_lms
def test_a_change_body_built_from_what_the_document_publishes_is_one_the_endpoint_applies(client, schema):
    """
    The published change body is checked by sending one and reading the answer.

    A body a generated client builds comes from the request component alone,
    so each property it offers is sent here with a value of the type it
    publishes, and the account that comes back has to hold every one of them.
    """
    account_owner(client, "shape-changer")
    change = {
        "bio": "Teaches mathematics.",
        "country": "US",
        "gender": "f",
        "goals": "Finish the course.",
        "level_of_education": "m",
        "mailing_address": "12 Park Avenue",
        "name": "Changed Name",
        "phone_number": "+18005555555",
        "state": "MA",
        "year_of_birth": 1985,
    }
    validator = jsonschema.Draft202012Validator(published_component_schema(schema, "PatchedAccountUpdate"))
    assert [error.message for error in validator.iter_errors(change)] == []
    answer = client.patch(
        "/api/user/v2/accounts/me/",
        data=json.dumps(change),
        content_type="application/merge-patch+json",
    )
    assert answer.status_code == 200, answer.content
    account = answer.json()
    assert {name: account[name] for name in change} == change
    assert_records_match(schema, account, "Account")


@skip_unless_lms
def test_the_refusal_for_a_retired_email_address_publishes_what_it_adds_to_the_envelope(schema):
    """The two members that refusal carries beyond the envelope are typed, not left to be guessed."""
    components = schema["components"]["schemas"]
    published = components["RetiredAccountErrorResponse"]
    assert set(components["ErrorResponse"]["properties"]) < set(published["properties"])
    assert published["properties"]["can_cancel_retirement"] == {
        "type": "boolean",
        "description": (
            "Whether the retirement can still be reversed, which it can only while it is inside "
            "the cooling-off period."
        ),
    }
    assert published["properties"]["retirement_id"] == {
        "type": "integer",
        "nullable": True,
        "description": (
            "Identifies the retirement to a caller able to reverse it, or null when there is "
            "nothing left to reverse."
        ),
    }
    assert published["required"] == components["ErrorResponse"]["required"]
    refusal = schema["paths"][ACCOUNT_LIST_PATH]["get"]["responses"]["404"]
    assert refusal["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RetiredAccountErrorResponse",
    }


@skip_unless_lms
def test_the_version_one_account_addresses_keep_an_operation_id_of_their_own(schema):
    """The collection and the member tokenize alike, and are published apart."""
    published = {
        (path, method): operation["operationId"]
        for path, method, operation in operations(schema)
        if path in SUPERSEDED_USER_ADDRESSES
    }
    assert published[("/api/user/v1/accounts", "get")] == "v1_accounts_list"
    assert published[("/api/user/v1/accounts/{username}", "get")] == "v1_accounts_retrieve"
    assert published[("/api/user/v1/accounts/{username}", "patch")] == "v1_accounts_partial_update"
    assert len(set(published.values())) == len(published)


@skip_unless_lms
def test_each_renamed_operation_id_names_an_address_in_the_document_that_derives_it(schema, service_wide):
    """
    A rename that outlived the address it was written for would name nothing.

    An entry is keyed by the id the generator derives, which differs between the
    document that trims the area off the front of each path and the one that
    trims nothing, so each entry applies to exactly one of them.
    """
    documents = {"this version": schema, "service-wide": service_wide}
    for (path, method, _derived), operation_id in RENAMED_OPERATION_IDS.items():
        naming = [
            name for name, document in documents.items()
            if document["paths"].get(path, {}).get(method.lower(), {}).get("operationId") == operation_id
        ]
        assert len(naming) == 1, (path, method, operation_id, naming)


@skip_unless_lms
def test_a_name_change_publishes_the_body_it_reads_and_the_one_it_returns(schema):
    operation = schema["paths"][NAME_CHANGE_PATH]["post"]
    # A name is posted from a web form as often as it is sent as JSON, so the
    # media types a form submits in are published alongside it.
    assert set(operation["requestBody"]["content"]) == {
        "application/json", "application/x-www-form-urlencoded", "multipart/form-data",
    }
    for media_type in operation["requestBody"]["content"]:
        assert operation["requestBody"]["content"][media_type]["schema"] == {
            "$ref": "#/components/schemas/NameChange",
        }, media_type
    assert operation["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/NameChange",
    }
    assert sorted(operation["responses"]) == ["201", "400", "401", "403", "404", "406", "415"]
    published = schema["components"]["schemas"]["NameChange"]
    assert published["required"] == ["name"]
    assert published["properties"]["name"]["maxLength"] == 255
    assert published["properties"]["name"]["description"] == (
        "Full name to put on the profile once the request is confirmed."
    )


@skip_unless_lms
def test_a_confirmation_publishes_that_it_reads_nothing_and_answers_with_nothing(schema):
    operation = schema["paths"][NAME_CHANGE_CONFIRMATION_PATH]["post"]
    assert "requestBody" not in operation
    assert operation["responses"]["204"]["description"] == (
        "The profile now carries the name that was asked for."
    )
    assert "content" not in operation["responses"]["204"]
    assert sorted(operation["responses"]) == ["204", "400", "401", "403", "404", "406"]


#: Every form an image may be sent in: the media type, the name a body sent in
#: that type carries, and the extension of the image to send. The extension of
#: the name is held against the media type, and a form part carries its own
#: name, so it needs none.
IMAGE_UPLOAD_FORMS = (
    ("multipart/form-data", None, ".jpeg"),
    ("image/gif", "picture.gif", ".gif"),
    ("image/jpeg", "picture.jpg", ".jpg"),
    ("image/pjpeg", "picture.jpg", ".jpg"),
    ("image/png", "picture.png", ".png"),
)

#: The address the image upload supersedes, which reads the same forms.
SUPERSEDED_IMAGE_PATH = "/api/user/v1/accounts/{username}/image"


@skip_unless_lms
def test_the_profile_image_publishes_every_media_type_it_reads_the_image_in(schema):
    """The image is read as a part of a form or as the whole body, and both are published."""
    operation = schema["paths"][PROFILE_IMAGE_PATH]["post"]
    content = operation["requestBody"]["content"]
    assert set(content) == {media_type for media_type, _name, _extension in IMAGE_UPLOAD_FORMS}
    assert operation["requestBody"]["required"] is True
    assert content["multipart/form-data"]["schema"] == {
        "$ref": "#/components/schemas/ProfileImageUpload",
    }
    for media_type in set(content) - {"multipart/form-data"}:
        assert content[media_type]["schema"] == {"type": "string", "format": "binary"}, media_type
    header = [
        parameter for parameter in operation["parameters"] if parameter["in"] == "header"
    ]
    assert [parameter["name"] for parameter in header] == ["Content-Disposition"]
    assert header[0].get("required", False) is False
    assert 'filename="picture.png"' in header[0]["description"]
    published = schema["components"]["schemas"]["ProfileImageUpload"]
    assert published["required"] == ["file"]
    assert published["properties"]["file"] == {
        "type": "string",
        "format": "binary",
        "description": (
            "Image to show beside the user's name. It is stored in each size the site renders, "
            "cropped to a square, and must be a JPEG, PNG or GIF within the size limits this "
            "site configures."
        ),
    }
    assert sorted(operation["responses"]) == ["204", "400", "401", "403", "404", "406", "415"]


@pytest.mark.django_db
@skip_unless_lms
def test_every_media_type_published_for_an_image_is_one_the_endpoint_stores_it_from(client, schema):
    """
    Each form the document offers for an image is sent to the endpoints that serve it.

    A client generated from the document sends one of the published types, so
    a type published at either address and refused there would leave such a
    client with no way to set an image. Both addresses read the same forms and
    publish the same list, which is read from the document here rather than
    written out again.
    """
    owner = UserFactory.create(username="image_media_types", password=PASSWORD)
    assert client.login(username=owner.username, password=PASSWORD)
    addresses = {
        SUPERSEDED_IMAGE_PATH: f"/api/user/v1/accounts/{owner.username}/image",
        PROFILE_IMAGE_PATH: f"/api/user/v2/accounts/{owner.username}/profile_image/",
    }
    storage = get_profile_image_storage()
    try:
        for published, address in addresses.items():
            offered = set(schema["paths"][published]["post"]["requestBody"]["content"])
            assert offered == {media_type for media_type, _name, _ext in IMAGE_UPLOAD_FORMS}, published
            for media_type, name, extension in IMAGE_UPLOAD_FORMS:
                with make_image_file(extension=extension) as image_file:
                    if name is None:
                        answer = client.post(address, {"file": image_file})
                    else:
                        answer = client.post(
                            address,
                            data=image_file.read(),
                            content_type=media_type,
                            HTTP_CONTENT_DISPOSITION=f'attachment; filename="{name}"',
                        )
                assert answer.status_code == 204, (address, media_type, answer.content)
    finally:
        for stored in get_profile_image_names(owner.username).values():
            storage.delete(stored)


@skip_unless_lms
def test_clearing_the_profile_image_publishes_that_it_reads_nothing(schema):
    operation = schema["paths"][PROFILE_IMAGE_PATH]["delete"]
    assert "requestBody" not in operation
    assert [parameter["name"] for parameter in operation["parameters"]] == ["username"]
    assert operation["responses"]["204"]["description"] == "The account carries no image of its own."
    assert sorted(operation["responses"]) == ["204", "400", "401", "403", "404", "406"]
    assert operation["responses"]["400"]["description"] == (
        "The request carries a query parameter this endpoint does not read."
    )


@skip_unless_lms
def test_a_verification_status_publishes_the_properties_it_carries(schema):
    body = schema["paths"][VERIFICATION_STATUS_PATH]["get"]["responses"]["200"]
    assert body["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/VerificationStatus",
    }
    published = schema["components"]["schemas"]["VerificationStatus"]
    assert set(published["properties"]) == {"is_verified", "status", "expiration_datetime"}
    assert sorted(published["required"]) == ["expiration_datetime", "is_verified", "status"]
    assert published["properties"]["is_verified"]["type"] == "boolean"
    assert all(published["properties"][name]["description"] for name in published["properties"])


@skip_unless_lms
def test_the_day_a_summary_reports_is_published_as_the_day_it_carries(schema):
    """
    The summary reports a calendar day and an attempt records a moment, under one name.

    A summary says when an accepted proof runs out as the day alone, so the
    property is published with the shape of that day rather than as a
    date-time, which is what the same property of an attempt carries.
    """
    summary = schema["components"]["schemas"]["VerificationStatus"]["properties"]["expiration_datetime"]
    attempt = schema["components"]["schemas"]["Verification"]["properties"]["expiration_datetime"]
    assert summary["type"] == "string"
    assert summary["pattern"] == EXPIRATION_DAY_PATTERN
    assert "format" not in summary
    assert "MM/DD/YYYY" in summary["description"]
    assert attempt["type"] == "string"
    assert attempt["format"] == "date-time"
    assert attempt["nullable"] is True


@skip_unless_lms
def test_every_status_a_recorded_attempt_can_hold_is_published(schema):
    """
    A client switching on these values meets no stored value the text leaves out.

    The attempts come from a table per kind of check, and the statuses those
    tables hold are not the same set, so what is published has to name every
    one of them.
    """
    published = schema["components"]["schemas"]["Verification"]["properties"]["status"]["description"]
    stored = {
        str(value)
        for choices in (IDVerificationAttempt.STATUS, VerificationAttempt.STATUS)
        for value, _label in choices
    }
    assert stored == {"created", "ready", "submitted", "must_retry", "pending", "approved", "denied"}
    for value in sorted(stored):
        assert f"`{value}`" in published, value


@skip_unless_lms
def test_a_verification_attempt_publishes_the_properties_it_carries(schema):
    page = schema["components"]["schemas"]["PaginatedVerificationList"]
    assert page["properties"]["results"]["items"] == {"$ref": "#/components/schemas/Verification"}
    published = schema["components"]["schemas"]["Verification"]
    assert set(published["properties"]) == {
        "type", "status", "expiration_datetime", "message", "updated_at", "receipt_id",
    }
    assert published["properties"]["expiration_datetime"]["nullable"] is True
    assert published["properties"]["receipt_id"]["nullable"] is True
    assert published["properties"]["type"]["readOnly"] is True
    assert all(published["properties"][name]["description"] for name in published["properties"])


@skip_unless_lms
def test_an_account_publishes_an_example_of_each_shape_it_answers_with(schema):
    """
    Each example a client author reads is a body this endpoint really returns.

    The answer is whichever of three shapes the caller's standing and request
    produce, so each is published, and each is held to the component and to
    the set of properties that shape carries.
    """
    published = schema["paths"][ACCOUNT_MEMBER_PATH]["get"]["responses"]["200"]
    examples = published["content"]["application/json"]["examples"]
    described_by = {
        "YourOwnAccount": "Account",
        "AnAccountSomebodyElseOwns": "Account",
        "AnAccountReducedToWhatIdentifiesItsOwner": "AccountMinimal",
    }
    assert list(examples) == list(described_by)
    assert [example["description"] for example in examples.values()] == [
        "Everything an account carries, which is what its owner and a staff caller receive.",
        "The properties this owner shares with every learner. An owner who shares field by field "
        "shares fewer, and one who shares nothing leaves only the three properties every account "
        "carries.",
        "What `view=minimal` answers with; `fields` selects any other set.",
    ]
    for name, example in examples.items():
        assert_records_match(schema, example["value"], described_by[name], case=name)

    whole = examples["YourOwnAccount"]["value"]
    assert set(whole) == set(schema["components"]["schemas"]["Account"]["properties"])
    shared = examples["AnAccountSomebodyElseOwns"]["value"]
    assert set(shared) == set(settings.ACCOUNT_VISIBILITY_CONFIGURATION["bulk_shareable_fields"])
    assert shared["account_privacy"] == "all_users"
    reduced = examples["AnAccountReducedToWhatIdentifiesItsOwner"]["value"]
    assert set(reduced) == set(ACCOUNT_MINIMAL_FIELDS)


@skip_unless_lms
def test_the_verification_list_publishes_the_order_it_can_be_read_in(schema):
    """The values published are the values the serializer takes, so the two cannot drift apart."""
    field = VerificationQuerySerializer().fields["ordering"]
    parameter = published_ordering(schema, VERIFICATION_LIST_PATH)
    assert parameter["schema"]["enum"] == ["updated_at", "-updated_at", ""]
    assert parameter["schema"]["enum"] == [*field.choices, ""]
    assert parameter["schema"]["default"] == "-updated_at" == field.default
    assert parameter["description"].startswith(field.help_text)
    assert parameter.get("required", False) is False


@skip_unless_lms
def test_every_list_publishes_its_sort_as_the_values_it_takes_and_the_order_it_uses_without_one(schema):
    """
    The sort of every list is asked for under one name, and each publishes both
    the values it takes and the order a caller that sends none is answered in.

    A list that can be sorted by several properties at once takes them as a
    comma-separated array; one with a single sortable property takes that
    property on its own, with the empty value asking for the default.
    """
    published = {}
    for path in (
        "/api/user/v2/users/",
        "/api/user/v2/user_preferences/",
        ACCOUNT_LIST_PATH,
        VERIFICATION_LIST_PATH,
    ):
        shape = published_ordering(schema, path)["schema"]
        published[path] = (shape["type"], sorted(shape.get("items", shape)["enum"]), shape["default"])
    assert published == {
        "/api/user/v2/users/": (
            "array", ["-date_joined", "-username", "date_joined", "username"], ["username"],
        ),
        "/api/user/v2/user_preferences/": (
            "array", ["-key", "-username", "key", "username"], ["username", "key"],
        ),
        ACCOUNT_LIST_PATH: ("string", ["", "-username", "username"], "username"),
        VERIFICATION_LIST_PATH: ("string", ["", "-updated_at", "updated_at"], "-updated_at"),
    }


@skip_unless_lms
def test_an_account_and_a_row_of_the_account_collection_are_narrowed_by_the_same_presets(schema):
    """One account is shaped by the presets that shape a row of the collection, and by the same values."""
    presets = {}
    for path, method in ((ACCOUNT_LIST_PATH, "get"), (ACCOUNT_MEMBER_PATH, "get"), (ACCOUNT_MEMBER_PATH, "patch")):
        parameter = next(
            published for published in schema["paths"][path][method]["parameters"]
            if published["name"] == "view"
        )
        presets[path, method] = set(parameter["schema"]["enum"])
    assert presets == {
        (ACCOUNT_LIST_PATH, "get"): {"minimal", "shared", ""},
        (ACCOUNT_MEMBER_PATH, "get"): {"minimal", "shared", ""},
        (ACCOUNT_MEMBER_PATH, "patch"): {"minimal", "shared", ""},
    }


@skip_unless_lms
def test_the_addresses_this_phase_adds_publish_no_other_query_parameters(schema):
    """Each of these addresses reads what it names and nothing else."""
    published = {
        path: sorted(
            parameter["name"] for parameter in operation.get("parameters", [])
            if parameter["in"] == "query"
        )
        for path, _method, operation in operations(schema)
        if path in {
            NAME_CHANGE_PATH,
            NAME_CHANGE_CONFIRMATION_PATH,
            PROFILE_IMAGE_PATH,
            VERIFICATION_STATUS_PATH,
            VERIFICATION_LIST_PATH,
        }
    }
    assert published == {
        NAME_CHANGE_PATH: [],
        NAME_CHANGE_CONFIRMATION_PATH: [],
        PROFILE_IMAGE_PATH: [],
        VERIFICATION_STATUS_PATH: [],
        VERIFICATION_LIST_PATH: ["ordering", "page", "page_size"],
    }


#: The names this resource's operations are published under. The two reads
#: tokenize alike, so the read of one preference is named for what it reads and
#: the read of the whole document keeps the name both addresses derive; left to
#: itself the assembled document tells the two apart with a numeral, which says
#: which address sorts first rather than what a client gets back.
PREFERENCE_OPERATION_IDS = {
    (PREFERENCE_DOCUMENT_PATH, "get"): "v2_accounts_preferences_retrieve",
    (PREFERENCE_DOCUMENT_PATH, "patch"): "v2_accounts_preferences_partial_update",
    (PREFERENCE_NAME_PATH, "get"): "v2_accounts_preferences_retrieve_one",
    (PREFERENCE_NAME_PATH, "put"): "v2_accounts_preferences_update",
    (PREFERENCE_NAME_PATH, "delete"): "v2_accounts_preferences_destroy",
    (EMAIL_OPT_IN_PATH, "post"): "v2_email_opt_ins_create",
}


@skip_unless_lms
def test_the_preference_operations_carry_the_names_they_are_published_under(schema):
    published = {
        (path, method): operation["operationId"]
        for path, method, operation in operations(schema)
        if path in {PREFERENCE_DOCUMENT_PATH, PREFERENCE_NAME_PATH, EMAIL_OPT_IN_PATH}
    }
    assert published == PREFERENCE_OPERATION_IDS


@skip_unless_lms
def test_the_read_of_one_preference_is_named_in_every_document_that_publishes_it(schema, service_wide):
    """
    Each document names that read as it names its four siblings, and loses no name.

    The two documents derive different names for one operation, because one
    trims the area off the front of every path and the other trims nothing, so
    the read is named in each of them through the name that document derives.
    That leaves the five operations of this resource carrying the same leading
    tokens wherever they are published, which is what a client generated from
    a document takes its module from. The addresses of version 1 are named the
    same way, and there only the document that trims the area off is covered:
    the one that trims nothing keeps every name it has published, numeral and
    all.
    """
    assert schema["paths"][PREFERENCE_NAME_PATH]["get"]["operationId"] == (
        "v2_accounts_preferences_retrieve_one"
    )
    assert service_wide["paths"][PREFERENCE_NAME_PATH]["get"]["operationId"] == (
        "api_user_v2_accounts_preferences_retrieve_one"
    )
    assert schema["paths"][PREFERENCE_DOCUMENT_PATH]["get"]["operationId"] == (
        "v2_accounts_preferences_retrieve"
    )
    assert service_wide["paths"][PREFERENCE_DOCUMENT_PATH]["get"]["operationId"] == (
        "api_user_v2_accounts_preferences_retrieve"
    )
    assert {
        (path, method): operation["operationId"]
        for path, method, operation in operations(service_wide)
        if path in {PREFERENCE_DOCUMENT_PATH, PREFERENCE_NAME_PATH}
    } == {
        (PREFERENCE_DOCUMENT_PATH, "get"): "api_user_v2_accounts_preferences_retrieve",
        (PREFERENCE_DOCUMENT_PATH, "patch"): "api_user_v2_accounts_preferences_partial_update",
        (PREFERENCE_NAME_PATH, "get"): "api_user_v2_accounts_preferences_retrieve_one",
        (PREFERENCE_NAME_PATH, "put"): "api_user_v2_accounts_preferences_update",
        (PREFERENCE_NAME_PATH, "delete"): "api_user_v2_accounts_preferences_destroy",
    }
    version_one = {
        (path, method): operation["operationId"]
        for path, method, operation in operations(service_wide)
        if path.startswith("/api/user/v1/preferences/{username}")
    }
    assert version_one == {
        ("/api/user/v1/preferences/{username}", "get"): "api_user_v1_preferences_retrieve",
        ("/api/user/v1/preferences/{username}", "patch"): "api_user_v1_preferences_partial_update",
        ("/api/user/v1/preferences/{username}/{preference_key}", "get"): (
            "api_user_v1_preferences_retrieve_2"
        ),
        ("/api/user/v1/preferences/{username}/{preference_key}", "put"): "api_user_v1_preferences_update",
        ("/api/user/v1/preferences/{username}/{preference_key}", "delete"): (
            "api_user_v1_preferences_destroy"
        ),
    }


@skip_unless_lms
def test_the_stored_preference_reads_are_named_alike_in_every_document_that_publishes_them(schema, service_wide):
    """
    The collection read and the member read of this resource keep one prefix wherever published.

    A client takes the module it writes an operation into from the leading
    tokens of that operation's name, so a document naming the two reads of one
    resource differently splits the resource across two modules. The documents
    derive different names for the member read, because one trims the area off
    the front of every path and the other trims nothing, so it is named in
    each of them through the name that document derives.
    """
    assert {
        (path, method): operation["operationId"]
        for path, method, operation in operations(schema)
        if path in {PREFERENCE_LIST_PATH, PREFERENCE_MEMBER_PATH}
    } == {
        (PREFERENCE_LIST_PATH, "get"): "v2_user_preferences_list",
        (PREFERENCE_MEMBER_PATH, "get"): "v2_user_preferences_retrieve",
    }
    assert {
        (path, method): operation["operationId"]
        for path, method, operation in operations(service_wide)
        if path in {PREFERENCE_LIST_PATH, PREFERENCE_MEMBER_PATH}
    } == {
        (PREFERENCE_LIST_PATH, "get"): "api_user_v2_user_preferences_list",
        (PREFERENCE_MEMBER_PATH, "get"): "api_user_v2_user_preferences_retrieve",
    }


@skip_unless_lms
def test_the_preference_document_is_published_as_the_open_ended_object_it_is(schema):
    """It has no fixed set of names, so it is described rather than named as a component."""
    members = {
        ("get", "application/json"): {"type": "string"},
        # A change may also remove a name, which it does by sending a null.
        ("patch", "application/merge-patch+json"): {"type": "string", "nullable": True},
    }
    for (method, media_type), member in members.items():
        operation = schema["paths"][PREFERENCE_DOCUMENT_PATH][method]
        carried = (
            operation["responses"]["200"] if method == "get" else operation["requestBody"]
        )["content"]
        assert list(carried) == [media_type], method
        body = carried[media_type]["schema"]
        assert body["type"] == "object", method
        assert body["additionalProperties"] == member, method
        assert "$ref" not in body, method


@skip_unless_lms
def test_the_preference_document_is_published_as_the_fields_that_carry_it_describe(schema):
    """The body is formatted and read by these fields, so each address publishes what its own does."""
    carried = {
        ("get", "application/json"): AccountPreferenceDocumentField,
        ("patch", "application/merge-patch+json"): AccountPreferenceChangesField,
    }
    for (method, media_type), field in carried.items():
        operation = schema["paths"][PREFERENCE_DOCUMENT_PATH][method]
        published = (
            operation["responses"]["200"] if method == "get" else operation["requestBody"]
        )["content"][media_type]["schema"]
        assert published == get_override(field, "field"), method


@pytest.mark.django_db
@skip_unless_lms
def test_a_merge_patch_the_endpoint_carries_out_is_one_the_request_body_takes(client, schema):
    """A null removes a name, so a body generated from the document can express a removal."""
    owner = AdminFactory.create(username="patch-preferences", password=PASSWORD)
    UserPreferenceFactory.create(user=owner, key="time_zone", value="Asia/Tokyo")
    assert client.login(username=owner.username, password=PASSWORD)
    validator = jsonschema.Draft202012Validator(published_request_schema(
        schema, PREFERENCE_DOCUMENT_PATH, "patch", "application/merge-patch+json",
    ))
    assert [error.message for error in validator.iter_errors({"time_zone": None})] == []
    assert [error.message for error in validator.iter_errors({"time_zone": "Europe/Paris"})] == []
    assert [error.message for error in validator.iter_errors({"time_zone": 5})] == [
        "5 is not valid under any of the given schemas",
    ]
    address = reverse("user_v2:account_preference_list", kwargs={"username": owner.username})
    removal = client.patch(
        address, data=json.dumps({"time_zone": None}), content_type="application/merge-patch+json",
    )
    assert removal.status_code == 204, removal.content
    assert client.get(address).json() == {}


@pytest.mark.django_db
@skip_unless_lms
def test_a_change_that_names_no_preference_is_refused_by_the_document_too(client, schema):
    """
    A body naming nothing to change is refused, and the document says so as well as the handler.

    A caller that generates a request from the document should learn that the
    body has to name something before it is sent, rather than being told so
    by a refusal.
    """
    owner = AdminFactory.create(username="empty-patch", password=PASSWORD)
    UserPreferenceFactory.create(user=owner, key="time_zone", value="Asia/Tokyo")
    assert client.login(username=owner.username, password=PASSWORD)
    validator = jsonschema.Draft202012Validator(published_request_schema(
        schema, PREFERENCE_DOCUMENT_PATH, "patch", "application/merge-patch+json",
    ))
    assert [error.message for error in validator.iter_errors({})] == [
        "{} should be non-empty",
    ]
    assert [error.message for error in validator.iter_errors({"time_zone": "Europe/Paris"})] == []
    refusal = client.patch(
        reverse("user_v2:account_preference_list", kwargs={"username": owner.username}),
        data=json.dumps({}),
        content_type="application/merge-patch+json",
    )
    assert refusal.status_code == 400, refusal.content
    assert refusal.json()["errors"] == {
        "non_field_errors": ["Name at least one preference to change."],
    }


@pytest.mark.django_db
@skip_unless_lms
def test_a_preference_document_the_endpoint_answers_is_what_the_document_describes(client, schema):
    owner = AdminFactory.create(username="shape-preferences", password=PASSWORD)
    for key, value in (("pref-lang", "en"), ("time_zone", "Asia/Tokyo"), ("visibility.bio", "all_users")):
        UserPreferenceFactory.create(user=owner, key=key, value=value)
    assert client.login(username=owner.username, password=PASSWORD)
    body = client.get(
        reverse("user_v2:account_preference_list", kwargs={"username": owner.username}),
    ).json()
    assert body == {"pref-lang": "en", "time_zone": "Asia/Tokyo", "visibility.bio": "all_users"}
    validator = jsonschema.Draft202012Validator(
        published_body_schema(schema, PREFERENCE_DOCUMENT_PATH),
    )
    assert [error.message for error in validator.iter_errors(body)] == []


@pytest.mark.django_db
@skip_unless_lms
def test_the_email_choice_publishes_the_refusal_a_session_is_answered_with_without_its_token(schema):
    """
    The refusal a browser session meets here is published alongside the rest.

    This address is reached from a page, and a session is held to the request
    token the site hands out with it, so the refusal of a request that carries
    none is the one a browser client meets most. It is driven here and matched
    against what the operation publishes, because the choice belongs to the
    caller and so nothing else about who they are is refused at this address.
    """
    user = UserFactory.create(username="optin_token", password=PASSWORD)
    held_to_its_token = APIClient(enforce_csrf_checks=True)
    assert held_to_its_token.login(username=user.username, password=PASSWORD)
    answer = held_to_its_token.post(
        EMAIL_OPT_IN_PATH,
        {"course_key": "course-v1:edX+DemoX+Demo", "email_opt_in": True},
        format="json",
    )
    assert answer.status_code == 403, answer.content
    published = schema["paths"][EMAIL_OPT_IN_PATH]["post"]["responses"]
    assert str(answer.status_code) in published
    assert published["403"]["description"] == (
        "The request was made with a session cookie and carried none of the request token the "
        "site hands out with it, so the site cannot tell that the request came from a page it "
        "served. Sending that token with the session, or a signed token in place of the "
        "session, is answered."
    )


@skip_unless_lms
def test_the_name_a_preference_is_stored_under_is_published_under_one_parameter_name(schema):
    """
    Both addresses that carry a preference name carry it under the same name.

    A client generated from the document takes an argument from each path
    parameter, so one notion published under two names in one version reaches
    a reader as two notions, and a published name cannot be changed afterwards.
    """
    carried = {
        parameter["name"]
        for path, _method, operation in operations(schema)
        if path in {PREFERENCE_MEMBER_PATH, PREFERENCE_NAME_PATH}
        for parameter in operation["parameters"]
        if parameter["in"] == "path"
    }
    assert carried == {"username", "key"}


#: What each preference operation answers, by the address and the method. A
#: change admits only the caller the address names, so the account it names is
#: the caller's own and is there to be changed: no such request is answered
#: with the refusal naming an account that does not exist, and none publishes
#: it.
PREFERENCE_PUBLISHED_ANSWERS = {
    (PREFERENCE_DOCUMENT_PATH, "get"): ["200", "400", "401", "403", "404", "406", "500"],
    (PREFERENCE_DOCUMENT_PATH, "patch"): ["204", "400", "401", "403", "406", "415", "500"],
    (PREFERENCE_NAME_PATH, "get"): ["200", "400", "401", "403", "404", "406", "500"],
    (PREFERENCE_NAME_PATH, "put"): ["204", "400", "401", "403", "404", "406", "415", "500"],
    (PREFERENCE_NAME_PATH, "delete"): ["204", "400", "401", "403", "404", "406", "500"],
}


@skip_unless_lms
def test_each_preference_operation_publishes_the_answers_it_can_give(schema):
    published = {
        (path, method): sorted(operation["responses"])
        for path, method, operation in operations(schema)
        if path in {PREFERENCE_DOCUMENT_PATH, PREFERENCE_NAME_PATH}
    }
    assert published == PREFERENCE_PUBLISHED_ANSWERS


@skip_unless_lms
def test_no_preference_operation_publishes_a_parameter_beyond_the_address_it_is_read_from(schema):
    """These addresses read no query parameter: not a page, not a preset, not a selection."""
    published = {
        (path, method): [parameter["name"] for parameter in operation.get("parameters", [])]
        for path, method, operation in operations(schema)
        if path in {PREFERENCE_DOCUMENT_PATH, PREFERENCE_NAME_PATH, EMAIL_OPT_IN_PATH}
    }
    assert published == {
        (PREFERENCE_DOCUMENT_PATH, "get"): ["username"],
        (PREFERENCE_DOCUMENT_PATH, "patch"): ["username"],
        (PREFERENCE_NAME_PATH, "get"): ["key", "username"],
        (PREFERENCE_NAME_PATH, "put"): ["key", "username"],
        (PREFERENCE_NAME_PATH, "delete"): ["key", "username"],
        (EMAIL_OPT_IN_PATH, "post"): [],
    }


@skip_unless_lms
def test_the_email_choice_publishes_the_body_it_reads_and_that_it_answers_with_nothing(schema):
    operation = schema["paths"][EMAIL_OPT_IN_PATH]["post"]
    # The page a learner makes this choice on submits a form, so the media
    # types a form submits in are published alongside the JSON body.
    assert set(operation["requestBody"]["content"]) == {
        "application/json", "application/x-www-form-urlencoded", "multipart/form-data",
    }
    for media_type, carried in operation["requestBody"]["content"].items():
        assert carried["schema"] == {"$ref": "#/components/schemas/EmailOptIn"}, media_type
    assert "content" not in operation["responses"]["204"]
    assert sorted(operation["responses"]) == [
        "204", "400", "401", "403", "404", "406", "415", "500",
    ]
    choice = schema["components"]["schemas"]["EmailOptIn"]
    assert sorted(choice["required"]) == ["course_key", "email_opt_in"]
    assert choice["properties"]["course_key"]["type"] == "string"
    assert choice["properties"]["email_opt_in"]["type"] == "boolean"
    # What a boolean is read from here is narrower than the media types allow,
    # so the published description says so.
    assert choice["properties"]["email_opt_in"]["description"] == (
        "Whether this user wants messages from that organization, sent as true or false. A "
        "value that says neither is refused rather than read as one of them."
    )


#: What every operation of this version says about a request it can give no
#: answer to. Which representations an answer can be given in is settled as
#: the request enters, before the address it names is read or written, so the
#: refusal reaches an operation that answers with a body and one that does not
#: alike.
UNSATISFIABLE_ACCEPT_DESCRIPTION = (
    "The `Accept` header of the request names no representation this endpoint can answer in."
)


@skip_unless_lms
def test_every_operation_publishes_the_refusal_of_an_accept_it_cannot_satisfy(schema):
    """
    No operation of this version leaves this refusal unpublished.

    This is asserted over every operation rather than per address, so an
    operation added later is held to it without this test being changed.
    """
    unpublished = {
        f"{method.upper()} {path}"
        for path, method, operation in operations(schema)
        if path in VERSION_TWO_PATHS and "406" not in operation["responses"]
    }
    assert unpublished == set()
    wrongly_described = {
        f"{method.upper()} {path}": operation["responses"]["406"]["description"]
        for path, method, operation in operations(schema)
        if path in VERSION_TWO_PATHS
        and operation["responses"]["406"]["description"] != UNSATISFIABLE_ACCEPT_DESCRIPTION
    }
    assert wrongly_described == {}


@skip_unless_lms
def test_asking_for_a_name_change_publishes_the_refusal_of_a_session_without_its_token(schema):
    """
    A write made with a session the site cannot tie to a page it served is refused here too.

    The sibling collection of this version that records a choice for the
    caller publishes that refusal; this one answers with it and did not
    publish it, so a caller handling failures by the statuses the document
    carries fell through to a status it had no model for.
    """
    asked = schema["paths"][NAME_CHANGE_PATH]["post"]["responses"]
    recorded = schema["paths"][EMAIL_OPT_IN_PATH]["post"]["responses"]
    assert "403" in asked
    assert asked["403"]["description"] == recorded["403"]["description"]
    assert asked["403"]["content"] == recorded["403"]["content"]
def dereferenced(schema, node):
    """
    Return a published node with its reference followed, if it carries one.

    A node describing a fixed set of values is published as a component whose
    name the generator derives from the field, and that name holds only while
    one field of the service claims it, so what the node points at is read
    here rather than named. A property carrying a description of its own is
    published as the one alternative it may match, which is followed too.
    """
    alternatives = node.get("allOf")
    if alternatives is not None and len(alternatives) == 1:
        node = alternatives[0]
    reference = node.get("$ref")
    if reference is None:
        return node
    return schema["components"]["schemas"][reference.rsplit("/", 1)[-1]]


def published_property(schema, path, method, name):
    """Return the published description of one property of a request body."""
    body = schema["paths"][path][method]["requestBody"]["content"]["application/json"]["schema"]
    return dereferenced(schema, dereferenced(schema, body)["properties"][name])


@skip_unless_lms
def test_the_erasure_publishes_the_sets_of_data_it_can_erase(schema):
    """A caller picks one of them in the body, so each is named in the document."""
    scope = published_property(schema, RETIREMENT_ERASURE_PATH, "post", "scope")
    assert scope["enum"] == list(ERASURE_SCOPES)
    body = schema["paths"][RETIREMENT_ERASURE_PATH]["post"]["requestBody"]
    assert body["required"] is True
    assert dereferenced(
        schema, body["content"]["application/json"]["schema"],
    )["required"] == ["scope"]


@skip_unless_lms
def test_a_batch_publishes_what_it_can_report_about_each_name_it_is_given(schema):
    result = schema["components"]["schemas"]["RetirementBatchResult"]
    outcome = dereferenced(schema, result["properties"]["outcome"])
    assert outcome["enum"] == list(BATCH_OUTCOMES)
    assert sorted(result["properties"]) == ["outcome", "username"]
    assert sorted(result["required"]) == ["outcome", "username"]


@skip_unless_lms
def test_both_batch_addresses_publish_the_same_exchange(schema):
    """They act on different records, and a caller reads and writes the same shapes at both."""
    exchanges = {
        path: (
            schema["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"],
            schema["paths"][path]["post"]["responses"]["200"]["content"]["application/json"]["schema"],
        )
        for path in (RETIREMENT_CLEANUP_PATH, PARTNER_REPORT_CLEANUP_PATH)
    }
    assert exchanges[RETIREMENT_CLEANUP_PATH] == exchanges[PARTNER_REPORT_CLEANUP_PATH]
    assert exchanges[RETIREMENT_CLEANUP_PATH] == (
        {"$ref": "#/components/schemas/RetirementBatch"},
        {"$ref": "#/components/schemas/RetirementBatchOutcome"},
    )


@skip_unless_lms
def test_the_queue_publishes_the_filters_it_narrows_by(schema):
    parameters = {
        parameter["name"]: parameter
        for parameter in schema["paths"][RETIREMENT_LIST_PATH]["get"]["parameters"]
    }
    assert set(parameters) == {
        "cool_off_days", "created_after", "created_before", "state",
        "fields", "ordering", "page", "page_size", "view",
    }
    for name in ("cool_off_days", "created_after", "created_before", "state"):
        assert parameters[name]["in"] == "query"
        assert parameters[name].get("required", False) is False, name
        assert parameters[name]["description"], name
    assert parameters["cool_off_days"]["schema"] == {"type": "integer", "minimum": 0, "maximum": 36500}
    assert parameters["created_after"]["schema"]["format"] == "date-time"
    assert parameters["created_before"]["schema"]["format"] == "date-time"
    assert parameters["state"]["schema"]["type"] == "string"
    assert parameters["ordering"]["schema"]["items"]["enum"] == sorted(
        [*RETIREMENT_SORT_FIELDS, *(f"-{field}" for field in RETIREMENT_SORT_FIELDS)],
    )
    assert "default" not in parameters["ordering"]["schema"]


@skip_unless_lms
def test_the_claim_publishes_how_many_entries_it_can_be_held_to(schema):
    parameters = {
        parameter["name"]: parameter
        for parameter in schema["paths"][PARTNER_REPORT_LIST_PATH]["post"]["parameters"]
    }
    assert set(parameters) == {"limit"}
    assert parameters["limit"]["schema"] == {"type": "integer", "minimum": 1, "maximum": 100}
    assert parameters["limit"].get("required", False) is False
    assert "requestBody" not in schema["paths"][PARTNER_REPORT_LIST_PATH]["post"]


@skip_unless_lms
def test_the_claim_answers_with_an_array_rather_than_a_page(schema):
    """The entries answered are the entries taken, so the answer is not cut into pages."""
    body = schema["paths"][PARTNER_REPORT_LIST_PATH]["post"]["responses"]["200"]
    assert body["content"]["application/json"]["schema"] == {
        "type": "array",
        "items": {"$ref": "#/components/schemas/RetirementPartnerReportRecord"},
    }


@skip_unless_lms
def test_selecting_properties_publishes_how_far_a_name_reaches(schema):
    """A caller is told that a dotted name selects nothing and is refused."""
    for path, method in (
        (RETIREMENT_MEMBER_PATH, "get"),
        (RETIREMENT_LIST_PATH, "get"),
    ):
        described = {
            parameter["name"]: parameter["description"]
            for parameter in schema["paths"][path][method]["parameters"]
        }["fields"]
        assert "`user.email`" in described, (path, method)
        assert "names no field a response carries and is refused" in described, (path, method)
        assert "the whole of that property is returned" not in described, (path, method)


@pytest.mark.django_db
@skip_unless_lms
def test_the_claim_publishes_exactly_the_properties_an_entry_carries(schema):
    """An entry is published with the properties it is answered with, and with no others."""
    user = UserFactory.create()
    UserRetirementPartnerReportingStatus.objects.create(
        user=user,
        original_username=user.username,
        original_email=user.email,
        original_name=user.profile.name,
    )
    answered = set(claim_partner_reports(UserRetirementPartnerReportingStatus.objects.all())[0])
    record = schema["components"]["schemas"]["RetirementPartnerReportRecord"]
    assert set(record["properties"]) == answered
    assert set(record["required"]) == answered


@skip_unless_lms
def test_the_queue_answers_with_a_page(schema):
    body = schema["paths"][RETIREMENT_LIST_PATH]["get"]["responses"]["200"]
    assert body["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/PaginatedRetirementResponseList",
    }
    page = schema["components"]["schemas"]["PaginatedRetirementResponseList"]
    assert set(page["properties"]) == PAGINATION_MEMBERS
    assert page["properties"]["results"] == {
        "type": "array",
        "items": {"$ref": "#/components/schemas/RetirementResponse"},
    }


@skip_unless_lms
def test_a_retirement_record_publishes_the_step_as_a_name(schema):
    record = schema["components"]["schemas"]["Retirement"]
    for name in ("current_state", "last_state"):
        assert record["properties"][name]["type"] == "string", name
    assert set(record["properties"]) == {
        "id", "user", "original_username", "original_email", "original_name",
        "retired_username", "retired_email", "current_state", "last_state", "created", "modified",
    }
    assert "responses" not in record["properties"]


#: What each operation of the retirement pipeline answers, by the address and
#: the method. The erasure is the one operation of this area that lets a
#: failure of the work it drives reach the caller, because a step that fails
#: partway through has already erased part of what it names and the caller
#: decides what to do about that, so it is the one that publishes a 500.
RETIREMENT_PUBLISHED_ANSWERS = {
    (RETIREMENT_LIST_PATH, "get"): ["200", "400", "401", "403", "404", "406"],
    # The request an account's owner makes publishes a 500 as well, because
    # the message about it is sent while the request is being recorded and a
    # message that cannot be sent leaves nothing recorded; and a 409, because
    # a record may already be held for the account it was made from.
    (RETIREMENT_LIST_PATH, "post"): ["204", "400", "401", "403", "406", "409", "415", "500"],
    (RETIREMENT_MEMBER_PATH, "get"): ["200", "400", "401", "403", "404", "406"],
    (RETIREMENT_MEMBER_PATH, "patch"): ["204", "400", "401", "403", "404", "406", "409", "415"],
    (RETIREMENT_MEMBER_PATH, "delete"): ["204", "400", "401", "403", "404", "406", "409"],
    (RETIREMENT_ERASURE_PATH, "post"): [
        "204", "400", "401", "403", "404", "406", "409", "415", "500",
    ],
    (RETIREMENT_CLEANUP_PATH, "post"): ["200", "400", "401", "403", "406", "409", "415"],
    (PARTNER_REPORT_LIST_PATH, "post"): ["200", "400", "401", "403", "406"],
    (PARTNER_REPORT_MEMBER_PATH, "put"): ["204", "400", "401", "403", "404", "406", "409"],
    (PARTNER_REPORT_CLEANUP_PATH, "post"): ["200", "400", "401", "403", "406", "409", "415"],
}


@skip_unless_lms
def test_each_retirement_operation_publishes_the_answers_it_can_give(schema):
    published = {
        (path, method): sorted(operation["responses"])
        for path, method, operation in operations(schema)
        if path in RETIREMENT_PATHS
    }
    assert published == RETIREMENT_PUBLISHED_ANSWERS


@skip_unless_lms
def test_the_move_of_a_retirement_publishes_the_whole_body_it_reads(schema):
    """
    The move is an instruction read whole, so what it requires is published as required.

    A body carrying changes to a stored record leaves every field optional and
    is published under a name of its own. This one is refused when either the
    step to move to or what happened at the step being left is missing, so a
    caller generating a request from the document is told both are needed, and
    the account update beside it -- which really does carry changes -- keeps
    the optional form.
    """
    body = schema["paths"][RETIREMENT_MEMBER_PATH]["patch"]["requestBody"]
    assert body["required"] is True
    assert body["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RetirementUpdate",
    }
    component = schema["components"]["schemas"]["RetirementUpdate"]
    assert component["required"] == ["new_state", "response"]
    assert sorted(component["properties"]) == ["force", "new_state", "response"]
    assert "PatchedRetirementUpdate" not in schema["components"]["schemas"]

    changes = schema["paths"][ACCOUNT_MEMBER_PATH]["patch"]["requestBody"]
    assert changes.get("required", False) is False
    assert "required" not in schema["components"]["schemas"]["PatchedAccountUpdate"]


@skip_unless_lms
def test_a_retirement_publishes_the_profile_it_answers_as_null_where_there_is_none(schema):
    """An account being retired need not hold a profile, and the property is answered either way."""
    account = schema["components"]["schemas"]["RetirementAccount"]
    assert account["properties"]["profile"] == {
        "allOf": [{"$ref": "#/components/schemas/RetirementAccountProfile"}],
        "description": "The profile of the account, or null for an account that has none.",
        "readOnly": True,
        "nullable": True,
    }
    assert "profile" in account["required"]
    # The profile itself carries no property that may be absent, so nothing
    # below it is published as nullable.
    profile = schema["components"]["schemas"]["RetirementAccountProfile"]
    assert [name for name, published in profile["properties"].items() if published.get("nullable")] == []


#: What the body of a superseded operation requires, for the operations whose
#: body is written out as a shape rather than named by a serializer. The
#: entries are addresses of several resources, because the rule is read from
#: the shape and not from the address it is published at.
SUPERSEDED_NEEDED_BODIES = {
    ("/api/user/v1/accounts/retirement_cleanup/", "post"): ["usernames"],
    ("/api/user/v1/accounts/retirement_partner_report/", "put"): ["username"],
    ("/api/user/v1/accounts/retirement_partner_report_cleanup/", "post"): ["original_username"],
    ("/api/user/v1/accounts/retire/", "post"): ["username"],
    ("/api/user/v1/accounts/retire_misc/", "post"): ["username"],
    ("/api/user/v1/accounts/update_retirement_status/", "patch"): ["username", "new_state", "response"],
}


def members_needed(shape):
    """Return the members a body shape cannot be sent without, through a list to its members."""
    if "items" in shape:
        return members_needed(shape["items"])
    return shape["required"]


@skip_unless_lms
def test_a_superseded_operation_publishes_a_body_it_cannot_be_called_without(schema, service_wide):
    """
    A body written out as a shape says what it needs, and the operation needs it.

    An operation whose body is named by a serializer is marked as needing one
    by the fields that serializer requires. A body written out as a shape is
    published as it stands, so nothing has judged whether the operation can be
    called with no body at all: it is read here from what the shape itself
    says it cannot be sent without. Both documents are checked, because the
    one the service serves for browsing its whole surface has published these
    operations for years and a client generated from it stops sending a body
    the moment the document stops asking for one.
    """
    for document in (schema, service_wide):
        for (path, method), needed in SUPERSEDED_NEEDED_BODIES.items():
            body = document["paths"][path][method]["requestBody"]
            assert body["required"] is True, (path, method)
            assert members_needed(
                body["content"]["application/json"]["schema"],
            ) == needed, (path, method)


@skip_unless_lms
def test_the_claim_of_the_partner_report_queue_publishes_no_body_on_either_version(schema, service_wide):
    """Neither reads one: the queue it drains is named by nothing the caller sends."""
    for document in (schema, service_wide):
        for path in ("/api/user/v1/accounts/retirement_partner_report/", PARTNER_REPORT_LIST_PATH):
            assert "requestBody" not in document["paths"][path]["post"], path


#: The retirement-status model as it was published while the corrected request
#: body was its only reference. It is written out here rather than read from
#: the configuration, so that a pin which no longer describes what a client
#: already generates from fails this.
PATCHED_RETIREMENT_STATUS_COMPONENT = {
    "type": "object",
    "description": "Perform serialization for the RetirementStatus model",
    "properties": {
        "id": {"type": "integer", "readOnly": True},
        "user": {"allOf": [{"$ref": "#/components/schemas/RetirementUser"}], "readOnly": True},
        "current_state": {"allOf": [{"$ref": "#/components/schemas/RetirementState"}], "readOnly": True},
        "last_state": {"allOf": [{"$ref": "#/components/schemas/RetirementState"}], "readOnly": True},
        "created": {"type": "string", "format": "date-time", "readOnly": True},
        "modified": {"type": "string", "format": "date-time", "readOnly": True},
        "original_username": {"type": "string", "maxLength": 150},
        "original_email": {"type": "string", "format": "email", "maxLength": 254},
        "original_name": {"type": "string", "maxLength": 255},
        "retired_username": {"type": "string", "maxLength": 150},
        "retired_email": {"type": "string", "format": "email", "maxLength": 254},
    },
}


@skip_unless_lms
def test_the_component_the_corrected_request_body_leaves_behind_is_carried_by_both_documents(schema, service_wide):
    """
    The partial update of a version 1 retirement was the component's only reference.

    A client generated from a document names a class after each component in
    it, so a component that loses its last reference is named in the settings
    both documents are built from, rather than disappearing with the body that
    was wrong. The document the service serves for browsing its whole surface
    overlays its own keys on those settings and keeps this one, and the
    narrower document a client library is generated from carries it too.
    """
    for document in (schema, service_wide):
        components = document["components"]["schemas"]
        assert components["PatchedUserRetirementStatus"] == PATCHED_RETIREMENT_STATUS_COMPONENT
        # Nothing the kept component refers to is missing from the document
        # that carries it, so no reference in it dangles.
        assert "RetirementUser" in components
        assert "RetirementState" in components


#: The two addresses whose published name the body correction would otherwise
#: move, with the name the service-wide document publishes for each.
CORRECTED_QUEUE_READS = {
    "/api/user/v1/accounts/retirement_queue/": "api_user_v1_accounts_retirement_queue_retrieve",
    "/api/user/v1/accounts/retirements_by_status_and_date/": (
        "api_user_v1_accounts_retirements_by_status_and_date_retrieve"
    ),
}


@pytest.mark.django_db
@skip_unless_lms
def test_the_corrected_queue_reads_keep_the_names_the_service_wide_document_publishes(schema, service_wide):
    """
    Correcting a body to the array it answers with would otherwise move its name.

    The generator names an operation for what it answers with, so an operation
    whose body is corrected from one record to the array of them is named for a
    list where it was named for a single record. Both of these have been
    published under the single-record name for years and a client imports a
    module by that name, so each is pinned; the document that has published
    neither keeps the name it derives.
    """
    published = {
        path: service_wide["paths"][path]["get"]["operationId"] for path in CORRECTED_QUEUE_READS
    }
    assert published == CORRECTED_QUEUE_READS

    with mock.patch.dict("openedx.core.djangoapps.user_api.schema.RENAMED_OPERATION_IDS", {}, clear=True):
        unpinned = service_wide_from_user_mounts()
    assert {
        path: unpinned["paths"][path]["get"]["operationId"] for path in CORRECTED_QUEUE_READS
    } == {
        "/api/user/v1/accounts/retirement_queue/": "api_user_v1_accounts_retirement_queue_list",
        "/api/user/v1/accounts/retirements_by_status_and_date/": (
            "api_user_v1_accounts_retirements_by_status_and_date_list"
        ),
    }
    # The document this area's version is published in has carried neither
    # address before, so each keeps the name derived there.
    assert {
        path: schema["paths"][path]["get"]["operationId"] for path in CORRECTED_QUEUE_READS
    } == {
        "/api/user/v1/accounts/retirement_queue/": "v1_accounts_retirement_queue_list",
        "/api/user/v1/accounts/retirements_by_status_and_date/": (
            "v1_accounts_retirements_by_status_and_date_list"
        ),
    }


#: The forms the request a caller makes for their own retirement is published
#: as being read in, which are the three that address reads: two shipped
#: applications send it as a web form and the pages that make the same request
#: send it as JSON.
OWN_RETIREMENT_REQUEST_MEDIA_TYPES = {
    "application/json",
    "application/x-www-form-urlencoded",
    "multipart/form-data",
}

#: The credentials the request a caller makes for their own retirement is
#: published as taking. The generator describes the session cookie from the
#: class that reads it and nothing else, so the two tokens are named by the
#: operation, from the schemes the service appends.
OWN_RETIREMENT_REQUEST_CREDENTIALS = [{"cookieAuth": []}, {"jwt": []}, {"bearer": []}]


@skip_unless_lms
def test_the_request_for_a_callers_own_retirement_publishes_the_forms_it_reads(schema):
    """A client generated from the document sends the form the shipped applications send."""
    published = schema["paths"]["/api/user/v2/retirements/"]["post"]["requestBody"]
    assert set(published["content"]) == OWN_RETIREMENT_REQUEST_MEDIA_TYPES
    assert published["required"] is True
    assert {
        media_type: body["schema"]["$ref"] for media_type, body in published["content"].items()
    } == dict.fromkeys(
        OWN_RETIREMENT_REQUEST_MEDIA_TYPES, "#/components/schemas/RetirementRequest",
    )
    body = schema["components"]["schemas"]["RetirementRequest"]
    assert body["required"] == ["password"]
    assert body["properties"]["password"]["writeOnly"] is True


@skip_unless_lms
def test_the_request_for_a_callers_own_retirement_publishes_every_credential_it_takes(schema):
    """
    A client is generated from this operation, so what it publishes is what that client sends.

    Two shipped applications reach it with an access token the authorization
    endpoint handed out, which is the whole reason the operation takes one, and
    a client offering no way to send that token is a client those callers
    cannot use.
    """
    assert schema["paths"]["/api/user/v2/retirements/"]["post"]["security"] == (
        OWN_RETIREMENT_REQUEST_CREDENTIALS
    )


@skip_unless_lms
def test_every_credential_an_operation_publishes_is_one_the_document_describes(schema):
    """A name no scheme is published under leaves a generated client unable to send it."""
    named = {
        credential
        for operations in schema["paths"].values()
        for operation in operations.values()
        if isinstance(operation, dict)
        for alternative in operation.get("security", ())
        for credential in alternative
    }
    assert named == {"cookieAuth", "jwt", "bearer"}
    assert named <= set(schema["components"]["securitySchemes"])


@skip_unless_lms
def test_the_replacement_publishes_the_body_it_reads_rather_than_the_one_it_answers(schema):
    """The answer carries a result per mapping; the body carries the mappings."""
    published = schema["paths"]["/api/user/v2/username_replacements/"]["post"]
    assert published["requestBody"]["required"] is True
    assert list(published["requestBody"]["content"]) == ["application/json"]
    assert published["requestBody"]["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/UsernameReplacementRequest"
    )
    assert published["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/UsernameReplacementOutcome"
    )


@skip_unless_lms
def test_the_published_mapping_takes_a_name_of_any_shape(schema):
    """
    A client building a request from the document builds one the endpoint carries out.

    A name is written as asked for and a null is reported as failed, so the
    name asked for carries no bound and may be null; a mapping still names
    exactly one account.
    """
    mappings = schema["components"]["schemas"]["UsernameReplacementRequest"]["properties"][
        "username_mappings"
    ]
    assert mappings["maxItems"] == REPLACEMENT_MAX_MAPPINGS
    assert {key: mappings["items"][key] for key in ("type", "minProperties", "maxProperties")} == {
        "type": "object", "minProperties": 1, "maxProperties": 1,
    }
    assert mappings["items"]["additionalProperties"] == {"type": "string", "nullable": True}
