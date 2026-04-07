"""
Tests for the pull_xblock_translations management command.
"""

from unittest.mock import patch

from django.core.management import call_command
from done import DoneXBlock
from path import Path

from xmodule.modulestore.api import get_javascript_i18n_file_path, get_python_locale_root
from xmodule.modulestore.tests.conftest import tmp_translations_dir


def test_pull_xblock_translations(tmp_path):
    """
    Test the compile_xblock_translations management command.
    """
    temp_xblock_locale_path = Path(str(tmp_path))

    with patch('common.djangoapps.xblock_django.translation.get_non_xmodule_xblocks') as mock_get_non_xmodule_xblocks:
        with patch('xmodule.modulestore.api.get_python_locale_root') as mock_get_python_locale_root:
            with patch('subprocess.run') as mock_run:
                mock_get_python_locale_root.return_value = Path(str(temp_xblock_locale_path))
                mock_get_non_xmodule_xblocks.return_value = [('done', DoneXBlock)]

                call_command(
                    'pull_xblock_translations',
                    filter='ar,de_DE,jp',
                    repository='openedx/custom-translations',
                    branch='release/redwood',
                )

                assert mock_run.call_count == 1, 'Calls `subprocess.run`'
                assert mock_run.call_args.kwargs['args'] == [
                    'atlas', 'pull',
                    '--expand-glob',
                    '--filter', 'ar,de_DE,jp',
                    '--repository', 'openedx/custom-translations',
                    '--branch', 'release/redwood',
                    '--silent',
                    'translations/*/done/conf/locale:done',
                ]


def test_compile_xblock_translations(tmp_translations_dir):
    """
    Test the compile_xblock_translations management command.
    """
    # msgfmt isn't available in test environment, so we mock the `subprocess.run` and copy the django.mo file,
    # it to ensure `compile_xblock_js_messages` can work.
    with tmp_translations_dir(xblocks=[('done', DoneXBlock)], fixtures_to_copy=['django.po', 'django.mo']):
        with patch.object(DoneXBlock, 'i18n_js_namespace', 'TestingDoneXBlockI18n'):
            po_file = get_python_locale_root() / 'done/tr/LC_MESSAGES/django.po'

            with patch('subprocess.run') as mock_run:
                call_command('compile_xblock_translations')
                assert mock_run.call_count == 1, 'Calls `subprocess.run`'
                assert mock_run.call_args.kwargs['args'] == [
                    'msgfmt', '--check-format', '-o', str(po_file.with_suffix('.mo')), str(po_file),
                ], 'Compiles the .po files'

            js_file_text = get_javascript_i18n_file_path('done', 'tr').read_text()
            assert 'Merhaba' in js_file_text, 'Ensures the JavaScript catalog is compiled'
            assert 'TestingDoneXBlockI18n' in js_file_text, 'Ensures the namespace is used'
            assert 'gettext' in js_file_text, 'Ensures the gettext function is defined'
