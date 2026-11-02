"""
Tests for Asides
"""


from unittest import TestCase
from unittest.mock import patch

from web_fragments.fragment import Fragment
from xblock.core import XBlockAside
from xblock.fields import Scope, String

from xmodule.modulestore.tests.utils import XmlModulestoreBuilder


class AsideTestType(XBlockAside):
    """
    Test Aside type
    """
    FRAG_CONTENT = "<p>Aside rendered</p>"

    content = String(default="default_content", scope=Scope.content)
    data_field = String(default="default_data", scope=Scope.settings)

    @XBlockAside.aside_for('student_view')
    def student_view_aside(self, block, context):  # pylint: disable=unused-argument
        """Add to the student view"""
        return Fragment(self.FRAG_CONTENT)


class TestAsidesXmlStore(TestCase):
    """
    Test Asides sourced from xml store
    """

    @patch(
        'xmodule.modulestore.xml.XMLImportingModuleStoreRuntime.applicable_aside_types',
        lambda self, block: ['test_aside']
    )
    @XBlockAside.register_temp_plugin(AsideTestType, 'test_aside')
    def test_xml_aside(self):
        """
        Check that the xml modulestore read in all the asides with their values
        """
        with XmlModulestoreBuilder().build(course_ids=['edX/aside_test/2012_Fall']) as (__, store):
            def check_block(block):
                """
                Check whether block has the expected aside w/ its fields and then recurse to the block's children
                """
                asides = block.runtime.get_asides(block)
                assert len(asides) == 1, f'Found {asides} asides but expected only test_aside'
                assert isinstance(asides[0], AsideTestType)
                category = block.scope_ids.block_type
                assert asides[0].data_field == f'{category} aside data'
                assert asides[0].content == f'{category.capitalize()} Aside'

                for child in block.get_children():
                    check_block(child)

            check_block(store.get_course(store.make_course_key('edX', "aside_test", "2012_Fall")))
