"""
course_overview api tests
"""
from unittest.mock import patch

from django.http.response import Http404
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.catalog.tests.factories import CourseRunFactory
from openedx.core.djangoapps.content.course_overviews.api import (
    get_course_overview_or_404,
    get_course_overview_or_none,
    get_course_overviews,
    get_course_overviews_from_ids,
    get_pseudo_course_overview,
)
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # pylint: disable=wrong-import-order
)


class TestCourseOverviewsApi(ModuleStoreTestCase):
    """
    TestCourseOverviewsApi tests.
    """

    def setUp(self):
        super().setUp()
        for _ in range(3):
            CourseOverviewFactory.create()

    def test_get_course_overview_or_none_success(self):
        """
        Test for `test_get_course_overview_or_none` function when the overview exists.
        """
        course_overview = CourseOverviewFactory.create()
        retrieved_course_overview = get_course_overview_or_none(course_overview.id)
        assert course_overview.id == retrieved_course_overview.id

    def test_get_course_overview_or_none_missing(self):
        """
        Test for `test_get_course_overview_or_none` function when the overview does not exist.
        """
        course_run_key = CourseKey.from_string('course-v1:coping+with+deletions')
        retrieved_course_overview = get_course_overview_or_none(course_run_key)
        assert retrieved_course_overview is None

    @patch('openedx.core.djangoapps.content.course_overviews.api.CourseOverview.get_from_id')
    def test_get_course_overview_or_none_exception(self, mock_get):
        """
        Test for `get_course_overview_or_none` function when an exception is raised.
        """
        course_run_key = CourseKey.from_string('course-v1:coping+with+exceptions')
        mock_get.side_effect = Exception()
        retrieved_course_overview = get_course_overview_or_none(course_run_key)
        assert retrieved_course_overview is None

    def test_get_course_overview_or_404_success(self):
        """
        Test for `test_get_course_overview_or_404` function when the overview exists.
        """
        course_overview = CourseOverviewFactory.create()
        retrieved_course_overview = get_course_overview_or_404(course_overview.id)
        assert course_overview.id == retrieved_course_overview.id

    def test_get_course_overview_or_404_missing(self):
        """
        Test for `test_get_course_overview_or_404` function when the overview does not exist.
        """
        course_run_key = CourseKey.from_string('course-v1:coping+with+deletions')
        with self.assertRaises(Http404):  # noqa: PT027
            get_course_overview_or_404(course_run_key)

    def test_get_course_overview_from_ids(self):
        """
        Test for `get_course_overviews_from_ids` function.
        """
        course_ids = []
        course_overview_data = CourseOverview.objects.all()
        for course_overview in course_overview_data:
            course_ids.append(course_overview.id)

        results = get_course_overviews_from_ids(course_ids)

        assert len(results) == 3

    def test_get_course_overviews(self):
        """
        get_course_overviews should return the expected CourseOverview data
        in serialized form (a list of dicts)
        """
        course_ids = []
        course_ids.append(str(CourseOverview.objects.first().id))
        course_ids.append(str(CourseOverview.objects.last().id))

        data = get_course_overviews(course_ids)
        assert len(data) == 2
        for overview in data:
            assert overview['id'] in course_ids

        fields = [
            'display_name_with_default',
            'has_started',
            'has_ended',
            'pacing',
        ]
        for field in fields:
            assert field in data[0]

    @patch("openedx.core.djangoapps.content.course_overviews.api.get_course_run_details")
    def test_get_pseudo_course_overview(self, mock_get_course_run_details):
        """
        Test for the `get_pseudo_course_overview` function that creates a temporary course overview for courses that
        have been deleted.
        """
        course_run = CourseRunFactory()
        mock_get_course_run_details.return_value = {
            'title': course_run['title'],
        }
        course_key = CourseKey.from_string(course_run['key'])

        result = get_pseudo_course_overview(course_key)
        assert result.display_name == course_run['title']
        assert result.display_org_with_default == course_key.org
        assert result.certificates_show_before_end
