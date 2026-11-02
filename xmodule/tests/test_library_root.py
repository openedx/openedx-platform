"""
Basic unit tests for LibraryRoot
"""


from unittest.mock import patch
from web_fragments.fragment import Fragment
from xblock.runtime import Runtime as VanillaRuntime

from xmodule.modulestore.tests.factories import BlockFactory, LibraryFactory
from xmodule.modulestore.tests.utils import MixedSplitTestCase
from xmodule.x_module import AUTHOR_VIEW

dummy_render = lambda block, _: Fragment(block.data)  # pylint: disable=invalid-name


@patch(
    'xmodule.modulestore.split_mongo.runtime.SplitModuleStoreRuntime.render', VanillaRuntime.render
)
@patch('xmodule.html_block.HtmlBlock.author_view', dummy_render, create=True)
@patch('xmodule.html_block.HtmlBlock.has_author_view', True, create=True)
@patch('xmodule.x_module.ModuleStoreRuntime.applicable_aside_types', lambda self, block: [])
class TestLibraryRoot(MixedSplitTestCase):
    """
    Basic unit tests for LibraryRoot (library_root_xblock.py)
    """

    def test_library_author_view(self):
        """
        Test that LibraryRoot.author_view can run and includes content from its
        children.
        We have to patch the runtime (module system) in order to be able to
        render blocks in our test environment.
        """
        message = "Hello world"
        library = LibraryFactory.create(modulestore=self.store)
        # Add one HTML block to the library:
        BlockFactory.create(
            category="html",
            parent_location=library.location,
            user_id=self.user_id,
            publish_item=False,
            modulestore=self.store,
            data=message
        )
        library = self.store.get_library(library.location.library_key)

        context = {'reorderable_items': set(), }
        # Patch the HTML block to always render "Hello world"

        result = library.render(AUTHOR_VIEW, context)
        assert message in result.content

    def test_library_author_view_with_paging(self):
        """
        Test that LibraryRoot.author_view can apply paging
        We have to patch the runtime (module system) in order to be able to
        render blocks in our test environment.
        """
        library = LibraryFactory.create(modulestore=self.store)
        # Add five HTML blocks to the library:
        blocks = [
            BlockFactory.create(
                category="html",
                parent_location=library.location,
                user_id=self.user_id,
                publish_item=False,
                modulestore=self.store,
                data="HtmlBlock" + str(i)
            )
            for i in range(5)
        ]
        library = self.store.get_library(library.location.library_key)

        def render_and_check_contents(page, page_size):
            """ Renders block and asserts on returned content """
            context = {'reorderable_items': set(), 'paging': {'page_number': page, 'page_size': page_size}}
            expected_blocks = blocks[page_size * page:page_size * (page + 1)]
            result = library.render(AUTHOR_VIEW, context)

            for expected_block in expected_blocks:
                assert expected_block.data in result.content

        render_and_check_contents(0, 3)
        render_and_check_contents(1, 3)
        render_and_check_contents(0, 2)
        render_and_check_contents(1, 2)
