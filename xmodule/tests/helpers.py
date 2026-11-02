"""
Utility methods for unit tests.
"""


import filecmp
import pprint

import pytest
from path import Path as path
from xblock.reference.user_service import UserService, XBlockUser
from xmodule.x_module import ModuleStoreRuntime


def directories_equal(directory1, directory2):
    """
    Returns True if the 2 directories have equal content, else false.
    """
    def compare_dirs(dir1, dir2):
        """ Compare directories for equality. """
        comparison = filecmp.dircmp(dir1, dir2)
        if (len(comparison.left_only) > 0) or (len(comparison.right_only) > 0):
            return False
        if (len(comparison.funny_files) > 0) or (len(comparison.diff_files) > 0):
            return False
        for subdir in comparison.subdirs:
            if not compare_dirs(dir1 / subdir, dir2 / subdir):
                return False

        return True

    return compare_dirs(path(directory1), path(directory2))


def mock_render_template(*args, **kwargs):
    """
    Pretty-print the args and kwargs.

    Allows us to not depend on any actual template rendering mechanism,
    while still returning a unicode object
    """
    return pprint.pformat((args, kwargs)).encode().decode()


class StubMakoService:
    """
    Stub MakoService for testing blocks that use mako templates.
    """

    def __init__(self, render_template=None):
        self._render_template = render_template or mock_render_template

    def render_template(self, *args, **kwargs):
        """
        Invokes the configured render_template method.
        """
        return self._render_template(*args, **kwargs)

    def render_lms_template(self, *args, **kwargs):
        """
        Invokes the configured render_template method.
        """
        return self._render_template(*args, **kwargs)

    def render_cms_template(self, *args, **kwargs):
        """
        Invokes the configured render_template method.
        """
        return self._render_template(*args, **kwargs)


class StubUserService(UserService):
    """
    Stub UserService for testing the sequence block.
    """

    def __init__(self,
                 user=None,
                 user_is_staff=False,
                 user_role=None,
                 anonymous_user_id=None,
                 deprecated_anonymous_user_id=None,
                 request_country_code=None,
                 **kwargs):
        self.user = user
        self.user_is_staff = user_is_staff
        self.user_role = user_role
        self.anonymous_user_id = anonymous_user_id
        self.deprecated_anonymous_user_id = deprecated_anonymous_user_id
        self.request_country_code = request_country_code
        self._django_user = user
        super().__init__(**kwargs)

    def get_current_user(self):
        """
        Implements abstract method for getting the current user.
        """
        user = XBlockUser()
        if self.user and self.user.is_authenticated:
            user.opt_attrs['edx-platform.anonymous_user_id'] = self.anonymous_user_id
            user.opt_attrs['edx-platform.deprecated_anonymous_user_id'] = self.deprecated_anonymous_user_id
            user.opt_attrs['edx-platform.request_country_code'] = self.request_country_code
            user.opt_attrs['edx-platform.user_is_staff'] = self.user_is_staff
            user.opt_attrs['edx-platform.user_id'] = self.user.id
            user.opt_attrs['edx-platform.user_role'] = self.user_role
            user.opt_attrs['edx-platform.username'] = self.user.username
        else:
            user.opt_attrs['edx-platform.username'] = 'anonymous'
            user.opt_attrs['edx-platform.request_country_code'] = self.request_country_code
            user.opt_attrs['edx-platform.is_authenticated'] = False
        return user

    def get_user_by_anonymous_id(self, uid=None):  # pylint: disable=unused-argument
        """
        Return the original user passed into the service.
        """
        return self.user


class StubReplaceURLService:
    """
    Stub ReplaceURLService for testing blocks.
    """

    def replace_urls(self, text, static_replace_only=False):
        """
        Invokes the configured render_template method.
        """
        return text


@pytest.fixture
def override_descriptor_system(monkeypatch):
    """
    Fixture to override get_block method of ModuleStoreRuntime
    """

    def get_block(self, usage_id, for_parent=None):
        """See documentation for `xblock.runtime:Runtime.get_block`"""
        block = self.load_item(usage_id, for_parent=for_parent)
        return block

    monkeypatch.setattr(ModuleStoreRuntime, "get_block", get_block)
