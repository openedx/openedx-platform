"""
Safe version of extractall which does not extract any files that would
be, or symlink to a file that is, outside of the directory extracted in.

Adapted from:
http://stackoverflow.com/questions/10060069/safely-extract-zip-or-tar-using-python
"""

import logging
from os.path import abspath, dirname, realpath
from os.path import join as joinpath
from tarfile import TarFile, TarInfo
from typing import List, Union  # noqa: UP035
from zipfile import ZipFile, ZipInfo

from django.conf import settings
from django.core.exceptions import SuspiciousOperation

log = logging.getLogger(__name__)


def resolved(rpath):
    """
    Returns the canonical absolute path of `rpath`.
    """
    return realpath(abspath(rpath))


def _is_bad_path(path, base):
    """
    Is (the canonical absolute path of) `path` outside `base`?
    """
    return not resolved(joinpath(base, path)).startswith(base)


def _is_bad_link(info, base):
    """
    Does the file sym- or hard-link to files outside `base`?
    """
    # Links are interpreted relative to the directory containing the link
    tip = resolved(joinpath(base, dirname(info.name)))
    return _is_bad_path(info.linkname, base=tip)


def _check_tarinfo(finfo: TarInfo, base: str):
    """
    Checks a file in a tar archive (TarInfo object) for safety.

    It ensures that the file isn't a hard link or symlink to a file pointing to
    a path outside the archive and checks that the file isn't a device file.

    Raises:
        SuspiciousOperation: If the TarInfo object is found to be a
        hard link, symlink, or a special device file.
    """
    if finfo.issym() and _is_bad_link(finfo, base):
        log.debug("File %r is blocked: Hard link to %r", finfo.name, finfo.linkname)
        raise SuspiciousOperation("Hard link")
    if finfo.islnk() and _is_bad_link(finfo, base):
        log.debug("File %r is blocked: Symlink to %r", finfo.name, finfo.linkname)
        raise SuspiciousOperation("Symlink")
    if finfo.isdev():
        log.debug("File %r is blocked: FIFO, device or character file", finfo.name)
        raise SuspiciousOperation("Dev file")


def _checkmembers(members: Union[List[ZipInfo], List[TarInfo]], base: str):  # noqa: UP006, UP007
    """
    Check that all elements of the archive file are safe.
    """
    base = resolved(base)

    # check that we're not trying to import outside of the github_repo_root
    if not base.startswith(resolved(settings.GITHUB_REPO_ROOT)):
        raise SuspiciousOperation("Attempted to import course outside of data dir")

    for finfo in members:
        if isinstance(finfo, ZipInfo):
            filename = finfo.filename
        elif isinstance(finfo, TarInfo):
            filename = finfo.name
            _check_tarinfo(finfo, base)
        if _is_bad_path(filename, base):
            log.debug("File %r is blocked (illegal path)", filename)
            raise SuspiciousOperation("Illegal path")


def safe_extractall(file_name, output_path):
    """
    Extract Zip or Tar files
    """
    archive = None
    if not output_path.endswith("/"):
        output_path += "/"
    try:
        if file_name.endswith(".zip"):
            archive = ZipFile(file_name, "r")
            members = archive.infolist()
        elif file_name.endswith(".tar.gz"):
            archive = TarFile.open(file_name)
            members = archive.getmembers()
        else:
            raise ValueError("Unsupported archive format")
        _checkmembers(members, output_path)
        archive.extractall(output_path)
    finally:
        if archive:
            archive.close()
