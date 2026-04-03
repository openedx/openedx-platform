"""  # lint-amnesty, pylint: disable=cyclic-import
Test utils for CCX
"""


import datetime

import pytz
from django.conf import settings

from common.djangoapps.student.roles import CourseCcxCoachRole, CourseInstructorRole, CourseStaffRole
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.ccx.overrides import override_field_for_ccx
from lms.djangoapps.ccx.tests.factories import CcxFactory
from openedx.core.djangoapps.ace_common.tests.mixins import EmailTemplateTagMixin
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


class CcxTestCase(EmailTemplateTagMixin, SharedModuleStoreTestCase):
    """
    General test class to be used in other CCX tests classes.

    It creates a course that can be used as master course for CCXs.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = course = CourseFactory.create(enable_ccx=True)

        # Create a course outline
        cls.mooc_start = start = datetime.datetime(
            2010, 5, 12, 2, 42, tzinfo=pytz.UTC
        )
        cls.mooc_due = due = datetime.datetime(
            2010, 7, 7, 0, 0, tzinfo=pytz.UTC
        )

        cls.chapters = [
            BlockFactory.create(start=start, parent=course) for _ in range(2)
        ]
        cls.sequentials = flatten([
            [
                BlockFactory.create(parent=chapter) for _ in range(2)
            ] for chapter in cls.chapters
        ])
        cls.verticals = flatten([
            [
                BlockFactory.create(
                    start=start, due=due, parent=sequential, graded=True, format='Homework', category='vertical'
                ) for _ in range(2)
            ] for sequential in cls.sequentials
        ])

        # Trying to wrap the whole thing in a bulk operation fails because it
        # doesn't find the parents. But we can at least wrap this part...
        with cls.store.bulk_operations(course.id, emit_signals=False):
            blocks = flatten([  # pylint: disable=unused-variable
                [
                    BlockFactory.create(parent=vertical) for _ in range(2)
                ] for vertical in cls.verticals
            ])

    def setUp(self):
        """
        Set up tests
        """
        super().setUp()
        # Create instructor account
        self.coach = UserFactory.create()
        # create an instance of modulestore
        self.mstore = modulestore()

    def make_staff(self):
        """
        create staff user.
        """
        staff = UserFactory.create()
        role = CourseStaffRole(self.course.id)
        role.add_users(staff)

        return staff

    def make_instructor(self):
        """
        create instructor user.
        """
        instructor = UserFactory.create()
        role = CourseInstructorRole(self.course.id)
        role.add_users(instructor)

        return instructor

    def make_coach(self):
        """
        create coach user
        """
        role = CourseCcxCoachRole(self.course.id)
        role.add_users(self.coach)

    def make_ccx(self, max_students_allowed=settings.CCX_MAX_STUDENTS_ALLOWED):
        """
        create ccx
        """
        ccx = CcxFactory(course_id=self.course.id, coach=self.coach)
        override_field_for_ccx(ccx, self.course, 'max_student_enrollments_allowed', max_students_allowed)
        return ccx

    def get_outbox(self):
        """
        get fake outbox
        """
        from django.core import mail
        return mail.outbox


def flatten(seq):
    """
    For [[1, 2], [3, 4]] returns [1, 2, 3, 4].  Does not recurse.
    """
    return [x for sub in seq for x in sub]


def iter_blocks(course):
    """
    Returns an iterator over all of the blocks in a course.
    """
    def visit(block):
        """ get child blocks """
        yield block
        for child in block.get_children():
            yield from visit(child)
    return visit(course)
