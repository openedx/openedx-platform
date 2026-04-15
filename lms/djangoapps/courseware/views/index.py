"""
View for Courseware Index
"""

# pylint: disable=attribute-defined-outside-init


import logging

from django.contrib.auth.views import redirect_to_login
from django.utils.decorators import method_decorator
from django.utils.functional import cached_property
from django.views.decorators.cache import cache_control
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import View
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey

from common.djangoapps.util.views import ensure_valid_course_key
from lms.djangoapps.courseware.exceptions import Redirect
from lms.djangoapps.courseware.masquerade import setup_masquerade
from openedx.features.course_experience import COURSE_ENABLE_UNENROLLED_ACCESS_FLAG
from openedx.features.course_experience.url_helpers import make_learning_mfe_courseware_url
from openedx.features.enterprise_support.api import data_sharing_consent_required
from xmodule.modulestore.django import modulestore

from ..block_render import get_block_for_descriptor
from ..courses import get_course_with_access
from ..permissions import MASQUERADE_AS_STUDENT

log = logging.getLogger("edx.courseware.views.index")


class CoursewareIndex(View):
    """
    View class for the Courseware page.
    """

    @cached_property
    def enable_unenrolled_access(self):
        return COURSE_ENABLE_UNENROLLED_ACCESS_FLAG.is_enabled(self.course_key)

    @method_decorator(ensure_csrf_cookie)
    @method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True))
    @method_decorator(ensure_valid_course_key)
    @method_decorator(data_sharing_consent_required)
    def get(self, request, course_id, section=None, subsection=None, position=None):
        """
        Instead of loading the legacy courseware sequences pages, load the equivalent URL
        in the learning MFE.  This view does not do any auth checks since they are done by
        the MFE when attempting to load content.

        Arguments:
            request: HTTP request
            course_id (unicode): course id
            section (unicode): section url_name
            subsection (unicode): subsection url_name
            position (unicode): position in block, eg of <sequential> block

        """

        self.course_key = CourseKey.from_string(course_id)

        if not (request.user.is_authenticated or self.enable_unenrolled_access):
            return redirect_to_login(request.get_full_path())

        # Course load to resolve sections/subsections
        with modulestore().bulk_operations(self.course_key):
            course = get_course_with_access(
                request.user,
                "load",
                self.course_key,
                depth=2,
                check_if_enrolled=True,
                check_if_authenticated=True,
            )

        # Get the section, subsection and unit blocks so that we can redirect to the right content
        # location in the MFE
        subsection_location = None
        if section and subsection:
            section_block = course.get_child_by(lambda m: m.location.block_id == section)
            if section_block:
                subsection_block = section_block.get_child_by(lambda m: m.location.block_id == subsection)
                if subsection_block:
                    subsection_location = subsection_block.location

        try:
            unit_key = UsageKey.from_string(request.GET.get('activate_block_id', ''))
            if unit_key.block_type != 'vertical':
                unit_key = None
        except InvalidKeyError:
            unit_key = None

        # Setup masquerading if needed for this user.
        # This in needed even though this view just does a redirect because
        # the relevant cookies and session data is set for future requests
        # when this function is called.
        self.masquerade, self.effective_user = setup_masquerade(
            self.request,
            self.course_key,
            request.user.has_perm(MASQUERADE_AS_STUDENT, course),
            reset_masquerade_data=True
        )
        # Set the user in the request to the effective user.
        self.request.user = self.effective_user
        mfe_url = make_learning_mfe_courseware_url(
            self.course_key,
            subsection_location,
            unit_key,
            params=request.GET,
            preview=False
        )
        raise Redirect(mfe_url)


def save_child_position(seq_block, child_name):
    """
    child_name: url_name of the child
    """
    for position, child in enumerate(seq_block.get_children(), start=1):
        if child.location.block_id == child_name:
            # Only save if position changed
            if position != seq_block.position:
                seq_block.position = position
    # Save this new position to the underlying KeyValueStore
    seq_block.save()


def save_positions_recursively_up(user, request, field_data_cache, xmodule, course=None):
    """
    Recurses up the course tree starting from a leaf
    Saving the position property based on the previous node as it goes
    """
    current_block = xmodule

    while current_block:
        parent_location = modulestore().get_parent_location(current_block.location)
        parent = None
        if parent_location:
            parent_block = modulestore().get_item(parent_location)
            parent = get_block_for_descriptor(
                user,
                request,
                parent_block,
                field_data_cache,
                current_block.location.course_key,
                course=course
            )

        if parent and hasattr(parent, 'position'):
            save_child_position(parent, current_block.location.block_id)

        current_block = parent
