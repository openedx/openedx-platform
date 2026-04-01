"""
Block for running content split tests
"""


import json
import logging
import threading
from functools import reduce
from operator import itemgetter
from uuid import uuid4

from django.utils.functional import cached_property
from lxml import etree
from web_fragments.fragment import Fragment
from webob import Response
from xblock.core import XBlock
from xblock.exceptions import NoSuchServiceError
from xblock.fields import Integer, ReferenceValueDict, Scope, String
from xblock.progress import Progress
from xmodule.mako_block import MakoTemplateBlockBase
from xmodule.modulestore.inheritance import UserPartitionList
from xmodule.seq_block import ProctoringFields, SequenceMixin
from xmodule.studio_editable import StudioEditableBlock
from xmodule.util.builtin_assets import add_webpack_js_to_fragment
from xmodule.validation import StudioValidation, StudioValidationMessage
from xmodule.xml_block import XmlMixin
from xmodule.x_module import (
    ResourceTemplates,
    shim_xmodule_js,
    STUDENT_VIEW,
    XModuleMixin,
    XModuleToXBlockMixin,
)

log = logging.getLogger('edx.' + __name__)

# Make '_' a no-op so we can scrape strings. Using lambda instead of
#  `django.utils.translation.ugettext_noop` because Django cannot be imported in this file
_ = lambda text: text

DEFAULT_GROUP_NAME = _('Group ID {group_id}')


class UserPartitionValues(threading.local):
    """
    A thread-local storage for available user_partitions
    """
    def __init__(self):
        super().__init__()
        self.values = []

    def build_partition_values(self, all_user_partitions, selected_user_partition):
        """
        This helper method builds up the user_partition values that will
        be passed to the Studio editor
        """
        self.values = []
        # Add "No selection" value if there is not a valid selected user partition.
        if not selected_user_partition:
            self.values.append(SplitTestFields.no_partition_selected)
        for user_partition in get_split_user_partitions(all_user_partitions):
            self.values.append(
                {"display_name": user_partition.name, "value": user_partition.id}
            )
        return self.values


# All available user partitions (with value and display name). This is updated each time
# editable_metadata_fields is called.
user_partition_values = UserPartitionValues()


class SplitTestFields:
    """Fields needed for split test block"""
    has_children = True

    # Default value used for user_partition_id
    no_partition_selected = {'display_name': _("Not Selected"), 'value': -1}

    display_name = String(
        display_name=_("Display Name"),
        help=_("The display name for this component. (Not shown to learners)"),
        scope=Scope.settings,
        default=_("Content Experiment")
    )

    # Specified here so we can see what the value set at the course-level is.
    user_partitions = UserPartitionList(
        help=_("The list of group configurations for partitioning students in content experiments."),
        default=[],
        scope=Scope.settings
    )

    user_partition_id = Integer(
        help=_("The configuration defines how users are grouped for this content experiment. Caution: Changing the group configuration of a student-visible experiment will impact the experiment data."),  # lint-amnesty, pylint: disable=line-too-long
        scope=Scope.content,
        display_name=_("Group Configuration"),
        default=no_partition_selected["value"],
        values=lambda: user_partition_values.values  # Will be populated before the Studio editor is shown.
    )

    # group_id is an int
    # child is a serialized UsageId (aka Location).  This child
    # location needs to actually match one of the children of this
    # Block.  (expected invariant that we'll need to test, and handle
    # authoring tools that mess this up)
    group_id_to_child = ReferenceValueDict(
        help=_("Which child block students in a particular group_id should see"),
        scope=Scope.content
    )


def get_split_user_partitions(user_partitions):
    """
    Helper method that filters a list of user_partitions and returns just the
    ones that are suitable for the split_test block.
    """
    return [user_partition for user_partition in user_partitions if user_partition.scheme.name == "random"]


@XBlock.needs("i18n")
@XBlock.needs('user_tags')  # pylint: disable=abstract-method
@XBlock.needs('mako')
@XBlock.needs('partitions')
@XBlock.needs('user')
class SplitTestBlock(  # lint-amnesty, pylint: disable=abstract-method
    SplitTestFields,
    SequenceMixin,
    ProctoringFields,
    MakoTemplateBlockBase,
    XmlMixin,
    XModuleToXBlockMixin,
    ResourceTemplates,
    XModuleMixin,
    StudioEditableBlock,
):
    """
    Show the user the appropriate child.  Uses the ExperimentState
    API to figure out which child to show.

    Course staff still get put in an experimental condition, but have the option
    to see the other conditions.  The only thing that counts toward their
    grade/progress is the condition they are actually in.

    Technical notes:
      - There is more dark magic in this code than I'd like.  The whole varying-children +
        grading interaction is a tangle between super and subclasses of descriptors and
        blocks.
    """
    resources_dir = 'assets/split_test'

    filename_extension = "xml"

    has_author_view = True

    show_in_read_only_mode = True

    mako_template = "widgets/metadata-only-edit.html"
    studio_js_module_name = 'SequenceDescriptor'

    @cached_property
    def child_block(self):
        """
        Return the child block for the partition or None.
        """
        child_blocks = self.get_child_blocks()
        if len(child_blocks) >= 1:
            return child_blocks[0]
        return None

    @cached_property
    def child(self):
        """
        Return the user bound child block for the partition or None.

        Handles the AttributeError exception that may occur when attempting to retrieve
        an icon for the split_test xblock within the CMS.
        """
        try:
            if self.child_block is not None:
                return self.runtime.get_block_for_descriptor(self.child_block)
            else:
                return None
        except AttributeError:
            log.warning(
                "Error while getting block instance for descriptor with location: [%s]",
                self.location
            )
            return None

    def get_child_block_by_location(self, location):
        """
        Look through the children and look for one with the given location.
        Returns the block.
        If none match, return None
        """
        for child in self.get_children():
            if child.location == location:
                return child

        return None

    def get_content_titles(self):
        """
        Returns list of content titles for split_test's child.

        This overwrites the get_content_titles method included in x_module by default.

        WHY THIS OVERWRITE IS NECESSARY: If we fetch *all* of split_test's children,
        we'll end up getting all of the possible conditions users could ever see.
        Ex: If split_test shows a video to group A and HTML to group B, the
        regular get_content_titles in x_module will get the title of BOTH the video
        AND the HTML.

        We only want the content titles that should actually be displayed to the user.

        split_test's .child property contains *only* the child that should actually
        be shown to the user, so we call get_content_titles() on only that child.
        """
        return self.child.get_content_titles()

    def get_child_blocks(self):
        """
        For grading--return just the chosen child.

        Handles the NoSuchServiceError and ValueError exception that may occur when attempting to retrieve
        an icon for the split_test xblock within the CMS.
        """
        try:
            group_id = self.get_group_id()
        except NoSuchServiceError:
            log.warning(
                "Error while getting user service in runtime with location: [%s]",
                self.location
            )
            return []
        except ValueError:
            log.warning(
                "Error while getting group ID for partition with location: [%s]",
                self.location
            )
            return []

        if group_id is None:
            return []

        # group_id_to_child comes from json, so it has to have string keys
        str_group_id = str(group_id)
        child_block = None
        if str_group_id in self.group_id_to_child:
            child_location = self.group_id_to_child[str_group_id]
            child_block = self.get_child_block_by_location(child_location)
        else:
            # Oops.  Config error.
            log.debug("configuration error in split test block: invalid group_id %r (not one of %r).  Showing error", str_group_id, list(self.group_id_to_child.keys()))  # lint-amnesty, pylint: disable=line-too-long

        if child_block is None:
            # Peak confusion is great.  Now that we set child_block,
            # get_children() should return a list with one element--the
            # xmodule for the child
            log.debug("configuration error in split test block: no such child")
            return []

        return [child_block]

    def get_group_id(self):
        """
        Returns the group ID, or None if none is available.
        """
        partitions_service = self.runtime.service(self, 'partitions')
        user_service = self.runtime.service(self, 'user')
        user = user_service._django_user  # pylint: disable=protected-access
        return partitions_service.get_user_group_id_for_partition(user, self.user_partition_id)

    def _staff_view(self, context):
        """
        Render the staff view for a split test block.
        """
        fragment = Fragment()
        active_contents = []
        inactive_contents = []

        for child_location in self.children:  # pylint: disable=no-member
            child_block = self.get_child_block_by_location(child_location)
            child = self.runtime.get_block_for_descriptor(child_block)
            rendered_child = child.render(STUDENT_VIEW, context)
            fragment.add_fragment_resources(rendered_child)
            group_name, updated_group_id = self.get_data_for_vertical(child)

            if updated_group_id is None:  # inactive group
                group_name = child.display_name
                updated_group_id = [g_id for g_id, loc in self.group_id_to_child.items() if loc == child_location][0]
                inactive_contents.append({
                    'group_name': _('{group_name} (inactive)').format(group_name=group_name),
                    'id': str(child.location),
                    'content': rendered_child.content,
                    'group_id': updated_group_id,
                })
                continue

            active_contents.append({
                'group_name': group_name,
                'id': str(child.location),
                'content': rendered_child.content,
                'group_id': updated_group_id,
            })

        # Sort active and inactive contents by group name.
        sorted_active_contents = sorted(active_contents, key=itemgetter('group_name'))
        sorted_inactive_contents = sorted(inactive_contents, key=itemgetter('group_name'))

        # Use the new template
        fragment.add_content(self.runtime.service(self, 'mako').render_lms_template('split_test_staff_view.html', {
            'items': sorted_active_contents + sorted_inactive_contents,
        }))
        fragment.add_css('.split-test-child { display: none; }')
        fragment.add_javascript_url(self.runtime.local_resource_url(self, 'public/js/split_test_staff.js'))
        fragment.initialize_js('ABTestSelector')
        return fragment

    def author_view(self, context):
        """
        Renders the Studio preview by rendering each child so that they can all be seen and edited.
        """
        fragment = Fragment()
        root_xblock = context.get('root_xblock')
        is_root = root_xblock and root_xblock.location == self.location
        active_groups_preview = None
        inactive_groups_preview = None

        if is_root:
            [active_children, inactive_children] = self.active_and_inactive_children()
            active_groups_preview = self.studio_render_children(
                fragment, active_children, context
            )
            inactive_groups_preview = self.studio_render_children(
                fragment, inactive_children, context
            )

        fragment.add_content(self.runtime.service(self, 'mako').render_lms_template('split_test_author_view.html', {
            'split_test': self,
            'is_root': is_root,
            'is_configured': self.is_configured,
            'active_groups_preview': active_groups_preview,
            'inactive_groups_preview': inactive_groups_preview,
            'group_configuration_url': self.group_configuration_url,
        }))
        fragment.add_javascript_url(self.runtime.local_resource_url(self, 'public/js/split_test_author_view.js'))
        fragment.initialize_js('SplitTestAuthorView')

        return fragment

    def studio_render_children(self, fragment, children, context):
        """
        Renders the specified children and returns it as an HTML string. In addition, any
        dependencies are added to the specified fragment.
        """
        html = ""
        for active_child_block in children:
            active_child = self.runtime.get_block_for_descriptor(active_child_block)
            rendered_child = active_child.render(StudioEditableBlock.get_preview_view_name(active_child), context)
            if active_child.category == 'vertical':
                group_name, group_id = self.get_data_for_vertical(active_child)
                if group_name:
                    rendered_child.content = rendered_child.content.replace(
                        DEFAULT_GROUP_NAME.format(group_id=group_id),
                        group_name
                    )
            fragment.add_fragment_resources(rendered_child)
            html = html + rendered_child.content

        return html

    def studio_view(self, context):
        """
        Return the studio view.
        """
        fragment = Fragment(
            self.runtime.service(self, 'mako').render_cms_template(self.mako_template, self.get_context())
        )
        add_webpack_js_to_fragment(fragment, 'SplitTestBlockEditor')
        shim_xmodule_js(fragment, self.studio_js_module_name)
        return fragment

    def student_view(self, context):
        """
        Renders the contents of the chosen condition for students, and all the
        conditions for staff.
        """
        if self.child is None:
            # raise error instead?  In fact, could complain on block load...
            return Fragment(content="<div>Nothing here.  Move along.</div>")

        if self.runtime.user_is_staff:
            return self._staff_view(context)
        else:
            child_fragment = self.child.render(STUDENT_VIEW, context)
            fragment = Fragment(self.runtime.service(self, 'mako').render_lms_template('split_test_student_view.html', {
                'child_content': child_fragment.content,
                'child_id': self.child.scope_ids.usage_id,
            }))
            fragment.add_fragment_resources(child_fragment)
            fragment.add_javascript_url(self.runtime.local_resource_url(self, 'public/js/split_test_student.js'))
            fragment.initialize_js('SplitTestStudentView')
            return fragment

    @XBlock.handler
    def log_child_render(self, request, suffix=''):  # lint-amnesty, pylint: disable=unused-argument
        """
        Record in the tracking logs which child was rendered
        """
        try:
            child_id = str(self.child.scope_ids.usage_id)
        except Exception:
            log.info(
                "Can't get usage_id of Nonetype object in course {course_key}".format(
                    course_key=str(self.location.course_key)
                )
            )
            raise
        self.runtime.publish(self, 'xblock.split_test.child_render', {'child_id': child_id})
        return Response()

    def get_icon_class(self):
        return self.child.get_icon_class() if self.child else 'other'

    def get_progress(self):
        children = self.get_children()
        progresses = [child.get_progress() for child in children]
        progress = reduce(Progress.add_counts, progresses, None)
        return progress

    def get_data_for_vertical(self, vertical):
        """
        Return name and id of a group corresponding to `vertical`.
        """
        user_partition = self.get_selected_partition()
        if user_partition:
            for group in user_partition.groups:
                group_id = str(group.id)
                child_location = self.group_id_to_child.get(group_id, None)
                if child_location == vertical.location:
                    return (group.name, group.id)
        return (None, None)

    @property
    def tooltip_title(self):
        return getattr(self.child, 'tooltip_title', '')

    def definition_to_xml(self, resource_fs):
        xml_object = etree.Element('split_test')
        renderable_groups = {}
        # json.dumps doesn't know how to handle Location objects
        for group in self.group_id_to_child:
            renderable_groups[group] = str(self.group_id_to_child[group])
        xml_object.set('group_id_to_child', json.dumps(renderable_groups))
        xml_object.set('user_partition_id', str(self.user_partition_id))
        for child in self.get_children():
            self.runtime.add_block_as_child_node(child, xml_object)
        return xml_object

    @classmethod
    def definition_from_xml(cls, xml_object, system):
        children = []
        raw_group_id_to_child = xml_object.attrib.get('group_id_to_child', None)
        user_partition_id = xml_object.attrib.get('user_partition_id', None)
        try:
            group_id_to_child = json.loads(raw_group_id_to_child)
        except ValueError:
            msg = "group_id_to_child is not valid json"
            log.exception(msg)
            system.error_tracker(msg)

        for child in xml_object:
            try:
                block = system.process_xml(etree.tostring(child))
                children.append(block.scope_ids.usage_id)
            except Exception:  # lint-amnesty, pylint: disable=broad-except
                msg = "Unable to load child when parsing split_test block."
                log.exception(msg)
                system.error_tracker(msg)

        return ({
            'group_id_to_child': group_id_to_child,
            'user_partition_id': user_partition_id
        }, children)

    def get_context(self):
        _context = super().get_context()
        _context.update({
            'selected_partition': self.get_selected_partition()
        })
        return _context

    def has_dynamic_children(self):
        """
        Grading needs to know that only one of the children is actually "real".  This
        makes it use block.get_child_blocks().
        """
        return True

    def editor_saved(self, user, old_metadata, old_content):  # lint-amnesty, pylint: disable=unused-argument
        """
        Used to create default verticals for the groups.

        Assumes that a mutable modulestore is being used.
        """
        # Any existing value of user_partition_id will be in "old_content" instead of "old_metadata"
        # because it is Scope.content.
        if 'user_partition_id' not in old_content or old_content['user_partition_id'] != self.user_partition_id:
            selected_partition = self.get_selected_partition()
            if selected_partition is not None:
                self.group_id_mapping = {}  # pylint: disable=attribute-defined-outside-init
                for group in selected_partition.groups:
                    self._create_vertical_for_group(group, user.id)
                # Don't need to call update_item in the modulestore because the caller of this method will do it.
        else:
            # If children referenced in group_id_to_child have been deleted, remove them from the map.
            for str_group_id, usage_key in self.group_id_to_child.items():
                if usage_key not in self.children:  # pylint: disable=no-member
                    del self.group_id_to_child[str_group_id]

    @property
    def editable_metadata_fields(self):
        # Update the list of partitions based on the currently available user_partitions.
        user_partition_values.build_partition_values(self.user_partitions, self.get_selected_partition())

        editable_fields = super().editable_metadata_fields

        # Explicitly add user_partition_id, which does not automatically get picked up because it is Scope.content.
        # Note that this means it will be saved by the Studio editor as "metadata", but the field will
        # still update correctly.
        editable_fields[SplitTestFields.user_partition_id.name] = self._create_metadata_editor_info(
            SplitTestFields.user_partition_id
        )

        return editable_fields

    @property
    def non_editable_metadata_fields(self):
        non_editable_fields = super().non_editable_metadata_fields
        non_editable_fields.extend([
            SplitTestBlock.is_entrance_exam,
            SplitTestBlock.due,
            SplitTestBlock.user_partitions,
            SplitTestBlock.group_id_to_child,
        ])
        return non_editable_fields

    def get_selected_partition(self):
        """
        Returns the partition that this split block is currently using, or None
        if the currently selected partition ID does not match any of the defined partitions.
        """
        for user_partition in self.user_partitions:  # lint-amnesty, pylint: disable=not-an-iterable
            if user_partition.id == self.user_partition_id:
                return user_partition

        return None

    def active_and_inactive_children(self):
        """
        Returns two values:
        1. The active children of this split test, in the order of the groups.
        2. The remaining (inactive) children, in the order they were added to the split test.
        """
        children = self.get_children()

        user_partition = self.get_selected_partition()
        if not user_partition:
            return [], children

        def get_child_block(location):
            """
            Returns the child block which matches the specified location, or None if one is not found.
            """
            for child in children:
                if child.location == location:
                    return child
            return None

        # Compute the active children in the order specified by the user partition
        active_children = []
        for group in user_partition.groups:
            group_id = str(group.id)
            child_location = self.group_id_to_child.get(group_id, None)
            child = get_child_block(child_location)
            if child:
                active_children.append(child)

        # Compute the inactive children in the order they were added to the split test
        inactive_children = [child for child in children if child not in active_children]

        return active_children, inactive_children

    @property
    def is_configured(self):
        """
        Returns true if the split_test instance is associated with a UserPartition.
        """
        return not self.user_partition_id == SplitTestFields.no_partition_selected['value']

    def validate(self):
        """
        Validates the state of this split_test instance. This is the override of the general XBlock method,
        and it will also ask its superclass to validate.
        """
        validation = super().validate()
        split_test_validation = self.validate_split_test()

        if split_test_validation:
            return validation

        validation = StudioValidation.copy(validation)
        if validation and (not self.is_configured and len(split_test_validation.messages) == 1):
            validation.summary = split_test_validation.messages[0]
        else:
            validation.summary = self.general_validation_message(split_test_validation)
            validation.add_messages(split_test_validation)

        return validation

    def validate_split_test(self):
        """
        Returns a StudioValidation object describing the current state of the split_test_block
        (not including superclass validation messages).
        """
        _ = self.runtime.service(self, "i18n").ugettext
        split_validation = StudioValidation(self.location)
        if self.user_partition_id < 0:
            split_validation.add(
                StudioValidationMessage(
                    StudioValidationMessage.NOT_CONFIGURED,
                    _("The experiment is not associated with a group configuration."),
                    action_class='edit-button',
                    action_label=_("Select a Group Configuration")
                )
            )
        else:
            user_partition = self.get_selected_partition()
            if not user_partition:
                split_validation.add(
                    StudioValidationMessage(
                        StudioValidationMessage.ERROR,
                        _("The experiment uses a deleted group configuration. Select a valid group configuration or delete this experiment.")  # lint-amnesty, pylint: disable=line-too-long
                    )
                )
            else:
                # If the user_partition selected is not valid for the split_test block, error.
                # This can only happen via XML and import/export.
                if not get_split_user_partitions([user_partition]):
                    split_validation.add(
                        StudioValidationMessage(
                            StudioValidationMessage.ERROR,
                            _("The experiment uses a group configuration that is not supported for experiments. "
                              "Select a valid group configuration or delete this experiment.")
                        )
                    )
                else:
                    [active_children, inactive_children] = self.active_and_inactive_children()
                    if len(active_children) < len(user_partition.groups):
                        split_validation.add(
                            StudioValidationMessage(
                                StudioValidationMessage.ERROR,
                                _("The experiment does not contain all of the groups in the configuration."),
                                action_runtime_event='add-missing-groups',
                                action_label=_("Add Missing Groups")
                            )
                        )
                    if len(inactive_children) > 0:
                        split_validation.add(
                            StudioValidationMessage(
                                StudioValidationMessage.WARNING,
                                _("The experiment has an inactive group. "
                                  "Move content into active groups, then delete the inactive group.")
                            )
                        )
        return split_validation

    def general_validation_message(self, validation=None):
        """
        Returns just a summary message about whether or not this split_test instance has
        validation issues (not including superclass validation messages). If the split_test instance
        validates correctly, this method returns None.
        """
        if validation is None:
            validation = self.validate_split_test()

        if not validation:
            has_error = any(message.type == StudioValidationMessage.ERROR for message in validation.messages)
            return StudioValidationMessage(
                StudioValidationMessage.ERROR if has_error else StudioValidationMessage.WARNING,
                _("This content experiment has issues that affect content visibility.")
            )
        return None

    @XBlock.handler
    def add_missing_groups(self, request, suffix=''):  # lint-amnesty, pylint: disable=unused-argument
        """
        Create verticals for any missing groups in the split test instance.

        Called from Studio view.
        """
        user_partition = self.get_selected_partition()

        changed = False
        for group in user_partition.groups:
            str_group_id = str(group.id)
            if str_group_id not in self.group_id_to_child:
                user_id = self.runtime.service(self, 'user').get_current_user().opt_attrs['edx-platform.user_id']
                self._create_vertical_for_group(group, user_id)
                changed = True

        if changed:
            # user.id - to be fixed by Publishing team
            self.runtime.modulestore.update_item(self, None)
        return Response()

    @property
    def group_configuration_url(self):  # lint-amnesty, pylint: disable=missing-function-docstring
        assert hasattr(self.runtime, 'modulestore') and hasattr(self.runtime.modulestore, 'get_course'), \
            "modulestore has to be available"

        course_block = self.runtime.modulestore.get_course(self.location.course_key)
        group_configuration_url = None
        if 'split_test' in course_block.advanced_modules:
            user_partition = self.get_selected_partition()
            if user_partition:
                group_configuration_url = "{url}#{configuration_id}".format(
                    url='/group_configurations/' + str(self.location.course_key),
                    configuration_id=str(user_partition.id)
                )

        return group_configuration_url

    def _create_vertical_for_group(self, group, user_id):
        """
        Creates a vertical to associate with the group.

        This appends the new vertical to the end of children, and updates group_id_to_child.
        A mutable modulestore is needed to call this method (will need to update after mixed
        modulestore work, currently relies on mongo's create_item method).
        """
        assert hasattr(self.runtime, 'modulestore') and hasattr(self.runtime.modulestore, 'create_item'), \
            "editor_saved should only be called when a mutable modulestore is available"
        modulestore = self.runtime.modulestore
        dest_usage_key = self.location.replace(category="vertical", name=uuid4().hex)
        metadata = {'display_name': DEFAULT_GROUP_NAME.format(group_id=group.id)}
        modulestore.create_item(
            user_id,
            self.location.course_key,
            dest_usage_key.block_type,
            block_id=dest_usage_key.block_id,
            definition_data=None,
            metadata=metadata,
            runtime=self.runtime,
        )
        self.children.append(dest_usage_key)  # pylint: disable=no-member
        self.group_id_to_child[str(group.id)] = dest_usage_key
