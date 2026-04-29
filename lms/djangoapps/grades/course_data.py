"""
Code used to get and cache the requested course-data
"""


from lms.djangoapps.course_blocks.api import get_course_blocks
from openedx.core.djangoapps.content.block_structure.api import get_block_structure_manager
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

from .transformer import GradesTransformer


class CourseData:
    """
    Utility access layer to intelligently get and cache the
    requested course data as long as at least one property is
    provided upon initialization.

    This is an in-memory object that maintains its own internal
    cache during its lifecycle.
    """
    def __init__(self, user, course=None, collected_block_structure=None, structure=None, course_key=None):
        if not any((course, collected_block_structure, structure, course_key)):
            raise ValueError(
                "You must specify one of course, collected_block_structure, structure, or course_key to this method."
            )
        self.user = user
        self._collected_block_structure = collected_block_structure
        self._structure = structure
        self._course = course
        self._course_key = course_key
        self._location = None

    @property
    def course_key(self):  # pylint: disable=missing-function-docstring
        if not self._course_key:
            if self._course:
                self._course_key = self._course.id
            else:
                structure = self.effective_structure
                self._course_key = structure.root_block_usage_key.course_key
        return self._course_key

    @property
    def location(self):  # pylint: disable=missing-function-docstring
        if not self._location:
            structure = self.effective_structure
            if structure:
                self._location = structure.root_block_usage_key
            elif self._course:
                self._location = self._course.location
            else:
                self._location = modulestore().make_course_usage_key(self.course_key)
        return self._location

    @property
    def structure(self):  # pylint: disable=missing-function-docstring
        if self._structure is None:
            self._structure = get_course_blocks(
                self.user,
                self.location,
                collected_block_structure=self._collected_block_structure,
            )
        return self._structure

    @property
    def collected_structure(self):
        if self._collected_block_structure is None:
            self._collected_block_structure = get_block_structure_manager(self.course_key).get_collected()
        return self._collected_block_structure

    @property
    def course(self):
        if not self._course:
            self._course = modulestore().get_course(self.course_key)
        return self._course

    @property
    def grading_policy_hash(self):  # pylint: disable=missing-function-docstring
        structure = self.effective_structure
        if structure:
            return structure.get_transformer_block_field(
                structure.root_block_usage_key,
                GradesTransformer,
                'grading_policy_hash',
            )
        else:
            return GradesTransformer.grading_policy_hash(self.course)

    @property
    def version(self):
        structure = self.effective_structure
        course_block = structure[self.location] if structure else self.course
        return getattr(course_block, 'course_version', None)

    @property
    def edited_on(self):  # pylint: disable=missing-function-docstring
        # get course block from structure only; subtree_edited_on field on modulestore's course block isn't optimized.
        structure = self.effective_structure
        if structure:
            course_block = structure[self.location]
            return getattr(course_block, 'subtree_edited_on', None)

    def __str__(self):
        """
        Return human-readable string representation.
        """
        return f'Course: course_key: {self.course_key}'

    def full_string(self):  # pylint: disable=missing-function-docstring
        if self.effective_structure:
            return 'Course: course_key: {}, version: {}, edited_on: {}, grading_policy: {}'.format(  # noqa: UP032
                self.course_key, self.version, self.edited_on, self.grading_policy_hash,
            )
        else:
            return f'Course: course_key: {self.course_key}, empty course structure'

    @property
    def effective_structure(self):
        """
        Get whichever course block structure is already loaded, if any.

        This may give either the user-specific course structure or the generic
        structure, depending on which is cached at the moment. Because of that,
        this should only be used for queries related to the root block of the
        course, which will always exist in either structure.

        For anything else, such as queries involving course sections or blocks,
        use either .structure or .collected_structure to explicitly state
        whether you want the user-specific version of the course or not.
        """
        return self._structure or self._collected_block_structure
