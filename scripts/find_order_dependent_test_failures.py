"""
This script can be used to find the fewest number of tests required to get a
failure, in cases where a test failure is dependent on test order.

The script performs the following:
1. It strips the console log of a pytest-xdist Jenkins run into the test
lists of each pytest worker until it finds the first failure.
2. It makes sure that running the single failing test doesn't fail on its
own.
3. It then finds the fewest number of tests required to continue to see the
failure, and outputs the pytest command needed to replicate.

Sample usage::

    python scripts/find_order_dependent_test_failures.py --test-file test_list.txt

"""


import os  # noqa: I001
import re
import shutil
import tempfile

import click

OUTPUT_FOLDER_NAME = "test_order_files"
verbose_option = None


@click.command()
@click.option(
    '--test-file',
    help="File name of a .txt file containing a list of passing tests and ending with a single "
         "failing test from a test run that may have an order dependency.",
    required=True
)
@click.option(
    '--verbose/--quiet',
    help="Verbose includes the test output.",
    default=None
)
def main(test_file, verbose):
    """
    Script to find simplest duplication of a test order issue.

    Note: The script used to be able to do the following:
    1. Pulling tests from test output (like pytest -v), and finding the test names.
    2. Filtering down to a single test per file in a fast_mode for finding certain types of errors.
    3. Returning a list of passing tests followed by a single failing test.

    Unless that functionality is added back, the above steps must be done manually.
    """
    global verbose_option
    verbose_option = verbose

    _clean_output_folder()

    failing_test_list = _load_test_file(test_file)

    if not failing_test_list:
        print('No failures found in log file.')
        return

    if _create_and_check_test_files_for_failures(failing_test_list[-1:], 'SINGLE'):
        print("Single test failed. Failures not dependent on order.")
        return

    test_list_with_failures, pytest_command = _find_fewest_tests_with_failures(failing_test_list, 'ALL')
    if test_list_with_failures:
        print(f'Found failures running {len(test_list_with_failures)} tests.')
        print(f'Use: {pytest_command}')
        return

    print('No tests failed locally.')


def _clean_output_folder():
    if os.path.isdir(OUTPUT_FOLDER_NAME):
        shutil.rmtree(OUTPUT_FOLDER_NAME)
    os.mkdir(OUTPUT_FOLDER_NAME)


def _load_test_file(test_file):
    """
    Returns list of tests from the provided file.
    """
    test_list = []
    with open(test_file) as console_file:
        for line in console_file:
            test_list.append(line)
    return test_list


def _get_pytest_command(output_file_name):
    """
    Return the pytest command to run.
    """
    return f"pytest -p 'no:randomly' `cat {output_file_name}`"


def _run_tests_and_check_for_failures(output_file_name):
    """
    Runs tests and returns True if failures are found.
    """
    global verbose_option
    pytest_command = _get_pytest_command(output_file_name)
    test_output = os.popen(pytest_command).read()
    if verbose_option:
        print(test_output)
    failures_search = re.search(r'=== (\d+) failed', test_output)
    return bool(failures_search) and int(failures_search.group(1)) > 0


def _create_and_check_test_files_for_failures(test_list, test_type):
    """
    Run the test list to see if there are any failures.

    Keeps around any test files that produced a failure, and deletes
    the passing files.

    Returns the pytest command to run if failures are found.
    """
    print(f"Testing {test_type}, includes {len(test_list)} test(s)...")
    output_file_name = f"{OUTPUT_FOLDER_NAME}_failing_test_list_{test_type}.txt"
    output_file_path = os.path.join(OUTPUT_FOLDER_NAME, output_file_name)
    # Note: We don't really need a temporary file, and could just output the tests directly
    # to the command line, but this keeps the verbose  output cleaner.
    temp_file = tempfile.NamedTemporaryFile(prefix=output_file_name, dir=OUTPUT_FOLDER_NAME, delete=False)

    with open(temp_file.name, 'w') as output_file:
        for line in test_list:
            output_file.write(line + "\n")
    temp_file.close()

    if _run_tests_and_check_for_failures(temp_file.name):
        os.rename(temp_file.name, output_file_path)
        print('- test failures found.')
        return _get_pytest_command(output_file_path)

    os.remove(temp_file.name)
    print('- no failures found.')
    return None


def _find_fewest_tests_with_failures(test_list, test_type):
    """
    Recursively tests half the tests, finding the smallest number of tests to obtain a failure.

    Returns:
        (test_list, pytest_command): Tuple with the smallest test_list and the pytest_command
            to be used for testing. Returns (None, None) if no failures are found.
    """
    if len(test_list) <= 1:
        return None, None

    pytest_command = _create_and_check_test_files_for_failures(test_list, test_type)
    if not pytest_command:
        return None, None

    if len(test_list) == 2:
        return test_list, pytest_command

    half_tests_num = round((len(test_list) - 1) / 2)
    failing_test = test_list[-1:]
    test_list_a = test_list[0:half_tests_num] + failing_test
    test_list_b = test_list[half_tests_num:]
    failing_test_list_a, pytest_command_a = _find_fewest_tests_with_failures(test_list_a, 'GROUP-A')
    if failing_test_list_a:
        return failing_test_list_a, pytest_command_a

    failing_test_list_b, pytest_command_b = _find_fewest_tests_with_failures(test_list_b, 'GROUP-B')
    if failing_test_list_b:
        return failing_test_list_b, pytest_command_b

    # This could occur if there is a complex set of dependencies where the
    # original list fails, but neither of its halves (A or B) fail.
    return test_list, pytest_command


if __name__ == "__main__":
    main()
