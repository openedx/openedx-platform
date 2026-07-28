"""
Tests for methods defined in builtin_assets.py
"""
from unittest import TestCase
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from web_fragments.fragment import Fragment, FragmentResource

from xmodule.util import builtin_assets


class AddCssToFragmentTests(TestCase):
    """
    Tests for add_css_to_fragment.
    """

    def test_absolute_path_raises_value_error(self):
        fragment = Fragment()
        with self.assertRaises(ValueError):  # noqa: PT027
            builtin_assets.add_css_to_fragment(
                fragment,
                "/openedx/edx-platform/xmodule/assets/VideoBlockEditor.css",
            )

    def test_not_css_raises_value_error(self):
        fragment = Fragment()
        with self.assertRaises(ValueError):  # noqa: PT027
            builtin_assets.add_css_to_fragment(
                fragment,
                "vertical/public/js/vertical_student_view.js"
            )

    def test_misspelled_path_raises_not_found(self):
        fragment = Fragment()
        with self.assertRaises(FileNotFoundError):  # noqa: PT027
            builtin_assets.add_css_to_fragment(
                fragment,
                "VideoBlockEditorrrrr.css",
            )

    def test_static_file_missing_raises_improperly_configured(self):
        fragment = Fragment()
        with patch.object(builtin_assets, 'get_static_file_url', lambda _path: None):
            with self.assertRaises(ImproperlyConfigured):  # noqa: PT027
                builtin_assets.add_css_to_fragment(
                    fragment,
                    "VideoBlockEditor.css",
                )

    def test_happy_path(self):
        fragment = Fragment()
        builtin_assets.add_css_to_fragment(fragment, "VideoBlockEditor.css")
        assert fragment.resources[0] == FragmentResource(
            kind='url',
            data='/static/css-builtin-blocks/VideoBlockEditor.css',
            mimetype='text/css',
            placement='head',
        )
