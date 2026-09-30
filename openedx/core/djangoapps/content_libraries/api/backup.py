"""
Public API for content library backup (zip export) utilities.
"""
from __future__ import annotations

import os
import shutil
import zipfile
from datetime import datetime
from tempfile import mkdtemp

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.text import slugify
from opaque_keys.edx.locator import LibraryLocatorV2, log
from openedx_content.api import create_zip_file as create_lib_zip_file
from path import Path

__all__ = ["create_library_v2_zip", "extract_library_v2_zip_to_dir"]


def create_library_v2_zip(library_key: LibraryLocatorV2, user) -> tuple:
    """
    Create a zip backup of a v2 library and return ``(temp_dir, zip_file_path)``.

    The caller is responsible for cleaning up ``temp_dir`` when done.

    Args:
        library_key: LibraryLocatorV2 identifying the library to export.
        user: User object passed to the backup API.

    Returns:
        A tuple of ``(temp_dir as Path, zip_file_path as str)``.
    """
    root_dir = Path(mkdtemp())
    sanitized_lib_key = str(library_key).replace(":", "-")
    sanitized_lib_key = slugify(sanitized_lib_key, allow_unicode=True)
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    filename = f'{sanitized_lib_key}-{timestamp}.zip'
    file_path = os.path.join(root_dir, filename)
    origin_server = getattr(settings, 'CMS_BASE', None)
    create_lib_zip_file(package_ref=str(library_key), path=file_path, user=user, origin_server=origin_server)
    return root_dir, file_path


def extract_library_v2_zip_to_dir(library_key, root_dir, library_dir, username=None):
    """
    Export a v2 library to a directory by creating a zip backup and extracting it.

    V2 libraries are stored in Learning Core and use a zip-based backup mechanism.
    This function creates a temporary zip backup, extracts its contents into
    ``library_dir`` under ``root_dir``, then cleans up the temporary zip.

    Args:
        library_key: LibraryLocatorV2 for the library to export
        root_dir: Root directory where library_dir will be created
        library_dir: Directory name under root_dir to extract the library into
        username: Username string for the backup API (optional)

    Raises:
        Exception: If backup creation or extraction fails
        DoesNotExist: If the specified user does not exist
    """
    # Get user object for backup API (if username provided)
    user_obj = None
    if username:
        # Let it raise if given user doesn't exist
        user_obj = get_user_model().objects.get(username=username)

    temp_dir, zip_path = create_library_v2_zip(library_key, user_obj)

    try:
        target_dir = os.path.join(root_dir, library_dir)
        os.makedirs(target_dir, exist_ok=True)
        # Extract zip contents (will overwrite existing files)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(target_dir)
        log.info('Extracted library v2 backup to %s', target_dir)
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
