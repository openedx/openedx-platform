"""Utilities for managing course code libraries and sandbox execution."""

from django.conf import settings
from opaque_keys.edx.keys import CourseKey, LearningContextKey

DEFAULT_PYTHON_LIB_FILENAME = "python_lib.zip"


def course_code_library_asset_name():
    """
    Return the asset name to use for course code libraries, defaulting to python_lib.zip.
    """
    return getattr(settings, "PYTHON_LIB_FILENAME", DEFAULT_PYTHON_LIB_FILENAME)


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
        Always False: course code only runs in the codejail service.

        Kept only because xblocks-core's ProblemBlock still reads it from this service.
        Remove it with https://github.com/openedx/xblocks-core/issues/350
        """
        return False

    def get_python_lib_zip(self):
        """
        Return the bytes of the course code library file, if it exists.
        """
        return get_python_lib_zip(self.contentstore, self.course_id)
