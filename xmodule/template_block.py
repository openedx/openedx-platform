"""
Template block
"""
import logging
from string import Template

from lxml import etree
from web_fragments.fragment import Fragment
from xblock.core import XBlock

from xmodule.editing_block import EditingMixin
from xmodule.modulestore.exceptions import ItemNotFoundError
from xmodule.raw_block import RawMixin
from xmodule.util.builtin_assets import add_css_to_fragment, add_webpack_js_to_fragment
from xmodule.x_module import ResourceTemplates, XModuleMixin, XModuleToXBlockMixin, shim_xmodule_js
from xmodule.xml_block import XmlMixin

log = logging.getLogger(__name__)


class CustomTagTemplateBlock(  # pylint: disable=abstract-method
    RawMixin,
    XmlMixin,
    EditingMixin,
    XModuleToXBlockMixin,
    ResourceTemplates,
    XModuleMixin,
):
    """
    A block which provides templates for CustomTagBlock. The template name
    is set on the `impl` attribute of CustomTagBlock. See below for more details
    on how to use it.
    """


@XBlock.needs('mako')
class CustomTagBlock(CustomTagTemplateBlock):  # pylint: disable=abstract-method
    """
    This block supports tags of the form
    <customtag option="val" option2="val2" impl="tagname"/>

    In this case, $tagname should refer to a file in data/custom_tags, which
    contains a Python string.Template formatted template that uses ${option} and
    ${option2} for the content.

    For instance:

    data/mycourse/custom_tags/book::
        More information given in <a href="/book/${page}">the text</a>

    course.xml::
        ...
        <customtag page="234" impl="book"/>
        ...

    Renders to::
        More information given in <a href="/book/234">the text</a>
    """
    resources_dir = None
    template_dir_name = 'customtag'

    def studio_view(self, _context):
        """
        Return the studio view.
        """
        fragment = Fragment(
            self.runtime.service(self, 'mako').render_cms_template(self.mako_template, self.get_context())
        )
        add_css_to_fragment(fragment, 'CustomTagBlockEditor.css')
        add_webpack_js_to_fragment(fragment, 'CustomTagBlockEditor')
        shim_xmodule_js(fragment, 'XMLEditingDescriptor')
        return fragment

    def render_template(self, system, xml_data):
        '''Render the template, given the definition xml_data'''
        if not xml_data:
            return "Please set the template for this custom tag."
        xmltree = etree.fromstring(xml_data)
        if 'impl' in xmltree.attrib and xmltree.attrib['impl']:
            template_name = xmltree.attrib['impl']
        else:
            # VS[compat]  backwards compatibility with old nested customtag structure
            child_impl = xmltree.find('impl')
            if child_impl is not None:
                template_name = child_impl.text
            else:
                # TODO (vshnayder): better exception type
                return Template("Could not find impl attribute in customtag {}").safe_substitute({})

        params = dict(list(xmltree.items()))

        # cdodge: look up the template as a module
        template_loc = self.location.replace(category='custom_tag_template', name=template_name)
        try:
            template_block = system.get_block(template_loc)
            template_block_data = template_block.data
        except ItemNotFoundError as ex:  # noqa: F841
            template_block_data = f"Could not find template block for custom tag with Id {template_name}"
            log.info(template_block_data)

        template = Template(template_block_data)
        return template.safe_substitute(params)

    @property
    def rendered_html(self):
        return self.render_template(self.runtime, self.data)

    def student_view(self, _context):
        """
        Renders the student view.
        """
        fragment = Fragment()
        fragment.add_content(self.rendered_html)
        return fragment

    def export_to_file(self):
        """
        Custom tags are special: since they're already pointers, we don't want
        to export them in a file with yet another layer of indirection.
        """
        return False


class TranslateCustomTagBlock(  # pylint: disable=abstract-method
    CustomTagBlock,
):
    """
    Converts olx of the form `<$custom_tag attr="" attr=""/>` to CustomTagBlock
    of the form `<customtag attr="" attr="" impl="$custom_tag"/>`.
    """
    resources_dir = None

    def render_template(self, system, xml_data):
        xml_string = ""
        if xml_data:
            xmltree = etree.fromstring(xml_data)
            xmltree = self.replace_xml(xmltree)
            xml_string = etree.tostring(xmltree, pretty_print=True).decode("utf-8")
        return super().render_template(system, xml_string or xml_data)

    def replace_xml(self, node):
        """
        Replaces the xml_data from <$custom_tag attr="" attr=""/> to
        <customtag attr="" attr="" impl="$custom_tag"/>.
        """
        tag = node.tag
        node.tag = 'customtag'
        node.attrib['impl'] = tag
        return node
