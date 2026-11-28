"""
Test helpers for courses whose Files are stored in ``openedx_content``.
"""

from opaque_keys.edx.keys import CourseKey
from openedx_catalog import api as catalog_api
from openedx_catalog.models_api import CourseRun

from openedx.core.djangoapps.contentstore_shim import api
from openedx.core.djangoapps.contentstore_shim.models import CourseAssetBackend


def set_course_asset_backend_status_for_test(
    course_key: CourseKey,
    status: CourseAssetBackend.Status = CourseAssetBackend.Status.MIGRATED,
) -> None:
    """
    Set the status of a course's Files, e.g. to store them in
    ``openedx_content``.

    Unlike ``api.set_course_asset_backend_status()``, this creates the
    ``CourseRun`` if it doesn't exist, since test courses often don't have one
    (it's normally created when the course is first published).

    The status is cached, so test cases that use this must clear the cache
    between tests (as ``CacheIsolationTestCase`` and ``ModuleStoreTestCase``
    do).
    """
    try:
        catalog_api.get_course_run(course_key)
    except CourseRun.DoesNotExist:
        catalog_api.create_course_run_for_modulestore_course_with(course_key, title=f"Test course {course_key.run}")
    api.set_course_asset_backend_status(course_key, status)
