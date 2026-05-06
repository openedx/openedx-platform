"""
Utilities for string manipulation.
"""


def str_to_bool(str):  # pylint: disable=redefined-builtin
    """
    Converts "true" (case-insensitive) to the boolean True.
    Everything else will return False (including None).

    An error will be thrown for non-string input (besides None).
    """
    return False if str is None else str.lower() == "true"


def _has_non_ascii_characters(data_string):
    """
    Check if provided string contains non ascii characters

    :param data_string: str or unicode object
    """
    try:
        data_string.encode('ascii')
    except UnicodeEncodeError:
        return True

    return False
