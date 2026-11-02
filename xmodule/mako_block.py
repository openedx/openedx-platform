"""
Code to handle mako templating for XModules and XBlocks.
"""


from web_fragments.fragment import Fragment

from .x_module import shim_xmodule_js


class MakoTemplateBlockBase:
    """
    XBlock intended as a mixin that uses a mako template
    to specify the block html.

    Expects the descriptor to have the `mako_template` attribute set
    with the name of the template to render, and it will pass
    the descriptor as the `module` parameter to that template
    """
    # pylint: disable=no-member

    js_module_name = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def get_context(self):
        """
        Return the context to render the mako template with
        """
        return {
            'module': self,
            'editable_metadata_fields': self.editable_metadata_fields
        }

    def studio_view(self, context):  # pylint: disable=unused-argument
        """
        View used in Studio.
        """
        # pylint: disable=no-member
        fragment = Fragment(
            self.runtime.service(self, 'mako').render_template(self.mako_template, self.get_context())
        )
        shim_xmodule_js(fragment, self.js_module_name)
        return fragment
