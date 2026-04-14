"""
Tests for StudioEditableBlock.
"""


from xmodule.tests.test_vertical import BaseVerticalBlockTest
from xmodule.x_module import AUTHOR_VIEW


class StudioEditableBlockTestCase(BaseVerticalBlockTest):
    """
    Class containing StudioEditableBlock tests.
    """

    def test_render_reorderable_children(self):
        """
        Test the behavior of render_reorderable_children.
        """
        reorderable_items = set()
        context = {
            'reorderable_items': reorderable_items,
            'read_only': False,
            'root_xblock': self.vertical,
        }

        # Both children of the vertical should be rendered as reorderable
        self.course.runtime.render(self.vertical, AUTHOR_VIEW, context).content  # pylint: disable=expression-not-assigned  # noqa: B018
        assert self.vertical.get_children()[0].location in reorderable_items
        assert self.vertical.get_children()[1].location in reorderable_items
