"""
'library' XBlock (LibraryRoot)
"""


import logging

from web_fragments.fragment import Fragment
from xblock.core import XBlock
from xblock.fields import Boolean, List, Scope, String

from xmodule.studio_editable import StudioEditableBlock

log = logging.getLogger(__name__)

# Make '_' a no-op so we can scrape strings. Using lambda instead of
#  `django.utils.translation.ugettext_noop` because Django cannot be imported in this file
_ = lambda text: text


@XBlock.needs('mako')
class LibraryRoot(XBlock):
    """
    The LibraryRoot is the root XBlock of a content library. All other blocks in
    the library are its children. It contains metadata such as the library's
    display_name.
    """
    resources_dir = None

    display_name = String(
        help=_("The display name for this component."),
        default="Library",
        display_name=_("Library Display Name"),
        scope=Scope.settings
    )
    advanced_modules = List(
        display_name=_("Advanced Module List"),
        help=_("Enter the names of the advanced components to use in your library."),
        scope=Scope.settings,
        xml_node=True,
    )
    show_children_previews = Boolean(
        display_name="Hide children preview",
        help="Choose if preview of library contents is shown",
        scope=Scope.user_state,
        default=True
    )
    has_children = True
    has_author_view = True

    def __str__(self):
        return f"Library: {self.display_name}"

    def author_view(self, context):
        """
        Renders the Studio preview view.
        """
        fragment = Fragment()
        self.render_children(context, fragment, can_reorder=False, can_add=True)
        return fragment

    def render_children(self, context, fragment, can_reorder=False, can_add=False):  # pylint: disable=unused-argument
        """
        Renders the children of the block with HTML appropriate for Studio. Reordering is not supported.
        """
        contents = []

        paging = context.get('paging', None)

        children_count = len(self.children)  # pylint: disable=no-member
        item_start, item_end = 0, children_count

        # TODO sort children
        if paging:
            page_number = paging.get('page_number', 0)
            raw_page_size = paging.get('page_size', None)
            page_size = raw_page_size if raw_page_size is not None else children_count
            item_start, item_end = page_size * page_number, page_size * (page_number + 1)

        children_to_show = self.children[item_start:item_end]  # pylint: disable=no-member

        force_render = context.get('force_render', None)
        context['can_move'] = False

        for child_key in children_to_show:
            # Children must have a separate context from the library itself. Make a copy.
            child_context = context.copy()
            child_context['show_preview'] = self.show_children_previews
            child_context['can_edit_visibility'] = False
            child = self.runtime.get_block(child_key)
            child_view_name = StudioEditableBlock.get_preview_view_name(child)

            if str(child.location) == force_render:
                child_context['show_preview'] = True

            if child_context['show_preview']:
                rendered_child = self.runtime.render_child(child, child_view_name, child_context)
            else:
                rendered_child = self.runtime.render_child_placeholder(child, child_view_name, child_context)
            fragment.add_fragment_resources(rendered_child)

            contents.append({
                'id': str(child.location),
                'content': rendered_child.content,
            })

        fragment.add_content(
            # For historic reasons, this template is in the LMS folder, and some external code may depend on that.
            self.runtime.service(self, 'mako').render_lms_template("studio_render_paged_children_view.html", {
                'items': contents,
                'xblock_context': context,
                'can_add': can_add,
                'first_displayed': item_start,
                'total_children': children_count,
                'displayed_children': len(children_to_show),
                'previews': self.show_children_previews
            })
        )

    @property
    def display_org_with_default(self):
        """
        Org display names are not implemented. This just provides API compatibility with CourseBlock.
        Always returns the raw 'org' field from the key.
        """
        return self.scope_ids.usage_id.course_key.org

    @property
    def display_number_with_default(self):
        """
        Display numbers are not implemented. This just provides API compatibility with CourseBlock.
        Always returns the raw 'library' field from the key.
        """
        return self.scope_ids.usage_id.course_key.library

    @XBlock.json_handler
    def trigger_previews(self, request_body, suffix):  # pylint: disable=unused-argument
        """ Enable or disable previews in studio for library children. """
        self.show_children_previews = request_body.get('showChildrenPreviews', self.show_children_previews)
        return {'showChildrenPreviews': self.show_children_previews}
