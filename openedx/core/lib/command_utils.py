"""
Useful utilities for management commands.
"""


from django.core.management.base import CommandError
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey


def get_mutually_exclusive_required_option(options, *selections):
    """
    Validates that exactly one of the 2 given options is specified.
    Returns the name of the found option.
    """

    selected = [sel for sel in selections if options.get(sel)]
    if len(selected) != 1:
        selection_string = ', '.join(f'--{selection}' for selection in selections)

        raise CommandError(f'Must specify exactly one of {selection_string}')
    return selected[0]


def validate_mutually_exclusive_option(options, option_1, option_2):
    """
    Validates that both of the 2 given options are not specified.
    """
    if options.get(option_1) and options.get(option_2):
        raise CommandError(f'Both --{option_1} and --{option_2} cannot be specified.')


def validate_dependent_option(options, dependent_option, depending_on_option):
    """
    Validates that option_1 is specified if dependent_option is specified.
    """
    if options.get(dependent_option) and not options.get(depending_on_option):
        raise CommandError(f'Option --{dependent_option} requires option --{depending_on_option}.')


def parse_course_keys(course_key_strings):
    """
    Parses and returns a list of CourseKey objects from the given
    list of course key strings.
    """
    try:
        return [CourseKey.from_string(course_key_string) for course_key_string in course_key_strings]
    except InvalidKeyError as error:
        raise CommandError(f'Invalid key specified: {str(error)}')  # pylint: disable=raise-missing-from  # noqa: B904
