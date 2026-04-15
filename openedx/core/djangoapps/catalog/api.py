"""
Python APIs exposed by the catalog app to other in-process apps.
"""

from .utils import course_run_keys_for_program as _course_run_keys_for_program
from .utils import get_course_run_details as _get_course_run_details
from .utils import get_programs as _get_programs
from .utils import get_programs_by_type_slug as _get_programs_by_type_slug


def get_programs_by_type(site, program_type_slug):
    """
    Retrieves a list of programs for the site whose type's slug matches the parameter.
    Slug is used is used as a consistent value to check since ProgramType.name is
    a field that gets translated.

    Params:
        site (Site): The corresponding Site object to fetch programs for.
        program_type_slug (string): The type slug that matching programs must have.

    Returns:
        A list of programs (dicts) for the given site with the given type slug
    """
    return _get_programs_by_type_slug(site, program_type_slug)


def get_programs_from_cache_by_uuid(uuids):
    """
    Retrieves the programs for the provided UUIDS. Relies on
    the Program cache, if it is not updated or data is missing the result
    will be missing data or empty.

    Params:
        uuids (list): A list of Program UUIDs to get Program data for from the cache.
    Returns:
        (list): list of dictionaries representing programs.
    """
    return _get_programs(uuids=uuids)


def get_course_run_key_for_program_from_cache(program):
    """
    Retrieves a list of Course Run Keys from the Program.

    Params:
        program (dict): A dictionary from the program cache containing the data for a program.
    Returns:
        (set): A set of Course Run Keys.
    """
    return _course_run_keys_for_program(parent_program=program)


def get_course_run_details(course_key, fields_list):
    """
    Retrieves course run details for a given course run key.

    Params:
        course_key (CourseKey): The identifier for the course.
        fields_list (List, string): A list of fields (as strings) of values we want returned.
    Returns:
        dict containing response from the Discovery API that includes the fields specified in `fields_list`
    """
    return _get_course_run_details(course_key, fields_list)
