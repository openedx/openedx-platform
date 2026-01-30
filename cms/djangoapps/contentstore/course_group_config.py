"""
Class for manipulating groups configuration on a course object.
"""


import json
import logging
from collections import defaultdict

from django.utils.translation import gettext as _

from cms.djangoapps.contentstore.utils import reverse_usage_url
from common.djangoapps.util.db import MYSQL_MAX_INT, generate_int_id
from lms.lib.utils import get_parent_unit
# Re-exported for backward compatibility - other modules import these from here
from openedx.core.djangoapps.course_groups.constants import (  # pylint: disable=unused-import
    COHORT_SCHEME,
    CONTENT_GROUP_CONFIGURATION_DESCRIPTION,
    CONTENT_GROUP_CONFIGURATION_NAME,
    ENROLLMENT_SCHEME,
    RANDOM_SCHEME,
)
from openedx.core.djangoapps.course_groups.partition_scheme import get_cohorted_user_partition
from xmodule.partitions.partitions import MINIMUM_UNUSED_PARTITION_ID, ReadOnlyUserPartitionError, UserPartition  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.partitions.partitions_service import get_all_partitions_for_course  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.split_test_block import get_split_user_partitions  # lint-amnesty, pylint: disable=wrong-import-order

MINIMUM_GROUP_ID = MINIMUM_UNUSED_PARTITION_ID

log = logging.getLogger(__name__)


class GroupConfigurationsValidationError(Exception):
    """
    An error thrown when a group configurations input is invalid.
    """
    pass  # lint-amnesty, pylint: disable=unnecessary-pass


class GroupConfiguration:
    """
    Prepare Group Configuration for the course.
    """
    def __init__(self, json_string, course, configuration_id=None):
        """
        Receive group configuration as a json (`json_string`), deserialize it
        and validate.
        """
        self.configuration = GroupConfiguration.parse(json_string)
        self.course = course
        self.assign_id(configuration_id)
        self.assign_group_ids()
        self.validate()

    @staticmethod
    def parse(json_string):
        """
        Deserialize given json that represents group configuration.
        """
        try:
            configuration = json.loads(json_string.decode("utf-8"))
        except ValueError:
            raise GroupConfigurationsValidationError(_("invalid JSON"))  # lint-amnesty, pylint: disable=raise-missing-from
        configuration["version"] = UserPartition.VERSION
        return configuration

    def validate(self):
        """
        Validate group configuration representation.
        """
        if not self.configuration.get("name"):
            raise GroupConfigurationsValidationError(_("must have name of the configuration"))
        if len(self.configuration.get('groups', [])) < 1:
            raise GroupConfigurationsValidationError(_("must have at least one group"))

    def assign_id(self, configuration_id=None):
        """
        Assign id for the json representation of group configuration.
        """
        if configuration_id:
            self.configuration['id'] = int(configuration_id)
        else:
            self.configuration['id'] = generate_int_id(
                MINIMUM_GROUP_ID, MYSQL_MAX_INT, GroupConfiguration.get_used_ids(self.course)
            )

    def assign_group_ids(self):
        """
        Assign ids for the group_configuration's groups.
        """
        used_ids = [g.id for p in get_all_partitions_for_course(self.course) for g in p.groups]
        # Assign ids to every group in configuration.
        for group in self.configuration.get('groups', []):
            if group.get('id') is None:
                group["id"] = generate_int_id(MINIMUM_GROUP_ID, MYSQL_MAX_INT, used_ids)
                used_ids.append(group["id"])

    @staticmethod
    def get_used_ids(course):
        """
        Return a list of IDs that already in use.
        """
        return {p.id for p in get_all_partitions_for_course(course)}

    def get_user_partition(self):
        """
        Get user partition for saving in course.
        """
        try:
            return UserPartition.from_json(self.configuration)
        except ReadOnlyUserPartitionError:
            raise GroupConfigurationsValidationError(_("unable to load this type of group configuration"))  # lint-amnesty, pylint: disable=raise-missing-from

    @staticmethod
    def _get_usage_dict(course, unit, block, scheme_name=None):
        """
        Get usage info for unit/block.
        """
        parent_unit = get_parent_unit(block)

        if unit == parent_unit and not block.has_children:
            # Display the topmost unit page if
            # the item is a child of the topmost unit and doesn't have its own children.
            unit_for_url = unit
        elif (not parent_unit and unit.get_parent()) or (unit == parent_unit and block.has_children):
            # Display the item's page rather than the unit page if
            # the item is one level below the topmost unit and has children, or
            # the item itself *is* the topmost unit (and thus does not have a parent unit, but is not an orphan).
            unit_for_url = block
        else:
            # If the item is nested deeper than two levels (the topmost unit > vertical > ... > item)
            # display the page for the nested vertical element.
            parent = block.get_parent()
            nested_vertical = block
            while parent != parent_unit:
                nested_vertical = parent
                parent = parent.get_parent()
            unit_for_url = nested_vertical

        unit_url = reverse_usage_url(
            'container_handler',
            course.location.course_key.make_usage_key(unit_for_url.location.block_type, unit_for_url.location.block_id)
        )

        usage_dict = {'label': f"{unit.display_name} / {block.display_name}", 'url': unit_url}
        if scheme_name == RANDOM_SCHEME:
            validation_summary = block.general_validation_message()
            usage_dict.update({'validation': validation_summary.to_json() if validation_summary else None})
        return usage_dict

    @staticmethod
    def get_content_experiment_usage_info(store, course):
        """
        Get usage information for all Group Configurations currently referenced by a split_test instance.
        """
        split_tests = store.get_items(course.id, qualifiers={'category': 'split_test'})
        return GroupConfiguration._get_content_experiment_usage_info(store, course, split_tests)

    @staticmethod
    def get_split_test_partitions_with_usage(store, course):
        """
        Returns json split_test group configurations updated with usage information.
        """
        usage_info = GroupConfiguration.get_content_experiment_usage_info(store, course)
        configurations = []
        for partition in get_split_user_partitions(course.user_partitions):
            configuration = partition.to_json()
            configuration['usage'] = usage_info.get(partition.id, [])
            configurations.append(configuration)
        return configurations

    @staticmethod
    def _get_content_experiment_usage_info(store, course, split_tests):  # pylint: disable=unused-argument
        """
        Returns all units names, their urls and validation messages.

        Returns:
        {'user_partition_id':
            [
                {
                    'label': 'Unit 1 / Experiment 1',
                    'url': 'url_to_unit_1',
                    'validation': {'message': 'a validation message', 'type': 'warning'}
                },
                {
                    'label': 'Unit 2 / Experiment 2',
                    'url': 'url_to_unit_2',
                    'validation': {'message': 'another validation message', 'type': 'error'}
                }
            ],
        }
        """
        usage_info = defaultdict(list)
        for split_test in split_tests:
            unit = split_test.get_parent()
            if not unit:
                log.warning("Unable to find parent for split_test %s", split_test.location)
                # Make sure that this user_partition appears in the output even though it has no content
                usage_info[split_test.user_partition_id] = []
                continue

            usage_info[split_test.user_partition_id].append(GroupConfiguration._get_usage_dict(
                course=course,
                unit=unit,
                block=split_test,
                scheme_name=RANDOM_SCHEME,
            ))
        return usage_info

    @staticmethod
    def get_partitions_usage_info(store, course):
        """
        Returns all units names and their urls.

        Returns:
        {'partition_id':
            {'group_id':
                [
                    {
                        'label': 'Unit 1 / Problem 1',
                        'url': 'url_to_unit_1'
                    },
                    {
                        'label': 'Unit 2 / Problem 2',
                        'url': 'url_to_unit_2'
                    }
                ],
            }
        }
        """
        items = store.get_items(course.id, settings={'group_access': {'$exists': True}}, include_orphans=False)

        usage_info = defaultdict(lambda: defaultdict(list))
        for block, partition_id, group_id in GroupConfiguration._iterate_items_and_group_ids(course, items):
            unit = block.get_parent()
            if not unit:
                log.warning("Unable to find parent for component %s", block.location)
                continue

            usage_info[partition_id][group_id].append(GroupConfiguration._get_usage_dict(
                course,
                unit=unit,
                block=block,
            ))

        return usage_info

    @staticmethod
    def get_content_groups_items_usage_info(store, course):
        """
        Get usage information on items for content groups.
        """
        items = store.get_items(course.id, settings={'group_access': {'$exists': True}})

        return GroupConfiguration._get_content_groups_items_usage_info(course, items)

    @staticmethod
    def _get_content_groups_items_usage_info(course, items):
        """
        Returns all items names and their urls.

        This will return only groups for all non-random partitions.

        Returns:
        {'partition_id':
            {'group_id':
                [
                    {
                        'label': 'Problem 1 / Problem 1',
                        'url': 'url_to_item_1'
                    },
                    {
                        'label': 'Problem 2 / Problem 2',
                        'url': 'url_to_item_2'
                    }
                ],
            }
        }
        """
        usage_info = defaultdict(lambda: defaultdict(list))
        for block, partition_id, group_id in GroupConfiguration._iterate_items_and_group_ids(course, items):
            usage_info[partition_id][group_id].append(GroupConfiguration._get_usage_dict(
                course,
                unit=block,
                block=block,
            ))

        return usage_info

    @staticmethod
    def _iterate_items_and_group_ids(course, items):
        """
        Iterate through items and group IDs in a course.

        This will yield group IDs for all user partitions except those with a scheme of random.

        Yields: tuple of (item, partition_id, group_id)
        """
        all_partitions = get_all_partitions_for_course(course)
        for config in all_partitions:
            if config is not None and config.scheme.name != RANDOM_SCHEME:
                for item in items:
                    if hasattr(item, 'group_access') and item.group_access:
                        group_ids = item.group_access.get(config.id, [])

                        for group_id in group_ids:
                            yield item, config.id, group_id

    @staticmethod
    def update_usage_info(store, course, configuration):
        """
        Update usage information for particular Group Configuration.

        Returns json of particular group configuration updated with usage information.
        """
        configuration_json = None
        # Get all Experiments that use particular  Group Configuration in course.
        if configuration.scheme.name == RANDOM_SCHEME:
            split_tests = store.get_items(
                course.id,
                category='split_test',
                content={'user_partition_id': configuration.id}
            )
            configuration_json = configuration.to_json()
            usage_information = GroupConfiguration._get_content_experiment_usage_info(store, course, split_tests)
            configuration_json['usage'] = usage_information.get(configuration.id, [])
        elif configuration.scheme.name == COHORT_SCHEME:
            # In case if scheme is "cohort"
            configuration_json = GroupConfiguration.update_partition_usage_info(store, course, configuration)
        return configuration_json

    @staticmethod
    def update_partition_usage_info(store, course, configuration):
        """
        Update usage information for particular Partition Configuration.

        Returns json of particular partition configuration updated with usage information.
        """
        usage_info = GroupConfiguration.get_partitions_usage_info(store, course)
        partition_configuration = configuration.to_json()

        for group in partition_configuration['groups']:
            group['usage'] = usage_info[configuration.id].get(group['id'], [])

        return partition_configuration

    @staticmethod
    def get_or_create_content_group(store, course):
        """
        Returns the first user partition from the course which uses the
        CohortPartitionScheme, or generates one if no such partition is
        found.  The created partition is not saved to the course until
        the client explicitly creates a group within the partition and
        POSTs back.
        """
        content_group_configuration = get_cohorted_user_partition(course)
        if content_group_configuration is None:
            content_group_configuration = UserPartition(
                id=generate_int_id(MINIMUM_GROUP_ID, MYSQL_MAX_INT, GroupConfiguration.get_used_ids(course)),
                name=CONTENT_GROUP_CONFIGURATION_NAME,
                description=CONTENT_GROUP_CONFIGURATION_DESCRIPTION,
                groups=[],
                scheme_id=COHORT_SCHEME
            )
            return content_group_configuration.to_json()

        content_group_configuration = GroupConfiguration.update_partition_usage_info(
            store,
            course,
            content_group_configuration
        )
        return content_group_configuration

    @staticmethod
    def get_all_user_partition_details(store, course):
        """
        Returns all the available partitions with updated usage information

        :return: list of all partitions available with details
        """
        all_partitions = get_all_partitions_for_course(course)
        all_updated_partitions = []
        for partition in all_partitions:
            configuration = GroupConfiguration.update_partition_usage_info(
                store,
                course,
                partition
            )
            all_updated_partitions.append(configuration)
        return all_updated_partitions
