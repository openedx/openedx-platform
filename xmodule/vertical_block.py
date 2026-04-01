"""
VerticalBlock - an XBlock which renders its children in a column.
"""


import logging
from copy import copy
from datetime import datetime
from functools import reduce
from zoneinfo import ZoneInfo

from django.conf import settings
from lxml import etree
from openedx_filters.learning.filters import VerticalBlockChildRenderStarted, VerticalBlockRenderCompleted
from web_fragments.fragment import Fragment
from xblock.core import XBlock  # lint-amnesty, pylint: disable=wrong-import-order
from xblock.fields import Boolean, Scope
from xblock.progress import Progress

from xmodule.mako_block import MakoTemplateBlockBase
from xmodule.seq_block import SequenceFields
from xmodule.studio_editable import StudioEditableBlock
from xmodule.util.builtin_assets import add_webpack_js_to_fragment
from xmodule.util.misc import is_xblock_an_assignment
from xmodule.x_module import PUBLIC_VIEW, STUDENT_VIEW, XModuleFields
from xmodule.xml_block import XmlMixin

log = logging.getLogger(__name__)

# Make '_' a no-op so we can scrape strings. Using lambda instead of
#  `django.utils.translation.ugettext_noop` because Django cannot be imported in this file
_ = lambda text: text

# HACK: This shouldn't be hard-coded to two types
# OBSOLETE: This obsoletes 'type'
CLASS_PRIORITY = ['video', 'problem']


class VerticalFields:
    """
    A mixin to introduce fields in the Vertical Block.
    """

    discussion_enabled = Boolean(
        display_name=_("Enable in-context discussions for the Unit"),
        help=_("Add discussion for the Unit."),
        default=settings.FEATURES.get('IN_CONTEXT_DISCUSSION_ENABLED_DEFAULT', True),
        scope=Scope.settings,
    )


@XBlock.needs('user', 'bookmarks', 'mako')
@XBlock.wants('completion')
@XBlock.wants('call_to_action')
class VerticalBlock(
    SequenceFields,
    VerticalFields,
    XModuleFields,
    StudioEditableBlock,
    XmlMixin,
    MakoTemplateBlockBase,
    XBlock
):
    """
    Layout XBlock for rendering subblocks vertically.
    """

    resources_dir = 'assets/vertical'

    mako_template = 'widgets/sequence-edit.html'
    js_module_name = "VerticalBlock"

    has_children = True

    show_in_read_only_mode = True

    def _student_or_public_view(self, context, view):  # lint-amnesty, pylint: disable=too-many-statements
        """
        Renders the requested view type of the block in the LMS.
        """
        fragment = Fragment()
        contents = []

        if context:
            child_context = copy(context)
        else:
            child_context = {}

        if view == STUDENT_VIEW:
            if 'bookmarked' not in child_context:
                bookmarks_service = self.runtime.service(self, 'bookmarks')
                child_context['bookmarked'] = bookmarks_service.is_bookmarked(
                    usage_key=self.location),  # lint-amnesty, pylint: disable=no-member, trailing-comma-tuple
            if 'username' not in child_context:
                user_service = self.runtime.service(self, 'user')
                child_context['username'] = user_service.get_current_user().opt_attrs.get(
                    'edx-platform.username'
                )

        child_blocks = self.get_children()  # lint-amnesty, pylint: disable=no-member

        child_blocks_to_complete_on_view = set()
        completion_service = self.runtime.service(self, 'completion')
        if completion_service and completion_service.completion_tracking_enabled():
            child_blocks_to_complete_on_view = completion_service.blocks_to_mark_complete_on_view(child_blocks)
            complete_on_view_delay = completion_service.get_complete_on_view_delay_ms()

        child_context['child_of_vertical'] = True
        is_child_of_vertical = context.get('child_of_vertical', False)

        # pylint: disable=no-member
        for child in child_blocks:
            child_has_access_error = self.block_has_access_error(child)
            if context.get('hide_access_error_blocks') and child_has_access_error:
                continue
            child_block_context = copy(child_context)
            if child in list(child_blocks_to_complete_on_view):
                child_block_context['wrap_xblock_data'] = {
                    'mark-completed-on-view-after-delay': complete_on_view_delay
                }
            try:
                # .. filter_implemented_name: VerticalBlockChildRenderStarted
                # .. filter_type: org.openedx.learning.vertical_block_child.render.started.v1
                child, child_block_context = VerticalBlockChildRenderStarted.run_filter(
                    block=child, context=child_block_context
                )
            except VerticalBlockChildRenderStarted.PreventChildBlockRender as exc:
                log.info("Skipping %s from vertical block. Reason: %s", child, exc.message)
                continue

            rendered_child = child.render(view, child_block_context)
            fragment.add_fragment_resources(rendered_child)

            contents.append({
                'id': str(child.location),
                'content': rendered_child.content
            })

        completed = self.is_block_complete_for_assignments(completion_service)
        past_due = completed is False and self.due and self.due < datetime.now(ZoneInfo("UTC"))
        cta_service = self.runtime.service(self, 'call_to_action')
        vertical_banner_ctas = cta_service.get_ctas(self, 'vertical_banner', completed) if cta_service else []

        fragment_context = {
            'items': contents,
            'xblock_context': context,
            'unit_title': self.display_name_with_default if not is_child_of_vertical else None,
            'due': self.due,
            'completed': completed,
            'past_due': past_due,
            'has_assignments': completed is not None,
            'subsection_format': context.get('format', ''),
            'vertical_banner_ctas': vertical_banner_ctas,
        }

        if view == STUDENT_VIEW:
            fragment_context.update({
                'show_bookmark_button': child_context.get('show_bookmark_button', not is_child_of_vertical),
                'show_title': child_context.get('show_title', True),
                'bookmarked': child_context['bookmarked'],
                'bookmark_id': "{},{}".format(
                    child_context['username'], str(self.location)),  # pylint: disable=no-member
            })

        mako_service = self.runtime.service(self, 'mako')
        fragment.add_content(mako_service.render_lms_template('vert_module.html', fragment_context))

        add_webpack_js_to_fragment(fragment, 'VerticalStudentView')
        fragment.initialize_js('VerticalStudentView')

        try:
            # .. filter_implemented_name: VerticalBlockRenderCompleted
            # .. filter_type: org.openedx.learning.vertical_block.render.completed.v1
            _, fragment, context, view = VerticalBlockRenderCompleted.run_filter(
                block=self, fragment=fragment, context=context, view=view
            )
        except VerticalBlockRenderCompleted.PreventVerticalBlockRender as exc:
            log.info("VerticalBlock rendering stopped. Reason: %s", exc.message)
            fragment.content = exc.message

        return fragment

    def block_has_access_error(self, block):
        """
        Returns whether has_access_error is True for the given block (itself or any child)
        """
        # Check its access attribute (regular question will have it set)
        has_access_error = getattr(block, 'has_access_error', False)
        if has_access_error:
            return True

        # Check child nodes if they exist (e.g. randomized library question aka LegacyLibraryContentBlock)
        for child in block.get_children():
            has_access_error = getattr(child, 'has_access_error', False)
            if has_access_error:
                return True
            has_access_error = self.block_has_access_error(child)
        return has_access_error

    def student_view(self, context):
        """
        Renders the student view of the block in the LMS.
        """
        return self._student_or_public_view(context, STUDENT_VIEW)

    def public_view(self, context):
        """
        Renders the anonymous view of the block in the LMS.
        """
        return self._student_or_public_view(context, PUBLIC_VIEW)

    def author_view(self, context):
        """
        Renders the Studio preview view, which supports drag and drop.
        """
        fragment = Fragment()
        root_xblock = context.get('root_xblock')
        is_root = root_xblock and root_xblock.location == self.location  # pylint: disable=no-member

        # For the container page we want the full drag-and-drop, but for unit pages we want
        # a more concise version that appears alongside the "View =>" link-- unless it is
        # the unit page and the vertical being rendered is itself the unit vertical (is_root == True).
        if is_root or not context.get('is_unit_page'):
            self.render_children(context, fragment, can_reorder=True, can_add=True)
        return fragment

    def get_progress(self):
        """
        Returns the progress on this block and all children.
        """
        # TODO: Cache progress or children array?
        children = self.get_children()
        progresses = [child.get_progress() for child in children]
        progress = reduce(Progress.add_counts, progresses, None)
        return progress

    def get_icon_class(self):
        """
        Returns the highest priority icon class.
        """
        child_classes = {child.get_icon_class() for child in self.get_children()}
        new_class = 'other'
        for higher_class in CLASS_PRIORITY:
            if higher_class in child_classes:
                new_class = higher_class
        return new_class

    @classmethod
    def definition_from_xml(cls, xml_object, system):
        children = []
        for child in xml_object:
            try:
                child_block = system.process_xml(etree.tostring(child, encoding='unicode'))
                children.append(child_block.scope_ids.usage_id)
            except Exception as exc:  # pylint: disable=broad-except
                log.exception("Unable to load child when parsing Vertical. Continuing...")
                if system.error_tracker is not None:
                    system.error_tracker(f"ERROR: {exc}")
                continue
        return {}, children

    def definition_to_xml(self, resource_fs):
        xml_object = etree.Element('vertical')
        for child in self.get_children():
            self.runtime.add_block_as_child_node(child, xml_object)
        return xml_object

    @property
    def non_editable_metadata_fields(self):
        """
        Gather all fields which can't be edited.
        """
        non_editable_fields = super().non_editable_metadata_fields
        non_editable_fields.extend([
            self.fields['due'],  # lint-amnesty, pylint: disable=unsubscriptable-object
        ])
        return non_editable_fields

    def studio_view(self, context):
        fragment = super().studio_view(context)
        # This continues to use the old XModuleDescriptor javascript code to enabled studio editing.
        # TODO: Remove this when studio better supports editing of pure XBlocks.
        fragment.add_javascript('VerticalBlock = XModule.Descriptor;')
        return fragment

    def index_dictionary(self):
        """
        Return dictionary prepared with block content and type for indexing.
        """
        # return key/value fields in a Python dict object
        # values may be numeric / string or dict
        # default implementation is an empty dict
        xblock_body = super().index_dictionary()
        index_body = {
            "display_name": self.display_name,
        }
        if "content" in xblock_body:
            xblock_body["content"].update(index_body)
        else:
            xblock_body["content"] = index_body
        # We use "Sequence" for sequentials and verticals
        xblock_body["content_type"] = "Sequence"

        return xblock_body

    # So far, we only need this here. Move it somewhere more sensible if other bits of code want it too.
    def is_block_complete_for_assignments(self, completion_service):
        """
        Considers a block complete only if all scored & graded leaf blocks are complete.

        This is different from the normal `complete` flag because children of the block that are informative (like
        readings or videos) do not count. We only care about actual homework content.

        Compare with is_block_structure_complete_for_assignments in course_experience/utils.py, which does the same
        calculation, but for a BlockStructure node and its children.

        Returns:
            True if complete
            False if not
            None if no assignments present or no completion info present (don't show any past-due or complete info)
        """
        if not completion_service or not completion_service.completion_tracking_enabled():
            return None

        children = completion_service.get_completable_children(self)
        children_locations = [child.scope_ids.usage_id for child in children]
        completions = completion_service.get_completions(children_locations)

        all_complete = None
        for child in children:
            complete = completions[child.scope_ids.usage_id] == 1
            if is_xblock_an_assignment(child):
                if not complete:
                    return False
                all_complete = True

        return all_complete
