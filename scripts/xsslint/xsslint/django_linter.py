"""
Classes for Django Template Linting.
"""
import re

from xsslint.reporting import ExpressionRuleViolation
from xsslint.utils import Expression, StringLines


class TransExpression(Expression):
    """
        The expression handling trans tag
    """

    def __init__(self, ruleset, results, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.string_lines = StringLines(kwargs['template'])
        self.ruleset = ruleset
        self.results = results

    def validate_expression(self, template_file, expressions=None):
        """
        Validates trans tag expression for missing escaping filter

        Arguments:
            template_file: The content of the Django template.
            results: Violations to be generated.

        Returns:
            None
        """
        trans_expr = self.expression_inner

        # extracting translation string message
        trans_var_name_used, trans_expr_msg = self.process_translation_string(trans_expr)
        if not trans_var_name_used or not trans_expr_msg:
            return

        # Checking if trans tag has interpolated variables eg {} in translations string.
        # and testing for possible interpolate_html tag used for it.
        if self.check_string_interpolation(trans_expr_msg,
                                           trans_var_name_used,
                                           expressions,
                                           template_file):
            return

        escape_expr_start_pos, escape_expr_end_pos = self.find_filter_tag(template_file)
        if not escape_expr_start_pos or not escape_expr_end_pos:
            return

        self.process_escape_filter_tag(template_file=template_file,
                                       escape_expr_start_pos=escape_expr_start_pos,
                                       escape_expr_end_pos=escape_expr_end_pos,
                                       trans_var_name_used=trans_var_name_used)

    def process_translation_string(self, trans_expr):
        """
        Process translation string into string and variable name used

        Arguments:
            trans_expr: Translation expression inside {% %}
        Returns:
            None
        """

        quote = re.search(r"""\s*['"].*['"]\s*""", trans_expr, re.I)
        if not quote:
            _add_violations(self.results,
                            self.ruleset.django_trans_escape_filter_parse_error,
                            self)
            return None, None

        trans_expr_msg = trans_expr[quote.start():quote.end()].strip()
        if _check_is_string_has_html(trans_expr_msg):
            _add_violations(self.results,
                            self.ruleset.django_html_interpolation_missing,
                            self)
            return None, None

        pos = trans_expr.find('as', quote.end())
        if pos == -1:
            _add_violations(self.results, self.ruleset.django_trans_missing_escape, self)
            return None, None

        trans_var_name_used = trans_expr[pos + len('as'):].strip()
        return trans_var_name_used, trans_expr_msg

    def check_string_interpolation(self, trans_expr_msg, trans_var_name_used, expressions, template_file):
        """
        Checks if the translation string has used interpolation variable eg {variable} but not
        used interpolate_html tag to escape them

        Arguments:
            trans_expr_msg: Translation string in quotes
            trans_var_name_used: Translation variable used
            expressions: List of expressions found during django file processing
            template_file: django template file
        Returns:
            True: In case it finds interpolated variables
            False: No interpolation variables found
        """

        if _check_is_string_has_variables(trans_expr_msg):
            interpolate_tag, html_interpolated = _is_html_interpolated(trans_var_name_used,
                                                                       expressions)

            if not html_interpolated:
                _add_violations(self.results, self.ruleset.django_html_interpolation_missing, self)
            if interpolate_tag:
                interpolate_tag.validate_expression(template_file, expressions)
            return True
        return

    def find_filter_tag(self, template_file):
        """
        Finds if there is force_filter tag applied

        Arguments:
            template_file: django template file
        Returns:
            (None, None): In case there is a violations
            (start, end): Found filter tag start and end position
        """

        trans_expr_lineno = self.string_lines.index_to_line_number(self.start_index)
        escape_expr_start_pos = template_file.find('{{', self.end_index)
        if escape_expr_start_pos == -1:
            _add_violations(self.results,
                            self.ruleset.django_trans_missing_escape,
                            self)
            return None, None

        # {{ found but should be on the same line as trans tag
        trans_expr_filter_lineno = self.string_lines.index_to_line_number(escape_expr_start_pos)
        if trans_expr_filter_lineno != trans_expr_lineno:
            _add_violations(self.results,
                            self.ruleset.django_trans_missing_escape,
                            self)
            return None, None

        escape_expr_end_pos = template_file.find('}}', escape_expr_start_pos)
        # couldn't find matching }}
        if escape_expr_end_pos == -1:
            _add_violations(self.results,
                            self.ruleset.django_trans_missing_escape,
                            self)
            return None, None

        # }} should be also on the same line
        trans_expr_filter_lineno = self.string_lines.index_to_line_number(escape_expr_end_pos)
        if trans_expr_filter_lineno != trans_expr_lineno:
            _add_violations(self.results,
                            self.ruleset.django_trans_missing_escape,
                            self)
            return None, None

        return escape_expr_start_pos, escape_expr_end_pos

    def process_escape_filter_tag(self, **kwargs):
        """
        Checks if the escape filter and process it for violations

        Arguments:
            kwargs:  Having force_filter expression start, end, trans expression variable
            used and templates
        Returns:
            None: If found any violations
        """

        template_file = kwargs['template_file']
        escape_expr_start_pos = kwargs['escape_expr_start_pos']
        escape_expr_end_pos = kwargs['escape_expr_end_pos']
        trans_var_name_used = kwargs['trans_var_name_used']

        escape_expr = template_file[escape_expr_start_pos + len('{{'):
                                    escape_expr_end_pos].strip(' ')

        # check escape expression has the right variable and its escaped properly
        # with force_escape filter
        if '|' not in escape_expr or len(escape_expr.split('|')) != 2:
            _add_violations(self.results,
                            self.ruleset.django_trans_invalid_escape_filter,
                            self)
            return

        escape_expr_var_used, escape_filter = \
            escape_expr.split('|')[0].strip(' '), escape_expr.split('|')[1].strip(' ')
        if trans_var_name_used != escape_expr_var_used:
            _add_violations(self.results,
                            self.ruleset.django_trans_escape_variable_mismatch,
                            self)
            return

        if escape_filter != 'force_escape':
            _add_violations(self.results,
                            self.ruleset.django_trans_invalid_escape_filter,
                            self)
            return


class BlockTransExpression(Expression):
    """
        The expression handling blocktrans tag
    """
    def __init__(self, ruleset, results, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.string_lines = StringLines(kwargs['template'])
        self.ruleset = ruleset
        self.results = results

    def validate_expression(self, template_file, expressions=None):
        """
        Validates blocktrans tag expression for missing escaping filter

        Arguments:
            template_file: The content of the Django template.
            results: Violations to be generated.

        Returns:
            None
        """
        if not self._process_block(template_file, expressions):
            return

        filter_start_pos = template_file.rfind('{%', 0, self.start_index)
        if filter_start_pos == -1:
            _add_violations(self.results,
                            self.ruleset.django_blocktrans_missing_escape_filter,
                            self)
            return

        filter_end_pos = template_file.find('%}', filter_start_pos)
        if filter_end_pos > self.start_index:
            _add_violations(self.results,
                            self.ruleset.django_blocktrans_escape_filter_parse_error,
                            self)
            return

        escape_filter = template_file[filter_start_pos:filter_end_pos + 2]

        if len(escape_filter) < len('{%filter force_escape%}'):
            _add_violations(self.results,
                            self.ruleset.django_blocktrans_missing_escape_filter,
                            self)
            return

        escape_filter = escape_filter[2:-2].strip()
        escape_filter = escape_filter.split(' ')

        if len(escape_filter) != 2:
            _add_violations(self.results,
                            self.ruleset.django_blocktrans_missing_escape_filter,
                            self)
            return

        if escape_filter[0] != 'filter' or escape_filter[1] != 'force_escape':
            _add_violations(self.results,
                            self.ruleset.django_blocktrans_missing_escape_filter,
                            self)
            return

    def _process_block(self, template_file, expressions):
        """
            Process blocktrans..endblocktrans block

            Arguments:
                template_file: The content of the Django template.

            Returns:
                None
        """
        blocktrans_string = self._extract_translation_msg(template_file)

        # if no string extracted might have hit a parse error just return
        if not blocktrans_string:
            return

        if _check_is_string_has_html(blocktrans_string):
            _add_violations(self.results, self.ruleset.django_html_interpolation_missing, self)
            return

        # Checking if blocktrans tag has interpolated variables eg {}
        # in translations string. Would be tested for
        # possible html interpolation done somewhere else.

        if _check_is_string_has_variables(blocktrans_string):
            blocktrans_expr = self.expression_inner
            pos = blocktrans_expr.find('asvar')
            if pos == -1:
                _add_violations(self.results, self.ruleset.django_html_interpolation_missing, self)
                return

            trans_var_name_used = blocktrans_expr[pos + len('asvar'):].strip()

            # check for interpolate_html expression for the variable in trans expression
            interpolate_tag, html_interpolated = _is_html_interpolated(trans_var_name_used,
                                                                       expressions)
            if not html_interpolated:
                _add_violations(self.results, self.ruleset.django_html_interpolation_missing, self)
            if interpolate_tag:
                interpolate_tag.validate_expression(template_file, expressions)
            return
        return True

    def _extract_translation_msg(self, template_file):

        endblocktrans = re.compile(r'{%\s*endblocktrans.*?%}').search(template_file,
                                                                      self.end_index)
        if not endblocktrans.start():
            _add_violations(self.results,
                            self.ruleset.django_blocktrans_parse_error,
                            self)
            return

        return template_file[self.end_index + 2: endblocktrans.start()].strip(' ')


class HtmlInterpolateExpression(Expression):
    """
        The expression handling interplate_html tag
    """
    def __init__(self, ruleset, results, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.string_lines = StringLines(kwargs['template'])
        self.ruleset = ruleset
        self.results = results
        self.validated = False
        self.interpolated_string_var = None

        trans_expr = self.expression_inner
        # extracting interpolated variable string name
        expr_list = trans_expr.split(' ')
        if len(expr_list) < 2:
            _add_violations(self.results,
                            self.ruleset.django_html_interpolation_invalid_tag,
                            self)
            return
        self.interpolated_string_var = expr_list[1]

    def validate_expression(self, template_file, expressions=None):
        """
        Validates interpolate_html tag expression for missing safe filter for html tags

        Arguments:
            template_file: The content of the Django template.
            results: Violations to be generated.

        Returns:
            None
        """

        # if the expression is already validated, we would not be processing it again
        if not self.interpolated_string_var or self.validated:
            return

        self.validated = True
        trans_expr = self.expression_inner

        html_tags = re.finditer(r"""\s*['"]</?[a-zA-Z0-9 =\-'_"]+.*?>['"]""",
                                trans_expr, re.I)
        for html_tag in html_tags:
            tag_end = html_tag.end()

            escape_filter = trans_expr[tag_end:tag_end + len('|safe')]
            if escape_filter != '|safe':
                _add_violations(self.results,
                                self.ruleset.django_html_interpolation_missing_safe_filter,
                                self)
                return

        return True


def _check_is_string_has_html(trans_expr):
    html_tags = re.search(r"""</?[a-zA-Z0-9 =\-'_":]+>""", trans_expr, re.I)

    if html_tags:
        return True


def _check_is_string_has_variables(trans_expr):
    var_tags = re.search(r"""(?<!{){(?!{)[a-zA-Z0-9 =\-'_":]+(?<!})}(?!})""", trans_expr, re.I)

    if var_tags:
        return True


def _is_html_interpolated(trans_var_name_used, expressions):
    html_interpolated = False
    interpolate_tag_expr = None
    for expr in expressions:
        if isinstance(expr, HtmlInterpolateExpression):
            if expr.interpolated_string_var == trans_var_name_used:
                html_interpolated = True
                interpolate_tag_expr = expr

    return interpolate_tag_expr, html_interpolated


def _add_violations(results, rule_violation, self):
    results.violations.append(ExpressionRuleViolation(
        rule_violation, self
    ))
