"""
Tests for linters.py
"""


import textwrap
from unittest import TestCase

from ddt import data, ddt

from xsslint.linters import (
    JavaScriptLinter, MakoTemplateLinter,
    PythonLinter, UnderscoreTemplateLinter,
    DjangoTemplateLinter
)
from xsslint.reporting import FileResults
from xsslint.utils import ParseString

UNDERSCORE_LINTER_RULESET = UnderscoreTemplateLinter.ruleset
JAVASCRIPT_LINTER_RULESET = JavaScriptLinter.ruleset + UnderscoreTemplateLinter.ruleset
PYTHON_LINTER_RULESET = PythonLinter.ruleset
MAKO_LINTER_RULESET = (
    MakoTemplateLinter.ruleset + PythonLinter.ruleset + JavaScriptLinter.ruleset + UnderscoreTemplateLinter.ruleset
)
DJANGO_LINTER_RULESET = (
    DjangoTemplateLinter.ruleset
)


def _build_javascript_linter():
    return JavaScriptLinter(
        underscore_linter=UnderscoreTemplateLinter()
    )


def _build_mako_linter():
    return MakoTemplateLinter(
        javascript_linter=_build_javascript_linter(),
        python_linter=PythonLinter(),
    )


def _build_django_linter():
    return DjangoTemplateLinter()


class TestLinter(TestCase):
    """
    Test Linter base class
    """
    def _validate_data_rules(self, data, results):
        """
        Validates that the appropriate rule violations were triggered.

        Arguments:
            data: A dict containing the 'rule' (or rules) to be tests.
            results: The results, containing violations to be validated.

        """
        rules = []
        if isinstance(data['rule'], list):
            rules = data['rule']
        elif data['rule'] is not None:
            rules.append(data['rule'])
        results.violations.sort(key=lambda violation: violation.sort_key())

        # Print violations if the lengths are different.
        if len(results.violations) != len(rules):
            for violation in results.violations:
                print(f"Found violation: {violation.rule}")

        assert len(results.violations) == len(rules)
        for violation, rule in zip(results.violations, rules):
            assert violation.rule == rule


@ddt
class TestUnderscoreTemplateLinter(TestLinter):
    """
    Test UnderscoreTemplateLinter
    """

    def test_check_underscore_file_is_safe(self):
        """
        Test check_underscore_file_is_safe with safe template
        """
        linter = UnderscoreTemplateLinter()
        results = FileResults('')

        template = textwrap.dedent("""
            <%- gettext('Single Line') %>

            <%-
                gettext('Multiple Lines')
            %>
        """)

        linter.check_underscore_file_is_safe(template, results)

        assert len(results.violations) == 0

    def test_check_underscore_file_is_not_safe(self):
        """
        Test check_underscore_file_is_safe with unsafe template
        """
        linter = UnderscoreTemplateLinter()
        results = FileResults('')

        template = textwrap.dedent("""
            <%= gettext('Single Line') %>

            <%=
                gettext('Multiple Lines')
            %>
        """)

        linter.check_underscore_file_is_safe(template, results)

        assert len(results.violations) == 2
        assert results.violations[0].rule == UNDERSCORE_LINTER_RULESET.underscore_not_escaped
        assert results.violations[1].rule == UNDERSCORE_LINTER_RULESET.underscore_not_escaped

    @data(
        {
            'template':
                '<% // xss-lint:   disable=underscore-not-escaped   %>\n'
                '<%= message %>',
            'is_disabled': [True],
        },
        {
            'template':
                '<% // xss-lint: disable=another-rule,underscore-not-escaped %>\n'
                '<%= message %>',
            'is_disabled': [True],
        },
        {
            'template':
                '<% // xss-lint: disable=another-rule %>\n'
                '<%= message %>',
            'is_disabled': [False],
        },
        {
            'template':
                '<% // xss-lint: disable=underscore-not-escaped %>\n'
                '<%= message %>\n'
                '<%= message %>',
            'is_disabled': [True, False],
        },
        {
            'template':
                '// This test does not use proper Underscore.js Template syntax\n'
                '// But, it is just testing that a maximum of 5 non-whitespace\n'
                '// are used to designate start of line for disabling the next line.\n'
                ' 1 2 3 4 5 xss-lint: disable=underscore-not-escaped %>\n'
                '<%= message %>\n'
                ' 1 2 3 4 5 6 xss-lint: disable=underscore-not-escaped %>\n'
                '<%= message %>',
            'is_disabled': [True, False],
        },
        {
            'template':
                '<%= message %><% // xss-lint: disable=underscore-not-escaped %>\n'
                '<%= message %>',
            'is_disabled': [True, False],
        },
        {
            'template':
                '<%= message %>\n'
                '<% // xss-lint: disable=underscore-not-escaped %>',
            'is_disabled': [False],
        },
    )
    def test_check_underscore_file_disable_rule(self, data):
        """
        Test check_underscore_file_is_safe with various disabled pragmas
        """
        linter = UnderscoreTemplateLinter()
        results = FileResults('')

        linter.check_underscore_file_is_safe(data['template'], results)

        violation_count = len(data['is_disabled'])
        assert len(results.violations) == violation_count
        for index in range(0, violation_count - 1):
            assert results.violations[index].is_disabled == data['is_disabled'][index]

    def test_check_underscore_file_disables_one_violation(self):
        """
        Test check_underscore_file_is_safe with disabled before a line only
        disables for the violation following
        """
        linter = UnderscoreTemplateLinter()
        results = FileResults('')

        template = textwrap.dedent("""
            <% // xss-lint: disable=underscore-not-escaped %>
            <%= message %>
            <%= message %>
        """)

        linter.check_underscore_file_is_safe(template, results)

        assert len(results.violations) == 2
        assert results.violations[0].is_disabled is True
        assert results.violations[1].is_disabled is False

    @data(
        {'template': '<%= edx.HtmlUtils.ensureHtml(message) %>'},
        {'template': '<%= HtmlUtils.ensureHtml(message) %>'},
        {'template': '<%= _.escape(message) %>'},
    )
    def test_check_underscore_no_escape_allowed(self, data):
        """
        Test check_underscore_file_is_safe with expressions that are allowed
        without escaping because the internal calls properly escape.
        """
        linter = UnderscoreTemplateLinter()
        results = FileResults('')

        linter.check_underscore_file_is_safe(data['template'], results)

        assert len(results.violations) == 0


@ddt
class TestJavaScriptLinter(TestLinter):
    """
    Test JavaScriptLinter
    """

    @data(
        {'template': 'var m = "Plain text " + message + "plain text"', 'rule': None},
        {'template': 'var m = "檌檒濦 " + message + "plain text"', 'rule': None},
        {
            'template':
                ("""$email_header.append($('<input>', type: "button", name: "copy-email-body-text","""
                 """ value: gettext("Copy Email To Editor"), id: 'copy_email_' + email_id))"""),
            'rule': None
        },
        {'template': 'var m = "<p>" + message + "</p>"', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_concat_html},
        {
            'template': r'var m = "<p>\"escaped quote\"" + message + "\"escaped quote\"</p>"',
            'rule': JAVASCRIPT_LINTER_RULESET.javascript_concat_html
        },
        {'template': '  // var m = "<p>" + commentedOutMessage + "</p>"', 'rule': None},
        {'template': 'var m = " <p> " + message + " </p> "', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_concat_html},
        {'template': 'var m = " <p> " + message + " broken string', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_concat_html},
    )
    def test_concat_with_html(self, data):
        """
        Test check_javascript_file_is_safe with concatenating strings and HTML
        """
        linter = _build_javascript_linter()
        results = FileResults('')

        linter.check_javascript_file_is_safe(data['template'], results)
        self._validate_data_rules(data, results)

    @data(
        {'template': 'test.append( test.render().el )', 'rule': None},
        {'template': 'test.append(test.render().el)', 'rule': None},
        {'template': 'test.append(test.render().$el)', 'rule': None},
        {'template': 'test.append(testEl)', 'rule': None},
        {'template': 'test.append($test)', 'rule': None},
        # plain text is ok because any & will be escaped, and it stops false
        # negatives on some other objects with an append() method
        {'template': 'test.append("plain text")', 'rule': None},
        {'template': 'test.append("<div/>")', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_append},
        {'template': 'graph.svg.append("g")', 'rule': None},
        {'template': 'test.append( $( "<div>" ) )', 'rule': None},
        {'template': 'test.append($("<div>"))', 'rule': None},
        {'template': 'test.append($("<div/>"))', 'rule': None},
        {'template': 'test.append(HtmlUtils.ensureHtml(htmlSnippet).toString())', 'rule': None},
        {'template': 'HtmlUtils.append($el, someHtml)', 'rule': None},
        {'template': 'test.append("fail on concat" + test.render().el)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_append},
        {'template': 'test.append("fail on concat" + testEl)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_append},
        {'template': 'test.append(message)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_append},
    )
    def test_jquery_append(self, data):
        """
        Test check_javascript_file_is_safe with JQuery append()
        """
        linter = _build_javascript_linter()
        results = FileResults('')

        linter.check_javascript_file_is_safe(data['template'], results)

        self._validate_data_rules(data, results)

    @data(
        {'template': 'test.prepend( test.render().el )', 'rule': None},
        {'template': 'test.prepend(test.render().el)', 'rule': None},
        {'template': 'test.prepend(test.render().$el)', 'rule': None},
        {'template': 'test.prepend(testEl)', 'rule': None},
        {'template': 'test.prepend($test)', 'rule': None},
        {'template': 'test.prepend("text")', 'rule': None},
        {'template': 'test.prepend( $( "<div>" ) )', 'rule': None},
        {'template': 'test.prepend($("<div>"))', 'rule': None},
        {'template': 'test.prepend($("<div/>"))', 'rule': None},
        {'template': 'test.prepend(HtmlUtils.ensureHtml(htmlSnippet).toString())', 'rule': None},
        {'template': 'HtmlUtils.prepend($el, someHtml)', 'rule': None},
        {'template': 'test.prepend("broken string)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_prepend},
        {'template': 'test.prepend("fail on concat" + test.render().el)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_prepend},
        {'template': 'test.prepend("fail on concat" + testEl)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_prepend},
        {'template': 'test.prepend(message)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_prepend},
    )
    def test_jquery_prepend(self, data):
        """
        Test check_javascript_file_is_safe with JQuery prepend()
        """
        linter = _build_javascript_linter()
        results = FileResults('')

        linter.check_javascript_file_is_safe(data['template'], results)

        self._validate_data_rules(data, results)

    @data(
        {'template': 'test.unwrap(HtmlUtils.ensureHtml(htmlSnippet).toString())', 'rule': None},
        {'template': 'test.wrap(HtmlUtils.ensureHtml(htmlSnippet).toString())', 'rule': None},
        {'template': 'test.wrapAll(HtmlUtils.ensureHtml(htmlSnippet).toString())', 'rule': None},
        {'template': 'test.wrapInner(HtmlUtils.ensureHtml(htmlSnippet).toString())', 'rule': None},
        {'template': 'test.after(HtmlUtils.ensureHtml(htmlSnippet).toString())', 'rule': None},
        {'template': 'test.before(HtmlUtils.ensureHtml(htmlSnippet).toString())', 'rule': None},
        {'template': 'test.replaceAll(HtmlUtils.ensureHtml(htmlSnippet).toString())', 'rule': None},
        {'template': 'test.replaceWith(HtmlUtils.ensureHtml(htmlSnippet).toString())', 'rule': None},
        {'template': 'test.replaceWith(edx.HtmlUtils.HTML(htmlString).toString())', 'rule': None},
        {'template': 'test.unwrap(anything)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_insertion},
        {'template': 'test.wrap(anything)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_insertion},
        {'template': 'test.wrapAll(anything)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_insertion},
        {'template': 'test.wrapInner(anything)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_insertion},
        {'template': 'test.after(anything)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_insertion},
        {'template': 'test.before(anything)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_insertion},
        {'template': 'test.replaceAll(anything)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_insertion},
        {'template': 'test.replaceWith(anything)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_insertion},
    )
    def test_jquery_insertion(self, data):
        """
        Test check_javascript_file_is_safe with JQuery insertion functions
        other than append(), prepend() and html() that take content as an
        argument (e.g. before(), after()).
        """
        linter = _build_javascript_linter()
        results = FileResults('')

        linter.check_javascript_file_is_safe(data['template'], results)

        self._validate_data_rules(data, results)

    @data(
        {'template': '  element.parentNode.appendTo(target);', 'rule': None},
        {'template': '  test.render().el.appendTo(target);', 'rule': None},
        {'template': '  test.render().$el.appendTo(target);', 'rule': None},
        {'template': '  test.$element.appendTo(target);', 'rule': None},
        {'template': '  test.testEl.appendTo(target);', 'rule': None},
        {'template': '$element.appendTo(target);', 'rule': None},
        {'template': 'el.appendTo(target);', 'rule': None},
        {'template': 'testEl.appendTo(target);', 'rule': None},
        {'template': 'testEl.prependTo(target);', 'rule': None},
        {'template': 'testEl.insertAfter(target);', 'rule': None},
        {'template': 'testEl.insertBefore(target);', 'rule': None},
        {'template': 'anycall().appendTo(target)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_insert_into_target},
        {'template': 'anything.appendTo(target)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_insert_into_target},
        {'template': 'anything.prependTo(target)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_insert_into_target},
        {'template': 'anything.insertAfter(target)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_insert_into_target},
        {'template': 'anything.insertBefore(target)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_insert_into_target},
    )
    def test_jquery_insert_to_target(self, data):
        """
        Test check_javascript_file_is_safe with JQuery insert to target
        functions that take a target as an argument, like appendTo() and
        prependTo().
        """
        linter = _build_javascript_linter()
        results = FileResults('')

        linter.check_javascript_file_is_safe(data['template'], results)

        self._validate_data_rules(data, results)

    @data(
        {'template': 'test.html()', 'rule': None},
        {'template': 'test.html( )', 'rule': None},
        {'template': "test.html( '' )", 'rule': None},
        {'template': "test.html('')", 'rule': None},
        {'template': 'test.html("")', 'rule': None},
        {'template': 'test.html(HtmlUtils.ensureHtml(htmlSnippet).toString())', 'rule': None},
        {'template': 'HtmlUtils.setHtml($el, someHtml)', 'rule': None},
        {'template': 'test.html("any string")', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_html},
        {'template': 'test.html("broken string)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_html},
        {'template': 'test.html("檌檒濦")', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_html},
        {'template': 'test.html(anything)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_jquery_html},
    )
    def test_jquery_html(self, data):
        """
        Test check_javascript_file_is_safe with JQuery html()
        """
        linter = _build_javascript_linter()
        results = FileResults('')

        linter.check_javascript_file_is_safe(data['template'], results)
        self._validate_data_rules(data, results)

    @data(
        {'template': '_.escape(message)', 'rule': None},
        {'template': 'anything.escape(message)', 'rule': None},
        {'template': 'anythingescape(message)', 'rule': None},
        {'template': '$escape(message)', 'rule': None},
        {'template': '_escape(message)', 'rule': None},
        {'template': 'escape(message)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_escape},
        {'template': '(escape(message))', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_escape},
        {'template': '  escape(message))', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_escape},
        {'template': 'window.escape(message)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_escape},
        {'template': '(window.escape(message)', 'rule': JAVASCRIPT_LINTER_RULESET.javascript_escape},
    )
    def test_javascript_escape(self, data):
        """
        Test check_javascript_file_is_safe with interpolate()
        """
        linter = _build_javascript_linter()
        results = FileResults('')

        linter.check_javascript_file_is_safe(data['template'], results)

        self._validate_data_rules(data, results)


@ddt
class TestPythonLinter(TestLinter):
    """
    Test PythonLinter
    """

    @data(
        {'template': 'm = "Plain text " + message + "plain text"', 'rule': None},
        {'template': 'm = "檌檒濦 " + message + "plain text"', 'rule': None},
        {'template': '  # m = "<p>" + commentedOutMessage + "</p>"', 'rule': None},
        {'template': 'm = "<p>" + message + "</p>"', 'rule': [PYTHON_LINTER_RULESET.python_concat_html, PYTHON_LINTER_RULESET.python_concat_html]},
        {'template': 'm = " <p> " + message + " </p> "', 'rule': [PYTHON_LINTER_RULESET.python_concat_html, PYTHON_LINTER_RULESET.python_concat_html]},
        {'template': 'm = " <p> " + message + " broken string', 'rule': PYTHON_LINTER_RULESET.python_parse_error},
    )
    def test_concat_with_html(self, data):
        """
        Test check_python_file_is_safe with concatenating strings and HTML
        """
        linter = PythonLinter()
        results = FileResults('')

        linter.check_python_file_is_safe(data['template'], results)

        self._validate_data_rules(data, results)

    def test_check_python_expression_display_name(self):
        """
        Test _check_python_file_is_safe with display_name_with_default_escaped
        fails.
        """
        linter = PythonLinter()
        results = FileResults('')

        python_file = textwrap.dedent("""
            context = {
                'display_name': self.display_name_with_default_escaped,
            }
        """)

        linter.check_python_file_is_safe(python_file, results)

        assert len(results.violations) == 1
        assert results.violations[0].rule == PYTHON_LINTER_RULESET.python_deprecated_display_name

    def test_check_custom_escaping(self):
        """
        Test _check_python_file_is_safe fails when custom escapins is used.
        """
        linter = PythonLinter()
        results = FileResults('')

        python_file = textwrap.dedent("""
            msg = mmlans.replace('<', '&lt;')
        """)

        linter.check_python_file_is_safe(python_file, results)

        assert len(results.violations) == 1
        assert results.violations[0].rule == PYTHON_LINTER_RULESET.python_custom_escape

    @data(
        {
            'python':
                textwrap.dedent("""
                    msg = Text("Mixed {span_start}text{span_end}").format(
                        span_start=HTML("<span>"),
                        span_end=HTML("</span>"),
                    )
                """),
            'rule': None
        },
        {
            'python':
                textwrap.dedent("""
                    msg = "Mixed {span_start}text{span_end}".format(
                        span_start=HTML("<span>"),
                        span_end=HTML("</span>"),
                    )
                """),
            'rule': PYTHON_LINTER_RULESET.python_requires_html_or_text
        },
        {
            'python':
                textwrap.dedent("""
                    msg = "Mixed {span_start}{text}{span_end}".format(
                        span_start=HTML("<span>"),
                        text=Text("This should still break."),
                        span_end=HTML("</span>"),
                    )
                """),
            'rule': PYTHON_LINTER_RULESET.python_requires_html_or_text
        },
        {
            'python':
                textwrap.dedent("""
                    msg = Text("Mixed {link_start}text{link_end}".format(
                        link_start=HTML("<a href='{}'>").format(url),
                        link_end=HTML("</a>"),
                    ))
                """),
            'rule': [PYTHON_LINTER_RULESET.python_close_before_format, PYTHON_LINTER_RULESET.python_requires_html_or_text]
        },
        {
            'python':
                textwrap.dedent("""
                    msg = Text("Mixed {link_start}text{link_end}").format(
                        link_start=HTML("<a href='{}'>".format(url)),
                        link_end=HTML("</a>"),
                    )
                """),
            'rule': PYTHON_LINTER_RULESET.python_close_before_format
        },
        {
            'python':
                textwrap.dedent("""
                    msg = Text("Mixed {link_start}text{link_end}".format(
                        link_start=HTML("<a href='{}'>".format(HTML('<b>'))),
                        link_end=HTML("</a>"),
                    ))
                """),
            'rule':
                [
                    PYTHON_LINTER_RULESET.python_close_before_format,
                    PYTHON_LINTER_RULESET.python_requires_html_or_text,
                    PYTHON_LINTER_RULESET.python_close_before_format,
                    PYTHON_LINTER_RULESET.python_requires_html_or_text
                ]
        },
        {
            'python':
                textwrap.dedent("""
                    msg = "Mixed {span_start}text{span_end}".format(
                        span_start="<span>",
                        span_end="</span>",
                    )
                """),
            'rule': [PYTHON_LINTER_RULESET.python_wrap_html, PYTHON_LINTER_RULESET.python_wrap_html]
        },
        {
            'python':
                textwrap.dedent("""
                    msg = Text(_("String with multiple lines "
                        "{link_start}unenroll{link_end} "
                        "and final line")).format(
                            link_start=HTML(
                                '<a id="link__over_multiple_lines" '
                                'data-course-id="{course_id}" '
                                'href="#test-modal">'
                            ).format(
                                # Line comment with ' to throw off parser
                                course_id=course_overview.id
                            ),
                            link_end=HTML('</a>'),
                    )
                """),
            'rule': None
        },
        {
            'python': "msg = '<span></span>'",
            'rule': None
        },
        {
            'python': "msg = HTML('<span></span>')",
            'rule': None
        },
        {
            'python': r"""msg = '<a href="{}"'.format(url)""",
            'rule': PYTHON_LINTER_RULESET.python_wrap_html
        },
        {
            'python': r"""msg = '{}</p>'.format(message)""",
            'rule': PYTHON_LINTER_RULESET.python_wrap_html
        },
        {
            'python': r"""r'regex with {} and named group(?P<id>\d+)?$'.format(test)""",
            'rule': None
        },
        {
            'python': r"""msg = '<a href="%s"' % url""",
            'rule': PYTHON_LINTER_RULESET.python_interpolate_html
        },
        {
            'python':
                textwrap.dedent("""
                    def __repr__(self):
                        # Assume repr implementations are safe, and not HTML
                        return '<CCXCon {}>'.format(self.title)
                """),
            'rule': None
        },
        {
            'python': r"""msg = '%s</p>' % message""",
            'rule': PYTHON_LINTER_RULESET.python_interpolate_html
        },
        {
            'python': "msg = HTML('<span></span>'",
            'rule': PYTHON_LINTER_RULESET.python_parse_error
        },
    )
    def test_check_python_with_text_and_html(self, data):
        """
        Test _check_python_file_is_safe tests for proper use of Text() and
        Html().

        """
        linter = PythonLinter()
        results = FileResults('')

        file_content = textwrap.dedent(data['python'])

        linter.check_python_file_is_safe(file_content, results)

        self._validate_data_rules(data, results)

    def test_check_python_with_text_and_html_mixed(self):
        """
        Test _check_python_file_is_safe tests for proper use of Text() and
        Html() for a Python file with a mix of rules.

        """
        linter = PythonLinter()
        results = FileResults('')

        file_content = textwrap.dedent("""
            msg1 = '<a href="{}"'.format(url)
            msg2 = "Mixed {link_start}text{link_end}".format(
                link_start=HTML("<a href='{}'>".format(url)),
                link_end="</a>",
            )
            msg3 = '<a href="%s"' % url
        """)

        linter.check_python_file_is_safe(file_content, results)

        results.violations.sort(key=lambda violation: violation.sort_key())

        assert len(results.violations) == 5
        assert results.violations[0].rule == PYTHON_LINTER_RULESET.python_wrap_html
        assert results.violations[1].rule == PYTHON_LINTER_RULESET.python_requires_html_or_text
        assert results.violations[2].rule == PYTHON_LINTER_RULESET.python_close_before_format
        assert results.violations[3].rule == PYTHON_LINTER_RULESET.python_wrap_html
        assert results.violations[4].rule == PYTHON_LINTER_RULESET.python_interpolate_html

    @data(
        {
            'python':
                """
                    response_str = textwrap.dedent('''
                        <div>
                            <h3 class="result">{response}</h3>
                        </div>
                    ''').format(response=response_text)
                """,
            'rule': PYTHON_LINTER_RULESET.python_wrap_html,
            'start_line': 2,
        },
        {
            'python':
                """
                def function(self):
                    '''
                    Function comment.
                    '''
                    response_str = textwrap.dedent('''
                        <div>
                            <h3 class="result">{response}</h3>
                        </div>
                    ''').format(response=response_text)
                """,
            'rule': PYTHON_LINTER_RULESET.python_wrap_html,
            'start_line': 6,
        },
        {
            'python':
                """
                def function(self):
                    '''
                    Function comment.
                    '''
                    response_str = '''<h3 class="result">{response}</h3>'''.format(response=response_text)
                """,
            'rule': PYTHON_LINTER_RULESET.python_wrap_html,
            'start_line': 6,
        },
        {
            'python':
                """
                def function(self):
                    '''
                    Function comment.
                    '''
                    response_str = textwrap.dedent('''
                        <div>
                            \"\"\" Do we care about a nested triple quote? \"\"\"
                            <h3 class="result">{response}</h3>
                        </div>
                    ''').format(response=response_text)
                """,
            'rule': PYTHON_LINTER_RULESET.python_wrap_html,
            'start_line': 6,
        },
    )
    def test_check_python_with_triple_quotes(self, data):
        """
        Test _check_python_file_is_safe with triple quotes.

        """
        linter = PythonLinter()
        results = FileResults('')

        file_content = textwrap.dedent(data['python'])

        linter.check_python_file_is_safe(file_content, results)

        self._validate_data_rules(data, results)
        assert results.violations[0].start_line == data['start_line']


@ddt
class TestMakoTemplateLinter(TestLinter):
    """
    Test MakoTemplateLinter
    """

    @data(
        {'directory': 'lms/templates', 'expected': True},
        {'directory': 'lms/templates/support', 'expected': True},
        {'directory': 'lms/templates/support', 'expected': True},
        {'directory': 'test_root/staticfiles/templates', 'expected': False},
        {'directory': './test_root/staticfiles/templates', 'expected': False},
        {'directory': './some/random/path', 'expected': False},
    )
    def test_is_valid_directory(self, data):
        """
        Test _is_valid_directory correctly determines mako directories
        """
        linter = _build_mako_linter()
        linter._skip_mako_dirs = ('test_root',)

        assert linter._is_valid_directory(data['directory']) == data['expected']

    @data(
        {
            'template': '\n <%page expression_filter="h"/>',
            'rule': None
        },
        {
            'template':
                '\n <%page args="section_data" expression_filter="h" /> ',
            'rule': None
        },
        {
            'template': '\n ## <%page expression_filter="h"/>',
            'rule': MAKO_LINTER_RULESET.mako_missing_default
        },
        {
            'template':
                '\n <%page expression_filter="h" /> '
                '\n <%page args="section_data"/>',
            'rule': MAKO_LINTER_RULESET.mako_multiple_page_tags
        },
        {
            'template':
                '\n <%page expression_filter="h" /> '
                '\n ## <%page args="section_data"/>',
            'rule': None
        },
        {
            'template': '\n <%page args="section_data" /> ',
            'rule': MAKO_LINTER_RULESET.mako_missing_default
        },
        {
            'template':
                '\n <%page args="section_data"/> <some-other-tag expression_filter="h" /> ',
            'rule': MAKO_LINTER_RULESET.mako_missing_default
        },
        {
            'template': '\n',
            'rule': MAKO_LINTER_RULESET.mako_missing_default
        },
    )
    def test_check_page_default(self, data):
        """
        Test _check_mako_file_is_safe with different page defaults
        """
        linter = _build_mako_linter()
        results = FileResults('')

        linter._check_mako_file_is_safe(data['template'], results)

        num_violations = 0 if data['rule'] is None else 1
        assert len(results.violations) == num_violations
        if num_violations > 0:
            assert results.violations[0].rule == data['rule']

    @data(
        {'expression': '${x}', 'rule': None},
        {'expression': '${{unbalanced}', 'rule': MAKO_LINTER_RULESET.mako_unparseable_expression},
        {'expression': '${x | n}', 'rule': MAKO_LINTER_RULESET.mako_invalid_html_filter},
        {'expression': '${x | n, decode.utf8}', 'rule': None},
        {'expression': '${x | h}', 'rule': MAKO_LINTER_RULESET.mako_unwanted_html_filter},
        {'expression': '  ## ${commented_out | h}', 'rule': None},
        {'expression': '${x | n, dump_js_escaped_json}', 'rule': MAKO_LINTER_RULESET.mako_invalid_html_filter},
    )
    def test_check_mako_expressions_in_html(self, data):
        """
        Test _check_mako_file_is_safe in html context provides appropriate violations
        """
        linter = _build_mako_linter()
        results = FileResults('')

        mako_template = textwrap.dedent("""
            <%page expression_filter="h"/>
            {expression}
        """.format(expression=data['expression']))

        linter._check_mako_file_is_safe(mako_template, results)

        self._validate_data_rules(data, results)

    def test_check_mako_expression_display_name(self):
        """
        Test _check_mako_file_is_safe with display_name_with_default_escaped
        fails.
        """
        linter = _build_mako_linter()
        results = FileResults('')

        mako_template = textwrap.dedent("""
            <%page expression_filter="h"/>
            ${course.display_name_with_default_escaped}
        """)

        linter._check_mako_file_is_safe(mako_template, results)

        assert len(results.violations) == 1
        assert results.violations[0].rule == MAKO_LINTER_RULESET.python_deprecated_display_name

    @data(
        {
            # Python blocks between <% ... %> use the same Python linting as
            # Mako expressions between ${ ... }. This single test verifies
            # that these blocks are linted. The individual linting rules are
            # tested in the Mako expression tests that follow.
            'expression':
                textwrap.dedent("""
                    <%
                        a_link_start = '<a class="link-courseURL" rel="external" href="'
                        a_link_end = '">' + _("your course summary page") + '</a>'
                        a_link = a_link_start + lms_link_for_about_page + a_link_end
                        text = _("Introductions, prerequisites, FAQs that are used on %s (formatted in HTML)") % a_link
                    %>
                """),
            'rule': [MAKO_LINTER_RULESET.python_wrap_html, MAKO_LINTER_RULESET.python_concat_html, MAKO_LINTER_RULESET.python_wrap_html]
        },
        {
            'expression':
                textwrap.dedent("""
                    ${"Mixed {span_start}text{span_end}".format(
                        span_start=HTML("<span>"),
                        span_end=HTML("</span>"),
                    )}
                """),
            'rule': MAKO_LINTER_RULESET.python_requires_html_or_text
        },
        {
            'expression':
                textwrap.dedent("""
                    ${Text("Mixed {span_start}text{span_end}").format(
                        span_start=HTML("<span>"),
                        span_end=HTML("</span>"),
                    )}
                """),
            'rule': None
        },
        {
            'expression':
                textwrap.dedent("""
                    ${"Mixed {span_start}{text}{span_end}".format(
                        span_start=HTML("<span>"),
                        text=Text("This should still break."),
                        span_end=HTML("</span>"),
                    )}
                """),
            'rule': MAKO_LINTER_RULESET.python_requires_html_or_text
        },
        {
            'expression':
                textwrap.dedent("""
                    ${Text("Mixed {link_start}text{link_end}".format(
                        link_start=HTML("<a href='{}'>").format(url),
                        link_end=HTML("</a>"),
                    ))}
                """),
            'rule': [MAKO_LINTER_RULESET.python_close_before_format, MAKO_LINTER_RULESET.python_requires_html_or_text]
        },
        {
            'expression':
                textwrap.dedent("""
                    ${Text("Mixed {link_start}text{link_end}").format(
                        link_start=HTML("<a href='{}'>".format(url)),
                        link_end=HTML("</a>"),
                    )}
                """),
            'rule': MAKO_LINTER_RULESET.python_close_before_format
        },
        {
            'expression':
                textwrap.dedent("""
                    ${"Mixed {span_start}text{span_end}".format(
                        span_start="<span>",
                        span_end="</span>",
                    )}
                """),
            'rule': [MAKO_LINTER_RULESET.python_wrap_html, MAKO_LINTER_RULESET.python_wrap_html]
        },
        {
            'expression':
                textwrap.dedent("""
                    ${Text(_("String with multiple lines "
                        "{link_start}unenroll{link_end} "
                        "and final line")).format(
                            link_start=HTML(
                                '<a id="link__over_multiple_lines" '
                                'data-course-id="{course_id}" '
                                'href="#test-modal">'
                            ).format(
                                course_id=course_overview.id
                            ),
                            link_end=HTML('</a>'),
                    )}
                """),
            'rule': None
        },
        {
            'expression': "${'<span></span>'}",
            'rule': MAKO_LINTER_RULESET.python_wrap_html
        },
        {
            'expression': "${'Embedded HTML <strong></strong>'}",
            'rule': MAKO_LINTER_RULESET.python_wrap_html
        },
        {
            'expression': "${ HTML('<span></span>') }",
            'rule': None
        },
        {
            'expression': "${HTML(render_entry(map['entries'], child))}",
            'rule': None
        },
        {
            'expression': "${ '<span></span>' + 'some other text' }",
            'rule': [MAKO_LINTER_RULESET.python_concat_html, MAKO_LINTER_RULESET.python_wrap_html]
        },
        {
            'expression': "${ HTML('<span>missing closing parentheses.</span>' }",
            'rule': MAKO_LINTER_RULESET.python_parse_error
        },
        {
            'expression': "${'Rock &amp; Roll'}",
            'rule': MAKO_LINTER_RULESET.mako_html_entities
        },
        {
            'expression': "${'Rock &#38; Roll'}",
            'rule': MAKO_LINTER_RULESET.mako_html_entities
        },
    )
    def test_check_mako_with_text_and_html(self, data):
        """
        Test _check_mako_file_is_safe tests for proper use of Text() and Html().
        """
        linter = _build_mako_linter()
        results = FileResults('')

        mako_template = textwrap.dedent("""
            <%page expression_filter="h"/>
            {expression}
        """.format(expression=data['expression']))

        linter._check_mako_file_is_safe(mako_template, results)

        self._validate_data_rules(data, results)

    def test_check_mako_entity_with_no_default(self):
        """
        Test _check_mako_file_is_safe does not fail on entities when
        safe-by-default is not set.
        """
        linter = _build_mako_linter()
        results = FileResults('')

        mako_template = "${'Rock &#38; Roll'}"

        linter._check_mako_file_is_safe(mako_template, results)

        assert len(results.violations) == 1
        assert results.violations[0].rule == MAKO_LINTER_RULESET.mako_missing_default

    def test_check_mako_expression_default_disabled(self):
        """
        Test _check_mako_file_is_safe with disable pragma for safe-by-default
        works to designate that this is not a Mako file
        """
        linter = _build_mako_linter()
        results = FileResults('')

        mako_template = textwrap.dedent("""
            # This is anything but a Mako file.

            # pragma can appear anywhere in file
            # xss-lint: disable=mako-missing-default
        """)

        linter._check_mako_file_is_safe(mako_template, results)

        assert len(results.violations) == 1
        assert results.violations[0].is_disabled

    def test_check_mako_expression_disabled(self):
        """
        Test _check_mako_file_is_safe with disable pragma results in no
        violation
        """
        linter = _build_mako_linter()
        results = FileResults('')

        mako_template = textwrap.dedent("""
            <%page expression_filter="h"/>
            ## xss-lint: disable=mako-unwanted-html-filter
            ${x | h}
        """)

        linter._check_mako_file_is_safe(mako_template, results)

        assert len(results.violations) == 1
        assert results.violations[0].is_disabled

    @data(
        {'template': '{% extends "wiki/base.html" %}'},
        {'template': '{{ message }}'},
        {'template': '{# comment #}'},
    )
    def test_check_mako_on_django_template(self, data):
        """
        Test _check_mako_file_is_safe with disable pragma results in no
        violation
        """
        linter = _build_mako_linter()
        results = FileResults('')

        linter._check_mako_file_is_safe(data['template'], results)

        assert len(results.violations) == 0

    def test_check_mako_expressions_in_html_with_escape_filter(self):
        """
        Test _check_mako_file_is_safe results in no violations,
        when strip_all_tags_but_br filter is applied in html context
        """
        linter = _build_mako_linter()
        results = FileResults('')

        mako_template = textwrap.dedent("""
            <%page expression_filter="h"/>
            ${x | n, strip_all_tags_but_br}
        """)

        linter._check_mako_file_is_safe(mako_template, results)
        assert len(results.violations) == 0

    def test_check_mako_expressions_in_html_without_default(self):
        """
        Test _check_mako_file_is_safe in html context without the page level
        default h filter suppresses expression level violation
        """
        linter = _build_mako_linter()
        results = FileResults('')

        mako_template = textwrap.dedent("""
            ${x | h}
        """)

        linter._check_mako_file_is_safe(mako_template, results)

        assert len(results.violations) == 1
        assert results.violations[0].rule == MAKO_LINTER_RULESET.mako_missing_default

    @data(
        {'expression': '${x}', 'rule': MAKO_LINTER_RULESET.mako_invalid_js_filter},
        {'expression': '${{unbalanced}', 'rule': MAKO_LINTER_RULESET.mako_unparseable_expression},
        {'expression': '${x | n}', 'rule': MAKO_LINTER_RULESET.mako_invalid_js_filter},
        {'expression': '${x | h}', 'rule': MAKO_LINTER_RULESET.mako_invalid_js_filter},
        {'expression': '${x | n, dump_js_escaped_json}', 'rule': None},
        {'expression': '${x | n, decode.utf8}', 'rule': None},
    )
    def test_check_mako_expressions_in_javascript(self, data):
        """
        Test _check_mako_file_is_safe in JavaScript script context provides
        appropriate violations
        """
        linter = _build_mako_linter()
        results = FileResults('')

        mako_template = textwrap.dedent("""
            <%page expression_filter="h"/>
            ## switch to JavaScript context
            <script>
                {expression}
            </script>
            ## switch back to HTML context
            ${{x}}
        """.format(expression=data['expression']))

        linter._check_mako_file_is_safe(mako_template, results)

        self._validate_data_rules(data, results)

    @data(
        {'expression': '${x}', 'rule': MAKO_LINTER_RULESET.mako_invalid_js_filter},
        {'expression': '"${x | n, js_escaped_string}"', 'rule': None},
    )
    def test_check_mako_expressions_in_require_module(self, data):
        """
        Test _check_mako_file_is_safe in JavaScript require context provides
        appropriate violations
        """
        linter = _build_mako_linter()
        results = FileResults('')

        mako_template = textwrap.dedent("""
            <%page expression_filter="h"/>
            ## switch to JavaScript context (after next line)
            <%static:require_module module_name="${{x}}" class_name="TestFactory">
                {expression}
            </%static:require_module>
            ## switch back to HTML context
            ${{x}}
        """.format(expression=data['expression']))

        linter._check_mako_file_is_safe(mako_template, results)

        self._validate_data_rules(data, results)

    @data(
        {'expression': '${x}', 'rule': MAKO_LINTER_RULESET.mako_invalid_js_filter},
        {'expression': '"${x | n, js_escaped_string}"', 'rule': None},
    )
    def test_check_mako_expressions_in_require_js(self, data):
        """
        Test _check_mako_file_is_safe in JavaScript require js context provides
        appropriate violations
        """
        linter = _build_mako_linter()
        results = FileResults('')

        mako_template = textwrap.dedent("""
            <%page expression_filter="h"/>
            # switch to JavaScript context
            <%block name="requirejs">
                {expression}
            </%block>
            ## switch back to HTML context
            ${{x}}
        """.format(expression=data['expression']))

        linter._check_mako_file_is_safe(mako_template, results)

        self._validate_data_rules(data, results)

    @data(
        {'media_type': 'text/javascript', 'rule': None},
        {'media_type': 'text/ecmascript', 'rule': None},
        {'media_type': 'application/ecmascript', 'rule': None},
        {'media_type': 'application/javascript', 'rule': None},
        {'media_type': 'text/x-mathjax-config', 'rule': None},
        {'media_type': 'json/xblock-args', 'rule': None},
        {'media_type': 'text/template', 'rule': MAKO_LINTER_RULESET.mako_invalid_html_filter},
        {'media_type': 'unknown/type', 'rule': MAKO_LINTER_RULESET.mako_unknown_context},
    )
    def test_check_mako_expressions_in_script_type(self, data):
        """
        Test _check_mako_file_is_safe in script tag with different media types
        """
        linter = _build_mako_linter()
        results = FileResults('')

        mako_template = textwrap.dedent("""
            <%page expression_filter="h"/>
            # switch to JavaScript context
            <script type="{}">
                ${{x | n, dump_js_escaped_json}}
            </script>
            ## switch back to HTML context
            ${{x}}
        """).format(data['media_type'])

        linter._check_mako_file_is_safe(mako_template, results)

        self._validate_data_rules(data, results)

    def test_check_mako_expressions_in_mixed_contexts(self):
        """
        Test _check_mako_file_is_safe in mixed contexts provides
        appropriate violations
        """
        linter = _build_mako_linter()
        results = FileResults('')

        mako_template = textwrap.dedent("""
            <%page expression_filter="h"/>
            ${x | h}
            <script type="text/javascript">
                ${x | h}
            </script>
            ${x | h}
            <%static:require_module module_name="${x}" class_name="TestFactory">
                ${x | h}
            </%static:require_module>
            ${x | h}
        """)

        linter._check_mako_file_is_safe(mako_template, results)

        assert len(results.violations) == 5
        assert results.violations[0].rule == MAKO_LINTER_RULESET.mako_unwanted_html_filter
        assert results.violations[1].rule == MAKO_LINTER_RULESET.mako_invalid_js_filter
        assert results.violations[2].rule == MAKO_LINTER_RULESET.mako_unwanted_html_filter
        assert results.violations[3].rule == MAKO_LINTER_RULESET.mako_invalid_js_filter
        assert results.violations[4].rule == MAKO_LINTER_RULESET.mako_unwanted_html_filter

    def test_check_mako_expressions_javascript_strings(self):
        """
        Test _check_mako_file_is_safe javascript string specific rules.
        - mako_js_missing_quotes
        - mako_js_html_string
        """
        linter = _build_mako_linter()
        results = FileResults('')

        mako_template = textwrap.dedent("""
            <%page expression_filter="h"/>
            <script type="text/javascript">
                var valid1 = '${x | n, js_escaped_string} ${y | n, js_escaped_string}'
                var valid2 = '${x | n, js_escaped_string} ${y | n, js_escaped_string}'
                var valid3 = 'string' + ' ${x | n, js_escaped_string} '
                var valid4 = "${Text(_('Some mixed text{begin_span}with html{end_span}')).format(
                    begin_span=HTML('<span>'),
                    end_span=HTML('</span>'),
                ) | n, js_escaped_string}"
                var valid5 = " " + "${Text(_('Please {link_start}send us e-mail{link_end}.')).format(
                    link_start=HTML('<a href="#" id="feedback_email">'),
                    link_end=HTML('</a>'),
                ) | n, js_escaped_string}";
                var invalid1 = ${x | n, js_escaped_string};
                var invalid2 = '<strong>${x | n, js_escaped_string}</strong>'
                var invalid3 = '<strong>${x | n, dump_js_escaped_json}</strong>'
            </script>
        """)

        linter._check_mako_file_is_safe(mako_template, results)

        assert len(results.violations) == 3
        assert results.violations[0].rule == MAKO_LINTER_RULESET.mako_js_missing_quotes
        assert results.violations[1].rule == MAKO_LINTER_RULESET.mako_js_html_string
        assert results.violations[2].rule == MAKO_LINTER_RULESET.mako_js_html_string

    def test_check_javascript_in_mako_javascript_context(self):
        """
        Test _check_mako_file_is_safe with JavaScript error in JavaScript
        context.
        """
        linter = _build_mako_linter()
        results = FileResults('')

        mako_template = textwrap.dedent("""
            <%page expression_filter="h"/>
            <script type="text/javascript">
                var message = '<p>' + msg + '</p>';
            </script>
        """)

        linter._check_mako_file_is_safe(mako_template, results)

        assert len(results.violations) == 1
        assert results.violations[0].rule == MAKO_LINTER_RULESET.javascript_concat_html
        assert results.violations[0].start_line == 4

    @data(
        {'template': "\n${x | n}", 'parseable': True},
        {
            'template': textwrap.dedent(
                """
                    <div>${(
                        'tabbed-multi-line-expression'
                    ) | n}</div>
                """),
            'parseable': True
        },
        {'template': "${{unparseable}", 'parseable': False},
    )
    def test_expression_detailed_results(self, data):
        """
        Test _check_mako_file_is_safe provides detailed results, including line
        numbers, columns, and line
        """
        linter = _build_mako_linter()
        results = FileResults('')

        linter._check_mako_file_is_safe(data['template'], results)

        assert len(results.violations) == 2
        assert results.violations[0].rule == MAKO_LINTER_RULESET.mako_missing_default

        violation = results.violations[1]
        lines = list(data['template'].splitlines())
        assert ('${' in lines[(violation.start_line - 1)])
        assert lines[(violation.start_line - 1)].startswith('${', (violation.start_column - 1))
        if data['parseable']:
            assert ('}' in lines[(violation.end_line - 1)])
            assert lines[(violation.end_line - 1)].startswith('}', ((violation.end_column - len('}')) - 1))
        else:
            assert violation.start_line == violation.end_line
            assert violation.end_column == '?'
        assert len(violation.lines) == ((violation.end_line - violation.start_line) + 1)
        for line_index in range(0, len(violation.lines)):
            assert violation.lines[line_index] == lines[((line_index + violation.start_line) - 1)]

    @data(
        {'template': "${x}"},
        {'template': "\n ${x}"},
        {'template': "${x} "},
        {'template': "${{test-balanced-delims}} "},
        {'template': "${'{unbalanced in string'}"},
        {'template': "${'unbalanced in string}'}"},
        {'template': "${(\n    'tabbed-multi-line-expression'\n  )}"},
    )
    def test_find_mako_expressions(self, data):
        """
        Test _find_mako_expressions for parseable expressions
        """
        linter = _build_mako_linter()

        expressions = linter._find_mako_expressions(data['template'])

        assert len(expressions) == 1
        start_index = expressions[0].start_index
        end_index = expressions[0].end_index
        assert data['template'][start_index:end_index] == data['template'].strip()
        assert expressions[0].expression == data['template'].strip()

    @data(
        {'template': " ${{unparseable} ${}", 'start_index': 1},
        {'template': " ${'unparseable} ${}", 'start_index': 1},
    )
    def test_find_unparseable_mako_expressions(self, data):
        """
        Test _find_mako_expressions for unparseable expressions
        """
        linter = _build_mako_linter()

        expressions = linter._find_mako_expressions(data['template'])
        assert (2 <= len(expressions))
        assert expressions[0].start_index == data['start_index']
        assert expressions[0].expression is None

    @data(
        {
            'template': '${""}',
            'result': {'start_index': 2, 'end_index': 4, 'quote_length': 1}
        },
        {
            'template': "${''}",
            'result': {'start_index': 2, 'end_index': 4, 'quote_length': 1}
        },
        {
            'template': "${'Hello'}",
            'result': {'start_index': 2, 'end_index': 9, 'quote_length': 1}
        },
        {
            'template': '${""" triple """}',
            'result': {'start_index': 2, 'end_index': 16, 'quote_length': 3}
        },
        {
            'template': r""" ${" \" \\"} """,
            'result': {'start_index': 3, 'end_index': 11, 'quote_length': 1}
        },
        {
            'template': "${'broken string}",
            'result': {'start_index': 2, 'end_index': None, 'quote_length': None}
        },
    )
    def test_parse_string(self, data):
        """
        Test _parse_string helper
        """
        linter = _build_mako_linter()

        parse_string = ParseString(data['template'], data['result']['start_index'], len(data['template']))
        string_dict = {
            'start_index': parse_string.start_index,
            'end_index': parse_string.end_index,
            'quote_length': parse_string.quote_length,
        }

        self.assertDictEqual(string_dict, data['result'])
        if parse_string.end_index is not None:
            assert data['template'][parse_string.start_index:parse_string.end_index] == parse_string.string
            start_inner_index = parse_string.start_index + parse_string.quote_length
            end_inner_index = parse_string.end_index - parse_string.quote_length
            assert data['template'][start_inner_index:end_inner_index] == parse_string.string_inner


@ddt
class TestDjangoTemplateLinter(TestLinter):
    """
    Test DjangoTemplateLinter
    """

    @data(

        {'expression': '{% trans "whatever" as tmsg %}{{tmsg|force_escape}}',
         'rule': None},

        {'expression': '{% trans "whatever" as tmsgx %}{{tmsg|force_escape}}',
         'rule': DJANGO_LINTER_RULESET.django_trans_escape_variable_mismatch},

        {'expression': '{% trans "whatever" as tmsgx %}{{tmsgx|force_escap}}',
         'rule': DJANGO_LINTER_RULESET.django_trans_invalid_escape_filter},

        {'expression': '{% trans "whatever" as tmsg %}',
         'rule': DJANGO_LINTER_RULESET.django_trans_missing_escape},

        {'expression': '{% trans "whatever" %}',
         'rule': DJANGO_LINTER_RULESET.django_trans_missing_escape},

        {'expression': '{% trans "{span_start} whatever {span_end}" as tmsg %}',
         'rule': DJANGO_LINTER_RULESET.django_html_interpolation_missing},

        {'expression': '{% trans "{span_start} whatever {span_end}" as tmsg %}{{tmsg|force_filter}}',
         'rule': DJANGO_LINTER_RULESET.django_html_interpolation_missing},

        {'expression': """
        {% trans "{span_start} whatever {span_end}" as tmsg %}
        {% interpolate_html tmsg user_name=user_data.name start_span='<span class="a">'|safe end_span='</span>'|safe %}
        """, 'rule': None},

        {'expression': """
            {% trans "{span_start} whatever {span_end}" as tmsg %}
            {% interpolate_html %}
            """, 'rule': [DJANGO_LINTER_RULESET.django_html_interpolation_missing, DJANGO_LINTER_RULESET.django_html_interpolation_invalid_tag]},

        {'expression': """
            {% trans "{span_start} whatever {span_end}" as tmsg %}
            {% interpolate_html t start_span='<span class="a">'|safe end_span='</span>'|safe %}
            """, 'rule': DJANGO_LINTER_RULESET.django_html_interpolation_missing},

        {'expression': """
        {% trans "{span_start} whatever {span_end}" as tmsg %}
        {% interpolate_html tmsg start_span='<span class="a">'|unknown end_span='</span>'|safe %}
        """, 'rule': DJANGO_LINTER_RULESET.django_html_interpolation_missing_safe_filter},

        {'expression': """
                    {% trans "<span 'a'='b' 'c'='d'> whatever </span>" as tmsg %}
        """, 'rule': DJANGO_LINTER_RULESET.django_html_interpolation_missing},

        {'expression': """
                        {% trans "<span 'a'='b' 'c'='d'> whatever </span>" %}
            """, 'rule': DJANGO_LINTER_RULESET.django_html_interpolation_missing},

        {'expression': """
                {% filter force_escape %}
                {% blocktrans %}
                Some translation string
                {% endblocktrans %}
                {% endfilter %}
                """, 'rule': None},

        {'expression': """
                {% filter force_escape
                {% blocktrans %}
                Some translation string
                {% endblocktrans %}
                {% endfilter %}
                """, 'rule': DJANGO_LINTER_RULESET.django_blocktrans_escape_filter_parse_error},

        {'expression': """
                {% filter someother_filter %}
                {% blocktrans %}
                Some translation strubg
                {% endblocktrans %}
                {% endfilter %}
                """, 'rule': DJANGO_LINTER_RULESET.django_blocktrans_missing_escape_filter},

        {'expression': """
                {% filter force_escape xyz %}
                {% blocktrans %}
                Some translation string
                {% endblocktrans %}
                {% endfilter %}
                """, 'rule': DJANGO_LINTER_RULESET.django_blocktrans_missing_escape_filter},

        {'expression': """
                {% blocktrans %}
                Some translation string
                {% endblocktrans %}
                """, 'rule': DJANGO_LINTER_RULESET.django_blocktrans_missing_escape_filter},

        {'expression': """
                {% blocktrans %}
                Some translation <span 'a'='b' 'c'='d'> whatever </span>
                {% endblocktrans %}
                """, 'rule': DJANGO_LINTER_RULESET.django_html_interpolation_missing},

    )
    def test_check_django_expressions_in_html(self, data):
        """
        Test _check_django_file_is_safe in html context provides appropriate violations
        """
        linter = _build_django_linter()
        results = FileResults('')

        django_template = textwrap.dedent(
            """
            {load_i18n}
            {load_django_html}
            {expression}
        """.format(expression=data['expression'],
                   load_i18n='{% load i18n %}',
                   load_django_html='{% load django_html %}'))

        linter._check_django_file_is_safe(django_template, results)

        self._validate_data_rules(data, results)

    def test_check_django_trans_expression_disabled(self):
        """
        Test _check_django_file_is_safe with disable pragma results in no
        violation
        """
        linter = _build_django_linter()
        results = FileResults('')

        django_template = textwrap.dedent(
            """
            {load_i18n}
            {load_django_html}
            {expression}
        """.format(expression="""
                    {# xss-lint: disable=django-trans-missing-escape #}
                    {% trans 'Documentation' as tmsg%}
        """,
                   load_i18n='{% load i18n %}',
                   load_django_html='{% load django_html %}'))

        linter._check_django_file_is_safe(django_template, results)

        assert len(results.violations) == 1
        assert results.violations[0].is_disabled

        def test_check_django_blocktrans_expression_disabled(self):
            """
            Test _check_django_file_is_safe with disable pragma results in no
            violation
            """
            linter = _build_django_linter()
            results = FileResults('')

            django_template = textwrap.dedent(
                """
                {load_i18n}
                {load_django_html}
                {expression}
            """.format(expression="""
                    {# xss-lint: disable=django-blocktrans-missing-escape-filter #}
                    {% blocktrans %}
                    sometext
                    {% endblocktrans %}
            """,
                       load_i18n='{% load i18n %}',
                       load_django_html='{% load django_html %}'))

            linter._check_django_file_is_safe(django_template, results)

            assert len(results.violations) == 1
            assert results.violations[0].is_disabled

    def test_check_django_trans_expression_commented(self):
        """
        Test _check_django_file_is_safe with comment results in no
        violation
        """
        linter = _build_django_linter()
        results = FileResults('')

        django_template = textwrap.dedent(
            """
            {load_i18n}
            {load_django_html}
            {expression}
        """.format(expression="""
                            {# trans 'Documentation' as tmsg #}
                """,
                   load_i18n='{% load i18n %}',
                   load_django_html='{% load django_html %}'))

        linter._check_django_file_is_safe(django_template, results)

        assert len(results.violations) == 0

    def test_check_django_blocktrans_expression_commented(self):
        """
        Test _check_django_file_is_safe with comment results in no
        violation
        """
        linter = _build_django_linter()
        results = FileResults('')

        django_template = textwrap.dedent(
            """
            {load_i18n}
            {load_django_html}
            {expression}
        """.format(expression="""
                            {% comment %}
                            {% blocktrans %}
                            {% endblocktrans %}
                            {% endcomment %}
                """,
                   load_i18n='{% load i18n %}',
                   load_django_html='{% load django_html %}'))

        linter._check_django_file_is_safe(django_template, results)

        assert len(results.violations) == 0

    def test_check_django_interpolate_tag_expression_commented(self):
        """
        Test _check_django_file_is_safe with comment results in no
        violation
        """
        linter = _build_django_linter()
        results = FileResults('')

        django_template = textwrap.dedent(
            """
            {load_i18n}
            {load_django_html}
            {expression}
        """.format(expression="""
                            {% comment %}
                            {% interpolate_html %}
                            {% endcomment %}
                """,
                   load_i18n='{% load i18n %}',
                   load_django_html='{% load django_html %}'))

        linter._check_django_file_is_safe(django_template, results)

        assert len(results.violations) == 0
