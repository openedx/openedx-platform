"""
Tests the ``generate_jwt_signing_key`` management command.
"""
# pylint: disable=missing-docstring


import os
import sys
import tempfile
from contextlib import contextmanager
from io import StringIO
from unittest.mock import patch

import ddt
import yaml
from django.core.management import call_command
from django.test import TestCase

from openedx.core.djangolib.testing.utils import skip_unless_lms

COMMAND_NAME = 'generate_jwt_signing_key'
LOGGER = 'openedx.core.djangoapps.oauth_dispatch.management.commands.generate_jwt_signing_key.log.info'
TEST_KEY_IDENTIFIER = 'some_key_identifier'


@skip_unless_lms
@ddt.ddt
class TestGenerateJwtSigningKey(TestCase):
    """
    Tests the ``generate_jwt_signing_key`` management command.
    """
    @contextmanager
    def _captured_output(self):
        new_out, new_err = StringIO(), StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        try:
            sys.stdout, sys.stderr = new_out, new_err
            yield sys.stdout, sys.stderr
        finally:
            sys.stdout, sys.stderr = old_out, old_err

    def _assert_log_message(self, mock_log, message, expected_to_exist):
        log_message_exists = any(
            message in log_entry[0][0]
            for log_entry in mock_log.call_args_list
        )
        assert log_message_exists == expected_to_exist

    def _assert_key_output(self, output_stream, filename, strip_key_prefix):
        expected_in_output = [
            '{}JWT_PRIVATE_SIGNING_JWK'.format('' if strip_key_prefix else 'EDXAPP_'),
            '{}JWT_SIGNING_ALGORITHM'.format('' if strip_key_prefix else 'EDXAPP_'),
            '{}JWT_PUBLIC_SIGNING_JWK_SET'.format('' if strip_key_prefix else 'COMMON_'),
        ]
        for expected in expected_in_output:
            assert expected in output_stream.getvalue()

        with open(filename) as file_obj:  # pylint: disable=bad-option-value, open-builtin
            output_from_yaml = yaml.safe_load(file_obj)
            for expected in expected_in_output:
                assert expected in output_from_yaml['JWT_AUTH']

    def _assert_presence_of_old_keys(self, mock_log, add_previous_public_keys):
        self._assert_log_message(mock_log, 'Old JWT_PUBLIC_SIGNING_JWK_SET', expected_to_exist=add_previous_public_keys)

    def _assert_presence_of_key_id(self, mock_log, output_stream, provide_key_id, key_id_size):
        if provide_key_id:
            assert TEST_KEY_IDENTIFIER in output_stream.getvalue()
        else:
            assert TEST_KEY_IDENTIFIER not in output_stream.getvalue()
            key_id = mock_log.call_args_list[0][0][1]
            assert len(key_id) == (key_id_size or 8)

    @ddt.data(
        dict(add_previous_public_keys=True, provide_key_id=False, key_id_size=None, strip_key_prefix=True),
        dict(add_previous_public_keys=True, provide_key_id=False, key_id_size=16, strip_key_prefix=False),
        dict(add_previous_public_keys=False, provide_key_id=True, key_id_size=None, strip_key_prefix=False),
    )
    @ddt.unpack
    def test_command(self, add_previous_public_keys, provide_key_id, key_id_size, strip_key_prefix):
        command_options = dict(add_previous_public_keys=add_previous_public_keys)
        if provide_key_id:
            command_options['key_id'] = TEST_KEY_IDENTIFIER
        if key_id_size:
            command_options['key_id_size'] = key_id_size
        _, filename = tempfile.mkstemp(suffix='.yml')
        command_options['output_file'] = filename
        command_options['strip_key_prefix'] = strip_key_prefix

        with self._captured_output() as (output_stream, _):
            with patch(LOGGER) as mock_log:
                call_command(COMMAND_NAME, **command_options)

        self._assert_key_output(output_stream, filename, strip_key_prefix)
        self._assert_presence_of_old_keys(mock_log, add_previous_public_keys)
        self._assert_presence_of_key_id(mock_log, output_stream, provide_key_id, key_id_size)
        os.remove(filename)
