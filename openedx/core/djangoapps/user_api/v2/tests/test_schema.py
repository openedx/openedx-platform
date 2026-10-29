"""Tests for what version 2 of the User API publishes in the OpenAPI schema."""

import json
from importlib import import_module
from unittest import mock

import jsonschema
import pytest
from django.conf import settings
from django.urls import reverse
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.settings import patched_settings
from waffle.testutils import override_switch

from common.djangoapps.student.models import PendingNameChange, UserProfile
from common.djangoapps.student.tests.factories import AccountRecoveryFactory, AdminFactory, UserFactory
from lms.djangoapps.verify_student.models import IDVerificationAttempt, VerificationAttempt
from lms.lib.spectacular import (
    RENAMED_OPERATION_IDS,
    SUPERSEDED_USER_PATHS,
    lms_mark_legacy_paths_deprecated,
)
from openedx.core.apidocs import get_api_docs_settings
from openedx.core.djangoapps.profile_images.tests.helpers import make_image_file
from openedx.core.djangoapps.site_configuration.tests.test_util import with_site_configuration_context
from openedx.core.djangoapps.user_api.accounts.image_helpers import (
    get_profile_image_names,
    get_profile_image_storage,
)
from openedx.core.djangoapps.user_api.accounts.utils import ENABLE_SECONDARY_EMAIL_FEATURE_SWITCH
from openedx.core.djangoapps.user_api.tests.factories import UserPreferenceFactory
from openedx.core.djangoapps.user_api.v2.serializers import (
    ACCOUNT_DEFAULT_SORT,
    ACCOUNT_MINIMAL_FIELDS,
    EXPIRATION_DAY_PATTERN,
    AccountQuerySerializer,
    TimeZoneQuerySerializer,
    VerificationQuerySerializer,
)
from openedx.core.djangoapps.user_api.v2.views import PREFERENCE_KEY_PATTERN_PARTIAL
from openedx.core.djangolib.testing.utils import skip_unless_lms

PASSWORD = "Password1234"

#: Stands for a property a response does not carry, where that is the assertion.
ABSENT = "absent"

ACCOUNT_LIST_PATH = "/api/user/v2/accounts/"
ACCOUNT_MEMBER_PATH = "/api/user/v2/accounts/{username}/"
ACCOUNT_DEACTIVATION_PATH = "/api/user/v2/accounts/{username}/deactivation/"
PROFILE_IMAGE_PATH = "/api/user/v2/accounts/{username}/profile_image/"
VERIFICATION_STATUS_PATH = "/api/user/v2/accounts/{username}/verification_status/"
VERIFICATION_LIST_PATH = "/api/user/v2/accounts/{username}/verifications/"
EMAIL_SEARCH_PATH = "/api/user/v2/email_searches/"
NAME_CHANGE_PATH = "/api/user/v2/name_changes/"
NAME_CHANGE_CONFIRMATION_PATH = "/api/user/v2/name_changes/{username}/confirmations/"

USER_MEMBER_PATH = "/api/user/v2/users/{username}/"
PREFERENCE_MEMBER_PATH = "/api/user/v2/user_preferences/{username},{key}/"
TIME_ZONE_LIST_PATH = "/api/user/v2/time_zones/"

#: The addresses that answer with a page of rows, and the ones that answer with
#: a single record.
LIST_PATHS = {
    ACCOUNT_LIST_PATH,
    VERIFICATION_LIST_PATH,
    "/api/user/v2/users/",
    "/api/user/v2/user_preferences/",
    TIME_ZONE_LIST_PATH,
}
MEMBER_PATHS = {
    ACCOUNT_MEMBER_PATH,
    VERIFICATION_STATUS_PATH,
    USER_MEMBER_PATH,
    PREFERENCE_MEMBER_PATH,
}

#: The addresses that act on a resource rather than answer with one.
ACTION_PATHS = {
    EMAIL_SEARCH_PATH,
    ACCOUNT_DEACTIVATION_PATH,
    PROFILE_IMAGE_PATH,
    NAME_CHANGE_PATH,
    NAME_CHANGE_CONFIRMATION_PATH,
}

#: The addresses a generated client library is built from.
SDK_PATHS = {
    ACCOUNT_LIST_PATH,
    ACCOUNT_MEMBER_PATH,
    VERIFICATION_STATUS_PATH,
    VERIFICATION_LIST_PATH,
} | ACTION_PATHS

VERSION_TWO_PATHS = LIST_PATHS | MEMBER_PATHS | ACTION_PATHS

PAGINATION_MEMBERS = {"count", "num_pages", "current_page", "start", "next", "previous", "results"}

#: Where this version is mounted in the service's URL configuration.
VERSION_TWO_MOUNT = "api/user/v2/"

#: The components this version contributes to the document.
VERSION_TWO_COMPONENTS = (
    "Account",
    "AccountExtendedField",
    "AccountLanguage",
    "AccountMinimal",
    "AccountProfileImage",
    "AccountProjection",
    "AccountResponse",
    "AccountSocialLink",
    "EmailSearch",
    "EmailSearchResult",
    "ErrorResponse",
    "NameChange",
    "PaginatedAccountResponseList",
    "PaginatedEmailSearchResultList",
    "PaginatedVerificationList",
    "PatchedAccountUpdate",
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
    "/api/user/v1/preferences/{pref_key}/users/": "/api/user/v2/users/",
    "/api/user/v1/forum_roles/{name}/users/": "/api/user/v2/users/",
    "/api/user/v1/preferences/time_zones/": "/api/user/v2/time_zones/",
}

#: Legacy enrollment addresses this schema already marked before the user API joined it.
SUPERSEDED_ENROLLMENT_PATHS = {
    "/api/enrollment/v2/course/{course_id}",
    "/api/enrollment/v2/enrollment/{course_id}",
    "/api/enrollment/v2/enrollment/{username},{course_id}",
    "/api/enrollment/v2/enrollments",
}

#: Enrollment operations whose ids must survive the widened path prefix.
ENROLLMENT_OPERATION_IDS = {
    "v1_enrollments_list",
    "v1_enrollment_create",
    "v2_enrollments_list",
    "v2_enrollments_list_legacy",
    "v2_enrollment_,_retrieve",
    "v2_roles_retrieve",
}


@pytest.fixture(name="schema")
@skip_unless_lms
def schema_fixture(db):  # pylint: disable=invalid-name, unused-argument
    """The schema published under the service's own drf-spectacular settings."""
    return SchemaGenerator().get_schema(request=None, public=True)


def service_wide_schema(patterns=None):
    """Return the document the service publishes for browsing its whole surface."""
    with patched_settings(get_api_docs_settings()):
        return SchemaGenerator(patterns=patterns).get_schema(request=None, public=True)


def patterns_without_this_version():
    """Return the service's URL configuration with this version's mount taken out."""
    configured = import_module(settings.ROOT_URLCONF).urlpatterns
    remaining = [entry for entry in configured if str(entry.pattern) != VERSION_TWO_MOUNT]
    assert len(remaining) == len(configured) - 1, "this version is not mounted where it is looked for"
    return remaining


def published_names(schema):
    """Return every component name and operation id a document carries."""
    return set(schema["components"]["schemas"]) | {
        operation["operationId"] for _path, _method, operation in operations(schema)
    }


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
        "v2_users_list": ["user"],
        "v2_users_retrieve": ["user"],
        "v2_user_preferences_list": ["user"],
        "v2_user_preferences_retrieve": ["user"],
        "v2_time_zones_list": ["user"],
    }


@skip_unless_lms
def test_enrollment_operation_ids_survive_the_widened_prefix(schema):
    published = {operation["operationId"] for _path, _method, operation in operations(schema)}
    assert ENROLLMENT_OPERATION_IDS <= published


@skip_unless_lms
def test_only_superseded_addresses_are_marked_deprecated(schema):
    deprecated = {
        path for path, _method, operation in operations(schema) if operation.get("deprecated")
    }
    assert deprecated == SUPERSEDED_ENROLLMENT_PATHS | set(SUPERSEDED_USER_ADDRESSES)


@skip_unless_lms
def test_only_this_version_and_the_addresses_it_supersedes_are_published(schema):
    """A user address this change neither serves nor replaces stays out of the document."""
    published = {path for path in schema["paths"] if path.startswith("/api/user/")}
    assert published == VERSION_TWO_PATHS | set(SUPERSEDED_USER_ADDRESSES)


@skip_unless_lms
def test_addresses_owned_by_other_apps_are_not_published(schema):
    """Neighbours of the mounts this change adds, including one under its own prefix."""
    for path in (
        "/api/user/v1/accounts/deactivate_logout/",
        "/api/user/v1/accounts/{username}/retirement_status/",
        "/api/user/v1/preferences/{username}",
        "/api/user/v1/account/registration/",
        "/api/user/v2/account/registration/",
        "/api/profile_images/v1/{username}/upload",
        "/api/profile_images/v1/{username}/remove",
    ):
        assert path not in schema["paths"], path


@skip_unless_lms
def test_pagination_envelope_is_published_in_full(schema):
    """Every paged body this version publishes carries the whole envelope, not DRF's four members."""
    paged = [name for name in VERSION_TWO_COMPONENTS if name.startswith("Paginated")]
    assert len(paged) == 6
    for component in paged:
        published = schema["components"]["schemas"][component]
        assert set(published["properties"]) == PAGINATION_MEMBERS, component


@pytest.mark.django_db
@skip_unless_lms
def test_published_pagination_envelope_matches_the_response_body(client, schema):
    admin = AdminFactory.create(username="schema-admin", password=PASSWORD)
    UserPreferenceFactory.create(user=admin, key="pref-lang", value="en")
    assert client.login(username=admin.username, password=PASSWORD)
    body = client.get(reverse("user_v2:user_preference_list")).json()
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
def test_enum_post_processing_runs_before_the_deprecation_hook():
    hooks = settings.SPECTACULAR_SETTINGS["POSTPROCESSING_HOOKS"]
    assert hooks[0] == "drf_spectacular.hooks.postprocess_schema_enums"
    assert "lms.lib.spectacular.lms_mark_legacy_paths_deprecated" in hooks[1:]


@skip_unless_lms
def test_hook_leaves_other_resources_alone():
    """The hook marks only the addresses it lists, even among their neighbours."""
    untouched = [
        "/api/profile_images/v1/{username}/upload",
        "/api/profile_images/v1/{username}/remove",
        "/api/user/v1/accounts/deactivate_logout/",
        "/api/user/v1/preferences/email_opt_in/",
        "/api/user/v2/account/registration/",
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
        "/api/enrollment/v2/enrollments",
    ]
    result = {"paths": {path: {"get": {"operationId": path}} for path in untouched + marked}}

    lms_mark_legacy_paths_deprecated(result, generator=None, request=None, public=True)

    for path in untouched:
        assert "deprecated" not in result["paths"][path]["get"], path
    for path in marked:
        assert result["paths"][path]["get"]["deprecated"] is True, path


@skip_unless_lms
def test_every_superseded_address_has_a_published_replacement(schema):
    assert set(SUPERSEDED_USER_ADDRESSES) == set(SUPERSEDED_USER_PATHS)
    for superseded, replacement in SUPERSEDED_USER_ADDRESSES.items():
        assert superseded in schema["paths"]
        assert replacement in schema["paths"]


#: The media types each superseded body is published in, by address and method.
SUPERSEDED_REQUEST_MEDIA_TYPES = {
    ("/api/user/v1/accounts/{username}", "patch"): {"application/merge-patch+json"},
    ("/api/user/v1/accounts/search_emails", "post"): {
        "application/json", "application/merge-patch+json",
    },
}


@pytest.mark.django_db
@skip_unless_lms
def test_each_superseded_body_is_published_in_the_media_types_that_address_reads(client, schema):
    """
    Every media type published for a superseded body is sent to that address here.

    Those bodies are named for addresses whose views name no serializer, and a
    shape named on its own is published in every media type the view's parsers
    accept, which can be more than the handler reads. A generated client sends
    the first type the document offers, so each one is answered by the running
    endpoint here, and a type the document leaves out is sent as well.
    """
    staff = AdminFactory.create(username="media_type_staff", password=PASSWORD)
    assert client.login(username=staff.username, password=PASSWORD)
    member = f"/api/user/v1/accounts/{staff.username}"
    requests = {
        ("/api/user/v1/accounts/{username}", "patch"): (member, "{}"),
        ("/api/user/v1/accounts/search_emails", "post"): (
            "/api/user/v1/accounts/search_emails", json.dumps({"emails": [staff.email]}),
        ),
    }
    for (path, method), media_types in SUPERSEDED_REQUEST_MEDIA_TYPES.items():
        assert set(schema["paths"][path][method]["requestBody"]["content"]) == media_types, path
        address, body = requests[(path, method)]
        for media_type in media_types:
            answer = getattr(client, method)(address, data=body, content_type=media_type)
            assert answer.status_code == 200, (address, media_type, answer.content)
    left_out = client.patch(member, data="{}", content_type="application/json")
    assert left_out.status_code == 415


@pytest.mark.django_db
@skip_unless_lms
def test_this_version_only_adds_to_the_service_wide_document():
    """
    Nothing published for browsing the whole service is lost when this version joins it.

    Enum post-processing runs over every operation the service publishes and
    renames both sides of a name collision, so a component this version never
    declared can be renamed out of the document, and a client generated from it
    loses the matching model. The document is therefore read with this version
    mounted and with its mount taken out, and the second has to be contained in
    the first.
    """
    without_this_version = published_names(service_wide_schema(patterns_without_this_version()))
    whole = service_wide_schema()
    missing = without_this_version - published_names(whole)
    assert missing == set(), sorted(missing)

    components = whole["components"]["schemas"]
    assert "user_api.User" in components
    assert {"UserDirectory", "UserPreferenceRecord", "TimeZone"} <= set(components)
    assert "CountryTimeZone" in components


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
def test_published_shapes_accept_every_body_the_endpoints_return(client, schema):
    """Every preset and selection the parameters offer returns a body the document describes."""
    admin = AdminFactory.create(username="shape-admin", password=PASSWORD)
    UserPreferenceFactory.create(user=admin, key="pref-lang", value="en")
    fill_in(admin)
    assert client.login(username=admin.username, password=PASSWORD)
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
        for path, _method, operation in operations(schema)
        if path in LIST_PATHS
    }
    assert documented == {
        ACCOUNT_LIST_PATH: ["200", "400", "401", "403", "404"],
        VERIFICATION_LIST_PATH: ["200", "400", "401", "403", "404"],
        "/api/user/v2/users/": ["200", "400", "401", "403", "404"],
        "/api/user/v2/user_preferences/": ["200", "400", "401", "403", "404"],
        TIME_ZONE_LIST_PATH: ["200", "400", "404"],
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
        "200", "400", "401", "403", "404", "415", "429", "500",
    ]
    assert schema["paths"][ACCOUNT_MEMBER_PATH]["patch"]["responses"]["500"]["description"] == (
        "The change was asked for correctly and could not be carried out. Sending it again "
        "with something else in it does not help; sending the same request later may."
    )
    assert sorted(schema["paths"][ACCOUNT_MEMBER_PATH]["get"]["responses"]) == [
        "200", "400", "401", "403", "404",
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
    assert sorted(operation["responses"]) == ["200", "400", "401", "403", "404", "415"]
    # The page is the only thing it reads from the query string.
    assert [parameter["name"] for parameter in operation["parameters"]] == ["page", "page_size"]


@skip_unless_lms
def test_a_deactivation_publishes_that_it_answers_with_nothing(schema):
    operation = schema["paths"][ACCOUNT_DEACTIVATION_PATH]["post"]
    assert "requestBody" not in operation
    assert sorted(operation["responses"]) == ["204", "400", "401", "403", "404"]
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
def test_each_renamed_operation_id_names_an_address_in_the_document_that_derives_it(schema):
    """
    A rename that outlived the address it was written for would name nothing.

    An entry is keyed by the id the generator derives, which differs between the
    document that trims the area off the front of each path and the one that
    trims nothing, so each entry applies to exactly one of them.
    """
    documents = {"this version": schema, "service-wide": service_wide_schema()}
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
    assert sorted(operation["responses"]) == ["201", "400", "401", "404", "415"]
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
    assert sorted(operation["responses"]) == ["204", "400", "401", "403", "404"]


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
    assert sorted(operation["responses"]) == ["204", "400", "401", "403", "404", "415"]


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
    assert sorted(operation["responses"]) == ["204", "400", "401", "403", "404"]
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
