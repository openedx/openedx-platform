"""
Views for building plugins.
"""


import logging

from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.http import HttpResponse
from django.shortcuts import render
from web_fragments.views import FragmentView

from common.djangoapps.edxmako.shortcuts import is_any_marketing_link_set, is_marketing_link_set, marketing_link

log = logging.getLogger('plugin_api')


class EdxFragmentView(FragmentView):  # pylint: disable=abstract-method
    """
    The base class of all Open edX fragment views.
    """
    page_title = None

    @staticmethod
    def get_css_dependencies(group):
        """
        Returns list of CSS dependencies belonging to `group` in settings.PIPELINE['JAVASCRIPT'].

        Respects `PIPELINE['PIPELINE_ENABLED']` setting.
        """
        if settings.PIPELINE['PIPELINE_ENABLED']:
            return [settings.PIPELINE['STYLESHEETS'][group]['output_filename']]
        else:
            return settings.PIPELINE['STYLESHEETS'][group]['source_filenames']

    @staticmethod
    def get_js_dependencies(group):
        """
        Returns list of JS dependencies belonging to `group` in settings.PIPELINE['JAVASCRIPT'].

        Respects `PIPELINE['PIPELINE_ENABLED']` setting.
        """
        if settings.PIPELINE['PIPELINE_ENABLED']:
            return [settings.PIPELINE['JAVASCRIPT'][group]['output_filename']]
        else:
            return settings.PIPELINE['JAVASCRIPT'][group]['source_filenames']

    def vendor_js_dependencies(self):
        """
        Returns list of the vendor JS files that this view depends on.
        """
        return []

    def js_dependencies(self):
        """
        Returns list of the JavaScript files that this view depends on.
        """
        return []

    def css_dependencies(self):
        """
        Returns list of the CSS files that this view depends on.
        """
        return []

    def add_fragment_resource_urls(self, fragment):
        """
        Adds URLs for JS and CSS resources needed by this fragment.
        """
        # Head dependencies
        for vendor_js_file in self.vendor_js_dependencies():
            fragment.add_resource_url(staticfiles_storage.url(vendor_js_file), 'application/javascript', 'head')

        for css_file in self.css_dependencies():
            fragment.add_css_url(staticfiles_storage.url(css_file))

        # Body dependencies
        for js_file in self.js_dependencies():
            fragment.add_javascript_url(staticfiles_storage.url(js_file))

    def create_base_standalone_context(self, request, fragment, **kwargs):  # pylint: disable=unused-argument
        """
        Creates the base context for rendering a fragment as a standalone page.
        """
        return {
            'uses_bootstrap': True,
            'disable_accordion': True,
            'allow_iframing': True,
            'disable_header': True,
            'disable_footer': True,
            'disable_window_wrap': True,
        }

    def _add_studio_standalone_context_variables(self, request, context):
        """
        Adds Studio-specific context variables for fragment standalone pages.

        Note: this is meant to be a temporary hack to ensure that Studio
        receives the context variables that are expected by some of its
        shared templates. Ideally these templates shouldn't depend upon
        this data being provided but should instead import the functionality
        it needs.
        """
        context.update({
            'request': request,
            'settings': settings,
            'EDX_ROOT_URL': settings.EDX_ROOT_URL,
            'marketing_link': marketing_link,
            'is_any_marketing_link_set': is_any_marketing_link_set,
            'is_marketing_link_set': is_marketing_link_set,
        })

    def standalone_page_title(self, request, fragment, **kwargs):  # pylint: disable=unused-argument
        """
        Returns the page title for the standalone page, or None if there is no title.
        """
        return None

    def render_standalone_response(self, request, fragment, **kwargs):
        """
        Renders a standalone page for the specified fragment.

        Note: if fragment is None, a 204 response will be returned (no content).
        """
        if fragment is None:
            return HttpResponse(status=204)
        context = self.create_base_standalone_context(request, fragment, **kwargs)
        self._add_studio_standalone_context_variables(request, context)
        context.update({
            'settings': settings,
            'fragment': fragment,
            'page_title': self.standalone_page_title(request, fragment, **kwargs),
        })
        template_name = 'fragments/standalone-page-bootstrap.html'

        return render(
            request=request,
            template_name=template_name,
            context=context
        )
