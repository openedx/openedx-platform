# lint-amnesty, pylint: disable=missing-module-docstring

import json

from django.core.management import call_command
from django.test import TestCase
from six import StringIO


class CommandsTestBase(TestCase):
    """
    Command for testing track functionality
    """
    def _run_dummy_command(self, *args, **kwargs):
        """
        Calls the test command and outputs a dict of the current context.
        """
        out = StringIO()
        call_command('tracked_dummy_command', *args, stdout=out, **kwargs)
        out.seek(0)
        return json.loads(out.read())

    def test_command(self):
        args = ['whee']
        kwargs = {'key1': 'default', 'key2': True}
        json_out = self._run_dummy_command(*args, **kwargs)
        assert json_out['command'].strip() == 'tracked_dummy_command'
