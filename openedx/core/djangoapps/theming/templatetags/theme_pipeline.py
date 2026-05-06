"""
Theme aware pipeline template tags.
"""


from django import template
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe
from pipeline.templatetags.pipeline import JavascriptNode, StylesheetNode
from pipeline.utils import guess_type

from openedx.core.djangoapps.theming.helpers_static import get_static_file_url

register = template.Library()  # pylint: disable=invalid-name


class ThemeStylesheetNode(StylesheetNode):
    """
    Overrides StyleSheetNode from django pipeline so that stylesheets are served based on the applied theme.
    """
    def render_css(self, package, path):
        """
        Override render_css from django-pipline so that stylesheets urls are based on the applied theme
        """
        template_name = package.template_name or "pipeline/css.html"
        context = package.extra_context
        context.update({
            'type': guess_type(path, 'text/css'),
            'url': mark_safe(get_static_file_url(path))
        })
        return render_to_string(template_name, context)


class ThemeJavascriptNode(JavascriptNode):
    """
    Overrides JavascriptNode from django pipeline so that js files are served based on the applied theme.
    """
    def render_js(self, package, path):
        """
        Override render_js from django-pipline so that js file urls are based on the applied theme
        """
        template_name = package.template_name or "pipeline/js.html"
        context = package.extra_context
        context.update({
            'type': guess_type(path, 'text/javascript'),
            'url': mark_safe(get_static_file_url(path))
        })
        return render_to_string(template_name, context)


@register.tag
def stylesheet(parser, token):  # pylint: disable=unused-argument
    """
    Template tag to serve stylesheets from django-pipeline. This definition uses the theming aware ThemeStyleSheetNode.
    """
    try:
        _, name = token.split_contents()
    except ValueError:
        raise template.TemplateSyntaxError(  # pylint: disable=raise-missing-from  # noqa: B904
            '%r requires exactly one argument: the name of a group in the PIPELINE["STYLESHEETS"] setting' %  # noqa: UP031  # pylint: disable=line-too-long
            token.split_contents()[0]
        )
    return ThemeStylesheetNode(name)


@register.tag
def javascript(parser, token):  # pylint: disable=unused-argument
    """
    Template tag to serve javascript from django-pipeline. This definition uses the theming aware ThemeJavascriptNode.
    """
    try:
        _, name = token.split_contents()
    except ValueError:
        raise template.TemplateSyntaxError(  # pylint: disable=raise-missing-from  # noqa: B904
            '%r requires exactly one argument: the name of a group in the PIPELINE["JAVASCRIPT"] setting' %  # noqa: UP031  # pylint: disable=line-too-long
            token.split_contents()[0]
        )
    return ThemeJavascriptNode(name)
