"""
Tests for content library backup (zip export) utilities.
"""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from unittest.mock import MagicMock, patch

import pytest
from django.test import TestCase
from opaque_keys.edx.locator import LibraryLocatorV2
from path import Path

from openedx.core.djangoapps.content_libraries.api.backup import extract_library_v2_zip_to_dir

LIBRARY_KEY = LibraryLocatorV2(org='TestOrg', slug='test-lib')


class TestExtractLibraryV2ZipToDir(TestCase):
    """
    Tests for ``extract_library_v2_zip_to_dir``.
    """

    def _make_zip_in_temp_dir(self, contents=None):
        """
        Helper: create a real temp dir + zip file and return (temp_dir_path, zip_path).
        ``contents`` is a dict of {filename: bytes} to write into the zip.
        """
        temp_dir = Path(tempfile.mkdtemp())
        zip_path = str(temp_dir / 'library.zip')
        with zipfile.ZipFile(zip_path, 'w') as zf:
            for name, data in (contents or {'data.xml': b'<library/>'}).items():
                zf.writestr(name, data)
        return temp_dir, zip_path

    @patch('openedx.core.djangoapps.content_libraries.api.backup.get_user_model')
    @patch('openedx.core.djangoapps.content_libraries.api.backup.create_library_v2_zip')
    def test_successful_extraction(self, mock_create_zip, mock_get_user_model):
        """
        On a successful call the function should:
        - resolve the username to a user object via the user model,
        - pass that user object to ``create_library_v2_zip``,
        - create the target directory if it does not already exist,
        - extract the zip contents into <root_dir>/<library_dir>,
        - clean up the temporary zip directory.
        """
        root_dir = Path(tempfile.mkdtemp())
        temp_zip_dir, zip_path = self._make_zip_in_temp_dir({'content.xml': b'<lib/>'})
        mock_create_zip.return_value = (temp_zip_dir, zip_path)
        mock_user = MagicMock()
        mock_get_user_model.return_value.objects.get.return_value = mock_user

        try:
            target = root_dir / 'my-library'
            assert not target.exists(), "Target dir should not exist before the call"

            extract_library_v2_zip_to_dir(LIBRARY_KEY, str(root_dir), 'my-library', username='testuser')

            mock_get_user_model.return_value.objects.get.assert_called_once_with(username='testuser')
            mock_create_zip.assert_called_once_with(LIBRARY_KEY, mock_user)
            assert target.isdir(), "Target dir should have been created"
            assert (target / 'content.xml').exists(), "Zip content should be extracted"
            assert not temp_zip_dir.exists(), "Temp zip dir should have been removed"
        finally:
            shutil.rmtree(root_dir, ignore_errors=True)
            shutil.rmtree(temp_zip_dir, ignore_errors=True)

    @patch('openedx.core.djangoapps.content_libraries.api.backup.get_user_model')
    @patch('openedx.core.djangoapps.content_libraries.api.backup.create_library_v2_zip')
    def test_temp_dir_cleaned_up_even_on_extraction_error(self, mock_create_zip, mock_get_user_model):
        """
        The temporary directory must be cleaned up even when extraction raises.
        """
        root_dir = Path(tempfile.mkdtemp())
        temp_zip_dir, zip_path = self._make_zip_in_temp_dir()
        mock_create_zip.return_value = (temp_zip_dir, zip_path)
        mock_get_user_model.return_value.objects.get.return_value = None

        try:
            with patch('zipfile.ZipFile.extractall', side_effect=OSError('disk full')):
                with pytest.raises(OSError, match='disk full'):
                    extract_library_v2_zip_to_dir(LIBRARY_KEY, str(root_dir), 'my-library', username=None)
            assert not temp_zip_dir.exists(), "Temp dir should be cleaned up on error"
        finally:
            shutil.rmtree(root_dir, ignore_errors=True)
            shutil.rmtree(temp_zip_dir, ignore_errors=True)
