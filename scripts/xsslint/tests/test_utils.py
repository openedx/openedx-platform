from unittest import TestCase

from ddt import data, ddt
from xsslint.utils import StringLines


@ddt
class TestStringLines(TestCase):
    """
    Test StringLines class.
    """
    @data(
        {'string': 'test', 'index': 0, 'line_start_index': 0, 'line_end_index': 4},
        {'string': 'test', 'index': 2, 'line_start_index': 0, 'line_end_index': 4},
        {'string': 'test', 'index': 3, 'line_start_index': 0, 'line_end_index': 4},
        {'string': '\ntest', 'index': 0, 'line_start_index': 0, 'line_end_index': 1},
        {'string': '\ntest', 'index': 2, 'line_start_index': 1, 'line_end_index': 5},
        {'string': '\ntest\n', 'index': 0, 'line_start_index': 0, 'line_end_index': 1},
        {'string': '\ntest\n', 'index': 2, 'line_start_index': 1, 'line_end_index': 6},
        {'string': '\ntest\n', 'index': 6, 'line_start_index': 6, 'line_end_index': 6},
    )
    def test_string_lines_start_end_index(self, data):
        """
        Test StringLines index_to_line_start_index and index_to_line_end_index.
        """
        lines = StringLines(data['string'])
        assert lines.index_to_line_start_index(data['index']) == data['line_start_index']
        assert lines.index_to_line_end_index(data['index']) == data['line_end_index']

    @data(
        {'string': 'test', 'line_number': 1, 'line': 'test'},
        {'string': '\ntest', 'line_number': 1, 'line': ''},
        {'string': '\ntest', 'line_number': 2, 'line': 'test'},
        {'string': '\ntest\n', 'line_number': 1, 'line': ''},
        {'string': '\ntest\n', 'line_number': 2, 'line': 'test'},
        {'string': '\ntest\n', 'line_number': 3, 'line': ''},
    )
    def test_string_lines_line_numbers(self, data):
        """
        Test line_number_to_line.
        """
        lines = StringLines(data['string'])
        assert lines.line_number_to_line(data['line_number']) == data['line']
