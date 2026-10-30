"""Service layer for the v4 legacy library list endpoint."""

from cms.djangoapps.contentstore.toggles import libraries_v1_enabled
from cms.djangoapps.contentstore.views.course import _accessible_libraries_iter, format_library_for_view
from cms.djangoapps.modulestore_migrator import api as migrator_api


def _list_order(library):
    """Sort key: display name ignoring case, then library key, so that pages are stable."""
    return str(library.display_name).casefold(), str(library.location.library_key)


def list_legacy_libraries(user, org=None, is_migrated=None):
    """
    Return the legacy libraries ``user`` can read that match the filters, in list order.

    Returns ``(libraries, migrations)``. ``libraries`` holds library summaries,
    ordered by display name ignoring case and then by library key.
    ``migrations`` maps a library key to its forwarding migration, or None,
    for each library whose migration had to be read to apply ``is_migrated``.
    It is empty when ``is_migrated`` is not given, so that a caller returning
    one page reads migrations for that page only.

    The filters only narrow what ``user`` can read: ``org`` matches the
    organization in the library key exactly, and ``is_migrated`` whether the
    library forwards to a content library. Nothing is listed while legacy
    libraries are disabled.
    """
    if not libraries_v1_enabled():
        return [], {}
    libraries = [
        library for library in _accessible_libraries_iter(user)
        if org is None or library.location.library_key.org == org
    ]
    migrations = {}
    if is_migrated is not None:
        migrations = {library.id: migrator_api.get_forwarding(library.id) for library in libraries}
        libraries = [library for library in libraries if bool(migrations[library.id]) == is_migrated]
    libraries.sort(key=_list_order)
    return libraries, migrations


def format_legacy_libraries(libraries, request, migrations):
    """
    Return one row per library in ``libraries``, as the Studio library listing shows it.

    The forwarding migration of a library missing from ``migrations`` is read here.
    """
    return [
        format_library_for_view(
            library,
            request,
            migration=(
                migrations[library.id] if library.id in migrations else migrator_api.get_forwarding(library.id)
            ),
        )
        for library in libraries
    ]
