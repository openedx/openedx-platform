"""
Information about the release line of this Open edX code.
"""


import unittest

# The release line: an Open edX release name ("ficus"), or "master".
# This should always be "master" on the master branch, and will be changed
# manually when we start release-line branches, like open-release/ficus.master.
RELEASE_LINE = "master"


def doc_version():
    """The docs.openedx.org version name used in documentation references.

    docs.openedx.org only publishes a "latest" version (and, at most, the
    current named release under a bare slug like "ulmo"). The legacy
    "open-release-<line>.master" slugs do not exist there, so help links
    generated on named release lines 404 for operators and course authors.
    Always point at "latest".

    Returns a short string like "latest".
    """
    return "latest"


def skip_unless_master(func_or_class):
    """
    Only run the decorated test for code on master or destined for master.

    Use this to skip tests that we expect to fail on a named release branch.
    Please use carefully!
    """
    return unittest.skipUnless(RELEASE_LINE == "master", "Test often fails on named releases")(func_or_class)
