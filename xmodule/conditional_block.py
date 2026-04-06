"""
ConditionalBlock is an XBlock which you can use for disabling some XBlocks by conditions.
"""


import json
import logging

from lazy import lazy
from lxml import etree
from opaque_keys.edx.locator import BlockUsageLocator
from web_fragments.fragment import Fragment
from xblock.core import XBlock
from xblock.fields import ReferenceList, Scope, String

from openedx.core.djangolib.markup import HTML, Text
from xmodule.mako_block import MakoTemplateBlockBase
from xmodule.modulestore.exceptions import ItemNotFoundError
from xmodule.seq_block import SequenceMixin
from xmodule.studio_editable import StudioEditableBlock
from xmodule.util.builtin_assets import add_webpack_js_to_fragment
from xmodule.validation import StudioValidation, StudioValidationMessage
from xmodule.x_module import STUDENT_VIEW, ResourceTemplates, XModuleMixin, XModuleToXBlockMixin, shim_xmodule_js
from xmodule.xml_block import XmlMixin

log = logging.getLogger('edx.' + __name__)

# Make '_' a no-op so we can scrape strings
_ = lambda text: text


@XBlock.needs('mako')
class ConditionalBlock(
    SequenceMixin,
    MakoTemplateBlockBase,
    XmlMixin,
    XModuleToXBlockMixin,
    ResourceTemplates,
    XModuleMixin,
    StudioEditableBlock,
):
    """
    Blocks child blocks from showing unless certain conditions are met.

    Example:

        <conditional sources="i4x://.../problem_1; i4x://.../problem_2" completed="True">
            <show sources="i4x://.../test_6; i4x://.../Avi_resources"/>
            <video url_name="secret_video" />
        </conditional>

        <conditional> tag attributes:
            sources - location id of required blocks, separated by ';'

            submitted - map to `is_submitted` block method.
            (pressing RESET button makes this function to return False.)

            attempted - map to `is_attempted` block method
            correct - map to `is_correct` block method
            poll_answer - map to `poll_answer` block attribute
            voted - map to `voted` block attribute

        <show> tag attributes:
            sources - location id of required blocks, separated by ';'

        You can add you own rules for <conditional> tag, like
        "completed", "attempted" etc. To do that yo must extend
        `ConditionalBlock.conditions_map` variable and add pair:
            my_attr: my_property/my_method

        After that you can use it:
            <conditional my_attr="some value" ...>
                ...
            </conditional>

        And my_property/my_method will be called for required blocks.

    """

    display_name = String(
        display_name=_("Display Name"),
        help=_("The display name for this component."),
        scope=Scope.settings,
        default=_('Conditional')
    )

    show_tag_list = ReferenceList(
        help=_("List of urls of children that are references to external modules"),
        scope=Scope.content
    )

    sources_list = ReferenceList(
        display_name=_("Source Components"),
        help=_("The component location IDs of all source components that are used to determine whether a learner is "
               "shown the content of this conditional block. Copy the component location ID of a component from its "
               "Settings dialog in Studio."),
        scope=Scope.content
    )

    conditional_attr = String(
        display_name=_("Conditional Attribute"),
        help=_("The attribute of the source components that determines whether a learner is shown the content of this "
               "conditional block."),
        scope=Scope.content,
        default='correct',
        values=lambda: [{'display_name': xml_attr, 'value': xml_attr}
                        for xml_attr in ConditionalBlock.conditions_map]
    )

    conditional_value = String(
        display_name=_("Conditional Value"),
        help=_("The value that the conditional attribute of the source components must match before a learner is shown "
               "the content of this conditional block."),
        scope=Scope.content,
        default='True'
    )

    conditional_message = String(
        display_name=_("Blocked Content Message"),
        help=_("The message that is shown to learners when not all conditions are met to show the content of this "
               "conditional block. Include {link} in the text of your message to give learners a direct link to "
               "required units. For example, 'You must complete {link} before you can access this unit'."),
        scope=Scope.content,
        default=_('You must complete {link} before you can access this unit.')
    )

    has_children = True

    _tag_name = 'conditional'

    resources_dir = None

    filename_extension = "xml"

    has_score = False

    show_in_read_only_mode = True

    mako_template = 'widgets/metadata-edit.html'
    studio_js_module_name = 'SequenceDescriptor'

    # Map
    # key: <tag attribute in xml>
    # value: <name of block attribute>
    conditions_map = {
        'poll_answer': 'poll_answer',  # poll_question attr

        # problem was submitted (it can be wrong)
        # if student will press reset button after that,
        # state will be reverted
        'submitted': 'is_submitted',  # capa_problem attr

        # if student attempted problem
        'attempted': 'is_attempted',  # capa_problem attr

        # if problem is full points
        'correct': 'is_correct',

        'voted': 'voted'  # poll_question attr
    }

    def __init__(self, *args, **kwargs):
        """
        Create an instance of the Conditional XBlock.
        """
        super().__init__(*args, **kwargs)
        # Convert sources xml_attribute to a ReferenceList field type so Location/Locator
        # substitution can be done.
        if not self.sources_list:
            if 'sources' in self.xml_attributes and isinstance(self.xml_attributes['sources'], str):
                self.sources_list = [
                    # TODO: it is not clear why we are replacing the run here (which actually is a no-op
                    # for old-style course locators. However, this is the implementation of
                    # CourseLocator.make_usage_key_from_deprecated_string, which was previously
                    # being called in this location.
                    BlockUsageLocator.from_string(item).replace(run=self.location.course_key.run)
                    for item in ConditionalBlock.parse_sources(self.xml_attributes)
                ]

    def is_condition_satisfied(self):  # lint-amnesty, pylint: disable=missing-function-docstring
        attr_name = self.conditions_map[self.conditional_attr]

        if self.conditional_value and self.get_required_blocks:
            for block in self.get_required_blocks:
                if not hasattr(block, attr_name):
                    # We don't throw an exception here because it is possible for
                    # the required block to have a property but
                    # for the resulting block to be a (flavor of) ErrorBlock.
                    # So just log and return false.
                    if block is not None:
                        # We do not want to log when block is None, and it is when requester
                        # does not have access to the requested required block.
                        log.warning('Error in conditional block: \
                            required module {block} has no {block_attr}'.format(block=block, block_attr=attr_name))
                    return False

                attr = getattr(block, attr_name)
                if callable(attr):
                    attr = attr()

                if self.conditional_value != str(attr):
                    break
            else:
                return True
        return False

    def student_view(self, _context):
        """
        Renders the student view.
        """
        fragment = Fragment()
        fragment.add_content(self.get_html())
        add_webpack_js_to_fragment(fragment, 'ConditionalBlockDisplay')
        shim_xmodule_js(fragment, 'Conditional')
        return fragment

    def get_html(self):
        required_html_ids = [block.location.html_id() for block in self.get_required_blocks]
        return self.runtime.service(self, 'mako').render_lms_template('conditional_ajax.html', {
            'element_id': self.location.html_id(),
            'ajax_url': self.ajax_url,
            'depends': ';'.join(required_html_ids)
        })

    def author_view(self, context):
        """
        Renders the Studio preview by rendering each child so that they can all be seen and edited.
        """
        fragment = Fragment()
        root_xblock = context.get('root_xblock')
        is_root = root_xblock and root_xblock.location == self.location
        if is_root:
            # User has clicked the "View" link. Show a preview of all possible children:
            self.render_children(context, fragment, can_reorder=True, can_add=True)
        # else: When shown on a unit page, don't show any sort of preview -
        # just the status of this block in the validation area.

        return fragment

    def studio_view(self, _context):
        """
        Return the studio view.
        """
        fragment = Fragment(
            self.runtime.service(self, 'mako').render_cms_template(self.mako_template, self.get_context())
        )
        add_webpack_js_to_fragment(fragment, 'ConditionalBlockEditor')
        shim_xmodule_js(fragment, self.studio_js_module_name)
        return fragment

    def handle_ajax(self, _dispatch, _data):
        """This is called by courseware.block_render, to handle
        an AJAX call.
        """
        if not self.is_condition_satisfied():
            context = {'module': self,
                       'message': self.conditional_message}
            html = self.runtime.service(self, 'mako').render_lms_template('conditional_block.html', context)
            return json.dumps({'fragments': [{'content': html}], 'message': bool(self.conditional_message)})

        fragments = [child.render(STUDENT_VIEW).to_dict() for child in self.get_children()]

        return json.dumps({'fragments': fragments})

    def get_icon_class(self):
        new_class = 'other'
        # HACK: This shouldn't be hard-coded to two types
        # OBSOLETE: This obsoletes 'type'
        class_priority = ['video', 'problem']

        child_classes = [
            child_block.get_icon_class() for child_block in self.get_children()
        ]
        for c in class_priority:
            if c in child_classes:
                new_class = c
        return new_class

    @staticmethod
    def parse_sources(xml_element):
        """ Parse xml_element 'sources' attr and return a list of location strings. """
        sources = xml_element.get('sources')
        if sources:
            return [location.strip() for location in sources.split(';')]

    @lazy
    def get_required_blocks(self):
        """
        Returns a list of bound XBlocks instances upon which XBlock depends.
        """
        return [
            self.runtime.get_block_for_descriptor(block) for block in self.get_required_block_descriptors()
        ]

    def get_required_block_descriptors(self):
        """
        Returns a list of unbound XBlocks instances upon which this XBlock depends.
        """
        blocks = []
        for location in self.sources_list:
            try:
                block = self.runtime.get_block(location)
                blocks.append(block)
            except ItemNotFoundError:
                msg = "Invalid module by location."
                log.exception(msg)
                self.runtime.error_tracker(msg)

        return blocks

    @classmethod
    def definition_from_xml(cls, xml_object, system):
        children = []
        show_tag_list = []
        definition = {}
        for conditional_attr in cls.conditions_map:
            conditional_value = xml_object.get(conditional_attr)
            if conditional_value is not None:
                definition.update({
                    'conditional_attr': conditional_attr,
                    'conditional_value': str(conditional_value),
                })
        for child in xml_object:
            if child.tag == 'show':
                locations = cls.parse_sources(child)
                for location in locations:
                    children.append(location)
                    show_tag_list.append(location)
            else:
                try:
                    block = system.process_xml(etree.tostring(child, encoding='unicode'))
                    children.append(block.scope_ids.usage_id)
                except:  # lint-amnesty, pylint: disable=bare-except
                    msg = "Unable to load child when parsing Conditional."
                    log.exception(msg)
                    system.error_tracker(msg)
        definition.update({
            'show_tag_list': show_tag_list,
            'conditional_message': xml_object.get('message', '')
        })
        return definition, children

    def definition_to_xml(self, resource_fs):
        xml_object = etree.Element(self._tag_name)
        for child in self.get_children():
            if child.location not in self.show_tag_list:
                self.runtime.add_block_as_child_node(child, xml_object)

        if self.show_tag_list:
            show_str = HTML('<show sources="{sources}" />').format(
                sources=Text(';'.join(str(location) for location in self.show_tag_list)))
            xml_object.append(etree.fromstring(show_str))

        # Overwrite the original sources attribute with the value from sources_list, as
        # Locations may have been changed to Locators.
        stringified_sources_list = [str(loc) for loc in self.sources_list]
        self.xml_attributes['sources'] = ';'.join(stringified_sources_list)
        self.xml_attributes[self.conditional_attr] = self.conditional_value
        self.xml_attributes['message'] = self.conditional_message
        return xml_object

    def validate(self):
        validation = super().validate()
        if not self.sources_list:
            conditional_validation = StudioValidation(self.location)
            conditional_validation.add(
                StudioValidationMessage(
                    StudioValidationMessage.NOT_CONFIGURED,
                    _("This component has no source components configured yet."),
                    action_class='edit-button',
                    action_label=_("Configure list of sources")
                )
            )
            validation = StudioValidation.copy(validation)
            validation.summary = conditional_validation.messages[0]
        return validation

    @property
    def non_editable_metadata_fields(self):
        non_editable_fields = super().non_editable_metadata_fields
        non_editable_fields.extend([
            ConditionalBlock.due,
            ConditionalBlock.show_tag_list,
        ])
        return non_editable_fields
