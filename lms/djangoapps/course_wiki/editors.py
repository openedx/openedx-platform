"""
Support for using the CodeMirror code editor as a wiki content editor.
"""


from django import forms
from django.forms.utils import flatatt
from django.template.loader import render_to_string
from django.utils.encoding import force_str
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe
from wiki.editors.base import BaseEditor
from wiki.editors.markitup import MarkItUpAdminWidget


class CodeMirrorWidget(forms.Widget):
    """
    Use CodeMirror as a Django form widget (like a textarea).
    """
    def __init__(self, attrs=None):
        # The 'rows' and 'cols' attributes are required for HTML correctness.
        default_attrs = {
            'class': 'markItUp',
            'rows': '10',
            'cols': '40',
            'aria-describedby': 'hint_id_content'
        }
        if attrs:
            default_attrs.update(attrs)
        super().__init__(default_attrs)

    def render(self, name, value, attrs=None, renderer=None):
        if value is None:
            value = ''

        extra_attrs = attrs.copy()
        extra_attrs['name'] = name
        final_attrs = self.build_attrs(self.attrs, extra_attrs=extra_attrs)

        # TODO use the help_text field of edit form instead of rendering a template

        return render_to_string('wiki/includes/editor_widget.html',
                                {'attrs': mark_safe(flatatt(final_attrs)),
                                 'content': conditional_escape(force_str(value)),
                                 })


class CodeMirror(BaseEditor):
    """
    Wiki content editor using CodeMirror.
    """
    editor_id = 'codemirror'

    def get_admin_widget(self, instance=None):  # pylint: disable=arguments-differ, unused-argument
        return MarkItUpAdminWidget()

    def get_widget(self, instance=None):  # pylint: disable=unused-argument
        return CodeMirrorWidget()

    class AdminMedia:  # pylint: disable=missing-class-docstring
        css = {
            'all': ("wiki/markitup/skins/simple/style.css",
                    "wiki/markitup/sets/admin/style.css",)
        }
        js = ("wiki/markitup/admin.init.js",
              "wiki/markitup/jquery.markitup.js",
              "wiki/markitup/sets/admin/set.js",
              )

    class Media:
        css = {
            'all': ("js/vendor/CodeMirror/codemirror.css",)
        }
        js = ("js/vendor/CodeMirror/codemirror.js",
              "js/vendor/CodeMirror/addons/xml.js",
              "js/vendor/CodeMirror/addons/edx_markdown.js",
              "js/wiki/accessible.js",
              "js/wiki/CodeMirror.init.js",
              )
