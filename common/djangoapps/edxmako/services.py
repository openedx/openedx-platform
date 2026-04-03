"""
Supports rendering an XBlock to HTML using mako templates.
"""
from django.template import engines
from django.template.utils import InvalidTemplateEngineError
from xblock.reference.plugins import Service

from common.djangoapps.edxmako import Engines
from common.djangoapps.edxmako.shortcuts import render_to_string

try:
    engines[Engines.PREVIEW]
except InvalidTemplateEngineError:
    # We are running in the CMS:
    lms_mako_namespace = "main"
    cms_mako_namespace = None
else:
    # We are running in the LMS:
    lms_mako_namespace = "lms.main"
    cms_mako_namespace = "main"


class MakoService(Service):
    """
    A service for rendering XBlocks to HTML using mako templates.

    Args:
        namespace_prefix(string): optional prefix to the mako namespace used to find the template file.
           e.g to access LMS templates from within Studio code, pass namespace_prefix='lms.'
    """
    def __init__(
        self,
        namespace_prefix='',
        **kwargs
    ):
        super().__init__(**kwargs)
        # Set the "default" namespace prefix, in case it's not specified when render_template() is called.
        self.namespace_prefix = namespace_prefix

    def render_template(self, template_file, dictionary, namespace='main'):
        """
        DEPRECATED. Takes (template_file, dictionary) and returns rendered HTML.

        Use render_lms_template or render_cms_template instead. Or better yet,
        don't use mako templates at all. React or django templates are much
        safer.
        """
        return render_to_string(template_file, dictionary, namespace=self.namespace_prefix + namespace)

    def render_lms_template(self, template_file, dictionary):
        """
        Render a template which is found in one of the LMS edx-platform template
        dirs. (lms.envs.common.MAKO_TEMPLATE_DIRS_BASE)

        Templates which are in these dirs will only work with this function:
            edx-platform/lms/templates/
            openedx/features/course_experience/templates
        """
        return render_to_string(template_file, dictionary, namespace=lms_mako_namespace)

    def render_cms_template(self, template_file, dictionary):
        """
        Render a template which is found in one of the CMS edx-platform template
        dirs. (cms.envs.common.MAKO_TEMPLATE_DIRS_BASE)

        Templates which are in these dirs will only work with this function:
            edx-platform/cms/templates/
            common/static/
            openedx/features/course_experience/templates
        """
        if cms_mako_namespace is None:
            raise RuntimeError("Cannot access CMS templates from the LMS")
        return render_to_string(template_file, dictionary, namespace=cms_mako_namespace)
