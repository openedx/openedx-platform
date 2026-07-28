"""
Event tracking backend module.

Contains the base class for event trackers, and implementation of some
backends.

"""


import abc

import six  # noqa: F401


class BaseBackend(metaclass=abc.ABCMeta):
    """
    Abstract Base Class for event tracking backends.

    """

    def __init__(self, **kwargs):  # noqa: B027
        pass

    @abc.abstractmethod
    def send(self, event):
        """Send event to tracker."""
        pass  # pylint: disable=unnecessary-pass
