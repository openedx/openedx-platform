"""
Group Configuration Tests.
"""


import json
from operator import itemgetter
from unittest.mock import patch

import ddt
from django.test import Client
from openedx_authz.constants.roles import COURSE_DATA_RESEARCHER, COURSE_STAFF
from rest_framework import status

from cms.djangoapps.contentstore.api.tests.base import BaseCourseViewTest
from cms.djangoapps.contentstore.course_group_config import (
    ENROLLMENT_SCHEME,
    GroupConfiguration,
)
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import reverse_course_url, reverse_usage_url
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.authz.tests.mixins import CourseAuthzTestMixin
from openedx.features.content_type_gating.helpers import CONTENT_GATING_PARTITION_ID
from openedx.features.content_type_gating.partitions import CONTENT_TYPE_GATING_SCHEME
from xmodule.modulestore import ModuleStoreEnum  # pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE
from xmodule.modulestore.tests.factories import (  # pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
)
from xmodule.partitions.partitions import (  # pylint: disable=wrong-import-order
    ENROLLMENT_TRACK_PARTITION_ID,
    Group,
    UserPartition,
)
from xmodule.validation import (  # pylint: disable=wrong-import-order
    StudioValidation,
    StudioValidationMessage,
)

GROUP_CONFIGURATION_JSON = {
    'name': 'Test name',
    'scheme': 'random',
    'description': 'Test description',
    'version': UserPartition.VERSION,
    'groups': [
        {
            'name': 'Group A',
            'version': 1,
        }, {
            'name': 'Group B',
            'version': 1,
        },
    ],
}


# pylint: disable=no-member
class HelperMethods:
    """
    Mixin that provides useful methods for Group Configuration tests.
    """
    def _create_content_experiment(self, cid=-1, group_id=None, cid_for_problem=None,
                                   name_suffix='', special_characters=''):
        """
        Create content experiment.

        Assign Group Configuration to the experiment if cid is provided.
        Assigns a problem to the first group in the split test if group_id and cid_for_problem is provided.
        """
        sequential = BlockFactory.create(
            category='sequential',
            parent_location=self.course.location,
            display_name=f'Test Subsection {name_suffix}'
        )
        vertical = BlockFactory.create(
            category='vertical',
            parent_location=sequential.location,
            display_name=f'Test Unit {name_suffix}'
        )
        c0_url = self.course.id.make_usage_key("vertical", f"split_test_cond0_{name_suffix}")
        c1_url = self.course.id.make_usage_key("vertical", f"split_test_cond1_{name_suffix}")
        c2_url = self.course.id.make_usage_key("vertical", f"split_test_cond2_{name_suffix}")
        split_test = BlockFactory.create(
            category='split_test',
            parent_location=vertical.location,
            user_partition_id=cid,
            display_name=f"Test Content Experiment {name_suffix}{special_characters}",
            group_id_to_child={"0": c0_url, "1": c1_url, "2": c2_url}
        )
        BlockFactory.create(
            parent_location=split_test.location,
            category="vertical",
            display_name="Condition 0 vertical",
            location=c0_url,
        )
        c1_vertical = BlockFactory.create(
            parent_location=split_test.location,
            category="vertical",
            display_name="Condition 1 vertical",
            location=c1_url,
        )
        BlockFactory.create(
            parent_location=split_test.location,
            category="vertical",
            display_name="Condition 2 vertical",
            location=c2_url,
        )

        problem = None
        if group_id and cid_for_problem:
            problem = BlockFactory.create(
                category='problem',
                parent_location=c1_vertical.location,
                display_name="Test Problem"
            )
            self.client.ajax_post(
                reverse_usage_url("xblock_handler", problem.location),
                data={'metadata': {'group_access': {cid_for_problem: [group_id]}}}
            )
            c1_vertical.children.append(problem.location)

        partitions_json = [p.to_json() for p in self.course.user_partitions]

        self.client.ajax_post(
            reverse_usage_url("xblock_handler", split_test.location),
            data={'metadata': {'user_partitions': partitions_json}}
        )

        self.save_course()
        return vertical, split_test, problem

    def _create_problem_with_content_group(self, cid, group_id, name_suffix='', special_characters='', orphan=False):
        """
        Create a problem
        Assign content group to the problem.
        """
        vertical_parent_location = self.course.location
        if not orphan:
            subsection = BlockFactory.create(
                category='sequential',
                parent_location=self.course.location,
                display_name=f"Test Subsection {name_suffix}"
            )
            vertical_parent_location = subsection.location

        vertical = BlockFactory.create(
            category='vertical',
            parent_location=vertical_parent_location,
            display_name=f"Test Unit {name_suffix}"
        )

        problem = BlockFactory.create(
            category='problem',
            parent_location=vertical.location,
            display_name=f"Test Problem {name_suffix}{special_characters}"
        )

        group_access_content = {'group_access': {cid: [group_id]}}

        self.client.ajax_post(
            reverse_usage_url("xblock_handler", problem.location),
            data={'metadata': group_access_content}
        )

        if not orphan:
            self.course.children.append(subsection.location)
        self.save_course()

        return vertical, problem

    def _add_user_partitions(self, count=1, scheme_id="random"):
        """
        Create user partitions for the course.
        """
        partitions = [
            UserPartition(
                i, 'Name ' + str(i), 'Description ' + str(i),
                [Group(0, 'Group A'), Group(1, 'Group B'), Group(2, 'Group C')],
                scheme=None, scheme_id=scheme_id
            ) for i in range(count)
        ]
        self.course.user_partitions = partitions
        self.save_course()


# pylint: disable=no-member
class GroupConfigurationsBaseTestCase:
    """
    Mixin with base test cases for the group configurations.
    """

    def _remove_ids(self, content):
        """
        Remove ids from the response. We cannot predict IDs, because they're
        generated randomly.
        We use this method to clean up response when creating new group configurations.
        Returns a tuple that contains removed group configuration ID and group IDs.
        """
        configuration_id = content.pop("id")
        group_ids = [group.pop("id") for group in content["groups"]]

        return (configuration_id, group_ids)

    def test_required_fields_are_absent(self):
        """
        Test required fields are absent.
        """
        bad_jsons = [
            # must have name of the configuration
            {
                'description': 'Test description',
                'groups': [
                    {'name': 'Group A'},
                    {'name': 'Group B'},
                ],
            },
            # must have at least one group
            {
                'name': 'Test name',
                'description': 'Test description',
                'groups': [],
            },
            # an empty json
            {},
        ]

        for bad_json in bad_jsons:
            response = self.client.post(
                self._url(),
                data=json.dumps(bad_json),
                content_type="application/json",
                HTTP_ACCEPT="application/json",
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
            self.assertEqual(response.status_code, 400)  # noqa: PT009
            self.assertNotIn("Location", response)  # noqa: PT009
            content = json.loads(response.content.decode('utf-8'))
            self.assertIn("error", content)  # noqa: PT009

    def test_invalid_json(self):
        """
        Test invalid json handling.
        """
        # No property name.
        invalid_json = "{'name': 'Test Name', []}"

        response = self.client.post(
            self._url(),
            data=invalid_json,
            content_type="application/json",
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 400)  # noqa: PT009
        self.assertNotIn("Location", response)  # noqa: PT009
        content = json.loads(response.content.decode('utf-8'))
        self.assertIn("error", content)  # noqa: PT009


@ddt.ddt
class GroupConfigurationsListHandlerTestCase(CourseTestCase, GroupConfigurationsBaseTestCase, HelperMethods):
    """
    Test cases for group_configurations_list_handler.
    """

    def _url(self):
        """
        Return url for the handler.
        """
        return reverse_course_url('group_configurations_list_handler', self.course.id)

    def test_view_index_ok(self):
        """
        Basic check that the groups configuration page redirects to the MFE.
        """
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 302)  # noqa: PT009

    def test_unsupported_http_accept_header(self):
        """
        Test if not allowed header present in request.
        """
        response = self.client.get(
            self._url(),
            HTTP_ACCEPT="text/plain",
        )
        self.assertEqual(response.status_code, 406)  # noqa: PT009

    def test_can_create_group_configuration(self):
        """
        Test that you can create a group configuration.
        """
        expected = {
            'description': 'Test description',
            'name': 'Test name',
            'scheme': 'random',
            'version': UserPartition.VERSION,
            'groups': [
                {'name': 'Group A', 'version': 1},
                {'name': 'Group B', 'version': 1},
            ],
            'parameters': {},
            'active': True
        }
        response = self.client.ajax_post(
            self._url(),
            data=GROUP_CONFIGURATION_JSON
        )
        self.assertEqual(response.status_code, 201)  # noqa: PT009
        self.assertIn("Location", response)  # noqa: PT009
        content = json.loads(response.content.decode('utf-8'))
        configuration_id, group_ids = self._remove_ids(content)  # pylint: disable=unused-variable
        self.assertEqual(content, expected)  # noqa: PT009
        # IDs are unique
        self.assertEqual(len(group_ids), len(set(group_ids)))  # noqa: PT009
        self.assertEqual(len(group_ids), 2)  # noqa: PT009
        self.reload_course()
        # Verify that user_partitions in the course contains the new group configuration.
        user_partititons = self.course.user_partitions
        self.assertEqual(len(user_partititons), 1)  # noqa: PT009
        self.assertEqual(user_partititons[0].name, 'Test name')  # noqa: PT009
        self.assertEqual(len(user_partititons[0].groups), 2)  # noqa: PT009
        self.assertEqual(user_partititons[0].groups[0].name, 'Group A')  # noqa: PT009
        self.assertEqual(user_partititons[0].groups[1].name, 'Group B')  # noqa: PT009
        self.assertEqual(user_partititons[0].parameters, {})  # noqa: PT009

    def test_lazily_creates_cohort_configuration(self):
        """
        Test that a cohort schemed user partition is NOT created by
        default for the user.
        """
        self.assertEqual(len(self.course.user_partitions), 0)  # noqa: PT009
        self.client.get(self._url())
        self.reload_course()
        self.assertEqual(len(self.course.user_partitions), 0)  # noqa: PT009

    @ddt.data('content_type_gate', 'enrollment_track')
    def test_cannot_create_restricted_group_configuration(self, scheme_id):
        """
        Test that you cannot create a restricted group configuration.
        """
        group_config = dict(GROUP_CONFIGURATION_JSON)
        group_config['scheme'] = scheme_id
        group_config.setdefault('parameters', {})['course_id'] = str(self.course.id)
        response = self.client.ajax_post(
            self._url(),
            data=group_config
        )
        self.assertEqual(response.status_code, 400)  # noqa: PT009


@ddt.ddt
class GroupConfigurationsDetailHandlerTestCase(CourseTestCase, GroupConfigurationsBaseTestCase, HelperMethods):
    """
    Test cases for group_configurations_detail_handler.
    """
    ID = 0

    def _url(self, cid=-1):
        """
        Return url for the handler.
        """
        cid = cid if cid > 0 else self.ID
        return reverse_course_url(
            'group_configurations_detail_handler',
            self.course.id,
            kwargs={'group_configuration_id': cid},
        )

    def test_can_create_new_content_group_if_it_does_not_exist(self):
        """
        PUT new content group.
        """
        expected = {
            'id': 666,
            'name': 'Test name',
            'scheme': 'cohort',
            'description': 'Test description',
            'version': UserPartition.VERSION,
            'groups': [
                {'id': 0, 'name': 'Group A', 'version': 1, 'usage': []},
                {'id': 1, 'name': 'Group B', 'version': 1, 'usage': []},
            ],
            'parameters': {},
            'active': True,
        }
        response = self.client.put(
            self._url(cid=666),
            data=json.dumps(expected),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        content = json.loads(response.content.decode('utf-8'))

        self.assertEqual(content, expected)  # noqa: PT009
        self.reload_course()
        # Verify that user_partitions in the course contains the new group configuration.
        user_partitions = self.course.user_partitions
        self.assertEqual(len(user_partitions), 1)  # noqa: PT009
        self.assertEqual(user_partitions[0].name, 'Test name')  # noqa: PT009
        self.assertEqual(len(user_partitions[0].groups), 2)  # noqa: PT009
        self.assertEqual(user_partitions[0].groups[0].name, 'Group A')  # noqa: PT009
        self.assertEqual(user_partitions[0].groups[1].name, 'Group B')  # noqa: PT009
        self.assertEqual(user_partitions[0].parameters, {})  # noqa: PT009

    def test_can_edit_content_group(self):
        """
        Edit content group and check its id and modified fields.
        """
        self._add_user_partitions(scheme_id='cohort')
        self.save_course()

        expected = {
            'id': self.ID,
            'name': 'New Test name',
            'scheme': 'cohort',
            'description': 'New Test description',
            'version': UserPartition.VERSION,
            'groups': [
                {'id': 0, 'name': 'New Group Name', 'version': 1, 'usage': []},
                {'id': 2, 'name': 'Group C', 'version': 1, 'usage': []},
            ],
            'parameters': {},
            'active': True,
        }

        response = self.client.put(
            self._url(),
            data=json.dumps(expected),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        content = json.loads(response.content.decode('utf-8'))
        self.assertEqual(content, expected)  # noqa: PT009
        self.reload_course()

        # Verify that user_partitions is properly updated in the course.
        user_partititons = self.course.user_partitions

        self.assertEqual(len(user_partititons), 1)  # noqa: PT009
        self.assertEqual(user_partititons[0].name, 'New Test name')  # noqa: PT009
        self.assertEqual(len(user_partititons[0].groups), 2)  # noqa: PT009
        self.assertEqual(user_partititons[0].groups[0].name, 'New Group Name')  # noqa: PT009
        self.assertEqual(user_partititons[0].groups[1].name, 'Group C')  # noqa: PT009
        self.assertEqual(user_partititons[0].parameters, {})  # noqa: PT009

    def test_can_delete_content_group(self):
        """
        Delete content group and check user partitions.
        """
        self._add_user_partitions(count=1, scheme_id='cohort')
        self.save_course()

        details_url_with_group_id = self._url(cid=0) + '/1'
        response = self.client.delete(
            details_url_with_group_id,
            content_type="application/json",
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 204)  # noqa: PT009
        self.reload_course()
        # Verify that group and partition is properly updated in the course.
        user_partititons = self.course.user_partitions
        self.assertEqual(len(user_partititons), 1)  # noqa: PT009
        self.assertEqual(user_partititons[0].name, 'Name 0')  # noqa: PT009
        self.assertEqual(len(user_partititons[0].groups), 2)  # noqa: PT009
        self.assertEqual(user_partititons[0].groups[1].name, 'Group C')  # noqa: PT009

    def test_cannot_delete_used_content_group(self):
        """
        Cannot delete content group if it is in use.
        """
        self._add_user_partitions(count=1, scheme_id='cohort')
        self._create_problem_with_content_group(cid=0, group_id=1)

        details_url_with_group_id = self._url(cid=0) + '/1'
        response = self.client.delete(
            details_url_with_group_id,
            content_type="application/json",
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 400)  # noqa: PT009
        content = json.loads(response.content.decode('utf-8'))
        self.assertTrue(content['error'])  # noqa: PT009
        self.reload_course()
        # Verify that user_partitions and groups are still the same.
        user_partititons = self.course.user_partitions
        self.assertEqual(len(user_partititons), 1)  # noqa: PT009
        self.assertEqual(len(user_partititons[0].groups), 3)  # noqa: PT009
        self.assertEqual(user_partititons[0].groups[1].name, 'Group B')  # noqa: PT009

    def test_cannot_delete_non_existent_content_group(self):
        """
        Cannot delete content group if it is doesn't exist.
        """
        self._add_user_partitions(count=1, scheme_id='cohort')
        details_url_with_group_id = self._url(cid=0) + '/90'
        response = self.client.delete(
            details_url_with_group_id,
            content_type="application/json",
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 404)  # noqa: PT009
        # Verify that user_partitions is still the same.
        user_partititons = self.course.user_partitions
        self.assertEqual(len(user_partititons), 1)  # noqa: PT009
        self.assertEqual(len(user_partititons[0].groups), 3)  # noqa: PT009

    def test_can_create_new_group_configuration_if_it_does_not_exist(self):
        """
        PUT new group configuration when no configurations exist in the course.
        """
        expected = {
            'id': 999,
            'name': 'Test name',
            'scheme': 'random',
            'description': 'Test description',
            'version': UserPartition.VERSION,
            'groups': [
                {'id': 0, 'name': 'Group A', 'version': 1},
                {'id': 1, 'name': 'Group B', 'version': 1},
            ],
            'usage': [],
            'parameters': {},
            'active': True,
        }

        response = self.client.put(
            self._url(cid=999),
            data=json.dumps(expected),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        content = json.loads(response.content.decode('utf-8'))
        self.assertEqual(content, expected)  # noqa: PT009
        self.reload_course()
        # Verify that user_partitions in the course contains the new group configuration.
        user_partitions = self.course.user_partitions
        self.assertEqual(len(user_partitions), 1)  # noqa: PT009
        self.assertEqual(user_partitions[0].name, 'Test name')  # noqa: PT009
        self.assertEqual(len(user_partitions[0].groups), 2)  # noqa: PT009
        self.assertEqual(user_partitions[0].groups[0].name, 'Group A')  # noqa: PT009
        self.assertEqual(user_partitions[0].groups[1].name, 'Group B')  # noqa: PT009
        self.assertEqual(user_partitions[0].parameters, {})  # noqa: PT009

    def test_can_edit_group_configuration(self):
        """
        Edit group configuration and check its id and modified fields.
        """
        self._add_user_partitions()
        self.save_course()

        expected = {
            'id': self.ID,
            'name': 'New Test name',
            'scheme': 'random',
            'description': 'New Test description',
            'version': UserPartition.VERSION,
            'groups': [
                {'id': 0, 'name': 'New Group Name', 'version': 1},
                {'id': 2, 'name': 'Group C', 'version': 1},
            ],
            'usage': [],
            'parameters': {},
            'active': True,
        }

        response = self.client.put(
            self._url(),
            data=json.dumps(expected),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        content = json.loads(response.content.decode('utf-8'))
        self.assertEqual(content, expected)  # noqa: PT009
        self.reload_course()

        # Verify that user_partitions is properly updated in the course.
        user_partititons = self.course.user_partitions

        self.assertEqual(len(user_partititons), 1)  # noqa: PT009
        self.assertEqual(user_partititons[0].name, 'New Test name')  # noqa: PT009
        self.assertEqual(len(user_partititons[0].groups), 2)  # noqa: PT009
        self.assertEqual(user_partititons[0].groups[0].name, 'New Group Name')  # noqa: PT009
        self.assertEqual(user_partititons[0].groups[1].name, 'Group C')  # noqa: PT009
        self.assertEqual(user_partititons[0].parameters, {})  # noqa: PT009

    def test_can_delete_group_configuration(self):
        """
        Delete group configuration and check user partitions.
        """
        self._add_user_partitions(count=2)
        self.save_course()

        response = self.client.delete(
            self._url(cid=0),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 204)  # noqa: PT009
        self.reload_course()
        # Verify that user_partitions is properly updated in the course.
        user_partititons = self.course.user_partitions
        self.assertEqual(len(user_partititons), 1)  # noqa: PT009
        self.assertEqual(user_partititons[0].name, 'Name 1')  # noqa: PT009

    def test_cannot_delete_used_group_configuration(self):
        """
        Cannot delete group configuration if it is in use.
        """
        self._add_user_partitions(count=2)
        self._create_content_experiment(cid=0)

        response = self.client.delete(
            self._url(cid=0),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 400)  # noqa: PT009
        content = json.loads(response.content.decode('utf-8'))
        self.assertTrue(content['error'])  # noqa: PT009
        self.reload_course()
        # Verify that user_partitions is still the same.
        user_partititons = self.course.user_partitions
        self.assertEqual(len(user_partititons), 2)  # noqa: PT009
        self.assertEqual(user_partititons[0].name, 'Name 0')  # noqa: PT009

    def test_cannot_delete_non_existent_group_configuration(self):
        """
        Cannot delete group configuration if it is doesn't exist.
        """
        self._add_user_partitions(count=2)
        response = self.client.delete(
            self._url(cid=999),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 404)  # noqa: PT009
        # Verify that user_partitions is still the same.
        user_partititons = self.course.user_partitions
        self.assertEqual(len(user_partititons), 2)  # noqa: PT009
        self.assertEqual(user_partititons[0].name, 'Name 0')  # noqa: PT009

    @ddt.data(CONTENT_TYPE_GATING_SCHEME, ENROLLMENT_SCHEME)
    def test_cannot_create_restricted_group_configuration(self, scheme_id):
        """
        Test that you cannot create a restricted group configuration.
        """
        group_config = dict(GROUP_CONFIGURATION_JSON)
        group_config['scheme'] = scheme_id
        group_config.setdefault('parameters', {})['course_id'] = str(self.course.id)
        response = self.client.ajax_post(
            self._url(),
            data=group_config
        )
        self.assertEqual(response.status_code, 400)  # noqa: PT009

    @ddt.data(
        (CONTENT_TYPE_GATING_SCHEME, CONTENT_GATING_PARTITION_ID),
        (ENROLLMENT_SCHEME, ENROLLMENT_TRACK_PARTITION_ID),
    )
    @ddt.unpack
    def test_cannot_edit_restricted_group_configuration(self, scheme_id, partition_id):
        """
        Test that you cannot edit a restricted group configuration.
        """
        group_config = dict(GROUP_CONFIGURATION_JSON)
        group_config['scheme'] = scheme_id
        group_config.setdefault('parameters', {})['course_id'] = str(self.course.id)
        response = self.client.put(
            self._url(cid=partition_id),
            data=json.dumps(group_config),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 400)  # noqa: PT009


@ddt.ddt
class GroupConfigurationsUsageInfoTestCase(CourseTestCase, HelperMethods):
    """
    Tests for usage information of configurations and content groups.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def _get_user_partition(self, scheme):
        """
        Returns the first user partition with the specified scheme.
        """
        for group in GroupConfiguration.get_all_user_partition_details(self.store, self.course):
            if group['scheme'] == scheme:
                return group
        return None

    def _get_expected_content_group(self, usage_for_group):
        """
        Returns the expected configuration with particular usage.
        """
        return {
            'id': 0,
            'name': 'Name 0',
            'scheme': 'cohort',
            'description': 'Description 0',
            'version': UserPartition.VERSION,
            'groups': [
                {'id': 0, 'name': 'Group A', 'version': 1, 'usage': []},
                {'id': 1, 'name': 'Group B', 'version': 1, 'usage': usage_for_group},
                {'id': 2, 'name': 'Group C', 'version': 1, 'usage': []},
            ],
            'parameters': {},
            'active': True,
        }

    def test_content_group_not_used(self):
        """
        Test that right data structure will be created if content group is not used.
        """
        self._add_user_partitions(scheme_id='cohort')
        actual = self._get_user_partition('cohort')
        expected = self._get_expected_content_group(usage_for_group=[])
        self.assertEqual(actual, expected)  # noqa: PT009

    def test_can_get_correct_usage_info_when_special_characters_are_in_content(self):
        """
        Test if content group json updated successfully with usage information.
        """
        self._add_user_partitions(count=1, scheme_id='cohort')
        vertical, __ = self._create_problem_with_content_group(
            cid=0, group_id=1, name_suffix='0', special_characters="JOSÉ ANDRÉS"
        )

        actual = self._get_user_partition('cohort')
        expected = self._get_expected_content_group(
            usage_for_group=[
                {
                    'url': f"/container/{vertical.location}",
                    'label': "Test Unit 0 / Test Problem 0JOSÉ ANDRÉS"
                }
            ]
        )

        self.assertEqual(actual, expected)  # noqa: PT009

    def test_can_get_correct_usage_info_for_content_groups(self):
        """
        Test if content group json updated successfully with usage information.
        """
        self._add_user_partitions(count=1, scheme_id='cohort')
        vertical, __ = self._create_problem_with_content_group(cid=0, group_id=1, name_suffix='0')

        actual = self._get_user_partition('cohort')

        expected = self._get_expected_content_group(usage_for_group=[
            {
                'url': f'/container/{vertical.location}',
                'label': 'Test Unit 0 / Test Problem 0'
            }
        ])

        self.assertEqual(actual, expected)  # noqa: PT009

    def test_can_get_correct_usage_info_with_orphan(self):
        """
        Test if content group json updated successfully with usage information
        even if there is an orphan in content group.
        """
        self.course = CourseFactory.create()
        self._add_user_partitions(count=1, scheme_id='cohort')
        vertical, __ = self._create_problem_with_content_group(cid=0, group_id=1, name_suffix='0', orphan=True)

        # Assert that there is an orphan in the course, and that it's the vertical
        self.assertEqual(len(self.store.get_orphans(self.course.id)), 1)  # noqa: PT009
        self.assertIn(vertical.location, self.store.get_orphans(self.course.id))  # noqa: PT009

        # Get the expected content group information.
        expected = self._get_expected_content_group(usage_for_group=[])

        # Get the actual content group information
        actual = self._get_user_partition('cohort')

        # Assert that actual content group information is same as expected one.
        self.assertEqual(actual, expected)  # noqa: PT009

    def test_can_use_one_content_group_in_multiple_problems(self):
        """
        Test if multiple problems are present in usage info when they use same
        content group.
        """
        self._add_user_partitions(scheme_id='cohort')
        vertical1, __ = self._create_problem_with_content_group(cid=0, group_id=1, name_suffix='1')
        vertical, __ = self._create_problem_with_content_group(cid=0, group_id=1, name_suffix='0')

        actual = self._get_user_partition('cohort')

        expected = self._get_expected_content_group(usage_for_group=[
            {
                'url': f'/container/{vertical1.location}',
                'label': 'Test Unit 1 / Test Problem 1'
            },
            {
                'url': f'/container/{vertical.location}',
                'label': 'Test Unit 0 / Test Problem 0'
            }
        ])

        self.assertEqual(actual, expected)  # noqa: PT009

    def test_group_configuration_not_used(self):
        """
        Test that right data structure will be created if group configuration is not used.
        """
        self._add_user_partitions()
        actual = GroupConfiguration.get_split_test_partitions_with_usage(self.store, self.course)
        expected = [{
            'id': 0,
            'name': 'Name 0',
            'scheme': 'random',
            'description': 'Description 0',
            'version': UserPartition.VERSION,
            'groups': [
                {'id': 0, 'name': 'Group A', 'version': 1},
                {'id': 1, 'name': 'Group B', 'version': 1},
                {'id': 2, 'name': 'Group C', 'version': 1},
            ],
            'usage': [],
            'parameters': {},
            'active': True,
        }]
        self.assertEqual(actual, expected)  # noqa: PT009

    def test_can_get_correct_usage_info_for_split_test(self):
        """
        When a split test is created and content group access is set for a problem within a group,
        the usage info should return a url to the split test, not to the group.
        """
        # Create user partition for groups in the split test,
        # and another partition to set group access for the problem within the split test.
        self._add_user_partitions(count=1)
        self.course.user_partitions += [
            UserPartition(
                id=1,
                name='Cohort User Partition',
                scheme=UserPartition.get_scheme('cohort'),
                description='Cohort User Partition',
                groups=[
                    Group(id=3, name="Problem Group")
                ],
            ),
        ]
        self.store.update_item(self.course, ModuleStoreEnum.UserID.test)
        self.reload_course()

        __, split_test, problem = self._create_content_experiment(cid=0, name_suffix='0', group_id=3, cid_for_problem=1)  # pylint: disable=unused-variable

        expected = {
            'id': 1,
            'name': 'Cohort User Partition',
            'scheme': 'cohort',
            'description': 'Cohort User Partition',
            'version': UserPartition.VERSION,
            'groups': [
                {'id': 3, 'name': 'Problem Group', 'version': 1, 'usage': [
                    {
                        'url': f'/container/{split_test.location}',
                        'label': 'Condition 1 vertical / Test Problem'
                    }
                ]},
            ],
            'parameters': {},
            'active': True,
        }
        actual = self._get_user_partition('cohort')

        self.assertEqual(actual, expected)  # noqa: PT009

    def test_can_get_correct_usage_info_for_unit(self):
        """
        When group access is set on the unit level, the usage info should return a url to the unit, not
        the sequential parent of the unit.
        """
        self.course.user_partitions = [
            UserPartition(
                id=0,
                name='User Partition',
                scheme=UserPartition.get_scheme('cohort'),
                description='User Partition',
                groups=[
                    Group(id=0, name="Group")
                ],
            ),
        ]
        vertical, __ = self._create_problem_with_content_group(
            cid=0, group_id=0, name_suffix='0'
        )

        self.client.ajax_post(
            reverse_usage_url("xblock_handler", vertical.location),
            data={'metadata': {'group_access': {0: [0]}}}
        )

        actual = self._get_user_partition('cohort')
        # order of usage list is arbitrary, sort for reliable comparison
        actual['groups'][0]['usage'].sort(key=itemgetter('label'))
        expected = {
            'id': 0,
            'name': 'User Partition',
            'scheme': 'cohort',
            'description': 'User Partition',
            'version': UserPartition.VERSION,
            'groups': [
                {'id': 0, 'name': 'Group', 'version': 1, 'usage': [
                    {
                        'url': f"/container/{vertical.location}",
                        'label': "Test Subsection 0 / Test Unit 0"
                    },
                    {
                        'url': f"/container/{vertical.location}",
                        'label': "Test Unit 0 / Test Problem 0"
                    }
                ]},
            ],
            'parameters': {},
            'active': True,
        }

        self.maxDiff = None

        assert actual == expected

    def test_can_get_correct_usage_info(self):
        """
        Test if group configurations json updated successfully with usage information.
        """
        self._add_user_partitions(count=2)
        __, split_test, __ = self._create_content_experiment(cid=0, name_suffix='0')
        self._create_content_experiment(name_suffix='1')

        actual = GroupConfiguration.get_split_test_partitions_with_usage(self.store, self.course)

        expected = [{
            'id': 0,
            'name': 'Name 0',
            'scheme': 'random',
            'description': 'Description 0',
            'version': UserPartition.VERSION,
            'groups': [
                {'id': 0, 'name': 'Group A', 'version': 1},
                {'id': 1, 'name': 'Group B', 'version': 1},
                {'id': 2, 'name': 'Group C', 'version': 1},
            ],
            'usage': [{
                'url': f'/container/{split_test.location}',
                'label': 'Test Unit 0 / Test Content Experiment 0',
                'validation': None,
            }],
            'parameters': {},
            'active': True,
        }, {
            'id': 1,
            'name': 'Name 1',
            'scheme': 'random',
            'description': 'Description 1',
            'version': UserPartition.VERSION,
            'groups': [
                {'id': 0, 'name': 'Group A', 'version': 1},
                {'id': 1, 'name': 'Group B', 'version': 1},
                {'id': 2, 'name': 'Group C', 'version': 1},
            ],
            'usage': [],
            'parameters': {},
            'active': True,
        }]

        self.assertEqual(actual, expected)  # noqa: PT009

    def test_can_get_usage_info_when_special_characters_are_used(self):
        """
        Test if group configurations json updated successfully when special
         characters are being used in content experiment
        """
        self._add_user_partitions(count=1)
        __, split_test, __ = self._create_content_experiment(cid=0, name_suffix='0', special_characters="JOSÉ ANDRÉS")

        actual = GroupConfiguration.get_split_test_partitions_with_usage(self.store, self.course, )

        expected = [{
            'id': 0,
            'name': 'Name 0',
            'scheme': 'random',
            'description': 'Description 0',
            'version': UserPartition.VERSION,
            'groups': [
                {'id': 0, 'name': 'Group A', 'version': 1},
                {'id': 1, 'name': 'Group B', 'version': 1},
                {'id': 2, 'name': 'Group C', 'version': 1},
            ],
            'usage': [{
                'url': reverse_usage_url("container_handler", split_test.location),
                'label': "Test Unit 0 / Test Content Experiment 0JOSÉ ANDRÉS",
                'validation': None,
            }],
            'parameters': {},
            'active': True,
        }]

        self.assertEqual(actual, expected)  # noqa: PT009

    def test_can_use_one_configuration_in_multiple_experiments(self):
        """
        Test if multiple experiments are present in usage info when they use same
        group configuration.
        """
        self._add_user_partitions()
        __, split_test, __ = self._create_content_experiment(cid=0, name_suffix='0')
        __, split_test1, __ = self._create_content_experiment(cid=0, name_suffix='1')

        actual = GroupConfiguration.get_split_test_partitions_with_usage(self.store, self.course)

        expected = [{
            'id': 0,
            'name': 'Name 0',
            'scheme': 'random',
            'description': 'Description 0',
            'version': UserPartition.VERSION,
            'groups': [
                {'id': 0, 'name': 'Group A', 'version': 1},
                {'id': 1, 'name': 'Group B', 'version': 1},
                {'id': 2, 'name': 'Group C', 'version': 1},
            ],
            'usage': [{
                'url': f'/container/{split_test.location}',
                'label': 'Test Unit 0 / Test Content Experiment 0',
                'validation': None,
            }, {
                'url': f'/container/{split_test1.location}',
                'label': 'Test Unit 1 / Test Content Experiment 1',
                'validation': None,
            }],
            'parameters': {},
            'active': True,
        }]
        self.assertEqual(actual, expected)  # noqa: PT009

    def test_can_handle_without_parent(self):
        """
        Test if it possible to handle case when split_test has no parent.
        """
        self._add_user_partitions()
        # Create split test without parent.
        orphan = self.store.create_item(
            ModuleStoreEnum.UserID.test,
            self.course.id, 'split_test',
        )
        orphan.user_partition_id = 0
        orphan.display_name = 'Test Content Experiment'
        self.store.update_item(orphan, ModuleStoreEnum.UserID.test)

        self.save_course()
        actual = GroupConfiguration.get_content_experiment_usage_info(self.store, self.course)
        self.assertEqual(actual, {0: []})  # noqa: PT009

    def test_can_handle_multiple_partitions(self):
        # Create the user partitions
        self.course.user_partitions = [
            UserPartition(
                id=0,
                name='Cohort user partition',
                scheme=UserPartition.get_scheme('cohort'),
                description='Cohorted user partition',
                groups=[
                    Group(id=0, name="Group A"),
                    Group(id=1, name="Group B"),
                ],
            ),
            UserPartition(
                id=1,
                name='Random user partition',
                scheme=UserPartition.get_scheme('random'),
                description='Random user partition',
                groups=[
                    Group(id=0, name="Group A"),
                    Group(id=1, name="Group B"),
                ],
            ),
        ]
        self.store.update_item(self.course, ModuleStoreEnum.UserID.test)

        # Assign group access rules for multiple partitions, one of which is a cohorted partition
        __, problem = self._create_problem_with_content_group(0, 1)
        problem.group_access = {
            0: [0],
            1: [1],
        }
        self.store.update_item(problem, ModuleStoreEnum.UserID.test)

        # This used to cause an exception since the code assumed that
        # only one partition would be available.
        actual = GroupConfiguration.get_partitions_usage_info(self.store, self.course)
        self.assertEqual(list(actual.keys()), [0])  # noqa: PT009

        actual = GroupConfiguration.get_content_groups_items_usage_info(self.store, self.course)
        self.assertEqual(list(actual.keys()), [0])  # noqa: PT009

    def test_can_handle_duplicate_group_ids(self):
        # Create the user partitions
        self.course.user_partitions = [
            UserPartition(
                id=0,
                name='Cohort user partition 1',
                scheme=UserPartition.get_scheme('cohort'),
                description='Cohorted user partition',
                groups=[
                    Group(id=2, name="Group 1A"),
                    Group(id=3, name="Group 1B"),
                ],
            ),
            UserPartition(
                id=1,
                name='Cohort user partition 2',
                scheme=UserPartition.get_scheme('cohort'),
                description='Random user partition',
                groups=[
                    Group(id=2, name="Group 2A"),
                    Group(id=3, name="Group 2B"),
                ],
            ),
        ]
        self.store.update_item(self.course, ModuleStoreEnum.UserID.test)

        # Assign group access rules for multiple partitions, one of which is a cohorted partition
        self._create_problem_with_content_group(0, 2, name_suffix='0')
        self._create_problem_with_content_group(1, 3, name_suffix='1')

        # This used to cause an exception since the code assumed that
        # only one partition would be available.
        actual = GroupConfiguration.get_partitions_usage_info(self.store, self.course)
        self.assertEqual(list(actual.keys()), [0, 1])  # noqa: PT009
        self.assertEqual(list(actual[0].keys()), [2])  # noqa: PT009
        self.assertEqual(list(actual[1].keys()), [3])  # noqa: PT009

        actual = GroupConfiguration.get_content_groups_items_usage_info(self.store, self.course)
        self.assertEqual(list(actual.keys()), [0, 1])  # noqa: PT009
        self.assertEqual(list(actual[0].keys()), [2])  # noqa: PT009
        self.assertEqual(list(actual[1].keys()), [3])  # noqa: PT009


class GroupConfigurationsValidationTestCase(CourseTestCase, HelperMethods):
    """
    Tests for validation in Group Configurations.
    """

    @patch('xmodule.split_test_block.SplitTestBlock.validate_split_test')
    def verify_validation_add_usage_info(self, expected_result, mocked_message, mocked_validation_messages):
        """
        Helper method for testing validation information present after add_usage_info.
        """
        self._add_user_partitions()
        split_test = self._create_content_experiment(cid=0, name_suffix='0')[1]

        validation = StudioValidation(split_test.location)
        validation.add(mocked_message)
        mocked_validation_messages.return_value = validation

        group_configuration = GroupConfiguration.get_split_test_partitions_with_usage(self.store, self.course)[0]
        self.assertEqual(expected_result.to_json(), group_configuration['usage'][0]['validation'])  # noqa: PT009

    def test_error_message_present(self):
        """
        Tests if validation message is present (error case).
        """
        mocked_message = StudioValidationMessage(StudioValidationMessage.ERROR, "Validation message")
        expected_result = StudioValidationMessage(
            StudioValidationMessage.ERROR, "This content experiment has issues that affect content visibility."
        )
        self.verify_validation_add_usage_info(expected_result, mocked_message)  # pylint: disable=no-value-for-parameter

    def test_warning_message_present(self):
        """
        Tests if validation message is present (warning case).
        """
        mocked_message = StudioValidationMessage(StudioValidationMessage.WARNING, "Validation message")
        expected_result = StudioValidationMessage(
            StudioValidationMessage.WARNING, "This content experiment has issues that affect content visibility."
        )
        self.verify_validation_add_usage_info(expected_result, mocked_message)  # pylint: disable=no-value-for-parameter

    @patch('xmodule.split_test_block.SplitTestBlock.validate_split_test')
    def verify_validation_update_usage_info(self, expected_result, mocked_message, mocked_validation_messages):
        """
        Helper method for testing validation information present after update_usage_info.
        """
        self._add_user_partitions()
        split_test = self._create_content_experiment(cid=0, name_suffix='0')[1]

        validation = StudioValidation(split_test.location)
        if mocked_message is not None:
            validation.add(mocked_message)
        mocked_validation_messages.return_value = validation

        group_configuration = GroupConfiguration.update_usage_info(
            self.store, self.course, self.course.user_partitions[0]
        )
        self.assertEqual(  # noqa: PT009
            expected_result.to_json() if expected_result is not None else None,
            group_configuration['usage'][0]['validation']
        )

    def test_update_usage_info(self):
        """
        Tests if validation message is present when updating usage info.
        """
        mocked_message = StudioValidationMessage(StudioValidationMessage.WARNING, "Validation message")
        expected_result = StudioValidationMessage(
            StudioValidationMessage.WARNING, "This content experiment has issues that affect content visibility."
        )
        # pylint: disable=no-value-for-parameter
        self.verify_validation_update_usage_info(expected_result, mocked_message)

    def test_update_usage_info_no_message(self):
        """
        Tests if validation message is not present when updating usage info.
        """
        self.verify_validation_update_usage_info(None, None)  # pylint: disable=no-value-for-parameter

class PostGroupConfigurationsListHandlerAuthzTest(CourseAuthzTestMixin, BaseCourseViewTest):
    """
    Tests endpoint used to create new Course Group Configurations
    (group_configurations_list_handler) authorization using openedx-authz.
    The endpoint uses COURSES_MANAGE_GROUP_CONFIGURATIONS permission.
    """

    view_name = "group_configurations_list_handler"
    authz_roles_to_assign = [COURSE_STAFF.external_key]
    course_key_arg_name = 'course_key_string'

    def setUp(self):
        super().setUp()
        # Function-based views require session auth, not DRF force_authenticate.
        # Re-initialize clients using Django's test Client with login().
        self.authorized_client = Client()
        self.authorized_client.login(
            username=self.authorized_user.username, password=self.password
        )
        self.unauthorized_client = Client()
        self.unauthorized_client.login(
            username=self.unauthorized_user.username, password=self.password
        )

    def test_authorized_user_can_access(self):
        """User with COURSE_STAFF role can access."""
        resp = self.authorized_client.post(
            self.get_url(self.course_key),
            data=json.dumps(GROUP_CONFIGURATION_JSON),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)  # noqa: PT009

    def test_unauthorized_user_cannot_access(self):
        """User without role cannot access."""
        resp = self.unauthorized_client.post(
            self.get_url(self.course_key),
            data=json.dumps(GROUP_CONFIGURATION_JSON),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

    def test_role_scoped_to_course(self):
        """Authorization should only apply to the assigned course."""
        other_course = self.store.create_course("OtherOrg", "OtherCourse", "Run", self.staff.id)

        resp = self.authorized_client.post(
            self.get_url(other_course.id),
            data=json.dumps(GROUP_CONFIGURATION_JSON),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

    def test_staff_user_allowed_via_legacy(self):
        """
        Staff users should still pass through legacy fallback.
        """
        self.client.login(username=self.staff.username, password=self.password)

        resp = self.client.post(
            self.get_url(self.course_key),
            data=json.dumps(GROUP_CONFIGURATION_JSON),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)  # noqa: PT009

    def test_superuser_allowed(self):
        """Superusers should always be allowed."""
        superuser = UserFactory(is_superuser=True, password=self.password)

        client = Client()
        client.login(
            username=superuser.username, password=self.password
        )

        resp = client.post(
            self.get_url(self.course_key),
            data=json.dumps(GROUP_CONFIGURATION_JSON),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)  # noqa: PT009

    def test_non_staff_user_cannot_access(self):
        """
        User without permissions should be denied.
        This case validates that a non-staff user cannot access even
        if they have course author access to the course.
        """
        non_staff_user = UserFactory(password=self.password)
        non_staff_client = Client()
        self.add_user_to_role(non_staff_user, COURSE_DATA_RESEARCHER.external_key)
        non_staff_client.login(
            username=non_staff_user.username, password=self.password
        )

        resp = non_staff_client.post(
            self.get_url(self.course_key),
            data=json.dumps(GROUP_CONFIGURATION_JSON),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009


class PutGroupConfigurationsDetailHandlerAuthzTest(CourseAuthzTestMixin, BaseCourseViewTest):
    """
    Tests endpoint used to update Course Group Configurations
    (group_configurations_detail_handler) authorization using openedx-authz.
    The endpoint uses COURSES_MANAGE_GROUP_CONFIGURATIONS permission.
    """

    view_name = "group_configurations_detail_handler"
    authz_roles_to_assign = [COURSE_STAFF.external_key]
    course_key_arg_name = 'course_key_string'
    extra_request_args = {'group_configuration_id': 999}

    def setUp(self):
        super().setUp()
        self.authorized_client = Client()
        self.authorized_client.login(
            username=self.authorized_user.username, password=self.password
        )
        self.unauthorized_client = Client()
        self.unauthorized_client.login(
            username=self.unauthorized_user.username, password=self.password
        )

    def test_authorized_user_can_access(self):
        """User with COURSE_STAFF role can access."""
        resp = self.authorized_client.put(
            self.get_url(self.course_key),
            data=json.dumps(GROUP_CONFIGURATION_JSON),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)  # noqa: PT009

    def test_unauthorized_user_cannot_access(self):
        """User without role cannot access."""
        resp = self.unauthorized_client.put(
            self.get_url(self.course_key),
            data=json.dumps(GROUP_CONFIGURATION_JSON),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

    def test_role_scoped_to_course(self):
        """Authorization should only apply to the assigned course."""
        other_course = self.store.create_course("OtherOrg", "OtherCourse", "Run2", self.staff.id)

        resp = self.authorized_client.put(
            self.get_url(other_course.id),
            data=json.dumps(GROUP_CONFIGURATION_JSON),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

    def test_staff_user_allowed_via_legacy(self):
        """Staff users should still pass through legacy fallback."""
        self.client.login(username=self.staff.username, password=self.password)

        resp = self.client.put(
            self.get_url(self.course_key),
            data=json.dumps(GROUP_CONFIGURATION_JSON),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)  # noqa: PT009

    def test_superuser_allowed(self):
        """Superusers should always be allowed."""
        superuser = UserFactory(is_superuser=True, password=self.password)

        client = Client()
        client.login(username=superuser.username, password=self.password)

        resp = client.put(
            self.get_url(self.course_key),
            data=json.dumps(GROUP_CONFIGURATION_JSON),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)  # noqa: PT009

    def test_non_staff_user_cannot_access(self):
        """User without permissions should be denied."""
        non_staff_user = UserFactory(password=self.password)
        non_staff_client = Client()
        self.add_user_to_role(non_staff_user, COURSE_DATA_RESEARCHER.external_key)
        non_staff_client.login(username=non_staff_user.username, password=self.password)

        resp = non_staff_client.put(
            self.get_url(self.course_key),
            data=json.dumps(GROUP_CONFIGURATION_JSON),
            content_type='application/json',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009


class DeleteGroupConfigurationsDetailHandlerAuthzTest(CourseAuthzTestMixin, BaseCourseViewTest):
    """
    Tests DELETE on group_configurations_detail_handler authorization using openedx-authz.
    The endpoint uses COURSES_MANAGE_GROUP_CONFIGURATIONS permission.
    """

    PARTITION_ID = 999

    view_name = "group_configurations_detail_handler"
    authz_roles_to_assign = [COURSE_STAFF.external_key]
    course_key_arg_name = 'course_key_string'
    extra_request_args = {'group_configuration_id': PARTITION_ID}

    def setUp(self):
        super().setUp()
        # Seed a random-scheme partition so DELETE finds a configuration to remove.
        self.course.user_partitions = [
            UserPartition(
                self.PARTITION_ID, 'Test Config', 'Test description',
                [Group(0, 'Group A'), Group(1, 'Group B')],
                scheme=None, scheme_id='random',
            )
        ]
        self.store.update_item(self.course, self.staff.id)

        self.authorized_client = Client()
        self.authorized_client.login(
            username=self.authorized_user.username, password=self.password
        )
        self.unauthorized_client = Client()
        self.unauthorized_client.login(
            username=self.unauthorized_user.username, password=self.password
        )

    def test_authorized_user_can_access(self):
        """User with COURSE_STAFF role can delete."""
        resp = self.authorized_client.delete(
            self.get_url(self.course_key),
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)  # noqa: PT009

    def test_unauthorized_user_cannot_access(self):
        """User without role cannot delete."""
        resp = self.unauthorized_client.delete(
            self.get_url(self.course_key),
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

    def test_role_scoped_to_course(self):
        """Authorization should only apply to the assigned course."""
        other_course = self.store.create_course("OtherOrg", "OtherCourse", "Run3", self.staff.id)

        resp = self.authorized_client.delete(
            self.get_url(other_course.id),
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

    def test_staff_user_allowed_via_legacy(self):
        """Staff users should still pass through legacy fallback."""
        self.client.login(username=self.staff.username, password=self.password)

        resp = self.client.delete(
            self.get_url(self.course_key),
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)  # noqa: PT009

    def test_superuser_allowed(self):
        """Superusers should always be allowed."""
        superuser = UserFactory(is_superuser=True, password=self.password)

        client = Client()
        client.login(username=superuser.username, password=self.password)

        resp = client.delete(
            self.get_url(self.course_key),
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)  # noqa: PT009

    def test_non_staff_user_cannot_access(self):
        """User without permissions should be denied."""
        non_staff_user = UserFactory(password=self.password)
        non_staff_client = Client()
        self.add_user_to_role(non_staff_user, COURSE_DATA_RESEARCHER.external_key)
        non_staff_client.login(username=non_staff_user.username, password=self.password)

        resp = non_staff_client.delete(
            self.get_url(self.course_key),
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009
