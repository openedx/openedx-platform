"""
Tests for the Split Testing Block
"""
import json
from unittest.mock import Mock, patch

import ddt
import lxml
from fs.memoryfs import MemoryFS

from xmodule.modulestore.tests.factories import CourseFactory
from xmodule.modulestore.tests.utils import MixedSplitTestCase
from xmodule.partitions.partitions import MINIMUM_UNUSED_PARTITION_ID, Group, UserPartition
from xmodule.partitions.tests.test_partitions import MockPartitionService, MockUserPartitionScheme, PartitionTestCase
from xmodule.split_test_block import SplitTestBlock, SplitTestFields, get_split_user_partitions, user_partition_values
from xmodule.tests import prepare_block_runtime
from xmodule.tests.test_course_block import DummyModuleStoreRuntime
from xmodule.tests.xml import XModuleXmlImportTest
from xmodule.tests.xml import factories as xml
from xmodule.validation import StudioValidationMessage
from xmodule.x_module import AUTHOR_VIEW, STUDENT_VIEW


class SplitTestBlockFactory(xml.XmlImportFactory):
    """
    Factory for generating SplitTestBlocks for testing purposes
    """
    tag = 'split_test'


class SplitTestUtilitiesTest(PartitionTestCase):
    """
    Tests for utility methods related to split_test block.
    """

    def test_split_user_partitions(self):
        """
        Tests the get_split_user_partitions helper method.
        """
        first_random_partition = UserPartition(
            0, 'first_partition', 'First Partition', [Group("0", 'alpha'), Group("1", 'beta')],
            self.random_scheme
        )
        second_random_partition = UserPartition(
            0, 'second_partition', 'Second Partition', [Group("4", 'zeta'), Group("5", 'omega')],
            self.random_scheme
        )
        all_partitions = [
            first_random_partition,
            # Only UserPartitions with scheme "random" will be returned as available options.
            UserPartition(
                1, 'non_random_partition', 'Will Not Be Returned', [Group("1", 'apple'), Group("2", 'banana')],
                self.non_random_scheme
            ),
            second_random_partition
        ]
        assert [first_random_partition, second_random_partition] == get_split_user_partitions(all_partitions)


class SplitTestBlockTest(XModuleXmlImportTest, PartitionTestCase):
    """
    Base class for all split_module tests.
    """
    def setUp(self):
        super().setUp()
        self.course_id = 'test_org/test_course_number/test_run'
        # construct module
        course = xml.CourseFactory.build()
        sequence = xml.SequenceFactory.build(parent=course)
        split_test = SplitTestBlockFactory(
            parent=sequence,
            attribs={
                'user_partition_id': '0',
                'group_id_to_child': '{"0": "i4x://edX/xml_test_course/html/split_test_cond0", "1":'
                                     ' "i4x://edX/xml_test_course/html/split_test_cond1"}'
            }
        )
        xml.HtmlFactory(parent=split_test, url_name='split_test_cond0', text='HTML FOR GROUP 0')
        xml.HtmlFactory(parent=split_test, url_name='split_test_cond1', text='HTML FOR GROUP 1')

        self.course = self.process_xml(course)
        self.course_sequence = self.course.get_children()[0]
        user = Mock(username='ma', email='ma@edx.org', is_staff=False, is_active=True)
        prepare_block_runtime(self.course.runtime, user=user, add_get_block=True)

        self.course.runtime.export_fs = MemoryFS()

        # Create mock partition service, as these tests are running with XML in-memory system.
        self.course.user_partitions = [
            self.user_partition,
            UserPartition(
                MINIMUM_UNUSED_PARTITION_ID, 'second_partition', 'Second Partition',
                [
                    Group(str(MINIMUM_UNUSED_PARTITION_ID + 1), 'abel'),
                    Group(str(MINIMUM_UNUSED_PARTITION_ID + 2), 'baker'), Group("103", 'charlie')
                ],
                MockUserPartitionScheme()
            )
        ]
        partitions_service = MockPartitionService(
            self.course,
            course_id=self.course.id,
        )
        self.course.runtime._services['partitions'] = partitions_service  # pylint: disable=protected-access

        # Mock user_service user
        user_service = Mock()
        user_service._django_user = user  # pylint: disable=protected-access

        self.split_test_block = self.course_sequence.get_children()[0]
        self.split_test_block.runtime = self.course.runtime
        self.split_test_block.bind_for_student(user.id)

        # Create mock modulestore for getting the course. Needed for rendering the HTML
        # view, since mock services exist and the rendering code will not short-circuit.
        mocked_modulestore = Mock()
        mocked_modulestore.get_course.return_value = self.course
        self.split_test_block.runtime.modulestore = mocked_modulestore


@ddt.ddt
class SplitTestBlockLMSTest(SplitTestBlockTest):
    """
    Test the split test block
    """

    def setUp(self):
        super().setUp()

        content_gating_flag_patcher = patch(
            'openedx.features.content_type_gating.partitions.ContentTypeGatingConfig.current',
            return_value=Mock(enabled=False, studio_override_enabled=False),
        ).start()
        self.addCleanup(content_gating_flag_patcher.stop)

    @ddt.data((0, 'split_test_cond0'), (1, 'split_test_cond1'))
    @ddt.unpack
    def test_child(self, user_tag, child_url_name):
        self.user_partition.scheme.current_group = self.user_partition.groups[user_tag]
        assert self.split_test_block.child_block.url_name == child_url_name

    @ddt.data((0, 'HTML FOR GROUP 0'), (1, 'HTML FOR GROUP 1'))
    @ddt.unpack
    def test_get_html(self, user_tag, child_content):
        self.user_partition.scheme.current_group = self.user_partition.groups[user_tag]
        assert child_content in self.course.runtime.render(self.split_test_block, STUDENT_VIEW).content

    @ddt.data(0, 1)
    def test_child_missing_tag_value(self, _user_tag):  # noqa: PT019
        # If user_tag has a missing value, we should still get back a valid child url
        assert self.split_test_block.child_block.url_name in ['split_test_cond0', 'split_test_cond1']

    @ddt.data(100, 200, 300, 400, 500, 600, 700, 800, 900, 1000)
    def test_child_persist_new_tag_value_when_tag_missing(self, _user_tag):  # noqa: PT019
        # If a user_tag has a missing value, a group should be saved/persisted for that user.
        # So, we check that we get the same url_name when we call on the url_name twice.
        # We run the test ten times so that, if our storage is failing, we'll be most likely to notice it.
        assert self.split_test_block.child_block.url_name == self.split_test_block.child_block.url_name

    # Patch the definition_to_xml for the html children.
    @patch('xmodule.html_block.HtmlBlock.definition_to_xml')
    def test_export_import_round_trip(self, def_to_xml):
        # The HtmlBlock definition_to_xml tries to write to the filesystem
        # before returning an xml object. Patch this to just return the xml.
        def_to_xml.return_value = lxml.etree.Element('html')

        # Mock out the process_xml
        # Expect it to return a child block for the SplitTestDescriptor when called.
        self.course.runtime.process_xml = Mock()

        # Write out the xml.
        xml_obj = self.split_test_block.definition_to_xml(MemoryFS())

        assert xml_obj.get('user_partition_id') == '0'
        assert xml_obj.get('group_id_to_child') is not None

        # Read the xml back in.
        fields, children = SplitTestBlock.definition_from_xml(xml_obj, self.course.runtime)
        assert fields.get('user_partition_id') == '0'
        assert fields.get('group_id_to_child') is not None
        assert len(children) == 2


class SplitTestBlockStudioTest(SplitTestBlockTest):
    """
    Unit tests for how split test interacts with Studio.
    """

    @patch('xmodule.split_test_block.SplitTestBlock.group_configuration_url', return_value='http://example.com')
    def test_render_author_view(self, group_configuration_url):  # pylint: disable=unused-argument
        """
        Test the rendering of the Studio author view.
        """

        def create_studio_context(root_xblock):
            """
            Context for rendering the studio "author_view".
            """
            return {
                'reorderable_items': set(),
                'root_xblock': root_xblock,
            }

        # The split_test block should render both its groups when it is the root
        context = create_studio_context(self.split_test_block)
        html = self.course.runtime.render(self.split_test_block, AUTHOR_VIEW, context).content
        assert 'HTML FOR GROUP 0' in html
        assert 'HTML FOR GROUP 1' in html

        # When rendering as a child, it shouldn't render either of its groups
        context = create_studio_context(self.course_sequence)
        html = self.course.runtime.render(self.split_test_block, AUTHOR_VIEW, context).content
        assert 'HTML FOR GROUP 0' not in html
        assert 'HTML FOR GROUP 1' not in html

        # The "Create Missing Groups" button should be rendered when groups are missing
        context = create_studio_context(self.split_test_block)
        self.split_test_block.user_partitions = [
            UserPartition(0, 'first_partition', 'First Partition',
                          [Group("0", 'alpha'), Group("1", 'beta'), Group("2", 'gamma')])
        ]
        html = self.course.runtime.render(self.split_test_block, AUTHOR_VIEW, context).content
        assert 'HTML FOR GROUP 0' in html
        assert 'HTML FOR GROUP 1' in html

    def test_group_configuration_url(self):
        """
        Test creation of correct Group Configuration URL.
        """
        mocked_course = Mock(advanced_modules=['split_test'])
        mocked_modulestore = Mock()
        mocked_modulestore.get_course.return_value = mocked_course
        self.split_test_block.runtime.modulestore = mocked_modulestore

        self.split_test_block.user_partitions = [
            UserPartition(0, 'first_partition', 'First Partition', [Group("0", 'alpha'), Group("1", 'beta')])
        ]

        expected_url = '/group_configurations/edX/xml_test_course/101#0'
        assert expected_url == self.split_test_block.group_configuration_url

    def test_editable_settings(self):
        """
        Test the setting information passed back from editable_metadata_fields.
        """
        editable_metadata_fields = self.split_test_block.editable_metadata_fields
        assert SplitTestBlock.display_name.name in editable_metadata_fields
        assert SplitTestBlock.due.name not in editable_metadata_fields
        assert SplitTestBlock.user_partitions.name not in editable_metadata_fields

        # user_partition_id will always appear in editable_metadata_settings, regardless
        # of the selected value.
        assert SplitTestBlock.user_partition_id.name in editable_metadata_fields

    def test_non_editable_settings(self):
        """
        Test the settings that are marked as "non-editable".
        """
        non_editable_metadata_fields = self.split_test_block.non_editable_metadata_fields
        assert SplitTestBlock.due in non_editable_metadata_fields
        assert SplitTestBlock.user_partitions in non_editable_metadata_fields
        assert SplitTestBlock.display_name not in non_editable_metadata_fields

    @patch('xmodule.split_test_block.user_partition_values.values')
    def test_available_partitions(self, _):  # noqa: PT019
        """
        Tests that the available partitions are populated correctly when editable_metadata_fields are called
        """
        # user_partitions is empty, only the "Not Selected" item will appear.
        self.split_test_block.user_partition_id = SplitTestFields.no_partition_selected['value']
        self.split_test_block.editable_metadata_fields  # pylint: disable=pointless-statement  # noqa: B018
        partitions = user_partition_values.values
        assert 1 == len(partitions)
        assert SplitTestFields.no_partition_selected['value'] == partitions[0]['value']

        # Populate user_partitions and call editable_metadata_fields again
        self.split_test_block.user_partitions = [
            UserPartition(
                0, 'first_partition', 'First Partition', [Group("0", 'alpha'), Group("1", 'beta')],
                self.random_scheme
            ),
            # Only UserPartitions with scheme "random" will be returned as available options.
            UserPartition(
                1, 'non_random_partition', 'Will Not Be Returned', [Group("1", 'apple'), Group("2", 'banana')],
                self.non_random_scheme
            )
        ]
        self.split_test_block.editable_metadata_fields  # pylint: disable=pointless-statement  # noqa: B018
        partitions = user_partition_values.values
        assert 2 == len(partitions)
        assert SplitTestFields.no_partition_selected['value'] == partitions[0]['value']
        assert 0 == partitions[1]['value']
        assert 'first_partition' == partitions[1]['display_name']

        # Try again with a selected partition and verify that there is no option for "No Selection"
        self.split_test_block.user_partition_id = 0
        self.split_test_block.editable_metadata_fields  # pylint: disable=pointless-statement  # noqa: B018
        partitions = user_partition_values.values
        assert 1 == len(partitions)
        assert 0 == partitions[0]['value']
        assert 'first_partition' == partitions[0]['display_name']

        # Finally try again with an invalid selected partition and verify that "No Selection" is an option
        self.split_test_block.user_partition_id = 999
        self.split_test_block.editable_metadata_fields  # pylint: disable=pointless-statement  # noqa: B018
        partitions = user_partition_values.values
        assert 2 == len(partitions)
        assert SplitTestFields.no_partition_selected['value'] == partitions[0]['value']
        assert 0 == partitions[1]['value']
        assert 'first_partition' == partitions[1]['display_name']

    def test_active_and_inactive_children(self):
        """
        Tests the active and inactive children returned for different split test configurations.
        """
        split_test_block = self.split_test_block
        children = split_test_block.get_children()

        # Verify that a split test has no active children if it has no specified user partition.
        split_test_block.user_partition_id = -1
        [active_children, inactive_children] = split_test_block.active_and_inactive_children()
        assert active_children == []
        assert inactive_children == children

        # Verify that all the children are returned as active for a correctly configured split_test
        split_test_block.user_partition_id = 0
        split_test_block.user_partitions = [
            UserPartition(0, 'first_partition', 'First Partition', [Group("0", 'alpha'), Group("1", 'beta')])
        ]
        [active_children, inactive_children] = split_test_block.active_and_inactive_children()
        assert active_children == children
        assert inactive_children == []

        # Verify that a split_test does not return inactive children in the active children
        self.split_test_block.user_partitions = [
            UserPartition(0, 'first_partition', 'First Partition', [Group("0", 'alpha')])
        ]
        [active_children, inactive_children] = split_test_block.active_and_inactive_children()
        assert active_children == [children[0]]
        assert inactive_children == [children[1]]

        # Verify that a split_test ignores misconfigured children
        self.split_test_block.user_partitions = [
            UserPartition(0, 'first_partition', 'First Partition', [Group("0", 'alpha'), Group("2", 'gamma')])
        ]
        [active_children, inactive_children] = split_test_block.active_and_inactive_children()
        assert active_children == [children[0]]
        assert inactive_children == [children[1]]

        # Verify that a split_test referring to a non-existent user partition has no active children
        self.split_test_block.user_partition_id = 2
        [active_children, inactive_children] = split_test_block.active_and_inactive_children()
        assert active_children == []
        assert inactive_children == children

    def test_validation_messages(self):  # pylint: disable=too-many-statements
        """
        Test the validation messages produced for different split test configurations.
        """
        split_test_block = self.split_test_block

        def verify_validation_message(message, expected_message, expected_message_type,
                                      expected_action_class=None, expected_action_label=None,
                                      expected_action_runtime_event=None):
            """
            Verify that the validation message has the expected validation message and type.
            """
            assert message.text == expected_message
            assert message.type == expected_message_type
            if expected_action_class:
                assert message.action_class == expected_action_class
            else:
                assert not hasattr(message, 'action_class')
            if expected_action_label:
                assert message.action_label == expected_action_label
            else:
                assert not hasattr(message, 'action_label')
            if expected_action_runtime_event:
                assert message.action_runtime_event == expected_action_runtime_event
            else:
                assert not hasattr(message, 'action_runtime_event')

        def verify_summary_message(general_validation, expected_message, expected_message_type):
            """
            Verify that the general validation message has the expected validation message and type.
            """
            assert general_validation.text == expected_message
            assert general_validation.type == expected_message_type

        # Verify the messages for an unconfigured user partition
        split_test_block.user_partition_id = -1
        validation = split_test_block.validate()
        assert len(validation.messages) == 0
        verify_validation_message(
            validation.summary,
            "The experiment is not associated with a group configuration.",
            StudioValidationMessage.NOT_CONFIGURED,
            'edit-button',
            "Select a Group Configuration",
        )

        # Verify the messages for a correctly configured split_test
        split_test_block.user_partition_id = 0
        split_test_block.user_partitions = [
            UserPartition(0, 'first_partition', 'First Partition', [Group("0", 'alpha'), Group("1", 'beta')])
        ]
        validation = split_test_block.validate_split_test()
        assert validation
        assert split_test_block.general_validation_message() is None, None

        # Verify the messages for a split test with too few groups
        split_test_block.user_partitions = [
            UserPartition(0, 'first_partition', 'First Partition',
                          [Group("0", 'alpha'), Group("1", 'beta'), Group("2", 'gamma')])
        ]
        validation = split_test_block.validate()
        assert len(validation.messages) == 1
        verify_validation_message(
            validation.messages[0],
            "The experiment does not contain all of the groups in the configuration.",
            StudioValidationMessage.ERROR,
            expected_action_runtime_event='add-missing-groups',
            expected_action_label="Add Missing Groups"
        )
        verify_summary_message(
            validation.summary,
            "This content experiment has issues that affect content visibility.",
            StudioValidationMessage.ERROR
        )
        # Verify the messages for a split test with children that are not associated with any group
        split_test_block.user_partitions = [
            UserPartition(0, 'first_partition', 'First Partition',
                          [Group("0", 'alpha')])
        ]
        validation = split_test_block.validate()
        assert len(validation.messages) == 1
        verify_validation_message(
            validation.messages[0],
            "The experiment has an inactive group. Move content into active groups, then delete the inactive group.",
            StudioValidationMessage.WARNING
        )
        verify_summary_message(
            validation.summary,
            "This content experiment has issues that affect content visibility.",
            StudioValidationMessage.WARNING
        )
        # Verify the messages for a split test with both missing and inactive children
        split_test_block.user_partitions = [
            UserPartition(0, 'first_partition', 'First Partition',
                          [Group("0", 'alpha'), Group("2", 'gamma')])
        ]
        validation = split_test_block.validate()
        assert len(validation.messages) == 2
        verify_validation_message(
            validation.messages[0],
            "The experiment does not contain all of the groups in the configuration.",
            StudioValidationMessage.ERROR,
            expected_action_runtime_event='add-missing-groups',
            expected_action_label="Add Missing Groups"
        )
        verify_validation_message(
            validation.messages[1],
            "The experiment has an inactive group. Move content into active groups, then delete the inactive group.",
            StudioValidationMessage.WARNING
        )
        # With two messages of type error and warning priority given to error.
        verify_summary_message(
            validation.summary,
            "This content experiment has issues that affect content visibility.",
            StudioValidationMessage.ERROR
        )

        # Verify the messages for a split test referring to a non-existent user partition
        split_test_block.user_partition_id = 2
        validation = split_test_block.validate()
        assert len(validation.messages) == 1
        verify_validation_message(
            validation.messages[0],
            "The experiment uses a deleted group configuration. "
            "Select a valid group configuration or delete this experiment.",
            StudioValidationMessage.ERROR
        )
        verify_summary_message(
            validation.summary,
            "This content experiment has issues that affect content visibility.",
            StudioValidationMessage.ERROR
        )

        # Verify the message for a split test referring to a non-random user partition
        split_test_block.user_partitions = [
            UserPartition(
                10, 'incorrect_partition', 'Non Random Partition', [Group("0", 'alpha'), Group("2", 'gamma')],
                scheme=self.non_random_scheme
            )
        ]
        split_test_block.user_partition_id = 10
        validation = split_test_block.validate()
        assert len(validation.messages) == 1
        verify_validation_message(
            validation.messages[0],
            "The experiment uses a group configuration that is not supported for experiments. "
            "Select a valid group configuration or delete this experiment.",
            StudioValidationMessage.ERROR
        )
        verify_summary_message(
            validation.summary,
            "This content experiment has issues that affect content visibility.",
            StudioValidationMessage.ERROR
        )


class SplitTestBlockExportImportTest(MixedSplitTestCase):
    """
    Export import test for split test xblock.
    """
    maxDiff = None

    def setUp(self):
        super().setUp()

        self.course = CourseFactory.create(modulestore=self.store)
        self.chapter = self.make_block("chapter", self.course)
        self.sequential = self.make_block("sequential", self.chapter)
        self.vertical = self.make_block("vertical", self.sequential)
        self.split_test_block = self.make_block(
            "split_test",
            self.vertical,
            display_name="A Split Test",
            user_partition_id=2,
        )
        self.child_blocks = [
            self.make_block("html", self.split_test_block, display_name=f"Hello HTML {i}")
            for i in range(1, 3)
        ]
        self.split_test_block.group_id_to_child = {
            '0': self.child_blocks[0].location,
            '1': self.child_blocks[1].location,
        }
        self.store.update_item(self.split_test_block, self.user_id)

    def test_xml_export_import_cycle(self):
        """
        Test the export-import cycle.
        """
        split_test_block = self.store.get_item(self.split_test_block.location)

        expected_olx = (
            '<split_test group_id_to_child="{group_id_to_child}" user_partition_id="2" display_name="A Split Test">\n'
            '  <html url_name="{child_blocks[0].location.block_id}"/>\n'
            '  <html url_name="{child_blocks[1].location.block_id}"/>\n'
            '</split_test>\n'
        ).format(
            child_blocks=self.child_blocks,
            group_id_to_child=json.dumps(
                {
                    "0": str(self.child_blocks[0].location),
                    "1": str(self.child_blocks[1].location),
                }
            ).replace('"', '&quot;')
        )
        export_fs = MemoryFS()
        # Set the virtual FS to export the olx to.
        split_test_block.runtime.export_fs = export_fs  # pylint: disable=protected-access

        # Export the olx.
        node = lxml.etree.Element("unknown_root")
        split_test_block.add_xml_to_node(node)

        # Read it back
        with export_fs.open('{dir}/{file_name}.xml'.format(  # noqa: UP032
            dir=split_test_block.scope_ids.usage_id.block_type,
            file_name=split_test_block.scope_ids.usage_id.block_id
        )) as f:
            exported_olx = f.read()

        # And compare.
        assert exported_olx == expected_olx

        runtime = DummyModuleStoreRuntime(load_error_blocks=True, course_id=split_test_block.location.course_key)
        runtime.resources_fs = export_fs

        # Now import it.
        olx_element = lxml.etree.fromstring(exported_olx)
        imported_split_test_block = SplitTestBlock.parse_xml(olx_element, runtime, None)

        # Check the new XBlock has the same properties as the old one.
        assert imported_split_test_block.display_name == split_test_block.display_name
        assert len(imported_split_test_block.children) == len(split_test_block.children)
        assert imported_split_test_block.children == split_test_block.children
        assert imported_split_test_block.user_partition_id == split_test_block.user_partition_id
        assert imported_split_test_block.group_id_to_child['0'] == str(split_test_block.group_id_to_child['0'])
        assert imported_split_test_block.group_id_to_child['1'] == str(split_test_block.group_id_to_child['1'])
