#!/usr/bin/env python
"""
Extremely simple wrapper to run `npm` commands from python venvs which install us.

Don't add anything onto this, please.
"""
import shlex
import subprocess
import sys
from pathlib import Path


def main():
    """
    Excecute the script
    """
    openedx_core_lib_npm = Path(__file__)
    script_name = openedx_core_lib_npm.name
    openedx_core = openedx_core_lib_npm.parent.parent
    repo_root = openedx_core.parent.parent
    if not (repo_root / "package.json").is_file():
        raise SystemExit(f"{script_name} could not find root of openedx-platform repository")
    command = ["npm", *sys.argv[1:]]
    print(f"{script_name}: running '{shlex.join(command)}' in {repo_root}", flush=True)
    result = subprocess.run(command, cwd=repo_root, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    sys.exit(main())
