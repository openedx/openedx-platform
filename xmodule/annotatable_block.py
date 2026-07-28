# pylint: disable=missing-module-docstring

import logging
import textwrap
import warnings

from django.conf import settings
from lxml import etree
from web_fragments.fragment import Fragment
from xblock.core import XBlock
from xblock.fields import Scope, String
from xblocks_contrib.annotatable import AnnotatableBlock as _ExtractedAnnotatableBlock

from openedx.core.djangolib.markup import HTML, Text
from xmodule.editing_block import EditingMixin
from xmodule.raw_block import RawMixin
from xmodule.util.builtin_assets import add_css_to_fragment, add_webpack_js_to_fragment
from xmodule.x_module import ResourceTemplates, XModuleMixin, XModuleToXBlockMixin, shim_xmodule_js
from xmodule.xml_block import XmlMixin

log = logging.getLogger(__name__)

# Make '_' a no-op so we can scrape strings. Using lambda instead of
#  `django.utils.translation.ugettext_noop` because Django cannot be imported in this file
_ = lambda text: text


@XBlock.needs('mako')
class _BuiltInAnnotatableBlock(
    RawMixin,
    XmlMixin,
    EditingMixin,
    XModuleToXBlockMixin,
    ResourceTemplates,
    XModuleMixin,
):
    """
    Annotatable XBlock.

    .. deprecated:: 2026-03
       This built-in annotatable block is deprecated. Please use the extracted ``AnnotatableBlock``
       from ``xblocks_contrib.annotatable`` instead.
    """

    is_extracted = False

    data = String(
        help=_("XML data for the annotation"),
        scope=Scope.content,
        default=textwrap.dedent(HTML("""
        <annotatable>
            <instructions>
                <p>Enter your (optional) instructions for the exercise in HTML format.</p>
                <p>Annotations are specified by an <code>{}annotation{}</code> tag which may may have the following attributes:</p>
                <ul class="instructions-template">
                    <li><code>title</code> (optional). Title of the annotation. Defaults to <i>Commentary</i> if omitted.</li>
                    <li><code>body</code> (<b>required</b>). Text of the annotation.</li>
                    <li><code>problem</code> (optional). Numeric index of the problem associated with this annotation. This is a zero-based index, so the first problem on the page would have <code>problem="0"</code>.</li>
                    <li><code>highlight</code> (optional). Possible values: yellow, red, orange, green, blue, or purple. Defaults to yellow if this attribute is omitted.</li>
                </ul>
            </instructions>
            <p>Add your HTML with annotation spans here.</p>
            <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. <annotation title="My title" body="My comment" highlight="yellow" problem="0">Ut sodales laoreet est, egestas gravida felis egestas nec.</annotation> Aenean at volutpat erat. Cras commodo viverra nibh in aliquam.</p>
            <p>Nulla facilisi. <annotation body="Basic annotation example." problem="1">Pellentesque id vestibulum libero.</annotation> Suspendisse potenti. Morbi scelerisque nisi vitae felis dictum mattis. Nam sit amet magna elit. Nullam volutpat cursus est, sit amet sagittis odio vulputate et. Curabitur euismod, orci in vulputate imperdiet, augue lorem tempor purus, id aliquet augue turpis a est. Aenean a sagittis libero. Praesent fringilla pretium magna, non condimentum risus elementum nec. Pellentesque faucibus elementum pharetra. Pellentesque vitae metus eros.</p>
        </annotatable>
        """).format(Text('<'), Text('>')))
    )
    display_name = String(
        display_name=_("Display Name"),
        help=_("The display name for this component."),
        scope=Scope.settings,
        default=_('Annotation'),
    )

    uses_xmodule_styles_setup = True

    studio_js_module_name = "XMLEditingDescriptor"
    mako_template = "widgets/raw-edit.html"

    icon_class = 'annotatable'
    resources_dir = None

    HIGHLIGHT_COLORS = ['yellow', 'orange', 'purple', 'blue', 'green']

    def _get_annotation_class_attr(self, index, el):  # pylint: disable=unused-argument
        """ Returns a dict with the CSS class attribute to set on the annotation
            and an XML key to delete from the element.
         """

        attr = {}
        cls = ['annotatable-span', 'highlight']
        highlight_key = 'highlight'
        color = el.get(highlight_key)

        if color is not None:
            if color in self.HIGHLIGHT_COLORS:
                cls.append('highlight-' + color)
            attr['_delete'] = highlight_key
        attr['value'] = ' '.join(cls)

        return {'class': attr}

    def _get_annotation_data_attr(self, index, el):  # pylint: disable=unused-argument
        """ Returns a dict in which the keys are the HTML data attributes
            to set on the annotation element. Each data attribute has a
            corresponding 'value' and (optional) '_delete' key to specify
            an XML attribute to delete.
        """

        data_attrs = {}
        attrs_map = {
            'body': 'data-comment-body',
            'title': 'data-comment-title',
            'problem': 'data-problem-id'
        }

        for xml_key in attrs_map.keys():  # pylint: disable=consider-iterating-dictionary
            if xml_key in el.attrib:
                value = el.get(xml_key, '')
                html_key = attrs_map[xml_key]
                data_attrs[html_key] = {'value': value, '_delete': xml_key}

        return data_attrs

    def _render_annotation(self, index, el):
        """ Renders an annotation element for HTML output.  """
        attr = {}
        attr.update(self._get_annotation_class_attr(index, el))
        attr.update(self._get_annotation_data_attr(index, el))

        el.tag = 'span'

        for key in attr.keys():  # pylint: disable=consider-iterating-dictionary
            el.set(key, attr[key]['value'])
            if '_delete' in attr[key] and attr[key]['_delete'] is not None:
                delete_key = attr[key]['_delete']
                del el.attrib[delete_key]

    def _render_content(self):
        """ Renders annotatable content with annotation spans and returns HTML. """

        xmltree = etree.fromstring(self.data)
        self._extract_instructions(xmltree)

        xmltree.tag = 'div'
        if 'display_name' in xmltree.attrib:
            del xmltree.attrib['display_name']

        index = 0
        for el in xmltree.findall('.//annotation'):
            self._render_annotation(index, el)
            index += 1

        return etree.tostring(xmltree, encoding='unicode')

    def _extract_instructions(self, xmltree):
        """ Removes <instructions> from the xmltree and returns them as a string, otherwise None. """
        instructions = xmltree.find('instructions')
        if instructions is not None:
            instructions.tag = 'div'
            xmltree.remove(instructions)
            return etree.tostring(instructions, encoding='unicode')
        return None

    def get_html(self):
        """ Renders parameters to template. """

        xmltree = etree.fromstring(self.data)
        instructions = self._extract_instructions(xmltree)

        context = {
            'display_name': self.display_name_with_default,
            'element_id': self.location.html_id(),
            'instructions_html': instructions,
            'content_html': self._render_content()
        }

        return self.runtime.service(self, 'mako').render_lms_template('annotatable.html', context)

    def student_view(self, context):  # pylint: disable=unused-argument
        """
        Renders the output that a student will see.
        """
        fragment = Fragment()
        fragment.add_content(self.get_html())
        add_css_to_fragment(fragment, 'AnnotatableBlockDisplay.css')
        add_webpack_js_to_fragment(fragment, 'AnnotatableBlockDisplay')
        shim_xmodule_js(fragment, 'Annotatable')

        return fragment

    def studio_view(self, _context):
        """
        Return the studio view.
        """
        fragment = Fragment(
            self.runtime.service(self, 'mako').render_cms_template(self.mako_template, self.get_context())
        )
        add_css_to_fragment(fragment, 'AnnotatableBlockEditor.css')
        add_webpack_js_to_fragment(fragment, 'AnnotatableBlockEditor')
        shim_xmodule_js(fragment, self.studio_js_module_name)
        return fragment


AnnotatableBlock = None


def reset_class():
    """Reset class as per django settings flag"""
    global AnnotatableBlock
    AnnotatableBlock = (
        _ExtractedAnnotatableBlock if settings.USE_EXTRACTED_ANNOTATABLE_BLOCK else _BuiltInAnnotatableBlock
    )
    return AnnotatableBlock


reset_class()
AnnotatableBlock.__name__ = "AnnotatableBlock"

if not settings.USE_EXTRACTED_ANNOTATABLE_BLOCK:
    warnings.warn(
        "The built-in `xmodule.annotatable_block` AnnotatableBlock implementation is deprecated. "
        "To fix this warning, enable `USE_EXTRACTED_ANNOTATABLE_BLOCK` (set it to True) to use "
        "`xblocks_contrib.annotatable.AnnotatableBlock` instead. "
        "Support for the built-in implementation, and the `USE_EXTRACTED_ANNOTATABLE_BLOCK` setting, "
        "will be removed in Willow.",
        DeprecationWarning,
        stacklevel=2,
    )
