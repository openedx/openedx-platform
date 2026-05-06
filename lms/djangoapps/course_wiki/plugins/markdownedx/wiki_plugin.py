# pylint: disable=missing-module-docstring
from wiki.core.plugins import registry as plugin_registry
from wiki.core.plugins.base import BasePlugin

from lms.djangoapps.course_wiki.plugins.markdownedx import mdx_mathjax, mdx_video


class ExtendMarkdownPlugin(BasePlugin):
    """
    This plugin simply loads all of the markdown extensions we use in edX.
    """

    markdown_extensions = [
        mdx_mathjax.MathJaxExtension(),
        mdx_video.VideoExtension(),
    ]

plugin_registry.register(ExtendMarkdownPlugin)
