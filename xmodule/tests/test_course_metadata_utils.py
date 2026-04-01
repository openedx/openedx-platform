"""
Tests for course_metadata_utils.
"""


from collections import namedtuple
from datetime import datetime, timedelta
from unittest import TestCase
from zoneinfo import ZoneInfo

import pytest
from xmodule.block_metadata_utils import (
    display_name_with_default,
    display_name_with_default_escaped,
)
from xmodule.course_metadata_utils import (
    DEFAULT_START_DATE,
    clean_course_key,
    course_start_date_is_default,
    has_course_ended,
    has_course_started,
    is_enrollment_open,
    number_for_course_location
)
from xmodule.modulestore.tests.utils import (
    MixedModulestoreBuilder,
    MongoModulestoreBuilder,
    VersioningModulestoreBuilder
)

_TODAY = datetime.now(ZoneInfo("UTC"))
_LAST_WEEK = _TODAY - timedelta(days=7)
_NEXT_WEEK = _TODAY + timedelta(days=7)


@pytest.mark.django_db
class CourseMetadataUtilsTestCase(TestCase):
    """
    Tests for course_metadata_utils.
    """

    def setUp(self):
        """
        Set up module store testing capabilities and initialize test courses.
        """
        super().setUp()

        mongo_builder = MongoModulestoreBuilder()
        split_builder = VersioningModulestoreBuilder()
        mixed_builder = MixedModulestoreBuilder([('split', split_builder), ('mongo', mongo_builder)])

        with mixed_builder.build_without_contentstore() as (__, mixed_store):
            with mixed_store.default_store('split'):
                self.html_course = mixed_store.create_course(
                    org="UniversityX",
                    course="CS-203",
                    run="Y2096",
                    user_id=-3,  # -3 refers to a "testing user"
                    fields={
                        "start": _NEXT_WEEK,
                        "display_name": "Intro to <div>html</div>",
                        "enrollment_start": _NEXT_WEEK,
                    }
                )
            with mixed_store.default_store('split'):
                self.html_course_enrollment_start = mixed_store.create_course(
                    org="UniversityX",
                    course="CS-204",
                    run="Y2097",
                    user_id=-3,  # -3 refers to a "testing user"
                    fields={
                        "start": _LAST_WEEK,
                        "display_name": "Intro to <div>html</div>",
                        "enrollment_start": _LAST_WEEK,
                    }
                )

    def test_course_metadata_utils(self):
        """
        Test every single function in course_metadata_utils.
        """

        def mock_strftime_localized(date_time, format_string):
            """
            Mock version of strftime_localized used for testing purposes.

            Because we don't have a real implementation of strftime_localized
            to work with (strftime_localized is provided by the XBlock runtime,
            which we don't have access to for this test case), we must declare
            this dummy implementation. This does NOT behave like a real
            strftime_localized should. It purposely returns a really dumb value
            that's only useful for testing purposes.

            Arguments:
                date_time (datetime): datetime to be formatted.
                format_string (str): format specifier. Valid values include:
                    - 'DATE_TIME'
                    - 'TIME'
                    - 'SHORT_DATE'
                    - 'LONG_DATE'

            Returns (str): format_string + " " + str(date_time)
            """
            if format_string in ['DATE_TIME', 'TIME', 'SHORT_DATE', 'LONG_DATE']:
                return format_string + " " + date_time.strftime("%Y-%m-%d %H:%M:%S")
            else:
                raise ValueError("Invalid format string :" + format_string)

        def noop_gettext(text):  # lint-amnesty, pylint: disable=unused-variable
            """Dummy implementation of gettext, so we don't need Django."""
            return text

        test_datetime = datetime(1945, 2, 6, 4, 20, 00, tzinfo=ZoneInfo("UTC"))
        advertised_start_parsable = "2038-01-19 03:14:07"

        FunctionTest = namedtuple('FunctionTest', 'function scenarios')
        TestScenario = namedtuple('TestScenario', 'arguments expected_return')

        function_tests = [
            FunctionTest(clean_course_key, [
                # Test with a Split course and '~' as padding.
                TestScenario(
                    (self.html_course.id, '~'),
                    "course_MNXXK4TTMUWXMMJ2KVXGS5TFOJZWS5DZLAVUGUZNGIYDGK2ZGIYDSNQ~"
                ),
            ]),
            FunctionTest(display_name_with_default_escaped, [
                # Test course with a display name that contains characters that need escaping.
                TestScenario((self.html_course,), "Intro to html"),
            ]),
            FunctionTest(display_name_with_default, [
                # Test course with a display name that contains characters that need escaping.
                TestScenario((self.html_course,), "Intro to <div>html</div>"),
            ]),
            FunctionTest(number_for_course_location, [
                TestScenario((self.html_course.location,), "CS-203"),
            ]),
            FunctionTest(has_course_started, [
                TestScenario((self.html_course.start,), False),
            ]),
            FunctionTest(has_course_ended, [
                TestScenario((self.html_course.end,), False),
            ]),
            FunctionTest(is_enrollment_open, [
                TestScenario((self.html_course.enrollment_start, self.html_course.enrollment_end,), False),
                TestScenario((
                    self.html_course_enrollment_start.enrollment_start,
                    self.html_course_enrollment_start.enrollment_end,
                ), True),
            ]),
            FunctionTest(course_start_date_is_default, [
                TestScenario((test_datetime, advertised_start_parsable), False),
                TestScenario((test_datetime, None), False),
                TestScenario((DEFAULT_START_DATE, advertised_start_parsable), False),
                TestScenario((DEFAULT_START_DATE, None), True),
            ]),
        ]

        for function_test in function_tests:
            for scenario in function_test.scenarios:
                actual_return = function_test.function(*scenario.arguments)
                assert actual_return == scenario.expected_return

        # Even though we don't care about testing mock_strftime_localized,
        # we still need to test it with a bad format string in order to
        # satisfy the coverage checker.
        with pytest.raises(ValueError):
            mock_strftime_localized(test_datetime, 'BAD_FORMAT_SPECIFIER')
