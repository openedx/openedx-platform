"""
Test the enable/disable discussions for all units API endpoint.
"""
import json

from django.urls import reverse
from opaque_keys.edx.keys import CourseKey

from cms.djangoapps.contentstore.tests.utils import AjaxEnabledTestClient
from common.djangoapps.student.tests.factories import UserFactory
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


class BulkEnableDisableDiscussionsTestCase(ModuleStoreTestCase):
    """
    Test the enable/disable discussions for all units API endpoint.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory(is_staff=True, is_superuser=True)
        self.user.set_password(self.user_password)
        self.user.save()

        self.course_key = CourseKey.from_string("course-v1:edx+TestX+2025")

        self.url = reverse('bulk_enable_disable_discussions', args=[str(self.course_key)])
        self.client = AjaxEnabledTestClient()
        self.client.login(username=self.user.username, password=self.user_password)

        # Create a test course
        self.course = CourseFactory.create(
            org=self.course_key.org,
            course=self.course_key.course,
            run=self.course_key.run,
            default_store=ModuleStoreEnum.Type.split,
            display_name="EnableDisableDiscussionsTestCase Course",
        )
        with self.store.bulk_operations(self.course_key):
            section = BlockFactory.create(
                parent=self.course,
                category='chapter',
                display_name="Generated Section",
            )
            sequence = BlockFactory.create(
                parent=section,
                category='sequential',
                display_name="Generated Sequence",
            )
            unit1 = BlockFactory.create(  # noqa: F841
                parent=sequence,
                category='vertical',
                display_name="Unit in Section1",
                discussion_enabled=True,
            )
            unit2 = BlockFactory.create(  # noqa: F841
                parent=sequence,
                category='vertical',
                display_name="Unit in Section2",
                discussion_enabled=True,
            )

    def test_disable_discussions_for_all_units(self):
        """
        Test that the API successfully disables discussions for all units.
        """
        self.enable_disable_discussions_for_all_units(False)

    def test_enable_discussions_for_all_units(self):
        """
        Test that the API successfully enables discussions for all units.
        """
        self.enable_disable_discussions_for_all_units(True)

    def enable_disable_discussions_for_all_units(self, is_enabled):
        """
        Test that the API successfully enables/disables discussions for all units.
        """
        data = {
            "discussion_enabled": is_enabled
        }
        response = self.client.put(self.url, data=json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 200)  # noqa: PT009
        response_data = response.json()
        print(response_data)
        self.assertEqual(response_data['units_updated_and_republished'], 0 if is_enabled else 2)  # noqa: PT009

        # Check that all verticals now have discussion_enabled set to the expected value
        with self.store.bulk_operations(self.course_key):
            verticals = self.store.get_items(self.course_key, qualifiers={'block_type': 'vertical'})
            for vertical in verticals:
                self.assertEqual(vertical.discussion_enabled, is_enabled)  # noqa: PT009

    def test_permission_denied_for_non_staff(self):
        """
        Test that non-staff users are denied access to the API.
        """
        # Create a non-staff user
        non_staff_user = UserFactory(is_staff=False, is_superuser=False)
        non_staff_user.set_password(self.user_password)
        non_staff_user.save()

        # Create a new client for the non-staff user
        non_staff_client = AjaxEnabledTestClient()
        non_staff_client.login(username=non_staff_user.username, password=self.user_password)

        response = non_staff_client.put(self.url, content_type='application/json')
        self.assertEqual(response.status_code, 403)  # noqa: PT009

    def test_badrequest_for_empty_request_body(self):
        """
        Test that the API returns a 400 for an empty request body.
        """
        response = self.client.put(self.url, data=json.dumps({}), content_type='application/json')
        self.assertEqual(response.status_code, 400)  # noqa: PT009
