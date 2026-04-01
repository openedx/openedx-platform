"""
Helper functions for logic related to learning (courseare & course home) URLs.

Centralized in openedx/features/course_experience instead of lms/djangoapps/courseware
because the Studio course outline may need these utilities.
"""
from typing import Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpRequest
from django.http.request import QueryDict
from opaque_keys.edx.keys import CourseKey, UsageKey
from urllib.parse import urlparse

from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.search import path_to_location  # lint-amnesty, pylint: disable=wrong-import-order

from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers

User = get_user_model()


def get_courseware_url(
        usage_key: UsageKey,
        request: Optional[HttpRequest] = None,
        is_staff: bool = False,
) -> str:
    """
    Return the URL to the canonical learning experience for a given block.

    We choose between either the Legacy frontend or Learning MFE depending on the
    course that the block is in, the requesting user, and the state of
    the 'courseware' waffle flags.

    If redirecting to a specific Sequence or Sequence/Unit in a Learning MFE
    regardless of configuration, call make_learning_mfe_courseware_url directly
    for better performance.

    Raises:
        * ItemNotFoundError if no data at the `usage_key`.
        * NoPathToItem if we cannot build a path to the `usage_key`.
    """
    return _get_new_courseware_url(usage_key=usage_key, request=request, is_staff=is_staff)


def _get_new_courseware_url(
        usage_key: UsageKey,
        request: Optional[HttpRequest] = None,
        is_staff: bool = None,
) -> str:
    """
    Return the URL to the "new" (Learning Micro-Frontend) experience for a given block.

    Raises:
        * ItemNotFoundError if no data at the `usage_key`.
        * NoPathToItem if we cannot build a path to the `usage_key`.
    """
    course_key = usage_key.course_key.replace(version_guid=None, branch=None)
    preview = request.GET.get('preview') if request and request.GET else False
    branch_type = (
        ModuleStoreEnum.Branch.draft_preferred
    ) if preview and is_staff else ModuleStoreEnum.Branch.published_only

    path = path_to_location(modulestore(), usage_key, request, full_path=True, branch_type=branch_type)

    if len(path) <= 1:
        # Course-run-level block:
        # We have no Sequence or Unit to return.
        sequence_key, unit_key = None, None
    elif len(path) == 2:
        # Section-level (ie chapter) block:
        # The Section is the Sequence. We have no Unit to return.
        sequence_key, unit_key = path[1], None
    elif len(path) == 3:
        # Subsection-level block:
        # The Subsection is the Sequence. We still have no Unit to return.
        sequence_key, unit_key = path[2], None
    else:
        # Unit-level (or lower) block:
        # The Subsection is the Sequence, and the next level down is the Unit.
        sequence_key, unit_key = path[2], path[3]
    return make_learning_mfe_courseware_url(
        course_key=course_key,
        sequence_key=sequence_key,
        unit_key=unit_key,
        preview=preview,
        params=request.GET if request and request.GET else None,
    )


def make_learning_mfe_courseware_url(
        course_key: CourseKey,
        sequence_key: Optional[UsageKey] = None,
        unit_key: Optional[UsageKey] = None,
        params: Optional[QueryDict] = None,
        preview: bool = None,
) -> str:
    """
    Return a str with the URL for the specified courseware content in the Learning MFE.

    The micro-frontend determines the user's position in the vertical via
    a separate API call, so all we need here is the course_key, sequence, and
    vertical IDs to format it's URL. For simplicity and performance reasons,
    this method does not inspect the modulestore to try to figure out what
    Unit/Vertical a sequence is in. If you try to pass in a unit_key without
    a sequence_key, the value will just be ignored and you'll get a URL pointing
    to just the course_key.

    Note that `sequence_key` may either point to a Section (ie chapter) or
    Subsection (ie sequential), as those are both abstractly understood as
    "sequences". If you pass in a Section-level `sequence_key`, then the MFE
    will replace it with key of the first Subsection in that Section.

    It is also capable of determining our section and vertical if they're not
    present.  Fully specifying it all is preferable, though, as the
    micro-frontend can save itself some work, resulting in a better user
    experience.

    We're building a URL like this:

    {LEARNING_MICROFRONTEND_URL}/course/{course_id}/{sequence_id}/{veritcal_id}

    `course_key`, `sequence_key`, and `unit_key` can be either OpaqueKeys or
    strings. They're only ever used to concatenate a URL string.
    `params` is an optional QueryDict object (e.g. request.GET)
    """
    learning_microfrontend_url = configuration_helpers.get_value(
        'LEARNING_MICROFRONTEND_URL',
        settings.LEARNING_MICROFRONTEND_URL,
    )
    mfe_link = f'{learning_microfrontend_url}/course/{course_key}'
    get_params = params.copy() if params else None

    if preview:
        if len(get_params.keys()) > 1:
            get_params.pop('preview')
        else:
            get_params = None

        if (unit_key or sequence_key):
            mfe_link = f'{learning_microfrontend_url}/preview/course/{course_key}'

    if sequence_key:
        mfe_link += f'/{sequence_key}'

        if unit_key:
            mfe_link += f'/{unit_key}'

    if get_params:
        mfe_link += f'?{get_params.urlencode()}'

    return mfe_link


def get_learning_mfe_home_url(
        course_key: CourseKey,
        url_fragment: Optional[str] = None,
        params: Optional[QueryDict] = None,
) -> str:
    """
    Given a course run key and view name, return the appropriate course home (MFE) URL.

    We're building a URL like this:

    {LEARNING_MICROFRONTEND_URL}/course/course-v1:edX+DemoX+Demo_Course/dates

    `course_key` can be either an OpaqueKey or a string.
    `url_fragment` is an optional string.
    `params` is an optional QueryDict object (e.g. request.GET)
    """
    learning_microfrontend_url = configuration_helpers.get_value(
        'LEARNING_MICROFRONTEND_URL',
        settings.LEARNING_MICROFRONTEND_URL,
    )
    mfe_link = f'{learning_microfrontend_url}/course/{course_key}'

    if url_fragment:
        mfe_link += f'/{url_fragment}'

    if params:
        mfe_link += f'?{params.urlencode()}'

    return mfe_link


def is_request_from_learning_mfe(request: HttpRequest):
    """
    Returns whether the given request was made by the frontend-app-learning MFE.
    """
    url_str = configuration_helpers.get_value(
        'LEARNING_MICROFRONTEND_URL',
        settings.LEARNING_MICROFRONTEND_URL,
    )
    if not url_str:
        return False

    url = urlparse(url_str)
    mfe_url_base = f'{url.scheme}://{url.netloc}'
    return request.META.get('HTTP_REFERER', '').startswith(mfe_url_base)
