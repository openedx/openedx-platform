#!/usr/bin/env python
"""
A linting tool to check for xss vulnerabilities.
"""


import argparse
import importlib
import json
import os
import re
import sys

from functools import reduce
from io import StringIO
from xsslint.reporting import SummaryResults
from xsslint.rules import RuleSet
from xsslint.utils import is_skip_dir


class BuildFailure(Exception):
    pass


def fail_quality(message):
    """
    Fail the specified quality check.
    """

    raise BuildFailure(message)


def _load_config_module(module_path):
    cwd = os.getcwd()
    if cwd not in sys.path:
        # Enable config module to be imported relative to wherever the script was run from.
        sys.path.append(cwd)
    return importlib.import_module(module_path)


def _build_ruleset(template_linters):
    """
    Combines the RuleSets from the provided template_linters into a single, aggregate RuleSet.

    Arguments:
        template_linters: A list of linting objects.

    Returns:
        The combined RuleSet.
    """
    return reduce(
        lambda combined, current: combined + current.ruleset,
        template_linters,
        RuleSet()
    )


def _process_file(full_path, template_linters, options, summary_results, out):
    """
    For each linter, lints the provided file.  This means finding and printing
    violations.

    Arguments:
        full_path: The full path of the file to lint.
        template_linters: A list of linting objects.
        options: A list of the options.
        summary_results: A SummaryResults with a summary of the violations.
        out: output file

    """
    num_violations = 0
    directory = os.path.dirname(full_path)
    file_name = os.path.basename(full_path)
    violations = []
    try:
        for template_linter in template_linters:
            results = template_linter.process_file(directory, file_name)
            violations += results.violations
            results.print_results(options, summary_results, out)
    except BaseException as e:
        raise Exception(f"Failed to process path: {full_path}") from e
    return violations


def _process_os_dir(directory, files, template_linters, options, summary_results, out):
    """
    Calls out to lint each file in the passed list of files.

    Arguments:
        directory: Directory being linted.
        files: All files in the directory to be linted.
        template_linters: A list of linting objects.
        options: A list of the options.
        summary_results: A SummaryResults with a summary of the violations.
        out: output file

    """
    violations = []
    for current_file in sorted(files, key=lambda s: s.lower()):
        full_path = os.path.join(directory, current_file)
        violations += _process_file(full_path, template_linters, options, summary_results, out)
    return violations


def _process_os_dirs(starting_dir, template_linters, options, summary_results, out):
    """
    For each linter, lints all the directories in the starting directory.

    Arguments:
        starting_dir: The initial directory to begin the walk.
        template_linters: A list of linting objects.
        options: A list of the options.
        summary_results: A SummaryResults with a summary of the violations.
        out: output file

    """
    violations = []
    skip_dirs = options.get('skip_dirs', ())
    for root, dirs, files in os.walk(starting_dir):
        if is_skip_dir(skip_dirs, root):
            del dirs
            continue
        dirs.sort(key=lambda s: s.lower())
        violations += _process_os_dir(root, files, template_linters, options, summary_results, out)
    return violations


def _get_xsslint_counts(result_contents):
    """
    This returns a dict of violations from the xsslint report.

    Arguments:
        filename: The name of the xsslint report.

    Returns:
        A dict containing the following:
            rules: A dict containing the count for each rule as follows:
                violation-rule-id: N, where N is the number of violations
            total: M, where M is the number of total violations

    """

    rule_count_regex = re.compile(r"^(?P<rule_id>[a-z-]+):\s+(?P<count>\d+) violations", re.MULTILINE)
    total_count_regex = re.compile(r"^(?P<count>\d+) violations total", re.MULTILINE)
    violations = {'rules': {}}
    for violation_match in rule_count_regex.finditer(result_contents):
        try:
            violations['rules'][violation_match.group('rule_id')] = int(violation_match.group('count'))
        except ValueError:
            violations['rules'][violation_match.group('rule_id')] = None
    try:
        violations['total'] = int(total_count_regex.search(result_contents).group('count'))
    # An AttributeError will occur if the regex finds no matches.
    # A ValueError will occur if the returned regex cannot be cast as a float.
    except (AttributeError, ValueError):
        violations['total'] = None
    return violations


def _check_violations(options, results):
    xsslint_script = "xss_linter.py"
    try:
        thresholds_option = options['thresholds']
        # Read the JSON file
        with open(thresholds_option, 'r') as file:
            violation_thresholds = json.load(file)

    except ValueError:
        violation_thresholds = None
    if isinstance(violation_thresholds, dict) is False or \
            any(key not in ("total", "rules") for key in violation_thresholds.keys()):
        print('xsslint')
        fail_quality("""FAILURE: Thresholds option "{thresholds_option}" was not supplied using proper format.\n"""
                     """Here is a properly formatted example, '{{"total":100,"rules":{{"javascript-escape":0}}}}' """
                     """with property names in double-quotes.""".format(thresholds_option=thresholds_option))

    try:
        metrics_str = "Number of {xsslint_script} violations: {num_violations}\n".format(
            xsslint_script=xsslint_script, num_violations=int(results['total'])
        )
        if 'rules' in results and any(results['rules']):
            metrics_str += "\n"
            rule_keys = sorted(results['rules'].keys())
            for rule in rule_keys:
                metrics_str += "{rule} violations: {count}\n".format(
                    rule=rule,
                    count=int(results['rules'][rule])
                )
    except TypeError:
        print('xsslint')
        fail_quality("FAILURE: Number of {xsslint_script} violations could not be found".format(
            xsslint_script=xsslint_script
        ))

    error_message = ""
    # Test total violations against threshold.
    if 'total' in list(violation_thresholds.keys()):
        if violation_thresholds['total'] < results['total']:
            error_message = "Too many violations total ({count}).\nThe limit is {violations_limit}.".format(
                count=results['total'], violations_limit=violation_thresholds['total']
            )

    # Test rule violations against thresholds.
    if 'rules' in violation_thresholds:
        threshold_keys = sorted(violation_thresholds['rules'].keys())
        for threshold_key in threshold_keys:
            if threshold_key not in results['rules']:
                error_message += (
                    "\nNumber of {xsslint_script} violations for {rule} could not be found"
                ).format(
                    xsslint_script=xsslint_script, rule=threshold_key
                )
            elif violation_thresholds['rules'][threshold_key] < results['rules'][threshold_key]:
                error_message += \
                    "\nToo many {rule} violations ({count}).\nThe {rule} limit is {violations_limit}.".format(
                        rule=threshold_key, count=results['rules'][threshold_key],
                        violations_limit=violation_thresholds['rules'][threshold_key],
                    )

    if error_message:
        print('xsslint')
        fail_quality("FAILURE: XSSLinter Failed.\n{error_message}\n"
                     "run the following command to hone in on the problem:\n"
                     "./scripts/xss-commit-linter.sh -h".format(error_message=error_message))
    else:
        print("successfully run xsslint")


def _lint(file_or_dir, template_linters, options, summary_results, out):
    """
    For each linter, lints the provided file or directory.

    Arguments:
        file_or_dir: The file or initial directory to lint.
        template_linters: A list of linting objects.
        options: A list of the options.
        summary_results: A SummaryResults with a summary of the violations.
        out: output file

    """

    if file_or_dir is not None and os.path.isfile(file_or_dir):
        violations = _process_file(file_or_dir, template_linters, options, summary_results, out)
    else:
        directory = "."
        if file_or_dir is not None:
            if os.path.exists(file_or_dir):
                directory = file_or_dir
            else:
                raise ValueError(f"Path [{file_or_dir}] is not a valid file or directory.")
        violations = _process_os_dirs(directory, template_linters, options, summary_results, out)

    for violation in violations:
        violation.print_results(options, sys.stdout)
    summary_results.print_results(options, out)
    result_output = _get_xsslint_counts(out.getvalue())
    _check_violations(options, result_output)


def main():
    """
    Used to execute the linter. Use --help option for help.

    Prints all violations.
    """
    epilog = "For more help using the xss linter, including details on how to\n"
    epilog += "understand and fix any violations, read the docs here:\n"
    epilog += "\n"
    # pylint: disable=line-too-long
    epilog += "  https://docs.openedx.org/en/latest/developers/references/developer_guide/preventing_xss/preventing_xss.html#xss-linter\n"

    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='Checks that templates are safe.',
        epilog=epilog,
    )
    parser.add_argument(
        '--list-files', dest='list_files', action='store_true',
        help='Only display the filenames that contain violations.'
    )
    parser.add_argument(
        '--rule-totals', dest='rule_totals', action='store_true',
        help='Display the totals for each rule.'
    )
    parser.add_argument(
        '--summary-format', dest='summary_format',
        choices=['eslint', 'json'], default='eslint',
        help='Choose the display format for the summary.'
    )
    parser.add_argument(
        '--verbose', dest='verbose', action='store_true',
        help='Print multiple lines where possible for additional context of violations.'
    )
    parser.add_argument(
        '--config', dest='config', action='store', default='xsslint.default_config',
        help='Specifies the config module to use. The config module should be in Python package syntax.'
    )
    parser.add_argument(
        '--thresholds', dest='thresholds', action='store',
        help='Specifies the config module to use. The config module should be in Python package syntax.'
    )
    parser.add_argument('path', nargs="?", default=None, help='A file to lint or directory to recursively lint.')

    args = parser.parse_args()
    config = _load_config_module(args.config)
    options = {
        'list_files': args.list_files,
        'rule_totals': args.rule_totals,
        'summary_format': args.summary_format,
        'verbose': args.verbose,
        'skip_dirs': getattr(config, 'SKIP_DIRS', ()),
        'thresholds': args.thresholds
    }
    template_linters = getattr(config, 'LINTERS', ())
    if not template_linters:
        raise ValueError(f"LINTERS is empty or undefined in the config module ({args.config}).")

    ruleset = _build_ruleset(template_linters)
    summary_results = SummaryResults(ruleset)
    _lint(args.path, template_linters, options, summary_results, out=StringIO())


if __name__ == "__main__":
    try:
        main()
    except BuildFailure as e:
        print(e)
        sys.exit(1)
