"""
This module defines the base OutlineProcessor class that is the primary method
of adding new logic that manipulates the Course Outline for a given student.
"""
import logging
from datetime import datetime

from opaque_keys.edx.keys import CourseKey  # lint-amnesty, pylint: disable=unused-import

from openedx.core import types

from ...data import CourseOutlineData

log = logging.getLogger(__name__)


class OutlineProcessor:
    """
    Base class for manipulating the Course Outline.

    You can inherit from this class and extend any of its four main methods:
    __init__, load_data, inaccessible_sequences, usage_keys_to_remove.

    An OutlineProcessor is invoked synchronously during a request for the
    CourseOutline. The steps are:
        * __init__
        * load_data
        * inaccessible_sequences, usage_keys_to_remove (no ordering guarantee)

    Also note that you should not assume any ordering relative to any other
    OutlineProcessor. Once async support works its way fully into Django, we'll
    likely even want to run these in parallel.

    Some outline processors (like ScheduleOutlineProcessor) may choose to have
    additional methods to return specific metadata to feed into
    UserCourseOutlineDetailsData.
    """

    def __init__(self, course_key: CourseKey, user: types.User, at_time: datetime):
        """
        Basic initialization.

        Extend to set your own data attributes, but don't do any real work (e.g.
        database access, expensive computation) here.
        """
        self.course_key = course_key
        self.user = user
        self.at_time = at_time

    def load_data(self, full_course_outline: CourseOutlineData):  # pylint: disable=unused-argument
        """
        Fetch whatever data you need about the course and user here.

        If everything you need is already in the CourseOutlineData, there is no
        need to override this method.

        DO NOT USE MODULESTORE OR BLOCKSTRUCTURES HERE, as the up-front
        performance penalties of those modules are the entire reason this app
        exists. Running this method in your subclass should take no more than
        tens of milliseconds, even on courses with hundreds of learning
        sequences.
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    def inaccessible_sequences(self, full_course_outline: CourseOutlineData):  # pylint: disable=unused-argument
        """
        Return a set/frozenset of Sequence UsageKeys that are not accessible.

        This will not be run for staff users (who can access everything), so
        there is no need to check for staff access here.
        """
        return frozenset()

    def usage_keys_to_remove(self, full_course_outline: CourseOutlineData):  # pylint: disable=unused-argument
        """
        Return a set/frozenset of UsageKeys to remove altogether.

        This will not be run for staff users (who can see everything), so
        there is no need to check for staff access here.
        """
        return frozenset()
