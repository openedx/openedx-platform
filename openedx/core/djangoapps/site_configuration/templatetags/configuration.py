"""
Template tags and helper functions for displaying breadcrumbs in page titles
based on the current site.
"""


from django import template
from django.conf import settings
from django.templatetags.static import static

from lms.djangoapps.branding.api import get_favicon_url
from openedx.core.djangoapps.theming import helpers as theming_helpers
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangolib.markup import HTML

register = template.Library()  # pylint: disable=invalid-name


@register.simple_tag(name="page_title_breadcrumbs", takes_context=True)
def page_title_breadcrumbs_tag(context, *crumbs):  # pylint: disable=unused-argument
    """
    Django template that creates breadcrumbs for page titles:
    {% page_title_breadcrumbs "Specific" "Less Specific" General %}
    """
    return configuration_helpers.page_title_breadcrumbs(*crumbs)


@register.simple_tag(name="platform_name")
def platform_name():
    """
    Django template tag that outputs the current platform name:
    {% platform_name %}
    """
    return configuration_helpers.get_value('platform_name', settings.PLATFORM_NAME)


@register.simple_tag(name="favicon_path")
def favicon_path():
    """
    Django template tag that outputs the configured favicon:
    {% favicon_path %}
    """
    return get_favicon_url()


@register.simple_tag(name="microsite_css_overrides_file")
def microsite_css_overrides_file():
    """
    Django template tag that outputs the css import for a:
    {% microsite_css_overrides_file %}
    """
    file_path = configuration_helpers.get_value('css_overrides_file', None)
    if file_path is not None:
        return HTML("<link href='{}' rel='stylesheet' type='text/css'>").format(static(file_path))
    else:
        return ""


@register.filter
def microsite_template_path(template_name):
    """
    Django template filter to apply template overriding to microsites.
    The django_templates loader does not support the leading slash, therefore
    it is stripped before returning.
    """
    template_name = theming_helpers.get_template_path(template_name)
    return template_name[1:] if template_name[0] == '/' else template_name


@register.simple_tag
def google_analytics_4_id():
    """
    Django template tag that outputs the GOOGLE_ANALYTICS_4_ID:
    {% google_analytics_4_id %}
    """
    return configuration_helpers.get_value("GOOGLE_ANALYTICS_4_ID", settings.GOOGLE_ANALYTICS_4_ID)


@register.simple_tag
def google_tag_manager_id():
    """
    Django template tag that outputs the GOOGLE_TAG_MANAGER_ID:
    {% google_tag_manager_id %}
    """
    return configuration_helpers.get_value("GOOGLE_TAG_MANAGER_ID", settings.GOOGLE_TAG_MANAGER_ID)
