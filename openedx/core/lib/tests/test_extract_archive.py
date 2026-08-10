"""
Tests for openedx.core.lib.extract_archive.

The path-containment helpers must treat ``base`` as a directory boundary, not
a raw string prefix: a sibling whose name extends ``base`` as a string prefix
(e.g. ``<parent>/foo_evil/x`` next to ``<parent>/foo``) is *outside* ``base``.
These tests pin that boundary down at the helper level and end-to-end through
``safe_extractall``.
"""

import io
import os
import tarfile
import tempfile

import pytest
from django.core.exceptions import SuspiciousOperation
from django.test import override_settings

from openedx.core.lib.extract_archive import _is_bad_path, safe_extractall

# Direct tests of the path-containment helper. No Django settings needed.

def test_is_bad_path_prefix_bypass_is_rejected():
    """
    A sibling path whose name extends the base's name as a raw string prefix
    (e.g. ``<parent>/Y29evil/file`` vs base ``<parent>/Y29``) is outside
    ``base`` and must be flagged as bad.
    """
    with tempfile.TemporaryDirectory() as parent:
        base = os.path.join(parent, "Y29")
        os.mkdir(base)
        assert _is_bad_path("../Y29evil/file", base) is True


def test_is_bad_path_inside_base_is_accepted():
    with tempfile.TemporaryDirectory() as parent:
        base = os.path.join(parent, "Y29")
        os.mkdir(base)
        assert _is_bad_path("nested/file.txt", base) is False


def test_is_bad_path_traversal_outside_base_is_rejected():
    with tempfile.TemporaryDirectory() as parent:
        base = os.path.join(parent, "Y29")
        os.mkdir(base)
        assert _is_bad_path("../../etc/passwd", base) is True


# End-to-end tests of safe_extractall against crafted .tar.gz archives.

def _add_file(tar, name, content=b""):
    info = tarfile.TarInfo(name=name)
    info.size = len(content)
    tar.addfile(info, io.BytesIO(content))


def _add_symlink(tar, name, linkname):
    info = tarfile.TarInfo(name=name)
    info.type = tarfile.SYMTYPE
    info.linkname = linkname
    tar.addfile(info)


def test_safe_extractall_blocks_file_entry_with_prefix_bypass(tmp_path):
    root = str(tmp_path)
    extract_dir = os.path.join(root, "Y29")
    os.mkdir(extract_dir)
    archive = os.path.join(root, "malicious.tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        _add_file(tar, "../Y29evil/sentinel.txt", b"owned")

    with override_settings(GITHUB_REPO_ROOT=root):
        with pytest.raises(SuspiciousOperation):
            safe_extractall(archive, extract_dir)

    escape_target = os.path.join(root, "Y29evil", "sentinel.txt")
    assert not os.path.exists(escape_target)


def test_safe_extractall_blocks_symlink_target_with_prefix_bypass(tmp_path):
    root = str(tmp_path)
    extract_dir = os.path.join(root, "Y29")
    os.mkdir(extract_dir)
    archive = os.path.join(root, "malicious.tar.gz")
    # symlink inside extract_dir pointing at a sibling whose name extends
    # extract_dir's basename as a string prefix.
    with tarfile.open(archive, "w:gz") as tar:
        _add_symlink(tar, name="link", linkname="../Y29evil/secret")

    with override_settings(GITHUB_REPO_ROOT=root):
        with pytest.raises(SuspiciousOperation):
            safe_extractall(archive, extract_dir)
