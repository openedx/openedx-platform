"""
Django template system engine for Mako templates.
"""


import logging

from django.template import TemplateDoesNotExist, TemplateSyntaxError
from django.template.backends.base import BaseEngine
from django.template.context import _builtin_context_processors
from django.utils.functional import cached_property
from django.utils.module_loading import import_string
from mako.exceptions import MakoException, TopLevelLookupException, text_error_template

from openedx.core.djangoapps.theming.helpers import get_template_path

from .paths import lookup_template
from .template import Template

LOGGER = logging.getLogger(__name__)


class Mako(BaseEngine):
    """
    A Mako template engine to be added to the ``TEMPLATES`` Django setting.
    """
    app_dirname = 'templates'

    def __init__(self, params):
        """
        Fetches template options, initializing BaseEngine properties,
        and assigning our Mako default settings.
        Note that OPTIONS contains backend-specific settings.
        :param params: This is simply the template dict you
                       define in your settings file.
        """
        params = params.copy()
        options = params.pop('OPTIONS').copy()
        super().__init__(params)
        self.context_processors = options.pop('context_processors', [])
        self.namespace = options.pop('namespace', 'main')

    def from_string(self, template_code):
        try:
            return Template(template_code)
        except MakoException:
            message = text_error_template().render()
            raise TemplateSyntaxError(message)  # pylint: disable=raise-missing-from  # noqa: B904

    def get_template(self, template_name):
        """
        Loads and returns a template for the given name.
        """
        template_name = get_template_path(template_name)
        try:
            return Template(lookup_template(self.namespace, template_name), engine=self)
        except TopLevelLookupException:
            raise TemplateDoesNotExist(template_name)  # pylint: disable=raise-missing-from  # noqa: B904

    @cached_property
    def template_context_processors(self):
        """
        Collect and cache the active context processors.
        """
        context_processors = _builtin_context_processors
        context_processors += tuple(self.context_processors)
        return tuple(import_string(path) for path in context_processors)
