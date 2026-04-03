"""
Utility classes for reporting linter results.
"""


import json
import os
import re

from xsslint.utils import StringLines


class RuleViolation:
    """
    Base class representing a rule violation which can be used for reporting.
    """

    def __init__(self, rule):
        """
        Init method.

        Arguments:
            rule: The Rule which was violated.

        """
        self.rule = rule
        self.full_path = ''
        self.is_disabled = False

    def _mark_disabled(self, string, scope_start_string=False):
        """
        Performs the disable pragma search and marks the rule as disabled if a
        matching pragma is found.

        Pragma format::

            xss-lint: disable=violation-name,other-violation-name

        Arguments:
            string: The string of code in which to search for the pragma.
            scope_start_string: True if the pragma must be at the start of the
                string, False otherwise. The pragma is considered at the start
                of the string if it has a maximum of 5 non-whitespace characters
                preceding it.

        Side Effect:
            Sets self.is_disabled as appropriate based on whether the pragma is
            found.

        """
        pragma_match = re.search(r'xss-lint:\s*disable=([a-zA-Z,\- ]+)', string)
        if pragma_match is None:
            return
        if scope_start_string:
            spaces_count = string.count(' ', 0, pragma_match.start())
            non_space_count = pragma_match.start() - spaces_count
            if non_space_count > 5:
                return

        for disabled_rule in pragma_match.group(1).split(','):
            if disabled_rule.strip() == self.rule.rule_id:
                self.is_disabled = True
                return

    def sort_key(self):
        """
        Returns a key that can be sorted on
        """
        return (0, 0, self.rule.rule_id)

    def first_line(self):
        """
        Since a file level rule has no first line, returns empty string.
        """
        return ''

    def prepare_results(self, full_path, string_lines):
        """
        Preps this instance for results reporting.

        Arguments:
            full_path: Path of the file in violation.
            string_lines: A StringLines containing the contents of the file in
                violation.

        """
        self.full_path = full_path
        self._mark_disabled(string_lines.get_string())

    def print_results(self, _options, out):
        """
        Prints the results represented by this rule violation.

        Arguments:
            _options: ignored
            out: output file
        """
        print(f"{self.full_path}: {self.rule.rule_id}", file=out)


class ExpressionRuleViolation(RuleViolation):
    """
    A class representing a particular rule violation for expressions which
    contain more specific details of the location of the violation for reporting
    purposes.

    """

    def __init__(self, rule, expression):
        """
        Init method.

        Arguments:
            rule: The Rule which was violated.
            expression: The Expression that was in violation.

        """
        super().__init__(rule)
        self.expression = expression
        self.start_line = 0
        self.start_column = 0
        self.end_line = 0
        self.end_column = 0
        self.lines = []
        self.is_disabled = False

    def _mark_expression_disabled(self, string_lines):
        """
        Marks the expression violation as disabled if it finds the disable
        pragma anywhere on the first line of the violation, or at the start of
        the line preceding the violation.

        Pragma format::

            xss-lint: disable=violation-name,other-violation-name

        Examples::

            <% // xss-lint: disable=underscore-not-escaped %>
            <%= gettext('Single Line') %>

            <%= gettext('Single Line') %><% // xss-lint: disable=underscore-not-escaped %>

        Arguments:
            string_lines: A StringLines containing the contents of the file in
                violation.

        Side Effect:
            Sets self.is_disabled as appropriate based on whether the pragma is
            found.

        """
        # disable pragma can be at the start of the preceding line
        has_previous_line = self.start_line > 1
        if has_previous_line:
            line_to_check = string_lines.line_number_to_line(self.start_line - 1)
            self._mark_disabled(line_to_check, scope_start_string=True)
            if self.is_disabled:
                return

        # TODO: this should work at end of any line of the violation
        # disable pragma can be anywhere on the first line of the violation
        line_to_check = string_lines.line_number_to_line(self.start_line)
        self._mark_disabled(line_to_check, scope_start_string=False)

    def sort_key(self):
        """
        Returns a key that can be sorted on
        """
        return (self.start_line, self.start_column, self.rule.rule_id)

    def first_line(self):
        """
        Returns the initial line of code of the violation.
        """
        return self.lines[0]

    def prepare_results(self, full_path, string_lines):
        """
        Preps this instance for results reporting.

        Arguments:
            full_path: Path of the file in violation.
            string_lines: A StringLines containing the contents of the file in
                violation.

        """
        self.full_path = full_path
        start_index = self.expression.start_index
        self.start_line = string_lines.index_to_line_number(start_index)
        self.start_column = string_lines.index_to_column_number(start_index)
        end_index = self.expression.end_index
        if end_index is not None:
            self.end_line = string_lines.index_to_line_number(end_index)
            self.end_column = string_lines.index_to_column_number(end_index)
        else:
            self.end_line = self.start_line
            self.end_column = '?'
        for line_number in range(self.start_line, self.end_line + 1):
            self.lines.append(string_lines.line_number_to_line(line_number))
        self._mark_expression_disabled(string_lines)

    def print_results(self, options, out):
        """
        Prints the results represented by this rule violation.

        Arguments:
            options: A list of the following options:
                list_files: True to print only file names, and False to print
                    all violations.
                verbose: True for multiple lines of context, False single line.
            out: output file

        """
        if options['verbose']:
            end_line = self.end_line + 1
        else:
            end_line = self.start_line + 1
        for line_number in range(self.start_line, end_line):
            if line_number == self.start_line:
                column = self.start_column
                rule_id = self.rule.rule_id + ":"
            else:
                column = 1
                rule_id = " " * (len(self.rule.rule_id) + 1)
            line = self.lines[line_number - self.start_line].encode(encoding='utf-8')
            print("{}: {}:{}: {} {}".format(
                self.full_path,
                line_number,
                column,
                rule_id,
                line
            ), file=out)


class SummaryResults:
    """
    Contains the summary results for all violations.
    """

    def __init__(self, ruleset):
        """
        Init method.

        Arguments:
            ruleset: A RuleSet object containing all of the possible Rules.
        """
        self.total_violations = 0
        self.totals_by_rule = dict.fromkeys(
            [rule.rule_id for rule in ruleset.rules.values()], 0
        )

    def add_violation(self, violation):
        """
        Adds a violation to the summary details.

        Arguments:
            violation: The violation to add to the summary.

        """
        self.total_violations += 1
        self.totals_by_rule[violation.rule.rule_id] += 1

    def print_results(self, options, out):
        """
        Prints the results (i.e. violations) in this file.

        Arguments:
            options: A list of the following options:
                list_files: True to print only file names, and False to print
                    all violations.
                rule_totals: If True include totals by rule.
            out: output file

        """
        if options['list_files'] is False:
            if options['summary_format'] == 'json':
                self._print_json_format(options, out)
            else:
                self._print_eslint_format(options, out)

    def _print_eslint_format(self, options, out):
        """
        Implementation of print_results with eslint-style output.
        """
        if options['rule_totals']:
            max_rule_id_len = max(len(rule_id) for rule_id in self.totals_by_rule)
            print("", file=out)
            for rule_id in sorted(self.totals_by_rule.keys()):
                padding = " " * (max_rule_id_len - len(rule_id))
                print(f"{rule_id}: {padding}{self.totals_by_rule[rule_id]} violations", file=out)
            print("", file=out)

        # matches output of eslint for simplicity
        print("", file=out)
        print(f"{self.total_violations} violations total", file=out)

    def _print_json_format(self, options, out):
        """
        Implementation of print_results with JSON output.
        """
        print("", file=out)
        print("Violation counts:", file=out)
        data = {'rules': self.totals_by_rule}
        if options['rule_totals']:
            data['total'] = self.total_violations
        json.dump(data, fp=out, indent=4, sort_keys=True)
        print("", file=out)
        print(
            "If you've fixed some XSS issues and these numbers have gone down, "
            "you can use this to update scripts/xsslint_thresholds.json",
            file=out
        )


class FileResults:
    """
    Contains the results, or violations, for a file.
    """

    def __init__(self, full_path):
        """
        Init method.

        Arguments:
            full_path: The full path for this file.

        """
        self.full_path = full_path
        self.directory = os.path.dirname(full_path)
        self.is_file = os.path.isfile(full_path)
        self.violations = []

    def prepare_results(self, file_string, line_comment_delim=None):
        """
        Prepares the results for output for this file.

        Arguments:
            file_string: The string of content for this file.
            line_comment_delim: A string representing the start of a line
                comment. For example "##" for Mako and "//" for JavaScript.

        """
        string_lines = StringLines(file_string)
        for violation in self.violations:
            violation.prepare_results(self.full_path, string_lines)
        if line_comment_delim is not None:
            self._filter_commented_code(line_comment_delim)

    def print_results(self, options, summary_results, out):
        """
        Prints the results (i.e. violations) in this file.

        Arguments:
            options: A list of the following options:
                list_files: True to print only file names, and False to print
                    all violations.
            summary_results: A SummaryResults with a summary of the violations.
                verbose: True for multiple lines of context, False single line.
            out: output file

        Side effect:
            Updates the passed SummaryResults.

        """
        if options['list_files']:
            if self.violations is not None and 0 < len(self.violations):
                print(self.full_path, file=out)
        else:
            self.violations.sort(key=lambda violation: violation.sort_key())
            for violation in self.violations:
                if not violation.is_disabled:
                    violation.print_results(options, out)
                    summary_results.add_violation(violation)

    def _filter_commented_code(self, line_comment_delim):
        """
        Remove any violations that were found in commented out code.

        Arguments:
            line_comment_delim: A string representing the start of a line
                comment. For example "##" for Mako and "//" for JavaScript.

        """
        self.violations = [v for v in self.violations if not self._is_commented(v, line_comment_delim)]

    def _is_commented(self, violation, line_comment_delim):
        """
        Checks if violation line is commented out.

        Arguments:
            violation: The violation to check
            line_comment_delim: A string representing the start of a line
                comment. For example "##" for Mako and "//" for JavaScript.

        Returns:
            True if the first line of the violation is actually commented out,
            False otherwise.
        """
        if 'parse' in violation.rule.rule_id:
            # For parse rules, don't filter them because the comment could be a
            # part of the parse issue to begin with.
            return False
        else:
            return violation.first_line().lstrip().startswith(line_comment_delim)
