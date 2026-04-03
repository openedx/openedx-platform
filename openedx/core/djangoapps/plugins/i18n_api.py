"""
edx-platform specific i18n helpers for edx-django-utils plugins.
"""

import os
import subprocess
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from django.core.management import BaseCommand, CommandError
from importlib_metadata import entry_points


@dataclass
class ArgparseArgument:
    """
    Argument specs to be used with argparse add_argument method.
    """

    flag: str = None  # This is passed as a positional argument
    dest: str = None
    help: str = None

    def get_kwargs(self):
        """
        Return keyword arguments for the `add_argument` method .
        """
        argument_dict = asdict(self)
        argument_dict.pop('flag')
        return argument_dict

    def get_args(self):
        """
        Return positional arguments for the `add_argument` method .
        """
        return [self.flag]


# `atlas pull` arguments definitions.
#
#  - https://github.com/openedx/openedx-atlas
#
ATLAS_ARGUMENTS = [
    ArgparseArgument(
        flag='--filter',
        dest='filter',
        help='Filter option for `atlas pull` e.g. --filter=ar,fr_CA,de_DE.'
    ),
    ArgparseArgument(
        flag='--repository',
        dest='repository',
        help='Custom repository slug for `atlas pull` e.g. '
             '--repository=friendsofopenedx/openedx-translations . '
             'Default is "openedx/openedx-translations".'
    ),
    ArgparseArgument(
        flag='--branch',
        dest='branch',
        help='Deprecated option. Use --revision instead.',
    ),
    ArgparseArgument(
        flag='--revision',
        dest='revision',
        help='Custom git revision for "atlas pull" e.g. --revision=release/redwood . Default is "main".',
    ),
]


class BaseAtlasPullCommand(BaseCommand):
    """
    Base `atlas pull` Django command.
    """

    def add_arguments(self, parser):
        """
        Configure Django command arguments.
        """
        for argument in ATLAS_ARGUMENTS:
            parser.add_argument(*argument.get_args(), **argument.get_kwargs())

        parser.add_argument(
            '--verbose|-v',
            action='store_true',
            default=False,
            dest='verbose',
            help='Verbose output using `--verbose` argument for `atlas pull`.',
        )

    def ensure_empty_directory(self, directory):
        """
        Ensure the pull directory is empty before running atlas pull.
        """
        plugin_translations_root = directory
        os.makedirs(plugin_translations_root, exist_ok=True)
        if os.listdir(plugin_translations_root):
            raise CommandError(f'"{plugin_translations_root}" should be empty before running atlas pull.')

    def get_atlas_pull_options(self, **options):
        """
        Pass-through the Django command options to `atlas pull`.
        """
        atlas_pull_options = []

        for argument in ATLAS_ARGUMENTS:
            option_value = options.get(argument.dest)
            if option_value is not None:
                atlas_pull_options += [argument.flag, option_value]

        if options['verbose']:
            atlas_pull_options += ['--verbose']
        else:
            atlas_pull_options += ['--silent']

        return atlas_pull_options


def atlas_pull_by_modules(module_names, locale_root, pull_options):
    """
    Atlas pull translations by module name instead of repository name.
    """
    atlas_pull_args = [
        # Asterisk (*) is used instead of the repository name because it's not known at runtime.
        # The `--expand-glob` option is used to expand match any repository name that has the right module name.
        f'translations/*/{module_name}/conf/locale:{module_name}'
        for module_name in module_names
    ]

    subprocess.run(
        args=['atlas', 'pull', '--expand-glob', *pull_options, *atlas_pull_args],
        check=True,
        cwd=locale_root,
    )


def compile_po_files(root_dir):
    """
    Compile the .po files into .mo files recursively in the given directory.

    Mimics the behavior of `django-admin compilemessages` for the po files but for any directory.
    """
    for root, _dirs, files in os.walk(root_dir):
        root = Path(root)
        for po_file in files:
            if po_file.endswith('.po'):
                po_file_path = root / po_file
                subprocess.run(
                    args=['msgfmt', '--check-format', '-o', str(po_file_path.with_suffix('.mo')), str(po_file_path)],
                    check=True,
                )


def get_installed_plugins_module_names():
    """
    Return the installed plugins Python module names.

    This function excludes the built-in edx-platform plugins such as `lms`, `cms` and `openedx`.
    """
    # group (e.g 'lms.djangoapp') -> set for root module names (e.g {'edx_sga'})
    root_modules = defaultdict(set)

    for entry_point in entry_points():
        module_name = entry_point.value
        root_module = module_name.split('.')[0]  # e.g. `edx_sga` from `edx_sga.core.xblock`
        root_modules[entry_point.group].add(root_module)

    return (
        # Return all lms.djangopapp and cms.djangoapp plugins
        (root_modules['lms.djangoapp'] | root_modules['cms.djangoapp'])
        # excluding the edx-platform built-in plugins which don't need atlas
        - {'lms', 'cms', 'common', 'openedx', 'xmodule'}
        # excluding XBlocks, which is handled by `pull_xblock_translations` command
        - root_modules['xblock.v1']
    )


def plugin_translations_atlas_pull(pull_options, locale_root):
    """
    Atlas pull the translations for the installed non-XBlocks plugins.
    """
    module_names = get_installed_plugins_module_names()

    atlas_pull_by_modules(
        module_names=module_names,
        locale_root=locale_root,
        pull_options=pull_options,
    )
