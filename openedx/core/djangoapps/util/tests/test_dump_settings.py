"""
Basic tests for dump_settings management command.

These are moreso testing that dump_settings works, less-so testing anything about the Django
settings files themselves. Remember that tests only run with (lms,cms)/envs/test.py,
which are based on (lms,cms)/envs/common.py, so these tests will not execute any of the
YAML-loading or post-processing defined in (lms,cms)/envs/production.py.
"""
import json

from django.core.management import call_command

from openedx.core.djangolib.testing.utils import skip_unless_cms, skip_unless_lms


@skip_unless_lms
def test_for_lms_settings(capsys):
    """
    Ensure LMS's test settings can be dumped, and sanity-check them for certain values.
    """
    dump = _get_settings_dump(capsys)

    # Check: something LMS-specific
    assert dump['MODULESTORE_BRANCH'] == "published-only"

    # Check: tuples are converted to lists
    assert isinstance(dump['XBLOCK_MIXINS'], list)

    # Check: classes are converted to dicts of info on the class location
    assert {"module": "xmodule.x_module", "qualname": "XModuleMixin"} in dump['XBLOCK_MIXINS']

    # Check: nested dictionaries come through OK, and int'l strings are just strings
    assert dump['COURSE_ENROLLMENT_MODES']['audit']['display_name'] == "Audit"


@skip_unless_cms
def test_for_cms_settings(capsys):
    """
    Ensure CMS's test settings can be dumped, and sanity-check them for certain values.
    """
    dump = _get_settings_dump(capsys)

    # Check: something CMS-specific
    assert dump['MODULESTORE_BRANCH'] == "draft-preferred"

    # Check: tuples are converted to lists
    assert isinstance(dump['XBLOCK_MIXINS'], list)

    # Check: classes are converted to dicts of info on the class location
    assert {"module": "xmodule.x_module", "qualname": "XModuleMixin"} in dump['XBLOCK_MIXINS']

    # Check: nested dictionaries come through OK, and int'l strings are just strings
    assert dump['COURSE_ENROLLMENT_MODES']['audit']['display_name'] == "Audit"


def _get_settings_dump(captured_sys):
    """
    Call dump_settings, ensure no error output, and return parsed JSON.
    """
    call_command('dump_settings')
    out, err = captured_sys.readouterr()
    assert out
    assert not err
    return json.loads(out)
