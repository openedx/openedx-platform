#!/usr/bin/env python
"""
Usage: manage.py {lms|cms} [--settings env] ...

Run django management commands. Because edx-platform contains multiple django projects,
the first argument specifies which project to run (cms [Studio] or lms [Learning Management System]).

By default, those systems run in with a settings file appropriate for development. However,
by passing the --settings flag, you can specify what environment specific settings file to use.

Any arguments not understood by this manage.py will be passed to django-admin.py
"""
# pylint: disable=wrong-import-order, wrong-import-position

from openedx.core.lib.logsettings import log_python_warnings
log_python_warnings()

# Patch the xml libs before anything else.
from openedx.core.lib.safe_lxml import defuse_xml_libs  # isort:skip
defuse_xml_libs()

import os
import sys
from argparse import ArgumentParser


def parse_args():
    """Parse edx specific arguments to manage.py"""
    parser = ArgumentParser()
    subparsers = parser.add_subparsers(title='system', description='edX service to run')

    lms = subparsers.add_parser(
        'lms',
        help='Learning Management System',
        add_help=False,
        usage='%(prog)s [options] ...'
    )
    lms.add_argument('-h', '--help', action='store_true', help='show this help message and exit')
    lms.add_argument(
        '--settings',
        help="Which django settings module to use under lms.envs. If not provided, the DJANGO_SETTINGS_MODULE "
             "environment variable will be used if it is set, otherwise it will default to lms.envs.devstack")
    lms.set_defaults(
        help_string=lms.format_help(),
        settings_base='lms/envs',
        default_settings='lms.envs.devstack',
    )

    cms = subparsers.add_parser(
        'cms',
        help='Studio',
        add_help=False,
        usage='%(prog)s [options] ...'
    )
    cms.add_argument(
        '--settings',
        help="Which django settings module to use under cms.envs. If not provided, the DJANGO_SETTINGS_MODULE "
             "environment variable will be used if it is set, otherwise it will default to cms.envs.devstack")
    cms.add_argument('-h', '--help', action='store_true', help='show this help message and exit')
    cms.set_defaults(
        help_string=cms.format_help(),
        settings_base='cms/envs',
        default_settings='cms.envs.devstack',
    )

    known_args, remaining_args = parser.parse_known_args()

    if known_args.help:
        print("edX:")
        print(known_args.help_string)

    return known_args, remaining_args


if __name__ == "__main__":
    edx_args, django_args = parse_args()

    edx_args_base = edx_args.settings_base.replace('/', '.') + '.'
    if edx_args.settings:
        os.environ["DJANGO_SETTINGS_MODULE"] = edx_args_base + edx_args.settings
    elif os.environ.get("EDX_PLATFORM_SETTINGS") and not os.environ.get("DJANGO_SETTINGS_MODULE"):
        os.environ["DJANGO_SETTINGS_MODULE"] = edx_args_base + os.environ["EDX_PLATFORM_SETTINGS"]

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", edx_args.default_settings)

    if edx_args.help:
        print("Django:")
        # This will trigger django-admin.py to print out its help
        django_args.append('--help')

    from django.core.management import execute_from_command_line
    execute_from_command_line([sys.argv[0]] + django_args)
