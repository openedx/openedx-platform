# lint-amnesty, pylint: disable=missing-module-docstring
from unittest.mock import patch

import ddt
from crum import set_current_request

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangolib.testing.utils import get_mock_request
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory

from ..course_data import CourseData
from ..course_grade import ZeroCourseGrade
from ..course_grade_factory import CourseGradeFactory
from .base import GradeTestBase
from .utils import answer_problem


@ddt.ddt
class ZeroGradeTest(GradeTestBase):
    """
    Tests ZeroCourseGrade (and, implicitly, ZeroSubsectionGrade)
    functionality.
    """

    @ddt.data(True, False)
    def test_zero(self, assume_zero_enabled):
        """
        Creates a ZeroCourseGrade and ensures it's empty.
        """
        course_data = CourseData(self.request.user, structure=self.course_structure)
        chapter_grades = ZeroCourseGrade(self.request.user, course_data).chapter_grades
        for chapter in chapter_grades:
            for section in chapter_grades[chapter]['sections']:
                for score in section.problem_scores.values():
                    assert score.earned == 0
                    assert score.first_attempted is None
                assert section.all_total.earned == 0

    @ddt.data(True, False)
    def test_zero_null_scores(self, assume_zero_enabled):
        """
        Creates a zero course grade and ensures that null scores aren't included in the section problem scores.
        """
        with patch('lms.djangoapps.grades.subsection_grade.get_score', return_value=None):
            course_data = CourseData(self.request.user, structure=self.course_structure)
            chapter_grades = ZeroCourseGrade(self.request.user, course_data).chapter_grades
            for chapter in chapter_grades:
                assert {} != chapter_grades[chapter]['sections']
                for section in chapter_grades[chapter]['sections']:
                    assert {} == section.problem_scores


class TestScoreForModule(SharedModuleStoreTestCase):
    """
    Test the method that calculates the score for a given block based on the
    cumulative scores of its children. This test class uses a hard-coded block
    hierarchy with scores as follows:
                                                a
                                       +--------+--------+
                                       b                 c
                        +--------------+-----------+     |
                        d              e           f     g
                     +-----+     +-----+-----+     |     |
                     h     i     j     k     l     m     n
                   (2/5) (3/5) (0/1)   -   (1/3)   -   (3/10)

    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        with cls.store.bulk_operations(cls.course.id):
            cls.a = BlockFactory.create(parent=cls.course, category="chapter", display_name="a")
            cls.b = BlockFactory.create(parent=cls.a, category="sequential", display_name="b")
            cls.c = BlockFactory.create(parent=cls.a, category="sequential", display_name="c")
            cls.d = BlockFactory.create(parent=cls.b, category="vertical", display_name="d")
            cls.e = BlockFactory.create(parent=cls.b, category="vertical", display_name="e")
            cls.f = BlockFactory.create(parent=cls.b, category="vertical", display_name="f")
            cls.g = BlockFactory.create(parent=cls.c, category="vertical", display_name="g")
            cls.h = BlockFactory.create(parent=cls.d, category="problem", display_name="h")
            cls.i = BlockFactory.create(parent=cls.d, category="problem", display_name="i")
            cls.j = BlockFactory.create(parent=cls.e, category="problem", display_name="j")
            cls.k = BlockFactory.create(parent=cls.e, category="html", display_name="k")
            cls.l = BlockFactory.create(parent=cls.e, category="problem", display_name="l")
            cls.m = BlockFactory.create(parent=cls.f, category="html", display_name="m")
            cls.n = BlockFactory.create(parent=cls.g, category="problem", display_name="n")

        cls.request = get_mock_request(UserFactory())
        CourseEnrollment.enroll(cls.request.user, cls.course.id)

        answer_problem(cls.course, cls.request, cls.h, score=2, max_value=5)
        answer_problem(cls.course, cls.request, cls.i, score=3, max_value=5)
        answer_problem(cls.course, cls.request, cls.j, score=0, max_value=1)
        answer_problem(cls.course, cls.request, cls.l, score=1, max_value=3)
        answer_problem(cls.course, cls.request, cls.n, score=3, max_value=10)

        cls.course_grade = CourseGradeFactory().read(cls.request.user, cls.course)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        set_current_request(None)

    def test_score_chapter(self):
        earned, possible = self.course_grade.score_for_block(self.a.location)
        assert earned == 9
        assert possible == 24

    def test_score_section_many_leaves(self):
        earned, possible = self.course_grade.score_for_block(self.b.location)
        assert earned == 6
        assert possible == 14

    def test_score_section_one_leaf(self):
        earned, possible = self.course_grade.score_for_block(self.c.location)
        assert earned == 3
        assert possible == 10

    def test_score_vertical_two_leaves(self):
        earned, possible = self.course_grade.score_for_block(self.d.location)
        assert earned == 5
        assert possible == 10

    def test_score_vertical_two_leaves_one_unscored(self):
        earned, possible = self.course_grade.score_for_block(self.e.location)
        assert earned == 1
        assert possible == 4

    def test_score_vertical_no_score(self):
        earned, possible = self.course_grade.score_for_block(self.f.location)
        assert earned == 0
        assert possible == 0

    def test_score_vertical_one_leaf(self):
        earned, possible = self.course_grade.score_for_block(self.g.location)
        assert earned == 3
        assert possible == 10

    def test_score_leaf(self):
        earned, possible = self.course_grade.score_for_block(self.h.location)
        assert earned == 2
        assert possible == 5

    def test_score_leaf_no_score(self):
        earned, possible = self.course_grade.score_for_block(self.m.location)
        assert earned == 0
        assert possible == 0
