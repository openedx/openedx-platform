"""
Tests verifying that xblock_handler enforces the correct permissions.
"""
from unittest.mock import call, patch

from openedx_authz.authz import COURSE_EDITOR, COURSE_STAFF

from cms.djangoapps.contentstore.tests.utils import AjaxEnabledTestClient, CourseTestCase
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core import toggles as core_toggles
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.factories import BlockFactory


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
        self.non_staff_client, _ = self.create_non_staff_authed_user_client()

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

    def test_post_make_public_with_content_changes_checks_edit_and_publish(
        self, _mock_flag, mock_is_allowed  # noqa: PT019
    ):
        """POST with publish='make_public' + metadata should check both edit and publish permissions"""
        self.client.ajax_post(
            f'/xblock/{self.vertical.location}',
            data={'publish': 'make_public', 'metadata': {'display_name': 'New'}}
        )
        assert mock_is_allowed.call_args_list[-2:] == [
            call(self.user.username, 'courses.edit_course_content', str(self.course.id)),
            call(self.user.username, 'courses.publish_course_content', str(self.course.id)),
        ]

    def test_post_republish_with_metadata_on_published_unit_checks_edit_and_publish(
        self, _mock_flag, mock_is_allowed  # noqa: PT019
    ):
        """POST with publish='republish' + metadata on a published unit publishes it, so it checks both"""
        self.client.ajax_post(
            f'/xblock/{self.vertical.location}',
            data={'publish': 'republish', 'metadata': {'visible_to_staff_only': True}}
        )
        assert mock_is_allowed.call_args_list[-2:] == [
            call(self.user.username, 'courses.edit_course_content', str(self.course.id)),
            call(self.user.username, 'courses.publish_course_content', str(self.course.id)),
        ]

    def test_post_republish_with_metadata_on_unpublished_unit_checks_edit_only(
        self, _mock_flag, mock_is_allowed  # noqa: PT019
    ):
        """POST with publish='republish' + metadata on a never-published unit does not publish it"""
        draft_unit = BlockFactory.create(
            category='vertical', parent_location=self.sequential.location, publish_item=False
        )
        mock_is_allowed.reset_mock()
        self.client.ajax_post(
            f'/xblock/{draft_unit.location}',
            data={'publish': 'republish', 'metadata': {'visible_to_staff_only': True}}
        )
        assert mock_is_allowed.call_args_list == [
            call(self.user.username, 'courses.edit_course_content', str(self.course.id)),
        ]

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
        mock_is_allowed.assert_called_with(
            self.user.username,
            'courses.edit_course_content',
            str(self.course.id)
        )

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


class XBlockHandlerPublishAuthzTest(CourseAuthoringAuthzTestMixin, CourseTestCase):
    """
    A save request that also publishes requires publish permission, with real AuthZ roles.

    A Course Editor can edit content but not publish it.
    """

    def setUp(self):
        super().setUp()
        chapter = BlockFactory.create(category='chapter', parent_location=self.course.location)
        sequential = BlockFactory.create(category='sequential', parent_location=chapter.location)
        self.published_unit = BlockFactory.create(category='vertical', parent_location=sequential.location)
        # Created last: publishing a block with BlockFactory also publishes its parent's subtree.
        self.draft_unit = BlockFactory.create(
            category='vertical', parent_location=sequential.location, publish_item=False
        )

    def _client_with_role(self, role):
        """Return a client logged in as a new user holding ``role`` in the course."""
        user = UserFactory(password=self.password)
        self.add_user_to_role_in_course(user, role, self.course.id)
        client = AjaxEnabledTestClient()
        client.login(username=user.username, password=self.password)
        return client

    def test_editor_cannot_publish_with_content_changes(self):
        """An Editor sending content plus make_public is denied and the unit stays unpublished."""
        client = self._client_with_role(COURSE_EDITOR.external_key)
        response = client.ajax_post(
            f'/xblock/{self.draft_unit.location}',
            data={'publish': 'make_public', 'metadata': {'display_name': 'New'}},
        )
        assert response.status_code == 403
        assert not modulestore().has_published_version(modulestore().get_item(self.draft_unit.location))

    def test_editor_cannot_republish_published_unit_with_content_changes(self):
        """An Editor changing a published unit with republish is denied, since the change would go live."""
        client = self._client_with_role(COURSE_EDITOR.external_key)
        response = client.ajax_post(
            f'/xblock/{self.published_unit.location}',
            data={'publish': 'republish', 'metadata': {'visible_to_staff_only': True}},
        )
        assert response.status_code == 403
        assert not modulestore().get_item(self.published_unit.location).visible_to_staff_only

    def test_editor_can_republish_unpublished_unit_with_content_changes(self):
        """An Editor can save content with republish on a never-published unit, since nothing is published."""
        client = self._client_with_role(COURSE_EDITOR.external_key)
        response = client.ajax_post(
            f'/xblock/{self.draft_unit.location}',
            data={'publish': 'republish', 'metadata': {'visible_to_staff_only': True}},
        )
        assert response.status_code == 200
        unit = modulestore().get_item(self.draft_unit.location)
        assert unit.visible_to_staff_only
        assert not modulestore().has_published_version(unit)

    def test_editor_can_edit_without_publishing(self):
        """An Editor can still save content changes that do not publish."""
        client = self._client_with_role(COURSE_EDITOR.external_key)
        response = client.ajax_post(
            f'/xblock/{self.published_unit.location}', data={'metadata': {'display_name': 'New'}}
        )
        assert response.status_code == 200
        assert modulestore().get_item(self.published_unit.location).display_name == 'New'

    def test_course_staff_can_publish_with_content_changes(self):
        """Course Staff has both permissions, so content plus make_public is saved and published."""
        client = self._client_with_role(COURSE_STAFF.external_key)
        response = client.ajax_post(
            f'/xblock/{self.draft_unit.location}',
            data={'publish': 'make_public', 'metadata': {'display_name': 'New'}},
        )
        assert response.status_code == 200
        assert modulestore().has_published_version(modulestore().get_item(self.draft_unit.location))
