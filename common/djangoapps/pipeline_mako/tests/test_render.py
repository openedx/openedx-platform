""" Tests for rendering functions in the mako pipeline. """

from unittest.mock import patch

import ddt
from django.conf import settings
from django.test import TestCase

from common.djangoapps.pipeline_mako import compressed_css, compressed_js, render_require_js_path_overrides
from openedx.core.djangolib.testing.utils import skip_unless_lms


class RequireJSPathOverridesTest(TestCase):
    """Test RequireJS path overrides. """

    OVERRIDES = {
        'jquery': 'common/js/vendor/jquery.js',
        'text': 'js/vendor/text.js',
        'backbone': 'common/js/vendor/backbone.js'
    }

    OVERRIDES_JS = [
        "<script type=\"text/javascript\">",
        "(function (require) {",
        "require.config({",
        "paths: {",
        "'jquery': 'common/js/vendor/jquery',",
        "'text': 'js/vendor/text',",
        "'backbone': 'common/js/vendor/backbone'",
        "}",
        "});",
        "}).call(this, require || RequireJS.require);",
        "</script>"
    ]

    def test_requirejs_path_overrides(self):
        result = render_require_js_path_overrides(self.OVERRIDES)
        # To make the string comparision easy remove the whitespaces
        self.assertCountEqual(list(map(str.strip, result.splitlines())), self.OVERRIDES_JS)  # noqa: PT009


@skip_unless_lms
@ddt.ddt
class PipelineRenderTest(TestCase):
    """Test individual pipeline rendering functions. """

    @staticmethod
    def mock_staticfiles_lookup(path):
        return '/static/' + path

    @patch('common.djangoapps.static_replace.try_staticfiles_lookup', side_effect=mock_staticfiles_lookup)
    @ddt.data(
        (True,),
        (False,),
    )
    def test_compressed_css(self, pipeline_enabled, mock_staticfiles_lookup):  # pylint: disable=unused-argument
        """
        Verify the behavior of compressed_css, with the pipeline
        both enabled and disabled.
        """
        pipeline = settings.PIPELINE.copy()
        pipeline['PIPELINE_ENABLED'] = pipeline_enabled
        with self.settings(PIPELINE=pipeline):
            # Verify the default behavior
            css_include = compressed_css('style-main-v1')
            assert 'lms-main-v1.css' in css_include

            # Verify that raw keyword causes raw URLs to be emitted
            css_include = compressed_css('style-main-v1', raw=True)
            assert 'lms-main-v1.css?raw' in css_include

    @patch('django.contrib.staticfiles.storage.staticfiles_storage.exists', return_value=True)
    @patch('common.djangoapps.static_replace.try_staticfiles_lookup', side_effect=mock_staticfiles_lookup)
    def test_compressed_js(self, mock_staticfiles_lookup, mock_staticfiles_exists):  # pylint: disable=unused-argument
        """
        Verify the behavior of compressed_css, with the pipeline
        both enabled and disabled.
        """
        pipeline = settings.PIPELINE.copy()
        # Verify that a single JS file is rendered with the pipeline enabled
        pipeline['PIPELINE_ENABLED'] = True
        with self.settings(PIPELINE=pipeline):
            js_include = compressed_js('base_application')
            assert 'lms-base-application.js' in js_include

        # Verify that multiple JS files are rendered with the pipeline disabled
        pipeline['PIPELINE_ENABLED'] = False
        with self.settings(PIPELINE=pipeline):
            js_include = compressed_js('base_application')
            assert '/static/js/src/logger.js' in js_include
