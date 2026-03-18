#!/usr/bin/env bash
#
# Recursively verify that every directory in edx-platform that contains
# Python source code also contains an __init__.py file (aka a "dunder-init file").
#
# Even though the Python 3 runner does not require that an __init__.py file
# exist in every Python directory, some of our tooling (namely, pylint and
# import-linter) depend on it existing, and will report false-positive success
# otherwise.
#
# Run from root of directory with no args.
# Example:
#   scripts/verify-dunder-init.sh
#
# Exits 0 if no errors, 1 otherwise.
# Missing __init__.py files are printed to STDOUT.
# Extra info is printed to STDIN.

set -euo pipefail # Strict mode.


## Directories that contain Python source code, but for which we don't care
## whether or not there's a __init__.py file.
exclude=''

# Exclude repo root.
exclude+='^\.$'

# Exclude test data that includes Python (do NOT exclude unit test source code, though).
exclude+='|^common/test/data/?.*$'

# xmodule data folder
exclude+='|^xmodule/tests/data/xml-course-root/capa$'
exclude+='|^xmodule/tests/data/xml-course-root/uploads/python_lib_zip$'

# Docs, scripts.
exclude+='|^docs/.*$'
exclude+='|^lms/djangoapps/monitoring/scripts$'
exclude+='|^scripts/?.*$'


## Counters for directories.
no_python=0
excluded=0
errored=0
confirmed=0


## Loop through all directories that are under version control.
>&2 echo
>&2 echo "============== begin list of missing files =============="
for directory in $(git ls-files | xargs dirname | sort | uniq) ; do
	if ! ls "$directory"/*.py &>/dev/null ; then
		# No Python in this directory; skip it.
		no_python=$(( no_python+1 ))
		continue
	fi
	if [[ "$directory" =~ $exclude ]]; then
		excluded=$(( excluded+1 ))
		# Directory is specifically excluded; skip it.
		continue
	fi
	if [[ -f "$directory"/__init__.py ]] ; then
		# Directory contains __init__.py; all good!
		confirmed=$(( confirmed+1 ))
		continue
	fi
	# Error! Print missing file to STDOUT.
	errored=$(( errored+1 ))
	echo "$directory/__init__.py"
done
>&2 echo "=============== end list of missing files ==============="


## Report results (to STDERR)
>&2 echo
>&2 echo "${no_python} directories do not contain Python source code."
>&2 echo "${excluded} directories contain Python source code, but are excluded."
>&2 echo
>&2 echo "${confirmed} Python source directories DO contain an __init__.py file."
>&2 echo "${errored} Python source directories do NOT contain an __init__.py file."
>&2 echo


## Succeed or fail.
if ! [[ errored -eq 0 ]] ; then
	>&2 echo "Check failed! All directories with Python source code must contain __init__.py (unless excluded)."
	exit 1
else
	>&2 echo "Check passed!"
	exit 0
fi
