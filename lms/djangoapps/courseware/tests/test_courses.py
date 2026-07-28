"""
Tests for course access
"""


import datetime
import itertools
from unittest import mock

import ddt
import pytest
import pytz
from completion.models import BlockCompletion
from completion.test_utils import CompletionWaffleTestMixin
from crum import set_current_request
from django.conf import settings
from django.test.client import RequestFactory
from django.test.utils import override_settings
from django.urls import reverse
from freezegun import freeze_time
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.courseware.block_render import get_block_for_descriptor
from lms.djangoapps.courseware.courses import (
    course_open_for_self_enrollment,
    get_cms_block_link,
    get_cms_course_link,
    get_course_about_section,
    get_course_assignments,
    get_course_chapter_ids,
    get_course_info_section,
    get_course_overview_with_access,
    get_course_with_access,
    get_courses,
    get_current_child,
)
from lms.djangoapps.courseware.courseware_access_exception import CoursewareAccessException
from lms.djangoapps.courseware.exceptions import CourseAccessRedirect
from lms.djangoapps.courseware.model_data import FieldDataCache
from openedx.core.djangolib.testing.utils import get_mock_request
from openedx.core.lib.courses import course_image_url
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import _get_modulestore_branch_setting, modulestore
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory, ToyCourseFactory, check_mongo_calls
from xmodule.tests.xml import XModuleXmlImportTest
from xmodule.tests.xml import factories as xml

CMS_BASE_TEST = 'testcms'
TEST_DATA_DIR = settings.COMMON_TEST_DATA_ROOT


@ddt.ddt
class CoursesTest(ModuleStoreTestCase):
    """Test methods related to fetching courses."""
    ENABLED_SIGNALS = ['course_published']
    GET_COURSE_WITH_ACCESS = 'get_course_with_access'
    GET_COURSE_OVERVIEW_WITH_ACCESS = 'get_course_overview_with_access'
    COURSE_ACCESS_FUNCS = {
        GET_COURSE_WITH_ACCESS: get_course_with_access,
        GET_COURSE_OVERVIEW_WITH_ACCESS: get_course_overview_with_access,
    }

    @override_settings(CMS_BASE=CMS_BASE_TEST)
    def test_get_cms_course_block_link(self):
        """
        Tests that get_cms_course_link_by_id and get_cms_block_link_by_id return the right thing
        """
        self.course = CourseFactory.create(  # pylint: disable=attribute-defined-outside-init
            org='org', number='num', display_name='name'
        )

        cms_url = f"//{CMS_BASE_TEST}/course/{str(self.course.id)}"
        assert cms_url == get_cms_course_link(self.course)
        cms_url = f"//{CMS_BASE_TEST}/course/{str(self.course.location)}"
        assert cms_url == get_cms_block_link(self.course, 'course')

    @ddt.data(GET_COURSE_WITH_ACCESS, GET_COURSE_OVERVIEW_WITH_ACCESS)
    def test_get_course_func_with_access_error(self, course_access_func_name):
        course_access_func = self.COURSE_ACCESS_FUNCS[course_access_func_name]
        user = UserFactory.create()
        course = CourseFactory.create(visible_to_staff_only=True)

        with pytest.raises(CoursewareAccessException) as error:
            course_access_func(user, 'load', course.id)
        assert str(error.value) == 'Course not found.'
        assert error.value.access_response.error_code == 'not_visible_to_user'
        assert not error.value.access_response.has_access

    @ddt.data(GET_COURSE_WITH_ACCESS, GET_COURSE_OVERVIEW_WITH_ACCESS)
    def test_get_course_func_with_catalog_visibility_error(self, course_access_func_name):
        """
        Test that accessing a course with catalog_visibility='none' via
        'see_about_page' action raises CourseAccessRedirect with a
        CatalogVisibilityError, not a generic CoursewareAccessException.
        """
        course_access_func = self.COURSE_ACCESS_FUNCS[course_access_func_name]
        user = UserFactory.create()
        course = CourseFactory.create(catalog_visibility='none', emit_signals=True)

        with pytest.raises(CourseAccessRedirect) as error:
            course_access_func(user, 'see_about_page', course.id)
        assert error.value.access_error is not None
        assert error.value.access_error.error_code == 'not_visible_in_catalog'
        assert not error.value.access_error.has_access

    @ddt.data(
        (GET_COURSE_WITH_ACCESS, 1),
        (GET_COURSE_OVERVIEW_WITH_ACCESS, 0),
    )
    @ddt.unpack
    def test_get_course_func_with_access(self, course_access_func_name, num_mongo_calls):
        course_access_func = self.COURSE_ACCESS_FUNCS[course_access_func_name]
        user = UserFactory.create()
        course = CourseFactory.create(emit_signals=True)
        with check_mongo_calls(num_mongo_calls):
            course_access_func(user, 'load', course.id)

    def test_get_courses_by_org(self):
        """
        Verify that org filtering performs as expected, and that an empty result
        is returned if the org passed by the caller does not match the designated
        org.
        """
        primary = 'primary'
        alternate = 'alternate'

        def _fake_get_value(value, default=None):
            """Used to stub out site_configuration.helpers.get_value()."""
            if value == 'course_org_filter':
                return alternate

            return default

        user = UserFactory.create()

        # Pass `emit_signals=True` so that these courses are cached with CourseOverviews.
        primary_course = CourseFactory.create(org=primary, emit_signals=True)
        alternate_course = CourseFactory.create(org=alternate, emit_signals=True)

        assert primary_course.org != alternate_course.org

        unfiltered_courses = get_courses(user)
        for org in [primary_course.org, alternate_course.org]:
            assert any((course.org == org) for course in unfiltered_courses)

        filtered_courses = get_courses(user, org=primary)
        assert all((course.org == primary_course.org) for course in filtered_courses)

        with mock.patch(
            'openedx.core.djangoapps.site_configuration.helpers.get_value',
            autospec=True,
        ) as mock_get_value:
            mock_get_value.side_effect = _fake_get_value

            # Request filtering for an org distinct from the designated org.
            no_courses = get_courses(user, org=primary)
            assert not list(no_courses)

            # Request filtering for an org matching the designated org.
            site_courses = get_courses(user, org=alternate)
            assert all((course.org == alternate_course.org) for course in site_courses)

    def test_get_courses_with_filter(self):
        """
        Verify that filtering performs as expected.
        """
        user = UserFactory.create()
        mobile_course = CourseFactory.create(emit_signals=True)
        non_mobile_course =\
            CourseFactory.create(mobile_available=False, emit_signals=True)

        test_cases = (
            (None, {non_mobile_course.id, mobile_course.id}),
            (dict(mobile_available=True), {mobile_course.id}),
            (dict(mobile_available=False), {non_mobile_course.id}),
        )
        for filter_, expected_courses in test_cases:
            assert {course.id for course in get_courses(user, filter_=filter_)} ==\
                   expected_courses, f'testing get_courses with filter_={filter_}'

    def test_get_current_child(self):
        mock_xblock = mock.MagicMock()
        assert get_current_child(mock_xblock) is None

        mock_xblock.position = -1
        mock_xblock.get_children.return_value = ['one', 'two', 'three']
        assert get_current_child(mock_xblock) == 'one'

        mock_xblock.position = 2
        assert get_current_child(mock_xblock) == 'two'
        assert get_current_child(mock_xblock, requested_child='first') == 'one'
        assert get_current_child(mock_xblock, requested_child='last') == 'three'

        mock_xblock.position = 3
        mock_xblock.get_children.return_value = []
        assert get_current_child(mock_xblock) is None


class ModuleStoreBranchSettingTest(ModuleStoreTestCase):
    """Test methods related to the modulestore branch setting."""
    @mock.patch(
        'xmodule.modulestore.django.get_current_request_hostname',
        mock.Mock(return_value='preview.localhost')
    )
    @override_settings(
        HOSTNAME_MODULESTORE_DEFAULT_MAPPINGS={r'preview\.': ModuleStoreEnum.Branch.draft_preferred},
        MODULESTORE_BRANCH='fake_default_branch',
    )
    def test_default_modulestore_preview_mapping(self):
        assert _get_modulestore_branch_setting() == ModuleStoreEnum.Branch.draft_preferred

    @mock.patch(
        'xmodule.modulestore.django.get_current_request_hostname',
        mock.Mock(return_value='localhost')
    )
    @override_settings(
        HOSTNAME_MODULESTORE_DEFAULT_MAPPINGS={r'preview\.': ModuleStoreEnum.Branch.draft_preferred},
        MODULESTORE_BRANCH='fake_default_branch',
    )
    def test_default_modulestore_branch_mapping(self):
        assert _get_modulestore_branch_setting() == 'fake_default_branch'


@override_settings(CMS_BASE=CMS_BASE_TEST)
class MongoCourseImageTestCase(ModuleStoreTestCase):
    """Tests for course image URLs when using a mongo modulestore."""

    def test_get_image_url(self):
        """Test image URL formatting."""
        course = CourseFactory.create()
        key = course.location
        assert course_image_url(course) ==\
               f'/asset-v1:{key.org}+{key.course}+{key.run}+type@asset+block@{course.course_image}'

    def test_non_ascii_image_name(self):
        # Verify that non-ascii image names are cleaned
        course = CourseFactory.create(course_image='before_\N{SNOWMAN}_after.jpg')
        key = course.location
        assert course_image_url(course) ==\
               f'/asset-v1:{key.org}+{key.course}+{key.run}+type@asset+block@before___after.jpg'

    def test_spaces_in_image_name(self):
        # Verify that image names with spaces in them are cleaned
        course = CourseFactory.create(course_image='before after.jpg')
        key = course.location
        assert course_image_url(course) ==\
               f'/asset-v1:{key.org}+{key.course}+{key.run}+type@asset+block@before_after.jpg'

    def test_static_asset_path_course_image_default(self):
        """
        Test that without course_image being set, but static_asset_path
        being set that we get the right course_image url.
        """
        course = CourseFactory.create(static_asset_path="foo")
        assert course_image_url(course) == '/static/foo/images/course_image.jpg'

    def test_static_asset_path_course_image_set(self):
        """
        Test that with course_image and static_asset_path both
        being set, that we get the right course_image url.
        """
        course = CourseFactory.create(course_image='things_stuff.jpg',
                                      static_asset_path="foo")
        assert course_image_url(course) == '/static/foo/things_stuff.jpg'


class XmlCourseImageTestCase(XModuleXmlImportTest):
    """Tests for course image URLs when using an xml modulestore."""

    def test_get_image_url(self):
        """Test image URL formatting."""
        course = self.process_xml(xml.CourseFactory.build())
        assert course_image_url(course) == '/static/xml_test_course/images/course_image.jpg'

    def test_non_ascii_image_name(self):
        course = self.process_xml(xml.CourseFactory.build(course_image='before_\N{SNOWMAN}_after.jpg'))
        assert course_image_url(course) == '/static/xml_test_course/before_☃_after.jpg'

    def test_spaces_in_image_name(self):
        course = self.process_xml(xml.CourseFactory.build(course_image='before after.jpg'))
        assert course_image_url(course) == '/static/xml_test_course/before after.jpg'


class CoursesRenderTest(ModuleStoreTestCase):
    """Test methods related to rendering courses content."""
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    # TODO: this test relies on the specific setup of the toy course.
    # It should be rewritten to build the course it needs and then test that.
    def setUp(self):
        """
        Set up the course and user context
        """
        super().setUp()

        self.course = ToyCourseFactory()
        self.addCleanup(set_current_request, None)
        self.request = get_mock_request(UserFactory.create())

    def test_get_course_info_section_render(self):
        # Test render works okay
        course_info = get_course_info_section(self.request, self.request.user, self.course, 'handouts')
        assert course_info == \
            "<a href='/asset-v1:edX+toy+2012_Fall+type@asset+block/handouts_sample_handout.txt'>Sample</a>"

        # Test when render raises an exception
        with mock.patch('lms.djangoapps.courseware.courses.get_block') as mock_block_render:
            mock_block_render.return_value = mock.MagicMock(
                render=mock.Mock(side_effect=Exception('Render failed!'))
            )
            course_info = get_course_info_section(self.request, self.request.user, self.course, 'handouts')
            assert 'this module is temporarily unavailable' in course_info

    def test_get_course_about_section_render(self):

        # Test render works okay
        course_about = get_course_about_section(self.request, self.course, 'short_description')
        assert course_about == 'A course about toys.'

        # Test when render raises an exception
        with mock.patch('lms.djangoapps.courseware.courses.get_block') as mock_block_render:
            mock_block_render.return_value = mock.MagicMock(
                render=mock.Mock(side_effect=Exception('Render failed!'))
            )
            course_about = get_course_about_section(self.request, self.course, 'short_description')
            assert 'this module is temporarily unavailable' in course_about


class CourseEnrollmentOpenTests(ModuleStoreTestCase):  # pylint: disable=missing-class-docstring
    def setUp(self):
        super().setUp()
        self.now = datetime.datetime.now().replace(tzinfo=pytz.UTC)

    def test_course_enrollment_open(self):
        start = self.now - datetime.timedelta(days=1)
        end = self.now + datetime.timedelta(days=1)
        course = CourseFactory(enrollment_start=start, enrollment_end=end)
        assert course_open_for_self_enrollment(course.id)

    def test_course_enrollment_closed_future(self):
        start = self.now + datetime.timedelta(days=1)
        end = self.now + datetime.timedelta(days=2)
        course = CourseFactory(enrollment_start=start, enrollment_end=end)
        assert not course_open_for_self_enrollment(course.id)

    def test_course_enrollment_closed_past(self):
        start = self.now - datetime.timedelta(days=2)
        end = self.now - datetime.timedelta(days=1)
        course = CourseFactory(enrollment_start=start, enrollment_end=end)
        assert not course_open_for_self_enrollment(course.id)

    def test_course_enrollment_dates_missing(self):
        course = CourseFactory()
        assert course_open_for_self_enrollment(course.id)

    def test_course_enrollment_dates_missing_start(self):
        end = self.now + datetime.timedelta(days=1)
        course = CourseFactory(enrollment_end=end)
        assert course_open_for_self_enrollment(course.id)

        end = self.now - datetime.timedelta(days=1)
        course = CourseFactory(enrollment_end=end)
        assert not course_open_for_self_enrollment(course.id)

    def test_course_enrollment_dates_missing_end(self):
        start = self.now - datetime.timedelta(days=1)
        course = CourseFactory(enrollment_start=start)
        assert course_open_for_self_enrollment(course.id)

        start = self.now + datetime.timedelta(days=1)
        course = CourseFactory(enrollment_start=start)
        assert not course_open_for_self_enrollment(course.id)


@ddt.ddt
class CourseInstantiationTests(ModuleStoreTestCase):
    """
    Tests around instantiating a course multiple times in the same request.
    """
    def setUp(self):
        super().setUp()

        self.factory = RequestFactory()

    @ddt.data(*itertools.product(range(5), [None, 0, 5]))
    @ddt.unpack
    def test_repeated_course_block_instantiation(self, loops, course_depth):

        with modulestore().default_store(ModuleStoreEnum.Type.split):
            course = CourseFactory.create()
            chapter = BlockFactory(parent=course, category='chapter', graded=True)
            section = BlockFactory(parent=chapter, category='sequential')
            __ = BlockFactory(parent=section, category='problem')

        fake_request = self.factory.get(
            reverse('progress', kwargs={'course_id': str(course.id)})
        )

        course = modulestore().get_course(course.id, depth=course_depth)

        for _ in range(loops):
            field_data_cache = FieldDataCache.cache_for_block_descendents(
                course.id, self.user, course, depth=course_depth
            )
            course_block = get_block_for_descriptor(
                self.user,
                fake_request,
                course,
                field_data_cache,
                course.id,
                course=course
            )
            for chapter in course_block.get_children():
                for section in chapter.get_children():
                    for item in section.get_children():
                        assert item.graded


class TestGetCourseChapters(ModuleStoreTestCase):
    """
    Tests for the `get_course_chapter_ids` function.
    """

    def test_get_non_existant_course(self):
        """
        Test non-existant course returns empty list.
        """
        assert get_course_chapter_ids(None) == []
        # build a fake key
        fake_course_key = CourseKey.from_string('course-v1:FakeOrg+CN1+CR-FALLNEVER1')
        assert get_course_chapter_ids(fake_course_key) == []

    def test_get_chapters(self):
        """
        Test get_course_chapter_ids returns expected result.
        """
        course = CourseFactory()
        BlockFactory(parent=course, category='chapter')
        BlockFactory(parent=course, category='chapter')
        course_chapter_ids = get_course_chapter_ids(course.location.course_key)
        assert len(course_chapter_ids) == 2
        assert course_chapter_ids == [str(child) for child in course.children]


class TestGetCourseAssignments(CompletionWaffleTestMixin, ModuleStoreTestCase):
    """
    Tests for the `get_course_assignments` function.
    """

    def test_completion_ignores_non_scored_items(self):
        """
        Test that we treat a sequential with incomplete (but not scored) items (like a video maybe) as complete.
        """
        course = CourseFactory()
        chapter = BlockFactory(parent=course, category='chapter', graded=True, due=datetime.datetime.now(),
                               start=datetime.datetime.now() - datetime.timedelta(hours=1))
        sequential = BlockFactory(parent=chapter, category='sequential')
        problem = BlockFactory(parent=sequential, category='problem', has_score=True)
        BlockFactory(parent=sequential, category='video', has_score=False)

        self.override_waffle_switch(True)
        BlockCompletion.objects.submit_completion(self.user, problem.location, 1)

        assignments = get_course_assignments(course.location.context_key, self.user, None)
        assert len(assignments) == 1
        assert assignments[0].complete

    def test_completion_does_not_count_empty_sequentials(self):
        """
        Test that we treat a sequential with no content as incomplete.

        This can happen with unreleased assignments, for example (start date in future).
        """
        course = CourseFactory()
        chapter = BlockFactory(parent=course, category='chapter', graded=True, due=datetime.datetime.now())
        BlockFactory(parent=chapter, category='sequential')

        assignments = get_course_assignments(course.location.context_key, self.user, None)
        assert len(assignments) == 1
        assert not assignments[0].complete

    def test_completion_does_not_treat_unreleased_as_complete(self):
        """
        Test that unreleased assignments are not treated as complete.
        """
        course = CourseFactory()
        chapter = BlockFactory(parent=course, category='chapter', graded=True,
                               due=datetime.datetime.now() + datetime.timedelta(hours=2),
                               start=datetime.datetime.now() + datetime.timedelta(hours=1))
        sequential = BlockFactory(parent=chapter, category='sequential')
        problem = BlockFactory(parent=sequential, category='problem', has_score=True)
        BlockFactory(parent=sequential, category='video', has_score=False)

        self.override_waffle_switch(True)
        BlockCompletion.objects.submit_completion(self.user, problem.location, 1)

        assignments = get_course_assignments(course.location.context_key, self.user, None)
        assert len(assignments) == 1
        assert not assignments[0].complete


@ddt.ddt
class TestGetCourseAssignmentsORA(CompletionWaffleTestMixin, ModuleStoreTestCase):
    """ Tests for ora-related behavior in get_course_assignments """
    TODAY = datetime.datetime(2023, 8, 2, 12, 23, 45, tzinfo=pytz.UTC)

    def setUp(self):
        super().setUp()
        self.freezer = freeze_time(self.TODAY)
        self.freezer.start()
        self.addCleanup(self.freezer.stop)

    def _date(self, t):
        """ Helper to easily generate sequential days """
        return datetime.timedelta(days=t) + self.TODAY

    # pylint: disable=attribute-defined-outside-init
    def _setup_course(
        self,
        course_dates=None,
        subsection_dates=None,
        ora_dates=None,
        date_config_type="manual",
        additional_rubric_assessments=None
    ):
        """
        Setup a course with one section, subsection, unit, and ORA

        With no arguments, the timeline of due dates is:
        T  | Date
        --------
        -1 | Course Starts
         0 | Current frozen time
         1 | Subsection, submission, and self-assessment open
         2 | submission is due
         4 | self-assessment is due and peer assessment opens
         5 | peer assessment is due
         6 | subsection is due
        10 | course ends
        """
        course_dates = course_dates or (self._date(-1), self._date(10))
        subsection_dates = subsection_dates or (self._date(1), self._date(6))
        ora_dates = ora_dates or {
            'response': (self._date(1), self._date(2)),
            'self': (self._date(1), self._date(4)),
            'peer': (self._date(4), self._date(5))
        }

        self.course = CourseFactory(start=course_dates[0], end=course_dates[1])
        self.section = BlockFactory(parent=self.course, category='chapter')
        self.subsection = BlockFactory(
            parent=self.section,
            category='sequential',
            graded=True,
            start=subsection_dates[0],
            due=subsection_dates[1],
        )
        vertical = BlockFactory(parent=self.subsection, category='vertical')

        rubric_assessments = [
            {
                'name': 'peer-assessment',
                'must_be_graded_by': 3,
                'must_grade': 5,
                'start': ora_dates['peer'][0].isoformat(),
                'due': ora_dates['peer'][1].isoformat(),
            },
            {
                'name': 'self-assessment',
                'start': ora_dates['self'][0].isoformat(),
                'due': ora_dates['self'][1].isoformat(),
            }
        ]
        if additional_rubric_assessments:
            rubric_assessments.extend(additional_rubric_assessments)

        self.openassessment = BlockFactory(
            parent=vertical,
            category='openassessment',
            rubric_assessments=rubric_assessments,
            submission_start=ora_dates['response'][0].isoformat(),
            submission_due=ora_dates['response'][1].isoformat(),
            date_config_type=date_config_type
        )

        self.course_end = course_dates[1]
        self.subsection_due = subsection_dates[1]
        self.submission_due = ora_dates['response'][1]
        self.peer_due = ora_dates['peer'][1]
        self.self_due = ora_dates['self'][1]

    def assert_ora_course_assignments(
        self,
        assignments,
        expected_date_submission,
        expected_date_peer,
        expected_date_self
    ):
        """
        Helper to assert that
         - there are four date blocks
         - The first one is for the subsection and the next three are the ora steps
         - the steps have the expected due dates
         """
        assert len(assignments) == 4

        assert assignments[0].block_key == self.subsection.location
        assert assignments[1].block_key == self.openassessment.location
        assert assignments[2].block_key == self.openassessment.location
        assert assignments[3].block_key == self.openassessment.location

        assert 'Submission' in assignments[1].title
        assert 'Peer' in assignments[2].title
        assert 'Self' in assignments[3].title

        assert assignments[1].date == expected_date_submission
        assert assignments[2].date == expected_date_peer
        assert assignments[3].date == expected_date_self

    def test_ora_date_config__manual(self):
        """
        When manual config is set, the dates for ora setps should be the step
        due dates
        """
        self._setup_course()
        self.assert_ora_course_assignments(
            get_course_assignments(self.course.location.context_key, self.user, None),
            self.submission_due,
            self.peer_due,
            self.self_due
        )

    def test_ora_date_config__subsection(self):
        """
        When subsection config is set, the dates for ora steps should all be the subsection due date
        """
        self._setup_course(date_config_type='subsection')
        self.assert_ora_course_assignments(
            get_course_assignments(self.course.location.context_key, self.user, None),
            self.subsection_due,
            self.subsection_due,
            self.subsection_due,
        )

    def test_ora_date_config__course_end(self):
        """
        When manual config is set, the dates for ora steps should all be the course end date
        """
        self._setup_course(date_config_type='course_end')
        self.assert_ora_course_assignments(
            get_course_assignments(self.course.location.context_key, self.user, None),
            self.course_end,
            self.course_end,
            self.course_end,
        )

    def test_course_end_none(self):
        """
        If the course has no end date defined and if the ora date config
        is set to course end, don't include due dates for the ORA assignment in the due dates
        """
        self._setup_course(
            course_dates=(self._date(-1), None),
            date_config_type='course_end'
        )
        assignments = get_course_assignments(self.course.location.context_key, self.user, None)
        assert len(assignments) == 1
        assert assignments[0].block_key == self.subsection.location

    def test_subsection_none(self):
        """
        If the subsection has no due date defined and if the ora date config
        is set to subsection, don't include due dates for the ORA assignment in the due dates
        """
        self._setup_course(
            subsection_dates=(self._date(1), None),
            date_config_type='subsection'
        )
        # Add another subsection with a due date, because the first subsection won't show up
        # without one
        subsection_2 = BlockFactory(
            parent=self.section,
            category='sequential',
            graded=True,
            start=self._date(2),
            due=self._date(3),
        )
        assignments = get_course_assignments(self.course.location.context_key, self.user, None)
        assert len(assignments) == 1
        assert assignments[0].block_key == subsection_2.location

    @ddt.data('manual', 'subsection', 'course_end')
    def test_ora_steps_with_no_due_date(self, config_type):
        additional_assessments = [
            {
                'name': 'assessment_that_is_never_due',
                'some_setting': 'whatever',
                'another_setting': 'meh',
            },
            {
                'name': 'another_ssessment_that_is_never_due',
                'favorite_fruit': 'pear',
                'favorite_color': 'green',
            }
        ]
        self._setup_course(
            additional_rubric_assessments=additional_assessments,
            date_config_type=config_type,
        )

        # There are no dates for these other steps
        assignments = get_course_assignments(self.course.location.context_key, self.user, None)
        assert len(assignments) == 4
        assert assignments[0].block_key == self.subsection.location
        assert 'Submission' in assignments[1].title
        assert 'Peer' in assignments[2].title
        assert 'Self' in assignments[3].title
