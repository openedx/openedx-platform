"""
Regression tests for ``openedx.core.lib.extract_archive.safe_extractall``
zip/tar bomb guards.
"""
import os
import tempfile
import zipfile

import pytest
from django.conf import settings
from django.core.exceptions import SuspiciousOperation
from django.test import SimpleTestCase
from django.test.utils import override_settings

from openedx.core.lib.extract_archive import _check_archive_bomb, safe_extractall


class CheckArchiveBombUnitTests(SimpleTestCase):
    """
    Unit tests for the ``_check_archive_bomb`` helper. These work on
    in-memory ``ZipInfo``/``TarInfo`` lists and do not touch the
    filesystem so they remain fast and hermetic.
    """

    def _make_zip_members(self, sizes):
        """
        Build a list of ``ZipInfo`` objects with the given uncompressed
        sizes. ``compress_size`` is irrelevant to ``_check_archive_bomb``
        (it only inspects ``file_size`` and the on-disk archive size).
        """
        members = []
        for idx, size in enumerate(sizes):
            info = zipfile.ZipInfo(filename=f'member_{idx}.xml')
            info.file_size = size
            members.append(info)
        return members

    @override_settings(
        COURSE_IMPORT_MAX_EXTRACTED_SIZE=100,
        COURSE_IMPORT_MAX_EXTRACTED_FILES=1000,
        COURSE_IMPORT_MAX_COMPRESSION_RATIO=10,
    )
    def test_rejects_oversized_archive(self):
        members = self._make_zip_members([50, 60])  # sums to 110 > 100
        with pytest.raises(SuspiciousOperation, match='Archive too large'):
            _check_archive_bomb(members, compressed_size=100)

    @override_settings(
        COURSE_IMPORT_MAX_EXTRACTED_SIZE=10_000_000,
        COURSE_IMPORT_MAX_EXTRACTED_FILES=3,
        COURSE_IMPORT_MAX_COMPRESSION_RATIO=1000,
    )
    def test_rejects_too_many_members(self):
        members = self._make_zip_members([10, 10, 10, 10])  # 4 > 3
        with pytest.raises(SuspiciousOperation, match='too many files'):
            _check_archive_bomb(members, compressed_size=10_000)

    @override_settings(
        COURSE_IMPORT_MAX_EXTRACTED_SIZE=10_000_000,
        COURSE_IMPORT_MAX_EXTRACTED_FILES=1000,
        COURSE_IMPORT_MAX_COMPRESSION_RATIO=10,
    )
    def test_rejects_high_compression_ratio(self):
        # 10_000 uncompressed / 50 compressed = ratio 200, way above 10
        members = self._make_zip_members([10_000])
        with pytest.raises(SuspiciousOperation, match='compression ratio too high'):
            _check_archive_bomb(members, compressed_size=50)

    @override_settings(
        COURSE_IMPORT_MAX_EXTRACTED_SIZE=10_000_000,
        COURSE_IMPORT_MAX_EXTRACTED_FILES=1000,
        COURSE_IMPORT_MAX_COMPRESSION_RATIO=10,
    )
    def test_accepts_normal_archive(self):
        # 5000 uncompressed / 1000 compressed = ratio 5, well under 10
        members = self._make_zip_members([2_000, 3_000])
        _check_archive_bomb(members, compressed_size=1_000)

    def test_empty_archive_ok(self):
        # An empty member list must not raise despite compressed_size=0
        _check_archive_bomb([], compressed_size=0)


class SafeExtractallIntegrationTests(SimpleTestCase):
    """
    End-to-end tests that build a real small zip on disk and confirm
    ``safe_extractall`` honors the bomb-guard budget.
    """

    def setUp(self):
        super().setUp()
        # ``_checkmembers`` requires the extraction target to live under
        # ``settings.GITHUB_REPO_ROOT``. Create a scratch subdirectory
        # there (creating the root itself if a sparse test settings file
        # has not materialized it) and clean it up on teardown.
        os.makedirs(settings.GITHUB_REPO_ROOT, exist_ok=True)
        self._scratch = tempfile.mkdtemp(
            prefix='test_extract_archive_', dir=settings.GITHUB_REPO_ROOT,
        )
        self.addCleanup(lambda: os.path.isdir(self._scratch)
                        and __import__('shutil').rmtree(self._scratch))

    def _make_zip(self, tmpdir, payloads):
        zip_path = os.path.join(tmpdir, 'archive.zip')
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zf:
            for name, data in payloads.items():
                zf.writestr(name, data)
        return zip_path

    def test_rejects_bomb_before_writing_anything(self):
        outdir = tempfile.mkdtemp(prefix='out_', dir=self._scratch)
        srcdir = tempfile.mkdtemp(prefix='src_', dir=self._scratch)
        # Make a file that compresses extremely well: 1 MB of the
        # same byte. ``ZIP_DEFLATED`` gives a very high ratio.
        zip_path = os.path.join(srcdir, 'bomb.zip')
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('inflated.txt', b'A' * 1_000_000)

        # Lower the size cap to 10 KB so the 1 MB payload unambiguously
        # exceeds it, independently of any compression-ratio check.
        with override_settings(
            COURSE_IMPORT_MAX_EXTRACTED_SIZE=10_000,
            COURSE_IMPORT_MAX_EXTRACTED_FILES=1000,
            COURSE_IMPORT_MAX_COMPRESSION_RATIO=1_000_000,
        ), pytest.raises(SuspiciousOperation):
            safe_extractall(zip_path, outdir)
        # No files were written to the output directory
        assert os.listdir(outdir) == []

    def test_happy_path_normal_archive(self):
        outdir = tempfile.mkdtemp(prefix='out_', dir=self._scratch)
        srcdir = tempfile.mkdtemp(prefix='src_', dir=self._scratch)
        zip_path = self._make_zip(
            srcdir, {'a.xml': b'<xml/>', 'b.xml': b'<xml/>'},
        )
        safe_extractall(zip_path, outdir)
        assert sorted(os.listdir(outdir)) == ['a.xml', 'b.xml']
