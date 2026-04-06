# lint-amnesty, pylint: disable=missing-module-docstring
from __future__ import annotations

import logging
from collections import defaultdict  # lint-amnesty, pylint: disable=unused-import
from datetime import datetime, timedelta

from edx_when.api import get_dates_for_course
from opaque_keys.edx.keys import CourseKey, UsageKey  # lint-amnesty, pylint: disable=unused-import

from common.djangoapps.student.auth import user_has_role
from common.djangoapps.student.roles import CourseBetaTesterRole
from openedx.core import types

from ...data import ScheduleData, ScheduleItemData, UserCourseOutlineData
from .base import OutlineProcessor

log = logging.getLogger(__name__)


class ScheduleOutlineProcessor(OutlineProcessor):
    """
    Responsible for applying all start/due/end date outline processing.

    We never hide the existence of a piece of content because of start or due
    dates. Content may be inaccessible because it has yet to be released or the
    exam has closed, but students are never prevented from knowing the content
    exists based on the start and due date information.

    This processor depends on edx-when to get customized due date information
    for users and content.

    Things we don't handle yet:
    * Beta test users
    * Things that are made inaccessible after they're due.
    """

    def __init__(self, course_key: CourseKey, user: types.User, at_time: datetime):
        super().__init__(course_key, user, at_time)
        self.dates = None
        self.keys_to_schedule_fields: dict[UsageKey, dict[str, datetime]] = defaultdict(dict)
        self._course_start = None
        self._course_end = None
        self._is_beta_tester = False

    def load_data(self, full_course_outline):
        """
        Pull dates information from edx-when.

        Return data format: (usage_key, 'due'): datetime.datetime(2019, 12, 11, 15, 0, tzinfo=<UTC>)
        """
        self.dates = get_dates_for_course(
            self.course_key, self.user, subsection_and_higher_only=True,
            published_version=full_course_outline.published_version
        )

        for (usage_key, field_name), date in self.dates.items():
            self.keys_to_schedule_fields[usage_key][field_name] = date

        course_usage_key = self.course_key.make_usage_key('course', 'course')
        self._course_start = self.keys_to_schedule_fields[course_usage_key].get('start')
        self._course_end = self.keys_to_schedule_fields[course_usage_key].get('end')
        self._is_beta_tester = user_has_role(self.user, CourseBetaTesterRole(self.course_key))

    def inaccessible_sequences(self, full_course_outline):
        """
        This might include Sequences that have not yet started, or Sequences
        for exams that have closed. If a Section has not started, all of its
        Sequences are inaccessible, regardless of the individual Sequence start
        dates.
        """
        if self._is_beta_tester and full_course_outline.days_early_for_beta is not None:
            start_offset = timedelta(days=full_course_outline.days_early_for_beta)
        else:
            start_offset = timedelta(days=0)

        # If the course hasn't started at all, then everything is inaccessible.
        if self._course_start is None or self.at_time < self._course_start - start_offset:
            return set(full_course_outline.sequences)

        self_paced = full_course_outline.self_paced

        inaccessible = set()
        for section in full_course_outline.sections:
            section_start = self.keys_to_schedule_fields[section.usage_key].get('start')
            if section_start is not None:
                section_start -= start_offset
            if section_start and self.at_time < section_start:
                # If the section hasn't started yet, all the sequences it
                # contains are inaccessible, regardless of the start value for
                # those sequences.
                inaccessible |= {seq.usage_key for seq in section.sequences}
            else:
                for seq in section.sequences:
                    seq_start = self.keys_to_schedule_fields[seq.usage_key].get('start')
                    if seq_start is not None:
                        seq_start -= start_offset
                    if seq_start and self.at_time < seq_start:
                        inaccessible.add(seq.usage_key)
                        continue

                    # if course is self-paced, all sequences with enabled hide after due
                    # must have due date equal to course's end date
                    if self_paced:
                        seq_due = self._course_end
                    else:
                        seq_due = self.keys_to_schedule_fields[seq.usage_key].get('due')

                    if seq.inaccessible_after_due:
                        if seq_due and self.at_time > seq_due:
                            inaccessible.add(seq.usage_key)
                            continue

        return inaccessible

    def schedule_data(self, pruned_course_outline: UserCourseOutlineData) -> ScheduleData:
        """
        Return supplementary scheduling information for this outline.

        Be careful to pass in a UserCourseOutlineData–i.e. an outline that has
        already been pruned to what a user is allowed to see. That way, we can
        use this to make sure that we're not returning data about
        LearningSequences that the user can't see because it was hidden by a
        different OutlineProcessor.
        """
        def _effective_start(*dates):
            specified_dates = [date for date in dates if date is not None]
            return max(specified_dates) if specified_dates else None

        pruned_section_keys = {section.usage_key for section in pruned_course_outline.sections}  # lint-amnesty, pylint: disable=unused-variable
        course_usage_key = self.course_key.make_usage_key('course', 'course')
        course_start = self.keys_to_schedule_fields[course_usage_key].get('start')
        course_end = self.keys_to_schedule_fields[course_usage_key].get('end')
        days_early_for_beta = pruned_course_outline.days_early_for_beta

        if days_early_for_beta is not None and self._is_beta_tester:
            start_offset = timedelta(days=days_early_for_beta)
        else:
            start_offset = timedelta(days=0)
        if course_start is not None:
            course_start -= start_offset

        sequences = {}
        sections = {}
        for section in pruned_course_outline.sections:
            section_dict = self.keys_to_schedule_fields[section.usage_key]
            section_start = section_dict.get('start')
            if section_start is not None:
                section_start -= start_offset
            section_effective_start = _effective_start(course_start, section_start)
            section_due = section_dict.get('due')

            sections[section.usage_key] = ScheduleItemData(
                usage_key=section.usage_key,
                start=section_start,
                effective_start=section_effective_start,
                due=section_due,
            )

            for seq in section.sequences:
                seq_dict = self.keys_to_schedule_fields[seq.usage_key]
                seq_start = seq_dict.get('start')
                if seq_start is not None:
                    seq_start -= start_offset
                seq_due = seq_dict.get('due')
                sequences[seq.usage_key] = ScheduleItemData(
                    usage_key=seq.usage_key,
                    start=seq_start,
                    effective_start=_effective_start(section_effective_start, seq_start),
                    due=seq_due,
                )

        return ScheduleData(
            course_start=course_start,
            course_end=course_end,
            sections=sections,
            sequences=sequences,
        )
