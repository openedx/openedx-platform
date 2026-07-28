# pylint: disable=missing-module-docstring

from xmodule.contentstore.content import StaticContent

from .django import contentstore


def empty_asset_trashcan(course_locs):
    '''
    This method will hard delete all assets (optionally within a course_id) from the trashcan
    '''
    store = contentstore('trashcan')

    for course_loc in course_locs:
        # first delete all of the thumbnails
        thumbs = store.get_all_content_thumbnails_for_course(course_loc)
        for thumb in thumbs:
            print(f"Deleting {thumb}...")
            store.delete(thumb['asset_key'])

        # then delete all of the assets
        assets, __ = store.get_all_content_for_course(course_loc)
        for asset in assets:
            print(f"Deleting {asset}...")
            store.delete(asset['asset_key'])


def restore_asset_from_trashcan(location):
    '''
    This method will restore an asset which got soft deleted and put back in the original course
    '''
    trash = contentstore('trashcan')
    store = contentstore()

    loc = StaticContent.get_location_from_path(location)
    content = trash.find(loc)

    # ok, save the content into the courseware
    store.save(content)

    # see if there is a thumbnail as well, if so move that as well
    if content.thumbnail_location is not None:
        try:
            thumbnail_content = trash.find(content.thumbnail_location)
            store.save(thumbnail_content)
        except Exception:  # pylint: disable=broad-except
            pass  # OK if this is left dangling
