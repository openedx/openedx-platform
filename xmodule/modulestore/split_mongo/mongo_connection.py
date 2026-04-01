"""
Segregation of pymongo functions from the data modeling mechanisms for split modulestore.
"""


import datetime
import logging
import math
import pickle
import re
import zlib
from contextlib import contextmanager
from time import time
from zoneinfo import ZoneInfo

from ccx_keys.locator import CCXLocator
from django.core.cache import caches, InvalidCacheBackendError
from django.db.models import F
from django.db.models.functions import Lower
from django.db.models.lookups import Exact
from django.db.transaction import TransactionManagementError
import pymongo
# Import this just to export it
from pymongo.errors import DuplicateKeyError  # pylint: disable=unused-import
from edx_django_utils import monitoring
from edx_django_utils.cache import RequestCache

from common.djangoapps.split_modulestore_django.models import SplitModulestoreCourseIndex
from xmodule.exceptions import HeartbeatFailure
from xmodule.modulestore import BlockData
from xmodule.modulestore.split_mongo import BlockKey
from xmodule.mongo_utils import connect_to_mongodb, create_collection_index
from openedx.core.lib.cache_utils import request_cached

log = logging.getLogger(__name__)


def get_cache(alias):
    """
    Return cache for an `alias`

    Note: The primary purpose of this is to mock the cache in test_split_modulestore.py
    """
    return caches[alias]


def round_power_2(value):
    """
    Return value rounded up to the nearest power of 2.
    """
    if value == 0:
        return 0

    return math.pow(2, math.ceil(math.log(value, 2)))


class Tagger:
    """
    An object used by :class:`QueryTimer` to allow timed code blocks
    to add measurements and tags to the timer.
    """

    def __init__(self, default_sample_rate):
        self.added_tags = []
        self.measures = []
        self.sample_rate = default_sample_rate

    def measure(self, name, size):
        """
        Record a measurement of the timed data. This would be something to
        indicate the size of the value being timed.

        Arguments:
            name: The name of the measurement.
            size (float): The size of the measurement.
        """
        self.measures.append((name, size))

    def tag(self, **kwargs):
        """
        Add tags to the timer.

        Arguments:
            **kwargs: Each keyword is treated as a tag name, and the
                value of the argument is the tag value.
        """
        self.added_tags.extend(list(kwargs.items()))

    @property
    def tags(self):
        """
        Return all tags for this (this includes any tags added with :meth:`tag`,
        and also all of the added measurements, bucketed into powers of 2).
        """
        return [
            '{}:{}'.format(name, round_power_2(size))
            for name, size in self.measures
        ] + [
            f'{name}:{value}'
            for name, value in self.added_tags
        ]


class QueryTimer:
    """
    An object that allows timing a block of code while also recording measurements
    about that code.
    """

    def __init__(self, metric_base, sample_rate=1):
        """
        Arguments:
            metric_base: The prefix to be used for all queries captured
            with this :class:`QueryTimer`.
        """
        self._metric_base = metric_base
        self._sample_rate = sample_rate

    @contextmanager
    def timer(self, metric_name, course_context):
        """
        Contextmanager which acts as a timer for the metric ``metric_name``,
        but which also yields a :class:`Tagger` object that allows the timed block
        of code to add tags and quantity measurements. Tags are added verbatim to the
        timer output. Measurements are recorded as histogram measurements in their own,
        and also as bucketed tags on the timer measurement.

        Arguments:
            metric_name: The name used to aggregate all of these metrics.
            course_context: The course which the query is being made for.
        """
        tagger = Tagger(self._sample_rate)
        metric_name = f"{self._metric_base}.{metric_name}"

        start = time()  # lint-amnesty, pylint: disable=unused-variable
        try:
            yield tagger
        finally:
            end = time()  # lint-amnesty, pylint: disable=unused-variable
            tags = tagger.tags
            tags.append(f'course:{course_context}')


TIMER = QueryTimer(__name__, 0.01)


def structure_from_mongo(structure, course_context=None):
    """
    Converts the 'blocks' key from a list [block_data] to a map
        {BlockKey: block_data}.
    Converts 'root' from [block_type, block_id] to BlockKey.
    Converts 'blocks.*.fields.children' from [[block_type, block_id]] to [BlockKey].
    N.B. Does not convert any other ReferenceFields (because we don't know which fields they are at this level).

    Arguments:
        structure: The document structure to convert
        course_context (CourseKey): For metrics gathering, the CourseKey
            for the course that this data is being processed for.
    """
    with TIMER.timer('structure_from_mongo', course_context) as tagger:
        tagger.measure('blocks', len(structure['blocks']))

        structure['root'] = BlockKey(*structure['root'])
        new_blocks = {}
        for block in structure['blocks']:
            if 'children' in block['fields']:
                block['fields']['children'] = [BlockKey(*child) for child in block['fields']['children']]
            new_blocks[BlockKey(block['block_type'], block.pop('block_id'))] = BlockData(**block)
        structure['blocks'] = new_blocks

        return structure


def structure_to_mongo(structure, course_context=None):
    """
    Converts the 'blocks' key from a map {BlockKey: block_data} to
        a list [block_data], inserting BlockKey.type as 'block_type'
        and BlockKey.id as 'block_id'.
    Doesn't convert 'root', since namedtuple's can be inserted
        directly into mongo.
    """
    with TIMER.timer('structure_to_mongo', course_context) as tagger:
        tagger.measure('blocks', len(structure['blocks']))

        new_structure = dict(structure)
        new_structure['blocks'] = []

        for block_key, block in structure['blocks'].items():
            new_block = dict(block.to_storable())
            new_block.setdefault('block_type', block_key.type)
            new_block['block_id'] = block_key.id
            new_structure['blocks'].append(new_block)

        return new_structure


class CourseStructureCache:
    """
    Wrapper around django cache object to cache course structure objects.
    The course structures are pickled and compressed when cached.

    If the 'course_structure_cache' doesn't exist, then don't do anything for
    for set and get.
    """

    def __init__(self):
        self.cache = None
        try:
            self.cache = get_cache('course_structure_cache')
        except InvalidCacheBackendError:
            pass

    def get(self, key, course_context=None):
        """Pull the compressed, pickled struct data from cache and deserialize."""
        if self.cache is None:
            return None

        with TIMER.timer("CourseStructureCache.get", course_context) as tagger:
            try:
                compressed_pickled_data = self.cache.get(key)
                tagger.tag(from_cache=str(compressed_pickled_data is not None).lower())

                if compressed_pickled_data is None:
                    # Always log cache misses, because they are unexpected
                    tagger.sample_rate = 1
                    return None

                tagger.measure('compressed_size', len(compressed_pickled_data))

                pickled_data = zlib.decompress(compressed_pickled_data)
                tagger.measure('uncompressed_size', len(pickled_data))

                return pickle.loads(pickled_data, encoding='latin-1')
            except Exception:  # lint-amnesty, pylint: disable=broad-except
                # The cached data is corrupt in some way, get rid of it.
                log.warning("CourseStructureCache: Bad data in cache for %s", course_context)
                self.cache.delete(key)
                return None

    def set(self, key, structure, course_context=None):
        """Given a structure, will pickle, compress, and write to cache."""
        if self.cache is None:
            return None

        with TIMER.timer("CourseStructureCache.set", course_context) as tagger:
            pickled_data = pickle.dumps(structure, 4)  # Protocol can't be incremented until cache is cleared
            tagger.measure('uncompressed_size', len(pickled_data))

            # 1 = Fastest (slightly larger results)
            compressed_pickled_data = zlib.compress(pickled_data, 1)
            data_size = len(compressed_pickled_data)
            tagger.measure('compressed_size', data_size)

            # We rely on the course structure cache default timeout, which should be
            # high by default (~ a few days).
            total_bytes_in_one_mb = 1024 * 1024
            if data_size < total_bytes_in_one_mb * 2:  # Only data with a size smaller than 2MB will be cached
                self.cache.set(key, compressed_pickled_data)
            else:
                chunk_size_in_mbs = round(data_size / total_bytes_in_one_mb, 2)

                # .. custom_attribute_name: split_mongo_compressed_size_in_mbs
                # .. custom_attribute_description: contains the data chunk size in MBs. The size on which
                #   the memcached client failed to store value in course structure cache.
                monitoring.set_custom_attribute('split_mongo_compressed_size_in_mbs', chunk_size_in_mbs)


class MongoPersistenceBackend:
    """
    Segregation of pymongo functions from the data modeling mechanisms for split modulestore.
    """

    def __init__(
        self, db, collection, host, port=27017, tz_aware=True, user=None, password=None,
        asset_collection=None, with_mysql_subclass=False, **kwargs  # lint-amnesty, pylint: disable=unused-argument
    ):
        """
        Create & open the connection, authenticate, and provide pointers to the collections
        """
        # Set a write concern of 1, which makes writes complete successfully to the primary
        # only before returning. Also makes pymongo report write errors.
        kwargs['w'] = 1

        #make sure the course index cache is fresh.
        RequestCache(namespace="course_index_cache").clear()

        self.collection = collection
        self.connection_params = {
            'db': db,
            'host': host,
            'port': port,
            'tz_aware': tz_aware,
            'user': user,
            'password': password,
            **kwargs
        }

        self.do_connection()

        # Is the MySQL subclass in use, passing through some reads/writes to us? If so this will be True.
        # If this MongoPersistenceBackend is being used directly (only MongoDB is involved), this is False.
        self.with_mysql_subclass = with_mysql_subclass

    def do_connection(self):
        self.database = connect_to_mongodb(**self.connection_params)
        self.course_index = self.database[self.collection + '.active_versions']
        self.structures = self.database[self.collection + '.structures']
        self.definitions = self.database[self.collection + '.definitions']

    def heartbeat(self):
        """
        Check that the db is reachable.
        """
        try:
            # The ismaster command is cheap and does not require auth.
            self.database.client.admin.command('ismaster')
            return True
        except pymongo.errors.ConnectionFailure:
            raise HeartbeatFailure(f"Can't connect to {self.database.name}", 'mongo')  # lint-amnesty, pylint: disable=raise-missing-from

    def check_connection(self):
        """
        Check if mongodb connection is open or not.
        """
        try:
            self.database.client.admin.command("ping")
            return True
        except pymongo.errors.InvalidOperation:
            return False

    def ensure_connection(self):
        """
        Ensure that mongodb connection is open.
        """
        if self.check_connection():
            return
        self.do_connection()

    def get_structure(self, key, course_context=None):
        """
        Get the structure from the persistence mechanism whose id is the given key.

        This method will use a cached version of the structure if it is available.
        """
        with TIMER.timer("get_structure", course_context) as tagger_get_structure:
            cache = CourseStructureCache()

            structure = cache.get(key, course_context)
            tagger_get_structure.tag(from_cache=str(bool(structure)).lower())
            if not structure:
                # Always log cache misses, because they are unexpected
                tagger_get_structure.sample_rate = 1

                with TIMER.timer("get_structure.find_one", course_context) as tagger_find_one:
                    doc = self.structures.find_one({'_id': key})
                    if doc is None:
                        log.warning(
                            "doc was None when attempting to retrieve structure for item with key %s",
                            str(key)
                        )
                        return None
                    tagger_find_one.measure("blocks", len(doc['blocks']))
                    structure = structure_from_mongo(doc, course_context)
                    tagger_find_one.sample_rate = 1

                cache.set(key, structure, course_context)

            return structure

    def find_structures_by_id(self, ids, course_context=None):
        """
        Return all structures that specified in ``ids``.

        Arguments:
            ids (list): A list of structure ids
        """
        with TIMER.timer("find_structures_by_id", course_context) as tagger:
            tagger.measure("requested_ids", len(ids))
            docs = [
                structure_from_mongo(structure, course_context)
                for structure in self.structures.find({'_id': {'$in': ids}})
            ]
            tagger.measure("structures", len(docs))
            return docs

    def find_courselike_blocks_by_id(self, ids, block_type, course_context=None):
        """
        Find all structures that specified in `ids`. Among the blocks only return block whose type is `block_type`.

        Arguments:
            ids (list): A list of structure ids
            block_type: type of block to return
        """
        with TIMER.timer("find_courselike_blocks_by_id", course_context) as tagger:
            tagger.measure("requested_ids", len(ids))
            docs = [
                structure_from_mongo(structure, course_context)
                for structure in self.structures.find(
                    {'_id': {'$in': ids}},
                    {'blocks': {'$elemMatch': {'block_type': block_type}}, 'root': 1}
                )
            ]
            tagger.measure("structures", len(docs))
            return docs

    def insert_structure(self, structure, course_context=None):
        """
        Insert a new structure into the database.
        """
        with TIMER.timer("insert_structure", course_context) as tagger:
            tagger.measure("blocks", len(structure["blocks"]))
            self.structures.insert_one(structure_to_mongo(structure, course_context))

    def get_course_index(self, key, ignore_case=False):
        """
        Get the course_index from the persistence mechanism whose id is the given key
        """
        with TIMER.timer("get_course_index", key):
            if ignore_case:
                query = {
                    key_attr: re.compile('^{}$'.format(re.escape(getattr(key, key_attr))), re.IGNORECASE)
                    for key_attr in ('org', 'course', 'run')
                }
            else:
                query = {
                    key_attr: getattr(key, key_attr)
                    for key_attr in ('org', 'course', 'run')
                }
            return self.course_index.find_one(query)

    def find_matching_course_indexes(
            self,
            branch=None,
            search_targets=None,
            org_target=None,
            course_context=None,
            course_keys=None

    ):
        """
        Find the course_index matching particular conditions.

        Arguments:
            branch: If specified, this branch must exist in the returned courses
            search_targets: If specified, this must be a dictionary specifying field values
                that must exist in the search_targets of the returned courses
            org_target: If specified, this is an ORG filter so that only course_indexs are
                returned for the specified ORG
        """
        with TIMER.timer("find_matching_course_indexes", course_context):
            query = {}
            if course_keys:
                courses_queries = self._generate_query_from_course_keys(branch, course_keys)
                query['$or'] = courses_queries
            else:
                if branch is not None:
                    query[f'versions.{branch}'] = {'$exists': True}

                if search_targets:
                    for key, value in search_targets.items():
                        query[f'search_targets.{key}'] = value

                if org_target:
                    query['org'] = org_target

            return self.course_index.find(query)

    def _generate_query_from_course_keys(self, branch, course_keys):
        """
        Generate query for courses using course keys
        """
        courses_queries = []
        query = {}
        if branch:
            query = {f'versions.{branch}': {'$exists': True}}

        for course_key in course_keys:
            course_query = {
                key_attr: getattr(course_key, key_attr)
                for key_attr in ('org', 'course', 'run')
            }
            course_query.update(query)
            courses_queries.append(course_query)

        return courses_queries

    def insert_course_index(self, course_index, course_context=None):
        """
        Create the course_index in the db
        """
        with TIMER.timer("insert_course_index", course_context):
            # Set last_update which is used to avoid collisions, unless a subclass already set it before calling super()
            if not self.with_mysql_subclass:
                course_index['last_update'] = datetime.datetime.now(ZoneInfo("UTC"))
            # Insert the new index:
            self.course_index.insert_one(course_index)

    def update_course_index(self, course_index, from_index=None, course_context=None):
        """
        Update the db record for course_index.

        Arguments:
            from_index: If set, only update an index if it matches the one specified in `from_index`.
        """
        with TIMER.timer("update_course_index", course_context):
            if from_index:
                query = {"_id": from_index["_id"]}
                # last_update not only tells us when this course was last updated but also helps prevent collisions.
                # However, if used with MySQL, we defer to the subclass's colision logic and commit exactly the same
                # writes as it does, rather than implementing separate (and possibly conflicting) collision detection.
                if 'last_update' in from_index and not self.with_mysql_subclass:
                    query['last_update'] = from_index['last_update']
            else:
                query = {
                    'org': course_index['org'],
                    'course': course_index['course'],
                    'run': course_index['run'],
                }
            # Set last_update which is used to avoid collisions, unless a subclass already set it before calling super()
            if not self.with_mysql_subclass:
                course_index['last_update'] = datetime.datetime.now(ZoneInfo("UTC"))
            # Update the course index:
            result = self.course_index.replace_one(query, course_index, upsert=False,)
            if result.modified_count == 0:
                log.warning(
                    "Collision in Split Mongo when applying course index to MongoDB. "
                    "Change was discarded. New index was: %s",
                    course_index,
                )

    def delete_course_index(self, course_key):
        """
        Delete the course_index from the persistence mechanism whose id is the given course_index
        """
        self.ensure_connection()
        with TIMER.timer("delete_course_index", course_key):
            query = {
                key_attr: getattr(course_key, key_attr)
                for key_attr in ('org', 'course', 'run')
            }
            return self.course_index.delete_one(query)

    def get_definition(self, key, course_context=None):
        """
        Get the definition from the persistence mechanism whose id is the given key
        """
        with TIMER.timer("get_definition", course_context) as tagger:
            definition = self.definitions.find_one({'_id': key})
            tagger.measure("fields", len(definition['fields']))
            tagger.tag(block_type=definition['block_type'])
            return definition

    def get_definitions(self, definitions, course_context=None):
        """
        Retrieve all definitions listed in `definitions`.
        """
        with TIMER.timer("get_definitions", course_context) as tagger:
            tagger.measure('definitions', len(definitions))
            definitions = self.definitions.find({'_id': {'$in': definitions}})
            return definitions

    def insert_definition(self, definition, course_context=None):
        """
        Create the definition in the db
        """
        with TIMER.timer("insert_definition", course_context) as tagger:
            tagger.measure('fields', len(definition['fields']))
            tagger.tag(block_type=definition['block_type'])
            self.definitions.insert_one(definition)

    def ensure_indexes(self):
        """
        Ensure that all appropriate indexes are created that are needed by this modulestore, or raise
        an exception if unable to.

        This method is intended for use by tests and administrative commands, and not
        to be run during server startup.
        """
        create_collection_index(
            self.course_index,
            [
                ('org', pymongo.ASCENDING),
                ('course', pymongo.ASCENDING),
                ('run', pymongo.ASCENDING)
            ],
            unique=True,
            background=True
        )

    def close_connections(self):
        """
        Closes any open connections to the underlying databases
        """
        RequestCache(namespace="course_index_cache").clear()
        if self.check_connection():
            self.database.client.close()

    def _drop_database(self, database=True, collections=True, connections=True):
        """
        A destructive operation to drop the underlying database and close all connections.
        Intended to be used by test code for cleanup.

        If database is True, then this should drop the entire database.
        Otherwise, if collections is True, then this should drop all of the collections used
        by this modulestore.
        Otherwise, the modulestore should remove all data from the collections.

        If connections is True, then close the connection to the database as well.
        """
        RequestCache(namespace="course_index_cache").clear()

        self.ensure_connection()
        connection = self.database.client

        if database:
            connection.drop_database(self.database.name)
        elif collections:
            self.course_index.drop()
            self.structures.drop()
            self.definitions.drop()
        else:
            self.course_index.remove({})
            self.structures.remove({})
            self.definitions.remove({})

        if connections:
            connection.close()


class DjangoFlexPersistenceBackend(MongoPersistenceBackend):
    """
    Backend for split mongo that can read/write from MySQL and/or S3 instead of Mongo,
    either partially replacing MongoDB or fully replacing it.
    """

    def __init__(self, *args, **kwargs):
        # Initialize the parent MongoDB backend, and tell it that MySQL is in use too, so some things like collision
        # detection will be done at the MySQL layer only and not duplicated at the MongoDB layer.
        super().__init__(*args, **kwargs, with_mysql_subclass=True)

    # Structures and definitions are only supported in MongoDB for now.
    # Course indexes are read from MySQL and written to both MongoDB and MySQL
    # Course indexes are cached within the process using their key and ignore_case atrributes as keys.
    # This method is request cached. The keys to the cache are the arguements to the method.
    # The `self` arguement is discarded as a key using an isinstance check.
    # This is because the DjangoFlexPersistenceBackend could be different in reference to the same course key.
    @request_cached(
        "course_index_cache",
        arg_map_function=lambda arg: str(arg) if not isinstance(arg, DjangoFlexPersistenceBackend) else "")
    def get_course_index(self, key, ignore_case=False):
        """
        Get the course_index from the persistence mechanism whose id is the given key
        """
        if key.version_guid and not key.org:
            # I don't think it was intentional, but with the MongoPersistenceBackend, using a key with only a version
            # guid and no org/course/run value would not raise an error, but would always return None. So we need to be
            # compatible with that.
            # e.g. test_split_modulestore.py:SplitModuleCourseTests.test_get_course -> get_course(key with only version)
            #      > _load_items > cache_items > begin bulk operations > get_course_index > results in this situation.
            log.warning("DjangoFlexPersistenceBackend: get_course_index without org/course/run will always return None")
            return None
        # We never include the branch or the version in the course key in the SplitModulestoreCourseIndex table:
        key = key.for_branch(None).version_agnostic()
        if not ignore_case:
            query_expr = Exact(F("course_id"), str(key))
        else:
            # Case insensitive search is important when creating courses to reject course IDs that differ only by
            # capitalization.
            # WARNING: 'course_id__iexact=key' does not work on this table as it uses a case-sensitive collation.
            # We need to use the following explicit lowercase comparison in order to correctly query:
            query_expr = Exact(Lower("course_id"), str(key).lower())
        try:
            return SplitModulestoreCourseIndex.objects.get(query_expr).as_v1_schema()
        except SplitModulestoreCourseIndex.DoesNotExist:
            # The mongo implementation does not retrieve by string key; it retrieves by (org, course, run) tuple.
            # As a result, it will handle read requests for a CCX key like
            #   ccx-v1:org.0+course_0+Run_0+branch@published-branch+ccx@1
            # identically to the corresponding course key. This seems to be an oversight though, not an intentional
            # feature, as the CCXModulestoreWrapper is supposed to "hide" CCX keys from the underlying modulestore.
            # Anyhow, for compatbility we need to do the same:
            if isinstance(key, CCXLocator):
                log.warning(
                    f"A CCX key leaked through to the underlying modulestore, bypassing CCXModulestoreWrapper: {key}"
                )
                return self.get_course_index(key.to_course_locator(), ignore_case)
            return None

    def find_matching_course_indexes(  # pylint: disable=arguments-differ
        self,
        branch=None,
        search_targets=None,
        org_target=None,
        course_context=None,
        course_keys=None,
        force_mongo=False,
    ):
        """
        Find the course_index matching particular conditions.

        Arguments:
            branch: If specified, this branch must exist in the returned courses
            search_targets: If specified, this must be a dictionary specifying field values
                that must exist in the search_targets of the returned courses
            org_target: If specified, this is an ORG filter so that only course_indexs are
                returned for the specified ORG
        """
        if force_mongo:
            # For data migration purposes, this argument will read from MongoDB instead of MySQL
            return super().find_matching_course_indexes(
                branch=branch, search_targets=search_targets, org_target=org_target,
                course_context=course_context, course_keys=course_keys,
            )
        queryset = SplitModulestoreCourseIndex.objects.all()
        if course_keys:
            queryset = queryset.filter(course_id__in=course_keys)
        if search_targets:
            if "wiki_slug" in search_targets:
                queryset = queryset.filter(wiki_slug=search_targets.pop("wiki_slug"))
            if search_targets:  # If there are any search targets besides wiki_slug (which we've handled by this point):
                raise ValueError(f"Unsupported search_targets: {', '.join(search_targets.keys())}")
        if org_target:
            queryset = queryset.filter(org=org_target)
        if branch is not None:
            branch_field = SplitModulestoreCourseIndex.field_name_for_branch(branch)
            queryset = queryset.exclude(**{branch_field: ""})

        return (course_index.as_v1_schema() for course_index in queryset)

    def insert_course_index(self, course_index, course_context=None):  # pylint: disable=arguments-differ
        """
        Create the course_index in the db
        """
        # clear the whole course_index request cache, required for sucessfully cloning a course.
        # This is a relatively large hammer for the problem, but we mostly only use one course at a time.
        RequestCache(namespace="course_index_cache").clear()

        course_index['last_update'] = datetime.datetime.now(ZoneInfo("UTC"))
        new_index = SplitModulestoreCourseIndex(**SplitModulestoreCourseIndex.fields_from_v1_schema(course_index))
        new_index.save()
        # Also write to MongoDB, so we can switch back to using it if this new MySQL version doesn't work well.
        # NOTE: This is REQUIRED for pruning (structures.py) to run safely. Don't remove this write until
        # pruning is modified to read from SplitModulestoreCourseIndex to get active versions.
        super().insert_course_index(course_index, course_context)

    def update_course_index(self, course_index, from_index=None, course_context=None):  # pylint: disable=arguments-differ
        """
        Update the db record for course_index.

        Arguments:
            from_index: If set, only update an index if it matches the one specified in `from_index`.

        Exceptions:
            SplitModulestoreCourseIndex.DoesNotExist: If the given object_id is not valid
        """
        # "last_update not only tells us when this course was last updated but also helps prevent collisions"
        # This code is just copying the behavior of the existing MongoPersistenceBackend
        # See https://github.com/openedx/edx-platform/pull/5200 for context
        RequestCache(namespace="course_index_cache").clear()
        course_index['last_update'] = datetime.datetime.now(ZoneInfo("UTC"))
        # Find the SplitModulestoreCourseIndex entry that we'll be updating:
        index_obj = SplitModulestoreCourseIndex.objects.get(objectid=course_index["_id"])

        # Check for collisions:
        if from_index and index_obj.last_update != from_index["last_update"]:
            # "last_update not only tells us when this course was last updated but also helps prevent collisions"
            log.warning(
                "Collision in Split Mongo when applying course index to MySQL. This can happen in dev if django debug "
                "toolbar is enabled, as it slows down parallel queries. New index was: %s",
                course_index,
            )
            return  # Collision; skip this update

        # Apply updates to the index entry. While doing so, track which branch versions were changed (if any).
        changed_branches = []
        for attr, value in SplitModulestoreCourseIndex.fields_from_v1_schema(course_index).items():
            if attr in ("objectid", "course_id"):
                # Enforce these attributes as immutable.
                if getattr(index_obj, attr) != value:
                    raise ValueError(
                        f"Attempted to change the {attr} key of a course index entry ({index_obj.course_id})"
                    )
            else:
                if attr.endswith("_version"):
                    # Model fields ending in _version are branches. If the branch version has changed, convert the field
                    # name to a branch name and report it in the history below.
                    if getattr(index_obj, attr) != value:
                        changed_branches.append(attr[:-8])
                setattr(index_obj, attr, value)
        if changed_branches:
            # For the django simple history, indicate what was changed. Unfortunately at this point we only really know
            # which branch(es) were changed, not anything more useful than that.
            index_obj._change_reason = f'Updated {" and ".join(changed_branches)} branch'  # pylint: disable=protected-access

        # Save the course index entry and create a historical record:
        index_obj.save()

        # Also write to MongoDB, so we can switch back to using it if this new MySQL version doesn't work well.
        # NOTE: This is REQUIRED for pruning (structures.py) to run safely. Don't remove this write until
        # pruning is modified to read from SplitModulestoreCourseIndex to get active versions.
        super().update_course_index(course_index, from_index, course_context)

    def delete_course_index(self, course_key):
        """
        Delete the course_index from the persistence mechanism whose id is the given course_index
        """
        RequestCache(namespace="course_index_cache").clear()
        SplitModulestoreCourseIndex.objects.filter(course_id=course_key).delete()
        # Also write to MongoDB, so we can switch back to using it if this new MySQL version doesn't work well.
        # NOTE: This is REQUIRED for pruning (structures.py) to run safely. Don't remove this write until
        # pruning is modified to read from SplitModulestoreCourseIndex to get active versions.
        super().delete_course_index(course_key)

    def _drop_database(self, database=True, collections=True, connections=True):
        """
        Reset data for testing.
        """
        RequestCache(namespace="course_index_cache").clear()
        try:
            SplitModulestoreCourseIndex.objects.all().delete()
        except TransactionManagementError as err:
            # If the test doesn't use 'with self.allow_transaction_exception():', then this error can occur and it may
            # be non-obvious why, so give a very clear explanation of how to fix it. See the docstring of
            # allow_transaction_exception() for more details.
            raise RuntimeError(
                "post-test cleanup failed with TransactionManagementError. "
                "Use 'with self.allow_transaction_exception():' from ModuleStoreTestCase/...IsolationMixin to fix it."
            ) from err
        # Also write to MongoDB, so we can switch back to using it if this new MySQL version doesn't work well.
        # NOTE: This is REQUIRED for pruning (structures.py) to run safely. Don't remove this write until
        # pruning is modified to read from SplitModulestoreCourseIndex to get active versions.
        super()._drop_database(database, collections, connections)
