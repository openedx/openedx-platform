"""
Integration tests for importing courses containing pure XBlocks.
"""


from django.conf import settings
from xblock.core import XBlock
from xblock.fields import String

from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.xml_importer import import_course_from_xml

TEST_DATA_DIR = settings.COMMON_TEST_DATA_ROOT


class StubXBlock(XBlock):
    """
    Stub XBlock to use in tests.

    The default XBlock implementation will load this XBlock
    from XML, using the lowercase version of the class
    as an element name ("stubxblock") and the field names
    as attributes of that element.

    Example:
        <stubxblock test_field="this is only a test" />
    """
    test_field = String(default="default")


class XBlockImportTest(ModuleStoreTestCase):
    """Test class to verify xblock import operations"""

    @XBlock.register_temp_plugin(StubXBlock)
    def test_import_public(self):
        self._assert_import(
            'pure_xblock_public',
            'set by xml'
        )

    def _assert_import(self, course_dir, expected_field_val, has_draft=False):
        """
        Import a course from XML, then verify that the XBlock was loaded
        with the correct field value.

        Args:
            course_dir (str): The name of the course directory (relative to the test data directory)
            expected_xblock_loc (str): The location of the XBlock in the course.
            expected_field_val (str): The expected value of the XBlock's test field.

        Kwargs:
            has_draft (bool): If true, check that a draft of the XBlock exists with
                the expected field value set.

        """
        # It is necessary to use the "old mongo" modulestore because split doesn't work
        # with the "has_draft" logic below.
        courses = import_course_from_xml(
            self.store, self.user.id, TEST_DATA_DIR, [course_dir], create_if_not_present=True
        )

        xblock_location = courses[0].id.make_usage_key('stubxblock', 'xblock_test')

        xblock = self.store.get_item(xblock_location)
        self.assertTrue(isinstance(xblock, StubXBlock))  # noqa: PT009
        self.assertEqual(xblock.test_field, expected_field_val)  # noqa: PT009
