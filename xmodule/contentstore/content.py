# pylint: disable=missing-module-docstring

import logging
import os
import re
import uuid
from io import BytesIO
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse

from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import AssetKey, CourseKey
from opaque_keys.edx.locator import AssetLocator, LibraryLocatorV2
from PIL import Image

from xmodule.assetstore.assetmgr import AssetManager
from xmodule.exceptions import NotFoundError
from xmodule.modulestore.exceptions import ItemNotFoundError

STATIC_CONTENT_VERSION = 1
XASSET_LOCATION_TAG = 'c4x'
XASSET_SRCREF_PREFIX = 'xasset:'
XASSET_THUMBNAIL_TAIL_NAME = '.jpg'
STREAM_DATA_CHUNK_SIZE = 1024
VERSIONED_ASSETS_PREFIX = '/assets/courseware'
VERSIONED_ASSETS_PATTERN = r'/assets/courseware/(v[\d]/)?([a-f0-9]{32})'


class StaticContent:  # pylint: disable=missing-class-docstring
    def __init__(self, loc, name, content_type, data, last_modified_at=None, thumbnail_location=None, import_path=None,
                 length=None, locked=False, content_digest=None):
        self.location = loc
        self.name = name  # a display string which can be edited, and thus not part of the location which needs to be fixed  # pylint: disable=line-too-long
        self.content_type = content_type
        self._data = data
        self.length = length
        self.last_modified_at = last_modified_at
        self.thumbnail_location = thumbnail_location
        # optional information about where this file was imported from. This is needed to support import/export
        # cycles
        self.import_path = import_path
        self.locked = locked
        self.content_digest = content_digest

    @property
    def is_thumbnail(self):
        return self.location.category == 'thumbnail'

    @staticmethod
    def generate_thumbnail_name(original_name, dimensions=None, extension=None):
        """
        - original_name: Name of the asset (typically its location.name)
        - dimensions: `None` or a tuple of (width, height) in pixels
        - extension: `None` or desired filename extension of the thumbnail
        """
        if extension is None:
            extension = XASSET_THUMBNAIL_TAIL_NAME

        name_root, ext = os.path.splitext(original_name)
        if not ext == extension:
            name_root = name_root + ext.replace('.', '-')

        if dimensions:
            width, height = dimensions
            name_root += f"-{width}x{height}"

        return "{name_root}{extension}".format(  # noqa: UP032
            name_root=name_root,
            extension=extension,
        )

    @staticmethod
    def compute_location(course_key, path, revision=None, is_thumbnail=False):  # pylint: disable=unused-argument
        """
        Constructs a location object for static content.

        - course_key: the course that this asset belongs to
        - path: is the name of the static asset
        - revision: is the object's revision information
        - is_thumbnail: is whether or not we want the thumbnail version of this
            asset
        """
        path = path.replace('/', '_')
        return course_key.make_asset_key(
            'asset' if not is_thumbnail else 'thumbnail',
            AssetLocator.clean_keeping_underscores(path)
        ).for_branch(None)

    def get_id(self):
        return self.location

    @property
    def data(self):
        return self._data

    ASSET_URL_RE = re.compile(r"""
        /?c4x/
        (?P<org>[^/]+)/
        (?P<course>[^/]+)/
        (?P<category>[^/]+)/
        (?P<name>[^/]+)
    """, re.VERBOSE | re.IGNORECASE)

    @staticmethod
    def is_c4x_path(path_string):
        """
        Returns a boolean if a path is believed to be a c4x link based on the leading element
        """
        return StaticContent.ASSET_URL_RE.match(path_string) is not None

    @staticmethod
    def get_static_path_from_location(location):
        """
        This utility static method will take a location identifier and create a 'durable' /static/.. URL representation of it.  # pylint: disable=line-too-long
        This link is 'durable' as it can maintain integrity across cloning of courseware across course-ids, e.g. reruns of  # pylint: disable=line-too-long
        courses.
        In the LMS/CMS, we have runtime link-rewriting, so at render time, this /static/... format will get translated into  # pylint: disable=line-too-long
        the actual /c4x/... path which the client needs to reference static content
        """
        if location is not None:
            return f"/static/{location.block_id}"
        else:
            return None

    @staticmethod
    def get_base_url_path_for_course_assets(course_key):  # pylint: disable=missing-function-docstring
        if (course_key is None) or isinstance(course_key, LibraryLocatorV2):
            return None

        assert isinstance(course_key, CourseKey)
        placeholder_id = uuid.uuid4().hex
        # create a dummy asset location with a fake but unique name. strip off the name, and return it
        url_path = StaticContent.serialize_asset_key_with_slash(
            course_key.make_asset_key('asset', placeholder_id).for_branch(None)
        )
        return url_path.replace(placeholder_id, '')

    @staticmethod
    def get_location_from_path(path):
        """
        Generate an AssetKey for the given path (old c4x/org/course/asset/name syntax)
        """
        try:
            return AssetKey.from_string(path)
        except InvalidKeyError:
            # TODO - re-address this once LMS-11198 is tackled.
            if path.startswith('/') or path.endswith('/'):
                # try stripping off the leading slash and try again
                return AssetKey.from_string(path.strip('/'))

    @staticmethod
    def is_versioned_asset_path(path):
        """Determines whether the given asset path is versioned."""
        return path.startswith(VERSIONED_ASSETS_PREFIX)

    @staticmethod
    def parse_versioned_asset_path(path):
        """
        Examines an asset path and breaks it apart if it is versioned,
        returning both the asset digest and the unversioned asset path,
        which will normally be an AssetKey.
        """
        asset_digest = None
        asset_path = path
        if StaticContent.is_versioned_asset_path(asset_path):
            result = re.match(VERSIONED_ASSETS_PATTERN, asset_path)
            if result is not None:
                asset_digest = result.groups()[1]
            asset_path = re.sub(VERSIONED_ASSETS_PATTERN, '', asset_path)

        return (asset_digest, asset_path)

    @staticmethod
    def add_version_to_asset_path(path, version):
        """
        Adds a prefix to an asset path indicating the asset's version.
        """

        # Don't version an already-versioned path.
        if StaticContent.is_versioned_asset_path(path):
            return path

        structure_version = f'v{STATIC_CONTENT_VERSION}'

        return f'{VERSIONED_ASSETS_PREFIX}/{structure_version}/{version}{path}'

    @staticmethod
    def get_asset_key_from_path(course_key, path):
        """
        Parses a path, extracting an asset key or creating one.

        Args:
            course_key: key to the course which owns this asset
            path: the path to said content

        Returns:
            AssetKey: the asset key that represents the path
        """

        # Clean up the path, removing any static prefix and any leading slash.
        if path.startswith('/static/'):
            path = path[len('/static/'):]

        # Old-style asset keys start with `/`, so don't try and strip it
        # in that case.
        if not path.startswith('/c4x'):
            path = path.lstrip('/')

        try:
            return AssetKey.from_string(path)
        except InvalidKeyError:
            # If we couldn't parse the path, just let compute_location figure it out.
            # It's most likely a path like /image.png or something.
            return StaticContent.compute_location(course_key, path)

    @staticmethod
    def is_excluded_asset_type(path, excluded_exts):
        """
        Check if this is an allowed file extension to serve.

        Some files aren't served through the CDN in order to avoid same-origin policy/CORS-related issues.
        """
        return any(path.lower().endswith(excluded_ext.lower()) for excluded_ext in excluded_exts)

    @staticmethod
    def get_canonicalized_asset_path(course_key, path, base_url, excluded_exts, encode=True):
        """
        Returns a fully-qualified path to a piece of static content.

        If a static asset CDN is configured, this path will include it.
        Otherwise, the path will simply be relative.

        Args:
            course_key: key to the course which owns this asset
            path: the path to said content

        Returns:
            string: fully-qualified path to asset
        """

        # Break down the input path.
        _, _, relative_path, params, query_string, _ = urlparse(path)

        # Convert our path to an asset key if it isn't one already.
        asset_key = StaticContent.get_asset_key_from_path(course_key, relative_path)

        # Check the status of the asset to see if this can be served via CDN aka publicly.
        serve_from_cdn = False
        content_digest = None
        try:
            content = AssetManager.find(asset_key, as_stream=True)
            serve_from_cdn = not getattr(content, "locked", True)
            content_digest = getattr(content, "content_digest", None)
        except (ItemNotFoundError, NotFoundError):
            # If we can't find the item, just treat it as if it's locked.
            serve_from_cdn = False

        # Do a generic check to see if anything about this asset disqualifies it from being CDN'd.
        is_excluded = False
        if StaticContent.is_excluded_asset_type(relative_path, excluded_exts):
            serve_from_cdn = False
            is_excluded = True

        # Update any query parameter values that have asset paths in them. This is for assets that
        # require their own after-the-fact values, like a Flash file that needs the path of a config
        # file passed to it e.g. /static/visualization.swf?configFile=/static/visualization.xml
        query_params = parse_qsl(query_string)
        updated_query_params = []
        for query_name, query_val in query_params:
            if query_val.startswith("/static/"):
                new_val = StaticContent.get_canonicalized_asset_path(
                    course_key, query_val, base_url, excluded_exts, encode=False)
                updated_query_params.append((query_name, new_val.encode('utf-8')))
            else:
                # Make sure we're encoding Unicode strings down to their byte string
                # representation so that `urlencode` can handle it.
                updated_query_params.append((query_name, query_val.encode('utf-8')))

        serialized_asset_key = StaticContent.serialize_asset_key_with_slash(asset_key)
        base_url = base_url if serve_from_cdn else ''
        asset_path = serialized_asset_key

        # If the content has a digest (i.e. md5sum) value specified, create a versioned path to the asset using it.
        if not is_excluded and content_digest:
            asset_path = StaticContent.add_version_to_asset_path(serialized_asset_key, content_digest)

        # Only encode this if told to.  Important so that we don't double encode
        # when working with paths that are in query parameters.
        if encode:
            asset_path = asset_path.encode('utf-8')
            asset_path = quote_plus(asset_path, '/:+@')

        return urlunparse(('', base_url, asset_path, params, urlencode(updated_query_params), ''))

    def stream_data(self):
        yield self._data

    @staticmethod
    def serialize_asset_key_with_slash(asset_key):
        """
        Legacy code expects the serialized asset key to start w/ a slash; so, do that in one place
        :param asset_key:
        """
        url = str(asset_key)
        if not url.startswith('/'):
            url = '/' + url  # TODO - re-address this once LMS-11198 is tackled.
        return url


class StaticContentStream(StaticContent):  # pylint: disable=missing-class-docstring
    def __init__(self, loc, name, content_type, stream, last_modified_at=None, thumbnail_location=None, import_path=None,  # pylint: disable=line-too-long
                 length=None, locked=False, content_digest=None):
        super().__init__(loc, name, content_type, None, last_modified_at=last_modified_at,
                         thumbnail_location=thumbnail_location, import_path=import_path,
                         length=length, locked=locked, content_digest=content_digest)
        self._stream = stream

    def stream_data(self):
        while True:
            chunk = self._stream.read(STREAM_DATA_CHUNK_SIZE)
            if len(chunk) == 0:
                break
            yield chunk

    def stream_data_in_range(self, first_byte, last_byte):
        """
        Stream the data between first_byte and last_byte (included)
        """
        self._stream.seek(first_byte)
        position = first_byte
        while True:
            if last_byte < position + STREAM_DATA_CHUNK_SIZE - 1:
                chunk = self._stream.read(last_byte - position + 1)
                yield chunk
                break
            chunk = self._stream.read(STREAM_DATA_CHUNK_SIZE)
            position += STREAM_DATA_CHUNK_SIZE
            yield chunk

    def close(self):
        self._stream.close()

    def copy_to_in_mem(self):  # pylint: disable=missing-function-docstring
        self._stream.seek(0)
        content = StaticContent(self.location, self.name, self.content_type, self._stream.read(),
                                last_modified_at=self.last_modified_at, thumbnail_location=self.thumbnail_location,
                                import_path=self.import_path, length=self.length, locked=self.locked,
                                content_digest=self.content_digest)
        return content


class ContentStore:
    '''
    Abstraction for all ContentStore providers (e.g. MongoDB)
    '''
    def save(self, content):
        raise NotImplementedError

    def find(self, filename):
        raise NotImplementedError

    def get_all_content_for_course(self, course_key, start=0, maxresults=-1, sort=None, filter_params=None):
        '''
        Returns a list of static assets for a course, followed by the total number of assets.
        By default all assets are returned, but start and maxresults can be provided to limit the query.

        The return format is a list of asset data dictionaries.
        The asset data dictionaries have the following keys:
            asset_key (:class:`opaque_keys.edx.AssetKey`): The key of the asset
            displayname: The human-readable name of the asset
            uploadDate (datetime.datetime): The date and time that the file was uploadDate
            contentType: The mimetype string of the asset
            md5: An md5 hash of the asset content
        '''
        raise NotImplementedError

    def delete_all_course_assets(self, course_key):
        """
        Delete all of the assets which use this course_key as an identifier
        :param course_key:
        """
        raise NotImplementedError

    def copy_all_course_assets(self, source_course_key, dest_course_key):
        """
        Copy all the course assets from source_course_key to dest_course_key
        """
        raise NotImplementedError

    def generate_thumbnail(self, content, tempfile_path=None, dimensions=None):
        """Create a thumbnail for a given image.

        Returns a tuple of (StaticContent, AssetKey)

        `content` is the StaticContent representing the image you want to make a
        thumbnail out of.

        `tempfile_path` is a string path to the location of a file to read from
        in order to grab the image data, instead of relying on `content.data`

        `dimensions` is an optional param that represents (width, height) in
        pixels. It defaults to None.
        """
        thumbnail_content = None
        is_svg = content.content_type == 'image/svg+xml'
        # use a naming convention to associate originals with the thumbnail
        thumbnail_name = StaticContent.generate_thumbnail_name(
            content.location.block_id, dimensions=dimensions, extension='.svg' if is_svg else None
        )
        thumbnail_file_location = StaticContent.compute_location(
            content.location.course_key, thumbnail_name, is_thumbnail=True
        )

        # if we're uploading an image, then let's generate a thumbnail so that we can
        # serve it up when needed without having to rescale on the fly
        try:
            if is_svg:
                # for svg simply store the provided svg file, since vector graphics should be good enough
                # for downscaling client-side
                if tempfile_path is None:
                    thumbnail_file = BytesIO(content.data)
                else:
                    with open(tempfile_path) as f:
                        thumbnail_file = BytesIO(f.read())
                thumbnail_content = StaticContent(thumbnail_file_location, thumbnail_name,
                                                  'image/svg+xml', thumbnail_file)
                self.save(thumbnail_content)
            elif content.content_type is not None and content.content_type.split('/')[0] == 'image':
                # use PIL to do the thumbnail generation (http://www.pythonware.com/products/pil/)
                # My understanding is that PIL will maintain aspect ratios while restricting
                # the max-height/width to be whatever you pass in as 'size'
                # @todo: move the thumbnail size to a configuration setting?!?
                if tempfile_path is None:
                    source = BytesIO(content.data)
                else:
                    source = tempfile_path

                # We use the context manager here to avoid leaking the inner file descriptor
                # of the Image object -- this way it gets closed after we're done with using it.
                thumbnail_file = BytesIO()
                with Image.open(source) as image:
                    # I've seen some exceptions from the PIL library when trying to save palletted
                    # PNG files to JPEG. Per the google-universe, they suggest converting to RGB first.
                    thumbnail_image = image.convert('RGB')

                    if not dimensions:
                        dimensions = (128, 128)

                    thumbnail_image.thumbnail(dimensions, Image.LANCZOS)
                    thumbnail_image.save(thumbnail_file, 'JPEG')
                    thumbnail_file.seek(0)

                # store this thumbnail as any other piece of content
                thumbnail_content = StaticContent(thumbnail_file_location, thumbnail_name,
                                                  'image/jpeg', thumbnail_file)

                self.save(thumbnail_content)

        except Exception as exc:  # pylint: disable=broad-except
            # log and continue as thumbnails are generally considered as optional
            logging.exception(
                "Failed to generate thumbnail for {}. Exception: {}".format(content.location, str(exc))  # noqa: UP032
            )

        return thumbnail_content, thumbnail_file_location

    def ensure_indexes(self):
        """
        Ensure that all appropriate indexes are created that are needed by this modulestore, or raise
        an exception if unable to.
        """
        pass  # pylint: disable=unnecessary-pass
