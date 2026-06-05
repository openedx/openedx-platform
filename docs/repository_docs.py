import fnmatch
import os
import shutil
from pathlib import Path

DEFAULT_PATTERNS_TO_EXCLUDE_DIRS = (
    '*.tox',
    '*.git',
    '*__pycache__',
    '*.github',
    '*.pytest_cache',
    'build',
    'docs',
    'node_modules',
    'src',
    'test_root',
)

DEFAULT_PATTERNS_TO_EXCLUDE_FILES = (
    'changelog.rst',
)


class RepositoryDocs:
    def __init__(
        self,
        root,
        build_path,
        patterns_to_exclude_dirs=DEFAULT_PATTERNS_TO_EXCLUDE_DIRS,
        patterns_to_exclude_files=DEFAULT_PATTERNS_TO_EXCLUDE_FILES,
    ):
        self.root = root
        self.build_path = build_path
        self.patterns_to_exclude_dirs = patterns_to_exclude_dirs
        self.patterns_to_exclude_files = patterns_to_exclude_files

    def build_rst_docs(self):
        os.makedirs(self.build_path, exist_ok=True)
        self._create_index_rst_file(self.build_path)
        rst_files = self._find_rst_files()
        self._copy_files(rst_files)

    def _copy_files(self, files):
        for file in files:
            if os.path.basename(file).lower() in self.patterns_to_exclude_files:
                continue
            relative_path = os.path.relpath(os.path.dirname(file), self.root)
            destination_path = os.path.join(self.build_path, relative_path)
            os.makedirs(destination_path, exist_ok=True)
            shutil.copy(file, destination_path)
            self._create_index_rst_files_on_path(destination_path)

    def _create_index_rst_files_on_path(self, path):
        directory_paths = self._get_directories_list_on_path(path)
        for directory_path in directory_paths:
            self._create_index_rst_file(directory_path)

    def _get_directories_list_on_path(self, path):
        directory_paths = []
        while path and path != self.root:
            directory_paths.append(path)
            path = os.path.dirname(path)
        return directory_paths

    def _create_index_rst_file(self, directory_path):
        directory_name = os.path.basename(directory_path)
        file_path = os.path.join(directory_path, "index.rst")
        if os.path.exists(file_path):
            return
        file_content = f"""{directory_name}
{len(directory_name) * '='}

.. toctree::
   :glob:
   :maxdepth: 1

   *
   */*index
"""
        with open(file_path, "w", encoding="utf-8") as file:
            file.write(file_content)

    def _find_rst_files(self):
        rst_files = []
        for dir_path, dir_names, file_names in os.walk(self.root):
            for excluded_dir in self.patterns_to_exclude_dirs:
                if fnmatch.fnmatch(dir_path, f'{self.root}/{excluded_dir}*'):
                    dir_names.clear()
                    file_names.clear()
                    break
            for file_name in file_names:
                if file_name.lower().endswith('.rst'):
                    rst_files.append(os.path.join(dir_path, file_name))
            if '__pycache__' in dir_names:
                dir_names.remove('__pycache__')
        return rst_files

    # Service directories to scan when building the apps overview index.
    _SERVICE_DIRS = [
        'lms/djangoapps',
        'cms/djangoapps',
        'openedx/core/djangoapps',
        'openedx/features',
        'common/djangoapps',
        'xmodule',
    ]

    def build_apps_index(self, output_path):
        """
        Generate a flat, scannable index of all Django apps that have a README
        or a docs/ subdirectory, grouped by service area.

        Written to output_path (overwritten on each build).
        """
        lines = [
            'App-Level Documentation',
            '=======================',
            '',
            'Quick-scan index of Django apps that have README files or documentation.',
            'Each link opens the auto-generated index for that app.',
            '',
        ]

        for service_dir in self._SERVICE_DIRS:
            service_path = os.path.join(self.root, service_dir)
            if not os.path.isdir(service_path):
                continue

            app_entries = []
            for app_name in sorted(os.listdir(service_path)):
                app_path = os.path.join(service_path, app_name)
                if not os.path.isdir(app_path):
                    continue
                has_readme = os.path.isfile(os.path.join(app_path, 'README.rst'))
                has_docs = os.path.isdir(os.path.join(app_path, 'docs'))
                if has_readme or has_docs:
                    # Path relative to docs/references/docs/ (where the generated tree lives)
                    rel = f'{service_dir}/{app_name}'
                    app_entries.append((app_name, rel))

            if not app_entries:
                continue

            heading = service_dir
            lines += [heading, '-' * len(heading), '']
            for app_name, rel in app_entries:
                lines.append(f'* :doc:`{app_name} <../references/docs/{rel}/index>`')
            lines.append('')

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        Path(output_path).write_text('\n'.join(lines), encoding="utf-8")

    def build_decisions_index(self, output_path):
        """
        Generate a comprehensive ADR index that links to every app-level
        docs/decisions/ directory, grouped by service area.

        Written to output_path (overwritten on each build).
        """
        # Collect all app-level decisions directories (skip top-level docs/decisions)
        top_level_decisions = os.path.join(self.root, 'docs', 'decisions')
        decisions_by_service = {}

        for service_dir in self._SERVICE_DIRS:
            service_path = os.path.join(self.root, service_dir)
            if not os.path.isdir(service_path):
                continue
            entries = []
            for dir_path, _dir_names, _file_names in os.walk(service_path):
                if os.path.basename(dir_path) == 'decisions':
                    parent = os.path.dirname(dir_path)
                    if os.path.basename(parent) == 'docs' and dir_path != top_level_decisions:
                        rel_from_root = os.path.relpath(dir_path, self.root)
                        # Human-readable label: just the app directory name
                        label = os.path.basename(os.path.dirname(os.path.dirname(dir_path)))
                        entries.append((label, rel_from_root))
            if entries:
                decisions_by_service[service_dir] = sorted(entries)

        lines = [
            'App-Level Architecture Decision Records',
            '========================================',
            '',
            'Links to per-app ADR directories, supplementing the top-level',
            ':doc:`repo-wide decisions <0001-courses-in-lms>`.',
            '',
        ]

        for service_dir, entries in decisions_by_service.items():
            heading = service_dir
            lines += [heading, '-' * len(heading), '']
            for label, rel_from_root in entries:
                # Path relative to docs/decisions/ (where this file lives)
                link = f'../references/docs/{rel_from_root}/index'
                # Normalise to forward slashes
                link = link.replace(os.sep, '/')
                lines.append(f'* :doc:`{label} <{link}>`')
            lines.append('')

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        Path(output_path).write_text('\n'.join(lines), encoding="utf-8")
