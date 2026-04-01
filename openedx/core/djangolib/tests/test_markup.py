"""
Tests for openedx.core.djangolib.markup
"""

import unittest

import ddt
from bs4 import BeautifulSoup
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from mako.template import Template

from openedx.core.djangolib.markup import HTML, Text, strip_all_tags_but_br


@ddt.ddt
class FormatHtmlTest(unittest.TestCase):
    """Test that we can format plain strings and HTML into them properly."""

    @ddt.data(
        ("hello", "hello"),
        ("<hello>", "&lt;hello&gt;"),
        ("It's cool", "It&#39;s cool"),
        ('"cool," she said.', "&#34;cool,&#34; she said."),
        ("Stop & Shop", "Stop &amp; Shop"),
        ("<a>нтмℓ-єѕ¢αρє∂</a>", "&lt;a&gt;нтмℓ-єѕ¢αρє∂&lt;/a&gt;"),
    )
    def test_simple(self, before_after):
        """Verify that plain text is safely HTML-escaped."""
        (before, after) = before_after
        assert str(Text(_(before))) == after  # pylint: disable=translation-of-non-string
        assert str(Text(before)) == after

    def test_formatting(self):
        """Ensure Text.format correctly mixes escaped text with raw HTML."""
        # The whole point of this function is to make sure this works:
        out = Text(_("Point & click {start}here{end}!")).format(
            start=HTML("<a href='http://edx.org'>"),
            end=HTML("</a>"),
        )
        assert str(out) == "Point &amp; click <a href='http://edx.org'>here</a>!"

    def test_nested_formatting(self):
        """Validate nested formatting where HTML contains formatted text."""
        # Sometimes, you have plain text, with html inserted, and the html has
        # plain text inserted.  It gets twisty...
        out = Text(_("Send {start}email{end}")).format(
            start=HTML("<a href='mailto:{email}'>").format(email="A&B"),
            end=HTML("</a>"),
        )
        assert str(out) == "Send <a href='mailto:A&amp;B'>email</a>"

    def test_mako(self):
        """Confirm Mako templates format Text/HTML objects with expected filters."""
        # The default_filters used here have to match the ones in edxmako.
        template = Template(
            """
                <%!
                from django.utils.translation import gettext as _

                from openedx.core.djangolib.markup import HTML, Text
                %>
                ${Text(_(u"A & {BC}")).format(BC=HTML("B & C"))}
            """,
            default_filters=["decode.utf8", "h"],
        )
        out = template.render()
        assert out.strip() == "A &amp; B & C"

    def test_ungettext(self):
        """Check that ngettext output is properly formatted and HTML-escaped."""
        for i in [1, 2]:
            out = Text(ngettext("1 & {}", "2 & {}", i)).format(HTML("<>"))
            assert out == f"{i} &amp; <>"

    def test_strip_all_tags_but_br_filter(self):
        """Verify filter removes every tags except br"""
        template = Template(
            """
                <%page expression_filter="h"/>
                <%!
                from openedx.core.djangolib.markup import strip_all_tags_but_br
                %>
                ${" course <br> title <script>" | n, strip_all_tags_but_br}
            """
        )
        rendered_template = template.render()

        assert "<br>" in rendered_template
        assert "<script>" not in rendered_template

    def test_strip_all_tags_but_br_returns_html(self):
        """
        Verify filter returns HTML Markup safe string object
        """

        html = strip_all_tags_but_br("{name}<br><script>")
        html = html.format(name="Rock & Roll")
        assert str(html) == "Rock &amp; Roll<br>"

    def test_clean_dengers_html_filter(self):
        """Verify filter removes expected tags"""
        template = Template(
            """
                <%page expression_filter="h"/>
                <%!
                from openedx.core.djangolib.markup import clean_dangerous_html
                %>
                <%
                    html_content = '''
                        <html>
                            <head>
                                <script type="text/javascript" src="evil-site"></script>
                                <link rel="alternate" type="text/rss" src="evil-rss">
                                <style>
                                    body {
                                        background-image: url(javascript:do_evil)
                                    };
                                    div {
                                        color: expression(evil)
                                    };
                                </style>
                            </head>
                            <body onload="evil_function()">
                                <!-- I am interpreted for EVIL! -->
                                <a href="javascript:evil_function()">a link</a>
                                <a href="#" onclick="evil_function()">another link</a>
                                <p onclick="evil_function()">a paragraph</p>
                                <div style="display: none">secret EVIL!</div>
                                <object> of EVIL!</object>
                                <iframe src="evil-site"></iframe>
                                <form action="evil-site">
                                    Password: <input type="password" name="password">
                                </form>
                                <blink>annoying EVIL!</blink>
                                <a href="evil-site">spam spam SPAM!</a>
                                <image src="evil!">
                            </body>
                        </html>
                    '''
                %>
                ${html_content | n, clean_dangerous_html}
            """
        )
        rendered_template = template.render()
        html_soup = BeautifulSoup(rendered_template, "html.parser")

        assert html_soup.find("a")
        assert html_soup.find("div")
        assert html_soup.find("div", attrs={"style": "display: none"})
        assert html_soup.find("p")
        assert html_soup.find("img")

        assert not html_soup.find("a", attrs={"onclick": "evil_function()"})
        assert not html_soup.find("html")
        assert not html_soup.find("head")
        assert not html_soup.find("script")
        assert not html_soup.find("style")
        assert not html_soup.find("link")
        assert not html_soup.find("iframe")
        assert not html_soup.find("form")
        assert not html_soup.find("blink")
        assert not html_soup.find("object")
