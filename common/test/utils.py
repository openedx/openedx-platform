"""
General testing utilities.
"""
from unittest.mock import Mock, patch


import functools
import sys
from contextlib import contextmanager

from django.dispatch import Signal
from markupsafe import escape


def assert_dict_contains_subset(test_case, subset, superset):
    """
    Assert that `superset` includes all key/value pairs from `subset`.
    """
    test_case.assertTrue(
        all(item in superset.items() for item in subset.items())
    )


@contextmanager
def nostderr():
    """
    ContextManager to suppress stderr messages
    http://stackoverflow.com/a/1810086/882918
    """
    savestderr = sys.stderr

    class Devnull:
        """ /dev/null incarnation as output-stream-like object """
        def write(self, _):
            """ Write method - just does nothing"""
            pass  # lint-amnesty, pylint: disable=unnecessary-pass

    sys.stderr = Devnull()
    try:
        yield
    finally:
        sys.stderr = savestderr


class XssTestMixin:
    """
    Mixin for testing XSS vulnerabilities.
    """

    def assert_no_xss(self, response, xss_content):
        """Assert that `xss_content` is not present in the content of
        `response`, and that its escaped version is present. Uses the
        same `markupsafe.escape` function as Mako templates.

        Args:
          response (Response): The HTTP response
          xss_content (str): The Javascript code to check for.

        Returns:
          None

        """
        self.assertContains(response, escape(xss_content))
        self.assertNotContains(response, xss_content)


def disable_signal(module, signal):
    """Replace `signal` inside of `module` with a dummy signal. Can be
    used as a method or class decorator, as well as a context manager."""
    return patch.object(module, signal, new=Signal())


class MockSignalHandlerMixin:
    """Mixin for testing sending of signals."""

    @contextmanager
    def assert_signal_sent(self, module, signal, *args, **kwargs):
        """Assert that a signal was sent with the correct arguments. Since
        Django calls signal handlers with the signal as an argument,
        it is added to `kwargs`.

        Uses `mock.patch.object`, which requires the target to be
        specified as a module along with a variable name inside that
        module.

        Args:
          module (module): The module in which to patch the given signal name.
          signal (str): The name of the signal to patch.
          *args, **kwargs: The arguments which should have been passed
            along with the signal. If `exclude_args` is passed as a
            keyword argument, its value should be a list of keyword
            arguments passed to the signal whose values should be
            ignored.

        """
        with patch.object(module, signal, new=Signal()) as mock_signal:
            mock_handler = Mock()
            # Wrap the mock in a real callable so inspect.iscoroutinefunction() works

            def handler(*h_args, **h_kwargs):
                """No-op signal handler."""
                return mock_handler(*h_args, **h_kwargs)

            mock_signal.connect(handler)
            yield
            assert mock_handler.called
            mock_args, mock_kwargs = mock_handler.call_args
            if 'exclude_args' in kwargs:
                for key in kwargs['exclude_args']:
                    assert key in mock_kwargs
                    del mock_kwargs[key]
                del kwargs['exclude_args']
            assert mock_args == args
            assert mock_kwargs == dict(kwargs, signal=mock_signal)


@contextmanager
def skip_signal(signal, **kwargs):
    """
    ContextManager to skip a signal by disconnecting it, yielding,
    and then reconnecting the signal.
    """
    signal.disconnect(**kwargs)
    try:
        yield
    finally:
        signal.connect(**kwargs)


class MockS3Boto3Mixin:
    """
    TestCase mixin that mocks the S3Boto3Storage save method and s3 connection.
    """
    def setUp(self):
        super().setUp()
        self._mocked_connection = patch('boto3.resource', return_value=Mock())
        self.mocked_connection = self._mocked_connection.start()

        self.patcher = patch('storages.backends.s3boto3.S3Boto3Storage.save')
        self.patcher.start()

    def tearDown(self):
        self._mocked_connection.stop()
        self.patcher.stop()
        super().tearDown()


class reprwrapper:
    """
    Wrapper class for functions that need a normalized string representation.
    """
    def __init__(self, func):
        self._func = func
        self.repr = f'Func: {func.__name__}'
        functools.update_wrapper(self, func)

    def __call__(self, *args, **kw):
        return self._func(*args, **kw)

    def __repr__(self):
        return self.repr


def normalize_repr(func):
    """
    Function decorator used to normalize its string representation.
    Used to wrap functions used as ddt parameters, so pytest-xdist
    doesn't complain about the sequence of discovered tests differing
    between worker processes.
    """
    return reprwrapper(func)
