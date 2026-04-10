"""
Tests for methods defined in mongo_utils.py
"""


from unittest import TestCase
from uuid import uuid4

import ddt
from pymongo import ReadPreference

from xmodule.mongo_utils import connect_to_mongodb


@ddt.ddt
class MongoUtilsTests(TestCase):
    """
    Tests for methods exposed in mongo_utils
    """

    @ddt.data(
        ('PRIMARY', 'primary', ReadPreference.PRIMARY),
        ('SECONDARY_PREFERRED', 'secondaryPreferred', ReadPreference.SECONDARY_PREFERRED),
        ('NEAREST', 'nearest', ReadPreference.NEAREST),
    )
    @ddt.unpack
    def test_connect_to_mongo_read_preference(self, enum_name, mongos_name, expected_read_preference):
        """
        Test that read_preference parameter gets converted to a valid pymongo read preference.
        """
        host = 'localhost'
        db = 'test_read_preference_%s' % uuid4().hex  # noqa: UP031
        # Support for read_preference given in constant name form (ie. PRIMARY, SECONDARY_PREFERRED)
        connection = connect_to_mongodb(db, host, read_preference=enum_name)
        assert connection.client.read_preference == expected_read_preference
        # Support for read_preference given as mongos name.
        connection = connect_to_mongodb(db, host, read_preference=mongos_name)
        assert connection.client.read_preference == expected_read_preference

    @ddt.data(True, False)
    def test_connect_to_mongo_with_retry_reads(self, is_retry_enabled):
        """
        Test that the MongoDB client is created with retryReads=[True | False].
        """
        host = 'localhost'
        db = 'test_retry_reads_%s_%s' % (str(is_retry_enabled).lower(), uuid4().hex)  # noqa: UP031
        connection = connect_to_mongodb(db, host, retry_reads=is_retry_enabled)
        assert connection.client.options.retry_reads is is_retry_enabled
