"""
Regression test for bug #36305 — xmlsec / lxml version mismatch on open-release/sumac.master.

The latest PyPI wheels for xmlsec and lxml link against a newer libxml2 than what ships
on Ubuntu 24.04 GitHub Actions runners, which raises a version-mismatch RuntimeError at
import time. The fix pins both packages in requirements/constraints.txt; this module is a
tripwire that ensures the pins stay in place on this branch.

See: https://github.com/openedx/edx-platform/issues/36305
See: https://github.com/openedx/edx-platform/issues/36695
"""

import os
import re
import unittest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONSTRAINTS_PATH = os.path.join(REPO_ROOT, "requirements", "constraints.txt")


def _read_constraints():
    """Return the contents of requirements/constraints.txt as a single string."""
    with open(CONSTRAINTS_PATH, encoding="utf-8") as handle:
        return handle.read()


class TestBug36305Regression(unittest.TestCase):
    """Ensure lxml and xmlsec stay pinned so CI does not pull a libxml2-incompatible wheel."""

    def test_unit_bug_36305_regression_xmlsec_is_pinned(self):
        """xmlsec must be pinned to a version compatible with the system libxml2."""
        contents = _read_constraints()
        self.assertRegex(
            contents,
            r"^xmlsec==1\.3\.14\s*$",
            msg="xmlsec must be pinned to 1.3.14 to avoid libxml2 mismatch (bug #36305).",
        )

    def test_unit_bug_36305_regression_lxml_is_pinned(self):
        """lxml must be pinned to an exact version."""
        contents = _read_constraints()
        match = re.search(r"^lxml==(\S+)\s*$", contents, flags=re.MULTILINE)
        self.assertIsNotNone(
            match,
            msg="lxml must be pinned to an exact version to avoid libxml2 mismatch (bug #36305).",
        )

    def test_integration_bug_36305_regression_import_xmlsec_succeeds(self):
        """Importing xmlsec must not raise a version-mismatch RuntimeError."""
        try:
            import xmlsec  # noqa: F401  pylint: disable=import-outside-toplevel,unused-import
        except RuntimeError as exc:  # pragma: no cover - only fires on broken wheel
            self.fail(
                "xmlsec import failed with RuntimeError (likely libxml2 version mismatch, "
                "bug #36305): {err}".format(err=exc)
            )
