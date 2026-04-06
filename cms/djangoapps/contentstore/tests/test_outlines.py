"""
Test the interaction with the learning_sequences app, where course outlines are
stored.
"""
from datetime import datetime, timezone

from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.content.learning_sequences.api import get_course_outline
from openedx.core.djangoapps.content.learning_sequences.data import CourseOutlineData, ExamData, VisibilityData
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import (  # lint-amnesty, pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
)

from ..outlines import get_outline_from_modulestore


class OutlineFromModuleStoreTestCase(ModuleStoreTestCase):
    """
    These tests all set up some sort of course content data in the modulestore
    and extract that data using get_course_outline() to make sure that it
    creates the CourseOutlineData that we expect.

    The learning_sequences app has its own tests to test different scenarios for
    creating the outline. This set of tests only cares about making sure the
    data comes out of the Modulestore in the way we expect.

    Comparisons are done on individual attributes rather than making a complete
    CourseOutline object for comparison, so that more data fields can be added
    later without breaking tests.
    """
    ENABLED_SIGNALS = []
    ENABLED_CACHES = []

    def setUp(self):
        super().setUp()

        self.course_key = CourseKey.from_string("course-v1:TNL+7733+OutlineFromModuleStoreTestCase")

        # This CourseFactory will be a reference to data in the *draft* branch.
        # Creating this does "auto-publish" – all container types changes do,
        # and everything we care about for outlines is a container (section,
        # sequence, unit). But publish version/time metadata will not match the
        # published branch.
        self.draft_course = CourseFactory.create(
            org=self.course_key.org,
            course=self.course_key.course,
            run=self.course_key.run,
            default_store=ModuleStoreEnum.Type.split,
            display_name="OutlineFromModuleStoreTestCase Course",
        )

    def test_empty_course_metadata(self):
        """Courses start empty, and could have a section with no sequences."""
        # The learning_sequences app only uses the published branch, which will
        # have slightly different metadata for version and published_at (because
        # it's created a tiny fraction of a second later). Explicitly pull from
        # published branch to make sure we have the right data.
        with self.store.branch_setting(ModuleStoreEnum.Branch.published_only, self.course_key):
            published_course = self.store.get_course(self.course_key, depth=2)
        outline, _errs = get_outline_from_modulestore(self.course_key)

        # Check basic metdata...
        assert outline.title == "OutlineFromModuleStoreTestCase Course"

        # published_at
        assert isinstance(outline.published_at, datetime)
        assert outline.published_at == published_course.subtree_edited_on
        assert outline.published_at.tzinfo == timezone.utc

        # published_version
        assert isinstance(outline.published_version, str)
        assert outline.published_version == str(published_course.course_version)  # str, not BSON

        # Misc.
        assert outline.entrance_exam_id == published_course.entrance_exam_id
        assert outline.days_early_for_beta == published_course.days_early_for_beta
        assert outline.self_paced == published_course.self_paced

        # Outline stores an enum for course_visibility, while Modulestore uses strs...
        assert outline.course_visibility.value == published_course.course_visibility

        # Check that the contents are empty.
        assert len(outline.sections) == 0
        assert len(outline.sequences) == 0

    def test_normal_sequence(self):
        ms_seq = self._create_seq_in_new_section(display_name="Normal Sequence")
        outline_seq, usage_key = self._outline_seq_data(ms_seq)
        assert outline_seq.usage_key == usage_key
        assert outline_seq.title == "Normal Sequence"
        assert outline_seq.visibility == VisibilityData()
        assert outline_seq.exam == ExamData()
        assert outline_seq.inaccessible_after_due is False

    def test_hidden_after_due_sequence(self):
        ms_seq = self._create_seq_in_new_section(hide_after_due=True)
        outline_seq, _usage_key = self._outline_seq_data(ms_seq)
        assert outline_seq.inaccessible_after_due is True

    def test_staff_only_seq(self):
        ms_seq = self._create_seq_in_new_section(visible_to_staff_only=True)
        outline_seq, _usage_key = self._outline_seq_data(ms_seq)
        assert outline_seq.visibility == VisibilityData(visible_to_staff_only=True)

    def test_hidden_from_toc_seq(self):
        ms_seq = self._create_seq_in_new_section(hide_from_toc=True)
        outline_seq, _usage_key = self._outline_seq_data(ms_seq)
        assert outline_seq.visibility == VisibilityData(hide_from_toc=True)

    def test_practice_exam_seq(self):
        ms_seq = self._create_seq_in_new_section(
            is_time_limited=True,
            is_practice_exam=True,
            is_proctored_enabled=True,
        )
        outline_seq, _usage_key = self._outline_seq_data(ms_seq)
        assert outline_seq.exam == ExamData(
            is_time_limited=True,
            is_practice_exam=True,
            is_proctored_enabled=True,
        )

    def test_proctored_exam_seq(self):
        ms_seq = self._create_seq_in_new_section(
            is_time_limited=True,
            is_proctored_enabled=True,
        )
        outline_seq, _usage_key = self._outline_seq_data(ms_seq)
        assert outline_seq.exam == ExamData(
            is_time_limited=True,
            is_proctored_enabled=True,
        )

    def test_multiple_sections(self):
        """Make sure sequences go into the right places."""
        with self.store.bulk_operations(self.course_key):
            section_1 = BlockFactory.create(
                parent=self.draft_course,
                category='chapter',
                display_name="Section 1 - Three Sequences",
            )
            BlockFactory.create(
                parent=self.draft_course,
                category='chapter',
                display_name="Section 2 - Empty",
            )
            for i in range(3):
                BlockFactory.create(
                    parent=section_1,
                    category='sequential',
                    display_name=f"Seq_1_{i}",
                )

        outline, _errs = get_outline_from_modulestore(self.course_key)

        assert len(outline.sections) == 2
        assert len(outline.sections[0].sequences) == 3
        assert outline.sections[0].sequences[0].title == "Seq_1_0"
        assert outline.sections[0].sequences[1].title == "Seq_1_1"
        assert outline.sections[0].sequences[2].title == "Seq_1_2"
        assert len(outline.sections[1].sequences) == 0

    def test_duplicate_children(self):
        with self.store.bulk_operations(self.course_key):
            section_1 = BlockFactory.create(
                parent=self.draft_course,
                category='chapter',
                display_name="Section",
            )
            seq = BlockFactory.create(
                parent=section_1,
                category='sequential',
                display_name="standard_seq"
            )
            section_2 = BlockFactory.create(
                parent=self.draft_course,
                category='chapter',
                display_name="Section 2",
                children=[seq.location]
            )
            section_3 = BlockFactory.create(
                parent=self.draft_course,
                category='chapter',
                display_name="Section 3",
                children=[seq.location]
            )

        self.store.update_item(section_2, self.user.id)
        self.store.update_item(section_3, self.user.id)
        assert section_1.children == section_2.children
        assert section_1.children == section_3.children

        outline, errs = get_outline_from_modulestore(self.course_key)
        assert len(outline.sections) == 3
        assert len(outline.sections[0].sequences) == 1
        assert len(outline.sections[1].sequences) == 0
        assert len(outline.sections[2].sequences) == 0
        assert len(outline.sequences) == 1
        assert len(errs) == 2

        # Version-less usage keys
        seq_loc = self.course_key.make_usage_key('sequential', 'standard_seq')
        assert errs[0].usage_key == seq_loc
        assert errs[1].usage_key == seq_loc

    def test_unit_in_section(self):
        """
        Test when the structure is Course -> Section -> Unit.

        Studio disallows this, but it's possible to craft in OLX. This type of
        structure is unsupported. We should fail with a CourseStructureError, as
        that will emit useful debug information.
        """
        # Course -> Section -> Unit (No Sequence)
        with self.store.bulk_operations(self.course_key):
            section_1 = BlockFactory.create(
                parent=self.draft_course,
                category='chapter',
                display_name="Section",
            )
            # This Unit should be skipped
            BlockFactory.create(
                parent=section_1,
                category='vertical',
                display_name="u1"
            )
            BlockFactory.create(
                parent=section_1,
                category='sequential',
                display_name="standard_seq"
            )
            BlockFactory.create(
                parent=section_1,
                category='sequential',
                display_name="pset_seq"
            )
            BlockFactory.create(
                parent=section_1,
                category='sequential',
                display_name="video_seq"
            )

            # This should work fine
            section_2 = BlockFactory.create(
                parent=self.draft_course,
                category='chapter',
                display_name="Section 2",
            )

            # Second error message here
            BlockFactory.create(
                parent=section_2,
                category='vertical',
                display_name="u2"
            )

        outline, errs = get_outline_from_modulestore(self.course_key)
        assert len(outline.sections) == 2
        assert len(outline.sections[0].sequences) == 3
        assert len(outline.sections[1].sequences) == 0
        assert len(outline.sequences) == 3

        # Version-less usage keys
        unit_1_loc = self.course_key.make_usage_key('vertical', 'u1')
        unit_2_loc = self.course_key.make_usage_key('vertical', 'u2')
        assert errs[0].usage_key == unit_1_loc
        assert errs[1].usage_key == unit_2_loc

    def test_sequence_without_section(self):
        """
        Test when the structure is Course -> Sequence -> Unit.

        Studio disallows this, but it's possible to craft in OLX. This type of
        structure is unsupported. We should fail with a CourseStructureError, as
        that will emit useful debug information.
        """
        # Course -> Sequence (No Section)
        with self.store.bulk_operations(self.course_key):
            seq = BlockFactory.create(
                parent=self.draft_course,
                category='sequential',
                display_name="Sequence",
            )
            BlockFactory.create(
                parent=seq,
                category='vertical',
                display_name="Unit",
            )

        outline, errs = get_outline_from_modulestore(self.course_key)
        assert len(errs) == 1

        # Strip version information from seq.location before comparison.
        assert errs[0].usage_key == seq.location.map_into_course(self.course_key)
        assert outline.sections == []
        assert outline.sequences == {}

    def test_missing_display_names(self):
        """
        When display_names are empty, it should fallback on url_names.
        """
        with self.store.bulk_operations(self.course_key):
            section = BlockFactory.create(
                parent=self.draft_course,
                category='chapter',
                display_name=None,
            )
            sequence = BlockFactory.create(
                parent=section,
                category='sequential',
                display_name=None,
            )

        outline, _errs = get_outline_from_modulestore(self.course_key)
        assert outline.sections[0].title == section.url_name
        assert outline.sections[0].sequences[0].title == sequence.url_name

    def test_empty_user_partition_groups(self):
        """
        Ignore user partition setting if no groups are associated.

        If we didn't ignore it, we would be creating content that can never be
        seen by any student.
        """
        with self.store.bulk_operations(self.course_key):
            section = BlockFactory.create(
                parent=self.draft_course,
                category='chapter',
                display_name='Ch 1',
                group_access={
                    49: [],
                    50: [1, 2],
                    51: [],
                }
            )
            BlockFactory.create(
                parent=section,
                category='sequential',
                display_name='Seq 1',
                group_access={
                    49: [],
                }
            )

        outline, errs = get_outline_from_modulestore(self.course_key)
        assert len(outline.sections) == 1
        assert len(outline.sequences) == 1
        assert outline.sections[0].user_partition_groups == {50: [1, 2]}
        assert outline.sections[0].sequences[0].user_partition_groups == {}
        assert len(errs) == 2

    def test_bubbled_up_user_partition_groups_no_children(self):
        """Testing empty case to make sure bubble-up code doesn't break."""
        with self.store.bulk_operations(self.course_key):
            section = BlockFactory.create(
                parent=self.draft_course,
                category='chapter',
                display_name='Ch 0',
            )

            # Bubble up with no children (nothing happens)
            BlockFactory.create(
                parent=section,
                category='sequential',
                display_name='Seq 0',
                group_access={}
            )

        outline, _errs = get_outline_from_modulestore(self.course_key)
        seq_data = outline.sections[0].sequences[0]
        assert seq_data.user_partition_groups == {}

    def test_bubbled_up_user_partition_groups_one_child(self):
        """Group settings should bubble up from Unit to Seq. if only one unit"""
        with self.store.bulk_operations(self.course_key):
            section = BlockFactory.create(
                parent=self.draft_course,
                category='chapter',
                display_name='Ch 0',
            )

            # Bubble up with 1 child (grabs the setting from child)
            seq_1 = BlockFactory.create(
                parent=section,
                category='sequential',
                display_name='Seq 1',
                group_access={}
            )
            BlockFactory.create(
                parent=seq_1,
                category='vertical',
                display_name='Single Vertical',
                group_access={
                    50: [1, 2],
                    51: [],  # Empty group shouldn't bubble up.
                },
            )

        outline, errs = get_outline_from_modulestore(self.course_key)
        seq_data = outline.sections[0].sequences[0]
        assert seq_data.user_partition_groups == {50: [1, 2]}
        assert len(errs) == 1

    def test_bubbled_up_user_partition_groups_multiple_children(self):
        """If all Units have the same group_access, bubble up to Sequence."""
        with self.store.bulk_operations(self.course_key):
            section = BlockFactory.create(
                parent=self.draft_course,
                category='chapter',
                display_name='Ch 0',
            )

            # Bubble up with n children, all matching for one group
            seq_n = BlockFactory.create(
                parent=section,
                category='sequential',
                display_name='Seq N',
                group_access={}
            )
            for i in range(4):
                BlockFactory.create(
                    parent=seq_n,
                    category='vertical',
                    display_name=f'vertical {i}',
                    group_access={50: [3, 4], 51: [i]}  # Only 50 should get bubbled up
                )
            BlockFactory.create(
                parent=seq_n,
                category='vertical',
                display_name='vertical 5',
                group_access={50: [4, 3], 51: [5]}  # Ordering should be normalized
            )

        outline, errs = get_outline_from_modulestore(self.course_key)
        seq_data = outline.sections[0].sequences[0]
        assert seq_data.user_partition_groups == {50: [3, 4]}
        assert len(errs) == 1

    def test_not_bubbled_up(self):
        """Don't bubble up from Unit if Seq has a conflicting group_access."""
        with self.store.bulk_operations(self.course_key):
            section = BlockFactory.create(
                parent=self.draft_course,
                category='chapter',
                display_name='Ch 0',
            )

            # Bubble up with 1 child (grabs the setting from child)
            seq_1 = BlockFactory.create(
                parent=section,
                category='sequential',
                display_name='Seq 1',
                group_access={50: [3, 4]}
            )
            BlockFactory.create(
                parent=seq_1,
                category='vertical',
                display_name='Single Vertical',
                group_access={50: [1, 2]},
            )

        outline, errs = get_outline_from_modulestore(self.course_key)
        seq_data = outline.sections[0].sequences[0]
        assert seq_data.user_partition_groups == {50: [3, 4]}  # Kept the seq settings
        assert len(errs) == 1

    def _outline_seq_data(self, modulestore_seq):
        """
        (CourseLearningSequenceData, UsageKey) for a Modulestore sequence.

        When we return the UsageKey part of the tuple, we'll strip out any
        CourseKey branch information that might be present (the most recently
        published set of blocks will have version information when they're
        published, but learning_sequences ignores all of that).
        """
        outline, _errs = get_outline_from_modulestore(self.course_key)

        # Recently modified content can have full version information on their
        # CourseKeys. We need to strip that out and have versionless-CourseKeys
        # or they won't be found properly.
        versionless_usage_key = modulestore_seq.location.map_into_course(self.course_key)
        outline_seq_data = outline.sequences[versionless_usage_key]

        return outline_seq_data, versionless_usage_key

    def _create_seq_in_new_section(self, **kwargs):
        """
        Helper that creates a sequence in a new section and returns it.

        Just reduces the boilerplate of "make me a sequence with the following
        params in a new section/chapter so I can do asserts on how it translated
        over."
        """
        with self.store.bulk_operations(self.course_key):
            section = BlockFactory.create(
                parent=self.draft_course,
                category='chapter',
                display_name="Generated Section",
            )
            sequence = BlockFactory.create(
                parent=section,
                category='sequential',
                **kwargs,
            )

        return sequence


class OutlineFromModuleStoreTaskTestCase(ModuleStoreTestCase):
    """
    Test to make sure that the outline is created after course publishing. (i.e.
    that it correctly receives the course_published signal).
    """
    ENABLED_SIGNALS = ['course_published']

    def test_task_invocation(self):
        """Test outline auto-creation after course publish"""
        course_key = CourseKey.from_string("course-v1:TNL+7733+2021-01-21")
        with self.assertRaises(CourseOutlineData.DoesNotExist):
            get_course_outline(course_key)

        course = CourseFactory.create(
            org=course_key.org,
            course=course_key.course,
            run=course_key.run,
            default_store=ModuleStoreEnum.Type.split,
        )
        section = BlockFactory.create(
            parent_location=course.location,
            category="chapter",
            display_name="First Section"
        )
        BlockFactory.create(
            parent_location=section.location,
            category="sequential",
            display_name="First Sequence"
        )
        BlockFactory.create(
            parent_location=section.location,
            category="sequential",
            display_name="Second Sequence"
        )
        self.store.publish(course.location, self.user.id)

        outline = get_course_outline(course_key)
        assert len(outline.sections) == 1
        assert len(outline.sequences) == 2
