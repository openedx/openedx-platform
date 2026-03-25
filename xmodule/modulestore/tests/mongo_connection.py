"""
This file is intended to provide settings for the mongodb connection used for tests.
The settings can be provided by environment variables in the shell running the tests.  This reads
in a variety of environment variables but provides sensible defaults in case those env var
overrides don't exist
"""


import os  # noqa: I001

MONGO_PORT_NUM = int(os.environ.get('EDXAPP_TEST_MONGO_PORT', '27017'))
MONGO_HOST = os.environ.get('EDXAPP_TEST_MONGO_HOST', 'localhost')
