# lint-amnesty, pylint: disable=django-not-configured, missing-module-docstring
# xsslint config module for edx-platform


import os
import sys

# Temporarily add xsslint to sys.path so that we can import from it. This won't be necessary once
# xsslint is moved out of edx-platform.
scripts_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(scripts_dir, 'xsslint'))
# pylint: disable=import-error,wrong-import-position
from xsslint.linters import (
    DjangoTemplateLinter,
    JavaScriptLinter,
    MakoTemplateLinter,
    PythonLinter,
    UnderscoreTemplateLinter,
)

# Define the directories that should be ignored by the script.
SKIP_DIRS = (
    '.git',
    '.pycharm_helpers',
    'common/static/xmodule/modules',
    'common/static/bundles',
    'docs',
    'perf_tests',
    'node_modules',
    'reports/diff_quality',
    'scripts/xsslint',
    'spec',
    'test_root',
    'vendor',
)


UNDERSCORE_SKIP_DIRS = SKIP_DIRS + ('test',)
UNDERSCORE_LINTER = UnderscoreTemplateLinter(
    skip_dirs=UNDERSCORE_SKIP_DIRS
)


JAVASCRIPT_SKIP_DIRS = SKIP_DIRS + ('i18n',)
JAVASCRIPT_LINTER = JavaScriptLinter(
    underscore_linter=UNDERSCORE_LINTER,
    javascript_skip_dirs=JAVASCRIPT_SKIP_DIRS,
)


PYTHON_SKIP_DIRS = SKIP_DIRS + ('tests', 'test/acceptance')
PYTHON_LINTER = PythonLinter(
    skip_dirs=PYTHON_SKIP_DIRS
)


MAKO_SKIP_DIRS = SKIP_DIRS
MAKO_LINTER = MakoTemplateLinter(
    javascript_linter=JAVASCRIPT_LINTER,
    python_linter=PYTHON_LINTER,
    skip_dirs=MAKO_SKIP_DIRS
)

DJANGO_SKIP_DIRS = SKIP_DIRS
DJANGO_LINTER = DjangoTemplateLinter(
    skip_dirs=DJANGO_SKIP_DIRS
)

# (Required) Define the linters (code-checkers) that should be run by the script.
LINTERS = (DJANGO_LINTER, MAKO_LINTER, UNDERSCORE_LINTER, JAVASCRIPT_LINTER, PYTHON_LINTER)
