# lint-amnesty, pylint: disable=missing-module-docstring
from datetime import timedelta
from unittest.mock import patch  # lint-amnesty, pylint: disable=wrong-import-order

from edx_toggles.toggles.testutils import override_waffle_flag

from cms.djangoapps.contentstore.config.waffle import CUSTOM_RELATIVE_DATES
from openedx.core.djangoapps.course_date_signals.handlers import (
    _gather_graded_items,
    _get_custom_pacing_children,
    _has_assignment_blocks,
    extract_dates_from_course,
)
from openedx.core.djangoapps.course_date_signals.models import SelfPacedRelativeDatesConfig
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory

from . import utils


class SelfPacedDueDatesTests(ModuleStoreTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        super().setUp()
        course = CourseFactory.create()
        for i in range(4):
            BlockFactory(parent=course, category="sequential", display_name=f"Section {i}")
        # get updated course
        self.course = self.store.get_item(course.location)

    def test_basic_spacing(self):
        expected_sections = [
            (0, 'Section 0', timedelta(days=7)),
            (1, 'Section 1', timedelta(days=14)),
            (2, 'Section 2', timedelta(days=21)),
            (3, 'Section 3', timedelta(days=28)),
        ]
        with patch.object(utils, 'get_expected_duration', return_value=timedelta(weeks=4)):
            actual = [(idx, section.display_name, offset) for (idx, section, offset) in utils.spaced_out_sections(self.course)]  # lint-amnesty, pylint: disable=line-too-long

        assert actual == expected_sections

    def test_hidden_sections(self):
        for _ in range(2):
            BlockFactory(parent=self.course, category="sequential", visible_to_staff_only=True)
        expected_sections = [
            (0, 'Section 0', timedelta(days=7)),
            (1, 'Section 1', timedelta(days=14)),
            (2, 'Section 2', timedelta(days=21)),
            (3, 'Section 3', timedelta(days=28)),
        ]
        with patch.object(utils, 'get_expected_duration', return_value=timedelta(weeks=4)):
            actual = [(idx, section.display_name, offset) for (idx, section, offset) in utils.spaced_out_sections(self.course)]  # lint-amnesty, pylint: disable=line-too-long

        assert actual == expected_sections

    def test_dates_for_ungraded_assignments(self):
        """
        _has_assignment_blocks should return true if the argument block
        children leaf nodes include an assignment that is graded and scored
        """
        with self.store.bulk_operations(self.course.id):
            sequence = BlockFactory(parent=self.course, category="sequential")
            vertical = BlockFactory(parent=sequence, category="vertical")

        sequence = self.store.get_item(sequence.location)
        assert _has_assignment_blocks(sequence) is False

        # Ungraded problems do not count as assignment blocks
        BlockFactory.create(
            parent=vertical,
            category='problem',
            graded=True,
            weight=0,
        )
        sequence = self.store.get_item(sequence.location)
        assert _has_assignment_blocks(sequence) is False

        BlockFactory.create(
            parent=vertical,
            category='problem',
            graded=False,
            weight=1,
        )
        sequence = self.store.get_item(sequence.location)
        assert _has_assignment_blocks(sequence) is False

        # Method will return true after adding a graded, scored assignment block
        BlockFactory.create(
            parent=vertical,
            category='problem',
            graded=True,
            weight=1,
        )
        sequence = self.store.get_item(sequence.location)
        assert _has_assignment_blocks(sequence) is True

    def test_sequence_with_graded_and_ungraded_assignments(self):
        """
        _gather_graded_items should set a due date of None on ungraded problem blocks
        even if the block has graded siblings in the sequence
        """
        with self.store.bulk_operations(self.course.id):
            sequence = BlockFactory(parent=self.course, category="sequential")
            vertical = BlockFactory(parent=sequence, category="vertical")
            sequence = self.store.get_item(sequence.location)
            BlockFactory.create(
                parent=vertical,
                category='problem',
                graded=False,
                weight=1,
            )
            ungraded_problem_2 = BlockFactory.create(
                parent=vertical,
                category='problem',
                graded=True,
                weight=0,
            )
            graded_problem_1 = BlockFactory.create(
                parent=vertical,
                category='problem',
                graded=True,
                weight=1,
            )
            expected_graded_items = [
                (ungraded_problem_2.location, {'due': None}),
                (graded_problem_1.location, {'due': 5}),
            ]
            sequence = self.store.get_item(sequence.location)
            self.assertCountEqual(_gather_graded_items(sequence, 5), expected_graded_items)

    def test_sequence_with_ora_and_non_ora_assignments(self):
        """
        _gather_graded_items should not set a due date for ORA problems
        """
        with self.store.bulk_operations(self.course.id):
            sequence = BlockFactory(parent=self.course, category="sequential")
            vertical = BlockFactory(parent=sequence, category="vertical")
            BlockFactory.create(
                parent=vertical,
                category='openassessment',
                graded=True
            )
            ungraded_problem_2 = BlockFactory.create(
                parent=vertical,
                category='problem',
                graded=True,
                weight=0,
            )
            graded_problem_1 = BlockFactory.create(
                parent=vertical,
                category='problem',
                graded=True,
                weight=1,
            )
            expected_graded_items = [
                (ungraded_problem_2.location, {'due': None}),
                (graded_problem_1.location, {'due': 5}),
            ]
            sequence = self.store.get_item(sequence.location)
            self.assertCountEqual(_gather_graded_items(sequence, 5), expected_graded_items)

    def test_get_custom_pacing_children(self):
        """
        _get_custom_pacing_items should return a list of (block item location, field metadata dictionary)
        where the due dates are set from relative_weeks_due
        """
        # A subsection with multiple units but no problems. Units should inherit due date.
        with self.store.bulk_operations(self.course.id):
            sequence = BlockFactory(parent=self.course, category='sequential', relative_weeks_due=2)
            vertical1 = BlockFactory(parent=sequence, category='vertical')
            vertical2 = BlockFactory(parent=sequence, category='vertical')
            vertical3 = BlockFactory(parent=sequence, category='vertical')
            expected_dates = [
                (sequence.location, {'due': timedelta(weeks=2)}),
                (vertical1.location, {'due': timedelta(weeks=2)}),
                (vertical2.location, {'due': timedelta(weeks=2)}),
                (vertical3.location, {'due': timedelta(weeks=2)})
            ]
        sequence = self.store.get_item(sequence.location)
        self.assertCountEqual(_get_custom_pacing_children(sequence, 2), expected_dates)

        with self.store.bulk_operations(self.course.id):
            # A subsection with multiple units, each of which has a problem.
            # Problems should also inherit due date.
            problem1 = BlockFactory(parent=vertical1, category='problem')
            problem2 = BlockFactory(parent=vertical2, category='problem')
            expected_dates.extend([
                (problem1.location, {'due': timedelta(weeks=2)}),
                (problem2.location, {'due': timedelta(weeks=2)})
            ])
        sequence = self.store.get_item(sequence.location)
        self.assertCountEqual(_get_custom_pacing_children(sequence, 2), expected_dates)

        # A subsection that has ORA as a problem. ORA should not inherit due date.
        BlockFactory.create(parent=vertical3, category='openassessment')
        sequence = self.store.get_item(sequence.location)
        self.assertCountEqual(_get_custom_pacing_children(sequence, 2), expected_dates)

        # A subsection that has an ORA problem and a non ORA problem. ORA should
        # not inherit due date, but non ORA problems should.
        problem3 = BlockFactory(parent=vertical3, category='problem')
        expected_dates.append((problem3.location, {'due': timedelta(weeks=2)}))
        sequence = self.store.get_item(sequence.location)
        self.assertCountEqual(_get_custom_pacing_children(sequence, 2), expected_dates)


class SelfPacedCustomDueDateTests(ModuleStoreTestCase):
    """
    Tests the custom Personalized Learner Schedule (PLS) dates in self paced courses
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        super().setUp()
        SelfPacedRelativeDatesConfig.objects.create(enabled=True)
        course = CourseFactory.create(self_paced=True)
        self.chapter = BlockFactory.create(category='chapter', parent=course)
        # get updated course
        self.course = self.store.get_item(course.location)

    @override_waffle_flag(CUSTOM_RELATIVE_DATES, active=True)
    def test_extract_dates_from_course_inheritance(self):
        """
        extract_dates_from_course should return a list of (block item location, field metadata dictionary)
        and the blocks should inherit the dates from those above in the hiearchy
        (ex. If a subsection is assigned a due date, its children should also have the same due date)
        """
        with self.store.bulk_operations(self.course.id):
            sequential = BlockFactory.create(category='sequential', parent=self.chapter, relative_weeks_due=3)
            vertical = BlockFactory.create(category='vertical', parent=sequential)
            problem = BlockFactory.create(category='problem', parent=vertical)
            expected_dates = [
                (self.course.location, {}),
                (self.chapter.location, {'due': timedelta(days=21)}),
                (sequential.location, {'due': timedelta(days=21)}),
                (vertical.location, {'due': timedelta(days=21)}),
                (problem.location, {'due': timedelta(days=21)})
            ]
        course = self.store.get_item(self.course.location)
        self.assertCountEqual(extract_dates_from_course(course), expected_dates)

    @override_waffle_flag(CUSTOM_RELATIVE_DATES, active=True)
    def test_extract_dates_from_course_custom_and_default_pls_one_subsection(self):
        """
        relative_weeks_due in one of the subsections. Only one of them should have a set due date.
        The other subsections do not have due dates because they are not graded
        and default PLS do not assign due dates to non graded assignments.
        If custom PLS is not set, the subsection will fall back to the default
        PLS logic of evenly spaced sections.
        """
        with self.store.bulk_operations(self.course.id):
            sequential = BlockFactory.create(category='sequential', parent=self.chapter, relative_weeks_due=3)
            BlockFactory.create(category='sequential', parent=self.chapter)
            BlockFactory.create(category='sequential', parent=self.chapter)
            expected_dates = [
                (self.course.location, {}),
                (self.chapter.location, {'due': timedelta(days=28)}),
                (sequential.location, {'due': timedelta(days=21)})
            ]
        course = self.store.get_item(self.course.location)
        self.assertCountEqual(extract_dates_from_course(course), expected_dates)

    @override_waffle_flag(CUSTOM_RELATIVE_DATES, active=True)
    def test_extract_dates_from_course_custom_and_default_pls_one_subsection_graded(self):
        """
        A section with a subsection that has relative_weeks_due and
        a subsection without relative_weeks_due that has graded content.
        Default PLS should apply for the subsection without relative_weeks_due that has graded content.
        If custom PLS is not set, the subsection will fall back to the default
        PLS logic of evenly spaced sections.
        """
        with self.store.bulk_operations(self.course.id):
            sequential1 = BlockFactory.create(category='sequential', parent=self.chapter, relative_weeks_due=2)
            vertical1 = BlockFactory.create(category='vertical', parent=sequential1)
            problem1 = BlockFactory.create(category='problem', parent=vertical1)

            chapter2 = BlockFactory.create(category='chapter', parent=self.course)
            sequential2 = BlockFactory.create(category='sequential', parent=chapter2, graded=True)
            vertical2 = BlockFactory.create(category='vertical', parent=sequential2)
            problem2 = BlockFactory.create(category='problem', parent=vertical2)

            expected_dates = [
                (self.course.location, {}),
                (self.chapter.location, {'due': timedelta(days=14)}),
                (sequential1.location, {'due': timedelta(days=14)}),
                (vertical1.location, {'due': timedelta(days=14)}),
                (problem1.location, {'due': timedelta(days=14)}),
                (chapter2.location, {'due': timedelta(days=42)}),
                (sequential2.location, {'due': timedelta(days=42)}),
                (vertical2.location, {'due': timedelta(days=42)}),
                (problem2.location, {'due': timedelta(days=42)})
            ]
        course = self.store.get_item(self.course.location)
        with patch.object(utils, 'get_expected_duration', return_value=timedelta(weeks=6)):
            self.assertCountEqual(extract_dates_from_course(course), expected_dates)

    @override_waffle_flag(CUSTOM_RELATIVE_DATES, active=True)
    def test_extract_dates_from_course_custom_and_default_pls_multiple_subsections_graded(self):
        """
        A section with a subsection that has relative_weeks_due and multiple sections without
        relative_weeks_due that has graded content. Default PLS should apply for the subsections
        without relative_weeks_due that has graded content.
        If custom PLS is not set, the subsection will fall back to the default
        PLS logic of evenly spaced sections.
        """
        with self.store.bulk_operations(self.course.id):
            sequential1 = BlockFactory.create(category='sequential', parent=self.chapter, relative_weeks_due=4)
            vertical1 = BlockFactory.create(category='vertical', parent=sequential1)
            problem1 = BlockFactory.create(category='problem', parent=vertical1)

            expected_dates = [
                (self.course.location, {}),
                (self.chapter.location, {'due': timedelta(days=28)}),
                (sequential1.location, {'due': timedelta(days=28)}),
                (vertical1.location, {'due': timedelta(days=28)}),
                (problem1.location, {'due': timedelta(days=28)})
            ]

        for i in range(3):
            course = self.store.get_item(self.course.location)
            chapter = BlockFactory.create(category='chapter', parent=course)
            with self.store.bulk_operations(self.course.id):
                sequential = BlockFactory.create(category='sequential', parent=chapter, graded=True)
                vertical = BlockFactory.create(category='vertical', parent=sequential)
                problem = BlockFactory.create(category='problem', parent=vertical)
                num_days = i * 14 + 28
                expected_dates.extend([
                    (chapter.location, {'due': timedelta(days=num_days)}),
                    (sequential.location, {'due': timedelta(days=num_days)}),
                    (vertical.location, {'due': timedelta(days=num_days)}),
                    (problem.location, {'due': timedelta(days=num_days)}),
                ])

        course = self.store.get_item(self.course.location)
        with patch.object(utils, 'get_expected_duration', return_value=timedelta(weeks=8)):
            self.assertCountEqual(extract_dates_from_course(course), expected_dates)

    @override_waffle_flag(CUSTOM_RELATIVE_DATES, active=True)
    def test_extract_dates_from_course_all_subsections(self):
        """
        With relative_weeks_due on all subsections. All subsections should
        have their corresponding due dates.
        """
        with self.store.bulk_operations(self.course.id):
            chapter = BlockFactory.create(category='chapter', parent=self.course)
            sequential1 = BlockFactory.create(category='sequential', parent=chapter, relative_weeks_due=3)
            sequential2 = BlockFactory.create(category='sequential', parent=chapter, relative_weeks_due=4)
            sequential3 = BlockFactory.create(category='sequential', parent=chapter, relative_weeks_due=5)
            expected_dates = [
                (self.course.location, {}),
                (chapter.location, {'due': timedelta(days=35)}),
                (sequential1.location, {'due': timedelta(days=21)}),
                (sequential2.location, {'due': timedelta(days=28)}),
                (sequential3.location, {'due': timedelta(days=35)})
            ]
        course = self.store.get_item(self.course.location)
        self.assertCountEqual(extract_dates_from_course(course), expected_dates)

    @override_waffle_flag(CUSTOM_RELATIVE_DATES, active=True)
    def test_extract_dates_from_course_no_subsections(self):
        """
        Without relative_weeks_due on all subsections. None of the subsections should
        have due dates.
        """
        with self.store.bulk_operations(self.course.id):
            for _ in range(3):
                BlockFactory.create(category='sequential', parent=self.chapter)
            expected_dates = [(self.course.location, {})]
        course = self.store.get_item(self.course.location)
        self.assertCountEqual(extract_dates_from_course(course), expected_dates)
