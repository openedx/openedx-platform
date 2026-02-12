"""
Common initialization app for the LMS and CMS
"""

from django.apps import AppConfig
from django.db import connection


class CommonInitializationConfig(AppConfig):  # lint-amnesty, pylint: disable=missing-class-docstring
    name = 'openedx.core.djangoapps.common_initialization'
    verbose_name = 'Common Initialization'

    def ready(self):
        # Common settings validations for the LMS and CMS.
        from . import checks  # lint-amnesty, pylint: disable=unused-import
        self._add_mimetypes()
        self._add_required_adapters()

    @staticmethod
    def _add_mimetypes():
        """
        Add extra mimetypes. Used in xblock_resource.
        """
        import mimetypes

        mimetypes.add_type('application/vnd.ms-fontobject', '.eot')
        mimetypes.add_type('application/x-font-opentype', '.otf')
        mimetypes.add_type('application/x-font-ttf', '.ttf')
        mimetypes.add_type('application/font-woff', '.woff')

    @staticmethod
    def _add_required_adapters():
        """
        Register CourseLocator in psycopg2 extensions
        :return:
        """
        if 'postgresql' in connection.vendor.lower():
            from opaque_keys.edx.locator import CourseLocator, LibraryLocator, BlockUsageLocator
            from psycopg2.extensions import QuotedString, register_adapter

            def adapt_course_locator(course_locator):
                return QuotedString(str(course_locator))  # lint-amnesty, pylint: disable=protected-access

            # Register the adapter
            register_adapter(CourseLocator, adapt_course_locator)
            register_adapter(LibraryLocator, adapt_course_locator)
            register_adapter(BlockUsageLocator, adapt_course_locator)
