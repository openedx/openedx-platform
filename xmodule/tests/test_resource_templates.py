"""
Tests for xmodule.x_module.ResourceTemplates
"""
import pathlib
import unittest

from django.test import override_settings

from xmodule.x_module import ResourceTemplates

CUSTOM_RESOURCE_TEMPLATES_DIRECTORY = pathlib.Path(__file__).parent.parent / "templates/"


class ResourceTemplatesTests(unittest.TestCase):
    """
    Tests for xmodule.x_module.ResourceTemplates
    """

    def test_templates(self):
        expected = {
            'latex_html.yaml',
            'announcement.yaml',
            'anon_user_id.yaml'}
        got = {t['template_id'] for t in TestClass.templates()}
        assert expected == got

    def test_templates_no_suchdir(self):
        assert len(TestClass2.templates()) == 0

    def test_get_template(self):
        assert TestClass.get_template('latex_html.yaml')['template_id'] == 'latex_html.yaml'

    @override_settings(CUSTOM_RESOURCE_TEMPLATES_DIRECTORY=CUSTOM_RESOURCE_TEMPLATES_DIRECTORY)
    def test_get_custom_template(self):
        assert TestClassResourceTemplate.get_template('latex_html.yaml')['template_id'] == 'latex_html.yaml'

    @override_settings(CUSTOM_RESOURCE_TEMPLATES_DIRECTORY=CUSTOM_RESOURCE_TEMPLATES_DIRECTORY)
    def test_custom_templates(self):
        expected = {
            'latex_html.yaml',
            'announcement.yaml',
            'anon_user_id.yaml'}
        got = {t['template_id'] for t in TestClassResourceTemplate.templates()}
        assert expected == got


class TestClass(ResourceTemplates):
    """
    Derives from the class under test for testing purposes.

    Since `ResourceTemplates` is intended to be used as a mixin, we need to
    derive a class from it in order to fill in some data it's expecting to find
    in its mro.
    """
    template_packages = ['xmodule']

    @classmethod
    def get_template_dir(cls):
        return 'templates/test'


class TestClass2(TestClass):
    """
    Like TestClass, but `get_template_dir` returns a directory that doesn't
    exist.

    See `TestClass`.
    """

    @classmethod
    def get_template_dir(cls):
        return 'foo'


class TestClassResourceTemplate(ResourceTemplates):
    """
    Like TestClass, but `template_packages` contains a module that doesn't
    have any templates.

    See `TestClass`.
    """
    template_packages = ['capa.checker']
    template_dir_name = 'test'
