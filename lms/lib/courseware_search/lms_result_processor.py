"""
This file contains implementation override of SearchResultProcessor which will allow
    * Blends in "location" property
    * Confirms user access to object
"""


from django.urls import reverse
from opaque_keys.edx.keys import CourseKey, UsageKey
from search.result_processor import SearchResultProcessor

from lms.djangoapps.course_blocks.api import get_course_blocks
from lms.djangoapps.courseware.access import has_access
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order


class LmsSearchResultProcessor(SearchResultProcessor):
    """ SearchResultProcessor for LMS Search """
    _course_key = None
    _usage_key = None
    _module_store = None
    _course_blocks = {}

    def get_course_key(self):
        """ fetch course key object from string representation - retain result for subsequent uses """
        if self._course_key is None:
            self._course_key = CourseKey.from_string(self._results_fields["course"])
        return self._course_key

    def get_usage_key(self):
        """ fetch usage key for component from string representation - retain result for subsequent uses """
        if self._usage_key is None:
            usage_key = UsageKey.from_string(self._results_fields["id"])
            self._usage_key = usage_key.map_into_course(self.get_course_key())
        return self._usage_key

    def get_module_store(self):
        """ module store accessor - retain result for subsequent uses """
        if self._module_store is None:
            self._module_store = modulestore()
        return self._module_store

    def get_course_blocks(self, user):
        """ fetch cached blocks for course - retain for subsequent use """
        course_key = self.get_course_key()
        if course_key not in self._course_blocks:
            root_block_usage_key = self.get_module_store().make_course_usage_key(course_key)
            self._course_blocks[course_key] = get_course_blocks(user, root_block_usage_key)
        return self._course_blocks[course_key]

    @property
    def url(self):
        """
        Property to display the url for the given location, useful for allowing navigation
        """
        if "course" not in self._results_fields or "id" not in self._results_fields:
            raise ValueError("Must have course and id in order to build url")

        return reverse(
            "jump_to",
            kwargs={"course_id": self._results_fields["course"], "location": self._results_fields["id"]}
        )

    def should_remove(self, user):
        """ Test to see if this result should be removed due to access restriction """
        if has_access(user, 'staff', self.get_course_key()):
            return False
        return self.get_usage_key() not in self.get_course_blocks(user).get_block_keys()
