"""
Unit tests for grades models.
"""


import json
from base64 import b64encode
from collections import OrderedDict
from datetime import datetime
from hashlib import sha1
from unittest.mock import patch

import ddt
import pytest
import pytz
from django.db.utils import IntegrityError
from django.test import TestCase
from django.utils.timezone import now
from freezegun import freeze_time
from opaque_keys import InvalidKeyError
from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator

from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.track.event_transaction_utils import get_event_transaction_id, get_event_transaction_type
from lms.djangoapps.grades.constants import GradeOverrideFeatureEnum
from lms.djangoapps.grades.models import (
    BLOCK_RECORD_LIST_VERSION,
    BlockRecord,
    BlockRecordList,
    PersistentCourseGrade,
    PersistentSubsectionGrade,
    PersistentSubsectionGradeOverride,
    VisibleBlocks,
)


class BlockRecordListTestCase(TestCase):
    """
    Verify the behavior of BlockRecordList, particularly around edge cases
    """

    def setUp(self):
        super().setUp()
        self.course_key = CourseLocator(
            org='some_org',
            course='some_course',
            run='some_run'
        )

    def test_empty_block_record_set(self):
        empty_json = '{{"blocks":[],"course_key":"{}","version":{}}}'.format(  # noqa: UP032
            str(self.course_key),
            BLOCK_RECORD_LIST_VERSION,
        )

        brs = BlockRecordList((), self.course_key)
        assert not brs
        assert brs.json_value == empty_json
        assert BlockRecordList.from_json(empty_json) == brs


class GradesModelTestCase(TestCase):
    """
    Base class for common setup of grades model tests.
    """
    def setUp(self):
        super().setUp()
        self.course_key = CourseLocator(
            org='some_org',
            course='some_course',
            run='some_run'
        )
        self.locator_a = BlockUsageLocator(
            course_key=self.course_key,
            block_type='problem',
            block_id='block_id_a'
        )
        self.locator_b = BlockUsageLocator(
            course_key=self.course_key,
            block_type='problem',
            block_id='block_id_b'
        )
        self.record_a = BlockRecord(locator=self.locator_a, weight=1, raw_possible=10, graded=False)
        self.record_b = BlockRecord(locator=self.locator_b, weight=1, raw_possible=10, graded=True)


@ddt.ddt
class BlockRecordTest(GradesModelTestCase):
    """
    Test the BlockRecord model.
    """

    def test_creation(self):
        """
        Tests creation of a BlockRecord.
        """
        weight = 1
        raw_possible = 10
        record = BlockRecord(
            self.locator_a,
            weight,
            raw_possible,
            graded=False,
        )
        assert record.locator == self.locator_a

    @ddt.data(
        (0, 0, "0123456789abcdef", True),
        (1, 10, 'totally_a_real_block_key', False),
        ("BlockRecord is", "a dumb data store", "with no validation", None),
    )
    @ddt.unpack
    def test_serialization(self, weight, raw_possible, block_key, graded):
        """
        Tests serialization of a BlockRecord using the _asdict() method.
        """
        record = BlockRecord(block_key, weight, raw_possible, graded)
        expected = OrderedDict([
            ("locator", block_key),
            ("weight", weight),
            ("raw_possible", raw_possible),
            ("graded", graded),
        ])
        assert expected == record._asdict()


class VisibleBlocksTest(GradesModelTestCase):
    """
    Test the VisibleBlocks model.
    """

    def setUp(self):
        super().setUp()
        self.user_id = 12345

    def _create_block_record_list(self, blocks, user_id=None):
        """
        Creates and returns a BlockRecordList for the given blocks.
        """
        block_record_list = BlockRecordList.from_list(blocks, self.course_key)
        return VisibleBlocks.cached_get_or_create(user_id or self.user_id, block_record_list)

    def test_creation(self):
        """
        Happy path test to ensure basic create functionality works as expected.
        """
        vblocks = self._create_block_record_list([self.record_a])
        list_of_block_dicts = [self.record_a._asdict()]
        for block_dict in list_of_block_dicts:
            block_dict['locator'] = str(block_dict['locator'])  # BlockUsageLocator is not json-serializable
        expected_data = {
            'blocks': [{
                'locator': str(self.record_a.locator),
                'raw_possible': 10,
                'weight': 1,
                'graded': self.record_a.graded,
            }],
            'course_key': str(self.record_a.locator.course_key),
            'version': BLOCK_RECORD_LIST_VERSION,
        }
        expected_json = json.dumps(expected_data, separators=(',', ':'), sort_keys=True)
        expected_hash = b64encode(sha1(expected_json.encode('utf-8')).digest()).decode('utf-8')
        assert expected_data == json.loads(vblocks.blocks_json)
        assert expected_json == vblocks.blocks_json
        assert expected_hash == vblocks.hashed

    def test_ordering_matters(self):
        """
        When creating new vblocks, different ordering of blocks produces
        different records in the database.
        """
        stored_vblocks = self._create_block_record_list([self.record_a, self.record_b])
        repeat_vblocks = self._create_block_record_list([self.record_b, self.record_a])
        same_order_vblocks = self._create_block_record_list([self.record_a, self.record_b])
        new_vblocks = self._create_block_record_list([self.record_b])

        assert stored_vblocks.pk != repeat_vblocks.pk
        assert stored_vblocks.hashed != repeat_vblocks.hashed

        assert stored_vblocks.pk == same_order_vblocks.pk
        assert stored_vblocks.hashed == same_order_vblocks.hashed

        assert stored_vblocks.pk != new_vblocks.pk
        assert stored_vblocks.hashed != new_vblocks.hashed

    def test_blocks_property(self):
        """
        Ensures that, given an array of BlockRecord, creating visible_blocks
        and accessing visible_blocks.blocks yields a copy of the initial array.
        Also, trying to set the blocks property should raise an exception.
        """
        expected_blocks = BlockRecordList.from_list([self.record_a, self.record_b], self.course_key)
        visible_blocks = self._create_block_record_list(expected_blocks)
        assert expected_blocks == visible_blocks.blocks
        with pytest.raises(AttributeError):
            visible_blocks.blocks = expected_blocks


@ddt.ddt
class PersistentSubsectionGradeTest(GradesModelTestCase):
    """
    Test the PersistentSubsectionGrade model.
    """

    def setUp(self):
        super().setUp()
        self.usage_key = BlockUsageLocator(
            course_key=self.course_key,
            block_type='subsection',
            block_id='subsection_12345',
        )
        self.block_records = BlockRecordList([self.record_a, self.record_b], self.course_key)
        self.params = {
            "user_id": 12345,
            "usage_key": self.usage_key,
            "course_version": "deadbeef",
            "subtree_edited_timestamp": "2016-08-01 18:53:24.354741Z",
            "earned_all": 6.0,
            "possible_all": 12.0,
            "earned_graded": 6.0,
            "possible_graded": 8.0,
            "visible_blocks": self.block_records,
            "first_attempted": datetime(2000, 1, 1, 12, 30, 45, tzinfo=pytz.UTC),
        }
        self.user = UserFactory(id=self.params['user_id'])

    @ddt.data('course_version', 'subtree_edited_timestamp')
    def test_optional_fields(self, field):
        del self.params[field]
        PersistentSubsectionGrade.update_or_create_grade(**self.params)

    @ddt.data(
        ("user_id", KeyError),
        ("usage_key", KeyError),
        ("earned_all", IntegrityError),
        ("possible_all", IntegrityError),
        ("earned_graded", IntegrityError),
        ("possible_graded", IntegrityError),
        ("visible_blocks", KeyError),
        ("first_attempted", KeyError),
    )
    @ddt.unpack
    def test_non_optional_fields(self, field, error):
        del self.params[field]
        with pytest.raises(error):
            PersistentSubsectionGrade.update_or_create_grade(**self.params)

    @ddt.data(True, False)
    def test_update_or_create_grade(self, already_created):
        created_grade = PersistentSubsectionGrade.update_or_create_grade(**self.params) if already_created else None

        self.params["earned_all"] = 7
        updated_grade = PersistentSubsectionGrade.update_or_create_grade(**self.params)
        assert updated_grade.earned_all == 7
        if already_created:
            assert created_grade.id == updated_grade.id
            assert created_grade.earned_all == 6

        with self.assertNumQueries(1):
            read_grade = PersistentSubsectionGrade.read_grade(
                user_id=self.params["user_id"],
                usage_key=self.params["usage_key"],
            )
            assert updated_grade == read_grade
            assert read_grade.visible_blocks.blocks == self.block_records

    def test_unattempted(self):
        self.params['first_attempted'] = None
        self.params['earned_all'] = 0.0
        self.params['earned_graded'] = 0.0
        grade = PersistentSubsectionGrade.update_or_create_grade(**self.params)
        assert grade.first_attempted is None
        assert grade.earned_all == 0.0
        assert grade.earned_graded == 0.0

    def test_first_attempted_not_changed_on_update(self):
        PersistentSubsectionGrade.update_or_create_grade(**self.params)
        moment = now()
        grade = PersistentSubsectionGrade.update_or_create_grade(**self.params)
        assert grade.first_attempted < moment

    def test_unattempted_save_does_not_remove_attempt(self):
        PersistentSubsectionGrade.update_or_create_grade(**self.params)
        self.params['first_attempted'] = None
        grade = PersistentSubsectionGrade.update_or_create_grade(**self.params)
        assert isinstance(grade.first_attempted, datetime)
        assert grade.earned_all == 6.0

    def test_update_or_create_event(self):
        with patch('lms.djangoapps.grades.events.tracker') as tracker_mock:
            grade = PersistentSubsectionGrade.update_or_create_grade(**self.params)
        self._assert_tracker_emitted_event(tracker_mock, grade)

    def test_create_event(self):
        with patch('lms.djangoapps.grades.events.tracker') as tracker_mock:
            grade = PersistentSubsectionGrade.update_or_create_grade(**self.params)
        self._assert_tracker_emitted_event(tracker_mock, grade)

    def test_grade_override(self):
        """
        Creating a subsection grade override should NOT change the score values
        of the related PersistentSubsectionGrade.
        """
        grade = PersistentSubsectionGrade.update_or_create_grade(**self.params)
        override = PersistentSubsectionGradeOverride.update_or_create_override(
            requesting_user=self.user,
            subsection_grade_model=grade,
            earned_all_override=0.0,
            earned_graded_override=0.0,
            feature=GradeOverrideFeatureEnum.gradebook,
        )

        grade = PersistentSubsectionGrade.update_or_create_grade(**self.params)
        assert self.params['earned_all'] == grade.earned_all
        assert self.params['earned_graded'] == grade.earned_graded
        history = override.get_history()
        assert 1 == len(list(history))
        assert '+' == list(history)[0].history_type
        # Any score values that aren't specified should use the values from grade as defaults
        assert 0 == override.earned_all_override
        assert 0 == override.earned_graded_override
        assert grade.possible_all == override.possible_all_override
        assert grade.possible_graded == override.possible_graded_override

    def _assert_tracker_emitted_event(self, tracker_mock, grade):
        """
        Helper function to ensure that the mocked event tracker
        was called with the expected info based on the passed grade.
        """
        tracker_mock.emit.assert_called_with(
            'edx.grades.subsection.grade_calculated',
            {
                'user_id': str(grade.user_id),
                'course_id': str(grade.course_id),
                'block_id': str(grade.usage_key),
                'course_version': str(grade.course_version),
                'weighted_total_earned': grade.earned_all,
                'weighted_total_possible': grade.possible_all,
                'weighted_graded_earned': grade.earned_graded,
                'weighted_graded_possible': grade.possible_graded,
                'first_attempted': str(grade.first_attempted),
                'subtree_edited_timestamp': str(grade.subtree_edited_timestamp),
                'event_transaction_id': str(get_event_transaction_id()),
                'event_transaction_type': str(get_event_transaction_type()),
                'visible_blocks_hash': str(grade.visible_blocks_id),
            }
        )

    def test_clear_subsection_grade(self):
        PersistentSubsectionGrade.update_or_create_grade(**self.params)
        deleted = PersistentSubsectionGrade.delete_subsection_grades_for_learner(
            self.user.id, self.course_key
        )
        self.assertEqual(deleted, 1)  # noqa: PT009
        self.assertFalse(PersistentSubsectionGrade.objects.filter(  # noqa: PT009
            user_id=self.user.id, course_id=self.course_key).exists()
        )

    def test_clear_subsection_grade_override(self):
        grade = PersistentSubsectionGrade.update_or_create_grade(**self.params)
        PersistentSubsectionGradeOverride.update_or_create_override(
            requesting_user=self.user,
            subsection_grade_model=grade,
            earned_all_override=0.0,
            earned_graded_override=0.0,
            feature=GradeOverrideFeatureEnum.gradebook,
        )
        deleted = PersistentSubsectionGrade.delete_subsection_grades_for_learner(self.user.id, self.course_key)
        self.assertEqual(deleted, 2)  # noqa: PT009


@ddt.ddt
class PersistentCourseGradesTest(GradesModelTestCase):
    """
    Tests the PersistentCourseGrade model.
    """

    def setUp(self):
        super().setUp()
        self.params = {
            "user_id": 12345,
            "course_id": self.course_key,
            "course_version": "JoeMcEwing",
            "course_edited_timestamp": datetime(
                year=2016,
                month=8,
                day=1,
                hour=18,
                minute=53,
                second=24,
                microsecond=354741,
                tzinfo=pytz.UTC,
            ),
            "percent_grade": 77.7,
            "letter_grade": "Great job",
            "passed": True,
        }

    def test_update(self):
        created_grade = PersistentCourseGrade.update_or_create(**self.params)
        self.params["percent_grade"] = 88.8
        self.params["letter_grade"] = "Better job"
        updated_grade = PersistentCourseGrade.update_or_create(**self.params)
        assert updated_grade.percent_grade == 88.8
        assert updated_grade.letter_grade == 'Better job'
        assert created_grade.id == updated_grade.id

    def test_passed_timestamp(self):
        # When the user has not passed, passed_timestamp is None
        self.params.update({
            'percent_grade': 25.0,
            'letter_grade': '',
            'passed': False,
        })
        grade = PersistentCourseGrade.update_or_create(**self.params)
        assert grade.passed_timestamp is None

        # After the user earns a passing grade, the passed_timestamp is set
        self.params.update({
            'percent_grade': 75.0,
            'letter_grade': 'C',
            'passed': True,
        })
        grade = PersistentCourseGrade.update_or_create(**self.params)
        passed_timestamp = grade.passed_timestamp
        assert grade.letter_grade == 'C'
        assert isinstance(passed_timestamp, datetime)

        # After the user improves their score, the new grade is reflected, but
        # the passed_timestamp remains the same.
        self.params.update({
            'percent_grade': 95.0,
            'letter_grade': 'A',
            'passed': True,
        })
        grade = PersistentCourseGrade.update_or_create(**self.params)
        assert grade.letter_grade == 'A'
        assert grade.passed_timestamp == passed_timestamp

        # If the grade later reverts to a failing grade, passed_timestamp remains the same.
        self.params.update({
            'percent_grade': 20.0,
            'letter_grade': '',
            'passed': False,
        })
        grade = PersistentCourseGrade.update_or_create(**self.params)
        assert grade.letter_grade == ''
        assert grade.passed_timestamp == passed_timestamp

    @patch('lms.djangoapps.grades.signals.signals.COURSE_GRADE_PASSED_FIRST_TIME.send')
    def test_passed_timestamp_is_now(self, mock):
        with freeze_time(now()):
            grade = PersistentCourseGrade.update_or_create(**self.params)
            assert now() == grade.passed_timestamp
            self.assertEqual(mock.call_count, 1)  # noqa: PT009

    def test_create_and_read_grade(self):
        created_grade = PersistentCourseGrade.update_or_create(**self.params)
        read_grade = PersistentCourseGrade.read(self.params["user_id"], self.params["course_id"])
        for param in self.params:
            if param == 'passed':
                continue  # passed/passed_timestamp takes special handling, and is tested separately
            assert self.params[param] == getattr(created_grade, param)
        assert isinstance(created_grade.passed_timestamp, datetime)
        assert created_grade == read_grade

    @ddt.data('course_version', 'course_edited_timestamp')
    def test_optional_fields(self, field):
        del self.params[field]
        grade = PersistentCourseGrade.update_or_create(**self.params)
        assert not getattr(grade, field)

    @ddt.data(
        ("percent_grade", "Not a float at all", ValueError),
        ("percent_grade", None, IntegrityError),
        ("letter_grade", None, IntegrityError),
        ("course_id", "Not a course key at all", InvalidKeyError),
        ("user_id", None, IntegrityError),
        ("grading_policy_hash", None, IntegrityError),
    )
    @ddt.unpack
    def test_update_or_create_with_bad_params(self, param, val, error):
        self.params[param] = val
        with pytest.raises(error):
            PersistentCourseGrade.update_or_create(**self.params)

    def test_grade_does_not_exist(self):
        with pytest.raises(PersistentCourseGrade.DoesNotExist):
            PersistentCourseGrade.read(self.params["user_id"], self.params["course_id"])

    def test_update_or_create_event(self):
        with patch('lms.djangoapps.grades.events.tracker') as tracker_mock:
            grade = PersistentCourseGrade.update_or_create(**self.params)
        self._assert_tracker_emitted_event(tracker_mock, grade)

    def _assert_tracker_emitted_event(self, tracker_mock, grade):
        """
        Helper function to ensure that the mocked event tracker
        was called with the expected info based on the passed grade.
        """
        tracker_mock.emit.assert_called_with(
            'edx.grades.course.grade_calculated',
            {
                'user_id': str(grade.user_id),
                'course_id': str(grade.course_id),
                'course_version': str(grade.course_version),
                'percent_grade': grade.percent_grade,
                'letter_grade': str(grade.letter_grade),
                'course_edited_timestamp': str(grade.course_edited_timestamp),
                'event_transaction_id': str(get_event_transaction_id()),
                'event_transaction_type': str(get_event_transaction_type()),
                'grading_policy_hash': str(grade.grading_policy_hash),
            }
        )

    def test_clear_course_grade(self):
        # create params for another user and another course
        other_user = UserFactory.create()
        other_user_params = {
            **self.params,
            'user_id': other_user.id
        }

        other_course_key = CourseLocator(
            org='some_org',
            course='some_other_course',
            run='some_run'
        )
        user_other_course_params = {
            **self.params,
            'course_id': other_course_key
        }

        # create course grades based on different params
        PersistentCourseGrade.update_or_create(**self.params)
        PersistentCourseGrade.update_or_create(**other_user_params)
        PersistentCourseGrade.update_or_create(**user_other_course_params)

        PersistentCourseGrade.delete_course_grade_for_learner(
            self.course_key, self.params['user_id']
        )

        # assert after deleteing grade for a single user and course
        with self.assertRaises(PersistentCourseGrade.DoesNotExist):  # noqa: PT027
            PersistentCourseGrade.read(self.params['user_id'], self.course_key)

        another_user_grade = PersistentCourseGrade.read(other_user_params['user_id'], self.course_key)
        self.assertIsNotNone(another_user_grade)  # noqa: PT009

        self.assertTrue(PersistentCourseGrade.objects.filter(  # noqa: PT009
            user_id=self.params['user_id'], course_id=other_course_key).exists()
        )
