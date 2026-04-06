"""
Test the OLX serialization utils
"""
from __future__ import annotations

import unittest

import ddt
from opaque_keys.edx.keys import CourseKey

from . import utils


@ddt.ddt
class TestUtils(unittest.TestCase):
    """
    Test the OLX serialization utils
    """

    def test_rewrite_absolute_static_urls(self):
        """
        Test that rewrite_absolute_static_urls() can find and replace all uses
        of absolute Studio URLs in a course.

        Some criteria:
        - Rewriting only happens if the course ID is the same. If the absolute
          URL points to a different course, the new /static/foo.png form won't
          work.
        """
        # Note that this doesn't have to be well-formed OLX
        course_id = CourseKey.from_string("course-v1:TestCourse+101+2020")
        olx_in = """
        <problem>
            <img src="https://studio.example.com/asset-v1:TestCourse+101+2020+type@asset+block@SCI_1.2_Image_.png">
            <a href='https://studio.example.com/asset-v1:TestCourse+101+2020+type@asset+block@Québec.html'>
                View a file with accented characters in the filename.
            </a>
            <a href="https://studio.example.com/xblock/block-v1:foo">Not an asset link</a>.
            <img src="https://studio.example.com/asset-v1:OtherCourse+500+2020+type@asset+block@exclude_me.png">
        </problem>
        """
        olx_expected = """
        <problem>
            <img src="/static/SCI_1.2_Image_.png">
            <a href='/static/Québec.html'>
                View a file with accented characters in the filename.
            </a>
            <a href="https://studio.example.com/xblock/block-v1:foo">Not an asset link</a>.
            <img src="https://studio.example.com/asset-v1:OtherCourse+500+2020+type@asset+block@exclude_me.png">
        </problem>
        """
        olx_out = utils.rewrite_absolute_static_urls(olx_in, course_id)
        assert olx_out == olx_expected

    @ddt.unpack
    @ddt.data(
        ('''<problem>\n<script>ambiguous script\n</script></problem>''', False),
        ('''<problem>\n<script type="text/python">\npython\nscript\n</script></problem>''', True),
        ('''<problem>\n<script type='text/python'>\npython\nscript\n</script></problem>''', True),
        ('''<problem>\n<script type="loncapa/python">\npython\nscript\n</script></problem>''', True),
        ('''<problem>\n<script type='loncapa/python'>\npython\nscript\n</script></problem>''', True),
    )
    def test_has_python_script(self, olx: str, has_script: bool):
        """
        Test the _has_python_script() helper
        """
        assert utils._has_python_script(olx) == has_script  # pylint: disable=protected-access

    @ddt.unpack
    @ddt.data(
        ('''<problem><jsinput html_file="/static/question.html"/></problem>''', "/static/question.html"),
        ('''<problem><jsinput html_file="/static/simple-question.html"/></problem>''', "/static/simple-question.html"),
        ('''<problem><jsinput html_file='/static/simple-question.html'/></problem>''', "/static/simple-question.html"),
        ('''<problem><jsinput html_file='/static/simple.question.html'/></problem>''', "/static/simple.question.html"),
        ('''<problem><jsinput html_file="example.com/static/simple-question.html"/></problem>''', None),
        ('''<problem><jsinput html_file="https://example.com/static/simple-question.html"/></problem>''', None),
        ('''<problem><jsinput html_file="https://example.com/static/simple-question.html"/></problem>''', None),
        ('''<problem><jsinput />some url: /static/simple-question.html</problem>''', None),
    )
    def test_extract_local_html_path(self, olx: str, local_html_path: str | None):
        """
        Test the _extract_local_html_path() helper. Confirm that it correctly detects the
        presence of a `/static/` url in the 'html_file` attribute of a `<jsinput>` tag.
        """
        assert utils._extract_local_html_path(olx) == local_html_path  # pylint: disable=protected-access

    def test_extract_static_assets(self):
        """
        Test the _extract_static_assets() helper. Confirm that it correctly extracts all the
        static assets that have relative paths present in the html file.
        """
        html_file_content = """
        <!DOCTYPE html>
        <html lang="en">
          <head>
            <title>Example Title</title>
            <link
               rel="stylesheet"
               type="text/css"
               href="simple-question.css">

          </head>
          <body>
            <p>This is a non-existent css file: fake.css</p>
            <script src="jsChannel.js"></script>
            <script src="/some/path/simple-question.min.js" defer></script>
            <script src="other/path/simple-question.min.js" defer></script>
            <script src='http://example.com/static/external.js'></script>
            <script src='https://example.com/static/external.js'></script>
            <img src='mario.png' />
            <label class="directions">Please select:
                <select class="choices"></select>
            </label>
            <p aria-live="polite" class="feedback"></p>
          </body>
        </html>
        """
        expected = [
            "simple-question.css",
            "jsChannel.js",
            "/some/path/simple-question.min.js",
            "other/path/simple-question.min.js",
            "mario.png"
        ]
        assert utils._extract_static_assets(html_file_content) == expected  # pylint: disable=protected-access
