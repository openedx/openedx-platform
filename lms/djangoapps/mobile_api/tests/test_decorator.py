"""
Tests for mobile API utilities.
"""


import ddt
from django.test import TestCase

from ..decorators import mobile_course_access, mobile_view


@ddt.ddt
class TestMobileAPIDecorators(TestCase):
    """
    Basic tests for mobile api decorators to ensure they retain the docstrings.
    """

    @ddt.data(mobile_view, mobile_course_access)
    def test_function_decorator(self, decorator):
        @decorator()
        def decorated_func():
            """
            Test docstring of decorated function.
            """
            pass  # pylint: disable=unnecessary-pass

        assert 'Test docstring of decorated function.' in decorated_func.__doc__
        assert decorated_func.__name__ == 'decorated_func'
        assert decorated_func.__module__.endswith('test_decorator')
