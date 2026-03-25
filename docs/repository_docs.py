import fnmatch  # noqa: I001
import os
import shutil

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
    'readme.rst',
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
            if file.name.lower() in self.patterns_to_exclude_files:
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
        file_path = f"{directory_path}/index.rst"
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
        with open(file_path, "w") as file:
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
