"""
Utilities for export a course's XML into a git repository,
committing and pushing the changes.
"""


import logging
import os
import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from opaque_keys.edx.locator import LibraryLocator, LibraryLocatorV2

from xmodule.contentstore.django import contentstore
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.xml_exporter import export_course_to_xml, export_library_to_xml

log = logging.getLogger(__name__)

GIT_REPO_EXPORT_DIR = getattr(settings, 'GIT_REPO_EXPORT_DIR', None)
GIT_EXPORT_DEFAULT_IDENT = settings.GIT_EXPORT_DEFAULT_IDENT


class GitExportError(Exception):
    """
    Convenience exception class for git export error conditions.
    """

    def __init__(self, message):
        # Force the lazy i18n values to turn into actual unicode objects
        super().__init__(str(message))

    NO_EXPORT_DIR = _("GIT_REPO_EXPORT_DIR not set or path {0} doesn't exist, "
                      "please create it, or configure a different path with "
                      "GIT_REPO_EXPORT_DIR").format(GIT_REPO_EXPORT_DIR)
    URL_BAD = _('Non writable git url provided. Expecting something like:'
                ' git@github.com:openedx/openedx-demo-course.git')
    URL_NO_AUTH = _('If using http urls, you must provide the username '
                    'and password in the url. Similar to '
                    'https://user:pass@github.com/user/course.')
    DETACHED_HEAD = _('Unable to determine branch, repo in detached HEAD mode')
    CANNOT_PULL = _('Unable to update or clone git repository.')
    XML_EXPORT_FAIL = _('Unable to export course to xml.')
    CONFIG_ERROR = _('Unable to configure git username and password')
    CANNOT_COMMIT = _('Unable to commit changes. This is usually '
                      'because there are no changes to be committed')
    CANNOT_PUSH = _('Unable to push changes.  This is usually '
                    'because the remote repository cannot be contacted')
    BAD_COURSE = _('Bad course location provided')
    MISSING_BRANCH = _('Missing branch on fresh clone')


def cmd_log(cmd, cwd):
    """
    Helper function to redirect stderr to stdout and log the command
    used along with the output. Will raise subprocess.CalledProcessError if
    command doesn't return 0, and returns the command's output.
    """
    output = subprocess.check_output(cmd, cwd=cwd, stderr=subprocess.STDOUT)
    log.debug('Command was: {!r}. '
              'Working directory was: {!r}'.format(' '.join(cmd), cwd))
    log.debug(f'Command output was: {output!r}')
    return output


def export_library_v2_to_zip(library_key, root_dir, library_dir, user=None):
    """
    Export a v2 library using the backup API.

    V2 libraries are stored in Learning Core and use a zip-based backup mechanism.
    This function creates a zip backup and extracts it to the specified directory.

    Args:
        library_key: LibraryLocatorV2 for the library to export
        root_dir: Root directory where library_dir will be created
        library_dir: Directory name for the exported library content
        user: User object for the backup API (optional)

    Raises:
        Exception: If backup creation or extraction fails
    """
    from openedx_content.api import create_zip_file as create_lib_zip_file

    # Get user object for backup API
    user_obj = User.objects.filter(username=user).first()
    # Create temporary zip backup
    temp_dir = Path(mkdtemp())
    sanitized_lib_key = str(library_key).replace(":", "-")
    sanitized_lib_key = slugify(sanitized_lib_key, allow_unicode=True)
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    zip_filename = f'{sanitized_lib_key}-{timestamp}.zip'
    zip_path = os.path.join(temp_dir, zip_filename)

    try:
        origin_server = getattr(settings, 'CMS_BASE', None)
        create_lib_zip_file(
            lp_key=str(library_key),
            path=zip_path,
            user=user_obj,
            origin_server=origin_server
        )

        # Target directory for extraction
        target_dir = os.path.join(root_dir, library_dir)

        # Create target directory if it doesn't exist
        os.makedirs(target_dir, exist_ok=True)

        # Extract zip contents (will overwrite existing files)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(target_dir)

        log.info('Extracted library v2 backup to %s', target_dir)

    finally:
        # Cleanup temporary files
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def export_to_git(content_key, repo, user='', rdir=None):
    """
    Export a course or library to git.

    Args:
        content_key: CourseKey or LibraryLocator for the content to export
        repo (str): Git repository URL
        user (str): Optional username for git commit identity
        rdir (str): Optional custom directory name for the repository

    Raises:
        GitExportError: For various git operation failures
    """
    # pylint: disable=too-many-statements

    # Detect content type and select appropriate export function
    is_library_v2 = isinstance(content_key, LibraryLocatorV2)
    if is_library_v2:
        # V2 libraries use backup API with zip extraction
        export_xml_func = export_library_v2_to_zip
        content_type_label = "library"
    elif isinstance(content_key, LibraryLocator):
        export_xml_func = export_library_to_xml
        content_type_label = "library"
    else:
        export_xml_func = export_course_to_xml
        content_type_label = "course"

    if not GIT_REPO_EXPORT_DIR:
        raise GitExportError(GitExportError.NO_EXPORT_DIR)

    if not os.path.isdir(GIT_REPO_EXPORT_DIR):
        raise GitExportError(GitExportError.NO_EXPORT_DIR)

    # Check for valid writable git url
    if not (repo.endswith('.git') or
            repo.startswith(('http:', 'https:', 'file:'))):
        raise GitExportError(GitExportError.URL_BAD)

    # Check for username and password if using http[s]
    if repo.startswith('http:') or repo.startswith('https:'):
        parsed = urlparse(repo)
        if parsed.username is None or parsed.password is None:
            raise GitExportError(GitExportError.URL_NO_AUTH)
    if rdir:
        rdir = os.path.basename(rdir)
    else:
        rdir = repo.rsplit('/', 1)[-1].rsplit('.git', 1)[0]

    log.debug("rdir = %s", rdir)

    # Pull or clone repo before exporting to xml
    # and update url in case origin changed.
    rdirp = f'{GIT_REPO_EXPORT_DIR}/{rdir}'
    branch = None
    if os.path.exists(rdirp):
        log.info('Directory already exists, doing a git reset and pull '
                 'instead of git clone.')
        cwd = rdirp
        # Get current branch
        cmd = ['git', 'symbolic-ref', '--short', 'HEAD']
        try:
            branch = cmd_log(cmd, cwd).decode('utf-8').strip('\n')
        except subprocess.CalledProcessError as ex:
            log.exception('Failed to get branch: %r', ex.output)
            raise GitExportError(GitExportError.DETACHED_HEAD) from ex

        cmds = [
            ['git', 'remote', 'set-url', 'origin', repo],
            ['git', 'fetch', 'origin'],
            ['git', 'reset', '--hard', f'origin/{branch}'],
            ['git', 'pull'],
            ['git', 'clean', '-d', '-f'],
        ]
    else:
        cmds = [['git', 'clone', repo]]
        cwd = GIT_REPO_EXPORT_DIR

    cwd = os.path.abspath(cwd)
    for cmd in cmds:
        try:
            cmd_log(cmd, cwd)
        except subprocess.CalledProcessError as ex:
            log.exception('Failed to pull git repository: %r', ex.output)
            raise GitExportError(GitExportError.CANNOT_PULL) from ex

    # export content as xml (or zip for v2 libraries) before commiting and pushing
    root_dir = os.path.dirname(rdirp)
    content_dir = os.path.basename(rdirp).rsplit('.git', 1)[0]

    try:
        if is_library_v2:
            export_xml_func(content_key, root_dir, content_dir, user)
        else:
            # V1 libraries and courses: use XML export (no user parameter)
            export_xml_func(modulestore(), contentstore(), content_key,
                            root_dir, content_dir)
    except (OSError, AttributeError) as ex:
        log.exception('Failed to export %s', content_type_label)
        raise GitExportError(GitExportError.XML_EXPORT_FAIL) from  # noqa: B904 ex

    # Get current branch if not already set
    if not branch:
        cmd = ['git', 'symbolic-ref', '--short', 'HEAD']
        try:
            branch = cmd_log(cmd, os.path.abspath(rdirp)).decode('utf-8').strip('\n')
        except subprocess.CalledProcessError as ex:
            log.exception('Failed to get branch from freshly cloned repo: %r',
                          ex.output)
            raise GitExportError(GitExportError.MISSING_BRANCH) from ex

    # Now that we have fresh xml exported, set identity, add
    # everything to git, commit, and push to the right branch.
    ident = {}
    try:
        user = User.objects.get(username=user)
        ident['name'] = user.username
        ident['email'] = user.email
    except User.DoesNotExist:
        # That's ok, just use default ident
        ident = GIT_EXPORT_DEFAULT_IDENT
    time_stamp = timezone.now()
    cwd = os.path.abspath(rdirp)
    commit_msg = f"Export {content_type_label} from Studio at {time_stamp}"
    try:
        cmd_log(['git', 'config', 'user.email', ident['email']], cwd)
        cmd_log(['git', 'config', 'user.name', ident['name']], cwd)
    except subprocess.CalledProcessError as ex:
        log.exception('Error running git configure commands: %r', ex.output)
        raise GitExportError(GitExportError.CONFIG_ERROR) from ex
    try:
        cmd_log(['git', 'add', '.'], cwd)
        cmd_log(['git', 'commit', '-a', '-m', commit_msg], cwd)
    except subprocess.CalledProcessError as ex:
        log.exception('Unable to commit changes: %r', ex.output)
        raise GitExportError(GitExportError.CANNOT_COMMIT) from ex
    try:
        cmd_log(['git', 'push', '-q', 'origin', branch], cwd)
    except subprocess.CalledProcessError as ex:
        log.exception('Error running git push command: %r', ex.output)
        raise GitExportError(GitExportError.CANNOT_PUSH) from ex

    log.info(
        '%s %s exported to git repository %s successfully',
        content_type_label.capitalize(),
        content_key,
        repo,
    )
