"""
Utilities for dealing with Javascript and JSON.
"""

import json

from django.utils.html import escapejs
from mako.filters import decode

from xmodule.modulestore import EdxJSONEncoder


def _escape_json_for_js(json_dumps_string):
    """
    Escape output of JSON dumps that is safe to be embedded in a <SCRIPT> tag.

    This implementation is based on escaping performed in
    simplejson.JSONEncoderForHTML.

    Arguments:
        json_dumps_string (string): A JSON string to be escaped.

            This must be the output of json.dumps to ensure:
            1. The string contains valid JSON, and
            2. That non-ascii characters are properly escaped

    Returns:
        (string) Escaped JSON that is safe to be embedded in HTML.

    """
    json_dumps_string = json_dumps_string.replace("&", "\\u0026")
    json_dumps_string = json_dumps_string.replace(">", "\\u003e")
    json_dumps_string = json_dumps_string.replace("<", "\\u003c")
    return json_dumps_string


def dump_js_escaped_json(obj, cls=EdxJSONEncoder):
    """
    JSON dumps and escapes objects that are safe to be embedded in JavaScript.

    Use this for anything but strings (e.g. dicts, tuples, lists, bools, and
    numbers).  For strings, use js_escaped_string.

    The output of this method is also usable as plain-old JSON.

    Usage:
        Used as follows in a Mako template inside a <SCRIPT> tag::

            var json_obj = ${obj | n, dump_js_escaped_json}

        If you must use the cls argument, then use as follows::

            var json_obj = ${dump_js_escaped_json(obj, cls) | n}

        Use the "n" Mako filter above.  It is possible that the default filter
        may include html escaping in the future, and this ensures proper
        escaping.

        Ensure ascii in json.dumps (ensure_ascii=True) allows safe skipping of
        Mako's default filter decode.utf8.

    Arguments:
        obj: The object soon to become a JavaScript escaped JSON string.  The
            object can be anything but strings (e.g. dicts, tuples, lists, bools, and
            numbers).
        cls (class): The JSON encoder class (defaults to EdxJSONEncoder).

    Returns:
        (string) Escaped encoded JSON.

    """
    obj = list(obj) if isinstance(obj, type({}.values())) else obj  # pylint: disable=isinstance-second-argument-not-valid-type, line-too-long
    json_string = json.dumps(obj, ensure_ascii=True, cls=cls)
    json_string = _escape_json_for_js(json_string)
    return json_string


def js_escaped_string(string_for_js):
    """
    Mako filter that escapes text for use in a JavaScript string.

    If None is provided, returns an empty string.

    Usage:
        Used as follows in a Mako template inside a <SCRIPT> tag::

            var my_string_for_js = "${my_string_for_js | n, js_escaped_string}"

        The surrounding quotes for the string must be included.

        Use the "n" Mako filter above.  It is possible that the default filter
        may include html escaping in the future, and this ensures proper
        escaping.

        Mako's default filter decode.utf8 is applied here since this default
        filter is skipped in the Mako template with "n".

    Arguments:
        string_for_js (string): Text to be properly escaped for use in a
            JavaScript string.

    Returns:
        (string) Text properly escaped for use in a JavaScript string as
        unicode.  Returns empty string if argument is None.

    """
    if string_for_js is None:
        string_for_js = ""
    string_for_js = decode.utf8(string_for_js)
    string_for_js = escapejs(string_for_js)
    return string_for_js
