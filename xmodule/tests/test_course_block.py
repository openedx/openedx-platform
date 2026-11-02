"""Tests the course blocks and their functions"""


import itertools
import unittest
from datetime import datetime, timedelta
import sys
from unittest.mock import Mock, patch

import ddt
from dateutil import parser
from django.conf import settings
from django.test import override_settings
from fs.memoryfs import MemoryFS
from opaque_keys.edx.keys import CourseKey
import pytest
from pytz import utc
from xblock.runtime import DictKeyValueStore, KvsFieldData

from openedx.core.lib.teams_config import TeamsConfig, DEFAULT_COURSE_RUN_MAX_TEAM_SIZE
import xmodule.course_block
from xmodule.course_metadata_utils import DEFAULT_START_DATE
from xmodule.data import CertificatesDisplayBehaviors
from xmodule.modulestore.xml import XMLImportingModuleStoreRuntime, XMLModuleStore
from xmodule.modulestore.exceptions import InvalidProctoringProvider

ORG = 'test_org'
COURSE = 'test_course'

NOW = datetime.strptime('2013-01-01T01:00:00', '%Y-%m-%dT%H:%M:00').replace(tzinfo=utc)

_TODAY = datetime.now(utc)
_LAST_WEEK = _TODAY - timedelta(days=7)
_NEXT_WEEK = _TODAY + timedelta(days=7)


@ddt.ddt()
class CourseFieldsTestCase(unittest.TestCase):  # lint-amnesty, pylint: disable=missing-class-docstring

    def test_default_start_date(self):
        assert xmodule.course_block.CourseFields.start.default == DEFAULT_START_DATE

    @ddt.data(True, False)
    def test_default_enrollment_start_date(self, should_have_default_enroll_start):
        features = settings.FEATURES.copy()
        features['CREATE_COURSE_WITH_DEFAULT_ENROLLMENT_START_DATE'] = should_have_default_enroll_start
        with override_settings(FEATURES=features):
            # reimport, so settings override could take effect
            del sys.modules['xmodule.course_block']
            import xmodule.course_block  # lint-amnesty, pylint: disable=redefined-outer-name, reimported
            expected = DEFAULT_START_DATE if should_have_default_enroll_start else None
            assert xmodule.course_block.CourseFields.enrollment_start.default == expected


class DummyModuleStoreRuntime(XMLImportingModuleStoreRuntime):  # pylint: disable=abstract-method
    """
    Minimal modulestore runtime for tests.
    """
    @patch('xmodule.modulestore.xml.OSFS', lambda dir: MemoryFS())
    def __init__(self, load_error_blocks, course_id=None):

        xmlstore = XMLModuleStore("data_dir", source_dirs=[],
                                  load_error_blocks=load_error_blocks)
        if course_id is None:
            course_id = CourseKey.from_string('/'.join([ORG, COURSE, 'test_run']))
        course_dir = "test_dir"
        error_tracker = Mock()

        super().__init__(
            xmlstore=xmlstore,
            course_id=course_id,
            course_dir=course_dir,
            error_tracker=error_tracker,
            load_error_blocks=load_error_blocks,
            services={'field-data': KvsFieldData(DictKeyValueStore())},
        )


def get_dummy_course(
    start,
    announcement=None,
    is_new=None,
    advertised_start=None,
    end=None,
    certs='end',
):
    """Get a dummy course"""

    system = DummyModuleStoreRuntime(load_error_blocks=True)

    def to_attrb(n, v):
        return '' if v is None else f'{n}="{v}"'.lower()

    is_new = to_attrb('is_new', is_new)
    announcement = to_attrb('announcement', announcement)
    advertised_start = to_attrb('advertised_start', advertised_start)
    end = to_attrb('end', end)

    start_xml = '''
         <course org="{org}" course="{course}" display_organization="{org}_display" display_coursenumber="{course}_display"
                graceperiod="1 day" url_name="test"
                start="{start}"
                {announcement}
                {is_new}
                {advertised_start}
                {end}
                certificates_display_behavior="{certs}">
            <chapter url="hi" url_name="ch" display_name="CH">
                <html url_name="h" display_name="H">Two houses, ...</html>
            </chapter>
         </course>
     '''.format(
        org=ORG,
        course=COURSE,
        start=start,
        is_new=is_new,
        announcement=announcement,
        advertised_start=advertised_start,
        end=end,
        certs=certs
    )

    return system.process_xml(start_xml)


class HasEndedMayCertifyTestCase(unittest.TestCase):
    """Double check the semantics around when to finalize courses."""

    def setUp(self):
        super().setUp()

        system = DummyModuleStoreRuntime(load_error_blocks=True)  # lint-amnesty, pylint: disable=unused-variable

        past_end = (datetime.now() - timedelta(days=12)).strftime("%Y-%m-%dT%H:%M:00")
        future_end = (datetime.now() + timedelta(days=12)).strftime("%Y-%m-%dT%H:%M:00")
        self.past_show_certs = get_dummy_course(
            "2012-01-01T12:00",
            end=past_end,
            certs=CertificatesDisplayBehaviors.EARLY_NO_INFO
        )
        self.past_show_certs_no_info = get_dummy_course(
            "2012-01-01T12:00",
            end=past_end,
            certs=CertificatesDisplayBehaviors.EARLY_NO_INFO
        )
        self.past_noshow_certs = get_dummy_course(
            "2012-01-01T12:00",
            end=past_end,
            certs=CertificatesDisplayBehaviors.END
        )

        self.future_show_certs_no_info = get_dummy_course(
            "2012-01-01T12:00",
            end=future_end,
            certs=CertificatesDisplayBehaviors.EARLY_NO_INFO
        )
        self.future_noshow_certs = get_dummy_course(
            "2012-01-01T12:00",
            end=future_end,
            certs=CertificatesDisplayBehaviors.END
        )

    def test_has_ended(self):
        """Check that has_ended correctly tells us when a course is over."""
        assert self.past_show_certs.has_ended()
        assert self.past_show_certs_no_info.has_ended()
        assert self.past_noshow_certs.has_ended()
        assert not self.future_show_certs_no_info.has_ended()
        assert not self.future_noshow_certs.has_ended()


class CourseSummaryHasEnded(unittest.TestCase):
    """ Test for has_ended method when end date is missing timezone information. """

    def test_course_end(self):
        test_course = get_dummy_course("2012-01-01T12:00")
        bad_end_date = parser.parse("2012-02-21 10:28:45")
        summary = xmodule.course_block.CourseSummary(test_course.id, end=bad_end_date)
        assert summary.has_ended()


@ddt.ddt
class IsNewCourseTestCase(unittest.TestCase):
    """Make sure the property is_new works on courses"""

    def setUp(self):
        super().setUp()

        # Needed for test_is_newish
        datetime_patcher = patch.object(
            xmodule.course_metadata_utils, 'datetime',
            Mock(wraps=datetime)
        )
        mocked_datetime = datetime_patcher.start()
        mocked_datetime.now.return_value = NOW
        self.addCleanup(datetime_patcher.stop)

    @patch('xmodule.course_metadata_utils.datetime.now')
    def test_sorting_score(self, gmtime_mock):
        gmtime_mock.return_value = NOW

        day1 = '2012-01-01T12:00'
        day2 = '2012-01-02T12:00'

        dates = [
            # Announce date takes priority over actual start
            # and courses announced on a later date are newer
            # than courses announced for an earlier date
            ((day1, day2, None), (day1, day1, None), self.assertLess),
            ((day1, day1, None), (day2, day1, None), self.assertEqual),

            # Announce dates take priority over advertised starts
            ((day1, day2, day1), (day1, day1, day1), self.assertLess),
            ((day1, day1, day2), (day2, day1, day2), self.assertEqual),

            # Later start == newer course
            ((day2, None, None), (day1, None, None), self.assertLess),
            ((day1, None, None), (day1, None, None), self.assertEqual),

            # Non-parseable advertised starts are ignored in preference to actual starts
            ((day2, None, "Spring"), (day1, None, "Fall"), self.assertLess),
            ((day1, None, "Spring"), (day1, None, "Fall"), self.assertEqual),

            # Partially parsable advertised starts should take priority over start dates
            ((day2, None, "October 2013"), (day2, None, "October 2012"), self.assertLess),
            ((day2, None, "October 2013"), (day1, None, "October 2013"), self.assertEqual),

            # Parseable advertised starts take priority over start dates
            ((day1, None, day2), (day1, None, day1), self.assertLess),
            ((day2, None, day2), (day1, None, day2), self.assertEqual),
        ]

        for a, b, assertion in dates:
            a_score = get_dummy_course(start=a[0], announcement=a[1], advertised_start=a[2]).sorting_score
            b_score = get_dummy_course(start=b[0], announcement=b[1], advertised_start=b[2]).sorting_score
            print(f"Comparing {a} to {b}")
            assertion(a_score, b_score)

    start_advertised_settings = [
        # start, advertised, result, is_still_default, date_time_result
        ('2012-12-02T12:00', None, 'Dec 02, 2012', False, 'Dec 02, 2012 at 12:00 UTC'),
        ('2012-12-02T12:00', '2011-11-01T12:00', 'Nov 01, 2011', False, 'Nov 01, 2011 at 12:00 UTC'),
        ('2012-12-02T12:00', 'Spring 2012', 'Spring 2012', False, 'Spring 2012'),
        ('2012-12-02T12:00', 'November, 2011', 'November, 2011', False, 'November, 2011'),
        (xmodule.course_block.CourseFields.start.default, None, 'TBD', True, 'TBD'),
        (xmodule.course_block.CourseFields.start.default, 'January 2014', 'January 2014', False, 'January 2014'),
    ]

    def test_start_date_is_default(self):
        for s in self.start_advertised_settings:
            d = get_dummy_course(start=s[0], advertised_start=s[1])
            assert d.start_date_is_still_default == s[3]

    def test_display_organization(self):
        block = get_dummy_course(start='2012-12-02T12:00', is_new=True)
        assert block.location.org != block.display_org_with_default
        assert block.display_org_with_default == f'{ORG}_display'

    def test_display_coursenumber(self):
        block = get_dummy_course(start='2012-12-02T12:00', is_new=True)
        assert block.location.course != block.display_number_with_default
        assert block.display_number_with_default == f'{COURSE}_display'

    def test_is_newish(self):
        block = get_dummy_course(start='2012-12-02T12:00', is_new=True)
        assert block.is_newish is True

        block = get_dummy_course(start='2013-02-02T12:00', is_new=False)
        assert block.is_newish is False

        block = get_dummy_course(start='2013-02-02T12:00', is_new=True)
        assert block.is_newish is True

        block = get_dummy_course(start='2013-01-15T12:00')
        assert block.is_newish is True

        block = get_dummy_course(start='2013-03-01T12:00')
        assert block.is_newish is True

        block = get_dummy_course(start='2012-10-15T12:00')
        assert block.is_newish is False

        block = get_dummy_course(start='2012-12-31T12:00')
        assert block.is_newish is True


class DiscussionTopicsTestCase(unittest.TestCase):

    def test_default_discussion_topics(self):
        d = get_dummy_course('2012-12-02T12:00')
        assert {'General': {'id': 'i4x-test_org-test_course-course-test'}} == d.discussion_topics


class TeamsConfigurationTestCase(unittest.TestCase):
    """
    Tests for the configuration of teams and the helper methods for accessing them.
    """

    def setUp(self):
        super().setUp()
        self.course = get_dummy_course('2012-12-02T12:00')
        self.course.teams_configuration = TeamsConfig(None)
        self.count = itertools.count()

    def add_team_configuration(self, max_team_size=3, topics=None, enabled=None):
        """ Add a team configuration to the course. """
        teams_config_data = {}
        teams_config_data["topics"] = [] if topics is None else topics
        if max_team_size is not None:
            teams_config_data["max_team_size"] = max_team_size
        if enabled is not None:
            teams_config_data["enabled"] = enabled
        self.course.teams_configuration = TeamsConfig(teams_config_data)

    def make_topic(self):
        """ Make a sample topic dictionary. """
        next_num = next(self.count)
        topic_id = f"topic_id_{next_num}"
        name = f"Name {next_num}"
        description = f"Description {next_num}"
        return {
            "name": name,
            "description": description,
            "id": topic_id,
            "type": "open",
            "max_team_size": None,
            "user_partition_id": None,
        }

    def test_teams_enabled_new_course(self):
        """
        Tests that teams are not enabled by default as no teamsets exist.
        """
        # Make sure we can detect when no teams exist.
        assert not self.course.teams_enabled
        assert not self.course.teams_configuration.is_enabled

    def test_teams_enabled_with_default(self):
        """
        Test that teams are automatically enabled if a teamset is added, but it can be disabled via the `enabled` field.
        """
        # Test that teams is enabled if topic are created
        self.add_team_configuration(max_team_size=4, topics=[self.make_topic()])
        assert self.course.teams_enabled
        assert self.course.teams_configuration.is_enabled

        # Test that teams are disabled if topic exists, but enabled is False
        self.add_team_configuration(max_team_size=4, topics=[self.make_topic()], enabled=False)
        assert not self.course.teams_enabled
        assert not self.course.teams_configuration.is_enabled

    def test_teams_enabled_no_teamsets(self):
        """
        Test that teams can be enabled / disabled with only the flag, even if no teamsets exist
        """
        self.add_team_configuration(max_team_size=4, topics=[], enabled=True)
        assert self.course.teams_enabled
        assert self.course.teams_configuration.is_enabled
        self.add_team_configuration(max_team_size=4, topics=[], enabled=False)
        assert not self.course.teams_enabled
        assert not self.course.teams_configuration.is_enabled

    def test_teams_enabled_max_size_only(self):
        """
        Test that teams isn't enabled if only a max team size is configured.
        """
        self.add_team_configuration(max_team_size=4)
        assert not self.course.teams_enabled

    def test_teams_enabled_no_max_size(self):
        """
        Test that teams is enabled if a max team size is missing but teamsets are created.s
        """
        self.add_team_configuration(max_team_size=None, topics=[self.make_topic()])
        assert self.course.teams_enabled

    def test_teams_max_size_no_teams_configuration(self):
        """
        Test that the default maximum team size matches the configured maximum
        """
        assert self.course.teams_configuration.default_max_team_size == DEFAULT_COURSE_RUN_MAX_TEAM_SIZE

    def test_teams_max_size_with_teams_configured(self):
        """
        Test that if you provide a custom global max_team_size, it reflects in the config.
        """
        size = 4
        self.add_team_configuration(max_team_size=size, topics=[self.make_topic(), self.make_topic()])
        assert self.course.teams_enabled
        assert size == self.course.teams_configuration.default_max_team_size

    def test_teamsets_no_config(self):
        """
        Tests that no teamsets are configured by default.
        """
        assert self.course.teamsets == []

    def test_teamsets_empty(self):
        """
        Test that if only the max team size is configured then there are no teamsets
        """
        self.add_team_configuration(max_team_size=4)
        assert self.course.teamsets == []

    def test_teamsets_present(self):
        """
        Tests that if valid teamsets are added they show up in the config
        """
        topics = [self.make_topic(), self.make_topic()]
        self.add_team_configuration(max_team_size=4, topics=topics)
        assert self.course.teams_enabled
        expected_teamsets_data = [
            teamset.cleaned_data
            for teamset in self.course.teamsets
        ]
        assert expected_teamsets_data == topics

    def test_teams_conf_cached_by_xblock_field(self):
        """
        Test that the teamsets are cached in the field so repeated queries don't perform re-computation
        """
        self.add_team_configuration(max_team_size=5, topics=[self.make_topic()])
        cold_cache_conf = self.course.teams_configuration
        warm_cache_conf = self.course.teams_configuration
        self.add_team_configuration(max_team_size=5, topics=[self.make_topic(), self.make_topic()])
        new_cold_cache_conf = self.course.teams_configuration
        new_warm_cache_conf = self.course.teams_configuration
        assert cold_cache_conf is warm_cache_conf
        assert new_cold_cache_conf is new_warm_cache_conf
        assert cold_cache_conf is not new_cold_cache_conf


class SelfPacedTestCase(unittest.TestCase):
    """Tests for self-paced courses."""

    def setUp(self):
        super().setUp()
        self.course = get_dummy_course('2012-12-02T12:00')

    def test_default(self):
        assert not self.course.self_paced


@ddt.ddt
class CourseBlockTestCase(unittest.TestCase):
    """
    Tests for a select few functions from CourseBlock.

    I wrote these test functions in order to satisfy the coverage checker for
    PR #8484, which modified some code within CourseBlock. However, this
    class definitely isn't a comprehensive test case for CourseBlock, as
    writing a such a test case was out of the scope of the PR.
    """

    def setUp(self):
        """
        Initialize dummy testing course.
        """
        super().setUp()
        self.course = get_dummy_course(start=_TODAY, end=_NEXT_WEEK)

    def test_clean_id(self):
        """
        Test CourseBlock.clean_id.
        """
        assert self.course.clean_id() == 'course_ORSXG5C7N5ZGOL3UMVZXIX3DN52XE43FF52GK43UL5ZHK3Q='
        assert self.course.clean_id(padding_char='$') == 'course_ORSXG5C7N5ZGOL3UMVZXIX3DN52XE43FF52GK43UL5ZHK3Q$'

    def test_has_started(self):
        """
        Test CourseBlock.has_started.
        """
        self.course.start = _LAST_WEEK
        assert self.course.has_started()
        self.course.start = _NEXT_WEEK
        assert not self.course.has_started()

    def test_number(self):
        """
        Test CourseBlock.number.
        """
        assert self.course.number == COURSE

    @ddt.data(
        (_LAST_WEEK, None, True),
        (None, _NEXT_WEEK, True),
        (_LAST_WEEK, _NEXT_WEEK, True),
        (_LAST_WEEK, _LAST_WEEK, False),
        (_NEXT_WEEK, _NEXT_WEEK, False)
    )
    @ddt.unpack
    def test_is_enrollment_open(self, enrollment_start_date, enrollment_end_date, enrollment_open):
        """
        Test CourseBlock.is_enrollment_open.
        """
        self.course.enrollment_start = enrollment_start_date
        self.course.enrollment_end = enrollment_end_date

        assert self.course.is_enrollment_open() is enrollment_open


@ddt.ddt
class ProctoringProviderTestCase(unittest.TestCase):
    """
    Tests for ProctoringProvider, including the default value, validation, and inheritance behavior.
    """

    def setUp(self):
        """
        Initialize dummy testing course.
        """
        super().setUp()
        self.proctoring_provider = xmodule.course_block.ProctoringProvider()

    def test_from_json_with_platform_default(self):
        """
        Test that a proctoring provider value equivalent to the platform
        default will pass validation.
        """
        default_provider = settings.PROCTORING_BACKENDS.get('DEFAULT')

        # we expect the validated value to be equivalent to the value passed in,
        # since there are no validation errors or missing data
        assert self.proctoring_provider.from_json(default_provider) == default_provider

    @override_settings(
        PROCTORING_BACKENDS={
            'DEFAULT': 'mock',
            'mock': {},
            'mock_proctoring_without_rules': {}
        }
    )
    @ddt.data(True, False)
    def test_from_json_with_invalid_provider(self, proctored_exams_setting_enabled):
        """
        Test that an invalid provider (i.e. not one configured at the platform level)
        throws a ValueError with the correct error message.
        """
        provider = 'invalid-provider'
        allowed_proctoring_providers = xmodule.course_block.get_available_providers()

        FEATURES_WITH_PROCTORED_EXAMS = settings.FEATURES.copy()
        FEATURES_WITH_PROCTORED_EXAMS['ENABLE_PROCTORED_EXAMS'] = proctored_exams_setting_enabled

        with override_settings(FEATURES=FEATURES_WITH_PROCTORED_EXAMS):
            if proctored_exams_setting_enabled:
                with pytest.raises(InvalidProctoringProvider) as context_manager:
                    self.proctoring_provider.from_json(provider, validate_providers=True)
                expected_error = f'The selected proctoring provider, {provider}, is not a valid provider. ' \
                    f'Please select from one of {allowed_proctoring_providers}.'
                assert str(context_manager.value) == expected_error
            else:
                provider_value = self.proctoring_provider.from_json(provider, validate_providers=True)
                assert provider_value == self.proctoring_provider.default

    def test_from_json_validate_providers(self):
        """
        Test that an invalid provider is ignored if validate providers is set to false
        """
        provider = 'invalid-provider'

        FEATURES_WITH_PROCTORED_EXAMS = settings.FEATURES.copy()
        FEATURES_WITH_PROCTORED_EXAMS['ENABLE_PROCTORED_EXAMS'] = True

        with override_settings(FEATURES=FEATURES_WITH_PROCTORED_EXAMS):
            provider_value = self.proctoring_provider.from_json(provider, validate_providers=False)
            assert provider_value == provider

    def test_from_json_adds_platform_default_for_missing_provider(self):
        """
        Test that a value with no provider will inherit the default provider
        from the platform defaults.
        """
        default_provider = settings.PROCTORING_BACKENDS.get('DEFAULT')
        assert default_provider is not None

        assert self.proctoring_provider.from_json(None) == default_provider

    @override_settings(
        PROCTORING_BACKENDS={
            'mock': {},
            'mock_proctoring_without_rules': {}
        }
    )
    def test_default_with_no_platform_default(self):
        """
        Test that, when the platform defaults are not set, the default is correct.
        """
        assert self.proctoring_provider.default is None

    @override_settings(PROCTORING_BACKENDS=None)
    def test_default_with_no_platform_configuration(self):
        """
        Test that, when the platform default is not specified, the default is correct.
        """
        default = self.proctoring_provider.default
        assert default is None
