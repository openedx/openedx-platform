"""
Common MongoDB connection functions.
"""


import logging

import pymongo
from pymongo.read_preferences import (  # pylint: disable=unused-import
    _MODES,
    _MONGOS_MODES,
    ReadPreference,
)

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name

# This will yeld a map of all available Mongo modes and their name
MONGO_READ_PREFERENCE_MAP = dict(zip(_MONGOS_MODES, _MODES))  # noqa: B905


def connect_to_mongodb(
    db, host,
    port=27017, tz_aware=True, user=None, password=None,
    retry_reads=True, **kwargs
):
    """
    Returns a MongoDB Database connection, optionally wrapped in a proxy. The proxy
    handles AutoReconnect errors by retrying read operations, since these exceptions
    typically indicate a temporary step-down condition for MongoDB.
    """
    # If the MongoDB server uses a separate authentication database that should be specified here.
    # Convert the lowercased authsource parameter to the camel-cased authSource expected by MongoClient.
    auth_source = db
    if auth_source_key := {'authSource', 'authsource'}.intersection(set(kwargs.keys())):
        auth_source = kwargs.pop(auth_source_key.pop()) or db

    # sanitize a kwarg which may be present and is no longer expected
    # AED 2020-03-02 TODO: Remove this when 'auth_source' will no longer exist in kwargs
    if 'auth_source' in kwargs:
        kwargs.pop('auth_source')

    # If read_preference is given as a name of a valid ReadPreference.<NAME>
    # constant such as "SECONDARY_PREFERRED" or a mongo mode such as
    # "secondaryPreferred", convert it. Otherwise pass it through unchanged.
    if 'read_preference' in kwargs:
        read_preference = MONGO_READ_PREFERENCE_MAP.get(
            kwargs['read_preference'],
            kwargs['read_preference']
        )

        read_preference = getattr(ReadPreference, read_preference, None)
        if read_preference is not None:
            kwargs['read_preference'] = read_preference

    if 'replicaSet' in kwargs and kwargs['replicaSet'] == '':
        kwargs['replicaSet'] = None

    connection_params = {
        'host': host,
        'port': port,
        'tz_aware': tz_aware,
        'document_class': dict,
        'retryReads': retry_reads,
        **kwargs,
    }

    if user is not None and password is not None and not db.startswith('test_'):
        connection_params.update({'username': user, 'password': password, 'authSource': auth_source})

    mongo_conn = pymongo.MongoClient(**connection_params)
    return mongo_conn[db]


def create_collection_index(
    collection, keys,
    ignore_created=True, ignore_created_opts=True, **kwargs
):
    """
    Create a MongoDB index in a collection. Optionally,
    ignore errors related to the index already existing.
    """
    # For an explanation of the error codes:
    # https://github.com/mongodb/mongo/blob/v3.0/src/mongo/db/catalog/index_catalog.cpp#L542-L583
    # https://github.com/mongodb/mongo/blob/v3.0/src/mongo/base/error_codes.err#L70-L87
    # pylint: disable=invalid-name
    INDEX_ALREADY_EXISTS = 68
    INDEX_OPTIONS_CONFLICT = 85
    try:
        collection.create_index(keys, **kwargs)
    except pymongo.errors.OperationFailure as exc:
        errors_to_ignore = []
        if ignore_created:
            errors_to_ignore.append(INDEX_ALREADY_EXISTS)
        if ignore_created_opts:
            errors_to_ignore.append(INDEX_OPTIONS_CONFLICT)
        if exc.code in errors_to_ignore:
            logger.warning("Existing index in collection '{}' remained unchanged!: {}".format(
                collection.full_name, exc.details['errmsg'])
            )
        else:
            raise exc
