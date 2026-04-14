"""
Simple OutlineProcessor that removes items based on Enrollment and course visibility setting.
"""
from common.djangoapps.student.models import CourseEnrollment
from openedx.features.course_experience import COURSE_ENABLE_UNENROLLED_ACCESS_FLAG

from ...data import CourseVisibility
from .base import OutlineProcessor


class EnrollmentOutlineProcessor(OutlineProcessor):
    """
    Simple OutlineProcessor that removes items based on Enrollment and course visibility setting.
    """
    def usage_keys_to_remove(self, full_course_outline):
        """
        Return sequences/sections to be removed
        """
        # Public outlines and courses don't need to hide anything from the outline.
        is_unenrolled_access_enabled = COURSE_ENABLE_UNENROLLED_ACCESS_FLAG.is_enabled(self.course_key)
        is_course_outline_publicly_visible = (
            full_course_outline.course_visibility in [CourseVisibility.PUBLIC, CourseVisibility.PUBLIC_OUTLINE]
        )

        if is_unenrolled_access_enabled and is_course_outline_publicly_visible:
            return frozenset()

        # Students who are enrolled can see the full outline.
        if CourseEnrollment.is_enrolled(self.user, self.course_key):
            return frozenset()

        # Otherwise remove everything:
        seqs_to_remove = set(full_course_outline.sequences)
        sections_to_remove = {sec.usage_key for sec in full_course_outline.sections}

        return frozenset(seqs_to_remove | sections_to_remove)

    def inaccessible_sequences(self, full_course_outline):
        """
        Return a set/frozenset of Sequence UsageKeys that are not accessible.
        """
        is_public_outline = full_course_outline.course_visibility == CourseVisibility.PUBLIC_OUTLINE
        is_enrolled_in_course = CourseEnrollment.is_enrolled(self.user, self.course_key)
        if is_public_outline and not is_enrolled_in_course:
            return frozenset(full_course_outline.sequences)
        return frozenset()
