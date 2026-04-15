"""
Helper class to convert mathjax equations to plain text.
"""

import re

import unicodeit


class InvalidMathEquation(Exception):
    """Raised when mathjax equation is invalid. This is used to skip all transformations."""


class EqnPatternNotFound(Exception):
    """Raised when a pattern is not found in equation. This is used to skip a specific transformation."""


class PlainTextMath:
    """
    Converts mathjax equations to plain text using unicodeit and some preprocessing.
    """
    equation_pattern = re.compile(
        r'\[mathjaxinline\](.*?)\[\/mathjaxinline\]|\[mathjax\](.*?)\[\/mathjax\]|\\\((.*?)\\\)|\\\[(.*?)\\\]'
    )
    eqn_replacements = (
        # just remove prefix `\`
        ("\\sin", "sin"),
        ("\\cos", "cos"),
        ("\\tan", "tan"),
        ("\\arcsin", "arcsin"),
        ("\\arccos", "arccos"),
        ("\\arctan", "arctan"),
        ("\\cot", "cot"),
        ("\\sec", "sec"),
        ("\\csc", "csc"),
        # Is used for matching brackets in mathjax, should not be required in plain text.
        ("\\left", ""),
        ("\\right", ""),
    )
    regex_replacements = (
        # Makes text bold, so not required in plain text.
        (re.compile(r'{\\bf (.*?)}'), r"\1"),
    )
    extract_inner_texts = (
        # Replaces any eqn: `\name{inner_text}` with `inner_text`
        "\\mathbf{",
        "\\bm{",
    )
    frac_open_close_pattern = re.compile(r"}\s*{")

    @staticmethod
    def _nested_bracket_matcher(equation: str, opening_pattern: str) -> str:
        r"""
        Matches opening and closing brackets in given string.

        Args:
            equation: string
            opening_pattern: for example, `\mathbf{`

        Returns:
            String inside the eqn brackets
        """
        start = equation.find(opening_pattern)
        if start == -1:
            raise EqnPatternNotFound()
        open_count = 0
        inner_start = start + len(opening_pattern)
        for i, char in enumerate(equation[inner_start:]):  # noqa: B007
            if char == "{":
                open_count += 1
            if char == "}":
                if open_count == 0:
                    break
                open_count -= 1
        else:
            raise InvalidMathEquation()
        # In below example `|` symbol is used to denote index position
        # |\mathbf{, \mathbf{|, \mathbf{some_text|}, \mathbf{some_text}|
        return (start, inner_start, inner_start + i, inner_start + i + 1)

    def _fraction_handler(self, equation: str) -> str:
        r"""
        Converts `\frac{x}{y}` to `(x/y)` while handling nested `{}`.

        For example: `\frac{2}{\sqrt{1+y}}` is converted to `(2/\sqrt{1+y})`.

        Args:
            equation: string

        Returns:
            String with `\frac` replaced by normal `/` symbol.
        """
        try:
            n_start, n_inner_start, n_inner_end, n_end = self._nested_bracket_matcher(equation, "\\frac{")
        except EqnPatternNotFound:
            return equation

        numerator = equation[n_inner_start:n_inner_end]
        # Handle nested fractions
        numerator = self._fraction_handler(numerator)

        try:
            _, d_inner_start, d_inner_end, d_end = self._nested_bracket_matcher(equation[n_end:], "{")
        except EqnPatternNotFound:
            return equation

        denominator = equation[n_end + d_inner_start:n_end + d_inner_end]
        # Handle nested fractions
        denominator = self._fraction_handler(denominator)
        # Now re-create the equation with `(numerator / denominator)`
        equation = equation[:n_start] + f"({numerator}/{denominator})" + equation[n_end + d_end:]
        return equation

    def _nested_text_extractor(self, equation: str, pattern: str) -> str:
        """
        Recursively extracts text from equation for given pattern
        """
        try:
            start, inner_start, inner_end, end = self._nested_bracket_matcher(equation, pattern)
            inner_text = equation[inner_start:inner_end]
            inner_text = self._nested_text_extractor(inner_text, pattern)
            equation = equation[:start] + inner_text + equation[end:]
        except EqnPatternNotFound:
            pass
        return equation

    def _handle_replacements(self, equation: str) -> str:
        """
        Makes a bunch of replacements in equation string.
        """
        for q, replacement in self.eqn_replacements:
            equation = equation.replace(q, replacement)
        for pattern in self.extract_inner_texts:
            equation = self._nested_text_extractor(equation, pattern)
        for pattern, replacement in self.regex_replacements:
            equation = re.sub(pattern, replacement, equation)
        return equation

    def run(self, eqn_matches: re.Match) -> str:
        """
        Takes re.Match object and runs conversion process on each match group.
        """
        groups = eqn_matches.groups()
        for group in groups:
            if not group:
                continue
            original = group
            try:
                group = self._handle_replacements(group)
                group = self._fraction_handler(group)
                return unicodeit.replace(group)
            except Exception:  # pylint: disable=broad-except
                return original
        return None


processor = PlainTextMath()


def process_mathjax(content: str) -> str:
    return re.sub(processor.equation_pattern, processor.run, content)
