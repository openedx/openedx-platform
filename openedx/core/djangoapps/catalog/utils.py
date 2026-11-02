"""Helper functions for working with the catalog service."""

import copy
import datetime
import logging
import uuid
from typing import TYPE_CHECKING, Any, List, Union
from zoneinfo import ZoneInfo

import pycountry
import requests
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from edx_rest_api_client.auth import SuppliedJwtAuth
from edx_rest_api_client.client import USER_AGENT
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.entitlements.utils import is_course_run_entitlement_fulfillable
from common.djangoapps.student.models import CourseEnrollment
from openedx.core.djangoapps.catalog.cache import (
    CATALOG_COURSE_PROGRAMS_CACHE_KEY_TPL,
    COURSE_PROGRAMS_CACHE_KEY_TPL,
    PATHWAY_CACHE_KEY_TPL,
    PROGRAM_CACHE_KEY_TPL,
    PROGRAMS_BY_ORGANIZATION_CACHE_KEY_TPL,
    PROGRAMS_BY_TYPE_CACHE_KEY_TPL,
    PROGRAMS_BY_TYPE_SLUG_CACHE_KEY_TPL,
    SITE_PATHWAY_IDS_CACHE_KEY_TPL,
    SITE_PROGRAM_UUIDS_CACHE_KEY_TPL,
)
from openedx.core.djangoapps.catalog.models import CatalogIntegration
from openedx.core.djangoapps.oauth_dispatch.jwt import create_jwt_for_user
from openedx.core.lib.edx_api_utils import get_api_data

if TYPE_CHECKING:
    from django.contrib.sites.models import Site

logger = logging.getLogger(__name__)

missing_details_msg_tpl = "Failed to get details for program {uuid} from the cache."


def get_catalog_api_base_url(site=None):
    """
    Returns a base API url used to make Catalog API requests.
    """
    if site:
        return site.configuration.get_value("COURSE_CATALOG_API_URL")

    return CatalogIntegration.current().get_internal_api_url()


def get_catalog_api_client(user):
    """
    Returns an API client which can be used to make Catalog API requests.
    """
    jwt = create_jwt_for_user(user)
    client = requests.Session()
    client.headers.update({"User-Agent": USER_AGENT})
    client.auth = SuppliedJwtAuth(jwt)

    return client


def check_catalog_integration_and_get_user(error_message_field):
    """
    Checks that catalog integration is enabled, and if so, attempts to get and
    return the service user.

    Parameters:
        error_message_field (str): The field that will be attempted to be
            retrieved when calling the api client. Used for the error message.

    Returns:
        Tuple of:
            The catalog integration service user if it exists, else None
            The catalog integration Object
                Note: (This is necessary for future calls of functions using this method)
    """
    catalog_integration = CatalogIntegration.current()

    if catalog_integration.is_enabled():
        try:
            user = catalog_integration.get_service_user()
        except ObjectDoesNotExist:
            logger.error(
                "Catalog service user with username [{username}] does not exist. "
                "{field} will not be retrieved.".format(
                    username=catalog_integration.service_username,
                    field=error_message_field,
                )
            )
            return None, catalog_integration
        return user, catalog_integration
    else:
        logger.error(
            "Unable to retrieve details about {field} because Catalog Integration is not enabled".format(
                field=error_message_field,
            )
        )
        return None, catalog_integration


# pylint: disable=redefined-outer-name
def get_programs(
    site: "Site" = None,
    uuid: str = None,
    uuids: List[str] = None,
    course: str = None,
    catalog_course_uuid: str = None,
    organization: str = None,
) -> Union[str, List[str]]:
    """Read programs from the cache.

    The cache is populated by a management command, cache_programs.

    Keyword Arguments:
        site (Site): django.contrib.sites.models object to fetch programs of.
        uuid (string): UUID identifying a specific program to read from the cache.
        uuids (list of string): UUIDs identifying a specific programs to read from the cache.
        course (string): course run id identifying a specific course run to read from the cache.
        catalog_course_uuid (string): Catalog Course UUID
        organization (string): short name for specific organization to read from the cache.

    Returns:
        list of str, representing programs.
        dict, if a specific program is requested.
    """
    if len([arg for arg in (site, uuid, uuids, course, catalog_course_uuid, organization) if arg is not None]) != 1:
        raise TypeError("get_programs takes exactly one argument")

    if uuid:
        program = cache.get(PROGRAM_CACHE_KEY_TPL.format(uuid=uuid))
        if not program:
            logger.warning(missing_details_msg_tpl.format(uuid=uuid))

        return program
    elif course:
        uuids = cache.get(COURSE_PROGRAMS_CACHE_KEY_TPL.format(course_run_id=course))
        if not uuids:
            # Currently, the cache does not differentiate between a cache miss and a course
            # without programs. After this is changed, log any cache misses here.
            return []
    elif catalog_course_uuid:
        uuids = cache.get(CATALOG_COURSE_PROGRAMS_CACHE_KEY_TPL.format(course_uuid=catalog_course_uuid))
        if not uuids:
            # Currently, the cache does not differentiate between a cache miss and a course
            # without programs. After this is changed, log any cache misses here.
            return []
    elif site:
        site_config = getattr(site, "configuration", None)
        catalog_url = site_config.get_value("COURSE_CATALOG_API_URL") if site_config else None
        if site_config and catalog_url:
            uuids = cache.get(SITE_PROGRAM_UUIDS_CACHE_KEY_TPL.format(domain=site.domain), [])
            if not uuids:
                logger.warning(f"Failed to get program UUIDs from the cache for site {site.domain}.")
        else:
            uuids = []
    elif organization:
        uuids = get_programs_for_organization(organization)
        if not uuids:
            return []

    return get_programs_by_uuids(uuids)


def get_programs_by_type(site, program_type):
    """
    Keyword Arguments:
        site (Site): The corresponding Site object to fetch programs for.
        program_type (string): The program_type that matching programs must have.

    Returns:
        A list of programs for the given site with the given program_type.
    """
    program_type_cache_key = PROGRAMS_BY_TYPE_CACHE_KEY_TPL.format(
        site_id=site.id, program_type=normalize_program_type(program_type)
    )
    uuids = cache.get(program_type_cache_key, [])
    if not uuids:
        logger.warning(str(f"Failed to get program UUIDs from cache for site {site.id} and type {program_type}"))
    return get_programs_by_uuids(uuids)


def get_programs_by_type_slug(site, program_type_slug):
    """
    Keyword Arguments:
        site (Site): The corresponding Site object to fetch programs for.
        program_type_slug (string): The type slug that matching programs must have.

    Returns:
        A list of programs for the given site with the given type slug.

    Slugs are a consistent identifier whereas type (used in `get_programs_by_type`)
    may be translated.
    """
    program_type_slug_cache_key = PROGRAMS_BY_TYPE_SLUG_CACHE_KEY_TPL.format(
        site_id=site.id, program_slug=program_type_slug
    )
    uuids = cache.get(program_type_slug_cache_key, [])
    if not uuids:
        logger.warning(
            str(f"Failed to get program UUIDs from cache for site {site.id} and type slug {program_type_slug}")
        )
    return get_programs_by_uuids(uuids)


def get_programs_by_uuids(uuids: List[Any]) -> List[str]:
    """
    Gets a list of programs for the provided uuids
    """
    # a list of UUID objects would be a perfectly reasonable parameter to provide
    uuid_strings = [str(handle) for handle in uuids]

    programs = cache.get_many([PROGRAM_CACHE_KEY_TPL.format(uuid=handle) for handle in uuid_strings])
    programs = list(programs.values())

    # The get_many above sometimes fails to bring back details cached on one or
    # more Memcached nodes. It doesn't look like these keys are being evicted.
    # 99% of the time all keys come back, but 1% of the time all the keys stored
    # on one or more nodes are missing from the result of the get_many. One
    # get_many may fail to bring these keys back, but a get_many occurring
    # immediately afterwards will succeed in bringing back all the keys. This
    # behavior can be mitigated by trying again for the missing keys, which is
    # what we do here. Splitting the get_many into smaller chunks may also help.
    missing_uuids = set(uuid_strings) - {program["uuid"] for program in programs}
    if missing_uuids:
        logger.info(f"Failed to get details for {len(missing_uuids)} programs. Retrying.")

        retried_programs = cache.get_many([PROGRAM_CACHE_KEY_TPL.format(uuid=uuid) for uuid in missing_uuids])
        programs += list(retried_programs.values())

        still_missing_uuids = set(uuid_strings) - {program["uuid"] for program in programs}
        for uuid in still_missing_uuids:
            logger.warning(missing_details_msg_tpl.format(uuid=uuid))

    return programs


def get_program_types(name=None):
    """Retrieve program types from the catalog service.

    Keyword Arguments:
        name (string): Name identifying a specific program type.

    Returns:
        list of dict, representing program types.
        dict, if a specific program type is requested.
    """
    user, catalog_integration = check_catalog_integration_and_get_user(error_message_field="Program types")
    if user:
        cache_key = f"{catalog_integration.CACHE_KEY}.program_types"

        data = get_api_data(
            catalog_integration,
            "program_types",
            api_client=get_catalog_api_client(user),
            base_api_url=get_catalog_api_base_url(),
            cache_key=cache_key if catalog_integration.is_cache_enabled else None,
        )

        # Filter by name if a name was provided
        if name:
            data = next(program_type for program_type in data if program_type["name"] == name)

        return data
    else:
        return []


def get_pathways(site, pathway_id=None):
    """
    Read pathways from the cache.
    The cache is populated by a management command, cache_programs.

    Arguments:
        site (Site): django.contrib.sites.models object

    Keyword Arguments:
        pathway_id (string): id identifying a specific pathway to read from the cache.

    Returns:
        list of dict, representing pathways.
        dict, if a specific pathway is requested.
    """
    missing_details_msg_tpl = "Failed to get details for credit pathway {id} from the cache."

    if pathway_id:
        pathway = cache.get(PATHWAY_CACHE_KEY_TPL.format(id=pathway_id))
        if not pathway:
            logger.warning(missing_details_msg_tpl.format(id=pathway_id))

        return pathway
    pathway_ids = cache.get(SITE_PATHWAY_IDS_CACHE_KEY_TPL.format(domain=site.domain), [])
    if not pathway_ids:
        logger.warning("Failed to get credit pathway ids from the cache.")

    pathways = cache.get_many([PATHWAY_CACHE_KEY_TPL.format(id=pathway_id) for pathway_id in pathway_ids])
    pathways = list(pathways.values())

    # The get_many above sometimes fails to bring back details cached on one or
    # more Memcached nodes. It doesn't look like these keys are being evicted.
    # 99% of the time all keys come back, but 1% of the time all the keys stored
    # on one or more nodes are missing from the result of the get_many. One
    # get_many may fail to bring these keys back, but a get_many occurring
    # immediately afterwards will succeed in bringing back all the keys. This
    # behavior can be mitigated by trying again for the missing keys, which is
    # what we do here. Splitting the get_many into smaller chunks may also help.
    missing_ids = set(pathway_ids) - {pathway["id"] for pathway in pathways}
    if missing_ids:
        logger.info(f"Failed to get details for {len(missing_ids)} pathways. Retrying.")

        retried_pathways = cache.get_many([PATHWAY_CACHE_KEY_TPL.format(id=pathway_id) for pathway_id in missing_ids])
        pathways += list(retried_pathways.values())

        still_missing_ids = set(pathway_ids) - {pathway["id"] for pathway in pathways}
        for missing_id in still_missing_ids:
            logger.warning(missing_details_msg_tpl.format(id=missing_id))

    return pathways


def get_currency_data():
    """Retrieve currency data from the catalog service.

    Returns:
        list of dict, representing program types.
        dict, if a specific program type is requested.
    """
    user, catalog_integration = check_catalog_integration_and_get_user(error_message_field="Currency data")
    if user:
        cache_key = f"{catalog_integration.CACHE_KEY}.currency"

        return get_api_data(
            catalog_integration,
            "currency",
            api_client=get_catalog_api_client(user),
            base_api_url=get_catalog_api_base_url(),
            traverse_pagination=False,
            cache_key=cache_key if catalog_integration.is_cache_enabled else None,
        )
    else:
        return []


def format_price(price, symbol="$", code="USD"):
    """
    Format the price to have the appropriate currency and digits..

    :param price: The price amount.
    :param symbol: The symbol for the price (default: $)
    :param code: The currency code to be appended to the price (default: USD)
    :return: A formatted price string, i.e. '$10 USD', '$10.52 USD'.
    """
    if int(price) == price:
        return f"{symbol}{int(price)} {code}"
    return f"{symbol}{price:0.2f} {code}"


def get_localized_price_text(price, request):
    """
    Returns the localized converted price as string (ex. '$150 USD')

    If the users location has been added to the request, this will return the given price based on conversion rate
    from the Catalog service and return a localized string otherwise will return the default price in USD
    """
    user_currency = {"symbol": "$", "rate": 1, "code": "USD"}

    # session.country_code is added via CountryMiddleware in the LMS
    user_location = getattr(request, "session", {}).get("country_code")

    # Override default user_currency if location is available
    if user_location and get_currency_data:
        currency_data = get_currency_data()
        user_country = pycountry.countries.get(alpha_2=user_location)
        user_currency = currency_data.get(user_country.alpha_3, user_currency)

    return format_price(
        price=(price * user_currency["rate"]), symbol=user_currency["symbol"], code=user_currency["code"]
    )


def get_programs_with_type(site, include_hidden=True):
    """
    Return the list of programs. You can filter the types of programs returned by using the optional
    include_hidden parameter. By default hidden programs will be included.

    The program dict is updated with the fully serialized program type.

    Arguments:
        site (Site): django.contrib.sites.models object

    Keyword Arguments:
        include_hidden (bool): whether to include hidden programs

    Return:
        list of dict, representing the active programs.
    """
    programs_with_type = []
    programs = get_programs(site)

    if programs:
        program_types = {program_type["name"]: program_type for program_type in get_program_types()}
        for program in programs:
            if program["type"] not in program_types:
                continue

            if program["hidden"] and not include_hidden:
                continue

            # deepcopy the program dict here so we are not adding
            # the type to the cached object
            program_with_type = copy.deepcopy(program)
            program_with_type["type"] = program_types[program["type"]]
            programs_with_type.append(program_with_type)

    return programs_with_type


def get_course_runs():
    """
    Retrieve all the course runs from the catalog service.

    Returns:
        list of dict with each record representing a course run.
    """
    course_runs = []
    user, catalog_integration = check_catalog_integration_and_get_user(error_message_field="Course runs")
    if user:
        querystring = {
            "page_size": catalog_integration.page_size,
            "exclude_utm": 1,
        }

        course_runs = get_api_data(
            catalog_integration,
            "course_runs",
            api_client=get_catalog_api_client(user),
            base_api_url=get_catalog_api_base_url(),
            querystring=querystring,
        )

    return course_runs


def get_course_runs_for_course(course_uuid):  # lint-amnesty, pylint: disable=missing-function-docstring
    if course_uuid is None:
        raise ValueError("missing course_uuid")
    user, catalog_integration = check_catalog_integration_and_get_user(error_message_field="Course runs")
    if user:
        cache_key = f"{catalog_integration.CACHE_KEY}.course.{course_uuid}.course_runs"

        data = get_api_data(
            catalog_integration,
            "courses",
            resource_id=course_uuid,
            api_client=get_catalog_api_client(user),
            base_api_url=get_catalog_api_base_url(),
            cache_key=cache_key if catalog_integration.is_cache_enabled else None,
            long_term_cache=True,
            many=False,
        )
        return data.get("course_runs", [])
    else:
        return []


def get_owners_for_course(course_uuid):
    """
    Retrieves the course owner given a course uuid.

    Arguments
        course_uuid (string): Course UUID
    """
    if course_uuid is None:
        return []

    user, catalog_integration = check_catalog_integration_and_get_user(error_message_field="Owners")
    if user:
        cache_key = f"{catalog_integration.CACHE_KEY}.course.{course_uuid}.course_runs"

        data = get_api_data(
            catalog_integration,
            "courses",
            resource_id=course_uuid,
            api_client=get_catalog_api_client(user),
            base_api_url=get_catalog_api_base_url(),
            cache_key=cache_key if catalog_integration.is_cache_enabled else None,
            traverse_pagination=False,
            long_term_cache=True,
            many=False,
        )
        return data.get("owners", [])

    return []


def get_course_uuid_for_course(course_run_key):
    """
    Retrieve the Course UUID for a given course key

    Arguments:
        course_run_key (CourseKey): A Key for a Course run that will be pulled apart to get just the information
        required for a Course (e.g. org+course)

    Returns:
        UUID: Course UUID and None if it was not retrieved.
    """
    if course_run_key is None:
        return None

    user, catalog_integration = check_catalog_integration_and_get_user(error_message_field="Course UUID")
    if user:
        api_client = get_catalog_api_client(user)
        base_api_url = get_catalog_api_base_url()

        run_cache_key = f"{catalog_integration.CACHE_KEY}.course_run.{course_run_key}"

        course_run_data = get_api_data(
            catalog_integration,
            "course_runs",
            resource_id=str(course_run_key),
            api_client=api_client,
            base_api_url=base_api_url,
            cache_key=run_cache_key if catalog_integration.is_cache_enabled else None,
            long_term_cache=True,
            many=False,
            traverse_pagination=False,
        )

        course_key_str = course_run_data.get("course", None)

        if course_key_str:
            run_cache_key = f"{catalog_integration.CACHE_KEY}.course.{course_key_str}"

            data = get_api_data(
                catalog_integration,
                "courses",
                resource_id=course_key_str,
                api_client=api_client,
                base_api_url=base_api_url,
                cache_key=run_cache_key if catalog_integration.is_cache_enabled else None,
                long_term_cache=True,
                many=False,
                traverse_pagination=False,
            )
            uuid_str = data.get("uuid", None)
            if uuid_str:
                return uuid.UUID(uuid_str)
    return None


def get_pseudo_session_for_entitlement(entitlement):
    """
    This function is used to pass pseudo-data to the front end, returning a single session, regardless of whether that
    session is currently available.

    First tries to return the first available session. If there are no available sessions, will try
    to return the most recent session regardless of availability.
    Returns None if there are no sessions for that course.
    """
    sessions_for_course = get_course_runs_for_course(entitlement.course_uuid)
    available_sessions = get_fulfillable_course_runs_for_entitlement(entitlement, sessions_for_course)
    if available_sessions:
        return available_sessions[0]
    if sessions_for_course:
        return sessions_for_course[-1]
    return None


def get_visible_sessions_for_entitlement(entitlement):
    """
    Takes an entitlement object and returns the course runs that a user can currently enroll in.
    """
    sessions_for_course = get_course_runs_for_course(entitlement.course_uuid)
    return get_fulfillable_course_runs_for_entitlement(entitlement, sessions_for_course)


def get_fulfillable_course_runs_for_entitlement(entitlement, course_runs):
    """
    Looks through the list of course runs and returns the course runs that can
    be applied to the entitlement.

    Args:
        entitlement (CourseEntitlement): The CourseEntitlement to which a
        course run is to be applied.
        course_runs (list): List of course run that we would like to apply
        to the entitlement.

    Return:
        list: A list of sessions that a user can apply to the provided entitlement.
    """
    enrollable_sessions = []

    # Only retrieve list of published course runs that can still be enrolled and upgraded
    search_time = datetime.datetime.now(ZoneInfo("UTC"))
    for course_run in course_runs:
        course_id = CourseKey.from_string(course_run.get("key"))
        (user_enrollment_mode, is_active) = CourseEnrollment.enrollment_mode_for_user(
            user=entitlement.user, course_id=course_id
        )
        is_enrolled_in_mode = is_active and (user_enrollment_mode == entitlement.mode)
        if (
            is_enrolled_in_mode
            and entitlement.enrollment_course_run
            and course_id == entitlement.enrollment_course_run.course_id
        ):
            # User is enrolled in the course so we should include it in the list of enrollable sessions always
            # this will ensure it is available for the UI
            enrollable_sessions.append(course_run)
        elif not is_enrolled_in_mode and is_course_run_entitlement_fulfillable(course_id, entitlement, search_time):
            enrollable_sessions.append(course_run)

    enrollable_sessions.sort(key=lambda session: session.get("start"))
    return enrollable_sessions


def get_course_run_details(course_run_key, fields):
    """
    Retrieve information about the course run with the given id

    Arguments:
        course_run_key: key for the course_run about which we are retrieving information

    Returns:
        dict with language, start date, end date, and max_effort details about specified course run
    """
    course_run_details = {}
    if course_run_key is None:
        return course_run_details

    user, catalog_integration = check_catalog_integration_and_get_user(
        error_message_field=f"Data for course_run {course_run_key}"
    )
    if user:
        cache_key = f"{catalog_integration.CACHE_KEY}.course_runs"

        course_run_details = get_api_data(
            catalog_integration,
            "course_runs",
            api_client=get_catalog_api_client(user),
            base_api_url=get_catalog_api_base_url(),
            resource_id=course_run_key,
            cache_key=cache_key,
            many=False,
            traverse_pagination=False,
            fields=fields,
        )
    return course_run_details


def is_course_run_in_program(course_run_key, program):
    """
    Check if a course run is part of a program.

    Arguments:
        program (dict): program data, as returned by get_programs()
        course_run_key (CourseKey|str)

    Returns: bool
        Whether the program exists AND the course run is part of it.
    """
    # Right now, this function simply loads all the program data from the cache,
    # walks the structure to collect the set of course run keys,
    # and then sees if `course_run_key` is in that set.
    # If we need to optimize this later, we can.
    course_run_key_str = str(course_run_key) if isinstance(course_run_key, CourseKey) else course_run_key
    course_run_keys = course_run_keys_for_program(program)
    return course_run_key_str in course_run_keys


def course_run_keys_for_program(parent_program):
    """
    All of the course run keys associated with this ``parent_program``, either
    via its ``curriculum`` field (looking at both the curriculum's courses
    and child programs), or through the many-to-many ``courses`` field on the program.
    """
    keys = set()
    for program in [parent_program] + child_programs(parent_program):
        curriculum = _primary_active_curriculum(program)
        if curriculum:
            keys.update(_course_runs_from_container(curriculum))
        keys.update(_course_runs_from_container(program))
    return keys


def course_uuids_for_program(parent_program):
    """
    All of the course uuids associated with this ``parent_program``, either
    via its ``curriculum`` field (looking at both the curriculum's courses
    and child programs), or through the many-to-many ``courses`` field on the program.
    """
    uuids = set()
    for program in [parent_program] + child_programs(parent_program):
        curriculum = _primary_active_curriculum(program)
        if curriculum:
            uuids.update(_courses_from_container(curriculum))
        uuids.update(_courses_from_container(program))
    return uuids


def child_programs(program):
    """
    Given a program, recursively find all child programs related
    to this program through its curricula.
    """
    curriculum = _primary_active_curriculum(program)
    if not curriculum:
        return []
    result = []
    for child in curriculum.get("programs", []):
        result.append(child)
        result.extend(child_programs(child))
    return result


def _primary_active_curriculum(program):
    """
    Returns the first active curriculum in the given program, or None.
    """
    try:
        return next(c for c in program.get("curricula", []) if c.get("is_active"))
    except StopIteration:
        return


def _course_runs_from_container(container):
    """
    Pluck nested course runs out of a ``container`` dictionary,
    which is either the ``curriculum`` field of a program, or
    a program itself (since either may contain a ``courses`` list).
    """
    return [
        course_run.get("key") for course in container.get("courses", []) for course_run in course.get("course_runs", [])
    ]


def _courses_from_container(container):
    """
    Pluck nested courses out of a ``container`` dictionary,
    which is either the ``curriculum`` field of a program, or
    a program itself (since either may contain a ``courses`` list).
    """
    return [course.get("uuid") for course in container.get("courses", [])]


def normalize_program_type(program_type):
    """Function that normalizes a program type string for use in a cache key."""
    return str(program_type).lower()


def get_programs_for_organization(organization):
    """
    Retrieve list of program uuids authored by a given organization
    """
    return cache.get(PROGRAMS_BY_ORGANIZATION_CACHE_KEY_TPL.format(org_key=organization))


def get_course_data(course_key_str, fields, querystring=None):
    """
    Retrieve information about the course with the given course key.

    Arguments:
        course_key_str: key for the course about which we are retrieving information.
        fields (List, string): The given fields that you want to retrieve from API response.
        querystring (dict): Optional query string parameters.

    Returns:
        dict with details about specified course.
    """
    user, catalog_integration = check_catalog_integration_and_get_user(error_message_field="Course UUID")
    if user:
        api_client = get_catalog_api_client(user)
        base_api_url = get_catalog_api_base_url()
        if course_key_str:
            course_cache_key = f"{catalog_integration.CACHE_KEY}.course.{course_key_str}"
            data = get_api_data(
                catalog_integration,
                "courses",
                resource_id=course_key_str,
                api_client=api_client,
                base_api_url=base_api_url,
                cache_key=course_cache_key if catalog_integration.is_cache_enabled else None,
                long_term_cache=True,
                many=False,
                fields=fields,
                querystring=querystring,
            )
            if data:
                return data


def get_course_run_data(course_run_id, fields):
    """
    Retrieve information about the course run with the given course run id.

    Arguments:
        course_run_id: key for the course run about which we are retrieving information.
        fields (List, string): The given fields that you want to retrieve from API response.

    Returns:
        dict with details about specified course run.
    """
    user, catalog_integration = check_catalog_integration_and_get_user(error_message_field="Course Run ID")
    if user:
        api_client = get_catalog_api_client(user)
        base_api_url = get_catalog_api_base_url()
        if course_run_id:
            course_run_cache_key = f"{catalog_integration.CACHE_KEY}.course_run.{course_run_id}"
            data = get_api_data(
                catalog_integration,
                "course_runs",
                resource_id=course_run_id,
                api_client=api_client,
                base_api_url=base_api_url,
                cache_key=course_run_cache_key if catalog_integration.is_cache_enabled else None,
                long_term_cache=True,
                many=False,
                fields=fields,
            )
            if data:
                return data
