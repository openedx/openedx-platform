"""
Add MathJax Markdown support

Source: https://github.com/mayoff/python-markdown-mathjax
"""
from xml.etree import ElementTree

import markdown
from markdown.util import AtomicString


class MathJaxPattern(markdown.inlinepatterns.Pattern):  # pylint: disable=missing-class-docstring

    def __init__(self):
        markdown.inlinepatterns.Pattern.__init__(self, r'(?<!\\)(\$\$?)(.+?)\2')

    def handleMatch(self, m):
        el = ElementTree.Element('span')
        el.text = AtomicString(m.group(2) + m.group(3) + m.group(2))
        return el


class MathJaxExtension(markdown.Extension):
    def extendMarkdown(self, md):  # pylint: disable=arguments-differ, unused-argument
        # Needs to come before escape matching because \ is pretty important in LaTeX
        md.inlinePatterns.register(MathJaxPattern(), 'mathjax', 180)


def makeExtension(**kwargs):
    return MathJaxExtension(**kwargs)
