"""
Test some of the functions in url_helpers
"""
import ddt
from django.test import TestCase
from django.test.client import RequestFactory
from django.test.utils import override_settings
from unittest.mock import patch

from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory

from .. import url_helpers


@ddt.ddt
class IsLearningMfeTests(TestCase):
    """
    Test is_request_from_learning_mfe.
    """

    def setUp(self):
        super().setUp()
        self.request_factory = RequestFactory()

    @ddt.data(
        ('', '', False,),
        ('https://mfe-url/', 'https://platform-url/course', False,),
        ('https://mfe-url/', 'https://mfe-url/course', True,),
        ('https://mfe-url/', 'https://mfe-url/', True,),
        ('https://mfe-url/subpath/', 'https://platform-url/course', False,),
        ('https://mfe-url/subpath/', 'https://mfe-url/course', True,),
        ('https://mfe-url/subpath/', 'https://mfe-url/', True,),
    )
    @ddt.unpack
    def test_is_request_from_learning_mfe(self, mfe_url, referrer_url, is_mfe):
        """
        Test requests are marked as from the Learning MFE when HTTP_REFERER matches LEARNING_MICROFRONTEND_URL.
        """
        with override_settings(LEARNING_MICROFRONTEND_URL=mfe_url):
            with patch.object(url_helpers.configuration_helpers, 'get_value', return_value=mfe_url):
                request = self.request_factory.get('/course')
                request.META['HTTP_REFERER'] = referrer_url
                assert url_helpers.is_request_from_learning_mfe(request) == is_mfe


@ddt.ddt
class GetCoursewareUrlTests(SharedModuleStoreTestCase):
    """
    Test get_courseware_url.
    """

    @classmethod
    def setUpClass(cls):
        """
        Set up data used across test functions.
        """
        # pylint: disable=super-method-not-called
        with super().setUpClassAndTestData():
            cls.items = cls.create_test_courses()

    @classmethod
    def create_test_courses(cls):
        """
        We build simple course structures.
        Course structure is a non-branching tree from the root Course block down
        to the Component-level problem block;

        For easy access in the test functions, we return a dict like this:
        {
            "course_run": <course block for Split Mongo course>,
            "section": <chapter block in course run>
            "subsection": <sequence block in section>
            "unit": <vertical block in subsection>
            "component": <problem block in unit>
        }
        """

        course_run = CourseFactory.create(
            org='TestX',
            number='UrlHelpers',
            run='split',
            display_name='URL Helpers Test Course',
        )
        with cls.store.bulk_operations(course_run.id):
            section = BlockFactory.create(
                parent_location=course_run.location,
                category='chapter',
                display_name="Generated Section",
            )
            subsection = BlockFactory.create(
                parent_location=section.location,
                category='sequential',
                display_name="Generated Subsection",
            )
            unit = BlockFactory.create(
                parent_location=subsection.location,
                category='vertical',
                display_name="Generated Unit",
            )
            component = BlockFactory.create(
                parent_location=unit.location,
                category='problem',
                display_name="Generated Problem Component",
            )

        return {
            'course_run': course_run,
            'section': section,
            'subsection': subsection,
            'unit': unit,
            'component': component,
        }

    @ddt.data(
        (
            'course_run',
            'http://learning-mfe/course/course-v1:TestX+UrlHelpers+split'
        ),
        (
            'section',
            (
                'http://learning-mfe/course/course-v1:TestX+UrlHelpers+split' +
                '/block-v1:TestX+UrlHelpers+split+type@chapter+block@Generated_Section'
            ),
        ),
        (
            'subsection',
            (
                'http://learning-mfe/course/course-v1:TestX+UrlHelpers+split' +
                '/block-v1:TestX+UrlHelpers+split+type@sequential+block@Generated_Subsection'
            ),
        ),
        (
            'unit',
            (
                'http://learning-mfe/course/course-v1:TestX+UrlHelpers+split' +
                '/block-v1:TestX+UrlHelpers+split+type@sequential+block@Generated_Subsection' +
                '/block-v1:TestX+UrlHelpers+split+type@vertical+block@Generated_Unit'
            ),
        ),
        (
            'component',
            (
                'http://learning-mfe/course/course-v1:TestX+UrlHelpers+split' +
                '/block-v1:TestX+UrlHelpers+split+type@sequential+block@Generated_Subsection' +
                '/block-v1:TestX+UrlHelpers+split+type@vertical+block@Generated_Unit'
            ),
        ),
    )
    @ddt.unpack
    def test_get_courseware_url(
        self,
        structure_level,
        expected_path,
    ):
        """
        Given:
        * a `store_type` ('split' or [old] 'mongo'),
        * an `active_experience` ('mfe' or 'legacy'),
        * and a `structure_level` ('course_run', 'section', 'subsection', 'unit', or 'component'),

        check that the expected path (URL without querystring) is returned by `get_courseware_url`.
        """
        block = self.items[structure_level]
        with patch.object(url_helpers.configuration_helpers, 'get_value', return_value='http://learning-mfe'):
            url = url_helpers.get_courseware_url(block.location)
        path = url.split('?')[0]
        assert path == expected_path
        course_run = self.items['course_run']
