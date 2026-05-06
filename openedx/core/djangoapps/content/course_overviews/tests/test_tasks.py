# pylint: disable=missing-module-docstring

from unittest import mock

from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

from ..tasks import enqueue_async_course_overview_update_tasks


class BatchedAsyncCourseOverviewUpdateTests(ModuleStoreTestCase):  # pylint: disable=missing-class-docstring
    def setUp(self):
        super().setUp()
        self.course_1 = CourseFactory.create()
        self.course_2 = CourseFactory.create()
        self.course_3 = CourseFactory.create()

    @mock.patch('openedx.core.djangoapps.content.course_overviews.models.CourseOverview.update_select_courses')
    def test_enqueue_all_courses_in_single_batch(self, mock_update_courses):
        enqueue_async_course_overview_update_tasks(
            course_ids=[],
            force_update=True,
            all_courses=True
        )

        called_args, called_kwargs = mock_update_courses.call_args_list[0]
        assert sorted([self.course_1.id, self.course_2.id, self.course_3.id]) == sorted(called_args[0])
        assert {'force_update': True} == called_kwargs
        assert 1 == mock_update_courses.call_count

    @mock.patch('openedx.core.djangoapps.content.course_overviews.models.CourseOverview.update_select_courses')
    def test_enqueue_specific_courses_in_two_batches(self, mock_update_courses):
        enqueue_async_course_overview_update_tasks(
            course_ids=[str(self.course_1.id), str(self.course_2.id)],
            force_update=True,
            chunk_size=1,
            all_courses=False
        )

        mock_update_courses.assert_has_calls([
            mock.call([self.course_1.id], force_update=True),
            mock.call([self.course_2.id], force_update=True)
        ])
