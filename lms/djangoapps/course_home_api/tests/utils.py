"""
Base classes or util functions for use in Course Home API tests
"""

from datetime import datetime

from cms.djangoapps.contentstore.outlines import update_outline_from_modulestore
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from lms.djangoapps.courseware.tests.helpers import MasqueradeMixin
from lms.djangoapps.verify_student.models import VerificationDeadline
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


@skip_unless_lms
class BaseCourseHomeTests(ModuleStoreTestCase, MasqueradeMixin):
    """
    Base class for Course Home API tests.

    Creates a course to
    """
    def setUp(self):
        super().setUp()

        self.course = CourseFactory.create(
            start=datetime(2020, 1, 1),
            end=datetime(2028, 1, 1),
            enrollment_start=datetime(2020, 1, 1),
            enrollment_end=datetime(2028, 1, 1),
            emit_signals=True,
            modulestore=self.store,
        )
        chapter = BlockFactory(parent=self.course, category='chapter')
        BlockFactory(parent=chapter, category='sequential')

        CourseModeFactory(course_id=self.course.id, mode_slug=CourseMode.AUDIT)
        CourseModeFactory(
            course_id=self.course.id,
            mode_slug=CourseMode.VERIFIED,
            expiration_datetime=datetime(2028, 1, 1),
            min_price=149,
            sku='ABCD1234',
        )
        VerificationDeadline.objects.create(course_key=self.course.id, deadline=datetime(2028, 1, 1))

        CourseOverviewFactory.create(run='1T2020')
        update_outline_from_modulestore(self.course.id)

        self.staff_user = self.user
        self.user, password = self.create_non_staff_user()
        self.client.login(username=self.user.username, password=password)

    def switch_to_staff(self):
        self.user = self.staff_user
        self.client.login(username=self.user.username, password=self.user_password)
