"""Tests for what version 2 of the User API publishes in the OpenAPI schema."""

import json
from importlib import import_module

import jsonschema
import pytest
from django.conf import settings
from django.urls import reverse
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.settings import patched_settings

from common.djangoapps.student.tests.factories import AdminFactory
from lms.lib.spectacular import SUPERSEDED_USER_PATHS, lms_mark_legacy_paths_deprecated
from openedx.core.apidocs import get_api_docs_settings
from openedx.core.djangoapps.user_api.tests.factories import UserPreferenceFactory
from openedx.core.djangoapps.user_api.v2.serializers import TimeZoneQuerySerializer
from openedx.core.djangoapps.user_api.v2.views import PREFERENCE_KEY_PATTERN_PARTIAL
from openedx.core.djangolib.testing.utils import skip_unless_lms

PASSWORD = "Password1234"

USER_MEMBER_PATH = "/api/user/v2/users/{username}/"
PREFERENCE_MEMBER_PATH = "/api/user/v2/user_preferences/{username},{key}/"
TIME_ZONE_LIST_PATH = "/api/user/v2/time_zones/"

#: The addresses that answer with a page of rows, and the ones that answer with
#: a single record.
LIST_PATHS = {
    "/api/user/v2/users/",
    "/api/user/v2/user_preferences/",
    TIME_ZONE_LIST_PATH,
}
MEMBER_PATHS = {USER_MEMBER_PATH, PREFERENCE_MEMBER_PATH}

VERSION_TWO_PATHS = LIST_PATHS | MEMBER_PATHS

PAGINATION_MEMBERS = {"count", "num_pages", "current_page", "start", "next", "previous", "results"}

#: Where this version is mounted in the service's URL configuration.
VERSION_TWO_MOUNT = "api/user/v2/"

#: The components this version contributes to the document.
VERSION_TWO_COMPONENTS = (
    "ErrorResponse",
    "PaginatedTimeZoneList",
    "PaginatedUserDirectoryResponseList",
    "PaginatedUserPreferenceRecordResponseList",
    "TimeZone",
    "UserDirectory",
    "UserDirectoryMinimal",
    "UserDirectoryProjection",
    "UserDirectoryResponse",
    "UserPreferenceRecord",
    "UserPreferenceRecordMinimal",
    "UserPreferenceRecordProjection",
    "UserPreferenceRecordResponse",
)

#: Every body these endpoints return: the address and parameters that produce
#: it, the published path it is documented under, and the rows it carries.
SHAPE_CASES = (
    ("/api/user/v2/users/", "/api/user/v2/users/", {}),
    ("/api/user/v2/users/", "/api/user/v2/users/", {"view": "minimal"}),
    ("/api/user/v2/users/", "/api/user/v2/users/", {"fields": "username,email"}),
    ("/api/user/v2/users/", "/api/user/v2/users/", {"view": "minimal", "fields": "username"}),
    ("/api/user/v2/users/{username}/", "/api/user/v2/users/shape-admin/", {}),
    ("/api/user/v2/users/{username}/", "/api/user/v2/users/shape-admin/", {"view": "minimal"}),
    ("/api/user/v2/users/{username}/", "/api/user/v2/users/shape-admin/", {"fields": "id,url"}),
    ("/api/user/v2/user_preferences/", "/api/user/v2/user_preferences/", {}),
    ("/api/user/v2/user_preferences/", "/api/user/v2/user_preferences/", {"view": "full"}),
    ("/api/user/v2/user_preferences/", "/api/user/v2/user_preferences/", {"view": "minimal"}),
    ("/api/user/v2/user_preferences/", "/api/user/v2/user_preferences/", {"fields": "key,value"}),
    (
        "/api/user/v2/user_preferences/{username},{key}/",
        "/api/user/v2/user_preferences/shape-admin,pref-lang/",
        {},
    ),
    (
        "/api/user/v2/user_preferences/{username},{key}/",
        "/api/user/v2/user_preferences/shape-admin,pref-lang/",
        {"view": "full"},
    ),
    (
        "/api/user/v2/user_preferences/{username},{key}/",
        "/api/user/v2/user_preferences/shape-admin,pref-lang/",
        {"view": "minimal"},
    ),
    (
        "/api/user/v2/user_preferences/{username},{key}/",
        "/api/user/v2/user_preferences/shape-admin,pref-lang/",
        {"fields": "value"},
    ),
    ("/api/user/v2/time_zones/", "/api/user/v2/time_zones/", {"country_code": "FR"}),
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


def published_body_schema(schema, path):
    """Return what a successful body of ``path`` must satisfy, components included."""
    body = schema["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    return as_json_schema({**body, "components": schema["components"]})

#: The version 1 addresses this change supersedes, written out independently of
#: the constant the hook reads so that changing either one shows up here.
SUPERSEDED_USER_ADDRESSES = {
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
def test_version_two_operations_carry_the_area_tag(schema):
    tags = {
        operation["operationId"]: operation.get("tags")
        for path, _method, operation in operations(schema)
        if path in VERSION_TWO_PATHS
    }
    assert tags == {
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
        "/api/user/v1/accounts",
        "/api/user/v1/me",
        "/api/user/v1/preferences/{username}",
        "/api/user/v1/account/registration/",
        "/api/user/v2/account/registration/",
    ):
        assert path not in schema["paths"], path


@skip_unless_lms
def test_pagination_envelope_is_published_in_full(schema):
    for component in (
        "PaginatedUserDirectoryResponseList",
        "PaginatedUserPreferenceRecordResponseList",
        "PaginatedTimeZoneList",
    ):
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
    for path, method, operation in operations(schema):
        if path not in VERSION_TWO_PATHS:
            continue
        for code, response in operation["responses"].items():
            if not code.startswith("4"):
                continue
            schema_ref = response["content"]["application/json"]["schema"]
            assert schema_ref == {"$ref": "#/components/schemas/ErrorResponse"}, (path, method, code)


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
        "/api/user/v1/accounts",
        "/api/user/v1/me",
        "/api/user/v1/preferences/email_opt_in/",
        "/api/user/v2/account/registration/",
        "/user_api/v1/users/",
        "/user_api/v1/preferences/time_zones/",
        "/api/enrollment/v2/enrollments/",
        "/api/courses/v1/courses/",
    ]
    marked = ["/api/user/v1/users/", "/api/enrollment/v2/enrollments"]
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


@pytest.mark.django_db
@skip_unless_lms
def test_published_shapes_accept_every_body_the_endpoints_return(client, schema):
    """Every preset and selection the parameters offer returns a body the document describes."""
    admin = AdminFactory.create(username="shape-admin", password=PASSWORD)
    UserPreferenceFactory.create(user=admin, key="pref-lang", value="en")
    assert client.login(username=admin.username, password=PASSWORD)
    for published_path, address, params in SHAPE_CASES:
        case = (address, params)
        response = client.get(address, params)
        assert response.status_code == 200, case
        body = response.json()
        if "results" in body:
            assert body["count"] == 1, case
        validator = jsonschema.Draft202012Validator(published_body_schema(schema, published_path))
        assert [error.message for error in validator.iter_errors(body)] == [], case


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
    assert LIST_PATHS | MEMBER_PATHS == set(VERSION_TWO_PATHS)
    documented = {
        path: sorted(operation["responses"])
        for path, _method, operation in operations(schema)
        if path in LIST_PATHS
    }
    assert documented == {
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
