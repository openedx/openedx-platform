"""
This module provides date summary blocks for the Course Info
page. Each block gives information about a particular
course-run-specific date which will be displayed to the user.
"""


import datetime

import crum
from babel.dates import format_timedelta
from django.conf import settings
from django.utils.functional import cached_property
from django.utils.translation import get_language, gettext_lazy, to_locale
from django.utils.translation import gettext as _
from lazy import lazy
from pytz import utc

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.certificates.api import can_show_certificate_available_date_field, get_active_web_certificate
from lms.djangoapps.courseware.utils import can_show_verified_upgrade, verified_upgrade_deadline_link
from lms.djangoapps.verify_student.models import VerificationDeadline
from lms.djangoapps.verify_student.services import IDVerificationService
from openedx.core.djangolib.markup import HTML
from openedx.features.course_duration_limits.access import get_user_course_expiration_date
from openedx.features.course_experience import RELATIVE_DATES_FLAG

from .context_processor import user_timezone_locale_prefs


class DateSummary:
    """Base class for all date summary blocks."""

    # A consistent representation of the current time.
    _current_time = None

    @property
    def current_time(self):
        """
        Returns a consistent current time.
        """
        if self._current_time is None:
            self._current_time = datetime.datetime.now(utc)
        return self._current_time

    @property
    def css_class(self):
        """
        The CSS class of this summary. Indicates the type of information
        this summary block contains, and its urgency.
        """
        return ''

    @property
    def date_type(self):
        return 'event'

    @property
    def title(self):
        """The title of this summary."""
        return ''

    @property
    def title_html(self):
        """The title as html for this summary."""
        return ''

    @property
    def description(self):
        """The detail text displayed by this summary."""
        return ''

    @property
    def extra_info(self):
        """Extra detail to display as a tooltip."""
        return None

    @property
    def date(self):
        """This summary's date."""
        return None

    @property
    def date_format(self):
        """
        The format to display this date in. By default, displays like Jan
        01, 2015.
        """
        return '%b %d, %Y'

    @property
    def link(self):
        """The location to link to for more information."""
        return ''

    @property
    def link_text(self):
        """The text of the link."""
        return ''

    def __init__(self, course, user, course_id=None):
        self.course = course
        self.user = user
        self.course_id = course_id or self.course.id

    @property
    def relative_datestring(self):
        """
        Return this block's date in a human-readable format. If the date
        is None, returns the empty string.
        """
        if self.date is None:
            return ''
        locale = to_locale(get_language())
        delta = self.date - self.current_time
        try:
            relative_date = format_timedelta(delta, locale=locale)
        # Babel doesn't have translations for Esperanto, so we get
        # a KeyError when testing translations with
        # ?preview-lang=eo. This should not happen with any other
        # languages. See https://github.com/python-babel/babel/issues/107
        except KeyError:
            relative_date = format_timedelta(delta)
        date_has_passed = delta.days < 0
        # Translators: 'absolute' is a date such as "Jan 01,
        # 2020". 'relative' is a fuzzy description of the time until
        # 'absolute'. For example, 'absolute' might be "Jan 01, 2020",
        # and if today were December 5th, 2020, 'relative' would be "1
        # month".
        date_format = _("{relative} ago - {absolute}") if date_has_passed else _("in {relative} - {absolute}")  # pylint: disable=redefined-outer-name
        return date_format.format(
            relative=relative_date,
            absolute='{date}',
        )

    @lazy
    def is_allowed(self):
        """
        Whether or not this summary block is applicable or active for its course.

        For example, a DateSummary might only make sense for a self-paced course, and
        you could restrict it here.

        You should not make time-sensitive checks here. That sort of thing belongs in
        is_enabled.
        """
        return True

    @property
    def is_enabled(self):
        """
        Whether or not this summary block should be shown.

        By default, the summary is only shown if its date is in the
        future.
        """
        return (
            self.date is not None and
            self.current_time.date() <= self.date.date()
        )

    def deadline_has_passed(self):
        """
        Return True if a deadline (the date) exists, and has already passed.
        Returns False otherwise.
        """
        deadline = self.date
        return deadline is not None and deadline <= self.current_time

    @property
    def time_remaining_string(self):
        """
        Returns the time remaining as a localized string.
        """
        locale = to_locale(get_language())
        return format_timedelta(self.date - self.current_time, locale=locale)

    def date_html(self, date_format='shortDate'):  # pylint: disable=redefined-outer-name
        """
        Returns a representation of the date as HTML.

        Note: this returns a span that will be localized on the client.
        """
        user_timezone = user_timezone_locale_prefs(crum.get_current_request())['user_timezone']
        return HTML(
            '<span class="date localized-datetime" data-format="{date_format}" data-datetime="{date_time}"'
            ' data-timezone="{user_timezone}" data-language="{user_language}">'
            '</span>'
        ).format(
            date_format=date_format,
            date_time=self.date,
            user_timezone=user_timezone,
            user_language=get_language(),
        )

    @property
    def long_date_html(self):
        """
        Returns a long representation of the date as HTML.

        Note: this returns a span that will be localized on the client.
        """
        return self.date_html(date_format='shortDate')

    @property
    def short_time_html(self):
        """
        Returns a short representation of the time as HTML.

        Note: this returns a span that will be localized on the client.
        """
        return self.date_html(date_format='shortTime')

    def __repr__(self):
        return 'DateSummary: "{title}" {date} is_enabled={is_enabled}'.format(  # noqa: UP032
            title=self.title,
            date=self.date,
            is_enabled=self.is_enabled
        )


class TodaysDate(DateSummary):
    """
    Displays today's date.
    """
    css_class = 'todays-date'
    is_enabled = True

    # The date is shown in the title, no need to display it again.
    def get_context(self):
        context = super().get_context()  # pylint: disable=no-member, super-with-arguments
        context['date'] = ''
        return context

    @property
    def date(self):
        return self.current_time

    @property
    def date_type(self):
        return 'todays-date'

    @property
    def title(self):
        return 'current_datetime'


class CourseStartDate(DateSummary):
    """
    Displays the start date of the course.
    """
    css_class = 'start-date'

    @property
    def date(self):
        if not self.course.self_paced:
            return self.course.start
        else:
            enrollment = CourseEnrollment.get_enrollment(self.user, self.course_id)
            return max(enrollment.created, self.course.start) if enrollment else self.course.start

    @property
    def date_type(self):
        return 'course-start-date'

    @property
    def title(self):
        enrollment = CourseEnrollment.get_enrollment(self.user, self.course_id)
        if self.course.self_paced and enrollment and self.course.start and enrollment.created > self.course.start:
            return gettext_lazy('Enrollment Date')
        return gettext_lazy('Course starts')


class CourseEndDate(DateSummary):
    """
    Displays the end date of the course.
    """
    css_class = 'end-date'
    title = gettext_lazy('Course ends')
    is_enabled = True

    @property
    def description(self):
        """
        Returns a description for what experience changes a learner encounters when the course end date passes.
        Note that this currently contains 4 scenarios:
            1. End date is in the future and learner is enrolled in a certificate earning mode
            2. End date is in the future and learner is not enrolled at all or not enrolled
                in a certificate earning mode
            3. End date is in the past
            4. End date does not exist (and now neither does the description)
        """
        if self.date and self.current_time <= self.date:
            mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course_id)
            if is_active and CourseMode.is_eligible_for_certificate(mode):
                return _('After this date, the course will be archived, which means you can review the '
                         'course content but can no longer participate in graded assignments or work towards earning '
                         'a certificate.')
            else:
                return _('After the course ends, the course content will be archived and no longer active.')
        elif self.date:
            return _('This course is archived, which means you can review course content but it is no longer active.')
        else:
            return ''

    @property
    def date(self):
        """
        Returns the course end date, if applicable.
        For self-paced courses using Personalized Learner Schedules, the end date is only displayed
        if it is within 365 days.
        """
        if self.course.self_paced and RELATIVE_DATES_FLAG.is_enabled(self.course_id):
            one_year = datetime.timedelta(days=365)
            if self.course.end and self.course.end < (self.current_time + one_year):
                return self.course.end
            return None

        return self.course.end

    @property
    def date_type(self):
        return 'course-end-date'


class CourseAssignmentDate(DateSummary):
    """
    Displays due dates for homework assignments with a link to the homework
    assignment if the link is provided.
    """
    css_class = 'assignment'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.assignment_date = None
        self.assignment_link = ''
        self.assignment_title = None
        self.assignment_title_html = None
        self.first_component_block_id = None
        self.contains_gated_content = False
        self.complete = None
        self.past_due = None
        self._extra_info = None

    @property
    def date(self):
        return self.assignment_date

    @date.setter
    def date(self, date):
        self.assignment_date = date

    @property
    def date_type(self):
        return 'assignment-due-date'

    @property
    def link(self):
        return self.assignment_link

    @property
    def extra_info(self):
        return self._extra_info

    @link.setter
    def link(self, link):
        self.assignment_link = link

    @property
    def title(self):
        return self.assignment_title

    @property
    def title_html(self):
        return self.assignment_title_html

    def set_title(self, title, link=None):
        """ Used to set the title_html and title properties for the assignment date block """
        if link:
            self.assignment_title_html = HTML(
                '<a href="{assignment_link}">{assignment_title}</a>'
            ).format(assignment_link=link, assignment_title=title)
        self.assignment_title = title


class CourseExpiredDate(DateSummary):
    """
    Displays the course expiration date for Audit learners (if enabled)
    """
    css_class = 'course-expired'

    @property
    def date(self):
        return get_user_course_expiration_date(self.user, self.course)

    @property
    def date_type(self):
        return 'course-expired-date'

    @property
    def description(self):
        return _('You lose all access to this course, including your progress.')

    @property
    def title(self):
        return _('Audit Access Expires')

    @lazy
    def is_allowed(self):
        return RELATIVE_DATES_FLAG.is_enabled(self.course.id)


class CertificateAvailableDate(DateSummary):
    """
    Displays the certificate available date of the course.
    """
    css_class = 'certificate-available-date'
    title = gettext_lazy('Certificate Available')

    @lazy
    def is_allowed(self):
        return (
            can_show_certificate_available_date_field(self.course) and
            self.has_certificate_modes and
            get_active_web_certificate(self.course)
        )

    @property
    def description(self):
        return _('Day certificates will become available for passing verified learners.')

    @property
    def date(self):
        return self.course.certificate_available_date

    @property
    def date_type(self):
        return 'certificate-available-date'

    @property
    def has_certificate_modes(self):
        return any(
            mode.slug for mode in CourseMode.modes_for_course(
                course_id=self.course.id, include_expired=True
            ) if mode.slug != CourseMode.AUDIT
        )


class VerifiedUpgradeDeadlineDate(DateSummary):
    """
    Displays the date before which learners must upgrade to the
    Verified track.
    """
    css_class = 'verified-upgrade-deadline'
    link_text = gettext_lazy('Upgrade to Verified Certificate')

    @property
    def link(self):
        return verified_upgrade_deadline_link(self.user, self.course, self.course_id)

    @cached_property
    def enrollment(self):
        return CourseEnrollment.get_enrollment(self.user, self.course_id)

    @lazy
    def is_allowed(self):
        return can_show_verified_upgrade(self.user, self.enrollment, self.course)

    @lazy
    def date(self):  # pylint: disable=invalid-overridden-method
        if self.enrollment:
            return self.enrollment.upgrade_deadline
        else:
            return None

    @property
    def date_type(self):
        return 'verified-upgrade-deadline'

    @property
    def title(self):
        dynamic_deadline = self._dynamic_deadline()
        if dynamic_deadline is not None:
            return _('Upgrade to Verified Certificate')

        return _('Verification Upgrade Deadline')

    def _dynamic_deadline(self):
        if not self.enrollment:
            return None

        return self.enrollment.dynamic_upgrade_deadline

    @property
    def description(self):
        dynamic_deadline = self._dynamic_deadline()
        if dynamic_deadline is not None:
            return _('Don\'t miss the opportunity to highlight your new knowledge and skills by earning a verified'
                     ' certificate.')

        return _('You are still eligible to upgrade to a Verified Certificate! '
                 'Pursue it to highlight the knowledge and skills you gain in this course.')

    @property
    def relative_datestring(self):
        dynamic_deadline = self._dynamic_deadline()
        if dynamic_deadline is None:
            return super().relative_datestring

        if self.date is None or self.deadline_has_passed():
            return ' '

        # Translators: This describes the time by which the user
        # should upgrade to the verified track. 'date' will be
        # their personalized verified upgrade deadline formatted
        # according to their locale.
        return _('by {date}')


class VerificationDeadlineDate(DateSummary):
    """
    Displays the date by which the user must complete the verification
    process.
    """
    is_enabled = True

    @property
    def css_class(self):
        base_state = 'verification-deadline'
        if self.deadline_has_passed():
            return base_state + '-passed'
        elif self.must_retry():
            return base_state + '-retry'
        else:
            return base_state + '-upcoming'

    @property
    def link_text(self):
        return self.link_table[self.css_class][0]

    @property
    def link(self):
        return self.link_table[self.css_class][1]

    @property
    def link_table(self):
        """Maps verification state to a tuple of link text and location."""
        return {
            'verification-deadline-passed': (_('Learn More'), ''),
            'verification-deadline-retry': (
                _('Retry Verification'),
                IDVerificationService.get_verify_location(),
            ),
            'verification-deadline-upcoming': (
                _('Verify My Identity'),
                IDVerificationService.get_verify_location(self.course_id),
            )
        }

    @property
    def title(self):
        if self.deadline_has_passed():
            return _('Missed Verification Deadline')
        return _('Verification Deadline')

    @property
    def description(self):
        if self.deadline_has_passed():
            return _(
                "Unfortunately you missed this course's deadline for"
                " a successful verification."
            )
        return _(
            "You must successfully complete verification before"
            " this date to qualify for a Verified Certificate."
        )

    @lazy
    def date(self):  # pylint: disable=invalid-overridden-method
        return VerificationDeadline.deadline_for_course(self.course_id)

    @property
    def date_type(self):
        return 'verification-deadline-date'

    @lazy
    def is_allowed(self):
        mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course_id)
        return (
            is_active and
            mode == 'verified' and
            self.verification_status in ('expired', 'none', 'must_reverify') and
            not settings.FEATURES.get('ENABLE_INTEGRITY_SIGNATURE')
        )

    @lazy
    def verification_status(self):
        """Return the verification status for this user."""
        verification_status = IDVerificationService.user_status(self.user)
        return verification_status['status']

    def must_retry(self):
        """Return True if the user must re-submit verification, False otherwise."""
        return self.verification_status == 'must_reverify'
