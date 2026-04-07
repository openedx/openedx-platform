"""
Test finding orphans via the view and django config
"""


import json

from opaque_keys.edx.locator import BlockUsageLocator

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import reverse_course_url
from common.djangoapps.student.models import CourseEnrollment
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.search import path_to_location  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import (  # lint-amnesty, pylint: disable=wrong-import-order
    CourseFactory,
    check_mongo_calls_range,
)


class TestOrphanBase(CourseTestCase):
    """
    Base class for Studio tests that require orphaned blocks
    """
    def create_course_with_orphans(self, default_store):
        """
        Creates a course with 3 orphan blocks, one of which
        has a child that's also in the course tree.
        """
        course = CourseFactory.create(default_store=default_store)

        # create chapters and add them to course tree
        chapter1 = self.store.create_child(self.user.id, course.location, 'chapter', "Chapter1")
        self.store.publish(chapter1.location, self.user.id)

        chapter2 = self.store.create_child(self.user.id, course.location, 'chapter', "Chapter2")
        self.store.publish(chapter2.location, self.user.id)

        # orphan chapter
        orphan_chapter = self.store.create_item(self.user.id, course.id, 'chapter', "OrphanChapter")
        self.store.publish(orphan_chapter.location, self.user.id)

        # create vertical and add it as child to chapter1
        vertical1 = self.store.create_child(self.user.id, chapter1.location, 'vertical', "Vertical1")
        self.store.publish(vertical1.location, self.user.id)

        # create orphan vertical
        orphan_vertical = self.store.create_item(self.user.id, course.id, 'vertical', "OrphanVert")
        self.store.publish(orphan_vertical.location, self.user.id)

        # create component and add it to vertical1
        html1 = self.store.create_child(self.user.id, vertical1.location, 'html', "Html1")
        self.store.publish(html1.location, self.user.id)

        # create component and add it as a child to vertical1 and orphan_vertical
        multi_parent_html = self.store.create_child(self.user.id, vertical1.location, 'html', "multi_parent_html")
        self.store.publish(multi_parent_html.location, self.user.id)

        orphan_vertical.children.append(multi_parent_html.location)
        self.store.update_item(orphan_vertical, self.user.id)

        # create an orphaned html block
        orphan_html = self.store.create_item(self.user.id, course.id, 'html', "OrphanHtml")
        self.store.publish(orphan_html.location, self.user.id)

        self.store.create_child(self.user.id, course.location, 'static_tab', "staticuno")
        self.store.create_child(self.user.id, course.location, 'course_info', "updates")

        return course

    def assertOrphanCount(self, course_key, number):
        """
        Asserts that we have the expected count of orphans
        for a given course_key
        """
        self.assertEqual(len(self.store.get_orphans(course_key)), number)


class TestOrphan(TestOrphanBase):
    """
    Test finding orphans via view and django config
    """

    def test_get_orphans(self):
        """
        Test that the orphan handler finds the orphans
        """
        course = self.create_course_with_orphans(ModuleStoreEnum.Type.split)
        orphan_url = reverse_course_url('orphan_handler', course.id)

        orphans = json.loads(
            self.client.get(
                orphan_url,
                HTTP_ACCEPT='application/json'
            ).content.decode('utf-8')
        )
        self.assertEqual(len(orphans), 3, f"Wrong # {orphans}")
        location = course.location.replace(category='chapter', name='OrphanChapter')
        self.assertIn(str(location), orphans)
        location = course.location.replace(category='vertical', name='OrphanVert')
        self.assertIn(str(location), orphans)
        location = course.location.replace(category='html', name='OrphanHtml')
        self.assertIn(str(location), orphans)

    def test_delete_orphans(self):
        """
        Test that the orphan handler deletes the orphans
        """
        course = self.create_course_with_orphans(ModuleStoreEnum.Type.split)
        orphan_url = reverse_course_url('orphan_handler', course.id)

        with check_mongo_calls_range(5, 3):
            self.client.delete(orphan_url)

        orphans = json.loads(
            self.client.get(orphan_url, HTTP_ACCEPT='application/json').content.decode('utf-8')
        )
        self.assertEqual(len(orphans), 0, f"Orphans not deleted {orphans}")

        # make sure that any children with one orphan parent and one non-orphan
        # parent are not deleted
        self.assertTrue(self.store.has_item(course.id.make_usage_key('html', "multi_parent_html")))

    def test_not_permitted(self):
        """
        Test that auth restricts get and delete appropriately
        """
        course = self.create_course_with_orphans(ModuleStoreEnum.Type.split)
        orphan_url = reverse_course_url('orphan_handler', course.id)

        test_user_client, test_user = self.create_non_staff_authed_user_client()
        CourseEnrollment.enroll(test_user, course.id)
        response = test_user_client.get(orphan_url)
        self.assertEqual(response.status_code, 403)
        response = test_user_client.delete(orphan_url)
        self.assertEqual(response.status_code, 403)

    def test_path_to_location_for_orphan_vertical(self):
        r"""
        Make sure that path_to_location works with a component having multiple vertical parents,
        from which one of them is orphan.

         course
            |
         chapter
           |
         vertical vertical
            \     /
              html
        """
        # Get a course with orphan blocks
        course = self.create_course_with_orphans(ModuleStoreEnum.Type.split)

        # Fetch the required course components.
        vertical1 = self.store.get_item(BlockUsageLocator(course.id, 'vertical', 'Vertical1'))
        chapter1 = self.store.get_item(BlockUsageLocator(course.id, 'chapter', 'Chapter1'))
        orphan_vertical = self.store.get_item(BlockUsageLocator(course.id, 'vertical', 'OrphanVert'))
        multi_parent_html = self.store.get_item(BlockUsageLocator(course.id, 'html', 'multi_parent_html'))

        # Verify `OrphanVert` is an orphan
        self.assertIn(orphan_vertical.location, self.store.get_orphans(course.id))

        # Verify `multi_parent_html` is child of both `Vertical1` and `OrphanVert`
        self.assertIn(multi_parent_html.location, orphan_vertical.children)
        self.assertIn(multi_parent_html.location, vertical1.children)

        # HTML component has `vertical1` as its parent.
        html_parent = self.store.get_parent_location(multi_parent_html.location)
        self.assertNotEqual(str(html_parent), str(orphan_vertical.location))
        self.assertEqual(str(html_parent), str(vertical1.location))

        # Get path of the `multi_parent_html` & verify path_to_location returns a expected path
        path = path_to_location(self.store, multi_parent_html.location)
        expected_path = (
            course.id,
            chapter1.location.block_id,
            vertical1.location.block_id,
            multi_parent_html.location.block_id,
            "",
            path[-1]
        )
        self.assertIsNotNone(path)
        self.assertEqual(len(path), 6)
        self.assertEqual(path, expected_path)

    def test_path_to_location_for_orphan_chapter(self):
        r"""
        Make sure that path_to_location works with a component having multiple chapter parents,
        from which one of them is orphan

         course
            |
        chapter   chapter
           |         |
        vertical  vertical
              \    /
               html

        """
        # Get a course with orphan blocks
        course = self.create_course_with_orphans(ModuleStoreEnum.Type.split)
        orphan_chapter = self.store.get_item(BlockUsageLocator(course.id, 'chapter', 'OrphanChapter'))
        chapter1 = self.store.get_item(BlockUsageLocator(course.id, 'chapter', 'Chapter1'))
        vertical1 = self.store.get_item(BlockUsageLocator(course.id, 'vertical', 'Vertical1'))

        # Verify `OrhanChapter` is an orphan
        self.assertIn(orphan_chapter.location, self.store.get_orphans(course.id))

        # Create a vertical (`Vertical0`) in orphan chapter (`OrphanChapter`).
        # OrphanChapter -> Vertical0
        vertical0 = self.store.create_child(self.user.id, orphan_chapter.location, 'vertical', "Vertical0")
        self.store.publish(vertical0.location, self.user.id)

        # Create a component in `Vertical0`
        # OrphanChapter -> Vertical0 -> Html
        html = self.store.create_child(self.user.id, vertical0.location, 'html', "HTML0")
        self.store.publish(html.location, self.user.id)

        # Verify chapter1 is parent of vertical1.
        vertical1_parent = self.store.get_parent_location(vertical1.location)
        self.assertEqual(str(vertical1_parent), str(chapter1.location))

        # Make `Vertical1` the parent of `HTML0`. So `HTML0` will have to parents (`Vertical0` & `Vertical1`)
        vertical1.children.append(html.location)
        self.store.update_item(vertical1, self.user.id)

        # Get parent location & verify its either of the two verticals. As both parents are non-orphan,
        # alphabetically least is returned
        html_parent = self.store.get_parent_location(html.location)
        self.assertEqual(str(html_parent), str(vertical1.location))

        # verify path_to_location returns a expected path
        path = path_to_location(self.store, html.location)
        expected_path = (
            course.id,
            chapter1.location.block_id,
            vertical1.location.block_id,
            html.location.block_id,
            "",
            path[-1]
        )
        self.assertIsNotNone(path)
        self.assertEqual(len(path), 6)
        self.assertEqual(path, expected_path)
