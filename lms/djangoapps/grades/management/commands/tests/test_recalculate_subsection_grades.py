"""
Tests for reset_grades management command.
"""


from datetime import datetime
from unittest.mock import MagicMock, patch

import ddt
from opaque_keys.edx.keys import CourseKey
from pytz import utc

from common.djangoapps.track.event_transaction_utils import get_event_transaction_id
from common.djangoapps.util.date_utils import to_timestamp
from lms.djangoapps.grades.constants import ScoreDatabaseTableEnum
from lms.djangoapps.grades.management.commands import recalculate_subsection_grades
from lms.djangoapps.grades.tests.test_tasks import HasCourseWithProblemsMixin
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)

DATE_FORMAT = "%Y-%m-%d %H:%M"


@ddt.ddt
class TestRecalculateSubsectionGrades(HasCourseWithProblemsMixin, ModuleStoreTestCase):
    """
    Tests recalculate subsection grades management command.
    """

    def setUp(self):
        super().setUp()
        self.command = recalculate_subsection_grades.Command()

    @patch('lms.djangoapps.grades.management.commands.recalculate_subsection_grades.Submission')
    @patch('lms.djangoapps.grades.management.commands.recalculate_subsection_grades.user_by_anonymous_id')
    @patch('lms.djangoapps.grades.management.commands.recalculate_subsection_grades.recalculate_subsection_grade_v3')
    def test_submissions(self, task_mock, id_mock, subs_mock):
        submission = MagicMock()
        submission.student_item = MagicMock(
            student_id="anonymousID",
            course_id=CourseKey.from_string('course-v1:x+y+z'),
            item_id='abc',
        )
        submission.created_at = utc.localize(datetime.strptime('2016-08-23 16:43', DATE_FORMAT))
        subs_mock.objects.filter.return_value = [submission]
        id_mock.return_value = MagicMock()
        id_mock.return_value.id = "ID"
        self._run_command_and_check_output(task_mock, ScoreDatabaseTableEnum.submissions, include_anonymous_id=True)

    @patch('lms.djangoapps.grades.management.commands.recalculate_subsection_grades.StudentModule')
    @patch('lms.djangoapps.grades.management.commands.recalculate_subsection_grades.user_by_anonymous_id')
    @patch('lms.djangoapps.grades.management.commands.recalculate_subsection_grades.recalculate_subsection_grade_v3')
    def test_csm(self, task_mock, id_mock, csm_mock):
        csm_record = MagicMock()
        csm_record.student_id = "ID"
        csm_record.course_id = CourseKey.from_string('course-v1:x+y+z')
        csm_record.module_state_key = "abc"
        csm_record.modified = utc.localize(datetime.strptime('2016-08-23 16:43', DATE_FORMAT))
        csm_mock.objects.filter.return_value = [csm_record]
        id_mock.return_value = MagicMock()
        id_mock.return_value.id = "ID"
        self._run_command_and_check_output(task_mock, ScoreDatabaseTableEnum.courseware_student_module)

    def _run_command_and_check_output(self, task_mock, score_db_table, include_anonymous_id=False):  # lint-amnesty, pylint: disable=missing-function-docstring
        self.command.handle(modified_start='2016-08-25 16:42', modified_end='2018-08-25 16:44')
        kwargs = {
            "user_id": "ID",
            "course_id": 'course-v1:x+y+z',
            "usage_id": 'abc',
            "only_if_higher": False,
            "expected_modified_time": to_timestamp(utc.localize(datetime.strptime('2016-08-23 16:43', DATE_FORMAT))),
            "score_deleted": False,
            "event_transaction_id": str(get_event_transaction_id()),
            "event_transaction_type": 'edx.grades.problem.submitted',
            "score_db_table": score_db_table,
        }

        if include_anonymous_id:
            kwargs['anonymous_user_id'] = 'anonymousID'

        task_mock.apply_async.assert_called_with(kwargs=kwargs)
