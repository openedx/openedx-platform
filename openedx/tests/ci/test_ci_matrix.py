"""
Since we have defined the test matrix manually in quality checks to achieve an optimized test build time,
this file is added to keep track if the ci matrices are up-to-date
"""
import os  # noqa: I001

import yaml

DIRS_TO_EXCLUDE = [
    'lms/djangoapps',
    'lms/static',
    'lms/templates',
    'openedx/core',
    'openedx/core/djangoapps'
]


def valid_directory(path):
    if path not in DIRS_TO_EXCLUDE and '__pycache__' not in path:
        return True

    return False


def test_quality_matrix_is_complete():
    """
    This test should fail if a new python directory/file is added in code but quality checks don't cover it
    """
    quality_ci_yml = f'{os.getcwd()}/.github/workflows/pylint-checks.yml'
    with open(quality_ci_yml) as fp:
        quality_yaml = yaml.safe_load(fp)

        matrix_dirs = []
        for matrix_item in quality_yaml['jobs']['run-pylint']['strategy']['matrix']['include']:
            matrix_dirs.extend(matrix_item['path'].split(' '))

        for module in ['lms', 'lms/djangoapps', 'openedx', 'openedx/core', 'openedx/core/djangoapps']:
            directories = [f.path for f in os.scandir(module) if f.is_dir() and valid_directory(f.path)]

            for sub_dir in directories:
                assert f'{sub_dir}/' in matrix_dirs, f'Please add {sub_dir} in quality matrix CI or exclude list'
