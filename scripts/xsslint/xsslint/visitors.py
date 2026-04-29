"""
Custom AST NodeVisitor classes uses for Python xss linting.
"""


import ast
import re

from xsslint.reporting import ExpressionRuleViolation
from xsslint.rules import RuleSet
from xsslint.utils import Expression, ParseString, StringLines

ruleset = RuleSet(
    python_concat_html='python-concat-html',
    python_deprecated_display_name='python-deprecated-display-name',
    python_requires_html_or_text='python-requires-html-or-text',
    python_close_before_format='python-close-before-format',
    python_wrap_html='python-wrap-html',
    python_interpolate_html='python-interpolate-html',
)


class BaseVisitor(ast.NodeVisitor):
    """
    Base class for AST NodeVisitor used for Python xss linting.

    Important: This base visitor skips all __repr__ function definitions.
    """
    def __init__(self, file_contents, results):
        """
        Init method.

        Arguments:
            file_contents: The contents of the Python file.
            results: A file results objects to which violations will be added.

        """
        super().__init__()
        self.file_contents = file_contents
        self.lines = StringLines(self.file_contents)
        self.results = results

    def node_to_expression(self, node):
        """
        Takes a node and translates it to an expression to be used with
        violations.

        Arguments:
            node: An AST node.

        """
        line_start_index = self.lines.line_number_to_start_index(node.lineno)
        start_index = line_start_index + node.col_offset
        if isinstance(node, ast.Str):
            # Triple quotes give col_offset of -1 on the last line of the string.
            if node.col_offset == -1:
                triple_quote_regex = re.compile("""['"]{3}""")
                end_triple_quote_match = triple_quote_regex.search(self.file_contents, line_start_index)
                open_quote_index = self.file_contents.rfind(end_triple_quote_match.group(), 0, end_triple_quote_match.start())  # noqa: E501
                if open_quote_index > 0:
                    start_index = open_quote_index
                else:
                    # If we can't find a starting quote, let's assume that what
                    # we considered the end quote is really the start quote.
                    start_index = end_triple_quote_match.start()
            string = ParseString(self.file_contents, start_index, len(self.file_contents))
            return Expression(string.start_index, string.end_index)
        else:
            return Expression(start_index)

    def visit_FunctionDef(self, node):
        """
        Skips processing of __repr__ functions, since these sometimes use '<'
        for non-HTML purposes.

        Arguments:
            node: An AST node.
        """
        if node.name != '__repr__':
            self.generic_visit(node)


class HtmlStringVisitor(BaseVisitor):
    """
    Checks for strings that contain HTML. Assumes any string with < or > is
    considered potential HTML.

    To be used only with strings in context of format or concat.

    """
    def __init__(self, file_contents, results, skip_wrapped_html=False):
        """
        Init function.

        Arguments:
            file_contents: The contents of the Python file.
            results: A file results objects to which violations will be added.
            skip_wrapped_html: True if visitor should skip strings wrapped with
                HTML() or Text(), and False otherwise.
        """
        super().__init__(file_contents, results)
        self.skip_wrapped_html = skip_wrapped_html
        self.unsafe_html_string_nodes = []
        self.over_escaped_entity_string_nodes = []
        self.has_text_or_html_call = False

    def visit_Str(self, node):
        """
        When strings are visited, checks if it contains HTML.

        Arguments:
            node: An AST node.
        """
        # Skips '<' (and '>') in regex named groups. For example, "(?P<group>)".
        if re.search('[(][?]P<', node.s) is None and re.search('<', node.s) is not None:
            self.unsafe_html_string_nodes.append(node)
        if re.search(r"&[#]?[a-zA-Z0-9]+;", node.s):
            self.over_escaped_entity_string_nodes.append(node)

    def visit_Call(self, node):
        """
        Skips processing of string contained inside HTML() and Text() calls when
        skip_wrapped_html is True.

        Arguments:
            node: An AST node.

        """
        is_html_or_text_call = isinstance(node.func, ast.Name) and node.func.id in ['HTML', 'Text']
        if self.skip_wrapped_html and is_html_or_text_call:
            self.has_text_or_html_call = True
        else:
            self.generic_visit(node)


class ContainsFormatVisitor(BaseVisitor):
    """
    Checks if there are any nested format() calls.

    This visitor is meant to be called on HTML() and Text() ast.Call nodes to
    search for any illegal nested format() calls.

    """
    def __init__(self, file_contents, results):
        """
        Init function.

        Arguments:
            file_contents: The contents of the Python file.
            results: A file results objects to which violations will be added.

        """
        super().__init__(file_contents, results)
        self.contains_format_call = False

    def visit_Attribute(self, node):
        """
        Simple check for format calls (attribute).

        Arguments:
            node: An AST node.

        """
        # Attribute(expr value, identifier attr, expr_context ctx)
        if node.attr == 'format':
            self.contains_format_call = True
        else:
            self.generic_visit(node)


class FormatInterpolateVisitor(BaseVisitor):
    """
    Checks if format() interpolates any HTML() or Text() calls. In other words,
    are Text() or HTML() calls nested inside the call to format().

    This visitor is meant to be called on a format() attribute node.

    """
    def __init__(self, file_contents, results):
        """
        Init function.

        Arguments:
            file_contents: The contents of the Python file.
            results: A file results objects to which violations will be added.

        """
        super().__init__(file_contents, results)
        self.interpolates_text_or_html = False
        self.format_caller_node = None

    def visit_Call(self, node):
        """
        Checks all calls. Remembers the caller of the initial format() call, or
        in other words, the left-hand side of the call. Also tracks if HTML()
        or Text() calls were seen.

        Arguments:
            node: The AST root node.

        """
        if isinstance(node.func, ast.Attribute) and node.func.attr == 'format':
            if self.format_caller_node is None:
                # Store the caller, or left-hand-side node of the initial
                # format() call.
                self.format_caller_node = node.func.value
        elif isinstance(node.func, ast.Name) and node.func.id in ['HTML', 'Text']:
            # found Text() or HTML() call in arguments passed to format()
            self.interpolates_text_or_html = True
        self.generic_visit(node)

    def generic_visit(self, node):
        """
        Determines whether or not to continue to visit nodes according to the
        following rules:
        - Once a Text() or HTML() call has been found, stop visiting more nodes.
        - Skip the caller of the outer-most format() call, or in other words,
        the left-hand side of the call.

        Arguments:
            node: The AST root node.

        """
        if self.interpolates_text_or_html is False:
            if self.format_caller_node is not node:
                super().generic_visit(node)


class OuterFormatVisitor(BaseVisitor):
    """
    Only visits outer most Python format() calls. These checks are not repeated
    for any nested format() calls.

    This visitor is meant to be used once from the root.

    """
    def visit_Call(self, node):
        """
        Checks that format() calls which contain HTML() or Text() use HTML() or
        Text() as the caller. In other words, Text() or HTML() must be used
        before format() for any arguments to format() that contain HTML() or
        Text().

        Arguments:
             node: An AST node.
        """
        if isinstance(node.func, ast.Attribute) and node.func.attr == 'format':
            visitor = HtmlStringVisitor(self.file_contents, self.results, True)
            visitor.visit(node)
            for unsafe_html_string_node in visitor.unsafe_html_string_nodes:
                self.results.violations.append(ExpressionRuleViolation(
                    ruleset.python_wrap_html, self.node_to_expression(unsafe_html_string_node)
                ))
            # Do not continue processing child nodes of this format() node.
        else:
            self.generic_visit(node)


class AllNodeVisitor(BaseVisitor):
    """
    Visits all nodes and does not interfere with calls to generic_visit(). This
    is used in conjunction with other visitors to check for a variety of
    violations.

    This visitor is meant to be used once from the root.

    """

    def visit_Attribute(self, node):
        """
        Checks for uses of deprecated `display_name_with_default_escaped`.

        Arguments:
             node: An AST node.
        """
        if node.attr == 'display_name_with_default_escaped':
            self.results.violations.append(ExpressionRuleViolation(
                ruleset.python_deprecated_display_name, self.node_to_expression(node)
            ))
        self.generic_visit(node)

    def visit_Call(self, node):
        """
        Checks for a variety of violations:
        - Checks that format() calls with nested HTML() or Text() calls use
        HTML() or Text() on the left-hand side.
        - For each HTML() and Text() call, calls into separate visitor to check
        for inner format() calls.

        Arguments:
             node: An AST node.

        """
        if isinstance(node.func, ast.Attribute) and node.func.attr == 'format':
            visitor = FormatInterpolateVisitor(self.file_contents, self.results)
            visitor.visit(node)
            if visitor.interpolates_text_or_html:
                format_caller = node.func.value
                is_caller_html_or_text = isinstance(format_caller, ast.Call) and \
                    isinstance(format_caller.func, ast.Name) and \
                    format_caller.func.id in ['Text', 'HTML']
                # If format call has nested Text() or HTML(), then the caller,
                # or left-hand-side of the format() call, must be a call to
                # Text() or HTML().
                if is_caller_html_or_text is False:
                    self.results.violations.append(ExpressionRuleViolation(
                        ruleset.python_requires_html_or_text, self.node_to_expression(node.func)
                    ))
        elif isinstance(node.func, ast.Name) and node.func.id in ['HTML', 'Text']:
            visitor = ContainsFormatVisitor(self.file_contents, self.results)
            visitor.visit(node)
            if visitor.contains_format_call:
                self.results.violations.append(ExpressionRuleViolation(
                    ruleset.python_close_before_format, self.node_to_expression(node.func)
                ))

        self.generic_visit(node)

    def visit_BinOp(self, node):
        """
        Checks for concat using '+' and interpolation using '%' with strings
        containing HTML.

        """
        rule = None
        if isinstance(node.op, ast.Mod):
            rule = ruleset.python_interpolate_html
        elif isinstance(node.op, ast.Add):
            rule = ruleset.python_concat_html
        if rule is not None:
            visitor = HtmlStringVisitor(self.file_contents, self.results)
            visitor.visit(node.left)
            has_illegal_html_string = len(visitor.unsafe_html_string_nodes) > 0
            # Create new visitor to clear state.
            visitor = HtmlStringVisitor(self.file_contents, self.results)
            visitor.visit(node.right)
            has_illegal_html_string = has_illegal_html_string or len(visitor.unsafe_html_string_nodes) > 0
            if has_illegal_html_string:
                self.results.violations.append(ExpressionRuleViolation(
                    rule, self.node_to_expression(node)
                ))
        self.generic_visit(node)
