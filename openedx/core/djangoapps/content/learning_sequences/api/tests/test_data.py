# lint-amnesty, pylint: disable=missing-module-docstring
from datetime import datetime, timezone
from unittest import TestCase

import attr
import pytest
from opaque_keys.edx.keys import CourseKey

from ...data import CourseLearningSequenceData, CourseOutlineData, CourseSectionData, CourseVisibility, VisibilityData


class TestCourseOutlineData(TestCase):
    """
    Simple set of tests for data class validations.
    """
    @classmethod
    def setUpClass(cls):
        """
        All our data classes are immutable, so we can set up a baseline course
        outline and then make slightly modified versions for each particular
        test as needed.
        """
        super().setUpClass()
        normal_visibility = VisibilityData(  # lint-amnesty, pylint: disable=unused-variable
            hide_from_toc=False,
            visible_to_staff_only=False
        )
        cls.course_key = CourseKey.from_string("course-v1:OpenEdX+Learning+TestRun")
        cls.course_outline = CourseOutlineData(
            course_key=cls.course_key,
            title="Exciting Test Course!",
            published_at=datetime(2020, 5, 19, tzinfo=timezone.utc),
            published_version="5ebece4b69dd593d82fe2014",
            entrance_exam_id=None,
            days_early_for_beta=None,
            sections=generate_sections(cls.course_key, [3, 2]),
            self_paced=False,
            course_visibility=CourseVisibility.PRIVATE
        )

    def test_deprecated_course_key(self):
        """Old-Mongo style, "Org/Course/Run" keys are not supported."""
        old_course_key = CourseKey.from_string("OpenEdX/TestCourse/TestRun")
        with pytest.raises(ValueError):
            attr.evolve(self.course_outline, course_key=old_course_key)

    def test_sequence_building(self):
        """Make sure sequences were set correctly from sections data."""
        for section in self.course_outline.sections:
            for seq in section.sequences:
                assert seq == self.course_outline.sequences[seq.usage_key]
        assert sum(len(section.sequences) for section in self.course_outline.sections) ==\
               len(self.course_outline.sequences)

    def test_duplicate_sequence(self):
        """We don't support DAGs. Sequences can only be in one Section."""
        # This section has Chapter 2's sequences in it
        section_with_dupe_seq = attr.evolve(
            self.course_outline.sections[1], title="Chapter 2 dupe",
        )
        with pytest.raises(ValueError):
            attr.evolve(
                self.course_outline,
                sections=self.course_outline.sections + [section_with_dupe_seq]
            )

    def test_size(self):
        """Limit how large a CourseOutline is allowed to be."""
        with pytest.raises(ValueError):
            attr.evolve(
                self.course_outline,
                sections=generate_sections(self.course_key, [1001])
            )

    def test_remove_sequence(self):
        """Remove a single sequence from the CourseOutlineData (creates a copy)."""
        seq_to_remove = self.course_outline.sections[0].sequences[0]
        new_outline = self.course_outline.remove({seq_to_remove.usage_key})
        assert self.course_outline != new_outline
        assert seq_to_remove.usage_key in self.course_outline.sequences
        assert seq_to_remove.usage_key not in new_outline.sequences
        assert len(new_outline.sections[0].sequences) == len(self.course_outline.sections[0].sequences) - 1
        for seq in new_outline.sections[0].sequences:
            assert seq != seq_to_remove

    def test_remove_section(self):
        """
        Remove a whole Section from the CourseOutlineData (creates a copy).

        Removing a Section also removes all Sequences in that Section.
        """
        section_to_remove = self.course_outline.sections[0]
        new_outline = self.course_outline.remove({section_to_remove.usage_key})
        assert self.course_outline != new_outline
        assert len(new_outline.sections) == len(self.course_outline.sections) - 1
        assert section_to_remove != new_outline.sections[0]
        for seq in section_to_remove.sequences:
            assert seq.usage_key not in new_outline.sequences

    def test_remove_nonexistant(self):
        """Removing something that's not already there is a no-op."""
        seq_key_to_remove = self.course_key.make_usage_key('sequential', 'not_here')
        new_outline = self.course_outline.remove({seq_key_to_remove})
        assert new_outline == self.course_outline

    def test_days_early_for_beta(self):
        """
        Check that days_early_for_beta exists, can be set, and validates correctly.
        """
        assert self.course_outline.days_early_for_beta is None
        new_outline = attr.evolve(
            self.course_outline,
            days_early_for_beta=5
        )
        assert new_outline is not None
        assert new_outline != self.course_outline
        assert new_outline.days_early_for_beta == 5

        with pytest.raises(ValueError) as error:
            attr.evolve(self.course_outline, days_early_for_beta=-1)
        assert error.match(
            "Provided value -1 for days_early_for_beta is invalid. The value must be positive or zero. "
            "A positive value will shift back the starting date for Beta users by that many days."
        )

    def test_empty_user_partition_groups(self):
        """
        A user partition group entry with no groups is an error.

        This would mean that a piece of content is associated with a partition
        but nobody would ever be able to see it because it's not associated with
        any group in the partition.
        """
        sections = generate_sections(self.course_key, [1])
        valid_section = attr.evolve(
            sections[0],
            user_partition_groups={
                50: frozenset([1, 2, 3]),
                51: frozenset([1]),
            }
        )
        with self.assertRaises(ValueError):
            attr.evolve(
                valid_section,
                user_partition_groups={
                    50: frozenset([1, 2, 3]),
                    51: frozenset(),  # This is not allowed
                }
            )


def generate_sections(course_key, num_sequences):
    """
    Generate a list of CourseSectionData.

    `num_sequences` is a list that contains the length of each CourseSectionData
    in order. So if you pass in [1, 3, 5], we would pass back a list of three
    CourseSectionData, where the first one has 1 CourseLearningSequenceData as
    it sequences, the second had 3 sequences, and the third had 5 sequences.

    All sections and sequences have normal visibility.
    """
    normal_visibility = VisibilityData(
        hide_from_toc=False,
        visible_to_staff_only=False
    )
    sections = []
    for sec_num, seq_count in enumerate(num_sequences, 1):
        sections.append(
            CourseSectionData(
                usage_key=course_key.make_usage_key('chapter', f'ch_{sec_num}'),
                title=f"Chapter {sec_num}: 🔥",
                visibility=normal_visibility,
                sequences=[
                    CourseLearningSequenceData(
                        usage_key=course_key.make_usage_key(
                            'sequential', f'seq_{sec_num}_{seq_num}'
                        ),
                        title=f"Seq {sec_num}.{seq_num}: 🔥",
                        visibility=normal_visibility,
                    )
                    for seq_num in range(seq_count)
                ]
            )
        )
    return sections
