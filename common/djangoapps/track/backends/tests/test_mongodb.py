# lint-amnesty, pylint: disable=missing-module-docstring

from unittest.mock import patch

from django.test import TestCase

from common.djangoapps.track.backends.mongodb import MongoBackend


class TestMongoBackend(TestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    def setUp(self):
        super().setUp()
        self.mongo_patcher = patch('common.djangoapps.track.backends.mongodb.MongoClient')
        self.mongo_patcher.start()
        self.addCleanup(self.mongo_patcher.stop)

        self.backend = MongoBackend()

    def test_mongo_backend(self):
        events = [{'test': 1}, {'test': 2}]

        self.backend.send(events[0])
        self.backend.send(events[1])

        # Check if we inserted events into the database

        calls = self.backend.collection.insert.mock_calls

        assert len(calls) == 2

        # Unpack the arguments and check if the events were used
        # as the first argument to collection.insert

        def first_argument(call):
            _, args, _ = call
            return args[0]

        assert events[0] == first_argument(calls[0])
        assert events[1] == first_argument(calls[1])
