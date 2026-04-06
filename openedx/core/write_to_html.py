"""
Class used to write pytest warning data  into html format
"""
import textwrap

import six  # lint-amnesty, pylint: disable=unused-import


class HtmlOutlineWriter:
    """
    writer to handle html writing
    """
    HEAD = textwrap.dedent(
        """
        <!DOCTYPE html>
        <html>
        <head>
        <meta charset="utf-8" />
        </head>
        <style>
        .toggle-box{
        display:none;
        }
        .toggle-box + label + div {
            display: none;
        }
        .toggle-box + label:before {
            color: #888;
            width: 10px;
        }

        .toggle-box:checked + label + div {
            margin-left: 3%;
            display: flex;
            flex-direction: column;
        }
        div{
        border-style: solid;
            border-width: 1px 0px 0px 0px;
            border-radius: 3px;
        }

        .location {
        background-color: #edcca9
        }

        body {
        background-color: cornsilk
        }

        .warning_text {
        background-color: #d5b593
        }
        .warning{
        background-color: #bd9f7d
        }
        .num {
        background-color: #a68968;
        }
        .lineno {
        background-color: #a68968;
        }


        }


        </style>
        <body>
    """
    )

    SECTION_START = textwrap.dedent(
        """\
        <div class="{klass}">
        <input class="toggle-box {klass}" id="sect_{id:05d}" type="checkbox">
        <label for="sect_{id:05d}">{html}</label>
        <div>
    """
    )

    SECTION_END = "</div></div>"

    def __init__(self, fout):
        self.fout = fout
        self.section_id = 0
        self.fout.write(self.HEAD)

    def start_section(self, html, klass=None):
        self.fout.write(
            self.SECTION_START.format(id=self.section_id, html=html, klass=klass or "",)
        )
        self.section_id += 1

    def end_section(self):
        self.fout.write(self.SECTION_END)

    def write(self, html):
        self.fout.write(html)
