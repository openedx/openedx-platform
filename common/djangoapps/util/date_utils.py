"""
Convenience methods for working with datetime objects
"""


import re
from datetime import datetime, timedelta

import crum
from django.utils.translation import get_language, gettext, pgettext
from pytz import UnknownTimeZoneError, timezone, utc

from lms.djangoapps.courseware.context_processor import user_timezone_locale_prefs
from openedx.core.djangolib.markup import HTML


def get_default_time_display(dtime):
    """
    Converts a datetime to a string representation. This is the default
    representation used in Studio and LMS.

    It will use the "DATE_TIME" format in the current language, if provided,
    or defaults to "Apr 09, 2013 at 16:00 UTC".

    If None is passed in for dt, an empty string will be returned.

    """
    if dtime is None:
        return ""
    if dtime.tzinfo is not None:
        try:
            timezone = " " + dtime.tzinfo.tzname(dtime)  # pylint: disable=redefined-outer-name
        except NotImplementedError:
            timezone = dtime.strftime('%z')
    else:
        timezone = " UTC"

    localized = strftime_localized(dtime, "DATE_TIME")
    return (localized + timezone).strip()


def get_time_display(dtime, format_string=None, coerce_tz=None):
    """
    Converts a datetime to a string representation.

    If None is passed in for dt, an empty string will be returned.

    If the format_string is None, or if format_string is improperly
    formatted, this method will return the value from `get_default_time_display`.

    Coerces aware datetime to tz=coerce_tz if set. coerce_tz should be a pytz timezone string
    like "US/Pacific", or None

    format_string should be a unicode string that is a valid argument for datetime's strftime method.
    """
    if dtime is not None and dtime.tzinfo is not None and coerce_tz:
        try:
            to_tz = timezone(coerce_tz)
        except UnknownTimeZoneError:
            to_tz = utc
        dtime = to_tz.normalize(dtime.astimezone(to_tz))
    if dtime is None or format_string is None:
        return get_default_time_display(dtime)
    try:
        return strftime_localized(dtime, format_string)
    except ValueError:
        return get_default_time_display(dtime)


def almost_same_datetime(dt1, dt2, allowed_delta=timedelta(minutes=1)):
    """
    Returns true if these are w/in a minute of each other. (in case secs saved to db
    or timezone aren't same)

    :param dt1:
    :param dt2:
    """
    return abs(dt1 - dt2) < allowed_delta


def to_timestamp(datetime_value):
    """
    Convert a datetime into a timestamp, represented as the number
    of seconds since January 1, 1970 UTC.
    """
    return int((datetime_value - datetime(1970, 1, 1, tzinfo=utc)).total_seconds())


def from_timestamp(timestamp):
    """
    Convert a timestamp (number of seconds since Jan 1, 1970 UTC)
    into a timezone-aware datetime.

    If the timestamp cannot be converted, returns None instead.
    """
    try:
        return datetime.utcfromtimestamp(int(timestamp)).replace(tzinfo=utc)
    except (ValueError, TypeError):
        return None


DEFAULT_SHORT_DATE_FORMAT = "%b %d, %Y"
DEFAULT_LONG_DATE_FORMAT = "%A, %B %d, %Y"
DEFAULT_TIME_FORMAT = "%I:%M:%S %p"
DEFAULT_DATE_TIME_FORMAT = "%b %d, %Y at %H:%M"
DEFAULT_DAY_AND_TIME_FORMAT = "%A at %-I%P"


def strftime_localized(dtime, format):      # pylint: disable=redefined-builtin
    """
    Format a datetime, just like the built-in strftime, but with localized words.

    The format string can also be one of:

    * "SHORT_DATE" for a date in brief form, localized.

    * "LONG_DATE" for a longer form of date, localized.

    * "DATE_TIME" for a date and time together, localized.

    * "TIME" for just the time, localized.

    The localization is based on the current language Django is using for the
    request.  The exact format strings used for each of the names above is
    determined by the translator for each language.

    Args:
        dtime (datetime): The datetime value to format.

        format (str): The format string to use, as specified by
            :ref:`datetime.strftime`.

    Returns:
        A unicode string with the formatted datetime.

    """

    if format == "SHORT_DATE":
        format = "%x"
    elif format == "LONG_DATE":  # pylint: disable=comparison-with-callable
        # Translators: the translation for "LONG_DATE_FORMAT" must be a format
        # string for formatting dates in a long form.  For example, the
        # American English form is "%A, %B %d %Y".
        # See http://strftime.org for details.
        format = gettext("LONG_DATE_FORMAT")
        if format == "LONG_DATE_FORMAT":
            format = DEFAULT_LONG_DATE_FORMAT
    elif format == "DATE_TIME":  # pylint: disable=comparison-with-callable
        # Translators: the translation for "DATE_TIME_FORMAT" must be a format
        # string for formatting dates with times.  For example, the American
        # English form is "%b %d, %Y at %H:%M".
        # See http://strftime.org for details.
        format = gettext("DATE_TIME_FORMAT")
        if format == "DATE_TIME_FORMAT":
            format = DEFAULT_DATE_TIME_FORMAT
    elif format == "DAY_AND_TIME":  # pylint: disable=comparison-with-callable
        format = DEFAULT_DAY_AND_TIME_FORMAT
    elif format == "TIME":  # pylint: disable=comparison-with-callable
        format = "%X"

    def process_percent_code(match):
        """
        Convert one percent-prefixed code in the format string.

        Called by re.sub just below.

        """
        code = match.group()
        if code == "%":
            # This only happens if the string ends with a %, which is not legal.
            raise ValueError("strftime format ends with raw %")

        if code == "%a":
            part = pgettext('abbreviated weekday name', WEEKDAYS_ABBREVIATED[dtime.weekday()])
        elif code == "%A":
            part = pgettext('weekday name', WEEKDAYS[dtime.weekday()])
        elif code == "%b":
            part = pgettext('abbreviated month name', MONTHS_ABBREVIATED[dtime.month])
        elif code == "%B":
            part = pgettext('month name', MONTHS[dtime.month])
        elif code == "%p":
            part = pgettext('am/pm indicator', AM_PM[dtime.hour // 12])
        elif code == "%x":
            # Get the localized short date format, and recurse.
            # Translators: the translation for "SHORT_DATE_FORMAT" must be a
            # format string for formatting dates in a brief form.  For example,
            # the American English form is "%b %d %Y".
            # See http://strftime.org for details.
            actual_format = gettext("SHORT_DATE_FORMAT")
            if actual_format == "SHORT_DATE_FORMAT":
                actual_format = DEFAULT_SHORT_DATE_FORMAT
            if "%x" in actual_format:
                # Prevent infinite accidental recursion.
                actual_format = DEFAULT_SHORT_DATE_FORMAT
            part = strftime_localized(dtime, actual_format)
        elif code == "%X":
            # Get the localized time format, and recurse.
            # Translators: the translation for "TIME_FORMAT" must be a format
            # string for formatting times.  For example, the American English
            # form is "%H:%M:%S". See http://strftime.org for details.
            actual_format = gettext("TIME_FORMAT")
            if actual_format == "TIME_FORMAT":
                actual_format = DEFAULT_TIME_FORMAT
            if "%X" in actual_format:
                # Prevent infinite accidental recursion.
                actual_format = DEFAULT_TIME_FORMAT
            part = strftime_localized(dtime, actual_format)
        else:
            # All the other format codes: just let built-in strftime take
            # care of them.
            part = dtime.strftime(code)

        return part

    formatted_date = re.sub(r"%-.|%.|%", process_percent_code, format)
    return formatted_date


def strftime_localized_html(dtime, fmt):
    """
    Returns an html string that can be further localized in browser by JS code

    For example, a user's default timezone preference is "whatever the browser default is" which must be queried via
    Javascript. So we write out a UTC formatted date here, but tag it with enough information that dateutil_factory.js
    can then later update the DOM to use the proper formatting.

    Arguments:
        dtime (datetime): Datetime to format
        fmt (str): One of the special enum values that strftime_localized accepts. Only SHORT_DATE supported now.
    """
    locale_prefs = user_timezone_locale_prefs(crum.get_current_request())
    user_timezone = locale_prefs['user_timezone']
    language = get_language()

    # Here we map the enums for strftime_localized into the enums used by date-utils.js when localizing on JS side.
    format_mapping = {
        'SHORT_DATE': 'shortDate',
    }
    assert fmt in format_mapping.keys(), 'format "%s" not yet supported in strftime_localized_html' % fmt  # pylint: disable=consider-iterating-dictionary  # noqa: UP031

    date_html = '<span class="localized-datetime" data-format="{format}" data-timezone="{user_timezone}" \
                       data-datetime="{formatted_date}" data-language="{language}">{formatted_date_localized}</span>'

    return HTML(date_html).format(
        language=language,
        user_timezone=user_timezone,
        format=format_mapping[fmt],
        formatted_date=dtime.isoformat(),
        formatted_date_localized=strftime_localized(dtime, fmt),
    )


# In order to extract the strings below, we have to mark them with pgettext.
# But we'll do the actual pgettext later, so use a no-op for now, and save the
# real pgettext so we can assign it back to the global name later.
real_pgettext = pgettext
pgettext = lambda context, text: text       # pylint: disable=invalid-name

AM_PM = {
    # Translators: This is an AM/PM indicator for displaying times.  It is
    # used for the %p directive in date-time formats. See http://strftime.org
    # for details.
    0: pgettext('am/pm indicator', 'AM'),
    # Translators: This is an AM/PM indicator for displaying times.  It is
    # used for the %p directive in date-time formats. See http://strftime.org
    # for details.
    1: pgettext('am/pm indicator', 'PM'),
}

WEEKDAYS = {
    # Translators: this is a weekday name that will be used when displaying
    # dates, as in "Monday Februrary 10, 2014". It is used for the %A
    # directive in date-time formats. See http://strftime.org for details.
    0: pgettext('weekday name', 'Monday'),
    # Translators: this is a weekday name that will be used when displaying
    # dates, as in "Tuesday Februrary 11, 2014". It is used for the %A
    # directive in date-time formats. See http://strftime.org for details.
    1: pgettext('weekday name', 'Tuesday'),
    # Translators: this is a weekday name that will be used when displaying
    # dates, as in "Wednesday Februrary 12, 2014". It is used for the %A
    # directive in date-time formats. See http://strftime.org for details.
    2: pgettext('weekday name', 'Wednesday'),
    # Translators: this is a weekday name that will be used when displaying
    # dates, as in "Thursday Februrary 13, 2014". It is used for the %A
    # directive in date-time formats. See http://strftime.org for details.
    3: pgettext('weekday name', 'Thursday'),
    # Translators: this is a weekday name that will be used when displaying
    # dates, as in "Friday Februrary 14, 2014". It is used for the %A
    # directive in date-time formats. See http://strftime.org for details.
    4: pgettext('weekday name', 'Friday'),
    # Translators: this is a weekday name that will be used when displaying
    # dates, as in "Saturday Februrary 15, 2014". It is used for the %A
    # directive in date-time formats. See http://strftime.org for details.
    5: pgettext('weekday name', 'Saturday'),
    # Translators: this is a weekday name that will be used when displaying
    # dates, as in "Sunday Februrary 16, 2014". It is used for the %A
    # directive in date-time formats. See http://strftime.org for details.
    6: pgettext('weekday name', 'Sunday'),
}

WEEKDAYS_ABBREVIATED = {
    # Translators: this is an abbreviated weekday name that will be used when
    # displaying dates, as in "Mon Feb 10, 2014". It is used for the %a
    # directive in date-time formats. See http://strftime.org for details.
    0: pgettext('abbreviated weekday name', 'Mon'),
    # Translators: this is an abbreviated weekday name that will be used when
    # displaying dates, as in "Tue Feb 11, 2014". It is used for the %a
    # directive in date-time formats. See http://strftime.org for details.
    1: pgettext('abbreviated weekday name', 'Tue'),
    # Translators: this is an abbreviated weekday name that will be used when
    # displaying dates, as in "Wed Feb 12, 2014". It is used for the %a
    # directive in date-time formats. See http://strftime.org for details.
    2: pgettext('abbreviated weekday name', 'Wed'),
    # Translators: this is an abbreviated weekday name that will be used when
    # displaying dates, as in "Thu Feb 13, 2014". It is used for the %a
    # directive in date-time formats. See http://strftime.org for details.
    3: pgettext('abbreviated weekday name', 'Thu'),
    # Translators: this is an abbreviated weekday name that will be used when
    # displaying dates, as in "Fri Feb 14, 2014". It is used for the %a
    # directive in date-time formats. See http://strftime.org for details.
    4: pgettext('abbreviated weekday name', 'Fri'),
    # Translators: this is an abbreviated weekday name that will be used when
    # displaying dates, as in "Sat Feb 15, 2014". It is used for the %a
    # directive in date-time formats. See http://strftime.org for details.
    5: pgettext('abbreviated weekday name', 'Sat'),
    # Translators: this is an abbreviated weekday name that will be used when
    # displaying dates, as in "Sun Feb 16, 2014". It is used for the %a
    # directive in date-time formats. See http://strftime.org for details.
    6: pgettext('abbreviated weekday name', 'Sun'),
}

MONTHS_ABBREVIATED = {
    # Translators: this is an abbreviated month name that will be used when
    # displaying dates, as in "Jan 10, 2014". It is used for the %b
    # directive in date-time formats. See http://strftime.org for details.
    1: pgettext('abbreviated month name', 'Jan'),
    # Translators: this is an abbreviated month name that will be used when
    # displaying dates, as in "Feb 10, 2014". It is used for the %b
    # directive in date-time formats. See http://strftime.org for details.
    2: pgettext('abbreviated month name', 'Feb'),
    # Translators: this is an abbreviated month name that will be used when
    # displaying dates, as in "Mar 10, 2014". It is used for the %b
    # directive in date-time formats. See http://strftime.org for details.
    3: pgettext('abbreviated month name', 'Mar'),
    # Translators: this is an abbreviated month name that will be used when
    # displaying dates, as in "Apr 10, 2014". It is used for the %b
    # directive in date-time formats. See http://strftime.org for details.
    4: pgettext('abbreviated month name', 'Apr'),
    # Translators: this is an abbreviated month name that will be used when
    # displaying dates, as in "May 10, 2014". It is used for the %b
    # directive in date-time formats. See http://strftime.org for details.
    5: pgettext('abbreviated month name', 'May'),
    # Translators: this is an abbreviated month name that will be used when
    # displaying dates, as in "Jun 10, 2014". It is used for the %b
    # directive in date-time formats. See http://strftime.org for details.
    6: pgettext('abbreviated month name', 'Jun'),
    # Translators: this is an abbreviated month name that will be used when
    # displaying dates, as in "Jul 10, 2014". It is used for the %b
    # directive in date-time formats. See http://strftime.org for details.
    7: pgettext('abbreviated month name', 'Jul'),
    # Translators: this is an abbreviated month name that will be used when
    # displaying dates, as in "Aug 10, 2014". It is used for the %b
    # directive in date-time formats. See http://strftime.org for details.
    8: pgettext('abbreviated month name', 'Aug'),
    # Translators: this is an abbreviated month name that will be used when
    # displaying dates, as in "Sep 10, 2014". It is used for the %b
    # directive in date-time formats. See http://strftime.org for details.
    9: pgettext('abbreviated month name', 'Sep'),
    # Translators: this is an abbreviated month name that will be used when
    # displaying dates, as in "Oct 10, 2014". It is used for the %b
    # directive in date-time formats. See http://strftime.org for details.
    10: pgettext('abbreviated month name', 'Oct'),
    # Translators: this is an abbreviated month name that will be used when
    # displaying dates, as in "Nov 10, 2014". It is used for the %b
    # directive in date-time formats. See http://strftime.org for details.
    11: pgettext('abbreviated month name', 'Nov'),
    # Translators: this is an abbreviated month name that will be used when
    # displaying dates, as in "Dec 10, 2014". It is used for the %b
    # directive in date-time formats. See http://strftime.org for details.
    12: pgettext('abbreviated month name', 'Dec'),
}

MONTHS = {
    # Translators: this is a month name that will be used when displaying
    # dates, as in "January 10, 2014". It is used for the %B directive in
    # date-time formats. See http://strftime.org for details.
    1: pgettext('month name', 'January'),
    # Translators: this is a month name that will be used when displaying
    # dates, as in "February 10, 2014". It is used for the %B directive in
    # date-time formats. See http://strftime.org for details.
    2: pgettext('month name', 'February'),
    # Translators: this is a month name that will be used when displaying
    # dates, as in "March 10, 2014". It is used for the %B directive in
    # date-time formats. See http://strftime.org for details.
    3: pgettext('month name', 'March'),
    # Translators: this is a month name that will be used when displaying
    # dates, as in "April 10, 2014". It is used for the %B directive in
    # date-time formats. See http://strftime.org for details.
    4: pgettext('month name', 'April'),
    # Translators: this is a month name that will be used when displaying
    # dates, as in "May 10, 2014". It is used for the %B directive in
    # date-time formats. See http://strftime.org for details.
    5: pgettext('month name', 'May'),
    # Translators: this is a month name that will be used when displaying
    # dates, as in "June 10, 2014". It is used for the %B directive in
    # date-time formats. See http://strftime.org for details.
    6: pgettext('month name', 'June'),
    # Translators: this is a month name that will be used when displaying
    # dates, as in "July 10, 2014". It is used for the %B directive in
    # date-time formats. See http://strftime.org for details.
    7: pgettext('month name', 'July'),
    # Translators: this is a month name that will be used when displaying
    # dates, as in "August 10, 2014". It is used for the %B directive in
    # date-time formats. See http://strftime.org for details.
    8: pgettext('month name', 'August'),
    # Translators: this is a month name that will be used when displaying
    # dates, as in "September 10, 2014". It is used for the %B directive in
    # date-time formats. See http://strftime.org for details.
    9: pgettext('month name', 'September'),
    # Translators: this is a month name that will be used when displaying
    # dates, as in "October 10, 2014". It is used for the %B directive in
    # date-time formats. See http://strftime.org for details.
    10: pgettext('month name', 'October'),
    # Translators: this is a month name that will be used when displaying
    # dates, as in "November 10, 2014". It is used for the %B directive in
    # date-time formats. See http://strftime.org for details.
    11: pgettext('month name', 'November'),
    # Translators: this is a month name that will be used when displaying
    # dates, as in "December 10, 2014". It is used for the %B directive in
    # date-time formats. See http://strftime.org for details.
    12: pgettext('month name', 'December'),
}

# Now that we are done defining constants, we have to restore the real pgettext
# so that the functions in this module will have the right definition.
pgettext = real_pgettext
