"""
Script for importing a content library from a tar.gz file
"""


import base64
import os

from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.exceptions import SuspiciousOperation
from django.core.management.base import BaseCommand, CommandError
from lxml import etree
from opaque_keys.edx.locator import LibraryLocator
from path import Path

from cms.djangoapps.contentstore.utils import add_instructor
from openedx.core.lib.extract_archive import safe_extractall
from xmodule.contentstore.django import contentstore  # pylint: disable=wrong-import-order
from xmodule.modulestore import ModuleStoreEnum  # pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order
from xmodule.modulestore.exceptions import DuplicateCourseError  # pylint: disable=wrong-import-order
from xmodule.modulestore.xml_importer import import_library_from_xml  # pylint: disable=wrong-import-order


class Command(BaseCommand):
    """
    Import the specified content library archive.
    """
    help = 'Import the specified content library into mongo'

    def add_arguments(self, parser):
        parser.add_argument('archive_path')
        parser.add_argument('owner_username')

    def handle(self, *args, **options):
        """
        Given a content library archive path, import the corresponding course to mongo.
        """

        archive_path = options['archive_path']
        username = options['owner_username']

        data_root = Path(settings.GITHUB_REPO_ROOT)
        subdir = base64.urlsafe_b64encode(os.path.basename(archive_path).encode('utf-8')).decode('utf-8')
        course_dir = data_root / subdir

        # Extract library archive
        try:
            safe_extractall(archive_path, course_dir)
        except SuspiciousOperation as exc:
            raise CommandError(f'\n=== Course import {archive_path}: Unsafe tar file - {exc.args[0]}\n') from exc

        # Paths to the library.xml file
        abs_xml_path = os.path.join(course_dir, 'library')
        rel_xml_path = os.path.relpath(abs_xml_path, data_root)

        # Gather library metadata from XML file
        xml_root = etree.parse(abs_xml_path / 'library.xml').getroot()
        if xml_root.tag != 'library':
            raise CommandError(f'Failed to import {archive_path}: Not a library archive')

        metadata = xml_root.attrib
        org = metadata['org']
        library = metadata['library']
        display_name = metadata['display_name']

        # Fetch user and library key
        user = User.objects.get(username=username)
        courselike_key, created = _get_or_create_library(org, library, display_name, user)

        # Check if data would be overwritten
        ans = ''
        while not created and ans not in ['y', 'yes', 'n', 'no']:
            inp = input(f'Library "{courselike_key}" already exists, overwrite it? [y/n] ')
            ans = inp.lower()
        if ans.startswith('n'):
            print(f'Aborting import of "{courselike_key}"')
            return

        # At last, import the library
        try:
            import_library_from_xml(
                modulestore(), user.id,
                settings.GITHUB_REPO_ROOT, [rel_xml_path],
                load_error_blocks=False,
                static_content_store=contentstore(),
                target_id=courselike_key
            )
        except Exception:
            print(f'\n=== Failed to import library-v1:{org}+{library}')
            raise

        print(f'Library "{archive_path}" imported to "{courselike_key}"')


def _get_or_create_library(org, number, display_name, user):
    """
    Create or retrieve given library and return its course-like key
    """

    try:
        # Create library if it does not exist
        store = modulestore()
        with store.default_store(ModuleStoreEnum.Type.split):
            library = store.create_library(
                org=org,
                library=number,
                user_id=user.id,
                fields={
                    "display_name": display_name
                },
            )
        add_instructor(library.location.library_key, user, user)
        return library.location.library_key, True
    except DuplicateCourseError:
        # Course exists, return its key
        return LibraryLocator(org=org, library=number), False
