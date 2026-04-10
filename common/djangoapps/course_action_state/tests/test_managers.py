# pylint: disable=invalid-name, attribute-defined-outside-init
"""
Tests for basic common operations related to Course Action State managers
"""

from collections import namedtuple

import pytest
from ddt import data, ddt
from django.test import TestCase
from opaque_keys.edx.locations import CourseLocator

from common.djangoapps.course_action_state.managers import CourseActionStateItemNotFoundError
from common.djangoapps.course_action_state.models import CourseRerunState

# Sequence of Action models to be tested with ddt.
COURSE_ACTION_STATES = (CourseRerunState, )


class TestCourseActionStateManagerBase(TestCase):
    """
    Base class for testing Course Action State Managers.
    """
    def setUp(self):
        super().setUp()
        self.course_key = CourseLocator("test_org", "test_course_num", "test_run")


@ddt
class TestCourseActionStateManager(TestCourseActionStateManagerBase):
    """
    Test class for testing the CourseActionStateManager.
    """
    @data(*COURSE_ACTION_STATES)
    def test_update_state_allow_not_found_is_false(self, action_class):
        with pytest.raises(CourseActionStateItemNotFoundError):
            action_class.objects.update_state(self.course_key, "fake_state", allow_not_found=False)

    @data(*COURSE_ACTION_STATES)
    def test_update_state_allow_not_found(self, action_class):
        action_class.objects.update_state(self.course_key, "initial_state", allow_not_found=True)
        assert action_class.objects.find_first(course_key=self.course_key) is not None

    @data(*COURSE_ACTION_STATES)
    def test_delete(self, action_class):
        obj = action_class.objects.update_state(self.course_key, "initial_state", allow_not_found=True)
        action_class.objects.delete(obj.id)
        with pytest.raises(CourseActionStateItemNotFoundError):
            action_class.objects.find_first(course_key=self.course_key)


@ddt
class TestCourseActionUIStateManager(TestCourseActionStateManagerBase):
    """
    Test class for testing the CourseActionUIStateManager.
    """
    def init_course_action_states(self, action_class):
        """
        Creates course action state entries with different states for the given action model class.
        Creates both displayable (should_display=True) and non-displayable (should_display=False) entries.
        """
        def create_course_states(starting_course_num, ending_course_num, state, should_display=True):
            """
            Creates a list of course state tuples by creating unique course locators with course-numbers
            from starting_course_num to ending_course_num.
            """
            CourseState = namedtuple('CourseState', 'course_key, state, should_display')
            return [
                CourseState(CourseLocator("org", "course", "run" + str(num)), state, should_display)
                for num in range(starting_course_num, ending_course_num)
            ]

        NUM_COURSES_WITH_STATE1 = 3
        NUM_COURSES_WITH_STATE2 = 3
        NUM_COURSES_WITH_STATE3 = 3
        NUM_COURSES_NON_DISPLAYABLE = 3

        # courses with state1 and should_display=True
        self.courses_with_state1 = create_course_states(
            0,
            NUM_COURSES_WITH_STATE1,
            'state1'
        )
        # courses with state2 and should_display=True
        self.courses_with_state2 = create_course_states(
            NUM_COURSES_WITH_STATE1,
            NUM_COURSES_WITH_STATE1 + NUM_COURSES_WITH_STATE2,
            'state2'
        )
        # courses with state3 and should_display=True
        self.courses_with_state3 = create_course_states(
            NUM_COURSES_WITH_STATE1 + NUM_COURSES_WITH_STATE2,
            NUM_COURSES_WITH_STATE1 + NUM_COURSES_WITH_STATE2 + NUM_COURSES_WITH_STATE3,
            'state3'
        )
        # all courses with should_display=True
        self.course_actions_displayable_states = (
            self.courses_with_state1 + self.courses_with_state2 + self.courses_with_state3
        )
        # courses with state3 and should_display=False
        self.courses_with_state3_non_displayable = create_course_states(
            NUM_COURSES_WITH_STATE1 + NUM_COURSES_WITH_STATE2 + NUM_COURSES_WITH_STATE3,
            NUM_COURSES_WITH_STATE1 + NUM_COURSES_WITH_STATE2 + NUM_COURSES_WITH_STATE3 + NUM_COURSES_NON_DISPLAYABLE,
            'state3',
            should_display=False,
        )

        # create course action states for all courses
        for CourseState in self.course_actions_displayable_states + self.courses_with_state3_non_displayable:
            action_class.objects.update_state(
                CourseState.course_key,
                CourseState.state,
                should_display=CourseState.should_display,
                allow_not_found=True
            )

    def assertCourseActionStatesEqual(self, expected, found):
        """Asserts that the set of course keys in the expected state equal those that are found"""
        self.assertSetEqual(  # noqa: PT009
            {course_action_state.course_key for course_action_state in expected},
            {course_action_state.course_key for course_action_state in found})

    @data(*COURSE_ACTION_STATES)
    def test_find_all_for_display(self, action_class):
        self.init_course_action_states(action_class)
        self.assertCourseActionStatesEqual(
            self.course_actions_displayable_states,
            action_class.objects.find_all(should_display=True),
        )

    @data(*COURSE_ACTION_STATES)
    def test_find_all_for_display_filter_exclude(self, action_class):
        self.init_course_action_states(action_class)
        for course_action_state, filter_state, exclude_state in (
            (self.courses_with_state1, 'state1', None),  # filter for state1
            (self.courses_with_state2, 'state2', None),  # filter for state2
            (self.courses_with_state2 + self.courses_with_state3, None, 'state1'),  # exclude state1
            (self.courses_with_state1 + self.courses_with_state3, None, 'state2'),  # exclude state2
            (self.courses_with_state1, 'state1', 'state2'),  # filter for state1, exclude state2
            ([], 'state1', 'state1'),  # filter for state1, exclude state1
        ):
            self.assertCourseActionStatesEqual(
                course_action_state,
                action_class.objects.find_all(
                    exclude_args=({'state': exclude_state} if exclude_state else None),
                    should_display=True,
                    **({'state': filter_state} if filter_state else {})
                )
            )

    def test_kwargs_in_update_state(self):
        destination_course_key = CourseLocator("org", "course", "run")
        source_course_key = CourseLocator("source_org", "source_course", "source_run")
        CourseRerunState.objects.update_state(
            course_key=destination_course_key,
            new_state='state1',
            allow_not_found=True,
            source_course_key=source_course_key,
        )
        found_action_state = CourseRerunState.objects.find_first(course_key=destination_course_key)
        assert source_course_key == found_action_state.source_course_key
