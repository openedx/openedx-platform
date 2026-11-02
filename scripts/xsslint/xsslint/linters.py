"""
Linter classes containing logic for checking various filetypes.
"""


import ast
import io
import os
import re
import textwrap

from xsslint import visitors
from xsslint.reporting import ExpressionRuleViolation, FileResults, RuleViolation
from xsslint.rules import RuleSet
from xsslint.utils import Expression, ParseString, StringLines, is_skip_dir
from xsslint.django_linter import TransExpression, BlockTransExpression, HtmlInterpolateExpression


class BaseLinter:
    """
    BaseLinter provides some helper functions that are used by multiple linters.

    """

    LINE_COMMENT_DELIM = None

    def _is_valid_directory(self, skip_dirs, directory):
        """
        Determines if the provided directory is a directory that could contain
        a file that needs to be linted.

        Arguments:
            skip_dirs: The directories to be skipped.
            directory: The directory to be linted.

        Returns:
            True if this directory should be linted for violations and False
            otherwise.
        """
        if is_skip_dir(skip_dirs, directory):
            return False

        return True

    def _load_file(self, file_full_path):
        """
        Loads a file into a string.

        Arguments:
            file_full_path: The full path of the file to be loaded.

        Returns:
            A string containing the files contents.

        """
        with open(file_full_path) as input_file:
            file_contents = input_file.read()
            return file_contents

    def _load_and_check_file_is_safe(self, file_full_path, lint_function, results):
        """
        Loads the Python file and checks if it is in violation.

        Arguments:
            file_full_path: The file to be loaded and linted.
            lint_function: A function that will lint for violations. It must
                take two arguments:
                1) string contents of the file
                2) results object
            results: A FileResults to be used for this file

        Returns:
            The file results containing any violations.

        """
        file_contents = self._load_file(file_full_path)
        lint_function(file_contents, results)
        return results

    def _find_closing_char_index(
            self, start_delim, open_char, close_char, template, start_index, num_open_chars=0, strings=None
    ):
        """
        Finds the index of the closing char that matches the opening char.

        For example, this could be used to find the end of a Mako expression,
        where the open and close characters would be '{' and '}'.

        Arguments:
            start_delim: If provided (e.g. '${' for Mako expressions), the
                closing character must be found before the next start_delim.
            open_char: The opening character to be matched (e.g '{')
            close_char: The closing character to be matched (e.g '}')
            template: The template to be searched.
            start_index: The start index of the last open char.
            num_open_chars: The current number of open chars.
            strings: A list of ParseStrings already parsed

        Returns:
            A dict containing the following, or None if unparseable:
                close_char_index: The index of the closing character
                strings: a list of ParseStrings

        """
        strings = [] if strings is None else strings

        # Find start index of an uncommented line.
        start_index = self._uncommented_start_index(template, start_index)
        # loop until we found something useful on an uncommented out line
        while start_index is not None:
            close_char_index = template.find(close_char, start_index)
            if close_char_index < 0:
                # If we can't find a close char, let's just quit.
                return None
            open_char_index = template.find(open_char, start_index, close_char_index)
            parse_string = ParseString(template, start_index, close_char_index)

            valid_index_list = [close_char_index]
            if 0 <= open_char_index:
                valid_index_list.append(open_char_index)
            if parse_string.start_index is not None:
                valid_index_list.append(parse_string.start_index)
            min_valid_index = min(valid_index_list)

            start_index = self._uncommented_start_index(template, min_valid_index)
            if start_index == min_valid_index:
                break

        if start_index is None:
            # No uncommented code to search.
            return None

        if parse_string.start_index == min_valid_index:
            strings.append(parse_string)
            if parse_string.end_index is None:
                return None
            else:
                return self._find_closing_char_index(
                    start_delim, open_char, close_char, template, start_index=parse_string.end_index,
                    num_open_chars=num_open_chars, strings=strings
                )

        if open_char_index == min_valid_index:
            if start_delim is not None:
                # if we find another starting delim, consider this unparseable
                start_delim_index = template.find(start_delim, start_index, close_char_index)
                if 0 <= start_delim_index < open_char_index:
                    return None
            return self._find_closing_char_index(
                start_delim, open_char, close_char, template, start_index=open_char_index + 1,
                num_open_chars=num_open_chars + 1, strings=strings
            )

        if num_open_chars == 0:
            return {
                'close_char_index': close_char_index,
                'strings': strings,
            }
        else:
            return self._find_closing_char_index(
                start_delim, open_char, close_char, template, start_index=close_char_index + 1,
                num_open_chars=num_open_chars - 1, strings=strings
            )

    def _uncommented_start_index(self, template, start_index):
        """
        Finds the first start_index that is on an uncommented line.

        Arguments:
            template: The template to be searched.
            start_index: The start index of the last open char.

        Returns:
            If start_index is on an uncommented out line, returns start_index.
            Otherwise, returns the start_index of the first line that is
            uncommented, if there is one. Otherwise, returns None.
        """
        if self.LINE_COMMENT_DELIM is not None:
            line_start_index = StringLines(template).index_to_line_start_index(start_index)
            uncommented_line_start_index_regex = re.compile(fr"^(?!\s*{self.LINE_COMMENT_DELIM})", re.MULTILINE)
            # Finds the line start index of the first uncommented line, including the current line.
            match = uncommented_line_start_index_regex.search(template, line_start_index)
            if match is None:
                # No uncommented lines.
                return None
            elif match.start() < start_index:
                # Current line is uncommented, so return original start_index.
                return start_index
            else:
                # Return start of first uncommented line.
                return match.start()
        else:
            # No line comment delimeter, so this acts as a no-op.
            return start_index


class UnderscoreTemplateLinter(BaseLinter):
    """
    The linter for Underscore.js template files.
    """

    ruleset = RuleSet(
        underscore_not_escaped='underscore-not-escaped',
    )

    def __init__(self, skip_dirs=None):
        """
        Init method.
        """
        super().__init__()
        self._skip_underscore_dirs = skip_dirs or ()

    def process_file(self, directory, file_name):
        """
        Process file to determine if it is an Underscore template file and
        if it is safe.

        Arguments:
            directory (string): The directory of the file to be checked
            file_name (string): A filename for a potential underscore file

        Returns:
            The file results containing any violations.

        """
        full_path = os.path.normpath(directory + '/' + file_name)
        results = FileResults(full_path)

        if not self._is_valid_directory(self._skip_underscore_dirs, directory):
            return results

        if not file_name.lower().endswith('.underscore'):
            return results

        return self._load_and_check_file_is_safe(full_path, self.check_underscore_file_is_safe, results)

    def check_underscore_file_is_safe(self, underscore_template, results):
        """
        Checks for violations in an Underscore.js template.

        Arguments:
            underscore_template: The contents of the Underscore.js template.
            results: A file results objects to which violations will be added.

        """
        self._check_underscore_expressions(underscore_template, results)
        results.prepare_results(underscore_template)

    def _check_underscore_expressions(self, underscore_template, results):
        """
        Searches for Underscore.js expressions that contain violations.

        Arguments:
            underscore_template: The contents of the Underscore.js template.
            results: A list of results into which violations will be added.

        """
        expressions = self._find_unescaped_expressions(underscore_template)
        for expression in expressions:
            if not self._is_safe_unescaped_expression(expression):
                results.violations.append(ExpressionRuleViolation(
                    self.ruleset.underscore_not_escaped, expression
                ))

    def _is_safe_unescaped_expression(self, expression):
        """
        Determines whether an expression is safely escaped, even though it is
        using the expression syntax that doesn't itself escape (i.e. <%= ).

        In some cases it is ok to not use the Underscore.js template escape
        (i.e. <%- ) because the escaping is happening inside the expression.

        Safe examples::

            <%= edx.HtmlUtils.ensureHtml(message) %>
            <%= HtmlUtils.ensureHtml(message) %>
            <%= _.escape(message) %>

        Arguments:
            expression: The Expression being checked.

        Returns:
            True if the Expression has been safely escaped, and False otherwise.

        """
        if expression.expression_inner.startswith('edx.HtmlUtils.'):
            return True
        if expression.expression_inner.startswith('HtmlUtils.'):
            return True
        if expression.expression_inner.startswith('_.escape('):
            return True
        return False

    def _find_unescaped_expressions(self, underscore_template):
        """
        Returns a list of unsafe expressions.

        At this time all expressions that are unescaped are considered unsafe.

        Arguments:
            underscore_template: The contents of the Underscore.js template.

        Returns:
            A list of Expressions.
        """
        unescaped_expression_regex = re.compile("<%=.*?%>", re.DOTALL)

        expressions = []
        for match in unescaped_expression_regex.finditer(underscore_template):
            expression = Expression(
                match.start(), match.end(), template=underscore_template, start_delim="<%=", end_delim="%>"
            )
            expressions.append(expression)
        return expressions


class JavaScriptLinter(BaseLinter):
    """
    The linter for JavaScript files.
    """

    LINE_COMMENT_DELIM = "//"

    ruleset = RuleSet(
        javascript_jquery_append='javascript-jquery-append',
        javascript_jquery_prepend='javascript-jquery-prepend',
        javascript_jquery_insertion='javascript-jquery-insertion',
        javascript_jquery_insert_into_target='javascript-jquery-insert-into-target',
        javascript_jquery_html='javascript-jquery-html',
        javascript_concat_html='javascript-concat-html',
        javascript_escape='javascript-escape',
    )

    def __init__(self, underscore_linter, javascript_skip_dirs=None):
        """
        Init method.
        """
        super().__init__()
        self.underscore_linter = underscore_linter
        self.ruleset = self.ruleset + self.underscore_linter.ruleset
        self._skip_javascript_dirs = javascript_skip_dirs or ()

    def process_file(self, directory, file_name):
        """
        Process file to determine if it is a JavaScript file and
        if it is safe.

        Arguments:
            directory (string): The directory of the file to be checked
            file_name (string): A filename for a potential JavaScript file

        Returns:
            The file results containing any violations.

        """
        file_full_path = os.path.normpath(directory + '/' + file_name)
        results = FileResults(file_full_path)

        if not results.is_file:
            return results

        if file_name.lower().endswith('.js') and not file_name.lower().endswith('.min.js'):
            skip_dirs = self._skip_javascript_dirs
        else:
            return results

        if not self._is_valid_directory(skip_dirs, directory):
            return results

        return self._load_and_check_file_is_safe(file_full_path, self.check_javascript_file_is_safe, results)

    def check_javascript_file_is_safe(self, file_contents, results):
        """
        Checks for violations in a JavaScript file.

        Arguments:
            file_contents: The contents of the JavaScript file.
            results: A file results objects to which violations will be added.

        """
        no_caller_check = None
        no_argument_check = None
        self._check_jquery_function(
            file_contents, "append", self.ruleset.javascript_jquery_append, no_caller_check,
            self._is_jquery_argument_safe, results
        )
        self._check_jquery_function(
            file_contents, "prepend", self.ruleset.javascript_jquery_prepend, no_caller_check,
            self._is_jquery_argument_safe, results
        )
        self._check_jquery_function(
            file_contents, "unwrap|wrap|wrapAll|wrapInner|after|before|replaceAll|replaceWith",
            self.ruleset.javascript_jquery_insertion, no_caller_check, self._is_jquery_argument_safe, results
        )
        self._check_jquery_function(
            file_contents, "appendTo|prependTo|insertAfter|insertBefore",
            self.ruleset.javascript_jquery_insert_into_target, self._is_jquery_insert_caller_safe, no_argument_check, results
        )
        self._check_jquery_function(
            file_contents, "html", self.ruleset.javascript_jquery_html, no_caller_check,
            self._is_jquery_html_argument_safe, results
        )
        self._check_javascript_escape(file_contents, results)
        self._check_concat_with_html(file_contents, self.ruleset.javascript_concat_html, results)
        self.underscore_linter.check_underscore_file_is_safe(file_contents, results)
        results.prepare_results(file_contents, line_comment_delim=self.LINE_COMMENT_DELIM)

    def _get_expression_for_function(self, file_contents, function_start_match):
        """
        Returns an expression that matches the function call opened with
        function_start_match.

        Arguments:
            file_contents: The contents of the JavaScript file.
            function_start_match: A regex match representing the start of the function
                call (e.g. ".escape(").

        Returns:
            An Expression that best matches the function.

        """
        start_index = function_start_match.start()
        inner_start_index = function_start_match.end()
        result = self._find_closing_char_index(
            None, "(", ")", file_contents, start_index=inner_start_index
        )
        if result is not None:
            end_index = result['close_char_index'] + 1
            expression = Expression(
                start_index, end_index, template=file_contents, start_delim=function_start_match.group(), end_delim=")"
            )
        else:
            expression = Expression(start_index)
        return expression

    def _check_javascript_escape(self, file_contents, results):
        """
        Checks that escape() is not used. escape() is not recommended.
        ref. https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/escape

        Arguments:
            file_contents: The contents of the JavaScript file.
            results: A file results objects to which violations will be added.

        """
        # Regex to match uses of escape() or window.escape().
        regex = re.compile(r"(?:^|(?<=window\.)|(?<![\w.$]))escape\(")
        for function_match in regex.finditer(file_contents):
            expression = self._get_expression_for_function(file_contents, function_match)
            results.violations.append(ExpressionRuleViolation(self.ruleset.javascript_escape, expression))

    def _check_jquery_function(self, file_contents, function_names, rule, is_caller_safe, is_argument_safe, results):
        """
        Checks that the JQuery function_names (e.g. append(), prepend()) calls
        are safe.

        Arguments:
            file_contents: The contents of the JavaScript file.
            function_names: A pipe delimited list of names of the functions
                (e.g. "wrap|after|before").
            rule: The name of the rule to use for validation errors (e.g.
                self.ruleset.javascript_jquery_append).
            is_caller_safe: A function to test if caller of the JQuery function
                is safe.
            is_argument_safe: A function to test if the argument passed to the
                JQuery function is safe.
            results: A file results objects to which violations will be added.

        """
        # Ignores calls starting with "HtmlUtils.", because those are safe
        regex = re.compile(fr"(?<!HtmlUtils).(?:{function_names})\(")
        for function_match in regex.finditer(file_contents):
            is_violation = True
            expression = self._get_expression_for_function(file_contents, function_match)
            if expression.end_index is not None:
                start_index = expression.start_index
                inner_start_index = function_match.end()
                close_paren_index = expression.end_index - 1
                function_argument = file_contents[inner_start_index:close_paren_index].strip()
                if is_argument_safe is not None and is_caller_safe is None:
                    is_violation = is_argument_safe(function_argument) is False
                elif is_caller_safe is not None and is_argument_safe is None:
                    line_start_index = StringLines(file_contents).index_to_line_start_index(start_index)
                    caller_line_start = file_contents[line_start_index:start_index]
                    is_violation = is_caller_safe(caller_line_start) is False
                else:
                    raise ValueError("Must supply either is_argument_safe, or is_caller_safe, but not both.")
            if is_violation:
                results.violations.append(ExpressionRuleViolation(rule, expression))

    def _is_jquery_argument_safe_html_utils_call(self, argument):
        """
        Checks that the argument sent to a jQuery DOM insertion function is a
        safe call to HtmlUtils.

        A safe argument is of the form:
        - HtmlUtils.xxx(anything).toString()
        - edx.HtmlUtils.xxx(anything).toString()

        Arguments:
            argument: The argument sent to the jQuery function (e.g.
            append(argument)).

        Returns:
            True if the argument is safe, and False otherwise.

        """
        # match on HtmlUtils.xxx().toString() or edx.HtmlUtils
        match = re.search(r"(?:edx\.)?HtmlUtils\.[a-zA-Z0-9]+\(.*\)\.toString\(\)", argument)
        return match is not None and match.group() == argument

    def _is_jquery_argument_safe(self, argument):
        """
        Check the argument sent to a jQuery DOM insertion function (e.g.
        append()) to check if it is safe.

        Safe arguments include:
        - the argument can end with ".el", ".$el" (with no concatenation)
        - the argument can be a single variable ending in "El" or starting with
            "$". For example, "testEl" or "$test".
        - the argument can be a single string literal with no HTML tags
        - the argument can be a call to $() with the first argument a string
            literal with a single HTML tag.  For example, ".append($('<br/>'))"
            or ".append($('<br/>'))".
        - the argument can be a call to HtmlUtils.xxx(html).toString()

        Arguments:
            argument: The argument sent to the jQuery function (e.g.
            append(argument)).

        Returns:
            True if the argument is safe, and False otherwise.

        """
        match_variable_name = re.search("[_$a-zA-Z]+[_$a-zA-Z0-9]*", argument)
        if match_variable_name is not None and match_variable_name.group() == argument:
            if argument.endswith('El') or argument.startswith('$'):
                return True
        elif argument.startswith('"') or argument.startswith("'"):
            # a single literal string with no HTML is ok
            # 1. it gets rid of false negatives for non-jquery calls (e.g. graph.append("g"))
            # 2. JQuery will treat this as a plain text string and will escape any & if needed.
            string = ParseString(argument, 0, len(argument))
            if string.string == argument and "<" not in argument:
                return True
        elif argument.startswith('$('):
            # match on JQuery calls with single string and single HTML tag
            # Examples:
            #    $("<span>")
            #    $("<div/>")
            #    $("<div/>", {...})
            match = re.search(r"""\$\(\s*['"]<[a-zA-Z0-9]+\s*[/]?>['"]\s*[,)]""", argument)
            if match is not None:
                return True
        elif self._is_jquery_argument_safe_html_utils_call(argument):
            return True
        # check rules that shouldn't use concatenation
        elif "+" not in argument:
            if argument.endswith('.el') or argument.endswith('.$el'):
                return True
        return False

    def _is_jquery_html_argument_safe(self, argument):
        """
        Check the argument sent to the jQuery html() function to check if it is
        safe.

        Safe arguments to html():
        - no argument (i.e. getter rather than setter)
        - empty string is safe
        - the argument can be a call to HtmlUtils.xxx(html).toString()

        Arguments:
            argument: The argument sent to html() in code (i.e. html(argument)).

        Returns:
            True if the argument is safe, and False otherwise.

        """
        if argument == "" or argument == "''" or argument == '""':
            return True
        elif self._is_jquery_argument_safe_html_utils_call(argument):
            return True
        return False

    def _is_jquery_insert_caller_safe(self, caller_line_start):
        """
        Check that the caller of a jQuery DOM insertion function that takes a
        target is safe (e.g. thisEl.appendTo(target)).

        If original line was::

            draggableObj.iconEl.appendTo(draggableObj.containerEl);

        Parameter caller_line_start would be:

            draggableObj.iconEl

        Safe callers include:
        - the caller can be ".el", ".$el"
        - the caller can be a single variable ending in "El" or starting with
            "$". For example, "testEl" or "$test".

        Arguments:
            caller_line_start: The line leading up to the jQuery function call.

        Returns:
            True if the caller is safe, and False otherwise.

        """
        # matches end of line for caller, which can't itself be a function
        caller_match = re.search(r"(?:\s*|[.])([_$a-zA-Z]+[_$a-zA-Z0-9])*$", caller_line_start)
        if caller_match is None:
            return False
        caller = caller_match.group(1)
        if caller is None:
            return False
        elif caller.endswith('El') or caller.startswith('$'):
            return True
        elif caller == 'el' or caller == 'parentNode':
            return True
        return False

    def _check_concat_with_html(self, file_contents, rule, results):
        """
        Checks that strings with HTML are not concatenated

        Arguments:
            file_contents: The contents of the JavaScript file.
            rule: The rule that was violated if this fails.
            results: A file results objects to which violations will be added.

        """
        lines = StringLines(file_contents)
        last_expression = None
        # Match quoted strings that starts with '<' or ends with '>'.
        regex_string_with_html = r"""
            {quote}                             # Opening quote.
                (
                   \s*<                         # Starts with '<' (ignoring spaces)
                   ([^{quote}]|[\\]{quote})*    # followed by anything but a closing quote.
                |                               # Or,
                   ([^{quote}]|[\\]{quote})*    # Anything but a closing quote
                   >\s*                         # ending with '>' (ignoring spaces)
                )
            {quote}                             # Closing quote.
        """
        # Match single or double quote.
        regex_string_with_html = "({}|{})".format(
            regex_string_with_html.format(quote="'"),
            regex_string_with_html.format(quote='"'),
        )
        # Match quoted HTML strings next to a '+'.
        regex_concat_with_html = re.compile(
            r"(\+\s*{string_with_html}|{string_with_html}\s*\+)".format(
                string_with_html=regex_string_with_html,
            ),
            re.VERBOSE
        )
        for match in regex_concat_with_html.finditer(file_contents):
            found_new_violation = False
            if last_expression is not None:
                last_line = lines.index_to_line_number(last_expression.start_index)
                # check if violation should be expanded to more of the same line
                if last_line == lines.index_to_line_number(match.start()):
                    last_expression = Expression(
                        last_expression.start_index, match.end(), template=file_contents
                    )
                else:
                    results.violations.append(ExpressionRuleViolation(
                        rule, last_expression
                    ))
                    found_new_violation = True
            else:
                found_new_violation = True
            if found_new_violation:
                last_expression = Expression(
                    match.start(), match.end(), template=file_contents
                )

        # add final expression
        if last_expression is not None:
            results.violations.append(ExpressionRuleViolation(
                rule, last_expression
            ))


class PythonLinter(BaseLinter):
    """
    The linter for Python files.

    The current implementation of the linter does naive Python parsing. It does
    not use the parser. One known issue is that parsing errors found inside a
    docstring need to be disabled, rather than being automatically skipped.
    Skipping docstrings is an enhancement that could be added.
    """

    LINE_COMMENT_DELIM = "#"

    ruleset = RuleSet(
        python_parse_error='python-parse-error',
        python_custom_escape='python-custom-escape',

        # The Visitor classes are python-specific and should be moved into the PythonLinter once they have
        # been decoupled from the MakoTemplateLinter.
    ) + visitors.ruleset

    def __init__(self, skip_dirs=None):
        """
        Init method.
        """
        super().__init__()
        self._skip_python_dirs = skip_dirs or ()

    def process_file(self, directory, file_name):
        """
        Process file to determine if it is a Python file and
        if it is safe.

        Arguments:
            directory (string): The directory of the file to be checked
            file_name (string): A filename for a potential Python file

        Returns:
            The file results containing any violations.

        """
        file_full_path = os.path.normpath(directory + '/' + file_name)
        results = FileResults(file_full_path)

        if not results.is_file:
            return results

        if file_name.lower().endswith('.py') is False:
            return results

        # skip tests.py files
        # TODO: Add configuration for files and paths
        if file_name.lower().endswith('tests.py'):
            return results

        # skip this linter code (i.e. xss_linter.py)
        if file_name == os.path.basename(__file__):
            return results

        if not self._is_valid_directory(self._skip_python_dirs, directory):
            return results

        return self._load_and_check_file_is_safe(file_full_path, self.check_python_file_is_safe, results)

    def check_python_file_is_safe(self, file_contents, results):
        """
        Checks for violations in a Python file.

        Arguments:
            file_contents: The contents of the Python file.
            results: A file results objects to which violations will be added.

        """
        root_node = self.parse_python_code(file_contents, results)
        self.check_python_code_is_safe(file_contents, root_node, results)
        # Check rules specific to .py files only
        # Note that in template files, the scope is different, so you can make
        # different assumptions.
        if root_node is not None:
            # check format() rules that can be run on outer-most format() calls
            visitor = visitors.OuterFormatVisitor(file_contents, results)
            visitor.visit(root_node)
        results.prepare_results(file_contents, line_comment_delim=self.LINE_COMMENT_DELIM)

    def check_python_code_is_safe(self, python_code, root_node, results):
        """
        Checks for violations in Python code snippet. This can also be used for
        Python that appears in files other than .py files, like in templates.

        Arguments:
            python_code: The contents of the Python code.
            root_node: The root node of the Python code parsed by AST.
            results: A file results objects to which violations will be added.

        """
        if root_node is not None:
            # check illegal concatenation and interpolation
            visitor = visitors.AllNodeVisitor(python_code, results)
            visitor.visit(root_node)
        # check rules parse with regex
        self._check_custom_escape(python_code, results)

    def parse_python_code(self, python_code, results):
        """
        Parses Python code.

        Arguments:
            python_code: The Python code to be parsed.

        Returns:
            The root node that was parsed, or None for SyntaxError.

        """
        python_code = self._strip_file_encoding(python_code)
        try:
            return ast.parse(python_code)

        except SyntaxError as e:
            if e.offset is None:
                expression = Expression(0)
            else:
                lines = StringLines(python_code)
                line_start_index = lines.line_number_to_start_index(e.lineno)
                expression = Expression(line_start_index + e.offset)
            results.violations.append(ExpressionRuleViolation(
                self.ruleset.python_parse_error, expression
            ))
            return None

    def _strip_file_encoding(self, file_contents):
        """
        Removes file encoding from file_contents because the file was already
        read into Unicode, and the AST parser complains.

        Arguments:
            file_contents: The Python file contents.

        Returns:
            The Python file contents with the encoding stripped.
        """
        # PEP-263 Provides Regex for Declaring Encoding
        # Example: -*- coding: <encoding name> -*-
        # This is only allowed on the first two lines, and it must be stripped
        # before parsing, because we have already read into Unicode and the
        # AST parser complains.
        encoding_regex = re.compile(r"^[ \t\v]*#.*?coding[:=][ \t]*([-_.a-zA-Z0-9]+)")
        encoding_match = encoding_regex.search(file_contents)
        # If encoding comment not found on first line, search second line.
        if encoding_match is None:
            lines = StringLines(file_contents)
            if lines.line_count() >= 2:
                encoding_match = encoding_regex.search(lines.line_number_to_line(2))
        # If encoding was found, strip it
        if encoding_match is not None:
            file_contents = file_contents.replace(encoding_match.group(), '#', 1)
        return file_contents

    def _check_custom_escape(self, file_contents, results):
        """
        Checks for custom escaping calls, rather than using a standard escaping
        method.

        Arguments:
            file_contents: The contents of the Python file
            results: A list of results into which violations will be added.

        """
        for match in re.finditer("(<.*&lt;|&lt;.*<)", file_contents):
            expression = Expression(match.start(), match.end())
            results.violations.append(ExpressionRuleViolation(
                self.ruleset.python_custom_escape, expression
            ))


class MakoTemplateLinter(BaseLinter):
    """
    The linter for Mako template files.
    """
    LINE_COMMENT_DELIM = "##"

    ruleset = RuleSet(
        mako_missing_default='mako-missing-default',
        mako_multiple_page_tags='mako-multiple-page-tags',
        mako_unparseable_expression='mako-unparseable-expression',
        mako_unwanted_html_filter='mako-unwanted-html-filter',
        mako_invalid_html_filter='mako-invalid-html-filter',
        mako_invalid_js_filter='mako-invalid-js-filter',
        mako_js_missing_quotes='mako-js-missing-quotes',
        mako_js_html_string='mako-js-html-string',
        mako_html_entities='mako-html-entities',
        mako_unknown_context='mako-unknown-context',

        # NOTE The MakoTemplateLinter directly checks for python_wrap_html and directly
        # instantiates Visitor instances to check for python issues. This logic should
        # be moved into the PythonLinter. The MakoTemplateLinter should only check for
        # Mako-specific issues.
        python_wrap_html='python-wrap-html',
    ) + visitors.ruleset

    def __init__(self, javascript_linter, python_linter, skip_dirs=None):
        """
        Init method.
        """
        super().__init__()
        self.javascript_linter = javascript_linter
        self.python_linter = python_linter
        self.ruleset = self.ruleset + self.javascript_linter.ruleset + self.python_linter.ruleset
        self._skip_mako_dirs = skip_dirs or ()

    def process_file(self, directory, file_name):
        """
        Process file to determine if it is a Mako template file and
        if it is safe.

        Arguments:
            directory (string): The directory of the file to be checked
            file_name (string): A filename for a potential Mako file

        Returns:
            The file results containing any violations.

        """
        mako_file_full_path = os.path.normpath(directory + '/' + file_name)
        results = FileResults(mako_file_full_path)

        if not results.is_file:
            return results

        if not self._is_valid_directory(directory):
            return results

        # TODO: When safe-by-default is turned on at the platform level, will we:
        # 1. Turn it on for .html only, or
        # 2. Turn it on for all files, and have different rulesets that have
        #    different rules of .xml, .html, .js, .txt Mako templates (e.g. use
        #    the n filter to turn off h for some of these)?
        # For now, we only check .html and .xml files
        if not (file_name.lower().endswith('.html') or file_name.lower().endswith('.xml')):
            return results

        return self._load_and_check_file_is_safe(mako_file_full_path, self._check_mako_file_is_safe, results)

    def _is_valid_directory(self, directory):
        """
        Determines if the provided directory is a directory that could contain
        Mako template files that need to be linted.

        Arguments:
            directory: The directory to be linted.

        Returns:
            True if this directory should be linted for Mako template violations
            and False otherwise.
        """
        if is_skip_dir(self._skip_mako_dirs, directory):
            return False

        # TODO: This is an imperfect guess concerning the Mako template
        # directories. This needs to be reviewed before turning on safe by
        # default at the platform level.
        if ('/templates/' in directory) or directory.endswith('/templates'):
            return True

        return False

    def _check_mako_file_is_safe(self, mako_template, results):
        """
        Checks for violations in a Mako template.

        Arguments:
            mako_template: The contents of the Mako template.
            results: A file results objects to which violations will be added.

        """
        if self._is_django_template(mako_template):
            return
        has_page_default = self._has_page_default(mako_template, results)
        self._check_mako_expressions(mako_template, has_page_default, results)
        self._check_mako_python_blocks(mako_template, has_page_default, results)
        results.prepare_results(mako_template, line_comment_delim=self.LINE_COMMENT_DELIM)

    def _is_django_template(self, mako_template):
        """
            Determines if the template is actually a Django template.

        Arguments:
            mako_template: The template code.

        Returns:
            True if this is really a Django template, and False otherwise.

        """
        if re.search('({%.*%})|({{.*}})|({#.*#})', mako_template) is not None:
            return True
        return False

    def _get_page_tag_count(self, mako_template):
        """
        Determines the number of page expressions in the Mako template. Ignores
        page expressions that are commented out.

        Arguments:
            mako_template: The contents of the Mako template.

        Returns:
            The number of page expressions
        """
        count = len(re.findall('<%page ', mako_template, re.IGNORECASE))
        count_commented = len(re.findall(r'##\s+<%page ', mako_template, re.IGNORECASE))
        return max(0, count - count_commented)

    def _has_page_default(self, mako_template, results):
        """
        Checks if the Mako template contains the page expression marking it as
        safe by default.

        Arguments:
            mako_template: The contents of the Mako template.
            results: A list of results into which violations will be added.

        Side effect:
            Adds violations regarding page default if necessary

        Returns:
            True if the template has the page default, and False otherwise.

        """
        page_tag_count = self._get_page_tag_count(mako_template)
        # check if there are too many page expressions
        if 2 <= page_tag_count:
            results.violations.append(RuleViolation(self.ruleset.mako_multiple_page_tags))
            return False
        # make sure there is exactly 1 page expression, excluding commented out
        # page expressions, before proceeding
        elif page_tag_count != 1:
            results.violations.append(RuleViolation(self.ruleset.mako_missing_default))
            return False
        # check that safe by default (h filter) is turned on
        page_h_filter_regex = re.compile('<%page[^>]*expression_filter=(?:"h"|\'h\')[^>]*/>')
        page_match = page_h_filter_regex.search(mako_template)
        if not page_match:
            results.violations.append(RuleViolation(self.ruleset.mako_missing_default))
        return page_match

    def _check_mako_expressions(self, mako_template, has_page_default, results):
        """
        Searches for Mako expressions and then checks if they contain
        violations, including checking JavaScript contexts for JavaScript
        violations.

        Arguments:
            mako_template: The contents of the Mako template.
            has_page_default: True if the page is marked as default, False
                otherwise.
            results: A list of results into which violations will be added.

        """
        expressions = self._find_mako_expressions(mako_template)
        contexts = self._get_contexts(mako_template)
        self._check_javascript_contexts(mako_template, contexts, results)
        for expression in expressions:
            if expression.end_index is None:
                results.violations.append(ExpressionRuleViolation(
                    self.ruleset.mako_unparseable_expression, expression
                ))
                continue

            context = self._get_context(contexts, expression.start_index)
            self._check_expression_and_filters(mako_template, expression, context, has_page_default, results)

    def _check_javascript_contexts(self, mako_template, contexts, results):
        """
        Lint the JavaScript contexts for JavaScript violations inside a Mako
        template.

        Arguments:
            mako_template: The contents of the Mako template.
            contexts: A list of context dicts with 'type' and 'index'.
            results: A list of results into which violations will be added.

        Side effect:
            Adds JavaScript violations to results.
        """
        javascript_start_index = None
        for context in contexts:
            if context['type'] == 'javascript':
                if javascript_start_index is None:
                    javascript_start_index = context['index']
            else:
                if javascript_start_index is not None:
                    javascript_end_index = context['index']
                    javascript_code = mako_template[javascript_start_index:javascript_end_index]
                    self._check_javascript_context(javascript_code, javascript_start_index, results)
                    javascript_start_index = None
        if javascript_start_index is not None:
            javascript_code = mako_template[javascript_start_index:]
            self._check_javascript_context(javascript_code, javascript_start_index, results)

    def _check_javascript_context(self, javascript_code, start_offset, results):
        """
        Lint a single JavaScript context for JavaScript violations inside a Mako
        template.

        Arguments:
            javascript_code: The template contents of the JavaScript context.
            start_offset: The offset of the JavaScript context inside the
                original Mako template.
            results: A list of results into which violations will be added.

        Side effect:
            Adds JavaScript violations to results.

        """
        javascript_results = FileResults("")
        self.javascript_linter.check_javascript_file_is_safe(javascript_code, javascript_results)
        self._shift_and_add_violations(javascript_results, start_offset, results)

    def _check_mako_python_blocks(self, mako_template, has_page_default, results):
        """
        Searches for Mako python blocks and checks if they contain
        violations.

        Arguments:
            mako_template: The contents of the Mako template.
            has_page_default: True if the page is marked as default, False
                otherwise.
            results: A list of results into which violations will be added.

        """
        # Finds Python blocks such as <% ... %>, skipping other Mako start tags
        # such as <%def> and <%page>.
        python_block_regex = re.compile(r'<%\s(?P<code>.*?)%>', re.DOTALL)

        for python_block_match in python_block_regex.finditer(mako_template):
            self._check_expression_python(
                python_code=python_block_match.group('code'),
                start_offset=(python_block_match.start() + len('<% ')),
                has_page_default=has_page_default,
                results=results
            )

    def _check_expression_python(self, python_code, start_offset, has_page_default, results):
        """
        Lint the Python inside a single Python expression in a Mako template.

        Arguments:
            python_code: The Python contents of an expression.
            start_offset: The offset of the Python content inside the original
                Mako template.
            has_page_default: True if the page is marked as default, False
                otherwise.
            results: A list of results into which violations will be added.

        Side effect:
            Adds Python violations to results.

        """
        python_results = FileResults("")

        # Dedent expression internals so it is parseable.
        # Note that the final columns reported could be off somewhat.
        adjusted_python_code = textwrap.dedent(python_code)
        first_letter_match = re.search(r'\w', python_code)
        adjusted_first_letter_match = re.search(r'\w', adjusted_python_code)
        if first_letter_match is not None and adjusted_first_letter_match is not None:
            start_offset += (first_letter_match.start() - adjusted_first_letter_match.start())
        python_code = adjusted_python_code

        root_node = self.python_linter.parse_python_code(python_code, python_results)
        self.python_linter.check_python_code_is_safe(python_code, root_node, python_results)
        # Check mako expression specific Python rules.
        if root_node is not None:
            visitor = visitors.HtmlStringVisitor(python_code, python_results, True)
            visitor.visit(root_node)
            for unsafe_html_string_node in visitor.unsafe_html_string_nodes:
                python_results.violations.append(ExpressionRuleViolation(
                    self.ruleset.python_wrap_html, visitor.node_to_expression(unsafe_html_string_node)
                ))
            if has_page_default:
                for over_escaped_entity_string_node in visitor.over_escaped_entity_string_nodes:
                    python_results.violations.append(ExpressionRuleViolation(
                        self.ruleset.mako_html_entities, visitor.node_to_expression(over_escaped_entity_string_node)
                    ))
        python_results.prepare_results(python_code, line_comment_delim=self.LINE_COMMENT_DELIM)
        self._shift_and_add_violations(python_results, start_offset, results)

    def _shift_and_add_violations(self, other_linter_results, start_offset, results):
        """
        Adds results from a different linter to the Mako results, after shifting
        the offset into the original Mako template.

        Arguments:
            other_linter_results: Results from another linter.
            start_offset: The offset of the linted code, a part of the template,
                inside the original Mako template.
            results: A list of results into which violations will be added.

        Side effect:
            Adds violations to results.

        """
        # translate the violations into the proper location within the original
        # Mako template
        for violation in other_linter_results.violations:
            expression = violation.expression
            expression.start_index += start_offset
            if expression.end_index is not None:
                expression.end_index += start_offset
            results.violations.append(ExpressionRuleViolation(violation.rule, expression))

    def _check_expression_and_filters(self, mako_template, expression, context, has_page_default, results):
        """
        Checks that the filters used in the given Mako expression are valid
        for the given context. Adds violation to results if there is a problem.

        Arguments:
            mako_template: The contents of the Mako template.
            expression: A Mako Expression.
            context: The context of the page in which the expression was found
                (e.g. javascript, html).
            has_page_default: True if the page is marked as default, False
                otherwise.
            results: A list of results into which violations will be added.

        """
        if context == 'unknown':
            results.violations.append(ExpressionRuleViolation(
                self.ruleset.mako_unknown_context, expression
            ))
            return

        # Example: finds "| n, h}" when given "${x | n, h}"
        filters_regex = re.compile(r'\|([.,\w\s]*)\}')
        filters_match = filters_regex.search(expression.expression)

        # Check Python code inside expression.
        if filters_match is None:
            python_code = expression.expression[2:-1]
        else:
            python_code = expression.expression[2:filters_match.start()]
        self._check_expression_python(python_code, expression.start_index + 2, has_page_default, results)

        # Check filters.
        if filters_match is None:
            if context == 'javascript':
                results.violations.append(ExpressionRuleViolation(
                    self.ruleset.mako_invalid_js_filter, expression
                ))
            return
        filters = filters_match.group(1).replace(" ", "").split(",")
        if filters == ['n', 'decode.utf8']:
            # {x | n, decode.utf8} is valid in any context
            pass
        elif context == 'html':
            if filters == ['h']:
                if has_page_default:
                    # suppress this violation if the page default hasn't been set,
                    # otherwise the template might get less safe
                    results.violations.append(ExpressionRuleViolation(
                        self.ruleset.mako_unwanted_html_filter, expression
                    ))
            elif filters == ['n', 'strip_all_tags_but_br']:
                # {x | n,  strip_all_tags_but_br} is valid in html context
                pass
            else:
                results.violations.append(ExpressionRuleViolation(
                    self.ruleset.mako_invalid_html_filter, expression
                ))
        elif context == 'javascript':
            self._check_js_expression_not_with_html(mako_template, expression, results)
            if filters == ['n', 'dump_js_escaped_json']:
                # {x | n, dump_js_escaped_json} is valid
                pass
            elif filters == ['n', 'js_escaped_string']:
                # {x | n, js_escaped_string} is valid, if surrounded by quotes
                self._check_js_string_expression_in_quotes(mako_template, expression, results)
            else:
                results.violations.append(ExpressionRuleViolation(
                    self.ruleset.mako_invalid_js_filter, expression
                ))

    def _check_js_string_expression_in_quotes(self, mako_template, expression, results):
        """
        Checks that a Mako expression using js_escaped_string is surrounded by
        quotes.

        Arguments:
            mako_template: The contents of the Mako template.
            expression: A Mako Expression.
            results: A list of results into which violations will be added.
        """
        parse_string = self._find_string_wrapping_expression(mako_template, expression)
        if parse_string is None:
            results.violations.append(ExpressionRuleViolation(
                self.ruleset.mako_js_missing_quotes, expression
            ))

    def _check_js_expression_not_with_html(self, mako_template, expression, results):
        """
        Checks that a Mako expression in a JavaScript context does not appear in
        a string that also contains HTML.

        Arguments:
            mako_template: The contents of the Mako template.
            expression: A Mako Expression.
            results: A list of results into which violations will be added.
        """
        parse_string = self._find_string_wrapping_expression(mako_template, expression)
        if parse_string is not None and re.search('[<>]', parse_string.string) is not None:
            results.violations.append(ExpressionRuleViolation(
                self.ruleset.mako_js_html_string, expression
            ))

    def _find_string_wrapping_expression(self, mako_template, expression):
        """
        Finds the string wrapping the Mako expression if there is one.

        Arguments:
            mako_template: The contents of the Mako template.
            expression: A Mako Expression.

        Returns:
            ParseString representing a scrubbed version of the wrapped string,
            where the Mako expression was replaced with "${...}", if a wrapped
            string was found.  Otherwise, returns None if none found.
        """
        lines = StringLines(mako_template)
        start_index = lines.index_to_line_start_index(expression.start_index)
        if expression.end_index is not None:
            end_index = lines.index_to_line_end_index(expression.end_index)
        else:
            return None
        # scrub out the actual expression so any code inside the expression
        # doesn't interfere with rules applied to the surrounding code (i.e.
        # checking JavaScript).
        scrubbed_lines = "".join((
            mako_template[start_index:expression.start_index],
            "${...}",
            mako_template[expression.end_index:end_index]
        ))
        adjusted_start_index = expression.start_index - start_index
        start_index = 0
        while True:
            parse_string = ParseString(scrubbed_lines, start_index, len(scrubbed_lines))
            # check for validly parsed string
            if (parse_string.start_index is not None and parse_string.end_index is not None) \
                    and (0 <= parse_string.start_index < parse_string.end_index):
                # check if expression is contained in the given string
                if parse_string.start_index < adjusted_start_index < parse_string.end_index:
                    return parse_string
                else:
                    # move to check next string
                    start_index = parse_string.end_index
            else:
                break
        return None

    def _get_contexts(self, mako_template):
        """
        Returns a data structure that represents the indices at which the
        template changes from HTML context to JavaScript and back.

        Return:
            A list of dicts where each dict contains:
                - index: the index of the context.
                - type: the context type (e.g. 'html' or 'javascript').
        """
        contexts_re = re.compile(
            r"""
                <script.*?(?<!/)> |  # script tag start
                </script> |  # script tag end
                <%static:require_module(_async)?.*?(?<!/)> |  # require js script tag start (optionally the _async version)
                </%static:require_module(_async)?> | # require js script tag end (optionally the _async version)
                <%static:webpack.*(?<!/)> |  # webpack script tag start
                </%static:webpack> | # webpack script tag end
                <%block[ ]*name=['"]requirejs['"]\w*(?<!/)> |  # require js tag start
                </%block>  # require js tag end
            """,
            re.VERBOSE | re.IGNORECASE
        )
        media_type_re = re.compile(r"""type=['"].*?['"]""", re.IGNORECASE)

        contexts = [{'index': 0, 'type': 'html'}]
        javascript_types = [
            'text/javascript', 'text/ecmascript', 'application/ecmascript', 'application/javascript',
            'text/x-mathjax-config', 'json/xblock-args', 'application/json',
        ]
        html_types = ['text/template']
        for context in contexts_re.finditer(mako_template):
            match_string = context.group().lower()
            if match_string.startswith("<script"):
                match_type = media_type_re.search(match_string)
                context_type = 'javascript'
                if match_type is not None:
                    # get media type (e.g. get text/javascript from
                    # type="text/javascript")
                    match_type = match_type.group()[6:-1].lower()
                    if match_type in html_types:
                        context_type = 'html'
                    elif match_type not in javascript_types:
                        context_type = 'unknown'
                contexts.append({'index': context.end(), 'type': context_type})
            elif match_string.startswith("</"):
                contexts.append({'index': context.start(), 'type': 'html'})
            else:
                contexts.append({'index': context.end(), 'type': 'javascript'})

        return contexts

    def _get_context(self, contexts, index):
        """
        Gets the context (e.g. javascript, html) of the template at the given
        index.

        Arguments:
            contexts: A list of dicts where each dict contains the 'index' of the context
                and the context 'type' (e.g. 'html' or 'javascript').
            index: The index for which we want the context.

        Returns:
             The context (e.g. javascript or html) for the given index.
        """
        current_context = contexts[0]['type']
        for context in contexts:
            if context['index'] <= index:
                current_context = context['type']
            else:
                break
        return current_context

    def _find_mako_expressions(self, mako_template):
        """
        Finds all the Mako expressions in a Mako template and creates a list
        of dicts for each expression.

        Arguments:
            mako_template: The content of the Mako template.

        Returns:
            A list of Expressions.

        """
        start_delim = '${'
        start_index = 0
        expressions = []

        while True:
            start_index = mako_template.find(start_delim, start_index)
            if start_index < 0:
                break

            # If start of mako expression is commented out, skip it.
            uncommented_start_index = self._uncommented_start_index(mako_template, start_index)
            if uncommented_start_index != start_index:
                start_index = uncommented_start_index
                continue

            result = self._find_closing_char_index(
                start_delim, '{', '}', mako_template, start_index=start_index + len(start_delim)
            )
            if result is None:
                expression = Expression(start_index)
                # for parsing error, restart search right after the start of the
                # current expression
                start_index = start_index + len(start_delim)
            else:
                close_char_index = result['close_char_index']
                expression = mako_template[start_index:close_char_index + 1]
                expression = Expression(
                    start_index,
                    end_index=close_char_index + 1,
                    template=mako_template,
                    start_delim=start_delim,
                    end_delim='}',
                    strings=result['strings'],
                )
                # restart search after the current expression
                start_index = expression.end_index
            expressions.append(expression)
        return expressions


class DjangoTemplateLinter(BaseLinter):
    """
    The linter for Django template files
    """
    LINE_COMMENT_DELIM = "{#"

    ruleset = RuleSet(
        django_trans_missing_escape='django-trans-missing-escape',
        django_trans_invalid_escape_filter='django-trans-invalid-escape-filter',
        django_trans_escape_variable_mismatch='django-trans-escape-variable-mismatch',
        django_blocktrans_missing_escape_filter='django-blocktrans-missing-escape-filter',
        django_blocktrans_parse_error='django-blocktrans-parse-error',
        django_blocktrans_escape_filter_parse_error='django-blocktrans-escape-filter-parse-error',
        django_html_interpolation_missing_safe_filter='django-html-interpolation-missing-safe-filter',
        django_html_interpolation_missing='django-html-interpolation-missing',
        django_html_interpolation_invalid_tag='django-html-interpolation-invalid-tag',
    )

    def __init__(self, skip_dirs=None):
        """
        Init method.
        """
        super().__init__()
        self._skip_django_dirs = skip_dirs or ()

    def process_file(self, directory, file_name):
        """
        Process file to determine if it is a Django template file and
        if it is safe.
        Arguments:
            directory (string): The directory of the file to be checked
            file_name (string): A filename for a potential Django file
        Returns:
            The file results containing any violations.
        """
        django_file_full_path = os.path.normpath(directory + '/' + file_name)
        results = FileResults(django_file_full_path)

        if not results.is_file:
            return results

        if not self._is_valid_directory(directory):
            return results

        if not (file_name.lower().endswith('.html')):
            return results

        return self._load_and_check_file_is_safe(django_file_full_path, self._check_django_file_is_safe, results)

    def _is_valid_directory(self, directory):
        """
        Determines if the provided directory is a directory that could contain
        Django template files that need to be linted.
        Arguments:
            directory: The directory to be linted.
        Returns:
            True if this directory should be linted for Django template violations
            and False otherwise.
        """
        if is_skip_dir(self._skip_django_dirs, directory):
            return False

        if ('/templates/' in directory) or directory.endswith('/templates'):
            return True

        return False

    def _is_django_template(self, django_template):
        """
            Determines if the template is actually a Django template.
        Arguments:
            mako_template: The template code.
        Returns:
            True if this is really a Django template, and False otherwise.
        """
        if re.search('({%.*%})|({{.*}})|({#.*#})', django_template) is not None:
            return True
        return False

    def _check_django_file_is_safe(self, django_template, results):
        if not self._is_django_template(django_template):
            return
        self._check_django_expression(django_template, results)
        results.prepare_results(django_template, line_comment_delim=self.LINE_COMMENT_DELIM)

    def _check_django_expression(self, django_template, results):
        """
        Searches for django trans and blocktrans expression and then checks
        if they contain violations
        Arguments:
            django_template: The contents of the Django template.
            results: A list of results into which violations will be added.
        """
        expressions = []
        self._find_django_expressions(django_template, results, expressions)
        for expr in expressions:
            expr.validate_expression(django_template, expressions)

    def _find_django_expressions(self, django_template, results, expressions):
        """
        Finds all the Django trans/blocktrans expressions in a Django template
        and creates a list of dicts for each expression.
        Arguments:
            django_template: The content of the Django template.
        Returns:
            A list of Expressions.
        """

        comments = list(re.finditer(r'{% comment .*%}', django_template, re.I))
        endcomments = list(re.finditer(r'{% endcomment .*%}', django_template, re.I))

        trans_iterator = re.finditer(r'{% trans .*?%}', django_template, re.I)
        for t in trans_iterator:
            if self._check_expression_not_commented(t, comments, endcomments):
                continue
            trans_expr = TransExpression(self.ruleset, results, t.start(), t.end(),
                                         start_delim='{%', end_delim='%}',
                                         template=django_template)
            if trans_expr:
                expressions.append(trans_expr)

        block_trans_iterator = re.finditer(r'{% blocktrans .*?%}', django_template, re.I)
        for bt in block_trans_iterator:
            if self._check_expression_not_commented(bt, comments, endcomments):
                continue
            trans_expr = BlockTransExpression(self.ruleset, results, bt.start(), bt.end(),
                                              start_delim='{%', end_delim='%}',
                                              template=django_template)
            if trans_expr:
                expressions.append(trans_expr)

        interpolation_iterator = re.finditer(r'{% interpolate_html .*?%}', django_template, re.I)
        for it in interpolation_iterator:
            if self._check_expression_not_commented(it, comments, endcomments):
                continue
            trans_expr = HtmlInterpolateExpression(self.ruleset, results,
                                                   it.start(), it.end(),
                                                   start_delim='{%', end_delim='%}',
                                                   template=django_template)
            if trans_expr:
                expressions.append(trans_expr)

    def _check_expression_not_commented(self, expr, comments, endcomments):

        for i in range(len(endcomments)):
            start_comment = comments[i]
            end_comment = endcomments[i]

            if (expr.start() >= start_comment.start()) and \
                    (expr.start() <= end_comment.start()):
                return True
