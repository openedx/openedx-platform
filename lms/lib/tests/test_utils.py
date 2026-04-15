"""
Tests for the LMS/lib utils
"""


from lms.lib import utils
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import (  # lint-amnesty, pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
)


class LmsUtilsTest(ModuleStoreTestCase):
    """
    Tests for the LMS utility functions
    """

    def setUp(self):
        """
        Setup a dummy course content.
        """
        super().setUp()

        self.course = CourseFactory.create()
        self.chapter = BlockFactory.create(category="chapter", parent_location=self.course.location)
        self.sequential = BlockFactory.create(category="sequential", parent_location=self.chapter.location)
        self.vertical = BlockFactory.create(category="vertical", parent_location=self.sequential.location)
        self.html_block_1 = BlockFactory.create(category="html", parent_location=self.vertical.location)
        self.vertical_with_container = BlockFactory.create(
            category="vertical", parent_location=self.sequential.location
        )
        self.child_container = BlockFactory.create(
            category="split_test", parent_location=self.vertical_with_container.location)
        self.child_vertical = BlockFactory.create(category="vertical", parent_location=self.child_container.location)
        self.child_html_block = BlockFactory.create(category="html", parent_location=self.child_vertical.location)

        # Read again so that children lists are accurate
        self.course = self.store.get_item(self.course.location)
        self.chapter = self.store.get_item(self.chapter.location)
        self.sequential = self.store.get_item(self.sequential.location)
        self.vertical = self.store.get_item(self.vertical.location)

        self.vertical_with_container = self.store.get_item(self.vertical_with_container.location)
        self.child_container = self.store.get_item(self.child_container.location)
        self.child_vertical = self.store.get_item(self.child_vertical.location)
        self.child_html_block = self.store.get_item(self.child_html_block.location)

    def test_get_parent_unit(self):
        """
        Tests `get_parent_unit` method for the successful result.
        """
        parent = utils.get_parent_unit(self.html_block_1)
        assert parent.location == self.vertical.location

        parent = utils.get_parent_unit(self.child_html_block)
        assert parent.location == self.vertical_with_container.location

        assert utils.get_parent_unit(None) is None
        assert utils.get_parent_unit(self.vertical) is None
        assert utils.get_parent_unit(self.course) is None
        assert utils.get_parent_unit(self.chapter) is None
        assert utils.get_parent_unit(self.sequential) is None

    def test_is_unit(self):
        """
        Tests `is_unit` method for the successful result.
        """
        assert not utils.is_unit(self.html_block_1)
        assert not utils.is_unit(self.child_vertical)
        assert utils.is_unit(self.vertical)
