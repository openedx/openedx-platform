"""
Tests for util.date_utils
"""

import unittest
from datetime import datetime, timedelta, tzinfo
from unittest.mock import patch
from zoneinfo import ZoneInfo

import crum
import ddt
import pytest
from markupsafe import Markup

from django.test.client import RequestFactory

from common.djangoapps.util.date_utils import (
    almost_same_datetime, get_default_time_display, get_time_display, strftime_localized, strftime_localized_html
)


def test_get_default_time_display():
    assert get_default_time_display(None) == ""
    test_time = datetime(1992, 3, 12, 15, 3, 30, tzinfo=ZoneInfo("UTC"))
    assert get_default_time_display(test_time) == "Mar 12, 1992 at 15:03 UTC"


def test_get_dflt_time_disp_notz():
    test_time = datetime(1992, 3, 12, 15, 3, 30)
    assert get_default_time_display(test_time) == "Mar 12, 1992 at 15:03 UTC"


def test_get_time_disp_ret_empty():
    assert get_time_display(None) == ""
    test_time = datetime(1992, 3, 12, 15, 3, 30, tzinfo=ZoneInfo("UTC"))
    assert get_time_display(test_time, "") == ""


def test_get_time_display():
    test_time = datetime(1992, 3, 12, 15, 3, 30, tzinfo=ZoneInfo("UTC"))
    assert get_time_display(test_time, 'dummy text') == "dummy text"
    assert get_time_display(test_time, '%b %d %Y') == "Mar 12 1992"
    assert get_time_display(test_time, '%b %d %Y %Z') == "Mar 12 1992 UTC"
    assert get_time_display(test_time, '%b %d %H:%M') == "Mar 12 15:03"


def test_get_time_pass_through():
    test_time = datetime(1992, 3, 12, 15, 3, 30, tzinfo=ZoneInfo("UTC"))
    assert get_time_display(test_time) == "Mar 12, 1992 at 15:03 UTC"
    assert get_time_display(test_time, None) == "Mar 12, 1992 at 15:03 UTC"
    assert get_time_display(test_time, "%") == "Mar 12, 1992 at 15:03 UTC"


def test_get_time_display_coerce():
    test_time_standard = datetime(1992, 1, 12, 15, 3, 30, tzinfo=ZoneInfo("UTC"))
    test_time_daylight = datetime(1992, 7, 12, 15, 3, 30, tzinfo=ZoneInfo("UTC"))
    assert get_time_display(test_time_standard, None, coerce_tz="US/Pacific") == "Jan 12, 1992 at 07:03 PST"
    assert get_time_display(test_time_standard, None, coerce_tz="NONEXISTENTTZ") == "Jan 12, 1992 at 15:03 UTC"
    assert get_time_display(test_time_standard, '%b %d %H:%M', coerce_tz="US/Pacific") == "Jan 12 07:03"
    assert get_time_display(test_time_daylight, None, coerce_tz="US/Pacific") == "Jul 12, 1992 at 08:03 PDT"
    assert get_time_display(test_time_daylight, None, coerce_tz="NONEXISTENTTZ") == "Jul 12, 1992 at 15:03 UTC"
    assert get_time_display(test_time_daylight, '%b %d %H:%M', coerce_tz="US/Pacific") == "Jul 12 08:03"


class NamelessTZ(tzinfo):  # lint-amnesty, pylint: disable=abstract-method
    """Static timezone for testing"""

    def utcoffset(self, _dt):
        return timedelta(hours=-3)

    def dst(self, _dt):
        return timedelta(0)


def test_get_default_time_display_no_tzname():
    assert get_default_time_display(None) == ""
    test_time = datetime(1992, 3, 12, 15, 3, 30, tzinfo=NamelessTZ())
    assert get_default_time_display(test_time) == "Mar 12, 1992 at 15:03-0300"


def test_almost_same_datetime():
    assert almost_same_datetime(
        datetime(2013, 5, 3, 10, 20, 30),
        datetime(2013, 5, 3, 10, 21, 29)
    )

    assert almost_same_datetime(
        datetime(2013, 5, 3, 11, 20, 30),
        datetime(2013, 5, 3, 10, 21, 29),
        timedelta(hours=1)
    )

    assert not almost_same_datetime(
        datetime(2013, 5, 3, 11, 20, 30),
        datetime(2013, 5, 3, 10, 21, 29)
    )

    assert not almost_same_datetime(
        datetime(2013, 5, 3, 11, 20, 30),
        datetime(2013, 5, 3, 10, 21, 29),
        timedelta(minutes=10)
    )


def fake_ugettext(translations):
    """
    Create a fake implementation of ugettext, for testing.
    """
    def _ugettext(text):
        return translations.get(text, text)
    return _ugettext


def fake_pgettext(translations):
    """
    Create a fake implementation of pgettext, for testing.
    """
    def _pgettext(context, text):
        return translations.get((context, text), text)
    return _pgettext


@ddt.ddt
class StrftimeLocalizedTest(unittest.TestCase):
    """
    Tests for strftime_localized.
    """
    @ddt.data(
        ("%Y", "2013"),
        ("%m/%d/%y", "02/14/13"),
        ("hello", "hello"),
        ('%Y년 %m월 %d일', "2013년 02월 14일"),
        ("%a, %b %d, %Y", "Thu, Feb 14, 2013"),
        ("%I:%M:%S %p", "04:41:17 PM"),
        ("%A at %-I%P", "Thursday at 4pm"),
    )
    def test_usual_strftime_behavior(self, fmt_expected):
        (fmt, expected) = fmt_expected
        dtime = datetime(2013, 2, 14, 16, 41, 17)
        assert expected == strftime_localized(dtime, fmt)
        assert expected == dtime.strftime(fmt)

    @ddt.data(
        ("SHORT_DATE", "Feb 14, 2013"),
        ("LONG_DATE", "Thursday, February 14, 2013"),
        ("TIME", "04:41:17 PM"),
        ("DAY_AND_TIME", "Thursday at 4pm"),
        ("%x %X!", "Feb 14, 2013 04:41:17 PM!"),
    )
    def test_shortcuts(self, fmt_expected):
        (fmt, expected) = fmt_expected
        dtime = datetime(2013, 2, 14, 16, 41, 17)
        assert expected == strftime_localized(dtime, fmt)

    @patch('common.djangoapps.util.date_utils.pgettext', fake_pgettext(translations={
        ("abbreviated month name", "Feb"): "XXfebXX",
        ("month name", "February"): "XXfebruaryXX",
        ("abbreviated weekday name", "Thu"): "XXthuXX",
        ("weekday name", "Thursday"): "XXthursdayXX",
        ("am/pm indicator", "PM"): "XXpmXX",
    }))
    @ddt.data(
        ("SHORT_DATE", "XXfebXX 14, 2013"),
        ("LONG_DATE", "XXthursdayXX, XXfebruaryXX 14, 2013"),
        ("DATE_TIME", "XXfebXX 14, 2013 at 16:41"),
        ("TIME", "04:41:17 XXpmXX"),
        ("%x %X!", "XXfebXX 14, 2013 04:41:17 XXpmXX!"),
    )
    def test_translated_words(self, fmt_expected):
        (fmt, expected) = fmt_expected
        dtime = datetime(2013, 2, 14, 16, 41, 17)
        assert expected == strftime_localized(dtime, fmt)

    @patch('common.djangoapps.util.date_utils.gettext', fake_ugettext(translations={
        "SHORT_DATE_FORMAT": "date(%Y.%m.%d)",
        "LONG_DATE_FORMAT": "date(%A.%Y.%B.%d)",
        "DATE_TIME_FORMAT": "date(%Y.%m.%d@%H.%M)",
        "TIME_FORMAT": "%Hh.%Mm.%Ss",
    }))
    @ddt.data(
        ("SHORT_DATE", "date(2013.02.14)"),
        ("Look: %x", "Look: date(2013.02.14)"),
        ("LONG_DATE", "date(Thursday.2013.February.14)"),
        ("DATE_TIME", "date(2013.02.14@16.41)"),
        ("TIME", "16h.41m.17s"),
        ("The time is: %X", "The time is: 16h.41m.17s"),
        ("%x %X", "date(2013.02.14) 16h.41m.17s"),
    )
    def test_translated_formats(self, fmt_expected):
        (fmt, expected) = fmt_expected
        dtime = datetime(2013, 2, 14, 16, 41, 17)
        assert expected == strftime_localized(dtime, fmt)

    @patch('common.djangoapps.util.date_utils.gettext', fake_ugettext(translations={
        "SHORT_DATE_FORMAT": "oops date(%Y.%x.%d)",
        "TIME_FORMAT": "oops %Hh.%Xm.%Ss",
    }))
    @ddt.data(
        ("SHORT_DATE", "Feb 14, 2013"),
        ("TIME", "04:41:17 PM"),
    )
    def test_recursion_protection(self, fmt_expected):
        (fmt, expected) = fmt_expected
        dtime = datetime(2013, 2, 14, 16, 41, 17)
        assert expected == strftime_localized(dtime, fmt)

    @ddt.data(
        "%",
        "Hello%"
        "%Y/%m/%d%",
    )
    def test_invalid_format_strings(self, fmt):
        dtime = datetime(2013, 2, 14, 16, 41, 17)
        with pytest.raises(ValueError):
            strftime_localized(dtime, fmt)


@ddt.ddt
class StrftimeLocalizedHtmlTest(unittest.TestCase):
    """
    Tests for strftime_localized_html.
    """
    def setUp(self):
        super().setUp()
        request = RequestFactory().request()
        self.addCleanup(crum.set_current_request, None)
        crum.set_current_request(request)

    @ddt.data(
        None,
        'Africa/Casablanca',
    )
    def test_happy_path(self, timezone):
        dtime = datetime(2013, 2, 14, 16, 41, 17)
        with patch('common.djangoapps.util.date_utils.user_timezone_locale_prefs',
                   return_value={'user_timezone': timezone}):
            html = strftime_localized_html(dtime, 'SHORT_DATE')
        assert isinstance(html, Markup)
        self.assertRegex(html,
                         '<span class="localized-datetime" data-format="shortDate" data-timezone="%s" ' % timezone +
                         '\\s*data-datetime="2013-02-14T16:41:17" data-language="en">Feb 14, 2013</span>')

    def test_invalid_format_string(self):
        dtime = datetime(2013, 2, 14, 16, 41, 17)
        with self.assertRaisesRegex(AssertionError, 'format "NOPE" not yet supported in strftime_localized_html'):
            strftime_localized_html(dtime, 'NOPE')
