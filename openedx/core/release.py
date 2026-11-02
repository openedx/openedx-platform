"""
Information about the release line of this Open edX code.
"""


import unittest

# The release line: an Open edX release name ("ficus"), or "master".
# This should always be "master" on the master branch, and will be changed
# manually when we start release-line branches, like open-release/ficus.master.
RELEASE_LINE = "ulmo"


def doc_version():
    """The readthedocs.org version name used in documentation references.

    Returns a short string like "latest" or "open-release-ficus.master".
    """
    if RELEASE_LINE == "master":
        return "latest"
    else:
        return f"open-release-{RELEASE_LINE}.master"


def skip_unless_master(func_or_class):
    """
    Only run the decorated test for code on master or destined for master.

    Use this to skip tests that we expect to fail on a named release branch.
    Please use carefully!
    """
    return unittest.skipUnless(RELEASE_LINE == "master", "Test often fails on named releases")(func_or_class)
