"""
Tests verifying that xblock_handler enforces the correct permissions.
"""
import json
from unittest.mock import patch

from opaque_keys.edx.keys import UsageKey

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from common.djangoapps.student.roles import CourseStaffRole
from openedx.core import toggles as core_toggles
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


class XBlockHandlerPermissionsTest(CourseTestCase):
    """
    Tests for xblock_storage_handlers.view_handlers.handle_xblock.

    Verifies legacy permission enforcement (staff vs non-staff).
    """

    def setUp(self):
        super().setUp()
        self.chapter = BlockFactory.create(category='chapter', parent_location=self.course.location)
        self.sequential = BlockFactory.create(category='sequential', parent_location=self.chapter.location)
        self.vertical = BlockFactory.create(category='vertical', parent_location=self.sequential.location)
        self.html_block = BlockFactory.create(category='html', parent_location=self.vertical.location)
        self.static_tab = BlockFactory.create(category='static_tab', parent_location=self.course.location)
        self.non_staff_client, self.non_staff_user = self.create_non_staff_authed_user_client()

    # --- GET /xblock/{blockId} ---

    def test_get_block_fields_staff_allowed(self):
        self.assertEqual(self.client.get_json(f'/xblock/{self.html_block.location}').status_code, 200)  # noqa: PT009

    def test_get_block_fields_non_staff_forbidden(self):
        self.assertEqual(self.non_staff_client.get_json(f'/xblock/{self.html_block.location}').status_code, 403)  # noqa: PT009  # pylint: disable=line-too-long

    # --- POST /xblock/{blockId} metadata ---

    def test_post_metadata_staff_allowed(self):
        resp = self.client.ajax_post(
            f'/xblock/{self.html_block.location}', data={'metadata': {'display_name': 'New Name'}}
        )
        self.assertEqual(resp.status_code, 200)  # noqa: PT009

    def test_post_metadata_non_staff_forbidden(self):
        resp = self.non_staff_client.ajax_post(
            f'/xblock/{self.html_block.location}', data={'metadata': {'display_name': 'New Name'}}
        )
        self.assertEqual(resp.status_code, 403)  # noqa: PT009

    # --- POST /xblock/{blockId} publish ---

    def test_publish_staff_allowed(self):
        resp = self.client.ajax_post(f'/xblock/{self.vertical.location}', data={'publish': 'make_public'})
        self.assertEqual(resp.status_code, 200)  # noqa: PT009

    def test_publish_non_staff_forbidden(self):
        resp = self.non_staff_client.ajax_post(f'/xblock/{self.vertical.location}', data={'publish': 'make_public'})
        self.assertEqual(resp.status_code, 403)  # noqa: PT009

    # --- DELETE /xblock/{blockId} ---

    def test_delete_block_staff_allowed(self):
        resp = self.client.delete(f'/xblock/{self.html_block.location}', HTTP_ACCEPT='application/json')
        self.assertEqual(resp.status_code, 204)  # noqa: PT009

    def test_delete_block_non_staff_forbidden(self):
        resp = self.non_staff_client.delete(f'/xblock/{self.html_block.location}', HTTP_ACCEPT='application/json')
        self.assertEqual(resp.status_code, 403)  # noqa: PT009

    # --- POST /xblock/ (create/duplicate) ---

    def test_post_duplicate_staff_allowed(self):
        data = {
            'duplicate_source_locator': str(self.html_block.location),
            'parent_locator': str(self.vertical.location),
        }
        self.assertEqual(self.client.ajax_post('/xblock/', data=data).status_code, 200)  # noqa: PT009

    def test_post_duplicate_non_staff_forbidden(self):
        data = {
            'duplicate_source_locator': str(self.html_block.location),
            'parent_locator': str(self.vertical.location),
        }
        self.assertEqual(self.non_staff_client.ajax_post('/xblock/', data=data).status_code, 403)  # noqa: PT009

    def _create_other_course_with_vertical(self, number):
        """Create a second course with a vertical, and return (course, vertical)."""
        other_course = CourseFactory.create(org="OtherOrg", number=number, run="2026_T1")
        chapter = BlockFactory.create(category="chapter", parent_location=other_course.location)
        sequential = BlockFactory.create(category="sequential", parent_location=chapter.location)
        vertical = BlockFactory.create(category="vertical", parent_location=sequential.location)
        return other_course, vertical

    def test_post_duplicate_cross_course_staff_creates_block_in_destination(self):
        """Users with access to both courses can duplicate across them; the copy lives in the destination."""
        other_course, other_vertical = self._create_other_course_with_vertical("OC1")
        source_items = len(self.store.get_items(self.course.id))

        resp = self.client.ajax_post("/xblock/", data={
            "duplicate_source_locator": str(self.html_block.location),
            "parent_locator": str(other_vertical.location),
        })

        assert resp.status_code == 200
        new_key = UsageKey.from_string(json.loads(resp.content)["locator"])
        assert new_key.course_key == other_course.id
        assert new_key in self.store.get_item(other_vertical.location).children
        assert self.store.get_item(new_key).location == new_key
        assert len(self.store.get_items(self.course.id)) == source_items

    def test_post_duplicate_cross_course_without_source_view_access_forbidden(self):
        """Write access on the destination is not enough: the user must also be able to view the source."""
        other_course, other_vertical = self._create_other_course_with_vertical("OC2")
        CourseStaffRole(other_course.id).add_users(self.non_staff_user)
        children_before = list(self.store.get_item(other_vertical.location).children)

        resp = self.non_staff_client.ajax_post("/xblock/", data={
            "duplicate_source_locator": str(self.html_block.location),
            "parent_locator": str(other_vertical.location),
        })

        assert resp.status_code == 403
        assert self.store.get_item(other_vertical.location).children == children_before

    def test_post_duplicate_cross_course_without_destination_write_access_forbidden(self):
        """View access on the source is not enough: the user must also be able to edit the destination."""
        _other_course, other_vertical = self._create_other_course_with_vertical("OC3")
        CourseStaffRole(self.course.id).add_users(self.non_staff_user)
        children_before = list(self.store.get_item(other_vertical.location).children)

        resp = self.non_staff_client.ajax_post("/xblock/", data={
            "duplicate_source_locator": str(self.html_block.location),
            "parent_locator": str(other_vertical.location),
        })

        assert resp.status_code == 403
        assert self.store.get_item(other_vertical.location).children == children_before

    def test_post_add_component_staff_allowed(self):
        data = {'category': 'html', 'parent_locator': str(self.vertical.location)}
        self.assertEqual(self.client.ajax_post('/xblock/', data=data).status_code, 200)  # noqa: PT009

    def test_post_add_component_non_staff_forbidden(self):
        data = {'category': 'html', 'parent_locator': str(self.vertical.location)}
        self.assertEqual(self.non_staff_client.ajax_post('/xblock/', data=data).status_code, 403)  # noqa: PT009

    # --- PUT /xblock/{blockId} (reorder) ---

    def test_put_reorder_staff_allowed(self):
        data={'children': [str(self.html_block.location)]}
        resp = self.client.put(
            f'/xblock/{self.vertical.location}', data=data,
            content_type='application/json', HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, 200)  # noqa: PT009

    def test_put_reorder_non_staff_forbidden(self):
        data={'children': [str(self.html_block.location)]}
        resp = self.non_staff_client.put(
            f'/xblock/{self.vertical.location}', data=data,
            content_type='application/json', HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, 403)  # noqa: PT009

    # --- PATCH /xblock/ (move) ---

    def test_patch_move_component_staff_allowed(self):
        vertical2 = BlockFactory.create(category='vertical', parent_location=self.sequential.location)
        data={
            'move_source_locator': str(self.html_block.location),
            'parent_locator': str(vertical2.location),
        }
        resp = self.client.patch(
            '/xblock/', data=data, content_type='application/json', HTTP_ACCEPT='application/json',
        )
        self.assertNotEqual(resp.status_code, 403)  # noqa: PT009

    def test_patch_move_component_non_staff_forbidden(self):
        data={
            'move_source_locator': str(self.html_block.location),
            'parent_locator': str(self.vertical.location),
        }
        resp = self.non_staff_client.patch(
            '/xblock/', data=data, content_type='application/json', HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, 403)  # noqa: PT009

    # --- static_tab and course_info ---

    def test_put_update_custom_page_staff_allowed(self):
        data={'metadata': {'display_name': 'Updated Page'}}
        resp = self.client.put(
            f'/xblock/{self.static_tab.location}', data=data,
            content_type='application/json', HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, 200)  # noqa: PT009

    def test_put_update_custom_page_non_staff_forbidden(self):
        data={'metadata': {'display_name': 'Updated Page'}}
        resp = self.non_staff_client.put(
            f'/xblock/{self.static_tab.location}', data=data,
            content_type='application/json', HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, 403)  # noqa: PT009

    def test_delete_custom_page_staff_allowed(self):
        resp = self.client.delete(f'/xblock/{self.static_tab.location}', HTTP_ACCEPT='application/json')
        self.assertEqual(resp.status_code, 204)  # noqa: PT009

    def test_delete_custom_page_non_staff_forbidden(self):
        resp = self.non_staff_client.delete(f'/xblock/{self.static_tab.location}', HTTP_ACCEPT='application/json')
        self.assertEqual(resp.status_code, 403)  # noqa: PT009

    def test_post_static_tab_content_staff_allowed(self):
        resp = self.client.ajax_post(
            f'/xblock/{self.static_tab.location}', data={'data': '<p>Content</p>', 'metadata': {'display_name': 'Page'}}
        )
        self.assertEqual(resp.status_code, 200)  # noqa: PT009

    def test_post_static_tab_content_non_staff_forbidden(self):
        resp = self.non_staff_client.ajax_post(
            f'/xblock/{self.static_tab.location}', data={'data': '<p>Content</p>', 'metadata': {'display_name': 'Page'}}
        )
        self.assertEqual(resp.status_code, 403)  # noqa: PT009

    def test_get_handouts_staff_allowed(self):
        handouts = BlockFactory.create(category='course_info', parent_location=self.course.location)
        self.assertEqual(self.client.get_json(f'/xblock/{handouts.location}').status_code, 200)  # noqa: PT009

    def test_get_handouts_non_staff_forbidden(self):
        handouts = BlockFactory.create(category='course_info', parent_location=self.course.location)
        self.assertEqual(self.non_staff_client.get_json(f'/xblock/{handouts.location}').status_code, 403)  # noqa: PT009


@patch('cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers.authz_api.is_user_allowed', return_value=True)
@patch.object(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, 'is_enabled', return_value=True)
class XBlockHandlerAuthzPermissionsTest(CourseTestCase):
    """
    Tests for authz-based permission checks in xblock_handler.

    Verifies that when AUTHZ_COURSE_AUTHORING_FLAG is enabled, the handler
    uses granular authz permissions instead of legacy permission checks.
    """

    def setUp(self):
        super().setUp()
        self.chapter = BlockFactory.create(category='chapter', parent_location=self.course.location)
        self.sequential = BlockFactory.create(category='sequential', parent_location=self.chapter.location)
        self.vertical = BlockFactory.create(category='vertical', parent_location=self.sequential.location)
        self.html_block = BlockFactory.create(category='html', parent_location=self.vertical.location)
        self.static_tab = BlockFactory.create(category='static_tab', parent_location=self.course.location)
        self.course_info = BlockFactory.create(category='course_info', parent_location=self.course.location)

    # --- GET /xblock/{blockId} ---

    def test_get_regular_block_checks_view_course(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """GET on regular block should check courses.view_course permission"""
        self.client.get_json(f'/xblock/{self.html_block.location}')
        mock_is_allowed.assert_called_with(
            self.user.username,
            'courses.view_course',
            str(self.course.id)
        )

    def test_get_course_info_checks_view_course_updates(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """GET on course_info block should check courses.view_course_updates permission"""
        self.client.get_json(f'/xblock/{self.course_info.location}')
        mock_is_allowed.assert_called_with(
            self.user.username,
            'courses.view_course_updates',
            str(self.course.id)
        )

    def test_get_static_tab_checks_view_course(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """GET on static_tab should check courses.view_course"""
        self.client.get_json(f'/xblock/{self.static_tab.location}')
        mock_is_allowed.assert_called_with(
            self.user.username,
            'courses.view_course',
            str(self.course.id)
        )

    # --- POST /xblock/{blockId} metadata ---

    def test_post_regular_block_checks_edit_course_content(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """POST on regular block without publish should check courses.edit_course_content"""
        self.client.ajax_post(f'/xblock/{self.html_block.location}', data={'metadata': {'display_name': 'New'}})
        mock_is_allowed.assert_called_with(
            self.user.username,
            'courses.edit_course_content',
            str(self.course.id)
        )

    def test_post_with_publish_none_and_metadata_checks_edit(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """POST with publish=None + metadata should check courses.edit_course_content"""
        self.client.ajax_post(
            f'/xblock/{self.vertical.location}',
            data={'publish': None, 'metadata': {'visible_to_staff_only': True}}
        )
        mock_is_allowed.assert_called_with(
            self.user.username,
            'courses.edit_course_content',
            str(self.course.id)
        )

    # --- POST /xblock/{blockId} publish ---

    def test_post_with_publish_checks_publish_course_content(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """POST with publish='make_public' should check courses.publish_course_content"""
        self.client.ajax_post(f'/xblock/{self.vertical.location}', data={'publish': 'make_public'})
        mock_is_allowed.assert_called_with(
            self.user.username,
            'courses.publish_course_content',
            str(self.course.id)
        )

    def test_post_discard_changes_checks_publish(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """POST with publish='discard_changes' should check courses.publish_course_content"""
        self.client.ajax_post(f'/xblock/{self.vertical.location}', data={'publish': 'discard_changes'})
        mock_is_allowed.assert_called_with(
            self.user.username,
            'courses.publish_course_content',
            str(self.course.id)
        )

    def test_post_republish_without_changes_checks_publish(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """POST with publish='republish' and no content changes should check courses.publish_course_content"""
        self.client.ajax_post(f'/xblock/{self.vertical.location}', data={'publish': 'republish'})
        mock_is_allowed.assert_called_with(
            self.user.username,
            'courses.publish_course_content',
            str(self.course.id)
        )

    def test_post_make_public_with_content_changes_checks_edit(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """POST with publish='make_public' + metadata should check courses.edit_course_content"""
        self.client.ajax_post(
            f'/xblock/{self.vertical.location}',
            data={'publish': 'make_public', 'metadata': {'display_name': 'New'}}
        )
        mock_is_allowed.assert_called_with(
            self.user.username,
            'courses.edit_course_content',
            str(self.course.id)
        )

    def test_post_republish_with_metadata_checks_edit(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """POST with publish='republish' + metadata changes should check courses.edit_course_content"""
        self.client.ajax_post(
            f'/xblock/{self.chapter.location}',
            data={'publish': 'republish', 'metadata': {'highlights': ['Week 1']}}
        )
        mock_is_allowed.assert_called_with(
            self.user.username,
            'courses.edit_course_content',
            str(self.course.id)
        )

    def test_post_republish_with_grader_type_checks_edit(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """POST with publish='republish' + graderType should check courses.edit_course_content"""
        self.client.ajax_post(
            f'/xblock/{self.sequential.location}',
            data={'publish': 'republish', 'graderType': 'Homework', 'prereqMinScore': 100}
        )
        mock_is_allowed.assert_called_with(
            self.user.username,
            'courses.edit_course_content',
            str(self.course.id)
        )

    # --- DELETE /xblock/{blockId} ---

    def test_delete_regular_block_checks_edit_course_content(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """DELETE on regular block should check courses.edit_course_content"""
        self.client.delete(f'/xblock/{self.html_block.location}', HTTP_ACCEPT='application/json')
        mock_is_allowed.assert_called_with(
            self.user.username,
            'courses.edit_course_content',
            str(self.course.id)
        )

    def test_delete_static_tab_checks_manage_pages_and_resources(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """DELETE on static_tab should check courses.manage_pages_and_resources"""
        self.client.delete(f'/xblock/{self.static_tab.location}', HTTP_ACCEPT='application/json')
        mock_is_allowed.assert_called_with(
            self.user.username,
            'courses.manage_pages_and_resources',
            str(self.course.id)
        )

    # --- POST /xblock/ (create/duplicate) ---

    def test_create_block_checks_edit_course_content(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """POST /xblock/ to create block should check courses.edit_course_content"""
        self.client.ajax_post('/xblock/', data={'category': 'html', 'parent_locator': str(self.vertical.location)})
        mock_is_allowed.assert_called_with(
            self.user.username,
            'courses.edit_course_content',
            str(self.course.id)
        )

    def test_create_static_tab_checks_manage_pages_and_resources(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """PUT /xblock/ to create static_tab should check courses.manage_pages_and_resources"""
        self.client.put(
            '/xblock/',
            data={'category': 'static_tab', 'parent_locator': str(self.course.location)},
            content_type='application/json', HTTP_ACCEPT='application/json',
        )
        mock_is_allowed.assert_called_with(
            self.user.username,
            'courses.manage_pages_and_resources',
            str(self.course.id)
        )

    def test_duplicate_block_checks_edit_course_content(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """POST /xblock/ to duplicate should check courses.edit_course_content"""
        self.client.ajax_post(
            '/xblock/',
            data={
                'duplicate_source_locator': str(self.html_block.location),
                'parent_locator': str(self.vertical.location),
            }
        )
        mock_is_allowed.assert_any_call(
            self.user.username,
            'courses.edit_course_content',
            str(self.course.id)
        )

    def _duplicate_cross_course(self, other_vertical):
        """Send a request to duplicate ``self.html_block`` under ``other_vertical``, in another course."""
        return self.client.ajax_post(
            "/xblock/",
            data={
                "duplicate_source_locator": str(self.html_block.location),
                "parent_locator": str(other_vertical.location),
            }
        )

    def _create_other_course_with_vertical(self):
        """Create a second course with a vertical, and return (course, vertical)."""
        other_course = CourseFactory.create(org="OtherOrg", number="OC4", run="2026_T1")
        chapter = BlockFactory.create(category="chapter", parent_location=other_course.location)
        sequential = BlockFactory.create(category="sequential", parent_location=chapter.location)
        vertical = BlockFactory.create(category="vertical", parent_location=sequential.location)
        return other_course, vertical

    def test_duplicate_cross_course_checks_view_on_source_and_edit_on_destination(
        self, _mock_flag, mock_is_allowed  # noqa: PT019
    ):
        """Cross-course duplicate checks courses.view_course on the source and edit_course_content on the target."""
        other_course, other_vertical = self._create_other_course_with_vertical()

        resp = self._duplicate_cross_course(other_vertical)

        assert resp.status_code == 200
        mock_is_allowed.assert_any_call(self.user.username, "courses.edit_course_content", str(other_course.id))
        mock_is_allowed.assert_any_call(self.user.username, "courses.view_course", str(self.course.id))
        new_key = UsageKey.from_string(json.loads(resp.content)["locator"])
        assert new_key.course_key == other_course.id

    def test_duplicate_cross_course_denied_without_source_view(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """No courses.view_course on the source course: forbidden, and nothing is written anywhere."""
        other_course, other_vertical = self._create_other_course_with_vertical()
        mock_is_allowed.side_effect = lambda _user, permission, scope: not (
            permission == "courses.view_course" and scope == str(self.course.id)
        )
        source_items = len(self.store.get_items(self.course.id))
        dest_items = len(self.store.get_items(other_course.id))
        children_before = list(self.store.get_item(other_vertical.location).children)

        resp = self._duplicate_cross_course(other_vertical)

        assert resp.status_code == 403
        assert len(self.store.get_items(self.course.id)) == source_items
        assert len(self.store.get_items(other_course.id)) == dest_items
        assert self.store.get_item(other_vertical.location).children == children_before

    def test_duplicate_cross_course_denied_without_destination_edit(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """No courses.edit_course_content on the destination course: forbidden."""
        other_course, other_vertical = self._create_other_course_with_vertical()
        mock_is_allowed.side_effect = lambda _user, permission, scope: not (
            permission == "courses.edit_course_content" and scope == str(other_course.id)
        )
        source_items = len(self.store.get_items(self.course.id))

        resp = self._duplicate_cross_course(other_vertical)

        assert resp.status_code == 403
        assert len(self.store.get_items(self.course.id)) == source_items

    # --- PUT /xblock/{blockId} (reorder) ---

    def test_put_reorder_checks_edit_course_content(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """PUT on regular block (reorder children) should check courses.edit_course_content"""
        self.client.put(
            f'/xblock/{self.vertical.location}',
            data={'children': [str(self.html_block.location)]},
            content_type='application/json', HTTP_ACCEPT='application/json',
        )
        mock_is_allowed.assert_called_with(
            self.user.username,
            'courses.edit_course_content',
            str(self.course.id)
        )

    # --- PATCH /xblock/ (move) ---

    def test_move_block_checks_edit_course_content(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """PATCH /xblock/ to move should check courses.edit_course_content"""
        vertical2 = BlockFactory.create(category='vertical', parent_location=self.sequential.location)
        self.client.patch(
            '/xblock/',
            data={
                'move_source_locator': str(self.html_block.location),
                'parent_locator': str(vertical2.location),
            },
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        mock_is_allowed.assert_called_with(
            self.user.username,
            'courses.edit_course_content',
            str(self.course.id)
        )

    # --- static_tab and course_info ---

    def test_post_static_tab_checks_manage_pages_and_resources(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """POST on static_tab should check courses.manage_pages_and_resources"""
        self.client.ajax_post(f'/xblock/{self.static_tab.location}', data={'metadata': {'display_name': 'Updated'}})
        mock_is_allowed.assert_called_with(
            self.user.username,
            'courses.manage_pages_and_resources',
            str(self.course.id)
        )

    def test_put_static_tab_checks_manage_pages_and_resources(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """PUT on static_tab should check courses.manage_pages_and_resources"""
        self.client.put(
            f'/xblock/{self.static_tab.location}',
            data={'metadata': {'display_name': 'Updated'}},
            content_type='application/json', HTTP_ACCEPT='application/json',
        )
        mock_is_allowed.assert_called_with(
            self.user.username,
            'courses.manage_pages_and_resources',
            str(self.course.id)
        )

    def test_post_course_info_checks_manage_course_updates(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """POST on course_info block should check courses.manage_course_updates"""
        self.client.ajax_post(f'/xblock/{self.course_info.location}', data={'data': '<p>Updated</p>'})
        mock_is_allowed.assert_called_with(
            self.user.username,
            'courses.manage_course_updates',
            str(self.course.id)
        )

    def test_put_course_info_checks_manage_course_updates(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """PUT on course_info should check courses.manage_course_updates"""
        self.client.put(
            f'/xblock/{self.course_info.location}',
            data={'data': '<p>Updated</p>'},
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        mock_is_allowed.assert_called_with(
            self.user.username,
            'courses.manage_course_updates',
            str(self.course.id)
        )

    # --- authz flag behavior ---

    def test_authz_denied_raises_permission_denied(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """When authz denies permission, PermissionDenied should be raised"""
        mock_is_allowed.return_value = False
        response = self.client.get_json(f'/xblock/{self.html_block.location}')
        self.assertEqual(response.status_code, 403)  # noqa: PT009

    def test_authz_flag_disabled_uses_legacy_permissions(self, _mock_flag, mock_is_allowed):  # noqa: PT019
        """When authz flag is disabled, should use legacy permission checks"""
        with patch.object(core_toggles.AUTHZ_COURSE_AUTHORING_FLAG, 'is_enabled', return_value=False):
            self.client.get_json(f'/xblock/{self.html_block.location}')
            mock_is_allowed.assert_not_called()
