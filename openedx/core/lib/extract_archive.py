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

# Default decompression budget for course archives. These thresholds block
# zip / tar bombs during course import without rejecting any realistic edX
# course export. Operators can override each threshold via Django settings:
#
#   COURSE_IMPORT_MAX_EXTRACTED_SIZE   (default 2 GB)
#   COURSE_IMPORT_MAX_EXTRACTED_FILES  (default 50 000)
#   COURSE_IMPORT_MAX_COMPRESSION_RATIO (default 200x)
#
# Rationale:
#   * 2 GB uncompressed covers large video-heavy OLX exports with margin.
#   * 50 000 files covers heavily fragmented OLX (real exports observed
#     in the 1000-5000 file range).
#   * 200x compression ratio is well above any legitimate compression
#     (XML text ~10x, media ~1x) and rejects canonical 42.zip-style bombs
#     whose ratio is upwards of 10^6.
_DEFAULT_MAX_EXTRACTED_SIZE = 2 * 1024 * 1024 * 1024
_DEFAULT_MAX_EXTRACTED_FILES = 50_000
_DEFAULT_MAX_COMPRESSION_RATIO = 200


def _get_archive_limits():
    """
    Resolve the three archive-size thresholds from Django settings so
    tests and operators can override them without patching this module.
    """
    return (
        getattr(settings, 'COURSE_IMPORT_MAX_EXTRACTED_SIZE', _DEFAULT_MAX_EXTRACTED_SIZE),
        getattr(settings, 'COURSE_IMPORT_MAX_EXTRACTED_FILES', _DEFAULT_MAX_EXTRACTED_FILES),
        getattr(settings, 'COURSE_IMPORT_MAX_COMPRESSION_RATIO', _DEFAULT_MAX_COMPRESSION_RATIO),
    )


def _check_archive_bomb(members, compressed_size):
    """
    Reject archives whose declared uncompressed size, file count, or
    compression ratio exceed the configured budget. Raises
    ``SuspiciousOperation`` on any violation. The check runs *before*
    ``archive.extractall`` writes anything to disk, so a pathological
    archive never materializes a byte on the target filesystem.
    """
    max_size, max_files, max_ratio = _get_archive_limits()

    if len(members) > max_files:
        log.debug(
            "Archive blocked: %d members exceeds limit of %d",
            len(members), max_files,
        )
        raise SuspiciousOperation("Archive contains too many files")

    total_uncompressed = 0
    for finfo in members:
        if isinstance(finfo, ZipInfo):
            member_size = finfo.file_size
        elif isinstance(finfo, TarInfo):
            member_size = finfo.size if finfo.isfile() else 0
        else:  # defensive: safe_extractall only yields the two types above
            member_size = 0
        total_uncompressed += member_size
        if total_uncompressed > max_size:
            log.debug(
                "Archive blocked: uncompressed size %d exceeds limit of %d",
                total_uncompressed, max_size,
            )
            raise SuspiciousOperation("Archive too large")

    # Compression-ratio guard: use max(compressed_size, 1) to avoid a
    # divide-by-zero on empty archives, which are harmless anyway.
    if total_uncompressed // max(compressed_size, 1) > max_ratio:
        log.debug(
            "Archive blocked: compression ratio %d exceeds limit of %d",
            total_uncompressed // max(compressed_size, 1), max_ratio,
        )
        raise SuspiciousOperation("Archive compression ratio too high")


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
    import os  # local import: stdlib, not module-hot

    archive = None
    if not output_path.endswith("/"):
        output_path += "/"
    try:
        compressed_size = os.path.getsize(file_name)
        if file_name.endswith(".zip"):
            archive = ZipFile(file_name, "r")
            members = archive.infolist()
        elif file_name.endswith(".tar.gz"):
            archive = TarFile.open(file_name)
            members = archive.getmembers()
        else:
            raise ValueError("Unsupported archive format")
        _checkmembers(members, output_path)
        _check_archive_bomb(members, compressed_size)
        archive.extractall(output_path)
    finally:
        if archive:
            archive.close()
