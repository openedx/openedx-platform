"""
Tests for the lms_filter_generator
"""
from unittest.mock import Mock, patch

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from lms.lib.courseware_search.lms_filter_generator import LmsSearchFilterGenerator
from xmodule.course_block import CATALOG_VISIBILITY_ABOUT, CATALOG_VISIBILITY_NONE
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import (  # pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
)


class LmsSearchFilterGeneratorTestCase(ModuleStoreTestCase):
    """ Tests for search result processor """

    def build_courses(self):
        """
        Build up a course tree with multiple test courses
        """

        self.courses = [
            CourseFactory.create(
                org='ElasticsearchFiltering',
                course='ES101F',
                run='test_run',
                display_name='Elasticsearch Filtering test course',
            ),

            CourseFactory.create(
                org='FilterTest',
                course='FT101',
                run='test_run',
                display_name='FilterTest test course',
            )
        ]

        self.chapter = BlockFactory.create(
            parent_location=self.courses[0].location,
            category='chapter',
            display_name="Week 1",
            publish_item=True,
        )

        self.chapter2 = BlockFactory.create(
            parent_location=self.courses[1].location,
            category='chapter',
            display_name="Week 1",
            publish_item=True,
        )

    def setUp(self):
        super().setUp()
        self.build_courses()
        self.user = UserFactory.create(username="jack", email="jack@fake.edx.org", password='test')

        for course in self.courses:
            CourseEnrollment.enroll(self.user, course.location.course_key)

    def test_course_id_not_provided(self):
        """
        Tests that we get the list of IDs of courses the user is enrolled in when the course ID is null or not provided
        """
        field_dictionary, filter_dictionary, _ = LmsSearchFilterGenerator.generate_field_filters(user=self.user)

        assert 'start_date' in filter_dictionary
        assert str(self.courses[0].id) in field_dictionary['course']
        assert str(self.courses[1].id) in field_dictionary['course']

    def test_course_id_provided(self):
        """
        Tests that we get the course ID when the course ID is provided
        """
        field_dictionary, filter_dictionary, _ = LmsSearchFilterGenerator.generate_field_filters(
            user=self.user,
            course_id=str(self.courses[0].id)
        )

        assert 'start_date' in filter_dictionary
        assert str(self.courses[0].id) == field_dictionary['course']

    def test_user_not_provided(self):
        """
        Tests that we get empty list of courses in case the user is not provided
        """
        field_dictionary, filter_dictionary, _ = LmsSearchFilterGenerator.generate_field_filters()

        assert 'start_date' in filter_dictionary
        assert 0 == len(field_dictionary['course'])

    @patch(
        'openedx.core.djangoapps.site_configuration.helpers.get_all_orgs',
        Mock(return_value=["LogistrationX", "TestSiteX"])
    )
    def test_excludes_site_org(self):
        """
        By default site orgs not belonging to current site org should be excluded.
        """
        _, _, exclude_dictionary = LmsSearchFilterGenerator.generate_field_filters(user=self.user)
        assert 'org' in exclude_dictionary
        exclude_orgs = exclude_dictionary['org']
        assert 2 == len(exclude_orgs)
        assert 'LogistrationX' == exclude_orgs[0]
        assert 'TestSiteX' == exclude_orgs[1]

    @patch('openedx.core.djangoapps.site_configuration.helpers.get_all_orgs', Mock(return_value=set()))
    def test_no_excludes_with_no_orgs(self):
        """ Test when no org is present - nothing to exclude """
        _, _, exclude_dictionary = LmsSearchFilterGenerator.generate_field_filters(user=self.user)
        assert 'org' not in exclude_dictionary

    @patch('openedx.core.djangoapps.site_configuration.helpers.get_value', Mock(return_value='TestSiteX'))
    def test_excludes_org_within(self):
        field_dictionary, _, exclude_dictionary = LmsSearchFilterGenerator.generate_field_filters(user=self.user)
        assert 'org' not in exclude_dictionary
        assert 'org' in field_dictionary
        assert ['TestSiteX'] == field_dictionary['org']

    @patch(
        'openedx.core.djangoapps.site_configuration.helpers.get_all_orgs',
        Mock(return_value={"TestSite1", "TestSite2", "TestSite3", "TestSite4"})
    )
    def test_excludes_multi_orgs(self):
        _, _, exclude_dictionary = LmsSearchFilterGenerator.generate_field_filters(user=self.user)
        assert 'org' in exclude_dictionary
        exclude_orgs = exclude_dictionary['org']
        assert 4 == len(exclude_orgs)
        assert 'TestSite1' in exclude_orgs
        assert 'TestSite2' in exclude_orgs
        assert 'TestSite3' in exclude_orgs
        assert 'TestSite4' in exclude_orgs

    @patch(
        'openedx.core.djangoapps.site_configuration.helpers.get_all_orgs',
        Mock(return_value={"TestSite1", "TestSite2", "TestSite3", "TestSite4"})
    )
    @patch('openedx.core.djangoapps.site_configuration.helpers.get_value', Mock(return_value='TestSite3'))
    def test_excludes_multi_orgs_within(self):
        field_dictionary, _, exclude_dictionary = LmsSearchFilterGenerator.generate_field_filters(user=self.user)
        assert 'org' not in exclude_dictionary
        assert 'org' in field_dictionary
        assert ['TestSite3'] == field_dictionary['org']

    @patch('django.conf.settings.SEARCH_SKIP_SHOW_IN_CATALOG_FILTERING', False)
    def test_excludes_catalog_visibility(self):
        _, _, exclude_dictionary = LmsSearchFilterGenerator.generate_field_filters(user=self.user)
        assert 'catalog_visibility' in exclude_dictionary
        assert exclude_dictionary['catalog_visibility'] == [CATALOG_VISIBILITY_ABOUT, CATALOG_VISIBILITY_NONE]
