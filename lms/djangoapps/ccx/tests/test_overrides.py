"""
tests for overrides
"""


import datetime
from unittest import mock

import pytz
from ccx_keys.locator import CCXLocator
from django.test.utils import override_settings
from edx_django_utils.cache import RequestCache

from common.djangoapps.student.tests.factories import AdminFactory
from lms.djangoapps.ccx.models import CustomCourseForEdX
from lms.djangoapps.ccx.overrides import override_field_for_ccx
from lms.djangoapps.ccx.tests.utils import flatten, iter_blocks
from lms.djangoapps.courseware.field_overrides import OverrideFieldData
from lms.djangoapps.courseware.tests.test_field_overrides import inject_field_overrides
from lms.djangoapps.courseware.testutils import FieldOverrideTestMixin
from openedx.core.lib.courses import get_course_by_id
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


@override_settings(
    XBLOCK_FIELD_DATA_WRAPPERS=['lms.djangoapps.courseware.field_overrides:OverrideModulestoreFieldData.wrap'],
    MODULESTORE_FIELD_OVERRIDE_PROVIDERS=['lms.djangoapps.ccx.overrides.CustomCoursesForEdxOverrideProvider'],
)
class TestFieldOverrides(FieldOverrideTestMixin, SharedModuleStoreTestCase):
    """
    Make sure field overrides behave in the expected manner.
    """
    @classmethod
    def setUpClass(cls):
        """
        Course is created here and shared by all the class's tests.
        """
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.course.enable_ccx = True

        # Create a course outline
        start = datetime.datetime(2010, 5, 12, 2, 42, tzinfo=pytz.UTC)
        due = datetime.datetime(2010, 7, 7, 0, 0, tzinfo=pytz.UTC)
        chapters = [BlockFactory.create(start=start, parent=cls.course)
                    for _ in range(2)]
        sequentials = flatten([
            [BlockFactory.create(parent=chapter) for _ in range(2)]
            for chapter in chapters])
        verticals = flatten([
            [BlockFactory.create(due=due, parent=sequential) for _ in range(2)]
            for sequential in sequentials])
        blocks = flatten([  # pylint: disable=unused-variable
            [BlockFactory.create(parent=vertical) for _ in range(2)]
            for vertical in verticals])

    def setUp(self):
        """
        Set up tests
        """
        super().setUp()

        self.ccx = ccx = CustomCourseForEdX(
            course_id=self.course.id,
            display_name='Test CCX',
            coach=AdminFactory.create())
        ccx.save()

        patch = mock.patch('lms.djangoapps.ccx.overrides.get_current_ccx')
        self.get_ccx = get_ccx = patch.start()
        get_ccx.return_value = ccx
        self.addCleanup(patch.stop)

        self.addCleanup(RequestCache.clear_all_namespaces)

        inject_field_overrides(iter_blocks(ccx.course), self.course, AdminFactory.create())

        self.ccx_key = CCXLocator.from_course_locator(self.course.id, ccx.id)
        self.ccx_course = get_course_by_id(self.ccx_key, depth=None)

        def cleanup_provider_classes():
            """
            After everything is done, clean up by un-doing the change to the
            OverrideFieldData object that is done during the wrap method.
            """
            OverrideFieldData.provider_classes = None
        self.addCleanup(cleanup_provider_classes)

    def test_override_start(self):
        """
        Test that overriding start date on a chapter works.
        """
        ccx_start = datetime.datetime(2014, 12, 25, 00, 00, tzinfo=pytz.UTC)
        chapter = self.ccx_course.get_children()[0]
        override_field_for_ccx(self.ccx, chapter, 'start', ccx_start)
        assert chapter.start == ccx_start

    def test_override_num_queries_new_field(self):
        """
        Test that for creating new field executed only create query
        """
        ccx_start = datetime.datetime(2014, 12, 25, 00, 00, tzinfo=pytz.UTC)
        chapter = self.ccx_course.get_children()[0]
        # One outer SAVEPOINT/RELEASE SAVEPOINT pair around everything caused by the
        # transaction.atomic decorator wrapping override_field_for_ccx.
        # One SELECT and one INSERT.
        # One inner SAVEPOINT/RELEASE SAVEPOINT pair around the INSERT caused by the
        # transaction.atomic down in Django's get_or_create()/_create_object_from_params().
        with self.assertNumQueries(6):
            override_field_for_ccx(self.ccx, chapter, 'start', ccx_start)

    def test_override_num_queries_update_existing_field(self):
        """
        Test that overriding existing field executed create, fetch and update queries.
        """
        ccx_start = datetime.datetime(2014, 12, 25, 00, 00, tzinfo=pytz.UTC)
        new_ccx_start = datetime.datetime(2015, 12, 25, 00, 00, tzinfo=pytz.UTC)
        chapter = self.ccx_course.get_children()[0]
        override_field_for_ccx(self.ccx, chapter, 'start', ccx_start)
        with self.assertNumQueries(3):
            override_field_for_ccx(self.ccx, chapter, 'start', new_ccx_start)

    def test_override_num_queries_field_value_not_changed(self):
        """
        Test that if value of field does not changed no query execute.
        """
        ccx_start = datetime.datetime(2014, 12, 25, 00, 00, tzinfo=pytz.UTC)
        chapter = self.ccx_course.get_children()[0]
        override_field_for_ccx(self.ccx, chapter, 'start', ccx_start)
        with self.assertNumQueries(2):      # 2 savepoints
            override_field_for_ccx(self.ccx, chapter, 'start', ccx_start)

    def test_overriden_field_access_produces_no_extra_queries(self):
        """
        Test no extra queries when accessing an overriden field more than once.
        """
        ccx_start = datetime.datetime(2014, 12, 25, 00, 00, tzinfo=pytz.UTC)
        chapter = self.ccx_course.get_children()[0]
        # One outer SAVEPOINT/RELEASE SAVEPOINT pair around everything caused by the
        # transaction.atomic decorator wrapping override_field_for_ccx.
        # One SELECT and one INSERT.
        # One inner SAVEPOINT/RELEASE SAVEPOINT pair around the INSERT caused by the
        # transaction.atomic down in Django's get_or_create()/_create_object_from_params().
        with self.assertNumQueries(6):
            override_field_for_ccx(self.ccx, chapter, 'start', ccx_start)

    def test_override_is_inherited(self):
        """
        Test that sequentials inherit overridden start date from chapter.
        """
        ccx_start = datetime.datetime(2014, 12, 25, 00, 00, tzinfo=pytz.UTC)
        chapter = self.ccx_course.get_children()[0]
        override_field_for_ccx(self.ccx, chapter, 'start', ccx_start)
        assert chapter.get_children()[0].start == ccx_start
        assert chapter.get_children()[1].start == ccx_start

    def test_override_is_inherited_even_if_set_in_mooc(self):
        """
        Test that a due date set on a chapter is inherited by grandchildren
        (verticals) even if a due date is set explicitly on grandchildren in
        the mooc.
        """
        ccx_due = datetime.datetime(2015, 1, 1, 00, 00, tzinfo=pytz.UTC)
        chapter = self.ccx_course.get_children()[0]
        chapter.display_name = 'itsme!'
        override_field_for_ccx(self.ccx, chapter, 'due', ccx_due)
        vertical = chapter.get_children()[0].get_children()[0]
        assert vertical.due == ccx_due
