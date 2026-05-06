"""
Access methods to get EditInfo for xblocks
"""


from abc import ABCMeta, abstractmethod

from xblock.core import XBlockMixin


class EditInfoMixin(XBlockMixin):
    """
    Provides the interfaces for getting the edit info from XBlocks
    """
    @property
    def edited_by(self):
        """
        The user id of the last user to change this xblock content, children, or settings.
        """
        return self.runtime.get_edited_by(self)

    @property
    def edited_on(self):
        """
        The datetime of the last change to this xblock content, children, or settings.
        """
        return self.runtime.get_edited_on(self)

    @property
    def subtree_edited_by(self):
        """
        The user id of the last user to change content, children, or settings in this xblock's subtree
        """
        return self.runtime.get_subtree_edited_by(self)

    @property
    def subtree_edited_on(self):
        """
        The datetime of the last change content, children, or settings in this xblock's subtree
        """
        return self.runtime.get_subtree_edited_on(self)

    @property
    def published_by(self):
        """
        The user id of the last user to publish this specific xblock (or a previous version of it).
        """
        return self.runtime.get_published_by(self)

    @property
    def published_on(self):
        """
        The datetime of the last time this specific xblock was published.
        """
        return self.runtime.get_published_on(self)


class EditInfoRuntimeMixin(metaclass=ABCMeta):
    """
    An abstract mixin class for the functions which the :class: `EditInfoMixin` methods call on the runtime
    """

    @abstractmethod
    def get_edited_by(self, xblock):
        """
        The datetime of the last change to this xblock content, children, or settings.
        """
        pass  # pylint: disable=unnecessary-pass

    @abstractmethod
    def get_edited_on(self, xblock):
        """
        The datetime of the last change to this xblock content, children, or settings.
        """
        pass  # pylint: disable=unnecessary-pass

    @abstractmethod
    def get_subtree_edited_by(self, xblock):
        """
        The user id of the last user to change content, children, or settings in this xblock's subtree
        """
        pass  # pylint: disable=unnecessary-pass

    @abstractmethod
    def get_subtree_edited_on(self, xblock):
        """
        The datetime of the last change content, children, or settings in this xblock's subtree
        """
        pass  # pylint: disable=unnecessary-pass

    @abstractmethod
    def get_published_by(self, xblock):
        """
        The user id of the last user to publish this specific xblock (or a previous version of it).
        """
        pass  # pylint: disable=unnecessary-pass

    @abstractmethod
    def get_published_on(self, xblock):
        """
        The datetime of the last time this specific xblock was published.
        """
        pass  # pylint: disable=unnecessary-pass
