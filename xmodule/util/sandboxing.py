"""Utilities for managing course code libraries and sandbox execution."""

import re

from django.conf import settings
from opaque_keys.edx.keys import CourseKey, LearningContextKey

DEFAULT_PYTHON_LIB_FILENAME = "python_lib.zip"


def course_code_library_asset_name():
    """
    Return the asset name to use for course code libraries, defaulting to python_lib.zip.
    """
    return getattr(settings, "PYTHON_LIB_FILENAME", DEFAULT_PYTHON_LIB_FILENAME)


def can_execute_unsafe_code(course_id):
    """
    Determine if this course is allowed to run unsafe code.

    For use from the ModuleStore.  Checks the `course_id` against a list of whitelisted
    regexes.

    Returns a boolean, true if the course can run outside the sandbox.

    """
    # To decide if we can run unsafe code, we check the course id against
    # a list of regexes configured on the server.
    # If this is not defined in the environment variables then default to the most restrictive, which
    # is 'no unsafe courses'
    # TODO: This should be a database configuration, where we can mark individual courses as being
    # safe/unsafe. Someone in the future should switch us over to that rather than using regexes
    # in a settings file
    # To others using this: the code as-is is brittle and likely to be changed in the future,
    # as per the TODO, so please consider carefully before adding more values to COURSES_WITH_UNSAFE_CODE
    for regex in getattr(settings, "COURSES_WITH_UNSAFE_CODE", []):
        if re.match(regex, str(course_id)):
            return True
    return False


def get_python_lib_zip(contentstore, context_key: LearningContextKey):
    """Return the bytes of the course code library file, if it exists."""
    if not isinstance(context_key, CourseKey):
        # Although Content Libraries V2 does support python-evaluated capa problems,
        # it doesn't yet support supplementary python zip files.
        return None
    python_lib_filename = course_code_library_asset_name()
    asset_key = context_key.make_asset_key("asset", python_lib_filename)
    zip_lib = contentstore().find(asset_key, throw_on_not_found=False)
    if zip_lib is not None:
        return zip_lib.data

    return None


class SandboxService:
    """
    A service which provides utilities for executing sandboxed Python code, for example, inside custom Python questions.

    Args:
        contentstore(function): function which creates an instance of xmodule.content.ContentStore
        course_id(string or CourseLocator): identifier for the course
    """

    def __init__(self, contentstore, course_id, **kwargs):
        super().__init__(**kwargs)
        self.contentstore = contentstore
        self.course_id = course_id

    def can_execute_unsafe_code(self):
        """
        Returns a boolean, true if the course can run outside the sandbox.
        """
        return can_execute_unsafe_code(self.course_id)

    def get_python_lib_zip(self):
        """
        Return the bytes of the course code library file, if it exists.
        """
        return get_python_lib_zip(self.contentstore, self.course_id)
