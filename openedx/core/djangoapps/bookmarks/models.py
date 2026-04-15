"""
Models for Bookmarks.
"""


import logging

from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.db import models
from jsonfield.fields import JSONField
from model_utils.models import TimeStampedModel
from opaque_keys.edx.django.models import CourseKeyField, UsageKeyField
from opaque_keys.edx.keys import UsageKey

from xmodule.modulestore import search
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError, NoPathToItem

from . import PathItem

log = logging.getLogger(__name__)


def prepare_path_for_serialization(path):
    """
    Return the data from a list of PathItems ready for serialization to json.
    """
    return [(str(path_item.usage_key), path_item.display_name) for path_item in path]


def parse_path_data(path_data):
    """
    Return a list of PathItems constructed from parsing path_data.
    """
    path = []
    for item in path_data:
        usage_key = UsageKey.from_string(item[0])
        usage_key = usage_key.replace(course_key=modulestore().fill_in_run(usage_key.course_key))
        path.append(PathItem(usage_key, item[1]))
    return path


class Bookmark(TimeStampedModel):
    """
    Bookmarks model.

    .. no_pii:
    """
    user = models.ForeignKey(User, db_index=True, on_delete=models.CASCADE)
    course_key = CourseKeyField(max_length=255, db_index=True)
    usage_key = UsageKeyField(max_length=255, db_index=True)
    _path = JSONField(db_column='path', help_text='Path in course tree to the block')

    xblock_cache = models.ForeignKey('bookmarks.XBlockCache', on_delete=models.CASCADE)

    class Meta:
        """
        Bookmark metadata.
        """
        unique_together = ('user', 'usage_key')

    def __str__(self):
        return self.resource_id

    @classmethod
    def create(cls, data):
        """
        Create a Bookmark object.

        Arguments:
            data (dict): The data to create the object with.

        Returns:
            A Bookmark object.

        Raises:
            ItemNotFoundError: If no block exists for the usage_key.
        """
        data = dict(data)
        usage_key = data.pop('usage_key')

        with modulestore().bulk_operations(usage_key.course_key):
            block = modulestore().get_item(usage_key)

            xblock_cache = XBlockCache.create({
                'usage_key': usage_key,
                'display_name': block.display_name_with_default,
            })
            data['_path'] = prepare_path_for_serialization(Bookmark.updated_path(usage_key, xblock_cache))

        data['course_key'] = usage_key.course_key
        data['xblock_cache'] = xblock_cache

        user = data.pop('user')

        # Sometimes this ends up in data, but newer versions of Django will fail on having unknown keys in defaults
        data.pop('display_name', None)

        bookmark, created = cls.objects.get_or_create(usage_key=usage_key, user=user, defaults=data)
        return bookmark, created

    @property
    def resource_id(self):
        """
        Return the resource id: {username,usage_id}.
        """
        return f"{self.user.username},{self.usage_key}"

    @property
    def display_name(self):
        """
        Return the display_name from self.xblock_cache.

        Returns:
            String.
        """
        return self.xblock_cache.display_name  # pylint: disable=no-member

    @property
    def path(self):
        """
        Return the path to the bookmark's block after checking self.xblock_cache.

        Returns:
            List of dicts.
        """
        if self.modified < self.xblock_cache.modified:  # pylint: disable=no-member
            path = Bookmark.updated_path(self.usage_key, self.xblock_cache)
            self._path = prepare_path_for_serialization(path)
            self.save()  # Always save so that self.modified is updated.
            return path

        return parse_path_data(self._path)

    @staticmethod
    def updated_path(usage_key, xblock_cache):
        """
        Return the update-to-date path.

        xblock_cache.paths is the list of all possible paths to a block
        constructed by doing a DFS of the tree. However, in case of DAGS,
        which section jump_to_id() takes the user to depends on the
        modulestore. If xblock_cache.paths has only one item, we can
        just use it. Otherwise, we use path_to_location() to get the path
        jump_to_id() will take the user to.
        """
        if xblock_cache.paths and len(xblock_cache.paths) == 1:
            return xblock_cache.paths[0]

        return Bookmark.get_path(usage_key)

    @staticmethod
    def get_path(usage_key):
        """
        Returns data for the path to the block in the course graph.

        Note: In case of multiple paths to the block from the course
        root, this function returns a path arbitrarily but consistently,
        depending on the modulestore. In the future, we may want to
        extend it to check which of the paths, the user has access to
        and return its data.

        Arguments:
            block (XBlock): The block whose path is required.

        Returns:
            list of PathItems
        """
        with modulestore().bulk_operations(usage_key.course_key):
            try:
                path = search.path_to_location(modulestore(), usage_key, full_path=True)
            except ItemNotFoundError:
                log.error('Block with usage_key: %s not found.', usage_key)
                return []
            except NoPathToItem:
                log.error('No path to block with usage_key: %s.', usage_key)
                return []

            path_data = []
            for ancestor_usage_key in path:
                if ancestor_usage_key != usage_key and ancestor_usage_key.block_type != 'course':
                    try:
                        block = modulestore().get_item(ancestor_usage_key)
                    except ItemNotFoundError:
                        return []  # No valid path can be found.
                    path_data.append(
                        PathItem(usage_key=block.location, display_name=block.display_name_with_default)
                    )

        return path_data


class XBlockCache(TimeStampedModel):
    """
    XBlockCache model to store info about xblocks.

    .. no_pii:
    """

    course_key = CourseKeyField(max_length=255, db_index=True)
    usage_key = UsageKeyField(max_length=255, db_index=True, unique=True)

    display_name = models.CharField(max_length=255, default='')
    _paths = JSONField(
        db_column='paths', default=[], help_text='All paths in course tree to the corresponding block.'
    )

    def __str__(self):
        return str(self.usage_key)

    @property
    def paths(self):
        """
        Return paths.

        Returns:
            list of list of PathItems.
        """
        return [parse_path_data(path) for path in self._paths] if self._paths else self._paths

    @paths.setter
    def paths(self, value):
        """
        Set paths.

        Arguments:
            value (list of list of PathItems): The list of paths to cache.
        """
        self._paths = [prepare_path_for_serialization(path) for path in value] if value else value

    @classmethod
    def create(cls, data):
        """
        Create an XBlockCache object.

        Arguments:
            data (dict): The data to create the object with.

        Returns:
            An XBlockCache object.
        """
        data = dict(data)

        usage_key = data.pop('usage_key')
        usage_key = usage_key.replace(course_key=modulestore().fill_in_run(usage_key.course_key))

        data['course_key'] = usage_key.course_key
        xblock_cache, created = cls.objects.get_or_create(usage_key=usage_key, defaults=data)

        if not created:
            new_display_name = data.get('display_name', xblock_cache.display_name)
            if xblock_cache.display_name != new_display_name:
                xblock_cache.display_name = new_display_name
                xblock_cache.save()

        return xblock_cache
