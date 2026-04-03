"""
tests for the models
"""


import json
from datetime import datetime, timedelta

import ddt
from pytz import utc

from common.djangoapps.student.roles import CourseCcxCoachRole
from common.djangoapps.student.tests.factories import AdminFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, check_mongo_calls

from ..overrides import override_field_for_ccx
from .factories import CcxFactory


@ddt.ddt
class TestCCX(ModuleStoreTestCase):
    """Unit tests for the CustomCourseForEdX model
    """
    def setUp(self):
        """common setup for all tests"""
        super().setUp()
        self.course = CourseFactory.create()
        self.coach = AdminFactory.create()
        role = CourseCcxCoachRole(self.course.id)
        role.add_users(self.coach)
        self.ccx = CcxFactory(course_id=self.course.id, coach=self.coach)

    def set_ccx_override(self, field, value):
        """Create a field override for the test CCX on <field> with <value>"""
        override_field_for_ccx(self.ccx, self.course, field, value)

    def test_ccx_course_is_correct_course(self):
        """verify that the course property of a ccx returns the right course"""
        expected = self.course
        actual = self.ccx.course
        assert expected.location == actual.location

    def test_ccx_course_caching(self):
        """verify that caching the propery works to limit queries"""
        with check_mongo_calls(2):
            # these statements are used entirely to demonstrate the
            # instance-level caching of these values on CCX objects. The
            # check_mongo_calls context is the point here.
            self.ccx.course  # pylint: disable=pointless-statement
        with check_mongo_calls(0):
            self.ccx.course  # pylint: disable=pointless-statement

    def test_ccx_start_is_correct(self):
        """verify that the start datetime for a ccx is correctly retrieved

        Note that after setting the start field override microseconds are
        truncated, so we can't do a direct comparison between before and after.
        For this reason we test the difference between and make sure it is less
        than one second.
        """
        expected = datetime.now(utc)
        self.set_ccx_override('start', expected)
        actual = self.ccx.start
        diff = expected - actual
        assert abs(diff.total_seconds()) < 1

    def test_ccx_start_caching(self):
        """verify that caching the start property works to limit queries"""
        now = datetime.now(utc)
        self.set_ccx_override('start', now)
        with check_mongo_calls(2):
            # these statements are used entirely to demonstrate the
            # instance-level caching of these values on CCX objects. The
            # check_mongo_calls context is the point here.
            self.ccx.start  # pylint: disable=pointless-statement
        with check_mongo_calls(0):
            self.ccx.start  # pylint: disable=pointless-statement

    def test_ccx_due_without_override(self):
        """verify that due returns None when the field has not been set"""
        actual = self.ccx.due
        assert actual is None

    def test_ccx_due_is_correct(self):
        """verify that the due datetime for a ccx is correctly retrieved"""
        expected = datetime.now(utc)
        self.set_ccx_override('due', expected)
        actual = self.ccx.due
        diff = expected - actual
        assert abs(diff.total_seconds()) < 1

    def test_ccx_due_caching(self):
        """verify that caching the due property works to limit queries"""
        expected = datetime.now(utc)
        self.set_ccx_override('due', expected)
        with check_mongo_calls(2):
            # these statements are used entirely to demonstrate the
            # instance-level caching of these values on CCX objects. The
            # check_mongo_calls context is the point here.
            self.ccx.due  # pylint: disable=pointless-statement
        with check_mongo_calls(0):
            self.ccx.due  # pylint: disable=pointless-statement

    def test_ccx_has_started(self):
        """verify that a ccx marked as starting yesterday has started"""
        now = datetime.now(utc)
        delta = timedelta(1)
        then = now - delta
        self.set_ccx_override('start', then)
        assert self.ccx.has_started()

    def test_ccx_has_not_started(self):
        """verify that a ccx marked as starting tomorrow has not started"""
        now = datetime.now(utc)
        delta = timedelta(1)
        then = now + delta
        self.set_ccx_override('start', then)
        assert not self.ccx.has_started()

    def test_ccx_has_ended(self):
        """verify that a ccx that has a due date in the past has ended"""
        now = datetime.now(utc)
        delta = timedelta(1)
        then = now - delta
        self.set_ccx_override('due', then)
        assert self.ccx.has_ended()

    def test_ccx_has_not_ended(self):
        """verify that a ccx that has a due date in the future has not eneded
        """
        now = datetime.now(utc)
        delta = timedelta(1)
        then = now + delta
        self.set_ccx_override('due', then)
        assert not self.ccx.has_ended()

    def test_ccx_without_due_date_has_not_ended(self):
        """verify that a ccx without a due date has not ended"""
        assert not self.ccx.has_ended()

    def test_ccx_max_student_enrollment_correct(self):
        """
        Verify the override value for max_student_enrollments_allowed
        """
        expected = 200
        self.set_ccx_override('max_student_enrollments_allowed', expected)
        actual = self.ccx.max_student_enrollments_allowed
        assert expected == actual

    def test_structure_json_default_empty(self):
        """
        By default structure_json does not contain anything
        """
        assert self.ccx.structure_json is None
        assert self.ccx.structure is None

    def test_structure_json(self):
        """
        Test a json stored in the structure_json
        """
        dummy_struct = [
            "block-v1:Organization+CN101+CR-FALL15+type@chapter+block@Unit_4",
            "block-v1:Organization+CN101+CR-FALL15+type@chapter+block@Unit_5",
            "block-v1:Organization+CN101+CR-FALL15+type@chapter+block@Unit_11"
        ]
        json_struct = json.dumps(dummy_struct)
        ccx = CcxFactory(
            course_id=self.course.id,
            coach=self.coach,
            structure_json=json_struct
        )
        assert ccx.structure_json == json_struct
        assert ccx.structure == dummy_struct

    def test_locator_property(self):
        """
        Verify that the locator helper property returns a correct CCXLocator
        """
        locator = self.ccx.locator
        assert self.ccx.id == int(locator.ccx)
