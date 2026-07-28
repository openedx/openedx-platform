"""
Test fixture for the `xmodule.modulestore` module.
"""

import shutil
from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest
from django.test.utils import override_settings
from path import Path

from xmodule.modulestore.api import get_python_locale_root


@pytest.fixture
def tmp_translations_dir(tmp_path, settings):
    """
    Pytest fixture to create a temporary directory for translations.

    Returns:
        (function): Context manager to be used with the `with tmp_translations_dir(...):` statement.
    """

    @contextmanager
    def _tmp_translations_dir(xblocks, fixtures_to_copy=None):
        """
        Context manager to create temporary directory for translations.

        Args:
            xblocks: A list of tuples of (module_name, xblock_class) to patch `get_non_xmodule_xblocks` for consistent
                     test runs.

            fixtures_to_copy: A list of `modulestore/tests/fixtures` file names to copy to the XBlocks directory.

        Yields:
            Path: The temporary edx-platform directory path.

        The temp directory will have the following structure:

        edx-platform/
          ├── conf
          │ └── plugins-locale
          │     └── xblock.v1
          │         └── done
          │             └── tr
          │                 └── LC_MESSAGES
          │                     └── django.po
          └── lms
              └── static
        """
        # tmp_path represents settings.REPO_ROOT
        # Converting to `path.path()` to be compatible with the `settings.REPO_ROOT` type.
        original_repo_root = settings.REPO_ROOT
        repo_root = Path(str(tmp_path / 'edx-platform'))

        project_dir_name = settings.PROJECT_ROOT.basename()  # lms or cms
        static_i18n_root = repo_root / f'{project_dir_name}/static'

        with override_settings(REPO_ROOT=repo_root, STATICI18N_ROOT=static_i18n_root):
            gettext_fixtures = original_repo_root / 'xmodule/modulestore/tests/fixtures'

            python_root = get_python_locale_root()
            python_root.makedirs_p()
            static_i18n_root.makedirs_p()

            if fixtures_to_copy:
                for module_name, _xblock in xblocks:
                    for fixture in fixtures_to_copy:
                        dest_dir = python_root / module_name / 'tr/LC_MESSAGES'
                        dest_dir.makedirs_p()
                        shutil.copyfile(gettext_fixtures / fixture, dest_dir / fixture)

            with patch('common.djangoapps.xblock_django.translation.get_non_xmodule_xblocks', return_value=xblocks):
                yield repo_root

    return _tmp_translations_dir


def create_mock_xblock(module_name):
    """
    Create a mocked XBlock with the given module name.
    """
    block = Mock()
    block.unmixed_class.__module__ = module_name
    return block


@pytest.fixture
def mock_modern_xblock(tmp_translations_dir):
    """
    Mocks a successful `atlas pull` for `my_modern_xblock` xblock.

    Yields:
        dict: A dictionary of mocked XBlocks:
            - modern_xblock: A mocked XBlock atlas translations.
            - legacy_xblock: A mocked XBlock without atlas translations.
    """
    with tmp_translations_dir(
        xblocks=[('my_modern_xblock', Mock())],
        fixtures_to_copy=['django.po', 'django.mo'],
    ):
        yield {
            'legacy_xblock': create_mock_xblock('my_legacy_xblock'),
            'modern_xblock': create_mock_xblock('my_modern_xblock'),
        }
