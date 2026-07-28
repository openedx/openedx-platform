"""
Tests Draft import order.
"""


from django.conf import settings

from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.xml_importer import import_course_from_xml

TEST_DATA_DIR = settings.COMMON_TEST_DATA_ROOT


# This test is in the CMS module because the test configuration to use a draft
# modulestore is dependent on django.
class DraftReorderTestCase(ModuleStoreTestCase):  # pylint: disable=missing-class-docstring

    def test_order(self):
        """
        Verify that drafts are imported in the correct order.
        """
        store = modulestore()
        course_items = import_course_from_xml(
            store, self.user.id, TEST_DATA_DIR, ['import_draft_order'], create_if_not_present=True
        )
        course_key = course_items[0].id
        sequential = store.get_item(course_key.make_usage_key('sequential', '0f4f7649b10141b0bdc9922dcf94515a'))
        verticals = sequential.children

        # The order that files are read in from the file system is not guaranteed (cannot rely on
        # alphabetical ordering, for example). Therefore, I have added a lot of variation in filename and desired
        # ordering so that the test reliably failed with the bug, at least on Linux.
        #
        # 'a', 'b', 'c', 'd', and 'z' are all drafts, with 'index_in_children_list' of
        #  2 ,  4 ,  6 ,  5 , and  0  respectively.
        #
        # '5a05be9d59fc4bb79282c94c9e6b88c7' and 'second' are public verticals.
        self.assertEqual(7, len(verticals))  # noqa: PT009
        self.assertEqual(course_key.make_usage_key('vertical', 'z'), verticals[0])  # noqa: PT009
        self.assertEqual(course_key.make_usage_key('vertical', '5a05be9d59fc4bb79282c94c9e6b88c7'), verticals[1])  # noqa: PT009  # pylint: disable=line-too-long
        self.assertEqual(course_key.make_usage_key('vertical', 'a'), verticals[2])  # noqa: PT009
        self.assertEqual(course_key.make_usage_key('vertical', 'second'), verticals[3])  # noqa: PT009
        self.assertEqual(course_key.make_usage_key('vertical', 'b'), verticals[4])  # noqa: PT009
        self.assertEqual(course_key.make_usage_key('vertical', 'd'), verticals[5])  # noqa: PT009
        self.assertEqual(course_key.make_usage_key('vertical', 'c'), verticals[6])  # noqa: PT009

        # Now also test that the verticals in a second sequential are correct.
        sequential = store.get_item(course_key.make_usage_key('sequential', 'secondseq'))
        verticals = sequential.children
        # 'asecond' and 'zsecond' are drafts with 'index_in_children_list' 0 and 2, respectively.
        # 'secondsubsection' is a public vertical.
        self.assertEqual(3, len(verticals))  # noqa: PT009
        self.assertEqual(course_key.make_usage_key('vertical', 'asecond'), verticals[0])  # noqa: PT009
        self.assertEqual(course_key.make_usage_key('vertical', 'secondsubsection'), verticals[1])  # noqa: PT009
        self.assertEqual(course_key.make_usage_key('vertical', 'zsecond'), verticals[2])  # noqa: PT009
