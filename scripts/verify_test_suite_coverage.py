"""
Fail if any test file belongs to no shard in unit-test-shards.json.

That file is maintained by hand and cannot simply be the repository roots: a few
apps under openedx/ -- content_staging, content_tagging -- are Studio-only and
raise at import under lms settings, so the lms and cms shards each cover part of
the tree rather than either covering all of it. The list therefore drifts when a
new Django app is added, and a drifting list means tests that silently never run.

Replaces collect-and-verify, which compared test *counts* between the shard list
and the roots. That needed every Python dependency and two full pytest
collections (~2 minutes) to report only that two numbers disagreed. Comparing
paths reads the file tree in a few seconds and names the directory at fault.
"""
import json
import pathlib
import sys

SHARDS_JSON = '.github/workflows/unit-test-shards.json'
ROOTS = ('lms', 'cms', 'openedx', 'common/djangoapps', 'xmodule')
TEST_GLOBS = ('test_*.py', 'tests.py', 'tests_*.py', '*_tests.py')
# Mirrors norecursedirs in pyproject.toml, plus trees that hold fixtures rather
# than collectable tests.
SKIP = ('node_modules', '/envs/', '/migrations/', 'test_root', '/.git/', '/features/')


def covered_paths():
    with open(SHARDS_JSON) as shards_file:
        shards = json.load(shards_file)
    return tuple({path for shard in shards.values() for path in shard['paths']})


def find_test_files():
    for root in ROOTS:
        for glob in TEST_GLOBS:
            for path in pathlib.Path(root).rglob(glob):
                text = str(path)
                if not any(skip in f'/{text}' for skip in SKIP):
                    yield text


def main():
    prefixes = covered_paths()
    uncovered = sorted({
        str(pathlib.Path(f).parent) for f in find_test_files()
        if not f.startswith(prefixes)
    })
    if uncovered:
        print("::error title=unit-test-shards.json is out of date::"
              "These directories contain tests that no shard in "
              f"{SHARDS_JSON} covers, so they never run in CI. Add them to a "
              "shard -- a cms.envs.test one if they need Studio settings.")
        for directory in uncovered:
            print(f"  {directory}")
        sys.exit(1)
    print(f"All test files are covered by {len(prefixes)} shard paths.")


if __name__ == "__main__":
    main()
