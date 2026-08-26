"""
Bump the pinned version of a single package in [tool.edx_lint].uv_constraints.

Used by the "Upgrade one Python dependency" workflow (previously sed-patched
requirements/constraints.txt directly; the constraint now lives in pyproject.toml
instead). Reads PACKAGE and NEW_VERSION from the environment so the caller doesn't
need to worry about shell-quoting either one.
"""
import os
import re

import tomlkit

PYPROJECT_PATH = "pyproject.toml"


def normalized_name(spec):
    return re.split(r"[<>=!~\[; ]", spec, maxsplit=1)[0].lower().replace("_", "-")


def bump_edx_lint_uv_constraints(target, new_version):
    doc = tomlkit.parse(open(PYPROJECT_PATH, encoding="utf-8").read())
    constraints = doc["tool"]["edx_lint"]["uv_constraints"]
    found = False
    for i, spec in enumerate(constraints):
        if normalized_name(str(spec)) == target and "==" in str(spec):
            constraints[i] = re.sub(r"==[^,]+", f"=={new_version}", str(spec))
            found = True
    if found:
        open(PYPROJECT_PATH, "w", encoding="utf-8").write(tomlkit.dumps(doc))
    return found


def main():
    package = os.environ["PACKAGE"]
    new_version = os.environ["NEW_VERSION"]
    target = normalized_name(package)

    bump_edx_lint_uv_constraints(target, new_version)


if __name__ == "__main__":
    main()
