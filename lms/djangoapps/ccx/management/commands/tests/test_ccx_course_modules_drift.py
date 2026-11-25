"""
Tests for the ccx_course_modules_drift management command.
"""

import json
from io import StringIO

from django.core.management import call_command

from lms.djangoapps.ccx.overrides import override_field_for_ccx
from lms.djangoapps.ccx.tests.utils import CcxTestCase


class CcxCourseModulesDriftTest(CcxTestCase):
    """
    Tests for the ccx_course_modules_drift management command.
    """
    def setUp(self):
        super().setUp()
        self.chapter_0, self.chapter_1 = (str(chapter.location) for chapter in self.chapters)

    def make_ccx_with_structure(self, structure):
        """
        Creates a CCX that shows only the first chapter to learners, with ``structure`` as its v0 list
        """
        ccx = self.make_ccx()
        override_field_for_ccx(ccx, self.chapters[1], 'visible_to_staff_only', True)
        ccx.structure_json = json.dumps(structure)
        ccx.save()
        return ccx

    def test_reports_drift(self):
        """
        Only CCXs with a v0 list that disagrees with what learners see are reported
        """
        self.make_ccx_with_structure([self.chapter_0])
        self.make_ccx_with_structure([self.chapter_0, self.chapter_1])
        self.make_ccx_with_structure(None)

        out = StringIO()
        with self.assertLogs('lms.djangoapps.ccx.management.commands.ccx_course_modules_drift', 'WARNING') as logs:
            call_command('ccx_course_modules_drift', stdout=out)

        assert 'checked=2 drifted=1 errors=0' in out.getvalue()
        assert len(logs.records) == 1
        assert self.chapter_1 in logs.output[0]
