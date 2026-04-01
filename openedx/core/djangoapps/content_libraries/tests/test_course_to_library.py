"""
Tests for Imports from Courses to openedx_content-based Content Libraries
"""
import ddt
from opaque_keys.edx.locator import LibraryContainerLocator

from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import ToyCourseFactory
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content_libraries.tests.base import ContentLibrariesRestApiTest
from openedx.core.djangolib.testing.utils import skip_unless_cms


@skip_unless_cms
@ddt.ddt
class CourseToLibraryTestCase(ContentLibrariesRestApiTest, ModuleStoreTestCase):
    """
    Tests that involve copying content from courses to libraries.
    """

    def test_library_paste_unit_from_course(self):
        """
        Test that we can paste a Unit from a course into a Library, and it gets
        converted from an XBlock to a Container.
        """
        # Create user to perform tests on
        author = UserFactory.create(username="Author", email="author@example.com", is_staff=True)
        with self.as_user(author):
            lib = self._create_library(
                slug="test_lib_paste_clipboard",
                title="Paste Clipboard Test Library",
                description="Testing pasting clipboard in library"
            )
            lib_id = lib["id"]

            course_key = ToyCourseFactory.create().id  # See xmodule/modulestore/tests/sample_courses.py
            unit_key = course_key.make_usage_key("vertical", "vertical_test")

            # Copy the unit
            self._api('post', "/api/content-staging/v1/clipboard/", {"usage_key": str(unit_key)}, expect_response=200)

            # Paste the content of the clipboard into the library
            paste_data = self._paste_clipboard_content_in_library(lib_id)
            pasted_container_key = LibraryContainerLocator.from_string(paste_data["id"])

            self.assertDictContainsEntries(self._get_container(paste_data["id"]), {
                "id": str(pasted_container_key),
                "container_type": "unit",
                "display_name": "Unit",
                "last_draft_created_by": author.username,
                "last_draft_created": paste_data["last_draft_created"],
                "created": paste_data["created"],
                "modified": paste_data["modified"],
                "last_published": None,
                "published_by": None,
                "has_unpublished_changes": True,
                "collections": [],
                "can_stand_alone": True,
            })

            children = self._get_container_children(paste_data["id"])
            assert len(children) == 4

            self.assertDictContainsEntries(children[0], {"display_name": "default", "block_type": "video"})
            assert children[0]["id"].startswith("lb:CL-TEST:test_lib_paste_clipboard:video:default-")
            assert "container_type" not in children[0]

            self.assertDictContainsEntries(children[1], {"display_name": "default", "block_type": "video"})
            assert children[1]["id"].startswith("lb:CL-TEST:test_lib_paste_clipboard:video:default-")
            assert children[0]["id"] != children[1]["id"]

            self.assertDictContainsEntries(children[2], {"display_name": "default", "block_type": "video"})
            assert children[2]["id"].startswith("lb:CL-TEST:test_lib_paste_clipboard:video:default-")
            assert children[0]["id"] != children[2]["id"]

            self.assertDictContainsEntries(children[3], {
                "display_name": "Change your answer",
                "block_type": "poll_question",
            })
            assert children[3]["id"].startswith("lb:CL-TEST:test_lib_paste_clipboard:poll_question:change-your-answer-")
