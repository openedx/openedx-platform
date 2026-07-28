"""
Bookmarks Python API.
"""
# Why does this file exist instead of just using api.py?
# (1) to expose BookmarkService (from .services import BookmarksService) in
#     api.py; its implementation in services.py requires code from api.py. It
#     caused a circular import error unless we put the API implementation into
#     a separate file like this.
# (2) The code below imports a number of symbols that shouldn't be part of the
#     public API (DEFAULT_FIELDS, OPTIONAL_FIELDS, Bookmark, BookmarkSerializer,
#     modulestore, ItemNotFoundError, settings, tracker) and it's much easier
#     to make them private this way (importing them into api_impl.py without
#     changes) than it is to import them with an underscore prefix and change
#     the code to use the prefixed imports.
from django.conf import settings
from eventtracking import tracker

from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError

from . import DEFAULT_FIELDS, OPTIONAL_FIELDS
from .models import Bookmark
from .serializers import BookmarkSerializer


class BookmarksLimitReachedError(Exception):
    """
    if try to create new bookmark when max limit of bookmarks already reached
    """
    pass  # pylint: disable=unnecessary-pass


def get_bookmark(user, usage_key, fields=None):
    """
    Return data for a bookmark.

    Arguments:
        user (User): The user of the bookmark.
        usage_key (UsageKey): The usage_key of the bookmark.
        fields (list): List of field names the data should contain (optional).

    Returns:
        Dict.

    Raises:
        ObjectDoesNotExist: If a bookmark with the parameters does not exist.
    """
    bookmarks_queryset = Bookmark.objects

    if len(set(fields or []) & set(OPTIONAL_FIELDS)) > 0:
        bookmarks_queryset = bookmarks_queryset.select_related('user', 'xblock_cache')
    else:
        bookmarks_queryset = bookmarks_queryset.select_related('user')

    bookmark = bookmarks_queryset.get(user=user, usage_key=usage_key)
    return BookmarkSerializer(bookmark, context={'fields': fields}).data


def get_bookmarks(user, course_key=None, fields=None, serialized=True):
    """
    Return data for bookmarks of a user.

    Arguments:
        user (User): The user of the bookmarks.
        course_key (CourseKey): The course_key of the bookmarks (optional).
        fields (list): List of field names the data should contain (optional).
            N/A if serialized is False.
        serialized (bool): Whether to return a queryset or a serialized list of dicts.
            Default is True.

    Returns:
         List of dicts if serialized is True else queryset.
    """
    if user.is_authenticated:
        bookmarks_queryset = Bookmark.objects.filter(user=user)

        if course_key:
            bookmarks_queryset = bookmarks_queryset.filter(course_key=course_key)

        if len(set(fields or []) & set(OPTIONAL_FIELDS)) > 0:
            bookmarks_queryset = bookmarks_queryset.select_related('user', 'xblock_cache')
        else:
            bookmarks_queryset = bookmarks_queryset.select_related('user')

        bookmarks_queryset = bookmarks_queryset.order_by('-id')
    else:
        bookmarks_queryset = Bookmark.objects.none()

    if serialized:
        return BookmarkSerializer(bookmarks_queryset, context={'fields': fields}, many=True).data

    return bookmarks_queryset


def can_create_more(data):
    """
    Determine if a new Bookmark can be created for the course
    based on limit defined in django.conf.settings.MAX_BOOKMARKS_PER_COURSE

    Arguments:
        data (dict): The data to create the object with.
    Returns:
        Boolean
    """
    data = dict(data)

    user = data['user']
    course_key = data['usage_key'].course_key

    # User can create up to max_bookmarks_per_course bookmarks
    if Bookmark.objects.filter(user=user, course_key=course_key).count() >= settings.MAX_BOOKMARKS_PER_COURSE:
        return False

    return True


def create_bookmark(user, usage_key):
    """
    Create a bookmark.

    Arguments:
        user (User): The user of the bookmark.
        usage_key (UsageKey): The usage_key of the bookmark.

    Returns:
         Dict.

    Raises:
        ItemNotFoundError: If no block exists for the usage_key.
        BookmarksLimitReachedError: if try to create new bookmark when max limit of bookmarks already reached
    """

    usage_key = usage_key.replace(course_key=modulestore().fill_in_run(usage_key.course_key))
    data = {
        'user': user,
        'usage_key': usage_key
    }

    if usage_key.course_key.run is None:
        raise ItemNotFoundError

    if not can_create_more(data):
        raise BookmarksLimitReachedError

    bookmark, created = Bookmark.create(data)
    if created:
        _track_event('edx.bookmark.added', bookmark)
    return BookmarkSerializer(bookmark, context={'fields': DEFAULT_FIELDS + OPTIONAL_FIELDS}).data


def delete_bookmark(user, usage_key):
    """
    Delete a bookmark.

    Arguments:
        user (User): The user of the bookmark.
        usage_key (UsageKey): The usage_key of the bookmark.

    Returns:
         Dict.

    Raises:
        ObjectDoesNotExist: If a bookmark with the parameters does not exist.
    """
    bookmark = Bookmark.objects.get(user=user, usage_key=usage_key)
    bookmark.delete()
    _track_event('edx.bookmark.removed', bookmark)


def delete_bookmarks(usage_key):
    """
    Delete all bookmarks for usage_key.

    Arguments:
        usage_key (UsageKey): The usage_key of the bookmarks.
    """
    units_keys = []

    if usage_key.block_type == 'vertical':
        units_keys.append(usage_key)
    else:
        # NOTE: Get all children for deleted block
        descriptor = modulestore().get_item(usage_key)
        for child in descriptor.get_children():
            if usage_key.block_type == 'chapter':
                units_keys += [unit.location for unit in child.get_children()]
            else:
                units_keys.append(child.location)

    bookmarks = Bookmark.objects.filter(usage_key__in=units_keys)

    # Emit removed bookmard event
    for bookmark in bookmarks:
        _track_event('edx.bookmark.removed', bookmark)

    bookmarks.delete()


def _track_event(event_name, bookmark):
    """
    Emit events for a bookmark.

    Arguments:
        event_name: name of event to track
        bookmark: Bookmark object
    """
    tracker.emit(
        event_name,
        {
            'course_id': str(bookmark.course_key),
            'bookmark_id': bookmark.resource_id,
            'component_type': bookmark.usage_key.block_type,
            'component_usage_id': str(bookmark.usage_key),
        }
    )
