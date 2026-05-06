"""
MongoDB/GridFS-level code for the contentstore.
"""


import hashlib
import json
import os

import gridfs
import pymongo
from bson.son import SON
from fs.osfs import OSFS
from gridfs.errors import FileExists, NoFile
from opaque_keys.edx.keys import AssetKey

from xmodule.contentstore.content import XASSET_LOCATION_TAG
from xmodule.exceptions import NotFoundError
from xmodule.modulestore.django import ASSET_IGNORE_REGEX
from xmodule.mongo_utils import connect_to_mongodb, create_collection_index
from xmodule.util.misc import escape_invalid_characters, get_library_or_course_attribute

from .content import ContentStore, StaticContent, StaticContentStream


class MongoContentStore(ContentStore):
    """
    MongoDB-backed ContentStore.
    """
    # pylint: disable=unused-argument

    def __init__(
        self, host, db,
        port=27017, tz_aware=True, user=None, password=None, bucket='fs', collection=None, **kwargs
    ):
        """
        Establish the connection with the mongo backend and connect to the collections

        :param collection: ignores but provided for consistency w/ other doc_store_config patterns
        """
        # GridFS will throw an exception if the Database is wrapped in a MongoProxy. So don't wrap it.
        self.connection_params = {
            'db': db,
            'host': host,
            'port': port,
            'tz_aware': tz_aware,
            'user': user,
            'password': password,
            **kwargs
        }
        self.bucket = bucket
        self.do_connection()

    def do_connection(self):
        """
        Connects to mongodb.
        """
        mongo_db = connect_to_mongodb(**self.connection_params)

        self.fs = gridfs.GridFS(mongo_db, self.bucket)  # pylint: disable=invalid-name

        self.fs_files = mongo_db[self.bucket + ".files"]  # the underlying collection GridFS uses
        self.chunks = mongo_db[self.bucket + ".chunks"]

    def close_connections(self):
        """
        Closes any open connections to the underlying databases
        """
        self.fs_files.database.client.close()

    def ensure_connection(self):
        """
        Ensure that mongodb connection is open.
        """
        if self.check_connection():
            return
        self.do_connection()

    def check_connection(self):
        """
        Check if mongodb connection is open or not.
        """
        connection = self.fs_files.database.client
        try:
            connection.admin.command('ping')
            return True
        except pymongo.errors.InvalidOperation:
            return False

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
        self.ensure_connection()
        connection = self.fs_files.database.client
        if database:
            connection.drop_database(self.fs_files.database.name)
        elif collections:
            self.fs_files.drop()
            self.chunks.drop()
        else:
            self.fs_files.remove({})
            self.chunks.remove({})

        if connections:
            self.close_connections()

    def save(self, content):
        content_id, content_son = self.asset_db_key(content.location)

        # The way to version files in gridFS is to not use the file id as the _id but just as the filename.
        # Then you can upload as many versions as you like and access by date or version. Because we use
        # the location as the _id, we must delete before adding (there's no replace method in gridFS)
        self.delete(content_id)  # delete is a noop if the entry doesn't exist; so, don't waste time checking

        thumbnail_location = content.thumbnail_location.to_deprecated_list_repr() if content.thumbnail_location else None  # pylint: disable=line-too-long
        with self.fs.new_file(_id=content_id, filename=str(content.location), content_type=content.content_type,  # pylint: disable=line-too-long
                              displayname=content.name, content_son=content_son,
                              thumbnail_location=thumbnail_location,
                              import_path=content.import_path,
                              # getattr b/c caching may mean some pickled instances don't have attr
                              locked=getattr(content, 'locked', False)) as fp:

            # It seems that this code thought that only some specific object would have the `__iter__` attribute
            # but many more objects have this in python3 and shouldn't be using the chunking logic. For string and
            # byte streams we write them directly to gridfs and convert them to byetarrys if necessary.
            if hasattr(content.data, '__iter__') and not isinstance(content.data, (bytes, (str,))):
                custom_md5 = hashlib.md5()
                for chunk in content.data:
                    fp.write(chunk)
                    custom_md5.update(chunk)
                fp.custom_md5 = custom_md5.hexdigest()
            else:
                # Ideally we could just ensure that we don't get strings in here and only byte streams
                # but being confident of that wolud be a lot more work than we have time for so we just
                # handle both cases here.
                if isinstance(content.data, str):
                    encoded_data = content.data.encode('utf-8')
                    fp.write(encoded_data)
                    fp.custom_md5 = hashlib.md5(encoded_data).hexdigest()
                else:
                    fp.write(content.data)
                    fp.custom_md5 = hashlib.md5(content.data).hexdigest()

        return content

    def delete(self, location_or_id):
        """
        Delete an asset.
        """
        if isinstance(location_or_id, AssetKey):
            location_or_id, _ = self.asset_db_key(location_or_id)
        # Deletes of non-existent files are considered successful
        self.fs.delete(location_or_id)

    def find(self, location, throw_on_not_found=True, as_stream=False):  # pylint: disable=arguments-differ
        content_id, __ = self.asset_db_key(location)

        try:
            if as_stream:
                fp = self.fs.get(content_id)
                # Need to replace dict IDs with SON for chunk lookup to work under Python 3
                # because field order can be different and mongo cares about the order
                if isinstance(fp._id, dict):  # pylint: disable=protected-access
                    fp._file['_id'] = content_id  # pylint: disable=protected-access
                thumbnail_location = getattr(fp, 'thumbnail_location', None)
                if thumbnail_location:
                    thumbnail_location = location.course_key.make_asset_key(
                        'thumbnail',
                        thumbnail_location[4]
                    )

                return StaticContentStream(
                    location, fp.displayname, fp.content_type, fp, last_modified_at=fp.uploadDate,
                    thumbnail_location=thumbnail_location,
                    import_path=getattr(fp, 'import_path', None),
                    length=fp.length, locked=getattr(fp, 'locked', False),
                    content_digest=getattr(fp, 'custom_md5', None),
                )
            else:
                with self.fs.get(content_id) as fp:
                    # Need to replace dict IDs with SON for chunk lookup to work under Python 3
                    # because field order can be different and mongo cares about the order
                    if isinstance(fp._id, dict):  # pylint: disable=protected-access
                        fp._file['_id'] = content_id  # pylint: disable=protected-access
                    thumbnail_location = getattr(fp, 'thumbnail_location', None)
                    if thumbnail_location:
                        thumbnail_location = location.course_key.make_asset_key(
                            'thumbnail',
                            thumbnail_location[4]
                        )

                    return StaticContent(
                        location, fp.displayname, fp.content_type, fp.read(), last_modified_at=fp.uploadDate,
                        thumbnail_location=thumbnail_location,
                        import_path=getattr(fp, 'import_path', None),
                        length=fp.length, locked=getattr(fp, 'locked', False),
                        content_digest=getattr(fp, 'custom_md5', None),
                    )
        except NoFile:
            if throw_on_not_found:  # pylint: disable=no-else-raise
                raise NotFoundError(content_id)  # pylint: disable=raise-missing-from  # noqa: B904
            else:
                return None

    def export(self, location, output_directory):  # pylint: disable=missing-function-docstring
        content = self.find(location)

        filename = content.name
        if content.import_path is not None:
            output_directory = output_directory + '/' + os.path.dirname(content.import_path)

        if not os.path.exists(output_directory):
            os.makedirs(output_directory)

        # Escape invalid char from filename.
        export_name = escape_invalid_characters(name=filename, invalid_char_list=['/', '\\'])

        disk_fs = OSFS(output_directory)

        with disk_fs.open(export_name, 'wb') as asset_file:
            asset_file.write(content.data)

    def export_all_for_course(self, course_key, output_directory, assets_policy_file):
        """
        Export all of this course's assets to the output_directory. Export all of the assets'
        attributes to the policy file.

        Args:
            course_key (CourseKey): the :class:`CourseKey` identifying the course
            output_directory: the directory under which to put all the asset files
            assets_policy_file: the filename for the policy file which should be in the same
                directory as the other policy files.
        """
        policy = {}
        assets, __ = self.get_all_content_for_course(course_key)

        for asset in assets:
            # TODO: On 6/19/14, I had to put a try/except around this
            # to export a course. The course failed on JSON files in
            # the /static/ directory placed in it with an import.
            #
            # If this hasn't been looked at in a while, remove this comment.
            #
            # When debugging course exports, this might be a good place
            # to look. -- pmitros
            self.export(asset['asset_key'], output_directory)
            for attr, value in asset.items():
                if attr not in ['_id', 'md5', 'uploadDate', 'length', 'chunkSize', 'asset_key']:
                    policy.setdefault(asset['asset_key'].block_id, {})[attr] = value

        with open(assets_policy_file, 'w') as f:
            json.dump(policy, f, sort_keys=True, indent=4)

    def get_all_content_thumbnails_for_course(self, course_key):
        return self._get_all_content_for_course(course_key, get_thumbnails=True)[0]

    def get_all_content_for_course(self, course_key, start=0, maxresults=-1, sort=None, filter_params=None):
        return self._get_all_content_for_course(
            course_key, start=start, maxresults=maxresults, get_thumbnails=False, sort=sort, filter_params=filter_params
        )

    def remove_redundant_content_for_courses(self):
        """
        Finds and removes all redundant files (Mac OS metadata files with filename ".DS_Store"
        or filename starts with "._") for all courses
        """
        assets_to_delete = 0
        for prefix in ['_id', 'content_son']:
            query = SON([
                (f'{prefix}.tag', XASSET_LOCATION_TAG),
                (f'{prefix}.category', 'asset'),
                (f'{prefix}.name', {'$regex': ASSET_IGNORE_REGEX}),
            ])
            items = self.fs_files.find(query)
            for asset in items:
                self.fs.delete(asset[prefix])
                assets_to_delete += 1

            self.fs_files.remove(query)
        return assets_to_delete

    def _get_all_content_for_course(self,
                                    course_key,
                                    get_thumbnails=False,
                                    start=0,
                                    maxresults=-1,
                                    sort=None,
                                    filter_params=None):
        '''
        Returns a list of all static assets for a course. The return format is a list of asset data dictionary elements.

        The asset data dictionaries have the following keys:
            asset_key (:class:`opaque_keys.edx.AssetKey`): The key of the asset
            displayname: The human-readable name of the asset
            uploadDate (datetime.datetime): The date and time that the file was uploadDate
            contentType: The mimetype string of the asset
            md5: An md5 hash of the asset content
        '''
        query = query_for_course(course_key, 'asset' if not get_thumbnails else 'thumbnail')
        user_language = 'en'
        if filter_params:
            user_language = filter_params.pop('user_language', 'en')
            query.update(filter_params)

        # Count total matching documents
        count = self.fs_files.count_documents(query)

        sort_list = []
        if sort:
            sort = dict(sort)
            if 'displayname' in sort:
                # Apply case-insensitive sorting
                cursor = self.fs_files.find(query, {
                    'contentType': 1,
                    'locked': 1,
                    'chunkSize': 1,
                    'content_son': 1,
                    'displayname': 1,
                    'filename': 1,
                    'length': 1,
                    'import_path': 1,
                    'uploadDate': 1,
                    'thumbnail_location': 1,
                    'md5': 1
                })
                cursor = cursor.sort('displayname', sort['displayname']).collation(
                    {'locale': user_language, 'strength': 2}
                )
            else:
                # Apply simple sorting
                sort_list = list(sort.items())
                cursor = self.fs_files.find(query).sort(sort_list)
        else:
            cursor = self.fs_files.find(query)

        # Apply pagination
        if start > 0:
            cursor = cursor.skip(start)
        if maxresults > 0:
            cursor = cursor.limit(maxresults)

        assets = list(cursor)
        # Construct asset keys
        for asset in assets:
            asset_id = asset.get('content_son', asset['_id'])
            asset['asset_key'] = course_key.make_asset_key(asset_id['category'], asset_id['name'])
        return assets, count

    def set_attr(self, asset_key, attr, value=True):
        """
        Add/set the given attr on the asset at the given location. Does not allow overwriting gridFS built in
        attrs such as _id, md5, uploadDate, length. Value can be any type which pymongo accepts.

        Returns nothing

        Raises NotFoundError if no such item exists
        Raises AttributeError is attr is one of the build in attrs.

        :param asset_key: an AssetKey
        :param attr: which attribute to set
        :param value: the value to set it to (any type pymongo accepts such as datetime, number, string)
        """
        self.set_attrs(asset_key, {attr: value})

    def get_attr(self, location, attr, default=None):
        """
        Get the value of attr set on location. If attr is unset, it returns default. Unlike set, this accessor
        does allow getting the value of reserved keywords.
        :param location: a c4x asset location
        """
        return self.get_attrs(location).get(attr, default)

    def set_attrs(self, location, attr_dict):
        """
        Like set_attr but sets multiple key value pairs.

        Returns nothing.

        Raises NotFoundError if no such item exists
        Raises AttributeError is attr_dict has any attrs which are one of the build in attrs.

        :param location:  a c4x asset location
        """
        for attr in attr_dict.keys():
            if attr in ['_id', 'md5', 'uploadDate', 'length']:
                raise AttributeError(f"{attr} is a protected attribute.")
        asset_db_key, __ = self.asset_db_key(location)
        # catch upsert error and raise NotFoundError if asset doesn't exist
        result = self.fs_files.update_one({'_id': asset_db_key}, {"$set": attr_dict}, upsert=False)
        if result.matched_count == 0:
            raise NotFoundError(asset_db_key)

    def get_attrs(self, location):
        """
        Gets all of the attributes associated with the given asset. Note, returns even built in attrs
        such as md5 which you cannot resubmit in an update; so, don't call set_attrs with the result of this
        but only with the set of attrs you want to explicitly update.

        The attrs will be a superset of _id, contentType, chunkSize, filename, uploadDate, & md5

        :param location: a c4x asset location
        """
        asset_db_key, __ = self.asset_db_key(location)
        item = self.fs_files.find_one({'_id': asset_db_key})
        if item is None:
            raise NotFoundError(asset_db_key)
        return item

    def copy_all_course_assets(self, source_course_key, dest_course_key):
        """
        See :meth:`.ContentStore.copy_all_course_assets`

        This implementation fairly expensively copies all of the data
        """
        source_query = query_for_course(source_course_key)
        # it'd be great to figure out how to do all of this on the db server and not pull the bits over
        for asset in self.fs_files.find(source_query):
            asset_key = self.make_id_son(asset)
            # don't convert from string until fs access
            source_content = self.fs.get(asset_key)
            if isinstance(asset_key, str):
                asset_key = AssetKey.from_string(asset_key)
                __, asset_key = self.asset_db_key(asset_key)
            # Need to replace dict IDs with SON for chunk lookup to work under Python 3
            # because field order can be different and mongo cares about the order
            if isinstance(source_content._id, dict):  # pylint: disable=protected-access
                source_content._file['_id'] = asset_key.copy()  # pylint: disable=protected-access
            asset_key['org'] = dest_course_key.org
            asset_key['course'] = dest_course_key.course
            if getattr(dest_course_key, 'deprecated', False):  # remove the run if exists
                if 'run' in asset_key:
                    del asset_key['run']
                asset_id = asset_key
            else:  # add the run, since it's the last field, we're golden
                asset_key['run'] = dest_course_key.run
                asset_id = str(
                    dest_course_key.make_asset_key(asset_key['category'], asset_key['name']).for_branch(None)
                )
            try:
                self.create_asset(source_content, asset_id, asset, asset_key)
            except FileExists:
                self.fs.delete(file_id=asset_id)
                self.create_asset(source_content, asset_id, asset, asset_key)

    def create_asset(self, source_content, asset_id, asset, asset_key):
        """
        Creates a new asset
        :param source_content:
        :param asset_id:
        :param asset:
        :param asset_key:
        :return:
        """
        self.fs.put(
            source_content.read(),
            _id=asset_id, filename=asset['filename'], content_type=asset['contentType'],
            displayname=asset['displayname'], content_son=asset_key,
            # thumbnail is not technically correct but will be functionally correct as the code
            # only looks at the name which is not course relative.
            thumbnail_location=asset['thumbnail_location'],
            import_path=asset['import_path'],
            # getattr b/c caching may mean some pickled instances don't have attr
            locked=asset.get('locked', False)
        )

    def delete_all_course_assets(self, course_key):
        """
        Delete all assets identified via this course_key. Dangerous operation which may remove assets
        referenced by other runs or other courses.
        :param course_key:
        """
        course_query = query_for_course(course_key)
        matching_assets = self.fs_files.find(course_query)
        for asset in matching_assets:
            asset_key = self.make_id_son(asset)
            self.fs.delete(asset_key)

    # codifying the original order which pymongo used for the dicts coming out of location_to_dict
    # stability of order is more important than sanity of order as any changes to order make things
    # unfindable
    ordered_key_fields = ['category', 'name', 'course', 'tag', 'org', 'revision']
    property_names = {
        'category': 'block_type',
        'name': 'block_id',
        'course': 'course',
        'tag': 'DEPRECATED_TAG',
        'org': 'org',
        'revision': 'branch',
    }

    @classmethod
    def asset_db_key(cls, location):
        """
        Returns the database _id and son structured lookup to find the given asset location.
        """
        dbkey = SON((field_name,
                     getattr(location, cls.property_names[field_name])) for field_name in cls.ordered_key_fields)
        if getattr(location, 'deprecated', False):
            content_id = dbkey
        else:
            # NOTE, there's no need to state that run doesn't exist in the negative case b/c access via
            # SON requires equivalence (same keys and values in exact same order)
            dbkey['run'] = location.run
            content_id = str(location.for_branch(None))
        return content_id, dbkey

    def make_id_son(self, fs_entry):
        """
        Change the _id field in fs_entry into the properly ordered SON or string
        Args:
            fs_entry: the element returned by self.fs_files.find
        """
        _id_field = fs_entry.get('_id', fs_entry)
        if isinstance(_id_field, str):
            return _id_field
        dbkey = SON((field_name, _id_field.get(field_name)) for field_name in self.ordered_key_fields)
        if 'run' in _id_field:
            # NOTE, there's no need to state that run doesn't exist in the negative case b/c access via
            # SON requires equivalence (same keys and values in exact same order)
            dbkey['run'] = _id_field['run']
        fs_entry['_id'] = dbkey
        return dbkey

    def ensure_indexes(self):
        # Index needed thru 'category' by `_get_all_content_for_course` and others. That query also takes a sort
        # which can be `uploadDate`, `displayname`,
        # TODO: uncomment this line once this index in prod is cleaned up. See OPS-2863 for tracking clean up.
        #  create_collection_index(
        #      self.fs_files,
        #      [
        #          ('_id.tag', pymongo.ASCENDING),
        #          ('_id.org', pymongo.ASCENDING),
        #          ('_id.course', pymongo.ASCENDING),
        #          ('_id.category', pymongo.ASCENDING)
        #      ],
        #      sparse=True,
        #      background=True
        #  )
        create_collection_index(
            self.fs_files,
            [
                ('content_son.org', pymongo.ASCENDING),
                ('content_son.course', pymongo.ASCENDING),
                ('uploadDate', pymongo.DESCENDING)
            ],
            sparse=True,
            background=True
        )
        create_collection_index(
            self.fs_files,
            [
                ('_id.org', pymongo.ASCENDING),
                ('_id.course', pymongo.ASCENDING),
                ('_id.name', pymongo.ASCENDING)
            ],
            sparse=True,
            background=True
        )
        create_collection_index(
            self.fs_files,
            [
                ('content_son.org', pymongo.ASCENDING),
                ('content_son.course', pymongo.ASCENDING),
                ('content_son.name', pymongo.ASCENDING)
            ],
            sparse=True,
            background=True
        )
        create_collection_index(
            self.fs_files,
            [
                ('_id.org', pymongo.ASCENDING),
                ('_id.course', pymongo.ASCENDING),
                ('uploadDate', pymongo.ASCENDING)
            ],
            sparse=True,
            background=True
        )
        create_collection_index(
            self.fs_files,
            [
                ('_id.org', pymongo.ASCENDING),
                ('_id.course', pymongo.ASCENDING),
                ('displayname', pymongo.ASCENDING)
            ],
            sparse=True,
            background=True
        )
        create_collection_index(
            self.fs_files,
            [
                ('content_son.org', pymongo.ASCENDING),
                ('content_son.course', pymongo.ASCENDING),
                ('uploadDate', pymongo.ASCENDING)
            ],
            sparse=True,
            background=True
        )
        create_collection_index(
            self.fs_files,
            [
                ('content_son.org', pymongo.ASCENDING),
                ('content_son.course', pymongo.ASCENDING),
                ('displayname', pymongo.ASCENDING)
            ],
            sparse=True,
            background=True
        )


def query_for_course(course_key, category=None):
    """
    Construct a SON object that will query for all assets possibly limited to the given type
    (thumbnail v assets) in the course using the index in mongo_indexes.md
    """
    if getattr(course_key, 'deprecated', False):
        prefix = '_id'
    else:
        prefix = 'content_son'
    dbkey = SON([
        (f'{prefix}.tag', XASSET_LOCATION_TAG),
        (f'{prefix}.org', course_key.org),
        (f'{prefix}.course', get_library_or_course_attribute(course_key)),
    ])
    if category:
        dbkey[f'{prefix}.category'] = category
    if getattr(course_key, 'deprecated', False):
        dbkey[f'{prefix}.run'] = {'$exists': False}
    else:
        dbkey[f'{prefix}.run'] = course_key.run
    return dbkey
