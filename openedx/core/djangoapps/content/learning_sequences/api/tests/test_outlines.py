"""
Top level API tests. Tests API public contracts only. Do not import/create/mock
models for this app.
"""
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import unittest

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.db.models import signals
from edx_proctoring.exceptions import ProctoredExamNotFoundException
from edx_toggles.toggles.testutils import override_waffle_flag
from edx_when.api import set_dates_for_course
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import LibraryLocator
import attr
import ddt
from lms.djangoapps.teams.tests.factories import CourseTeamFactory
from openedx.core.djangoapps.content.learning_sequences.api.processors.team_partition_groups import (
    TeamPartitionGroupsOutlineProcessor,
)
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory
import pytest

from openedx.core.djangoapps.course_apps.toggles import EXAMS_IDA
from openedx.core.djangoapps.course_groups.models import CourseCohortsSettings, CourseUserGroupPartitionGroup
from openedx.core.djangoapps.course_groups.tests.helpers import CohortFactory
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase
from openedx.features.course_experience import COURSE_ENABLE_UNENROLLED_ACCESS_FLAG
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.signals import update_masters_access_course
from common.djangoapps.student.auth import user_has_role
from common.djangoapps.student.roles import CourseBetaTesterRole
from common.djangoapps.student.tests.factories import BetaTesterFactory, UserFactory
from xmodule.partitions.partitions import (  # lint-amnesty, pylint: disable=wrong-import-order
    ENROLLMENT_TRACK_PARTITION_ID,
)

from ...data import (
    ContentErrorData,
    CourseLearningSequenceData,
    CourseOutlineData,
    CourseSectionData,
    CourseVisibility,
    ExamData,
    VisibilityData,

)
from ..outlines import (
    get_content_errors,
    get_course_outline,
    get_user_course_outline,
    get_user_course_outline_details,
    key_supports_outlines,
    replace_course_outline,
)
from ..processors.enrollment_track_partition_groups import EnrollmentTrackPartitionGroupsOutlineProcessor
from .test_data import generate_sections
from openedx.core.lib.teams_config import TeamsConfig


class OutlineSupportTestCase(unittest.TestCase):
    """
    Make sure we know what kinds of course-like keys we support for outlines.
    """
    def test_supported_types(self):
        assert key_supports_outlines(CourseKey.from_string("course-v1:edX+100+2021"))
        assert key_supports_outlines(CourseKey.from_string("ccx-v1:edX+100+2021+ccx@1"))

    def test_unsupported_types(self):
        assert not key_supports_outlines(CourseKey.from_string("edX/100/2021"))
        assert not key_supports_outlines(LibraryLocator(org="edX", library="100"))


class CourseOutlineTestCase(CacheIsolationTestCase):
    """
    Simple tests around reading and writing CourseOutlineData. No user info.
    """
    @classmethod
    def setUpTestData(cls):  # lint-amnesty, pylint: disable=super-method-not-called
        cls.course_key = CourseKey.from_string("course-v1:OpenEdX+Learn+Roundtrip")
        normal_visibility = VisibilityData(  # lint-amnesty, pylint: disable=unused-variable
            hide_from_toc=False,
            visible_to_staff_only=False
        )
        cls.course_outline = CourseOutlineData(
            course_key=cls.course_key,
            title="Roundtrip Test Course!",
            published_at=datetime(2020, 5, 20, tzinfo=timezone.utc),
            published_version="5ebece4b69dd593d82fe2015",
            entrance_exam_id=None,
            days_early_for_beta=None,
            sections=generate_sections(cls.course_key, [2, 2]),
            self_paced=False,
            course_visibility=CourseVisibility.PRIVATE
        )

    def test_deprecated_course_key(self):
        """Don't allow Old Mongo Courses at all."""
        old_course_key = CourseKey.from_string("Org/Course/Run")
        with pytest.raises(ValueError):
            outline = get_course_outline(old_course_key)  # lint-amnesty, pylint: disable=unused-variable

    def test_simple_roundtrip(self):
        """Happy path for writing/reading-back a course outline."""
        with pytest.raises(CourseOutlineData.DoesNotExist):
            course_outline = get_course_outline(self.course_key)  # lint-amnesty, pylint: disable=unused-variable

        replace_course_outline(self.course_outline)
        outline = get_course_outline(self.course_key)
        assert outline == self.course_outline

    def test_empty_course(self):
        """Empty Courses are a common case (when authoring just starts)."""
        empty_outline = attr.evolve(self.course_outline, sections=[])
        assert not empty_outline.sections
        assert not empty_outline.sequences
        replace_course_outline(empty_outline)
        assert empty_outline == get_course_outline(self.course_key)

    def test_empty_sections(self):
        """Empty Sections aren't very useful, but they shouldn't break."""
        empty_section_outline = attr.evolve(
            self.course_outline, sections=generate_sections(self.course_key, [0])
        )
        replace_course_outline(empty_section_outline)
        assert empty_section_outline == get_course_outline(self.course_key)

    def test_cached_response(self):
        # First lets seed the data...
        replace_course_outline(self.course_outline)

        # Uncached access always makes five database checks: LearningContext,
        # CourseSection (+1 for user partition group prefetch),
        # CourseSectionSequence (+1 for user partition group prefetch)
        with self.assertNumQueries(5):
            uncached_outline = get_course_outline(self.course_key)
            assert uncached_outline == self.course_outline

        # Successful cache access only makes a query to LearningContext to check
        # the current published version. That way we know that values are never
        # stale.
        with self.assertNumQueries(1):
            cached_outline = get_course_outline(self.course_key)

        # Cache hits in the same process are literally the same object.
        assert cached_outline is uncached_outline

        # Now we put a new version into the cache...
        new_version_outline = attr.evolve(
            self.course_outline, published_version="2222222222222222"
        )
        replace_course_outline(new_version_outline)

        # Make sure this new outline is returned instead of the previously
        # cached one.
        with self.assertNumQueries(5):
            uncached_new_version_outline = get_course_outline(self.course_key)  # lint-amnesty, pylint: disable=unused-variable
            assert new_version_outline == new_version_outline  # lint-amnesty, pylint: disable=comparison-with-itself


class UserCourseOutlineTestCase(CacheIsolationTestCase, ModuleStoreTestCase):
    """
    Tests for basic UserCourseOutline functionality.
    """

    @classmethod
    def setUpTestData(cls):  # lint-amnesty, pylint: disable=super-method-not-called
        cls.course_key = CourseKey.from_string("course-v1:OpenEdX+Outline+T1")
        # Users...
        cls.global_staff = UserFactory.create(
            username='global_staff', email='gstaff@example.com', is_staff=True
        )
        cls.student = UserFactory.create(
            username='student', email='student@example.com', is_staff=False
        )
        cls.beta_tester = BetaTesterFactory(course_key=cls.course_key)
        cls.anonymous_user = AnonymousUser()

        # Seed with data
        cls.simple_outline = CourseOutlineData(
            course_key=cls.course_key,
            title="User Outline Test Course!",
            published_at=datetime(2020, 5, 20, tzinfo=timezone.utc),
            published_version="5ebece4b69dd593d82fe2020",
            entrance_exam_id=None,
            days_early_for_beta=None,
            sections=generate_sections(cls.course_key, [2, 1, 3]),
            self_paced=False,
            course_visibility=CourseVisibility.PRIVATE
        )
        replace_course_outline(cls.simple_outline)

        # Enroll student in the course
        cls.student.courseenrollment_set.create(course_id=cls.course_key, is_active=True, mode="audit")
        # Enroll beta tester in the course
        cls.beta_tester.courseenrollment_set.create(course_id=cls.course_key, is_active=True, mode="audit")

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            org=self.course_key.org,
            course=self.course_key.course,
            run=self.course_key.run,
            display_name=f"Team Partitions Test Course {self.course_key.run}",
        )

    def test_simple_outline(self):
        """This outline is the same for everyone."""
        at_time = datetime(2020, 5, 21, tzinfo=timezone.utc)
        beta_tester_outline = get_user_course_outline(
            self.course_key, self.beta_tester, at_time
        )
        student_outline = get_user_course_outline(
            self.course_key, self.student, at_time
        )
        global_staff_outline = get_user_course_outline(
            self.course_key, self.global_staff, at_time
        )
        assert beta_tester_outline.sections == global_staff_outline.sections
        assert student_outline.sections == global_staff_outline.sections
        assert student_outline.at_time == at_time

        beta_tester_outline_details = get_user_course_outline_details(
            self.course_key, self.beta_tester, at_time
        )
        assert beta_tester_outline_details.outline == beta_tester_outline

        student_outline_details = get_user_course_outline_details(
            self.course_key, self.student, at_time
        )
        assert student_outline_details.outline == student_outline

        global_staff_outline_details = get_user_course_outline_details(
            self.course_key, self.global_staff, at_time
        )
        assert global_staff_outline_details.outline == global_staff_outline


class OutlineProcessorTestCase(CacheIsolationTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    @classmethod
    def setUpTestData(cls):  # lint-amnesty, pylint: disable=super-method-not-called
        cls.course_key = CourseKey.from_string("course-v1:OpenEdX+Outline+T1")

        # Users...
        cls.global_staff = UserFactory.create(
            username='global_staff', email='gstaff@example.com', is_staff=True
        )
        cls.student = UserFactory.create(
            username='student', email='student@example.com', is_staff=False
        )
        cls.beta_tester = BetaTesterFactory(course_key=cls.course_key)
        cls.anonymous_user = AnonymousUser()

        cls.all_seq_keys = []

    def get_details(self, at_time):
        staff_details = get_user_course_outline_details(self.course_key, self.global_staff, at_time)
        student_details = get_user_course_outline_details(self.course_key, self.student, at_time)
        beta_tester_details = get_user_course_outline_details(self.course_key, self.beta_tester, at_time)
        return staff_details, student_details, beta_tester_details

    @classmethod
    def set_sequence_keys(cls, keys):
        cls.all_seq_keys = keys

    def get_sequence_keys(self, exclude=None):  # lint-amnesty, pylint: disable=missing-function-docstring
        if exclude is None:
            exclude = []
        if not isinstance(exclude, list):
            raise TypeError("`exclude` must be a list of keys to be excluded")
        return [key for key in self.all_seq_keys if key not in exclude]


class ContentGatingTestCase(OutlineProcessorTestCase, ModuleStoreTestCase):
    """
    Content Gating specific outline tests
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        # The UsageKeys we're going to set up for content gating tests.
        cls.entrance_exam_section_key = cls.course_key.make_usage_key('chapter', 'entrance_exam')
        cls.entrance_exam_seq_key = cls.course_key.make_usage_key('sequential', 'entrance_exam')
        cls.open_section_key = cls.course_key.make_usage_key('chapter', 'open')
        cls.open_seq_key = cls.course_key.make_usage_key('sequential', 'open')
        cls.gated_section_key = cls.course_key.make_usage_key('chapter', 'gated')
        cls.gated_seq_key = cls.course_key.make_usage_key('sequential', 'gated')

        cls.set_sequence_keys([
            cls.entrance_exam_seq_key,
            cls.open_seq_key,
            cls.gated_seq_key,
        ])

        set_dates_for_course(
            cls.course_key,
            [
                (
                    cls.course_key.make_usage_key('course', 'course'),
                    {'start': datetime(2020, 5, 10, tzinfo=timezone.utc)}
                ),
                (
                    cls.entrance_exam_section_key,
                    {'start': datetime(2020, 5, 15, tzinfo=timezone.utc)}
                ),
                (
                    cls.entrance_exam_seq_key,
                    {'start': datetime(2020, 5, 15, tzinfo=timezone.utc)}
                ),
                (
                    cls.open_section_key,
                    {'start': datetime(2020, 5, 15, tzinfo=timezone.utc)}
                ),
                (
                    cls.open_seq_key,
                    {'start': datetime(2020, 5, 15, tzinfo=timezone.utc)}
                ),
                (
                    cls.gated_section_key,
                    {'start': datetime(2020, 5, 15, tzinfo=timezone.utc)}
                ),
                (
                    cls.gated_seq_key,
                    {'start': datetime(2020, 5, 15, tzinfo=timezone.utc)}
                ),
            ]
        )

        visibility = VisibilityData(
            hide_from_toc=False,
            visible_to_staff_only=False
        )
        cls.outline = CourseOutlineData(
            course_key=cls.course_key,
            title="User Outline Test Course!",
            published_at=datetime(2020, 5, 20, tzinfo=timezone.utc),
            published_version="5ebece4b69dd593d82fe2020",
            entrance_exam_id=str(cls.entrance_exam_section_key),
            days_early_for_beta=None,
            self_paced=False,
            course_visibility=CourseVisibility.PRIVATE,
            sections=[
                CourseSectionData(
                    usage_key=cls.entrance_exam_section_key,
                    title="Entrance Exam",
                    visibility=visibility,
                    sequences=[
                        CourseLearningSequenceData(
                            usage_key=cls.entrance_exam_seq_key,
                            title='Entrance Exam',
                            visibility=visibility
                        ),
                    ]
                ),
                CourseSectionData(
                    usage_key=cls.open_section_key,
                    title="Open Section",
                    visibility=visibility,
                    sequences=[
                        CourseLearningSequenceData(
                            usage_key=cls.open_seq_key,
                            title='Open Sequence',
                            visibility=visibility
                        ),
                    ]
                ),
                CourseSectionData(
                    usage_key=cls.gated_section_key,
                    title="Gated Section",
                    visibility=visibility,
                    sequences=[
                        CourseLearningSequenceData(
                            usage_key=cls.gated_seq_key,
                            title='Gated Sequence',
                            visibility=visibility
                        ),
                    ]
                ),
            ]
        )
        replace_course_outline(cls.outline)

        # Enroll student in the course
        cls.student.courseenrollment_set.create(course_id=cls.course_key, is_active=True, mode="verified")

    # lint-amnesty, pylint: disable=pointless-string-statement
    """
    Currently returns all, and only, sequences in required content, not just the first.
    This logic matches the existing transformer. Is this right?
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            org=self.course_key.org,
            number=self.course_key.course,
            run=self.course_key.run,
            display_name=f"Outline Test Course {self.course_key.run}",
        )

    @patch('openedx.core.djangoapps.content.learning_sequences.api.processors.content_gating.EntranceExamConfiguration.user_can_skip_entrance_exam')  # lint-amnesty, pylint: disable=line-too-long
    @patch('openedx.core.djangoapps.content.learning_sequences.api.processors.content_gating.milestones_helpers.get_required_content')  # lint-amnesty, pylint: disable=line-too-long
    def test_user_can_skip_entrance_exam(self, required_content_mock, user_can_skip_entrance_exam_mock):
        required_content_mock.return_value = [str(self.entrance_exam_section_key)]
        user_can_skip_entrance_exam_mock.return_value = True
        staff_details, student_details, _ = self.get_details(
            datetime(2020, 5, 25, tzinfo=timezone.utc)
        )

        # Staff can always access all sequences
        assert len(staff_details.outline.accessible_sequences) == 3

        # Student can access all sequences
        assert len(student_details.outline.accessible_sequences) == 3

    @patch('openedx.core.djangoapps.content.learning_sequences.api.processors.content_gating.EntranceExamConfiguration.user_can_skip_entrance_exam')  # lint-amnesty, pylint: disable=line-too-long
    @patch('openedx.core.djangoapps.content.learning_sequences.api.processors.content_gating.milestones_helpers.get_required_content')  # lint-amnesty, pylint: disable=line-too-long
    def test_user_can_not_skip_entrance_exam(self, required_content_mock, user_can_skip_entrance_exam_mock):
        required_content_mock.return_value = [str(self.entrance_exam_section_key)]
        user_can_skip_entrance_exam_mock.return_value = False
        staff_details, student_details, _ = self.get_details(
            datetime(2020, 5, 25, tzinfo=timezone.utc)
        )
        # Staff can always access all sequences
        assert len(staff_details.outline.accessible_sequences) == 3

        # Student can access only the entrance exam sequence
        assert len(student_details.outline.accessible_sequences) == 1
        assert self.entrance_exam_seq_key in student_details.outline.accessible_sequences
        for key in self.get_sequence_keys(exclude=[self.entrance_exam_seq_key]):
            assert key not in student_details.outline.accessible_sequences


class MilestonesTestCase(OutlineProcessorTestCase, ModuleStoreTestCase):
    """
    Milestones specific outline tests
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        # The UsageKeys we're going to set up for milestone tests.
        cls.section_key = cls.course_key.make_usage_key('chapter', 'ch1')
        cls.open_seq_key = cls.course_key.make_usage_key('sequential', 'open')
        cls.milestone_required_seq_key = cls.course_key.make_usage_key('sequential', 'milestone_required')

        cls.set_sequence_keys([
            cls.open_seq_key,
            cls.milestone_required_seq_key,
        ])

        set_dates_for_course(
            cls.course_key,
            [
                (
                    cls.course_key.make_usage_key('course', 'course'),
                    {'start': datetime(2020, 5, 10, tzinfo=timezone.utc)}
                ),
                (
                    cls.section_key,
                    {'start': datetime(2020, 5, 15, tzinfo=timezone.utc)}
                ),
                (
                    cls.open_seq_key,
                    {'start': datetime(2020, 5, 15, tzinfo=timezone.utc)}
                ),
                (
                    cls.milestone_required_seq_key,
                    {'start': datetime(2020, 5, 15, tzinfo=timezone.utc)}
                ),
            ]
        )

        visibility = VisibilityData(
            hide_from_toc=False,
            visible_to_staff_only=False
        )
        cls.outline = CourseOutlineData(
            course_key=cls.course_key,
            title="User Outline Test Course!",
            published_at=datetime(2020, 5, 20, tzinfo=timezone.utc),
            published_version="5ebece4b69dd593d82fe2020",
            entrance_exam_id=None,
            days_early_for_beta=None,
            self_paced=False,
            course_visibility=CourseVisibility.PRIVATE,
            sections=[
                CourseSectionData(
                    usage_key=cls.section_key,
                    title="Chapter 1",
                    visibility=visibility,
                    sequences=[
                        CourseLearningSequenceData(
                            usage_key=cls.open_seq_key,
                            title='Open Sequence',
                            visibility=visibility
                        ),
                        CourseLearningSequenceData(
                            usage_key=cls.milestone_required_seq_key,
                            title='Milestone Required Sequence',
                            visibility=visibility
                        ),
                    ]
                ),
            ]
        )
        replace_course_outline(cls.outline)

        # Enroll student in the course
        cls.student.courseenrollment_set.create(course_id=cls.course_key, is_active=True, mode="audit")

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            org=self.course_key.org,
            number=self.course_key.course,
            run=self.course_key.run,
            display_name=f"Outline Test Course {self.course_key.run}",
        )

    @patch('openedx.core.djangoapps.content.learning_sequences.api.processors.milestones.milestones_helpers.get_course_content_milestones')  # lint-amnesty, pylint: disable=line-too-long
    def test_user_can_skip_entrance_exam(self, get_course_content_milestones_mock):
        # Only return that there are milestones required for the
        # milestones_required_seq_key usage key
        def get_milestones_side_effect(_course_key, usage_key, _milestone_type, _user_id):
            return usage_key == str(self.milestone_required_seq_key)

        get_course_content_milestones_mock.side_effect = get_milestones_side_effect

        staff_details, student_details, _ = self.get_details(
            datetime(2020, 5, 25, tzinfo=timezone.utc)
        )

        # Staff can always access all sequences
        assert len(staff_details.outline.accessible_sequences) == 2

        # Student can access only the open sequence, but not milestone required sequence
        assert len(student_details.outline.accessible_sequences) == 1
        assert self.open_seq_key in student_details.outline.accessible_sequences
        assert self.milestone_required_seq_key not in student_details.outline.accessible_sequences


class ScheduleTestCase(OutlineProcessorTestCase, ModuleStoreTestCase):
    """
    Schedule-specific Outline tests.

    These aren't super-comprehensive with edge cases yet, partly because it's
    still lacking a few important features (e.g. early beta releases, close-
    after-due policy, etc.), and I'm not sure how we want to structure the
    testing after all that's rolled up.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        # The UsageKeys we're going to set up for date tests.
        cls.section_key = cls.course_key.make_usage_key('chapter', 'ch1')
        cls.seq_before_key = cls.course_key.make_usage_key('sequential', 'seq_before')
        cls.seq_same_key = cls.course_key.make_usage_key('sequential', 'seq_same')
        cls.seq_after_key = cls.course_key.make_usage_key('sequential', 'seq_after')
        cls.seq_inherit_key = cls.course_key.make_usage_key('sequential', 'seq_inherit')
        cls.seq_due_key = cls.course_key.make_usage_key('sequential', 'seq_due')

        cls.set_sequence_keys([
            cls.seq_before_key,
            cls.seq_same_key,
            cls.seq_after_key,
            cls.seq_inherit_key,
            cls.seq_due_key,
        ])

        # Set scheduling information into edx-when for a single Section with
        # sequences starting at various times.
        set_dates_for_course(
            cls.course_key,
            [
                (
                    cls.course_key.make_usage_key('course', 'course'),
                    {'start': datetime(2020, 5, 10, tzinfo=timezone.utc)}
                ),
                (
                    cls.section_key,
                    {'start': datetime(2020, 5, 15, tzinfo=timezone.utc)}
                ),
                # Sequence that starts before containing Section.
                (
                    cls.seq_before_key,
                    {'start': datetime(2020, 5, 14, tzinfo=timezone.utc)}
                ),
                # Sequence starts at same time as containing Section.
                (
                    cls.seq_same_key,
                    {'start': datetime(2020, 5, 15, tzinfo=timezone.utc)}
                ),
                # Sequence starts after containing Section.
                (
                    cls.seq_after_key,
                    {'start': datetime(2020, 5, 16, tzinfo=timezone.utc)}
                ),
                # Sequence should inherit start information from Section.
                (
                    cls.seq_inherit_key,
                    {'start': None}
                ),
                # Sequence should inherit start information from Section, but has a due date set.
                (
                    cls.seq_due_key,
                    {
                        'start': None,
                        'due': datetime(2020, 5, 20, tzinfo=timezone.utc)
                    }
                ),
            ]
        )
        visibility = VisibilityData(
            hide_from_toc=False,
            visible_to_staff_only=False
        )
        cls.outline = CourseOutlineData(
            course_key=cls.course_key,
            title="User Outline Test Course!",
            published_at=datetime(2020, 5, 20, tzinfo=timezone.utc),
            published_version="5ebece4b69dd593d82fe2020",
            entrance_exam_id=None,
            days_early_for_beta=None,
            self_paced=False,
            course_visibility=CourseVisibility.PRIVATE,
            sections=[
                CourseSectionData(
                    usage_key=cls.section_key,
                    title="Section",
                    visibility=visibility,
                    sequences=[
                        CourseLearningSequenceData(
                            usage_key=cls.seq_before_key,
                            title='Before',
                            visibility=visibility
                        ),
                        CourseLearningSequenceData(
                            usage_key=cls.seq_same_key,
                            title='Same', visibility=visibility
                        ),
                        CourseLearningSequenceData(
                            usage_key=cls.seq_after_key,
                            title='After',
                            visibility=visibility
                        ),
                        CourseLearningSequenceData(
                            usage_key=cls.seq_inherit_key,
                            title='Inherit',
                            visibility=visibility
                        ),
                        CourseLearningSequenceData(
                            usage_key=cls.seq_due_key,
                            title='Due',
                            visibility=visibility,
                            inaccessible_after_due=True
                        ),
                    ]
                )
            ],
        )
        replace_course_outline(cls.outline)

        # Enroll student in the course
        cls.student.courseenrollment_set.create(course_id=cls.course_key, is_active=True, mode="audit")
        # Enroll beta tester in the course
        cls.beta_tester.courseenrollment_set.create(course_id=cls.course_key, is_active=True, mode="audit")
        assert user_has_role(cls.beta_tester, CourseBetaTesterRole(cls.course_key))
        assert cls.outline.days_early_for_beta is None

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            org=self.course_key.org,
            number=self.course_key.course,
            run=self.course_key.run,
            display_name=f"Outline Test Course {self.course_key.run}",
        )

    def test_before_course_starts(self):
        staff_details, student_details, beta_tester_details = self.get_details(
            datetime(2020, 5, 9, tzinfo=timezone.utc)
        )
        # Staff can always access all sequences
        assert len(staff_details.outline.accessible_sequences) == 5
        # Student can access nothing
        assert len(student_details.outline.accessible_sequences) == 0
        # Beta tester can access nothing
        assert len(beta_tester_details.outline.accessible_sequences) == 0

        # Everyone can see everything
        assert len(staff_details.outline.sequences) == 5
        assert len(student_details.outline.sequences) == 5
        assert len(beta_tester_details.outline.sequences) == 5

    def test_course_beta_access(self):
        course_outline = attr.evolve(self.outline, days_early_for_beta=6)
        assert course_outline.days_early_for_beta is not None
        replace_course_outline(course_outline)

        staff_details, student_details, beta_tester_details = self.get_details(
            datetime(2020, 5, 9, tzinfo=timezone.utc)
        )
        # Staff can always access all sequences
        assert len(staff_details.outline.accessible_sequences) == 5
        # Student can access nothing
        assert len(student_details.outline.accessible_sequences) == 0
        # Beta tester can access some
        assert len(beta_tester_details.outline.accessible_sequences) == 4

        # Everyone can see everything
        assert len(staff_details.outline.sequences) == 5
        assert len(student_details.outline.sequences) == 5
        assert len(beta_tester_details.outline.sequences) == 5

    def test_before_section_starts(self):
        staff_details, student_details, beta_tester_details = self.get_details(
            datetime(2020, 5, 14, tzinfo=timezone.utc)
        )
        # Staff can always access all sequences
        assert len(staff_details.outline.accessible_sequences) == 5

        # Student can access nothing -- even though one of the sequences is set
        # to start on 2020-05-14, it's not available because the section hasn't
        # started yet.
        assert len(student_details.outline.accessible_sequences) == 0
        before_seq_sched_item_data = student_details.schedule.sequences[self.seq_before_key]
        assert before_seq_sched_item_data.start == datetime(2020, 5, 14, tzinfo=timezone.utc)
        assert before_seq_sched_item_data.effective_start == datetime(2020, 5, 15, tzinfo=timezone.utc)

        # Beta tester can access nothing
        assert len(beta_tester_details.outline.accessible_sequences) == 0

    def test_section_beta_access(self):
        course_outline = attr.evolve(self.outline, days_early_for_beta=1)
        assert course_outline.days_early_for_beta is not None
        replace_course_outline(course_outline)

        staff_details, student_details, beta_tester_details = self.get_details(
            datetime(2020, 5, 14, tzinfo=timezone.utc)
        )
        # Staff can always access all sequences
        assert len(staff_details.outline.accessible_sequences) == 5

        # Student can access nothing -- even though one of the sequences is set
        # to start on 2020-05-14, it's not available because the section hasn't
        # started yet.
        assert len(student_details.outline.accessible_sequences) == 0
        before_seq_sched_item_data = student_details.schedule.sequences[self.seq_before_key]
        assert before_seq_sched_item_data.start == datetime(2020, 5, 14, tzinfo=timezone.utc)
        assert before_seq_sched_item_data.effective_start == datetime(2020, 5, 15, tzinfo=timezone.utc)

        # Beta tester can access some
        assert len(beta_tester_details.outline.accessible_sequences) == 4

    def test_at_section_start(self):
        staff_details, student_details, beta_tester_details = self.get_details(
            datetime(2020, 5, 15, tzinfo=timezone.utc)
        )
        # Staff can always access all sequences
        assert len(staff_details.outline.accessible_sequences) == 5

        # Student can access all sequences except the one that starts after this
        # datetime (self.seq_after_key)
        assert len(student_details.outline.accessible_sequences) == 4
        assert self.seq_after_key not in student_details.outline.accessible_sequences
        for key in self.get_sequence_keys(exclude=[self.seq_after_key]):
            assert key in student_details.outline.accessible_sequences

        # Beta tester can access same as student
        assert len(beta_tester_details.outline.accessible_sequences) == 4

    def test_at_beta_section_start(self):
        course_outline = attr.evolve(self.outline, days_early_for_beta=1)
        assert course_outline.days_early_for_beta is not None
        replace_course_outline(course_outline)

        staff_details, student_details, beta_tester_details = self.get_details(
            datetime(2020, 5, 15, tzinfo=timezone.utc)
        )
        # Staff can always access all sequences
        assert len(staff_details.outline.accessible_sequences) == 5

        # Student can access all sequences except the one that starts after this
        # datetime (self.seq_after_key)
        assert len(student_details.outline.accessible_sequences) == 4
        assert self.seq_after_key not in student_details.outline.accessible_sequences
        for key in self.get_sequence_keys(exclude=[self.seq_after_key]):
            assert key in student_details.outline.accessible_sequences

        # Beta tester can access all
        assert len(beta_tester_details.outline.accessible_sequences) == 5

    def test_is_due_and_before_due(self):
        staff_details, student_details, beta_tester_details = self.get_details(
            datetime(2020, 5, 16, tzinfo=timezone.utc)
        )
        # Staff can always access all sequences
        assert len(staff_details.outline.accessible_sequences) == 5

        # Student can access all sequences including the one that is due in
        # the future (self.seq_due_key)
        assert len(student_details.outline.accessible_sequences) == 5
        assert self.seq_due_key in student_details.outline.accessible_sequences

        seq_due_sched_item_data = student_details.schedule.sequences[self.seq_due_key]
        assert seq_due_sched_item_data.due == datetime(2020, 5, 20, tzinfo=timezone.utc)

        # Beta tester can access some
        assert len(beta_tester_details.outline.accessible_sequences) == 5

    def test_is_due_and_after_due(self):
        staff_details, student_details, beta_tester_details = self.get_details(
            datetime(2020, 5, 21, tzinfo=timezone.utc)
        )
        # Staff can always access all sequences
        assert len(staff_details.outline.accessible_sequences) == 5

        # Student can access all sequences except the one that is due before this
        # datetime (self.seq_due_key)
        assert len(student_details.outline.accessible_sequences) == 4
        assert self.seq_due_key not in student_details.outline.accessible_sequences
        assert self.seq_due_key in student_details.outline.sequences
        for key in self.get_sequence_keys(exclude=[self.seq_due_key]):
            assert key in student_details.outline.accessible_sequences

        # Beta tester can access same as student
        assert len(beta_tester_details.outline.accessible_sequences) == 4


class SelfPacedTestCase(OutlineProcessorTestCase, ModuleStoreTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        # The UsageKeys we're going to set up for date tests.
        cls.section_key = cls.course_key.make_usage_key('chapter', 'ch1')
        cls.seq_one_key = cls.course_key.make_usage_key('sequential', 'seq_one_key')
        cls.seq_two_key = cls.course_key.make_usage_key('sequential', 'seq_two_key')

        cls.set_sequence_keys([
            cls.seq_one_key,
            cls.seq_two_key,
        ])

        # Set scheduling information into edx-when for a single Section with
        # two sequences with due date
        set_dates_for_course(
            cls.course_key,
            [
                (
                    cls.course_key.make_usage_key('course', 'course'),
                    {
                        'start': datetime(2020, 5, 10, tzinfo=timezone.utc),
                    }
                ),
                (
                    cls.section_key,
                    {'start': datetime(2020, 5, 15, tzinfo=timezone.utc)}
                ),
                (
                    cls.seq_one_key,
                    {'due': datetime(2020, 5, 21, tzinfo=timezone.utc)}
                ),
                (
                    cls.seq_two_key,
                    {'due': datetime(2020, 5, 21, tzinfo=timezone.utc)}
                ),
            ]
        )
        visibility = VisibilityData(
            hide_from_toc=False,
            visible_to_staff_only=False
        )
        cls.outline = CourseOutlineData(
            course_key=cls.course_key,
            title="User Outline Test Course!",
            published_at=datetime(2020, 5, 20, tzinfo=timezone.utc),
            published_version="5ebece4b69dd593d82fe2020",
            entrance_exam_id=None,
            days_early_for_beta=None,
            course_visibility=CourseVisibility.PRIVATE,
            sections=[
                CourseSectionData(
                    usage_key=cls.section_key,
                    title="Section",
                    visibility=visibility,
                    sequences=[
                        CourseLearningSequenceData(
                            usage_key=cls.seq_one_key,
                            title='Sequence One',
                            visibility=visibility
                        ),
                        CourseLearningSequenceData(
                            usage_key=cls.seq_two_key,
                            title='Sequence Two',
                            visibility=visibility
                        ),
                    ],
                ),
            ],
            self_paced=True,
        )

        replace_course_outline(cls.outline)

        # Enroll student in the course
        cls.student.courseenrollment_set.create(course_id=cls.course_key, is_active=True, mode="audit")

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            org=self.course_key.org,
            number=self.course_key.course,
            run=self.course_key.run,
            display_name=f"Outline Test Course {self.course_key.run}",
        )

    def test_sequences_accessible_after_due(self):
        at_time = datetime(2020, 5, 22, tzinfo=timezone.utc)  # lint-amnesty, pylint: disable=unused-variable

        staff_details, student_details, _ = self.get_details(
            datetime(2020, 5, 25, tzinfo=timezone.utc)
        )

        # Staff can always access all sequences
        assert len(staff_details.outline.accessible_sequences) == 2

        # In self-paced course, due date of sequences equals to due date of
        # course, so here student should see all sequences, even if their
        # due dates explicitly were set before end of course
        assert len(student_details.outline.accessible_sequences) == 2


class SpecialExamsTestCase(OutlineProcessorTestCase, ModuleStoreTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        # The UsageKeys we're going to set up for date tests.
        cls.section_key = cls.course_key.make_usage_key('chapter', 'ch1')
        cls.seq_practice_exam_key = cls.course_key.make_usage_key('sequential', 'seq_practice_exam_key')
        cls.seq_proctored_exam_key = cls.course_key.make_usage_key('sequential', 'seq_proctored_exam_key')
        cls.seq_timed_exam_key = cls.course_key.make_usage_key('sequential', 'seq_timed_exam_key')
        cls.seq_normal_key = cls.course_key.make_usage_key('sequential', 'seq_normal_key')

        cls.set_sequence_keys([
            cls.seq_practice_exam_key,
            cls.seq_proctored_exam_key,
            cls.seq_timed_exam_key,
            cls.seq_normal_key,
        ])

        # Set scheduling information into edx-when for a single Section with
        # two sequences with due date
        set_dates_for_course(
            cls.course_key,
            [
                (
                    cls.course_key.make_usage_key('course', 'course'),
                    {'start': datetime(2020, 5, 15, tzinfo=timezone.utc)}
                ),
                (
                    cls.section_key,
                    {'start': datetime(2020, 5, 15, tzinfo=timezone.utc)}
                ),
                (
                    cls.seq_practice_exam_key,
                    {'start': datetime(2020, 5, 15, tzinfo=timezone.utc)}
                ),
                (
                    cls.seq_proctored_exam_key,
                    {'start': datetime(2020, 5, 15, tzinfo=timezone.utc)}
                ),
                (
                    cls.seq_timed_exam_key,
                    {'start': datetime(2020, 5, 15, tzinfo=timezone.utc)}
                ),
                (
                    cls.seq_normal_key,
                    {'start': datetime(2020, 5, 15, tzinfo=timezone.utc)}
                ),
            ]
        )
        visibility = VisibilityData(
            hide_from_toc=False,
            visible_to_staff_only=False
        )
        cls.outline = CourseOutlineData(
            course_key=cls.course_key,
            title="User Outline Test Course!",
            published_at=datetime(2020, 5, 20, tzinfo=timezone.utc),
            published_version="5ebece4b69dd593d82fe2020",
            entrance_exam_id=None,
            days_early_for_beta=None,
            course_visibility=CourseVisibility.PRIVATE,
            sections=[
                CourseSectionData(
                    usage_key=cls.section_key,
                    title="Section",
                    visibility=visibility,
                    sequences=[
                        CourseLearningSequenceData(
                            usage_key=cls.seq_practice_exam_key,
                            title='Exam',
                            visibility=visibility,
                            exam=ExamData(is_practice_exam=True)
                        ),
                        CourseLearningSequenceData(
                            usage_key=cls.seq_proctored_exam_key,
                            title='Exam',
                            visibility=visibility,
                            exam=ExamData(is_proctored_enabled=True)
                        ),
                        CourseLearningSequenceData(
                            usage_key=cls.seq_timed_exam_key,
                            title='Exam',
                            visibility=visibility,
                            exam=ExamData(is_time_limited=True)
                        ),
                        CourseLearningSequenceData(
                            usage_key=cls.seq_normal_key,
                            title='Normal',
                            visibility=visibility
                        ),
                    ],
                ),
            ],
            self_paced=True,
        )

        replace_course_outline(cls.outline)

        # Enroll student in the course
        cls.student.courseenrollment_set.create(course_id=cls.course_key, is_active=True, mode="audit")

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            org=self.course_key.org,
            number=self.course_key.course,
            run=self.course_key.run,
            display_name=f"Outline Test Course {self.course_key.run}",
        )

    @patch.dict(settings.FEATURES, {'ENABLE_SPECIAL_EXAMS': True})
    def test_special_exams_enabled_all_sequences_visible(self):
        at_time = datetime(2020, 5, 22, tzinfo=timezone.utc)  # lint-amnesty, pylint: disable=unused-variable

        staff_details, student_details, _ = self.get_details(
            datetime(2020, 5, 25, tzinfo=timezone.utc)
        )

        # Staff can always access all sequences
        assert len(staff_details.outline.accessible_sequences) == 4
        assert len(staff_details.outline.sequences) == 4

        # Ensure the exams are all still present
        assert len(student_details.outline.accessible_sequences) == 4
        assert len(student_details.outline.sequences) == 4

    @patch.dict(settings.FEATURES, {'ENABLE_SPECIAL_EXAMS': False})
    def test_special_exams_disabled_preserves_exam_sequences(self):
        at_time = datetime(2020, 5, 22, tzinfo=timezone.utc)  # lint-amnesty, pylint: disable=unused-variable

        staff_details, student_details, _ = self.get_details(
            datetime(2020, 5, 25, tzinfo=timezone.utc)
        )

        # Staff can always access all sequences
        assert len(staff_details.outline.accessible_sequences) == 4
        assert len(staff_details.outline.sequences) == 4

        # Ensure the exams have been completely removed
        assert len(student_details.outline.accessible_sequences) == 4
        assert len(student_details.outline.sequences) == 4
        for key in self.get_sequence_keys(exclude=[self.seq_normal_key]):
            assert key in student_details.outline.accessible_sequences
            assert key in student_details.outline.sequences

    @patch.dict(settings.FEATURES, {'ENABLE_SPECIAL_EXAMS': True})
    @patch('openedx.core.djangoapps.content.learning_sequences.api.processors.special_exams.get_attempt_status_summary')
    def test_special_exam_attempt_data_in_details(self, mock_get_attempt_status_summary):
        at_time = datetime(2020, 5, 22, tzinfo=timezone.utc)  # lint-amnesty, pylint: disable=unused-variable

        def get_attempt_status_side_effect(user_id, _course_key, usage_key):
            """
            Returns fake data for calls to get_attempt_status_summary for the student
            """
            if user_id != self.student.id:
                raise ProctoredExamNotFoundException

            for sequence_key in self.get_sequence_keys(exclude=[self.seq_normal_key]):
                if usage_key == str(sequence_key):
                    num_fake_attempts = mock_get_attempt_status_summary.call_count % len(self.all_seq_keys)  # lint-amnesty, pylint: disable=unused-variable
                    return {
                        "summary": {
                            "usage_key": usage_key
                        }
                    }

        mock_get_attempt_status_summary.side_effect = get_attempt_status_side_effect

        _, student_details, _ = self.get_details(
            datetime(2020, 5, 25, tzinfo=timezone.utc)
        )

        assert len(student_details.special_exam_attempts.sequences) == 3
        for sequence_key in self.get_sequence_keys(exclude=[self.seq_normal_key]):
            assert sequence_key in student_details.special_exam_attempts.sequences
            attempt_summary = student_details.special_exam_attempts.sequences[sequence_key]
            assert type(attempt_summary) == dict  # lint-amnesty, pylint: disable=unidiomatic-typecheck
            assert attempt_summary["summary"]["usage_key"] == str(sequence_key)

    @patch.dict(settings.FEATURES, {'ENABLE_SPECIAL_EXAMS': False})
    @patch('openedx.core.djangoapps.content.learning_sequences.api.processors.special_exams.get_attempt_status_summary')
    def test_special_exam_attempt_data_empty_when_disabled(self, mock_get_attempt_status_summary):
        at_time = datetime(2020, 5, 22, tzinfo=timezone.utc)  # lint-amnesty, pylint: disable=unused-variable

        _, student_details, _ = self.get_details(
            datetime(2020, 5, 25, tzinfo=timezone.utc)
        )

        # Ensure that no calls are made to get_attempt_status_summary and no data in special_exam_attempts
        assert mock_get_attempt_status_summary.call_count == 0
        assert len(student_details.special_exam_attempts.sequences) == 0

    @override_waffle_flag(EXAMS_IDA, active=True)
    @patch.dict(settings.FEATURES, {'ENABLE_SPECIAL_EXAMS': True})
    @patch('openedx.core.djangoapps.content.learning_sequences.api.processors.special_exams.get_attempt_status_summary')
    def test_special_exam_attempt_data_exams_ida_flag_on(self, mock_get_attempt_status_summary):
        _, student_details, _ = self.get_details(
            datetime(2020, 5, 25, tzinfo=timezone.utc)
        )

        # Ensure that no calls are made to get_attempt_status_summary
        assert mock_get_attempt_status_summary.call_count == 0

    @override_waffle_flag(EXAMS_IDA, active=True)
    @patch.dict(settings.FEATURES, {'ENABLE_SPECIAL_EXAMS': True})
    def test_special_exam_attempt_data_exam_type(self):
        _, student_details, _ = self.get_details(
            datetime(2020, 5, 25, tzinfo=timezone.utc)
        )

        # Ensure that exam type is correct for proctored exam
        assert self.seq_proctored_exam_key in student_details.special_exam_attempts.sequences
        attempt_summary = student_details.special_exam_attempts.sequences[self.seq_proctored_exam_key]
        assert type(attempt_summary) == dict  # lint-amnesty, pylint: disable=unidiomatic-typecheck
        assert attempt_summary["short_description"] == "Proctored Exam"


class VisbilityTestCase(OutlineProcessorTestCase, ModuleStoreTestCase):
    """
    Visibility-related tests.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        # The UsageKeys we're going to set up for date tests.
        cls.normal_section_key = cls.course_key.make_usage_key('chapter', 'normal_section')
        cls.staff_section_key = cls.course_key.make_usage_key('chapter', 'staff_only_section')

        cls.staff_in_normal_key = cls.course_key.make_usage_key('sequential', 'staff_in_normal')
        cls.hide_in_normal_key = cls.course_key.make_usage_key('sequential', 'hide_in_normal')
        cls.due_in_normal_key = cls.course_key.make_usage_key('sequential', 'due_in_normal')
        cls.normal_in_normal_key = cls.course_key.make_usage_key('sequential', 'normal_in_normal')
        cls.normal_in_staff_key = cls.course_key.make_usage_key('sequential', 'normal_in_staff')

        cls.set_sequence_keys([
            cls.staff_in_normal_key,
            cls.hide_in_normal_key,
            cls.due_in_normal_key,
            cls.normal_in_normal_key,
            cls.normal_in_staff_key,
        ])

        v_normal = VisibilityData(
            hide_from_toc=False,
            visible_to_staff_only=False
        )
        v_hide_from_toc = VisibilityData(
            hide_from_toc=True,
            visible_to_staff_only=False
        )
        v_staff_only = VisibilityData(
            hide_from_toc=False,
            visible_to_staff_only=True
        )

        cls.outline = CourseOutlineData(
            course_key=cls.course_key,
            title="User Outline Test Course!",
            published_at=datetime(2020, 5, 20, tzinfo=timezone.utc),
            published_version="5ebece4b69dd593d82fe2020",
            entrance_exam_id=None,
            days_early_for_beta=None,
            course_visibility=CourseVisibility.PRIVATE,
            sections=[
                CourseSectionData(
                    usage_key=cls.normal_section_key,
                    title="Normal Section",
                    visibility=v_normal,
                    sequences=[
                        CourseLearningSequenceData(
                            usage_key=cls.staff_in_normal_key, title='Before', visibility=v_staff_only
                        ),
                        CourseLearningSequenceData(
                            usage_key=cls.hide_in_normal_key, title='Same', visibility=v_hide_from_toc
                        ),
                        CourseLearningSequenceData(
                            usage_key=cls.normal_in_normal_key, title='After', visibility=v_normal
                        ),
                    ]
                ),
                CourseSectionData(
                    usage_key=cls.staff_section_key,
                    title="Staff Only Section",
                    visibility=v_staff_only,
                    sequences=[
                        CourseLearningSequenceData(
                            usage_key=cls.normal_in_staff_key, title='Before', visibility=v_normal
                        ),
                    ]
                )

            ],
            self_paced=False
        )
        replace_course_outline(cls.outline)

        # Enroll student in the course
        cls.student.courseenrollment_set.create(course_id=cls.course_key, is_active=True, mode="audit")

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            org=self.course_key.org,
            number=self.course_key.course,
            run=self.course_key.run,
            display_name=f"Outline Test Course {self.course_key.run}",
        )

    def test_visibility(self):
        at_time = datetime(2020, 5, 21, tzinfo=timezone.utc)  # Exact value doesn't matter  # lint-amnesty, pylint: disable=unused-variable

        staff_details, student_details, _ = self.get_details(
            datetime(2020, 5, 25, tzinfo=timezone.utc)
        )

        # Sections visible
        assert len(staff_details.outline.sections) == 2
        assert len(student_details.outline.sections) == 1

        # Sequences visible
        assert len(staff_details.outline.sequences) == 4
        assert len(student_details.outline.sequences) == 1
        assert self.normal_in_normal_key in student_details.outline.sequences


class SequentialVisibilityTestCase(CacheIsolationTestCase, ModuleStoreTestCase):
    """
    Tests sequentials visibility under different course visibility settings i.e public, public_outline, private
    and different types of users e.g unenrolled, enrolled, anonymous, staff
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.global_staff = UserFactory.create(username='global_staff', email='gstaff@example.com', is_staff=True)
        cls.student = UserFactory.create(username='student', email='student@example.com', is_staff=False)
        cls.unenrolled_student = UserFactory.create(
            username='unenrolled', email='unenrolled@example.com', is_staff=False,
        )
        cls.anonymous_user = AnonymousUser()

        # Handy variable as we almost always need to test with all types of users
        cls.all_users = [cls.global_staff, cls.student, cls.unenrolled_student, cls.anonymous_user]

        cls.course_access_time = datetime(2020, 5, 21, tzinfo=timezone.utc)  # Some random time in past

        # Create course, set it start date to some time in past and attach outline to it
        cls.course_key = CourseKey.from_string("course-v1:OpenEdX+Outline+T0")
        set_dates_for_course(
            cls.course_key, [(cls.course_key.make_usage_key('course', 'course'), {'start': cls.course_access_time})]
        )
        cls.course_outline = CourseOutlineData(
            course_key=cls.course_key,
            title="User Outline Test Course!",
            published_at=datetime(2020, 5, 20, tzinfo=timezone.utc),
            published_version="5ebece4b69dd593d82fe2020",
            entrance_exam_id=None,
            days_early_for_beta=None,
            sections=generate_sections(cls.course_key, [2, 1, 3]),
            self_paced=False,
            course_visibility=CourseVisibility.PRIVATE
        )
        replace_course_outline(cls.course_outline)

        # enroll student into the course
        cls.student.courseenrollment_set.create(course_id=cls.course_key, is_active=True, mode="audit")

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            org=self.course_key.org,
            number=self.course_key.course,
            run=self.course_key.run,
            display_name=f"Outline Test Course {self.course_key.run}",
        )

    @override_waffle_flag(COURSE_ENABLE_UNENROLLED_ACCESS_FLAG, active=True)
    def test_public_course_outline(self):
        """Test that public course outline is the same for everyone."""
        course_outline = attr.evolve(self.course_outline, course_visibility=CourseVisibility.PUBLIC)
        replace_course_outline(course_outline)

        for user in self.all_users:
            with self.subTest(user=user):
                user_course_outline = get_user_course_outline(self.course_key, user, self.course_access_time)

                assert len(user_course_outline.sections) == 3
                assert len(user_course_outline.sequences) == 6
                assert all([(seq.usage_key in user_course_outline.accessible_sequences) for seq in  # lint-amnesty, pylint: disable=use-a-generator
                            user_course_outline.sequences.values()]),\
                    'Sequences should be accessible to all users for a public course'

    @override_waffle_flag(COURSE_ENABLE_UNENROLLED_ACCESS_FLAG, active=True)
    def test_public_outline_course_outline(self):
        """
        Test that a course with public_outline access has same outline for everyone
        except that the links are not accessible for non-enrolled and anonymous user.
        """
        course_outline = attr.evolve(self.course_outline, course_visibility=CourseVisibility.PUBLIC_OUTLINE)
        replace_course_outline(course_outline)

        for user in self.all_users:
            with self.subTest(user=user):
                user_course_outline = get_user_course_outline(self.course_key, user, self.course_access_time)

                assert len(user_course_outline.sections) == 3
                assert len(user_course_outline.sequences) == 6

                is_sequence_accessible = [
                    seq.usage_key in user_course_outline.accessible_sequences
                    for seq in user_course_outline.sequences.values()
                ]

                if user in [self.anonymous_user, self.unenrolled_student]:
                    assert all((not is_accessible) for is_accessible in is_sequence_accessible),\
                        "Sequences shouldn't be accessible to anonymous or " \
                        "non-enrolled students for a public_outline course"
                else:
                    assert all(is_sequence_accessible),\
                        'Sequences should be accessible to enrolled, staff users for a public_outline course'

    @override_waffle_flag(COURSE_ENABLE_UNENROLLED_ACCESS_FLAG, active=True)
    def test_private_course_outline(self):
        """
        Test that the outline of a course with private access is only accessible/visible
        to enrolled user or staff.
        """
        course_outline = attr.evolve(self.course_outline, course_visibility=CourseVisibility.PRIVATE)
        replace_course_outline(course_outline)

        for user in self.all_users:
            with self.subTest(user=user):
                user_course_outline = get_user_course_outline(self.course_key, user, self.course_access_time)

                is_sequence_accessible = [
                    seq.usage_key in user_course_outline.accessible_sequences
                    for seq in user_course_outline.sequences.values()
                ]

                if user in [self.anonymous_user, self.unenrolled_student]:
                    assert (len(user_course_outline.sections) == len(user_course_outline.sequences) == 0),\
                        'No section of a private course should be visible to anonymous or non-enrolled student'
                else:
                    # Enrolled or Staff User
                    assert len(user_course_outline.sections) == 3
                    assert len(user_course_outline.sequences) == 6
                    assert all(is_sequence_accessible),\
                        'Sequences should be accessible to enrolled, staff users for a public_outline course'


@ddt.ddt
class EnrollmentTrackPartitionGroupsTestCase(OutlineProcessorTestCase, ModuleStoreTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    """Tests for enrollment track partitions outline processor that affect outlines"""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.visibility = VisibilityData(
            hide_from_toc=False,
            visible_to_staff_only=False
        )

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            org=self.course_key.org,
            number=self.course_key.course,
            run=self.course_key.run,
            display_name=f"Outline Test Course {self.course_key.run}",
        )

    def _add_course_mode(
        self,
        course_key,
        mode_slug=CourseMode.VERIFIED,
        mode_display_name='Verified Certificate'
    ):
        """
        Add a course mode to the test course_key.
        Args:
            course_key
            mode_slug (str): the slug of the mode to add
            mode_display_name (str): the display name of the mode to add
            upgrade_deadline_expired (bool): whether the upgrade deadline has passed
        """
        signals.post_save.disconnect(update_masters_access_course, sender=CourseMode)
        try:
            CourseMode.objects.create(
                course_id=course_key,
                mode_slug=mode_slug,
                mode_display_name=mode_display_name,
                min_price=50
            )
        finally:
            signals.post_save.connect(update_masters_access_course, sender=CourseMode)

    def _create_and_enroll_learner(self, username, mode, is_staff=False):
        """
        Helper function to create the learner based on the username,
        then enroll the learner into the test course with the specified
        mode.
        Returns created learner
        """
        learner = UserFactory.create(
            username=username, email='{}@example.com'.format(username), is_staff=is_staff
        )
        learner.courseenrollment_set.create(course_id=self.course_key, is_active=True, mode=mode)
        return learner

    def _setup_course_outline_with_sections(
        self,
        course_sections,
        course_start_date=datetime(2021, 3, 26, tzinfo=timezone.utc)
    ):
        """
        Helper function to update the course outline under test with
        the course sections passed in.
        Returns the newly constructed course outline
        """
        set_dates_for_course(
            self.course_key,
            [
                (
                    self.course_key.make_usage_key('course', 'course'),
                    {'start': course_start_date}
                )
            ]
        )

        new_outline = CourseOutlineData(
            course_key=self.course_key,
            title="User Partition Test Course",
            published_at=course_start_date,
            published_version="8ebece4b69dd593d82fe2021",
            sections=course_sections,
            self_paced=False,
            days_early_for_beta=None,
            entrance_exam_id=None,
            course_visibility=CourseVisibility.PRIVATE,
        )

        replace_course_outline(new_outline)

        return new_outline

    def test_roundtrip(self):
        new_outline = self._setup_course_outline_with_sections(
            [
                CourseSectionData(
                    usage_key=self.course_key.make_usage_key('chapter', '0'),
                    title="Section 0",
                    user_partition_groups={
                        ENROLLMENT_TRACK_PARTITION_ID: frozenset([1, 2]),

                        # Just making these up to add more data
                        51: frozenset([100]),
                        52: frozenset([1, 2, 3, 4, 5]),
                    }
                )
            ],
        )

        replace_course_outline(new_outline)
        assert new_outline == get_course_outline(self.course_key)

    @ddt.data(
        (
            None,
            None,
            [CourseMode.VERIFIED],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 1, 'student2': 1}
        ),
        (
            None,
            None,
            [CourseMode.MASTERS],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 1, 'student2': 1}
        ),
        (
            set([2]),
            None,
            [CourseMode.VERIFIED],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 1, 'student2': 0}
        ),
        (
            set([7]),
            None,
            [CourseMode.MASTERS],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 0, 'student2': 1}
        ),
        (
            set([2, 7]),
            None,
            [CourseMode.VERIFIED, CourseMode.MASTERS],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 1, 'student2': 1}
        ),
        (
            set([2]),
            None,
            [CourseMode.VERIFIED, CourseMode.MASTERS],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 1, 'student2': 0}
        ),
        (
            set([7]),
            None,
            [CourseMode.VERIFIED, CourseMode.MASTERS],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 0, 'student2': 1}
        ),
        (
            None,
            set([2]),
            [CourseMode.VERIFIED],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 1, 'student2': 0}
        ),
        (
            None,
            set([7]),
            [CourseMode.MASTERS],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 0, 'student2': 1}
        ),
        (
            None,
            set([2, 7]),
            [CourseMode.VERIFIED, CourseMode.MASTERS],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 1, 'student2': 1}
        ),
        (
            None,
            set([2]),
            [CourseMode.VERIFIED, CourseMode.MASTERS],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 1, 'student2': 0}
        ),
        (
            None,
            set([7]),
            [CourseMode.VERIFIED, CourseMode.MASTERS],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 0, 'student2': 1}
        ),
    )
    @ddt.unpack
    def test_enrollment_track_partition_on_section(
        self,
        section_visible_groups,
        sequence_visible_groups,
        course_modes,
        learners_with_modes,
        expected_values_dict
    ):
        section_user_partition_groups = None
        sequence_user_partition_groups = None
        if section_visible_groups:
            section_user_partition_groups = {
                ENROLLMENT_TRACK_PARTITION_ID: frozenset(section_visible_groups)
            }
        if sequence_visible_groups:
            sequence_user_partition_groups = {
                ENROLLMENT_TRACK_PARTITION_ID: frozenset(sequence_visible_groups)
            }

        self._setup_course_outline_with_sections(
            [
                CourseSectionData(
                    usage_key=self.course_key.make_usage_key('chapter', '0'),
                    title="Section 0",
                    user_partition_groups=section_user_partition_groups,
                    sequences=[
                        CourseLearningSequenceData(
                            usage_key=self.course_key.make_usage_key('subsection', '0'),
                            title='Subsection 0',
                            visibility=self.visibility,
                            user_partition_groups=sequence_user_partition_groups,
                        ),
                    ]
                )
            ]
        )

        for course_mode in course_modes:
            self._add_course_mode(
                self.course_key,
                mode_slug=course_mode,
                mode_display_name=course_mode,
            )

        # Enroll students in the course
        learners_to_verify = set()
        for username, mode in learners_with_modes.items():
            learners_to_verify.add(
                self._create_and_enroll_learner(username, mode)
            )

        check_date = datetime(2021, 3, 27, tzinfo=timezone.utc)

        # Get details
        staff_details, _, beta_tester_details = self.get_details(check_date)

        assert len(staff_details.outline.accessible_sequences) == 1
        assert len(beta_tester_details.outline.accessible_sequences) == 0

        for learner_to_verify in learners_to_verify:
            learner_details = get_user_course_outline_details(self.course_key, learner_to_verify, check_date)
            assert len(learner_details.outline.accessible_sequences) == expected_values_dict[learner_to_verify.username]

    @ddt.data(
        (
            None,
            None,
            [CourseMode.VERIFIED],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 0, 'student2': 0}
        ),
        (
            None,
            None,
            [CourseMode.MASTERS],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 0, 'student2': 0}
        ),
        (
            set([2]),
            None,
            [CourseMode.VERIFIED],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 0, 'student2': 2}
        ),
        (
            set([7]),
            None,
            [CourseMode.MASTERS],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 2, 'student2': 0}
        ),
        (
            set([2, 7]),
            None,
            [CourseMode.VERIFIED, CourseMode.MASTERS],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 0, 'student2': 0}
        ),
        (
            set([2]),
            None,
            [CourseMode.VERIFIED, CourseMode.MASTERS],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 0, 'student2': 2}
        ),
        (
            set([7]),
            None,
            [CourseMode.VERIFIED, CourseMode.MASTERS],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 2, 'student2': 0}
        ),
        (
            None,
            set([2]),
            [CourseMode.VERIFIED],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 0, 'student2': 1}
        ),
        (
            None,
            set([7]),
            [CourseMode.MASTERS],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 1, 'student2': 0}
        ),
        (
            None,
            set([2, 7]),
            [CourseMode.VERIFIED, CourseMode.MASTERS],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 0, 'student2': 0}
        ),
        (
            None,
            set([2]),
            [CourseMode.VERIFIED, CourseMode.MASTERS],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 0, 'student2': 1}
        ),
        (
            None,
            set([7]),
            [CourseMode.VERIFIED, CourseMode.MASTERS],
            {'student1': 'verified', 'student2': 'masters'},
            {'student1': 1, 'student2': 0}
        ),
    )
    @ddt.unpack
    def test_processor_only(
        self,
        section_visible_groups,
        sequence_visible_groups,
        course_modes,
        learners_with_modes,
        expected_values_dict
    ):
        section_user_partition_groups = None
        sequence_user_partition_groups = None
        if section_visible_groups:
            section_user_partition_groups = {
                ENROLLMENT_TRACK_PARTITION_ID: frozenset(section_visible_groups)
            }
        if sequence_visible_groups:
            sequence_user_partition_groups = {
                ENROLLMENT_TRACK_PARTITION_ID: frozenset(sequence_visible_groups)
            }
        full_outline = self._setup_course_outline_with_sections(
            [
                CourseSectionData(
                    usage_key=self.course_key.make_usage_key('chapter', '0'),
                    title="Section 0",
                    user_partition_groups=section_user_partition_groups,
                    sequences=[
                        CourseLearningSequenceData(
                            usage_key=self.course_key.make_usage_key('subsection', '0'),
                            title='Subsection 0',
                            user_partition_groups=sequence_user_partition_groups,
                        ),
                    ]
                )
            ]
        )
        for course_mode in course_modes:
            self._add_course_mode(
                self.course_key,
                mode_slug=course_mode,
                mode_display_name=course_mode,
            )

        # Enroll students in the course
        learners_to_verify = set()
        for username, mode in learners_with_modes.items():
            learners_to_verify.add(
                self._create_and_enroll_learner(username, mode)
            )

        check_date = datetime(2021, 3, 27, tzinfo=timezone.utc)
        for learner_to_verify in learners_to_verify:
            processor = EnrollmentTrackPartitionGroupsOutlineProcessor(
                self.course_key, learner_to_verify, check_date
            )
            processor.load_data(full_outline)
            removed_usage_keys = processor.usage_keys_to_remove(full_outline)
            assert len(removed_usage_keys) == expected_values_dict[learner_to_verify.username]


@ddt.ddt
class CohortPartitionGroupsTestCase(OutlineProcessorTestCase, ModuleStoreTestCase):
    """Tests for cohort partitions outline processor that affects outlines"""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.visibility = VisibilityData(
            hide_from_toc=False,
            visible_to_staff_only=False
        )

    def setUp(self):
        super().setUp()
        self.course_key = CourseKey.from_string("course-v1:OpenEdX+Outlines+2021")
        self.course = CourseFactory.create(
            org=self.course_key.org,
            number=self.course_key.course,
            run=self.course_key.run,
            display_name=f"Cohort User Partition Test Course {self.course_key.run}",
        )

    def _create_and_enroll_learner(self, username, is_staff=False):
        """
        Helper function to create the learner based on the username,
        then enroll the learner into the test course with VERIFIED
        mode.
        Returns the created learner
        """
        learner = UserFactory.create(
            username=username, email='{}@example.com'.format(username), is_staff=is_staff
        )
        learner.courseenrollment_set.create(course_id=self.course_key, is_active=True, mode=CourseMode.VERIFIED)
        return learner

    def _setup_course_outline_with_sections(
        self,
        course_sections,
        course_start_date=datetime(2021, 3, 26, tzinfo=timezone.utc)
    ):
        """
        Helper function to update the course outline under test with
        the course sections passed in.
        Returns the newly constructed course outline
        """
        set_dates_for_course(
            self.course_key,
            [
                (
                    self.course_key.make_usage_key('course', 'course'),
                    {'start': course_start_date}
                )
            ]
        )

        new_outline = CourseOutlineData(
            course_key=self.course_key,
            title="Cohort User Partition Test Course",
            published_at=course_start_date,
            published_version="8ebece4b69dd593d82fe2023",
            sections=course_sections,
            self_paced=False,
            days_early_for_beta=None,
            entrance_exam_id=None,
            course_visibility=CourseVisibility.PRIVATE,
        )

        replace_course_outline(new_outline)

        return new_outline

    @ddt.data(
        (
            None,
            None,
            ['student1', 'student2'],
            {'student1': 1, 'student2': 1}
        ),
        (
            set([1001]),
            None,
            ['student1', 'student2'],
            {'student1': 1, 'student2': 0}
        ),
        (
            set([1002]),
            None,
            ['student1', 'student2'],
            {'student1': 0, 'student2': 1}
        ),
        (
            set([1001, 1002]),
            None,
            ['student1', 'student2'],
            {'student1': 1, 'student2': 1}
        ),
        (
            None,
            set([1001]),
            ['student1', 'student2'],
            {'student1': 1, 'student2': 0}
        ),
        (
            None,
            set([1002]),
            ['student1', 'student2'],
            {'student1': 0, 'student2': 1}
        ),
        (
            None,
            set([1001, 1002]),
            ['student1', 'student2'],
            {'student1': 1, 'student2': 1}
        ),
    )
    @ddt.unpack
    def test_cohort_partition_on_outline(
        self,
        section_visible_groups,
        sequence_visible_groups,
        learners,
        expected_values_dict
    ):

        section_user_partition_groups = None
        sequence_user_partition_groups = None
        if section_visible_groups:
            section_user_partition_groups = {
                1000: frozenset(section_visible_groups)
            }
        if sequence_visible_groups:
            sequence_user_partition_groups = {
                1000: frozenset(sequence_visible_groups)
            }

        CourseCohortsSettings.objects.create(course_id=self.course_key, is_cohorted=True)

        # Enroll students in the course
        learners_to_verify = []
        for username in learners:
            learners_to_verify.append(
                self._create_and_enroll_learner(username)
            )

        # Create cohorts and corresponding GroupPartitions
        cohort_1 = CohortFactory(
            course_id=self.course_key,
            name='Test Cohort 1',
            users=[learners_to_verify[0]]
        )

        CourseUserGroupPartitionGroup(
            course_user_group=cohort_1,
            partition_id=1000,
            group_id=1001
        ).save()

        cohort_2 = CohortFactory(
            course_id=self.course_key,
            name='Test Cohort 2',
            users=[learners_to_verify[1]]
        )

        CourseUserGroupPartitionGroup(
            course_user_group=cohort_2,
            partition_id=1000,
            group_id=1002
        ).save()

        self._setup_course_outline_with_sections(
            [
                CourseSectionData(
                    usage_key=self.course_key.make_usage_key('chapter', '0'),
                    title="Section 0",
                    user_partition_groups=section_user_partition_groups,
                    sequences=[
                        CourseLearningSequenceData(
                            usage_key=self.course_key.make_usage_key('subsection', '0'),
                            title='Subsection 0',
                            visibility=self.visibility,
                            user_partition_groups=sequence_user_partition_groups,
                        ),
                    ]
                )
            ]
        )

        check_date = datetime(2021, 3, 27, tzinfo=timezone.utc)

        for learner_to_verify in learners_to_verify:
            learner_details = get_user_course_outline_details(self.course_key, learner_to_verify, check_date)
            assert len(learner_details.outline.accessible_sequences) == expected_values_dict[learner_to_verify.username]


class ContentErrorTestCase(CacheIsolationTestCase):
    """Test error collection and reporting."""

    def test_errors(self):
        """
        Basic tests for writing and retriving errors.
        """
        course_key = CourseKey.from_string("course-v1:OpenEdX+Outlines+Errors")
        outline = CourseOutlineData(
            course_key=course_key,
            title="Outline Errors Test Course!",
            published_at=datetime(2021, 3, 21, tzinfo=timezone.utc),
            published_version="8ebece4b69dd593d82fe2020",
            sections=[],
            self_paced=False,
            days_early_for_beta=None,
            entrance_exam_id=None,
            course_visibility=CourseVisibility.PRIVATE,
        )
        usage_key_1 = course_key.make_usage_key('sequential', 'seq1')
        usage_key_2 = course_key.make_usage_key('sequential', 'seq2')
        replace_course_outline(
            outline,
            content_errors=[
                # Explicitly set to no usage key.
                ContentErrorData(message="Content is Hard", usage_key=None),

                # Implicitly set usage key
                ContentErrorData("Simple Content Error Description"),

                # Multiple copies of the same usage key
                ContentErrorData(message="Seq1 is wrong", usage_key=usage_key_1),
                ContentErrorData(message="Seq1 is still wrong", usage_key=usage_key_1),

                # Another key
                ContentErrorData(message="Seq2 is also wrong", usage_key=usage_key_2)
            ]
        )
        assert outline == get_course_outline(course_key)

        # Ordering is preserved.
        assert get_content_errors(course_key) == [
            ContentErrorData(message="Content is Hard", usage_key=None),
            ContentErrorData(message="Simple Content Error Description", usage_key=None),
            ContentErrorData(message="Seq1 is wrong", usage_key=usage_key_1),
            ContentErrorData(message="Seq1 is still wrong", usage_key=usage_key_1),
            ContentErrorData(message="Seq2 is also wrong", usage_key=usage_key_2),
        ]

        # Now do it again and make sure updates work as well as inserts
        replace_course_outline(
            outline,
            content_errors=[
                ContentErrorData(message="Content is Hard", usage_key=None),
            ]
        )
        assert get_content_errors(course_key) == [
            ContentErrorData(message="Content is Hard", usage_key=None),
        ]


@patch(
    "openedx.core.djangoapps.content.learning_sequences.api.processors.team_partition_groups.CONTENT_GROUPS_FOR_TEAMS.is_enabled",  # lint-amnesty, pylint: disable=line-too-long
    lambda _: True
)
@skip_unless_lms
class TeamPartitionGroupsTestCase(OutlineProcessorTestCase, ModuleStoreTestCase):
    """Tests for team partitions processor that affects outlines."""

    @classmethod
    def _create_and_enroll_learner(cls, username, is_staff=False):
        """
        Helper function to create the learner based on the username,
        then enroll the learner into the test course with VERIFIED
        mode.
        Returns the created learner
        """
        learner = UserFactory.create(
            username=username, email='{}@example.com'.format(username), is_staff=is_staff
        )
        learner.courseenrollment_set.create(course_id=cls.course_key, is_active=True)
        return learner

    @classmethod
    def _setup_course_outline_with_sections(
        cls,
        course_sections,
        course_start_date=datetime(2021, 3, 26, tzinfo=timezone.utc)
    ):
        """
        Helper function to update the course outline under test with
        the course sections passed in.
        Returns the newly constructed course outline
        """
        set_dates_for_course(
            cls.course_key,
            [
                (
                    cls.course_key.make_usage_key('course', 'course'),
                    {'start': course_start_date}
                )
            ]
        )

        new_outline = CourseOutlineData(
            course_key=cls.course_key,
            title="User Partition Test Course",
            published_at=course_start_date,
            published_version="8ebece4b69dd593d82fe2021",
            sections=course_sections,
            self_paced=False,
            days_early_for_beta=None,
            entrance_exam_id=None,
            course_visibility=CourseVisibility.PRIVATE,
        )

        replace_course_outline(new_outline)

        return new_outline

    @classmethod
    def setUpTestData(cls):
        """
        Set up the test case with the necessary data. Which includes:
        - Two Mocked team sets mirroring Teams Configurations team-sets created in Studio.
        - Two teams, one in each team set.
        - A course outline with two sections, each with a sequence that is visible to a different team via partition
        groups.
        - A student enrolled in the course and added to one of the teams.
        """
        super().setUpTestData()
        cls.visibility = VisibilityData(
            hide_from_toc=False,
            visible_to_staff_only=False
        )
        cls.course_key = CourseKey.from_string("course-v1:OpenEdX+Outlines+TeamPartitions")
        cls.team_sets = [
            MagicMock(name="1st TeamSet", teamset_id=1, user_partition_id=51),
            MagicMock(name="2nd TeamSet", teamset_id=2, user_partition_id=52),
        ]
        cls.student = cls._create_and_enroll_learner("student")
        cls.team_1 = CourseTeamFactory.create(
            name="Team in 1st TeamSet",
            course_id=cls.course_key,
            topic_id=cls.team_sets[0].teamset_id,
        )
        CourseTeamFactory.create(
            name="Team in 2nd TeamSet",
            course_id=cls.course_key,
            topic_id=cls.team_sets[1].teamset_id,
        )
        cls.team_1.add_user(cls.student)
        team_1_sequence_partition_groups = {
            cls.team_sets[0].user_partition_id: frozenset([cls.team_sets[0].teamset_id]),
        }
        team_2_sequence_partition_groups = {
            cls.team_sets[1].user_partition_id: frozenset([cls.team_sets[1].teamset_id]),
        }
        cls.outline = cls._setup_course_outline_with_sections(
            [
                CourseSectionData(
                    usage_key=cls.course_key.make_usage_key('chapter', '1'),
                    title="Section 0",
                    sequences=[
                        CourseLearningSequenceData(
                            usage_key=cls.course_key.make_usage_key('subsection', '1'),
                            title='Subsection 0',
                            visibility=cls.visibility,
                            user_partition_groups=team_1_sequence_partition_groups,
                        ),
                        CourseLearningSequenceData(
                            usage_key=cls.course_key.make_usage_key('subsection', '2'),
                            title='Subsection 1',
                            visibility=cls.visibility,
                            user_partition_groups=team_2_sequence_partition_groups,
                        ),
                    ]
                )
            ]
        )

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            org=self.course_key.org,
            number=self.course_key.course,
            run=self.course_key.run,
            display_name=f"Outline Test Course {self.course_key.run}",
            teams_configuration=TeamsConfig({
                "max_team_size": 4,
                "topics": [
                    {
                        "name": "General Discussion",
                        "id": "general",
                        "description": "General team topic"
                    }
                ]
            })
        )

    @patch("lms.djangoapps.teams.team_partition_scheme.TeamsConfigurationService")
    @patch("openedx.core.lib.teams_config._get_team_sets")
    def test_load_data_in_partition_processor(self, team_sets_mock, team_configuration_service_mock):
        """
        Test that the team partition groups processor loads the data correctly for the given user.

        Expected result:
        - The number of user groups should be equal to the number of teams the user is a part of.
        - The current user groups should contain user_partition_id as the key and an instance of the Group model
          as the value with the teams data.
        - The current user groups should contain the name of the team as the value for the key equal to the
        user_partition_id.
        """
        team_sets_mock.return_value = self.team_sets
        team_configuration_service_mock.return_value.get_teams_configuration.teamsets = self.team_sets
        team_partition_groups_processor = TeamPartitionGroupsOutlineProcessor(
            self.course_key, self.student, datetime.now()
        )

        team_partition_groups_processor.load_data(self.outline)

        assert len(team_partition_groups_processor.current_user_groups) == 1
        assert team_partition_groups_processor.current_user_groups.get(
            self.team_sets[0].user_partition_id
        ) is not None
        assert team_partition_groups_processor.current_user_groups.get(
            self.team_sets[1].user_partition_id
        ) is None
        assert team_partition_groups_processor.current_user_groups.get(
            self.team_sets[0].user_partition_id
        ).name == self.team_1.name

    @patch("lms.djangoapps.teams.team_partition_scheme.TeamsConfigurationService")
    @patch("openedx.core.lib.teams_config._get_team_sets")
    def test_user_not_excluded_by_partition_group(self, team_sets_mock, team_configuration_service_mock):
        """
        Test that the team partition groups processor correctly determines if a user is excluded by the partition
        groups.

        Expected result:
        - The user should not be excluded by the partition groups meaning the method should return False.
        """
        team_sets_mock.return_value = self.team_sets
        team_configuration_service_mock.return_value.get_teams_configuration.teamsets = self.team_sets
        team_partition_groups_processor = TeamPartitionGroupsOutlineProcessor(
            self.course_key, self.student, datetime.now()
        )
        sequence_partition_groups = {
            self.team_sets[0].user_partition_id: frozenset([self.team_sets[0].teamset_id]),
            self.team_sets[1].user_partition_id: frozenset([self.team_sets[1].teamset_id]),
            53: frozenset([100]),
        }
        team_partition_groups_processor.load_data(self.outline)

        # pylint: disable=protected-access
        assert not team_partition_groups_processor._is_user_excluded_by_partition_group(
            sequence_partition_groups
        )
        assert not team_partition_groups_processor._is_user_excluded_by_partition_group([])

    @patch("lms.djangoapps.teams.team_partition_scheme.TeamsConfigurationService")
    @patch("openedx.core.lib.teams_config._get_team_sets")
    def test_user_excluded_by_partition_group(self, team_sets_mock, team_configuration_service_mock):
        """
        Test that the team partition groups processor correctly determines if a user is excluded by the partition
        groups.

        Expected result:
        - The user should not be excluded by the partition groups meaning the method should return False.
        """
        team_sets_mock.return_value = self.team_sets
        team_configuration_service_mock.return_value.get_teams_configuration.teamsets = self.team_sets
        team_partition_groups_processor = TeamPartitionGroupsOutlineProcessor(
            self.course_key, self.student, datetime.now()
        )
        sequence_partition_groups = {
            self.team_sets[1].user_partition_id: frozenset([self.team_sets[1].teamset_id]),
            53: frozenset([100]),
        }
        team_partition_groups_processor.load_data(self.outline)

        # pylint: disable=protected-access
        assert team_partition_groups_processor._is_user_excluded_by_partition_group(sequence_partition_groups)

    @patch("lms.djangoapps.teams.team_partition_scheme.TeamsConfigurationService")
    @patch("openedx.core.lib.teams_config._get_team_sets")
    def test_usage_keys_removed(self, team_sets_mock, team_configuration_service_mock):
        """Test that the team partition groups processor correctly determines the usage keys to remove from the outline.

        Expected result:
        - The method should return the usage keys that are not visible to the user based on the partition groups.
        """
        team_sets_mock.return_value = self.team_sets
        team_configuration_service_mock.return_value.get_teams_configuration.teamsets = self.team_sets
        team_partition_groups_processor = TeamPartitionGroupsOutlineProcessor(
            self.course_key, self.student, datetime.now()
        )
        team_partition_groups_processor.load_data(self.outline)

        assert team_partition_groups_processor.usage_keys_to_remove(self.outline) == {
            self.course_key.make_usage_key('subsection', '2')
        }
