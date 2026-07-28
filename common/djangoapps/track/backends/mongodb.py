"""MongoDB event tracker backend."""


import logging

import pymongo
from bson.errors import BSONError
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from common.djangoapps.track.backends import BaseBackend

log = logging.getLogger(__name__)


class MongoBackend(BaseBackend):
    """Class for a MongoDB event tracker Backend"""

    def __init__(self, **kwargs):
        """
        Connect to a MongoDB.

        :Parameters:

          - `host`: hostname
          - `port`: port
          - `user`: collection username
          - `password`: collection user password
          - `database`: name of the database
          - `collection`: name of the collection
          - 'authsource': name of the authentication database
          - `extra`: parameters to pymongo.MongoClient not listed above

        """

        super().__init__(**kwargs)  # pylint: disable=super-with-arguments

        # Extract connection parameters from kwargs

        host = kwargs.get('host', 'localhost')
        port = kwargs.get('port', 27017)

        user = kwargs.get('user', '')
        password = kwargs.get('password', '')

        db_name = kwargs.get('database', 'track')
        collection_name = kwargs.get('collection', 'events')

        auth_source = kwargs.get('authsource') or None

        # Other mongo connection arguments
        extra = kwargs.get('extra', {})

        # By default disable write acknowledgments, reducing the time
        # blocking during an insert
        extra['w'] = extra.get('w', 0)

        # Make timezone aware by default
        extra['tz_aware'] = extra.get('tz_aware', True)

        # Connect to database and get collection

        self.connection = MongoClient(
            host=host,
            port=port,
            **extra
        )

        database = self.connection[db_name]

        if user or password:
            database.authenticate(user, password, source=auth_source)

        self.collection = database[collection_name]

        self._create_indexes()

    def _create_indexes(self):
        """Ensures the proper fields are indexed"""
        # WARNING: The collection will be locked during the index
        # creation. If the collection has a large number of
        # documents in it, the operation can take a long time.

        # TODO: The creation of indexes can be moved to a Django
        # management command or equivalent. There is also an option to
        # run the indexing on the background, without locking.
        self.collection.ensure_index([('time', pymongo.DESCENDING)], background=True)
        self.collection.ensure_index('event_type', background=True)

    def send(self, event):
        """Insert the event in to the Mongo collection"""
        try:
            self.collection.insert(event, manipulate=False)
        except (PyMongoError, BSONError):
            # The event will be lost in case of a connection error or any error
            # that occurs when trying to insert the event into Mongo.
            # pymongo will re-connect/re-authenticate automatically
            # during the next event.
            msg = 'Error inserting to MongoDB event tracker backend'
            log.exception(msg)
