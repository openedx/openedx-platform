"""
Tests for the plugins.i18n_api module.
"""
from unittest.mock import patch

import pytest
from django.core.management import CommandError

from ..i18n_api import (
    ArgparseArgument,
    BaseAtlasPullCommand,
    atlas_pull_by_modules,
    compile_po_files,
    get_installed_plugins_module_names,
)


def test_argparse_argument():
    """
    Test the ArgparseArgument utility class.
    """
    argument = ArgparseArgument(
        flag="--filter",
        dest="filter",
        help="Some filter",
    )

    assert argument.get_kwargs() == {
        "dest": "filter",
        "help": "Some filter",
    }, 'Should not include `flag` in keyword arguments to match argparse add_argument method'

    assert argument.get_args() == ['--filter'], '--filter should be a positional argument'


def test_atlas_pull_by_modules():
    """
    Test the atlas_pull_by_modules's subprocess.run parameters.
    """
    module_names = ['done', 'drag_and_drop_v2']
    locale_root = '/tmp/locale'

    with patch('subprocess.run') as mock_run:
        atlas_pull_by_modules(module_names, locale_root, ['--filter', 'ar,jp_JP', '--silent'])

    mock_run.assert_called_once_with(
        args=[
            'atlas', 'pull',
            '--expand-glob',
            '--filter', 'ar,jp_JP',
            '--silent',
            'translations/*/done/conf/locale:done',
            'translations/*/drag_and_drop_v2/conf/locale:drag_and_drop_v2',
        ],
        check=True,
        cwd=locale_root,
    )


def test_compile_po_files(tmp_path):
    """
    Test the compile_po_files recursive call to `msgfmt`.
    """
    locale_root = tmp_path / 'locale'
    locale_root.mkdir()
    po_file_path = locale_root / 'test.po'
    with open(po_file_path, 'w'):
        # Creates an empty po file
        pass

    with patch('subprocess.run') as mock_run:
        compile_po_files(locale_root)

    mock_run.assert_called_once_with(
        args=[
            'msgfmt', '--check-format',
            '-o', str(po_file_path.with_suffix('.mo')),
            str(po_file_path),
        ],
        check=True,
    )


def test_base_atlas_pull_command(tmp_path):
    """
    Test the BaseAtlasPullCommand's methods.
    """
    command = BaseAtlasPullCommand()

    assert command.ensure_empty_directory(tmp_path) is None, 'Should not raise an exception if the directory is empty'
    with pytest.raises(CommandError):
        with open(tmp_path / 'test.txt', 'w'):
            # Directory is not empty anymore
            pass
        command.ensure_empty_directory(tmp_path)

    assert command.get_atlas_pull_options(
        filter='ar,jp_JP',
        revision='custom_branch',
        repository='my_org/custom_repo',
        verbose=False,
    ) == [
        '--filter', 'ar,jp_JP', '--repository', 'my_org/custom_repo', '--revision', 'custom_branch', '--silent',
    ], 'Flatten out the options into a list of arguments for atlas pull'


def test_get_installed_plugins_module_names():
    """
    Test the get_installed_plugins_module_names helper.
    """
    plugins = get_installed_plugins_module_names()

    assert 'drag_and_drop_v2' not in plugins, 'XBlocks have their own translation process'
    assert 'edx_proctoring' in plugins, 'edx-proctoring should be included'
    assert 'lms' not in plugins, 'lms and cms plugins are translated as part of the edx-platform itself'
