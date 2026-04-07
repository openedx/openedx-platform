# lint-amnesty, pylint: disable=missing-module-docstring
import datetime
from unittest.mock import patch

import pytest

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.schedules.content_highlights import (
    course_has_highlights_from_store,
    get_all_course_highlights,
    get_next_section_highlights,
    get_week_highlights,
)
from openedx.core.djangoapps.schedules.exceptions import CourseUpdateDoesNotExist
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


@skip_unless_lms
class TestContentHighlights(ModuleStoreTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    def setUp(self):
        super().setUp()
        self._setup_course()
        self._setup_user()

    def _setup_course(self):
        self.course = CourseFactory.create(
            highlights_enabled_for_messaging=True
        )
        self.course_key = self.course.id

    def _setup_user(self):
        self.user = UserFactory.create()
        CourseEnrollment.enroll(self.user, self.course_key)

    def _create_chapter(self, **kwargs):
        BlockFactory.create(
            parent=self.course,
            category='chapter',
            **kwargs
        )

    def test_non_existent_course_raises_exception(self):
        nonexistent_course_key = self.course_key.replace(run='no_such_run')
        with pytest.raises(CourseUpdateDoesNotExist):
            get_week_highlights(self.user, nonexistent_course_key, week_num=1)

    def test_empty_course_raises_exception(self):
        with pytest.raises(CourseUpdateDoesNotExist):
            get_week_highlights(self.user, self.course_key, week_num=1)

    def test_happy_path(self):
        highlights = ['highlights']
        with self.store.bulk_operations(self.course_key):
            self._create_chapter(highlights=highlights)
        assert course_has_highlights_from_store(self.course_key)
        assert get_week_highlights(self.user, self.course_key, week_num=1) == highlights

    def test_get_all_course_highlights(self):
        all_highlights = [["week1highlight1", "week1highlight2"], ["week1highlight1", "week1highlight2"], [], []]
        with self.store.bulk_operations(self.course_key):
            for week_highlights in all_highlights:
                self._create_chapter(highlights=week_highlights)

        assert get_all_course_highlights(self.course_key) == all_highlights

    def test_highlights_disabled_for_messaging(self):
        highlights = ['A test highlight.']
        with self.store.bulk_operations(self.course_key):
            self._create_chapter(highlights=highlights)
            self.course.highlights_enabled_for_messaging = False
            self.store.update_item(self.course, self.user.id)

        assert not course_has_highlights_from_store(self.course_key)

        with pytest.raises(CourseUpdateDoesNotExist):
            get_week_highlights(
                self.user,
                self.course_key,
                week_num=1,
            )

    def test_course_with_no_highlights(self):
        with self.store.bulk_operations(self.course_key):
            self._create_chapter(display_name="Week 1")
            self._create_chapter(display_name="Week 2")

        self.course = self.store.get_course(self.course_key)  # lint-amnesty, pylint: disable=attribute-defined-outside-init
        assert len(self.course.get_children()) == 2

        assert not course_has_highlights_from_store(self.course_key)
        with pytest.raises(CourseUpdateDoesNotExist):
            get_week_highlights(self.user, self.course_key, week_num=1)

    def test_course_with_highlights(self):
        with self.store.bulk_operations(self.course_key):
            self._create_chapter(highlights=['a', 'b', 'á'])
            self._create_chapter(highlights=[])
            self._create_chapter(highlights=['skipped a week'])

        assert course_has_highlights_from_store(self.course_key)

        assert get_week_highlights(self.user, self.course_key, week_num=1) == ['a', 'b', 'á']
        assert get_week_highlights(self.user, self.course_key, week_num=2) == ['skipped a week']
        with pytest.raises(CourseUpdateDoesNotExist):
            get_week_highlights(self.user, self.course_key, week_num=3)

    def test_staff_only(self):
        with self.store.bulk_operations(self.course_key):
            self._create_chapter(
                highlights=["I'm a secret!"],
                visible_to_staff_only=True,
            )

        assert course_has_highlights_from_store(self.course_key)
        with pytest.raises(CourseUpdateDoesNotExist):
            get_week_highlights(self.user, self.course_key, week_num=1)

    @patch('openedx.core.djangoapps.course_date_signals.utils.get_expected_duration')
    def test_get_next_section_highlights(self, mock_duration):
        # All of the dates chosen here are to make things easy and clean to calculate with date offsets
        # It only goes up to 6 days because we are using two_days_ago as our reference point
        # so 6 + 2 = 8 days for the duration of the course
        mock_duration.return_value = datetime.timedelta(days=8)
        today = datetime.datetime.utcnow()
        two_days_ago = today - datetime.timedelta(days=2)
        two_days = today + datetime.timedelta(days=2)
        three_days = today + datetime.timedelta(days=3)
        four_days = today + datetime.timedelta(days=4)
        six_days = today + datetime.timedelta(days=6)
        with self.store.bulk_operations(self.course_key):
            self._create_chapter(  # Week 1
                highlights=['a', 'b', 'á'],
            )
            self._create_chapter(  # Week 2
                highlights=['skipped a week'],
            )
            self._create_chapter(  # Week 3
                highlights=[]
            )
            self._create_chapter(  # Week 4
                highlights=['final week!']
            )

        assert get_next_section_highlights(self.user, self.course_key, two_days_ago, today.date()) ==\
               (['skipped a week'], 2)
        exception_message = 'Next section [{}] has no highlights for {}'.format(  # pylint: disable=unused-variable
            'chapter 3', self.course_key
        )
        with pytest.raises(CourseUpdateDoesNotExist):
            get_next_section_highlights(self.user, self.course_key, two_days_ago, two_days.date())
        # Returns None, None if the target date does not match any due dates. This is caused by
        # making the mock_duration 8 days and there being only 4 chapters so any odd day will
        # fail to match.
        assert get_next_section_highlights(self.user, self.course_key, two_days_ago, three_days.date()) == (None, None)
        assert get_next_section_highlights(self.user, self.course_key, two_days_ago, four_days.date()) ==\
               (['final week!'], 4)
        exception_message = f'Last section was reached. There are no more highlights for {self.course_key}'
        with pytest.raises(CourseUpdateDoesNotExist):
            get_next_section_highlights(self.user, self.course_key, two_days_ago, six_days.date())

    @patch('lms.djangoapps.courseware.block_render.get_block_for_descriptor')
    def test_get_highlights_without_block(self, mock_get_block):
        mock_get_block.return_value = None

        with self.store.bulk_operations(self.course_key):
            self._create_chapter(highlights=['Test highlight'])

        with self.assertRaisesRegex(CourseUpdateDoesNotExist, 'Course block .* not found'):
            get_week_highlights(self.user, self.course_key, 1)

        yesterday = datetime.datetime.utcnow() - datetime.timedelta(days=1)
        today = datetime.datetime.utcnow()
        with self.assertRaisesRegex(CourseUpdateDoesNotExist, 'Course block .* not found'):
            get_next_section_highlights(self.user, self.course_key, yesterday, today.date())
