"""
Tests for the lms_result_processor
"""
import pytest

from common.djangoapps.student.tests.factories import UserFactory
from lms.lib.courseware_search.lms_result_processor import LmsSearchResultProcessor
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import (  # lint-amnesty, pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
)


class LmsSearchResultProcessorTestCase(ModuleStoreTestCase):
    """ Test case class to test search result processor """

    def build_course(self):
        """
        Build up a course tree with an html control
        """
        self.global_staff = UserFactory(is_staff=True)

        self.course = CourseFactory.create(
            org='Elasticsearch',
            course='ES101',
            run='test_run',
            display_name='Elasticsearch test course',
        )
        self.section = BlockFactory.create(
            parent=self.course,
            category='chapter',
            display_name='Test Section',
        )
        self.subsection = BlockFactory.create(
            parent=self.section,
            category='sequential',
            display_name='Test Subsection',
        )
        self.vertical = BlockFactory.create(
            parent=self.subsection,
            category='vertical',
            display_name='Test Unit',
        )
        self.html = BlockFactory.create(
            parent=self.vertical,
            category='html',
            display_name='Test Html control',
        )
        self.ghost_subsection = BlockFactory.create(
            parent=self.section,
            category='sequential',
            display_name=None,
        )
        self.ghost_vertical = BlockFactory.create(
            parent=self.ghost_subsection,
            category='vertical',
            display_name=None,
        )
        self.ghost_html = BlockFactory.create(
            parent=self.ghost_vertical,
            category='html',
            display_name='Ghost Html control',
        )

    def setUp(self):
        super().setUp()
        self.build_course()

    def test_url_parameter(self):
        fake_url = ""
        srp = LmsSearchResultProcessor({}, "test")
        with pytest.raises(ValueError):
            fake_url = srp.url
        assert fake_url == ''

        srp = LmsSearchResultProcessor(
            {
                "course": str(self.course.id),
                "id": str(self.html.scope_ids.usage_id),
                "content": {"text": "This is the html text"}
            },
            "test"
        )

        assert srp.url == '/courses/{}/jump_to/{}'.format(str(self.course.id),
                                                          str(self.html.scope_ids.usage_id))

    def test_should_remove(self):
        """
        Tests that "visible_to_staff_only" overrides start date.
        """
        srp = LmsSearchResultProcessor(
            {
                "course": str(self.course.id),
                "id": str(self.html.scope_ids.usage_id),
                "content": {"text": "This is html test text"}
            },
            "test"
        )

        assert srp.should_remove(self.global_staff) is False
