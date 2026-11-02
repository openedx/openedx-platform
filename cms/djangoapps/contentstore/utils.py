"""
Common utility functions useful throughout the contentstore
"""
from __future__ import annotations

import configparser
import html
import logging
import re
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlencode, urlparse, urlunparse
from uuid import uuid4

from bs4 import BeautifulSoup
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist, ValidationError
from django.urls import reverse
from django.utils import translation
from django.utils.text import Truncator
from django.utils.translation import gettext as _
from eventtracking import tracker
from help_tokens.core import HelpUrlExpert
from lti_consumer.models import CourseAllowPIISharingInLTIFlag
from milestones import api as milestones_api
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey, UsageKeyV2
from opaque_keys.edx.locator import BlockUsageLocator, LibraryContainerLocator, LibraryLocator
from openedx_events.content_authoring.data import DuplicatedXBlockData
from openedx_events.content_authoring.signals import XBLOCK_DUPLICATED
from openedx_events.learning.data import CourseNotificationData
from openedx_events.learning.signals import COURSE_NOTIFICATION_REQUESTED
from pytz import UTC
from rest_framework.fields import BooleanField
from xblock.fields import Scope

from cms.djangoapps.contentstore.toggles import (
    enable_course_optimizer,
    exam_setting_view_enabled,
    libraries_v1_enabled,
    libraries_v2_enabled,
    split_library_view_on_dashboard,
    use_new_advanced_settings_page,
    use_new_certificates_page,
    use_new_course_team_page,
    use_new_custom_pages,
    use_new_export_page,
    use_new_grading_page,
    use_new_group_configurations_page,
    use_new_import_page,
    use_new_schedule_details_page,
    use_new_textbooks_page,
    use_new_unit_page,
    use_new_updates_page,
    use_new_video_uploads_page,
)
from cms.djangoapps.models.settings.course_grading import CourseGradingModel
from cms.djangoapps.models.settings.course_metadata import CourseMetadata
from cms.djangoapps.modulestore_migrator import api as migrator_api
from cms.djangoapps.modulestore_migrator.data import ModulestoreMigration
from common.djangoapps.course_action_state.managers import CourseActionStateItemNotFoundError
from common.djangoapps.course_action_state.models import CourseRerunState, CourseRerunUIStateManager
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.edxmako.services import MakoService
from common.djangoapps.student import auth
from common.djangoapps.student.auth import STUDIO_EDIT_ROLES, has_studio_read_access, has_studio_write_access
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseStaffRole,
    GlobalStaff,
)
from common.djangoapps.track import contexts
from common.djangoapps.util.course import get_link_for_about_page
from common.djangoapps.util.date_utils import get_default_time_display
from common.djangoapps.util.milestones_helpers import (
    generate_milestone_namespace,
    get_namespace_choices,
    is_prerequisite_courses_enabled,
    is_valid_course_key,
    remove_prerequisite_course,
    set_prerequisite_courses,
)
from common.djangoapps.xblock_django.api import deprecated_xblocks
from common.djangoapps.xblock_django.user_service import DjangoXBlockUserService
from openedx.core import toggles as core_toggles
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content_libraries.api import get_container
from openedx.core.djangoapps.content_tagging.toggles import is_tagging_feature_disabled
from openedx.core.djangoapps.credit.api import get_credit_requirements, is_credit_course
from openedx.core.djangoapps.discussions.config.waffle import ENABLE_PAGES_AND_RESOURCES_MICROFRONTEND
from openedx.core.djangoapps.discussions.models import DiscussionsConfiguration
from openedx.core.djangoapps.django_comment_common.models import assign_default_role
from openedx.core.djangoapps.django_comment_common.utils import seed_permissions_roles
from openedx.core.djangoapps.models.course_details import CourseDetails
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.site_configuration.models import SiteConfiguration
from openedx.core.djangoapps.xblock.api import get_component_from_usage_key
from openedx.core.lib.courses import course_image_url
from openedx.core.lib.html_to_text import html_to_text
from openedx.core.lib.teams_config import CONTENT_GROUPS_FOR_TEAMS, TEAM_SCHEME
from openedx.features.content_type_gating.models import ContentTypeGatingConfig
from openedx.features.content_type_gating.partitions import CONTENT_TYPE_GATING_SCHEME
from openedx.features.course_experience.waffle import ENABLE_COURSE_ABOUT_SIDEBAR_HTML
from xmodule.course_block import DEFAULT_START_DATE  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.data import CertificatesDisplayBehaviors
from xmodule.library_tools import LegacyLibraryToolsService
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.exceptions import ItemNotFoundError  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.partitions.partitions_service import (
    get_all_partitions_for_course,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.services import ConfigurationService, SettingsService, TeamsConfigurationService
from xmodule.util.keys import BlockKey

from .models import ComponentLink, ContainerLink

IMPORTABLE_FILE_TYPES = ('.tar.gz', '.zip')
log = logging.getLogger(__name__)


def add_instructor(course_key, requesting_user, new_instructor):
    """
    Adds given user as instructor and staff to the given course,
    after verifying that the requesting_user has permission to do so.
    """
    # can't use auth.add_users here b/c it requires user to already have Instructor perms in this course
    CourseInstructorRole(course_key).add_users(new_instructor)
    auth.add_users(requesting_user, CourseStaffRole(course_key), new_instructor)


def initialize_permissions(course_key, user_who_created_course):
    """
    Initializes a new course by enrolling the course creator as a student,
    and initializing Forum by seeding its permissions and assigning default roles.
    """
    # seed the forums
    seed_permissions_roles(course_key)

    # auto-enroll the course creator in the course so that "View Live" will work.
    CourseEnrollment.enroll(user_who_created_course, course_key)

    # set default forum roles (assign 'Student' role)
    assign_default_role(course_key, user_who_created_course)


def remove_all_instructors(course_key):
    """
    Removes all instructor and staff users from the given course.
    """
    staff_role = CourseStaffRole(course_key)
    staff_role.remove_users(*staff_role.users_with_role())
    instructor_role = CourseInstructorRole(course_key)
    instructor_role.remove_users(*instructor_role.users_with_role())


def delete_course(course_key, user_id, keep_instructors=False):
    """
    Delete course from module store and if specified remove user and
    groups permissions from course.
    """
    _delete_course_from_modulestore(course_key, user_id)

    if not keep_instructors:
        _remove_instructors(course_key)


def _delete_course_from_modulestore(course_key, user_id):
    """
    Delete course from MongoDB. Deleting course will fire a signal which will result into
    deletion of the courseware associated with a course_key.
    """
    module_store = modulestore()

    with module_store.bulk_operations(course_key):
        module_store.delete_course(course_key, user_id)


def _remove_instructors(course_key):
    """
    In the django layer, remove all the user/groups permissions associated with this course
    """
    print('removing User permissions from course....')

    try:
        remove_all_instructors(course_key)
    except Exception as err:  # lint-amnesty, pylint: disable=broad-except
        log.error(f"Error in deleting course groups for {course_key}: {err}")


def get_lms_link_for_item(location, preview=False):
    """
    Returns an LMS link to the course with a jump_to to the provided location.

    :param location: the location to jump to
    :param preview: True if the preview version of LMS should be returned. Default value is false.
    """
    assert isinstance(location, UsageKey)

    # checks LMS_ROOT_URL value in site configuration for the given course_org_filter(org)
    # if not found returns settings.LMS_ROOT_URL
    lms_base = SiteConfiguration.get_value_for_org(
        location.org,
        "LMS_ROOT_URL",
        settings.LMS_ROOT_URL
    )
    query_string = ''

    if lms_base is None:
        return None

    if preview:
        params = {'preview': '1'}
        query_string = urlencode(params)

    url_parts = list(urlparse(lms_base))
    url_parts[2] = '/courses/{course_key}/jump_to/{location}'.format(
        course_key=str(location.course_key),
        location=str(location),
    )
    url_parts[4] = query_string

    return urlunparse(url_parts)


def get_lms_link_for_certificate_web_view(course_key, mode):
    """
    Returns the url to the certificate web view.
    """
    assert isinstance(course_key, CourseKey)

    # checks LMS_BASE value in SiteConfiguration against course_org_filter if not found returns settings.LMS_BASE
    lms_base = SiteConfiguration.get_value_for_org(course_key.org, "LMS_BASE", settings.LMS_BASE)

    if lms_base is None:
        return None

    return "//{certificate_web_base}/certificates/course/{course_id}?preview={mode}".format(
        certificate_web_base=lms_base,
        course_id=str(course_key),
        mode=mode
    )


def get_course_authoring_url(course_locator):
    """
    Gets course authoring microfrontend URL
    """
    return configuration_helpers.get_value_for_org(
        course_locator.org,
        'COURSE_AUTHORING_MICROFRONTEND_URL',
        settings.COURSE_AUTHORING_MICROFRONTEND_URL
    )


def get_pages_and_resources_url(course_locator):
    """
    Gets course authoring microfrontend URL for Pages and Resources view.
    """
    pages_and_resources_url = None
    if ENABLE_PAGES_AND_RESOURCES_MICROFRONTEND.is_enabled(course_locator):
        mfe_base_url = get_course_authoring_url(course_locator)
        if mfe_base_url:
            pages_and_resources_url = f'{mfe_base_url}/course/{course_locator}/pages-and-resources'
    return pages_and_resources_url


def get_proctored_exam_settings_url(course_locator) -> str:
    """
    Gets course authoring microfrontend URL for links to proctored exam settings page
    """
    proctored_exam_settings_url = ''
    if exam_setting_view_enabled(course_locator):
        mfe_base_url = get_course_authoring_url(course_locator)
        course_mfe_url = f'{mfe_base_url}/course/{course_locator}'
        if mfe_base_url:
            proctored_exam_settings_url = f'{course_mfe_url}/pages-and-resources/proctoring/settings'
    return proctored_exam_settings_url


def get_editor_page_base_url(course_locator) -> str:
    """
    Gets course authoring microfrontend URL for links to the new base editors
    """
    editor_url = None
    mfe_base_url = get_course_authoring_url(course_locator)
    course_mfe_url = f'{mfe_base_url}/course/{course_locator}/editor'
    if mfe_base_url:
        editor_url = course_mfe_url
    return editor_url


def get_studio_home_url():
    """
    Gets course authoring microfrontend URL for Studio Home view.
    """
    mfe_base_url = settings.COURSE_AUTHORING_MICROFRONTEND_URL
    if mfe_base_url:
        studio_home_url = f'{mfe_base_url}/home'
        return studio_home_url

    raise ImproperlyConfigured(
        "The COURSE_AUTHORING_MICROFRONTEND_URL must be configured. "
        "Please set it to the base url for your authoring MFE."
    )


def get_schedule_details_url(course_locator) -> str:
    """
    Gets course authoring microfrontend URL for schedule and details pages view.
    """
    schedule_details_url = None
    if use_new_schedule_details_page(course_locator):
        mfe_base_url = get_course_authoring_url(course_locator)
        course_mfe_url = f'{mfe_base_url}/course/{course_locator}/settings/details'
        if mfe_base_url:
            schedule_details_url = course_mfe_url
    return schedule_details_url


def get_advanced_settings_url(course_locator) -> str:
    """
    Gets course authoring microfrontend URL for advanced settings page view.
    """
    advanced_settings_url = None
    if use_new_advanced_settings_page(course_locator):
        mfe_base_url = get_course_authoring_url(course_locator)
        course_mfe_url = f'{mfe_base_url}/course/{course_locator}/settings/advanced'
        if mfe_base_url:
            advanced_settings_url = course_mfe_url
    return advanced_settings_url


def get_grading_url(course_locator) -> str:
    """
    Gets course authoring microfrontend URL for grading page view.
    """
    grading_url = None
    if use_new_grading_page(course_locator):
        mfe_base_url = get_course_authoring_url(course_locator)
        course_mfe_url = f'{mfe_base_url}/course/{course_locator}/settings/grading'
        if mfe_base_url:
            grading_url = course_mfe_url
    return grading_url


def get_course_team_url(course_locator) -> str:
    """
    Gets course authoring microfrontend URL for course team page view.
    """
    course_team_url = None
    if use_new_course_team_page(course_locator):
        mfe_base_url = get_course_authoring_url(course_locator)
        course_mfe_url = f'{mfe_base_url}/course/{course_locator}/course_team'
        if mfe_base_url:
            course_team_url = course_mfe_url
    return course_team_url


def get_updates_url(course_locator) -> str:
    """
    Gets course authoring microfrontend URL for updates page view.
    """
    updates_url = None
    if use_new_updates_page(course_locator):
        mfe_base_url = get_course_authoring_url(course_locator)
        course_mfe_url = f'{mfe_base_url}/course/{course_locator}/course_info'
        if mfe_base_url:
            updates_url = course_mfe_url
    return updates_url


def get_import_url(course_locator) -> str:
    """
    Gets course authoring microfrontend URL for import page view.
    """
    import_url = None
    if use_new_import_page(course_locator):
        mfe_base_url = get_course_authoring_url(course_locator)
        course_mfe_url = f'{mfe_base_url}/course/{course_locator}/import'
        if mfe_base_url:
            import_url = course_mfe_url
    return import_url


def get_export_url(course_locator) -> str:
    """
    Gets course authoring microfrontend URL for export page view.
    """
    export_url = None
    if use_new_export_page(course_locator):
        mfe_base_url = get_course_authoring_url(course_locator)
        course_mfe_url = f'{mfe_base_url}/course/{course_locator}/export'
        if mfe_base_url:
            export_url = course_mfe_url
    return export_url


def get_optimizer_url(course_locator) -> str:
    """
    Gets course authoring microfrontend URL for optimizer page view.
    """
    optimizer_url = None
    if enable_course_optimizer(course_locator):
        mfe_base_url = get_course_authoring_url(course_locator)
        course_mfe_url = f'{mfe_base_url}/course/{course_locator}/optimizer'
        if mfe_base_url:
            optimizer_url = course_mfe_url
    return optimizer_url


def get_files_uploads_url(course_locator) -> str:
    """
    Gets course authoring microfrontend URL for files and uploads page view.
    """
    files_uploads_url = None
    mfe_base_url = get_course_authoring_url(course_locator)
    course_mfe_url = f'{mfe_base_url}/course/{course_locator}/assets'
    if mfe_base_url:
        files_uploads_url = course_mfe_url
    return files_uploads_url


def get_video_uploads_url(course_locator) -> str:
    """
    Gets course authoring microfrontend URL for files and uploads page view.
    """
    video_uploads_url = None
    if use_new_video_uploads_page(course_locator):
        mfe_base_url = get_course_authoring_url(course_locator)
        course_mfe_url = f'{mfe_base_url}/course/{course_locator}/videos/'
        if mfe_base_url:
            video_uploads_url = course_mfe_url
    return video_uploads_url


def get_course_outline_url(course_locator, block_to_show=None) -> str:
    """
    Gets course authoring microfrontend URL for course oultine page view.
    """
    course_outline_url = None
    mfe_base_url = get_course_authoring_url(course_locator)
    course_mfe_url = f'{mfe_base_url}/course/{course_locator}'
    if block_to_show:
        course_mfe_url += f'?show={quote_plus(block_to_show)}'
    if mfe_base_url:
        course_outline_url = course_mfe_url
    return course_outline_url


def get_library_content_picker_url(course_locator) -> str:
    """
    Gets course authoring microfrontend library content picker URL for the given parent block.
    """
    content_picker_url = None
    if libraries_v2_enabled():
        mfe_base_url = get_course_authoring_url(course_locator)
        content_picker_url = f'{mfe_base_url}/component-picker?variant=published'

    return content_picker_url


def get_unit_url(course_locator, unit_locator) -> str:
    """
    Gets course authoring microfrontend URL for unit page view.
    """
    unit_url = None
    if use_new_unit_page(course_locator):
        mfe_base_url = get_course_authoring_url(course_locator)
        course_mfe_url = f'{mfe_base_url}/course/{course_locator}/container/{unit_locator}'
        if mfe_base_url:
            unit_url = course_mfe_url
    return unit_url


def get_certificates_url(course_locator) -> str:
    """
    Gets course authoring microfrontend URL for certificates page view.
    """
    certificates_url = None
    if use_new_certificates_page(course_locator):
        mfe_base_url = get_course_authoring_url(course_locator)
        course_mfe_url = f'{mfe_base_url}/course/{course_locator}/certificates'
        if mfe_base_url:
            certificates_url = course_mfe_url
    return certificates_url


def get_textbooks_url(course_locator) -> str:
    """
    Gets course authoring microfrontend URL for textbooks page view.
    """
    textbooks_url = None
    if use_new_textbooks_page(course_locator):
        mfe_base_url = get_course_authoring_url(course_locator)
        course_mfe_url = f'{mfe_base_url}/course/{course_locator}/textbooks'
        if mfe_base_url:
            textbooks_url = course_mfe_url
    return textbooks_url


def get_group_configurations_url(course_locator) -> str:
    """
    Gets course authoring microfrontend URL for group configurations page view.
    """
    group_configurations_url = None
    if use_new_group_configurations_page(course_locator):
        mfe_base_url = get_course_authoring_url(course_locator)
        course_mfe_url = f'{mfe_base_url}/course/{course_locator}/group_configurations'
        if mfe_base_url:
            group_configurations_url = course_mfe_url
    return group_configurations_url


def get_custom_pages_url(course_locator) -> str:
    """
    Gets course authoring microfrontend URL for custom pages view.
    """
    custom_pages_url = None
    if use_new_custom_pages(course_locator):
        mfe_base_url = get_course_authoring_url(course_locator)
        course_mfe_url = f'{mfe_base_url}/course/{course_locator}/custom-pages'
        if mfe_base_url:
            custom_pages_url = course_mfe_url
    return custom_pages_url


def get_course_libraries_url(course_locator) -> str:
    """
    Gets course authoring microfrontend URL for custom pages view.
    """
    url = None
    if libraries_v2_enabled():
        mfe_base_url = get_course_authoring_url(course_locator)
        if mfe_base_url:
            url = f'{mfe_base_url}/course/{course_locator}/libraries'
    return url


def get_taxonomy_list_url() -> str | None:
    """
    Gets course authoring microfrontend URL for taxonomy list page view.
    """
    if is_tagging_feature_disabled():
        return None

    mfe_base_url = settings.COURSE_AUTHORING_MICROFRONTEND_URL

    if not mfe_base_url:
        return None

    return f'{mfe_base_url}/taxonomies'


def get_taxonomy_tags_widget_url(course_locator=None) -> str | None:
    """
    Gets course authoring microfrontend URL for taxonomy tags drawer widget view.

    The `content_id` needs to be appended to the end of the URL when using it.
    """
    if is_tagging_feature_disabled():
        return None

    if course_locator:
        mfe_base_url = get_course_authoring_url(course_locator)
    else:
        mfe_base_url = settings.COURSE_AUTHORING_MICROFRONTEND_URL

    if not mfe_base_url:
        return None

    return f'{mfe_base_url}/tagging/components/widget/'


def course_import_olx_validation_is_enabled():
    """
    Check if course olx validation is enabled on course import.
    """
    return settings.FEATURES.get('ENABLE_COURSE_OLX_VALIDATION', False)


# pylint: disable=invalid-name
def is_currently_visible_to_students(xblock):
    """
    Returns true if there is a published version of the xblock that is currently visible to students.
    This means that it has a release date in the past, and the xblock has not been set to staff only.
    """

    try:
        published = modulestore().get_item(xblock.location, revision=ModuleStoreEnum.RevisionOption.published_only)
    # If there's no published version then the xblock is clearly not visible
    except ItemNotFoundError:
        return False

    # If visible_to_staff_only is True, this xblock is not visible to students regardless of start date.
    if published.visible_to_staff_only:
        return False

    # Check start date
    if 'detached' not in published._class_tags and published.start is not None:  # lint-amnesty, pylint: disable=protected-access
        return datetime.now(UTC) > published.start

    # No start date, so it's always visible
    return True


def has_children_visible_to_specific_partition_groups(xblock):
    """
    Returns True if this xblock has children that are limited to specific user partition groups.
    Note that this method is not recursive (it does not check grandchildren).
    """
    if not xblock.has_children:
        return False

    for child in xblock.get_children():
        if is_visible_to_specific_partition_groups(child):
            return True

    return False


def is_visible_to_specific_partition_groups(xblock):
    """
    Returns True if this xblock has visibility limited to specific user partition groups.
    """
    if not xblock.group_access:
        return False

    for partition in get_user_partition_info(xblock):
        if any(g["selected"] for g in partition["groups"]):
            return True

    return False


def find_release_date_source(xblock):
    """
    Finds the ancestor of xblock that set its release date.
    """

    # Stop searching at the section level
    if xblock.category == 'chapter':
        return xblock

    parent_location = modulestore().get_parent_location(xblock.location,
                                                        revision=ModuleStoreEnum.RevisionOption.draft_preferred)
    # Orphaned xblocks set their own release date
    if not parent_location:
        return xblock

    parent = modulestore().get_item(parent_location)
    if parent.start != xblock.start:
        return xblock
    else:
        return find_release_date_source(parent)


def find_staff_lock_source(xblock):
    """
    Returns the xblock responsible for setting this xblock's staff lock, or None if the xblock is not staff locked.
    If this xblock is explicitly locked, return it, otherwise find the ancestor which sets this xblock's staff lock.
    """

    # Stop searching if this xblock has explicitly set its own staff lock
    if xblock.fields['visible_to_staff_only'].is_set_on(xblock):
        return xblock

    # Stop searching at the section level
    if xblock.category == 'chapter':
        return None

    parent_location = modulestore().get_parent_location(xblock.location,
                                                        revision=ModuleStoreEnum.RevisionOption.draft_preferred)
    # Orphaned xblocks set their own staff lock
    if not parent_location:
        return None

    parent = modulestore().get_item(parent_location)
    return find_staff_lock_source(parent)


def ancestor_has_staff_lock(xblock, parent_xblock=None):
    """
    Returns True iff one of xblock's ancestors has staff lock.
    Can avoid mongo query by passing in parent_xblock.
    """
    if parent_xblock is None:
        parent_location = modulestore().get_parent_location(xblock.location,
                                                            revision=ModuleStoreEnum.RevisionOption.draft_preferred)
        if not parent_location:
            return False
        parent_xblock = modulestore().get_item(parent_location)
    return parent_xblock.visible_to_staff_only


def get_sequence_usage_keys(course):
    """
    Extracts a list of 'subsections' usage_keys
    """
    return [str(subsection.location)
            for section in course.get_children()
            for subsection in section.get_children()]


def create_course_info_usage_key(course, section_key):
    """
    Returns the usage key for the specified section's course info block.
    """
    return course.id.make_usage_key('course_info', section_key)


def reverse_url(handler_name, key_name=None, key_value=None, kwargs=None):
    """
    Creates the URL for the given handler.
    The optional key_name and key_value are passed in as kwargs to the handler.
    """
    kwargs_for_reverse = {key_name: str(key_value)} if key_name else None
    if kwargs:
        kwargs_for_reverse.update(kwargs)
    return reverse(handler_name, kwargs=kwargs_for_reverse)


def reverse_course_url(handler_name, course_key, kwargs=None):
    """
    Creates the URL for handlers that use course_keys as URL parameters.
    """
    return reverse_url(handler_name, 'course_key_string', course_key, kwargs)


def reverse_library_url(handler_name, library_key, kwargs=None):
    """
    Creates the URL for handlers that use library_keys as URL parameters.
    """
    return reverse_url(handler_name, 'library_key_string', library_key, kwargs)


def reverse_usage_url(handler_name, usage_key, kwargs=None):
    """
    Creates the URL for handlers that use usage_keys as URL parameters.
    """
    return reverse_url(handler_name, 'usage_key_string', usage_key, kwargs)


def get_split_group_display_name(xblock, course):
    """
    Returns group name if an xblock is found in user partition groups that are suitable for the split_test block.

    Arguments:
        xblock (XBlock): The courseware component.
        course (XBlock): The course block.

    Returns:
        group name (String): Group name of the matching group xblock.
    """
    for user_partition in get_user_partition_info(xblock, schemes=['random'], course=course):
        for group in user_partition['groups']:
            if 'Group ID {group_id}'.format(group_id=group['id']) == xblock.display_name_with_default:
                return group['name']


def get_user_partition_info(xblock, schemes=None, course=None):
    """
    Retrieve user partition information for an XBlock for display in editors.

    * If a partition has been disabled, it will be excluded from the results.

    * If a group within a partition is referenced by the XBlock, but the group has been deleted,
      the group will be marked as deleted in the results.

    Arguments:
        xblock (XBlock): The courseware component being edited.

    Keyword Arguments:
        schemes (iterable of str): If provided, filter partitions to include only
            schemes with the provided names.

        course (XBlock): The course block.  If provided, uses this to look up the user partitions
            instead of loading the course.  This is useful if we're calling this function multiple
            times for the same course want to minimize queries to the modulestore.

    Returns: list

    Example Usage:
    >>> get_user_partition_info(xblock, schemes=["cohort", "verification"])
    [
        {
            "id": 12345,
            "name": "Cohorts"
            "scheme": "cohort",
            "groups": [
                {
                    "id": 7890,
                    "name": "Foo",
                    "selected": True,
                    "deleted": False,
                }
            ]
        },
        {
            "id": 7292,
            "name": "Midterm A",
            "scheme": "verification",
            "groups": [
                {
                    "id": 1,
                    "name": "Completed verification at Midterm A",
                    "selected": False,
                    "deleted": False
                },
                {
                    "id": 0,
                    "name": "Did not complete verification at Midterm A",
                    "selected": False,
                    "deleted": False,
                }
            ]
        }
    ]

    """
    course = course or modulestore().get_course(xblock.location.course_key)

    if course is None:
        log.warning(
            "Could not find course %s to retrieve user partition information",
            xblock.location.course_key
        )
        return []

    if schemes is not None:
        schemes = set(schemes)

    partitions = []
    for p in sorted(get_all_partitions_for_course(course, active_only=True), key=lambda p: p.name):

        # Exclude disabled partitions, partitions with no groups defined
        # The exception to this case is when there is a selected group within that partition, which means there is
        # a deleted group
        # Also filter by scheme name if there's a filter defined.
        selected_groups = set(xblock.group_access.get(p.id, []) or [])
        if (p.groups or selected_groups) and (schemes is None or p.scheme.name in schemes):

            # First, add groups defined by the partition
            groups = []
            for g in p.groups:
                # Falsey group access for a partition mean that all groups
                # are selected.  In the UI, though, we don't show the particular
                # groups selected, since there's a separate option for "all users".
                groups.append({
                    "id": g.id,
                    "name": g.name,
                    "selected": g.id in selected_groups,
                    "deleted": False,
                })

            # Next, add any groups set on the XBlock that have been deleted
            all_groups = {g.id for g in p.groups}
            missing_group_ids = selected_groups - all_groups
            for gid in missing_group_ids:
                groups.append({
                    "id": gid,
                    "name": _("Deleted Group"),
                    "selected": True,
                    "deleted": True,
                })

            # Put together the entire partition dictionary
            partitions.append({
                "id": p.id,
                "name": str(p.name),  # Convert into a string in case ugettext_lazy was used
                "scheme": p.scheme.name,
                "groups": groups,
            })

    return partitions


def get_visibility_partition_info(xblock, course=None):
    """
    Retrieve user partition information for the component visibility editor.

    This pre-processes partition information to simplify the template.

    Arguments:
        xblock (XBlock): The component being edited.

        course (XBlock): The course block.  If provided, uses this to look up the user partitions
            instead of loading the course.  This is useful if we're calling this function multiple
            times for the same course want to minimize queries to the modulestore.

    Returns: dict

    """
    selectable_partitions = []
    # We wish to display enrollment partitions before cohort partitions.
    enrollment_user_partitions = get_user_partition_info(xblock, schemes=["enrollment_track"], course=course)

    # For enrollment partitions, we only show them if there is a selected group or
    # or if the number of groups > 1.
    for partition in enrollment_user_partitions:
        if len(partition["groups"]) > 1 or any(group["selected"] for group in partition["groups"]):
            selectable_partitions.append(partition)

    team_user_partitions = get_user_partition_info(xblock, schemes=["team"], course=course)
    selectable_partitions += team_user_partitions

    course_key = xblock.scope_ids.usage_id.course_key
    is_library = isinstance(course_key, LibraryLocator)
    if not is_library and ContentTypeGatingConfig.current(course_key=course_key).studio_override_enabled:
        selectable_partitions += get_user_partition_info(xblock, schemes=[CONTENT_TYPE_GATING_SCHEME], course=course)

    # Now add the cohort user partitions.
    selectable_partitions = selectable_partitions + get_user_partition_info(xblock, schemes=["cohort"], course=course)

    # Find the first partition with a selected group. That will be the one initially enabled in the dialog
    # (if the course has only been added in Studio, only one partition should have a selected group).
    selected_partition_index = -1

    # At the same time, build up all the selected groups as they are displayed in the dialog title.
    selected_groups_label = ''

    for index, partition in enumerate(selectable_partitions):
        for group in partition["groups"]:
            if group["selected"]:
                if len(selected_groups_label) == 0:
                    selected_groups_label = group['name']
                else:
                    # Translators: This is building up a list of groups. It is marked for translation because of the
                    # comma, which is used as a separator between each group.
                    selected_groups_label = _('{previous_groups}, {current_group}').format(
                        previous_groups=selected_groups_label,
                        current_group=group['name']
                    )
                if selected_partition_index == -1:
                    selected_partition_index = index

    return {
        "selectable_partitions": selectable_partitions,
        "selected_partition_index": selected_partition_index,
        "selected_groups_label": selected_groups_label,
    }


def get_xblock_aside_instance(usage_key):
    """
    Returns: aside instance of a aside xblock
    :param usage_key: Usage key of aside xblock
    """
    try:
        xblock = modulestore().get_item(usage_key.usage_key)
        for aside in xblock.runtime.get_asides(xblock):
            if aside.scope_ids.block_type == usage_key.aside_type:
                return aside
    except ItemNotFoundError:
        log.warning('Unable to load item %s', usage_key.usage_key)


def is_self_paced(course):
    """
    Returns True if course is self-paced, False otherwise.
    """
    return course and course.self_paced


def get_sibling_urls(subsection, unit_location):    # pylint: disable=too-many-statements
    """
    Given a subsection, returns the urls for the next and previous units.

    (the first unit of the next subsection or section, and
    the last unit of the previous subsection/section)
    """
    def get_unit_location(direction):
        """
        Returns the desired location of the adjacent unit in a subsection.
        """
        unit_index = subsection.children.index(unit_location)
        try:
            if direction == 'previous':
                if unit_index > 0:
                    location = subsection.children[unit_index - 1]
                else:
                    location = None
            else:
                location = subsection.children[unit_index + 1]
            return location
        except IndexError:
            return None

    def get_subsections_in_section():
        """
        Returns subsections present in a section.
        """
        try:
            section_subsections = section.get_children()
            return section_subsections
        except AttributeError:
            log.error("URL Retrieval Error: subsection {subsection} included in section {section}".format(
                section=section.location,
                subsection=subsection.location
            ))
            return None

    def get_sections_in_course():
        """
        Returns sections present in course.
        """
        try:
            section_subsections = section.get_parent().get_children()
            return section_subsections
        except AttributeError:
            log.error("URL Retrieval Error: In section {section} in course".format(
                section=section.location,
            ))
            return None

    def get_subsection_location(section_subsections, current_subsection, direction):
        """
        Returns the desired location of the adjacent subsections in a section.
        """
        location = None
        subsection_index = section_subsections.index(next(s for s in subsections if s.location ==
                                                          current_subsection.location))
        try:
            if direction == 'previous':
                if subsection_index > 0:
                    prev_subsection = subsections[subsection_index - 1]
                    location = prev_subsection.get_children()[-1].location
            else:
                next_subsection = subsections[subsection_index + 1]
                location = next_subsection.get_children()[0].location
            return location
        except IndexError:
            return None

    def get_section_location(course_sections, current_section, direction):
        """
        Returns the desired location of the adjacent sections in a course.
        """
        location = None
        section_index = course_sections.index(next(s for s in sections if s.location == current_section.location))
        try:
            if direction == 'previous':
                if section_index > 0:
                    prev_section = sections[section_index - 1]
                    location = prev_section.get_children()[-1].get_children()[-1].location
            else:
                next_section = sections[section_index + 1]
                location = next_section.get_children()[0].get_children()[0].location
            return location
        except IndexError:
            return None

    section = subsection.get_parent()
    prev_url = next_url = ''

    prev_loc = get_unit_location('previous')
    next_loc = get_unit_location('next')

    if not prev_loc:
        subsections = get_subsections_in_section()
        if subsections:
            prev_loc = get_subsection_location(subsections, subsection, 'previous')

    if not next_loc:
        subsections = get_subsections_in_section()
        if subsections:
            next_loc = get_subsection_location(subsections, subsection, 'next')

    if not prev_loc:
        sections = get_sections_in_course()
        if sections:
            prev_loc = get_section_location(sections, section, 'previous')

    if not next_loc:
        sections = get_sections_in_course()
        if sections:
            next_loc = get_section_location(sections, section, 'next')

    if prev_loc:
        prev_url = reverse_usage_url('container_handler', prev_loc)
    if next_loc:
        next_url = reverse_usage_url('container_handler', next_loc)
    return prev_url, next_url


def determine_label(display_name, block_type):
    """
    Returns the name of the xblock to display in studio.
    Please see TNL-9838.
    """
    if display_name in {"", None}:
        if block_type == 'html':
            return _("Text")
        else:
            return block_type
    else:
        return display_name


@contextmanager
def translation_language(language):
    """Context manager to override the translation language for the scope
    of the following block. Has no effect if language is None.
    """
    if language:
        previous = translation.get_language()
        translation.activate(language)
        try:
            yield
        finally:
            translation.activate(previous)
    else:
        yield


def get_subsections_by_assignment_type(course_key):
    """
    Construct a dictionary mapping each found assignment type in the course
    to a list of dictionaries with the display name of the subsection and
    the display name of the section they are in
    """
    subsections_by_assignment_type = defaultdict(list)

    with modulestore().bulk_operations(course_key):
        course = modulestore().get_course(course_key, depth=3)
        sections = course.get_children()
        for section in sections:
            subsections = section.get_children()
            for subsection in subsections:
                if subsection.format:
                    subsections_by_assignment_type[subsection.format].append(
                        f'{section.display_name} - {subsection.display_name}'
                    )
    return subsections_by_assignment_type


def update_course_discussions_settings(course):
    """
    Updates course provider_type when new course is created
    """
    provider = DiscussionsConfiguration.get(context_key=course.id).provider_type
    store = modulestore()
    course.discussions_settings['provider_type'] = provider
    store.update_item(course, course.published_by)


def duplicate_block(
    parent_usage_key,
    duplicate_source_usage_key,
    user,
    dest_usage_key=None,
    display_name=None,
    shallow=False,
    is_child=False
):
    """
    Duplicate an existing xblock as a child of the supplied parent_usage_key. You can
    optionally specify what usage key the new duplicate block will use via dest_usage_key.

    If shallow is True, does not copy children. Otherwise, this function calls itself
    recursively, and will set the is_child flag to True when dealing with recursed child
    blocks.
    """
    store = modulestore()
    with store.bulk_operations(duplicate_source_usage_key.course_key):
        source_item = store.get_item(duplicate_source_usage_key)
        if not dest_usage_key:
            # Change the blockID to be unique.
            dest_usage_key = source_item.location.replace(name=uuid4().hex)

        category = dest_usage_key.block_type

        duplicate_metadata, asides_to_create = gather_block_attributes(
            source_item, display_name=display_name, is_child=is_child,
        )

        dest_block = store.create_item(
            user.id,
            dest_usage_key.course_key,
            dest_usage_key.block_type,
            block_id=dest_usage_key.block_id,
            definition_data=source_item.get_explicitly_set_fields_by_scope(Scope.content),
            metadata=duplicate_metadata,
            runtime=source_item.runtime,
            asides=asides_to_create
        )

        children_handled = False

        if hasattr(dest_block, 'studio_post_duplicate'):
            # Allow an XBlock to do anything fancy it may need to when duplicated from another block.
            # These blocks may handle their own children or parenting if needed. Let them return booleans to
            # let us know if we need to handle these or not.
            load_services_for_studio(dest_block.runtime, user)
            children_handled = dest_block.studio_post_duplicate(store, source_item)

        # Children are not automatically copied over (and not all xblocks have a 'children' attribute).
        # Because DAGs are not fully supported, we need to actually duplicate each child as well.
        if source_item.has_children and not shallow and not children_handled:
            dest_block.children = dest_block.children or []
            for child in source_item.children:
                dupe = duplicate_block(dest_block.location, child, user=user, is_child=True)
                if dupe not in dest_block.children:  # _duplicate_block may add the child for us.
                    dest_block.children.append(dupe)
            store.update_item(dest_block, user.id)

        # pylint: disable=protected-access
        if 'detached' not in source_item.runtime.load_block_type(category)._class_tags:
            parent = store.get_item(parent_usage_key)
            # If source was already a child of the parent, add duplicate immediately afterward.
            # Otherwise, add child to end.
            if source_item.location in parent.children:
                source_index = parent.children.index(source_item.location)
                parent.children.insert(source_index + 1, dest_block.location)
            else:
                parent.children.append(dest_block.location)
            store.update_item(parent, user.id)

        # .. event_implemented_name: XBLOCK_DUPLICATED
        # .. event_type: org.openedx.content_authoring.xblock.duplicated.v1
        XBLOCK_DUPLICATED.send_event(
            time=datetime.now(timezone.utc),
            xblock_info=DuplicatedXBlockData(
                usage_key=dest_block.location,
                block_type=dest_block.location.block_type,
                source_usage_key=duplicate_source_usage_key,
            )
        )

        return dest_block.location


def update_from_source(*, source_block, destination_block, user_id):
    """
    Update a block to have all the settings and attributes of another source.

    Copies over all attributes and settings of a source block to a destination
    block. Blocks must be the same type. This function does not modify or duplicate
    children.

    This function is useful when a block, originally copied from a source block, drifts
    and needs to be updated to match the original.

    The modulestore function copy_from_template will copy a block's children recursively,
    replacing the target block's children. It does not, however, update any of the target
    block's settings. copy_from_template, then, is useful for cases like the Library
    Content Block, where the children are the same across all instances, but the settings
    may differ.

    By contrast, for cases where we're copying a block that has drifted from its source,
    we need to update the target block's settings, but we don't want to replace its children,
    or, at least, not only replace its children. update_from_source is useful for these cases.

    This function is meant to be imported by pluggable django apps looking to manage duplicated
    sections of a course. It is placed here for lack of a more appropriate location, since this
    code has not yet been brought up to the standards in OEP-45.
    """
    duplicate_metadata, asides = gather_block_attributes(source_block, display_name=source_block.display_name)
    for key, value in duplicate_metadata.items():
        setattr(destination_block, key, value)
    for key, value in source_block.get_explicitly_set_fields_by_scope(Scope.content).items():
        setattr(destination_block, key, value)
    modulestore().update_item(
        destination_block,
        user_id,
        metadata=duplicate_metadata,
        asides=asides,
    )


def gather_block_attributes(source_item, display_name=None, is_child=False):
    """
    Gather all the attributes of the source block that need to be copied over to a new or updated block.
    """
    # Update the display name to indicate this is a duplicate (unless display name provided).
    # Can't use own_metadata(), b/c it converts data for JSON serialization -
    # not suitable for setting metadata of the new block
    duplicate_metadata = {}
    for field in source_item.fields.values():
        if field.scope == Scope.settings and field.is_set_on(source_item):
            duplicate_metadata[field.name] = field.read_from(source_item)

    if is_child:
        display_name = display_name or source_item.display_name or source_item.category

    if display_name is not None:
        duplicate_metadata['display_name'] = display_name
    else:
        if source_item.display_name is None:
            duplicate_metadata['display_name'] = _("Duplicate of {0}").format(source_item.category)
        else:
            duplicate_metadata['display_name'] = _("Duplicate of '{0}'").format(source_item.display_name)

    asides_to_create = []
    for aside in source_item.runtime.get_asides(source_item):
        for field in aside.fields.values():
            if field.scope in (Scope.settings, Scope.content,) and field.is_set_on(aside):
                asides_to_create.append(aside)
                break

    for aside in asides_to_create:
        for field in aside.fields.values():
            if field.scope not in (Scope.settings, Scope.content,):
                field.delete_from(aside)
    return duplicate_metadata, asides_to_create


def load_services_for_studio(runtime, user):
    """
    Function to set some required services used for XBlock edits and studio_view.
    (i.e. whenever we're not loading _prepare_runtime_for_preview.) This is required to make information
    about the current user (especially permissions) available via services as needed.
    """
    services = {
        "user": DjangoXBlockUserService(user),
        "studio_user_permissions": StudioPermissionsService(user),
        "mako": MakoService(),
        "settings": SettingsService(),
        "lti-configuration": ConfigurationService(CourseAllowPIISharingInLTIFlag),
        "teams_configuration": TeamsConfigurationService(),
        "library_tools": LegacyLibraryToolsService(modulestore(), user.id)
    }

    runtime._services.update(services)  # lint-amnesty, pylint: disable=protected-access


def update_course_details(request, course_key, payload, course_block):
    """
    Utility function used to update course details. It is used for both DRF and legacy Django views.

    Args:
        request (WSGIRequest): Django HTTP request object
        course_key (CourseLocator): The course run key
        payload (dict): Dictionary of course run settings
        course_block (CourseBlockWithMixins): A course run instance

    Returns:
        None
    """

    from .views.entrance_exam import create_entrance_exam, delete_entrance_exam, update_entrance_exam

    # if pre-requisite course feature is enabled set pre-requisite course
    if is_prerequisite_courses_enabled():
        prerequisite_course_keys = payload.get('pre_requisite_courses', [])
        if prerequisite_course_keys:
            if not all(is_valid_course_key(course_key) for course_key in prerequisite_course_keys):
                raise ValidationError(_("Invalid prerequisite course key"))
            set_prerequisite_courses(course_key, prerequisite_course_keys)
        else:
            # None is chosen, so remove the course prerequisites
            course_milestones = milestones_api.get_course_milestones(
                course_key=course_key,
                relationship="requires",
            )
            for milestone in course_milestones:
                entrance_exam_namespace = generate_milestone_namespace(
                    get_namespace_choices().get('ENTRANCE_EXAM'),
                    course_key
                )
                if milestone["namespace"] != entrance_exam_namespace:
                    remove_prerequisite_course(course_key, milestone)

    # If the entrance exams feature has been enabled, we'll need to check for some feature-specific settings and handle
    # them accordingly. We have to be careful that we're only executing the following logic if we actually need to
    # create or delete an entrance exam from the specified course
    if core_toggles.ENTRANCE_EXAMS.is_enabled():
        course_entrance_exam_present = course_block.entrance_exam_enabled
        entrance_exam_enabled = payload.get('entrance_exam_enabled', '') == 'true'
        ee_min_score_pct = payload.get('entrance_exam_minimum_score_pct', None)
        # If the entrance exam box on the settings screen has been checked...
        if entrance_exam_enabled:
            # Load the default minimum score threshold from settings, then try to override it
            entrance_exam_minimum_score_pct = float(settings.ENTRANCE_EXAM_MIN_SCORE_PCT)
            if ee_min_score_pct:
                entrance_exam_minimum_score_pct = float(ee_min_score_pct)
            if entrance_exam_minimum_score_pct.is_integer():
                entrance_exam_minimum_score_pct = entrance_exam_minimum_score_pct / 100
            # If there's already an entrance exam defined, we'll update the existing one
            if course_entrance_exam_present:
                exam_data = {
                    'entrance_exam_minimum_score_pct': entrance_exam_minimum_score_pct
                }
                update_entrance_exam(request, course_key, exam_data)
            # If there's no entrance exam defined, we'll create a new one
            else:
                create_entrance_exam(request, course_key, entrance_exam_minimum_score_pct)
        # If the entrance exam box on the settings screen has been unchecked, and the course has an entrance exam
        # attached...
        elif not entrance_exam_enabled and course_entrance_exam_present:
            delete_entrance_exam(request, course_key)

    # Fix any potential issues with the display behavior and availability date of certificates before saving the update.
    # A self-paced course run should *never* have a display behavior of anything other than "Immediately Upon Passing"
    # ("early_no_info") and does not support having a certificate available date. We are aware of an issue with the
    # legacy Django template-based frontend where bad data is allowed to creep into the system, which can cause
    # downstream services (e.g. the Credentials IDA) to go haywire. This bad data is most often seen when a course run
    # is updated from instructor-paced to self-paced (self_paced == True in our JSON payload), so we check and fix
    # during these updates. Otherwise, the legacy UI seems to do the right thing.
    if "self_paced" in payload and payload["self_paced"]:
        payload["certificate_available_date"] = None
        payload["certificates_display_behavior"] = CertificatesDisplayBehaviors.EARLY_NO_INFO

    # Perform the normal update workflow for the CourseDetails model
    return CourseDetails.update_from_json(course_key, payload, request.user)


def get_course_settings(request, course_key, course_block):
    """
    Utils is used to get context of course settings.
    It is used for both DRF and django views.
    """

    from .views.course import get_courses_accessible_to_user, _process_courses_list

    credit_eligibility_enabled = settings.FEATURES.get('ENABLE_CREDIT_ELIGIBILITY', False)
    upload_asset_url = reverse_course_url('assets_handler', course_key)

    # see if the ORG of this course can be attributed to a defined configuration . In that case, the
    # course about page should be editable in Studio
    publisher_enabled = configuration_helpers.get_value_for_org(
        course_block.location.org,
        'ENABLE_PUBLISHER',
        settings.FEATURES.get('ENABLE_PUBLISHER', False)
    )
    marketing_enabled = configuration_helpers.get_value_for_org(
        course_block.location.org,
        'ENABLE_MKTG_SITE',
        settings.FEATURES.get('ENABLE_MKTG_SITE', False)
    )
    enable_extended_course_details = configuration_helpers.get_value_for_org(
        course_block.location.org,
        'ENABLE_EXTENDED_COURSE_DETAILS',
        settings.FEATURES.get('ENABLE_EXTENDED_COURSE_DETAILS', False)
    )

    about_page_editable = not publisher_enabled
    enrollment_end_editable = GlobalStaff().has_user(request.user) or not publisher_enabled
    short_description_editable = configuration_helpers.get_value_for_org(
        course_block.location.org,
        'EDITABLE_SHORT_DESCRIPTION',
        settings.FEATURES.get('EDITABLE_SHORT_DESCRIPTION', True)
    )
    sidebar_html_enabled = ENABLE_COURSE_ABOUT_SIDEBAR_HTML.is_enabled()

    verified_mode = CourseMode.verified_mode_for_course(course_key, include_expired=True)
    upgrade_deadline = (verified_mode and verified_mode.expiration_datetime and
                        verified_mode.expiration_datetime.isoformat())
    settings_context = {
        'context_course': course_block,
        'course_locator': course_key,
        'lms_link_for_about_page': get_link_for_about_page(course_block),
        'course_image_url': course_image_url(course_block, 'course_image'),
        'banner_image_url': course_image_url(course_block, 'banner_image'),
        'video_thumbnail_image_url': course_image_url(course_block, 'video_thumbnail_image'),
        'details_url': reverse_course_url('settings_handler', course_key),
        'about_page_editable': about_page_editable,
        'marketing_enabled': marketing_enabled,
        'short_description_editable': short_description_editable,
        'sidebar_html_enabled': sidebar_html_enabled,
        'upload_asset_url': upload_asset_url,
        'course_handler_url': reverse_course_url('course_handler', course_key),
        'language_options': settings.ALL_LANGUAGES,
        'credit_eligibility_enabled': credit_eligibility_enabled,
        'is_credit_course': False,
        'show_min_grade_warning': False,
        'enrollment_end_editable': enrollment_end_editable,
        'is_prerequisite_courses_enabled': is_prerequisite_courses_enabled(),
        'is_entrance_exams_enabled': core_toggles.ENTRANCE_EXAMS.is_enabled(),
        'enable_extended_course_details': enable_extended_course_details,
        'upgrade_deadline': upgrade_deadline,
        'mfe_proctored_exam_settings_url': get_proctored_exam_settings_url(course_block.id),
    }
    if is_prerequisite_courses_enabled():
        courses, in_process_course_actions = get_courses_accessible_to_user(request)
        # exclude current course from the list of available courses
        courses = [course for course in courses if course.id != course_key]
        if courses:
            courses, __ = _process_courses_list(courses, in_process_course_actions)
        settings_context.update({'possible_pre_requisite_courses': courses})

    if credit_eligibility_enabled:
        if is_credit_course(course_key):
            # get and all credit eligibility requirements
            credit_requirements = get_credit_requirements(course_key)
            # pair together requirements with same 'namespace' values
            paired_requirements = {}
            for requirement in credit_requirements:
                namespace = requirement.pop("namespace")
                paired_requirements.setdefault(namespace, []).append(requirement)

            # if 'minimum_grade_credit' of a course is not set or 0 then
            # show warning message to course author.
            show_min_grade_warning = False if course_block.minimum_grade_credit > 0 else True  # lint-amnesty, pylint: disable=simplifiable-if-expression
            settings_context.update(
                {
                    'is_credit_course': True,
                    'credit_requirements': paired_requirements,
                    'show_min_grade_warning': show_min_grade_warning,
                }
            )

    return settings_context


def get_course_team(auth_user, course_key, user_perms):
    """
    Utils is used to get context of all CMS users who are editors for the specified course.
    It is used for both DRF and django views.
    """

    from cms.djangoapps.contentstore.views.user import user_with_role

    course_block = modulestore().get_course(course_key)
    instructors = set(CourseInstructorRole(course_key).users_with_role())
    # the page only lists staff and assumes they're a superset of instructors. Do a union to ensure.
    staff = set(CourseStaffRole(course_key).users_with_role()).union(instructors)

    formatted_users = []
    for user in instructors:
        formatted_users.append(user_with_role(user, 'instructor'))
    for user in staff - instructors:
        formatted_users.append(user_with_role(user, 'staff'))

    course_team_context = {
        'context_course': course_block,
        'show_transfer_ownership_hint': auth_user in instructors and len(instructors) == 1,
        'users': formatted_users,
        'allow_actions': bool(user_perms & STUDIO_EDIT_ROLES),
    }

    return course_team_context


def get_course_grading(course_key):
    """
    Utils is used to get context of course grading.
    It is used for both DRF and django views.
    """

    course_block = modulestore().get_course(course_key)
    course_details = CourseGradingModel.fetch(course_key)
    course_assignment_lists = get_subsections_by_assignment_type(course_key)
    grading_context = {
        'context_course': course_block,
        'course_locator': course_key,
        'course_details': course_details,
        'grading_url': reverse_course_url('grading_handler', course_key),
        'is_credit_course': is_credit_course(course_key),
        'mfe_proctored_exam_settings_url': get_proctored_exam_settings_url(course_key),
        'course_assignment_lists': dict(course_assignment_lists),
        'default_grade_designations': settings.DEFAULT_GRADE_DESIGNATIONS
    }

    return grading_context


def get_help_urls():
    """
    Utils is used to get help tokens urls.
    """
    ini = HelpUrlExpert.the_one()
    ini.config = configparser.ConfigParser()
    ini.config.read(ini.ini_file_name)
    tokens = list(ini.config['pages'].keys())
    help_tokens = {token: HelpUrlExpert.the_one().url_for_token(token) for token in tokens}
    return help_tokens


def get_response_format(request):
    return request.GET.get('format') or request.POST.get('format') or 'html'


def request_response_format_is_json(request, response_format):
    return response_format == 'json' or 'application/json' in request.META.get('HTTP_ACCEPT', 'application/json')


def get_library_context(request, request_is_json=False):
    """
    Utils is used to get context of course home library tab. Returned in DRF view.
    """
    from cms.djangoapps.contentstore.views.course import (
        _accessible_libraries_iter,
        _get_course_creator_status,
        format_library_for_view,
        get_allowed_organizations,
        get_allowed_organizations_for_libraries,
        user_can_create_organizations,
    )
    from cms.djangoapps.contentstore.views.library import (
        user_can_view_create_library_button,
    )
    from openedx.core.djangoapps.content_libraries.api import (
        user_can_create_library,
    )

    is_migrated: bool | None  # None means: do not filter on is_migrated
    if (is_migrated_param := request.GET.get('is_migrated')) is not None:
        is_migrated = BooleanField().to_internal_value(is_migrated_param)
    else:
        is_migrated = None
    libraries = list(_accessible_libraries_iter(request.user) if libraries_v1_enabled() else [])
    migration_info: dict[LibraryLocator, ModulestoreMigration | None] = {
        lib.id: migrator_api.get_forwarding(lib.id)
        for lib in libraries
    }
    data = {
        'libraries': [
            format_library_for_view(
                lib,
                request,
                migration=migration_info[lib.id],
            )
            for lib in libraries
            if is_migrated is None or is_migrated == bool(migration_info[lib.id])
        ]
    }

    if not request_is_json:
        return {
            **data,
            'in_process_course_actions': [],
            'courses': [],
            'libraries_enabled': libraries_v1_enabled(),
            'show_new_library_button': user_can_view_create_library_button(request.user) and request.user.is_active,
            'show_new_library_v2_button': user_can_create_library(request.user),
            'user': request.user,
            'request_course_creator_url': reverse('request_course_creator'),
            'course_creator_status': _get_course_creator_status(request.user),
            'allow_unicode_course_id': settings.FEATURES.get('ALLOW_UNICODE_COURSE_ID', False),
            'archived_courses': True,
            'allow_course_reruns': settings.FEATURES.get('ALLOW_COURSE_RERUNS', True),
            'rerun_creator_status': GlobalStaff().has_user(request.user),
            'split_studio_home': split_library_view_on_dashboard(),
            'active_tab': 'libraries',
            'allowed_organizations_for_libraries': get_allowed_organizations_for_libraries(request.user),
            'allowed_organizations': get_allowed_organizations(request.user),
            'can_create_organizations': user_can_create_organizations(request.user),
        }

    return data


def get_course_context(request):
    """
    Utils is used to get context of course home library tab.
    It is used for both DRF and django views.
    """

    from cms.djangoapps.contentstore.views.course import (
        get_courses_accessible_to_user,
        _process_courses_list,
    )

    def format_in_process_course_view(uca):
        """
        Return a dict of the data which the view requires for each unsucceeded course
        """
        return {
            'display_name': uca.display_name,
            'course_key': str(uca.course_key),
            'org': uca.course_key.org,
            'number': uca.course_key.course,
            'run': uca.course_key.run,
            'is_failed': uca.state == CourseRerunUIStateManager.State.FAILED,
            'is_in_progress': uca.state == CourseRerunUIStateManager.State.IN_PROGRESS,
            'dismiss_link': reverse_course_url(
                'course_notifications_handler',
                uca.course_key,
                kwargs={
                    'action_state_id': uca.id,
                },
            ) if uca.state == CourseRerunUIStateManager.State.FAILED else ''
        }

    courses_iter, in_process_course_actions = get_courses_accessible_to_user(request)
    split_archived = settings.FEATURES.get('ENABLE_SEPARATE_ARCHIVED_COURSES', False)
    active_courses, archived_courses = _process_courses_list(courses_iter, in_process_course_actions, split_archived)
    in_process_course_actions = [format_in_process_course_view(uca) for uca in in_process_course_actions]
    return active_courses, archived_courses, in_process_course_actions


def get_course_context_v2(request):
    """Get context of the homepage course tab from the Studio Home."""

    # Importing here to avoid circular imports:
    # ImportError: cannot import name 'reverse_course_url' from partially initialized module
    # 'cms.djangoapps.contentstore.utils' (most likely due to a circular import)
    from cms.djangoapps.contentstore.views.course import (
        get_courses_accessible_to_user,
    )

    def format_in_process_course_view(uca):
        """
        Return a dict of the data which the view requires for each unsucceeded course.

        Args:
            uca: CourseRerunUIStateManager object.
        """
        return {
            'display_name': uca.display_name,
            'course_key': str(uca.course_key),
            'org': uca.course_key.org,
            'number': uca.course_key.course,
            'run': uca.course_key.run,
            'is_failed': uca.state == CourseRerunUIStateManager.State.FAILED,
            'is_in_progress': uca.state == CourseRerunUIStateManager.State.IN_PROGRESS,
            'dismiss_link': reverse_course_url(
                'course_notifications_handler',
                uca.course_key,
                kwargs={
                    'action_state_id': uca.id,
                },
            ) if uca.state == CourseRerunUIStateManager.State.FAILED else ''
        }

    courses_iter, in_process_course_actions = get_courses_accessible_to_user(request)
    in_process_course_actions = [format_in_process_course_view(uca) for uca in in_process_course_actions]
    return courses_iter, in_process_course_actions


def get_home_context(request, no_course=False):
    """
    Utils is used to get context of course home. Returned by DRF view.
    """

    from cms.djangoapps.contentstore.views.course import (
        get_allowed_organizations,
        get_allowed_organizations_for_libraries,
        user_can_create_organizations,
        _get_course_creator_status,
    )
    from cms.djangoapps.contentstore.views.library import (
        user_can_view_create_library_button,
    )
    from openedx.core.djangoapps.content_libraries.api import (
        user_can_create_library,
    )

    active_courses = []
    archived_courses = []
    in_process_course_actions = []

    user = request.user
    libraries = []

    if not no_course:
        active_courses, archived_courses, in_process_course_actions = get_course_context(request)

    if not split_library_view_on_dashboard() and libraries_v1_enabled() and not no_course:
        libraries = get_library_context(request, True)['libraries']

    home_context = {
        'courses': active_courses,
        'split_studio_home': split_library_view_on_dashboard(),
        'archived_courses': archived_courses,
        'in_process_course_actions': in_process_course_actions,
        'libraries_enabled': libraries_v1_enabled(),
        'libraries_v1_enabled': libraries_v1_enabled(),
        'libraries_v2_enabled': libraries_v2_enabled(),
        'taxonomies_enabled': not is_tagging_feature_disabled(),
        'taxonomy_list_mfe_url': get_taxonomy_list_url(),
        'libraries': libraries,
        'show_new_library_button': user_can_view_create_library_button(user),
        'show_new_library_v2_button': user_can_create_library(user),
        'user': user,
        'request_course_creator_url': reverse('request_course_creator'),
        'course_creator_status': _get_course_creator_status(user),
        'rerun_creator_status': GlobalStaff().has_user(user),
        'allow_unicode_course_id': settings.FEATURES.get('ALLOW_UNICODE_COURSE_ID', False),
        'allow_course_reruns': settings.FEATURES.get('ALLOW_COURSE_RERUNS', True),
        'active_tab': 'courses',
        'allowed_organizations': get_allowed_organizations(user),
        'allowed_organizations_for_libraries': get_allowed_organizations_for_libraries(user),
        'can_create_organizations': user_can_create_organizations(user),
        'can_access_advanced_settings': auth.has_studio_advanced_settings_access(user),
    }

    return home_context


def get_course_rerun_context(course_key, course_block, user):
    """
    Utils is used to get context of course rerun.
    It is used for both DRF and django views.
    """

    from cms.djangoapps.contentstore.views.course import _get_course_creator_status

    course_rerun_context = {
        'source_course_key': course_key,
        'display_name': course_block.display_name,
        'user': user,
        'course_creator_status': _get_course_creator_status(user),
        'allow_unicode_course_id': settings.FEATURES.get('ALLOW_UNICODE_COURSE_ID', False)
    }

    return course_rerun_context


def get_course_videos_context(course_block, pagination_conf, course_key=None):
    """
    Utils is used to get contest of course videos.
    It is used for both DRF and django views.
    """

    from edx_toggles.toggles import WaffleSwitch
    from edxval.api import (
        get_3rd_party_transcription_plans,
        get_transcript_credentials_state_for_org,
        get_transcript_preferences,
    )
    from openedx.core.djangoapps.video_config.models import VideoTranscriptEnabledFlag
    from openedx.core.djangoapps.video_config.toggles import use_xpert_translations_component
    from xmodule.video_block.transcripts_utils import Transcript  # lint-amnesty, pylint: disable=wrong-import-order

    from .video_storage_handlers import (
        get_all_transcript_languages,
        _get_index_videos,
        _get_default_video_image_url
    )

    VIDEO_SUPPORTED_FILE_FORMATS = {
        '.mp4': 'video/mp4',
        '.mov': 'video/quicktime',
    }
    VIDEO_UPLOAD_MAX_FILE_SIZE_GB = 5
    # Waffle switch for enabling/disabling video image upload feature
    VIDEO_IMAGE_UPLOAD_ENABLED = WaffleSwitch(  # lint-amnesty, pylint: disable=toggle-missing-annotation
        'videos.video_image_upload_enabled', __name__
    )

    course = course_block
    if not course:
        with modulestore().bulk_operations(course_key):
            course = modulestore().get_course(course_key)

    is_video_transcript_enabled = VideoTranscriptEnabledFlag.feature_enabled(course.id)
    is_ai_translations_enabled = use_xpert_translations_component(course.id)
    previous_uploads, pagination_context = _get_index_videos(course, pagination_conf)
    course_video_context = {
        'context_course': course,
        'image_upload_url': reverse_course_url('video_images_handler', str(course.id)),
        'video_handler_url': reverse_course_url('videos_handler', str(course.id)),
        'encodings_download_url': reverse_course_url('video_encodings_download', str(course.id)),
        'default_video_image_url': _get_default_video_image_url(),
        'previous_uploads': previous_uploads,
        'concurrent_upload_limit': settings.VIDEO_UPLOAD_PIPELINE.get('CONCURRENT_UPLOAD_LIMIT', 0),
        'video_supported_file_formats': list(VIDEO_SUPPORTED_FILE_FORMATS.keys()),
        'video_upload_max_file_size': VIDEO_UPLOAD_MAX_FILE_SIZE_GB,
        'video_image_settings': {
            'video_image_upload_enabled': VIDEO_IMAGE_UPLOAD_ENABLED.is_enabled(),
            'max_size': settings.VIDEO_IMAGE_SETTINGS['VIDEO_IMAGE_MAX_BYTES'],
            'min_size': settings.VIDEO_IMAGE_SETTINGS['VIDEO_IMAGE_MIN_BYTES'],
            'max_width': settings.VIDEO_IMAGE_MAX_WIDTH,
            'max_height': settings.VIDEO_IMAGE_MAX_HEIGHT,
            'supported_file_formats': settings.VIDEO_IMAGE_SUPPORTED_FILE_FORMATS
        },
        'is_video_transcript_enabled': is_video_transcript_enabled,
        'is_ai_translations_enabled': is_ai_translations_enabled,
        'active_transcript_preferences': None,
        'transcript_credentials': None,
        'transcript_available_languages': get_all_transcript_languages(),
        'video_transcript_settings': {
            'transcript_download_handler_url': reverse('transcript_download_handler'),
            'transcript_upload_handler_url': reverse('transcript_upload_handler'),
            'transcript_delete_handler_url': reverse_course_url('transcript_delete_handler', str(course.id)),
            'trancript_download_file_format': Transcript.SRT
        },
        'pagination_context': pagination_context
    }
    if is_video_transcript_enabled:
        course_video_context['video_transcript_settings'].update({
            'transcript_preferences_handler_url': reverse_course_url(
                'transcript_preferences_handler',
                str(course.id)
            ),
            'transcript_credentials_handler_url': reverse_course_url(
                'transcript_credentials_handler',
                str(course.id)
            ),
            'transcription_plans': get_3rd_party_transcription_plans(),
        })
        course_video_context['active_transcript_preferences'] = get_transcript_preferences(str(course.id))
        # Cached state for transcript providers' credentials (org-specific)
        course_video_context['transcript_credentials'] = get_transcript_credentials_state_for_org(course.id.org)
    return course_video_context


def get_course_index_context(request, course_key, course_block=None):
    """
    Wrapper function to wrap _get_course_index_context in bulk operation
    if course_block is None.
    """
    if not course_block:
        with modulestore().bulk_operations(course_key):
            course_block = modulestore().get_course(course_key)
            return _get_course_index_context(request, course_key, course_block)
    return _get_course_index_context(request, course_key, course_block)


def _get_course_index_context(request, course_key, course_block):
    """
    Utils is used to get context of course index outline.
    It is used for both DRF and django views.
    """

    from cms.djangoapps.contentstore.views.course import (
        course_outline_initial_state,
        _course_outline_json,
        _deprecated_blocks_info,
    )
    from openedx.core.djangoapps.content_staging import api as content_staging_api

    lms_link = get_lms_link_for_item(course_block.location)
    reindex_link = None
    if settings.FEATURES.get('ENABLE_COURSEWARE_INDEX', False):
        if GlobalStaff().has_user(request.user):
            reindex_link = f"/course/{str(course_key)}/search_reindex"
    sections = course_block.get_children()
    course_structure = _course_outline_json(request, course_block)
    locator_to_show = request.GET.get('show', None)

    course_release_date = (
        get_default_time_display(course_block.start)
        if course_block.start != DEFAULT_START_DATE
        else _("Set Date")
    )

    settings_url = reverse_course_url('settings_handler', course_key)

    try:
        current_action = CourseRerunState.objects.find_first(course_key=course_key, should_display=True)
    except (ItemNotFoundError, CourseActionStateItemNotFoundError):
        current_action = None

    deprecated_block_names = [block.name for block in deprecated_xblocks()]
    deprecated_blocks_info = _deprecated_blocks_info(course_block, deprecated_block_names)

    frontend_app_publisher_url = configuration_helpers.get_value_for_org(
        course_block.location.org,
        'FRONTEND_APP_PUBLISHER_URL',
        settings.FEATURES.get('FRONTEND_APP_PUBLISHER_URL', False)
    )
    # gather any errors in the currently stored proctoring settings.
    advanced_dict = CourseMetadata.fetch(course_block)
    proctoring_errors = CourseMetadata.validate_proctoring_settings(course_block, advanced_dict, request.user)

    user_clipboard = content_staging_api.get_user_clipboard_json(request.user.id, request)
    course_block.discussions_settings['discussion_configuration_url'] = (
        f'{get_pages_and_resources_url(course_block.id)}/discussion/settings'
    )
    try:
        course_overview = CourseOverview.objects.get(id=course_block.id)
    except CourseOverview.DoesNotExist:
        course_overview = None
    course_index_context = {
        'language_code': request.LANGUAGE_CODE,
        'context_course': course_block,
        'discussions_settings': course_block.discussions_settings,
        'lms_link': lms_link,
        'sections': sections,
        'course_structure': course_structure,
        'initial_state': course_outline_initial_state(locator_to_show, course_structure) if locator_to_show else None,  # lint-amnesty, pylint: disable=line-too-long
        'initial_user_clipboard': user_clipboard,
        'rerun_notification_id': current_action.id if current_action else None,
        'course_release_date': course_release_date,
        'settings_url': settings_url,
        'reindex_link': reindex_link,
        'deprecated_blocks_info': deprecated_blocks_info,
        'notification_dismiss_url': reverse_course_url(
            'course_notifications_handler',
            current_action.course_key,
            kwargs={
                'action_state_id': current_action.id,
            },
        ) if current_action else None,
        'frontend_app_publisher_url': frontend_app_publisher_url,
        'mfe_proctored_exam_settings_url': get_proctored_exam_settings_url(course_block.id),
        'advance_settings_url': reverse_course_url('advanced_settings_handler', course_block.id),
        'proctoring_errors': proctoring_errors,
        'taxonomy_tags_widget_url': get_taxonomy_tags_widget_url(course_block.id),
        'created_on': course_overview.created if course_overview else None,
    }
    return course_index_context


def get_container_handler_context(request, usage_key, course, xblock):  # pylint: disable=too-many-statements
    """
    Utils is used to get context for container xblock requests.
    It is used for both DRF and django views.
    """

    from cms.djangoapps.contentstore.views.component import (
        get_component_templates,
        get_unit_tags,
        CONTAINER_TEMPLATES,
        LIBRARY_BLOCK_TYPES,
    )
    from cms.djangoapps.contentstore.helpers import get_parent_xblock, is_unit
    from cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers import (
        add_container_page_publishing_info,
        create_xblock_info,
    )
    from openedx.core.djangoapps.content_staging import api as content_staging_api

    course_sequence_ids = get_sequence_usage_keys(course)
    component_templates = get_component_templates(course)
    ancestor_xblocks = []
    parent = get_parent_xblock(xblock)
    action = request.GET.get('action', 'view')

    is_unit_page = is_unit(xblock)
    unit = xblock if is_unit_page else None

    is_first = True
    block = xblock

    # Build the breadcrumbs and find the ``Unit`` ancestor
    # if it is not the immediate parent.
    while parent:

        if unit is None and is_unit(block):
            unit = block

        # add all to nav except current xblock page
        if xblock != block:
            current_block = {
                'title': block.display_name_with_default,
                'children': parent.get_children(),
                'is_last': is_first
            }
            is_first = False
            ancestor_xblocks.append(current_block)

        block = parent
        parent = get_parent_xblock(parent)

    ancestor_xblocks.reverse()

    if unit is None:
        raise ValueError("Could not determine unit page")

    subsection = get_parent_xblock(unit)
    if subsection is None:
        raise ValueError(f"Could not determine parent subsection from unit {unit.location}")

    section = get_parent_xblock(subsection)
    if section is None:
        raise ValueError(f"Could not determine ancestor section from unit {unit.location}")

    # for the sequence navigator
    prev_url, next_url = get_sibling_urls(subsection, unit.location)
    # these are quoted here because they'll end up in a query string on the page,
    # and quoting with mako will trigger the xss linter...
    prev_url = quote_plus(prev_url) if prev_url else None
    next_url = quote_plus(next_url) if next_url else None

    show_unit_tags = not is_tagging_feature_disabled()
    unit_tags = None
    if show_unit_tags and is_unit_page:
        unit_tags = get_unit_tags(usage_key)

    # Fetch the XBlock info for use by the container page. Note that it includes information
    # about the block's ancestors and siblings for use by the Unit Outline.
    xblock_info = create_xblock_info(xblock, include_ancestor_info=is_unit_page, tags=unit_tags)

    if is_unit_page:
        add_container_page_publishing_info(xblock, xblock_info)

    # need to figure out where this item is in the list of children as the
    # preview will need this
    index = 1
    for child in subsection.get_children():
        if child.location == unit.location:
            break
        index += 1

    # Get the status of the user's clipboard so they can paste components if they have something to paste
    user_clipboard = content_staging_api.get_user_clipboard_json(request.user.id, request)
    library_block_types = [problem_type['component'] for problem_type in LIBRARY_BLOCK_TYPES]
    is_library_xblock = xblock.location.block_type in library_block_types

    context = {
        'language_code': request.LANGUAGE_CODE,
        'context_course': course,  # Needed only for display of menus at top of page.
        'action': action,
        'xblock': xblock,
        'xblock_locator': xblock.location,
        'unit': unit,
        'is_unit_page': is_unit_page,
        'is_collapsible': is_library_xblock,
        'subsection': subsection,
        'section': section,
        'position': index,
        'prev_url': prev_url,
        'next_url': next_url,
        'new_unit_category': 'vertical',
        'outline_url': '{url}?format=concise'.format(url=reverse_course_url('course_handler', course.id)),
        'ancestor_xblocks': ancestor_xblocks,
        'component_templates': component_templates,
        'xblock_info': xblock_info,
        'templates': CONTAINER_TEMPLATES,
        'show_unit_tags': show_unit_tags,
        # Status of the user's clipboard, exactly as would be returned from the "GET clipboard" REST API.
        'user_clipboard': user_clipboard,
        'is_fullwidth_content': is_library_xblock,
        'course_sequence_ids': course_sequence_ids,
        'library_content_picker_url': get_library_content_picker_url(course.id),
    }
    return context


def get_textbooks_context(course):
    """
    Utils is used to get context for textbooks for course.
    It is used for both DRF and django views.
    """

    upload_asset_url = reverse_course_url('assets_handler', course.id)
    textbook_url = reverse_course_url('textbooks_list_handler', course.id)
    return {
        'context_course': course,
        'textbooks': course.pdf_textbooks,
        'upload_asset_url': upload_asset_url,
        'textbook_url': textbook_url,
    }


def get_certificates_context(course, user):
    """
    Utils is used to get context for container xblock requests.
    It is used for both DRF and django views.
    """

    from cms.djangoapps.contentstore.views.certificates import CertificateManager

    course_key = course.id
    certificate_url = reverse_course_url('certificates_list_handler', course_key)
    course_outline_url = reverse_course_url('course_handler', course_key)
    upload_asset_url = reverse_course_url('assets_handler', course_key)
    activation_handler_url = reverse_course_url(
        handler_name='certificate_activation_handler',
        course_key=course_key
    )
    course_modes = CertificateManager.get_course_modes(course)

    has_certificate_modes = len(course_modes) > 0

    if has_certificate_modes:
        certificate_web_view_url = get_lms_link_for_certificate_web_view(
            course_key=course_key,
            mode=course_modes[0]  # CourseMode.modes_for_course returns default mode if doesn't find anyone.
        )
    else:
        certificate_web_view_url = None

    is_active, certificates = CertificateManager.is_activated(course)
    context = {
        'context_course': course,
        'certificate_url': certificate_url,
        'course_outline_url': course_outline_url,
        'upload_asset_url': upload_asset_url,
        'certificates': certificates,
        'has_certificate_modes': has_certificate_modes,
        'course_modes': course_modes,
        'certificate_web_view_url': certificate_web_view_url,
        'is_active': is_active,
        'is_global_staff': GlobalStaff().has_user(user),
        'certificate_activation_handler_url': activation_handler_url,
        'mfe_proctored_exam_settings_url': get_proctored_exam_settings_url(course_key),
    }

    return context


def get_group_configurations_context(course, store):
    """
    Utils is used to get context for course's group configurations.
    It is used for both DRF and django views.
    """

    from cms.djangoapps.contentstore.course_group_config import (
        COHORT_SCHEME, ENROLLMENT_SCHEME, GroupConfiguration, RANDOM_SCHEME
    )
    from cms.djangoapps.contentstore.views.course import (
        are_content_experiments_enabled
    )
    from xmodule.partitions.partitions import UserPartition  # lint-amnesty, pylint: disable=wrong-import-order

    course_key = course.id
    group_configuration_url = reverse_course_url('group_configurations_list_handler', course_key)
    course_outline_url = reverse_course_url('course_handler', course_key)
    should_show_experiment_groups = are_content_experiments_enabled(course)
    if should_show_experiment_groups:
        experiment_group_configurations = GroupConfiguration.get_split_test_partitions_with_usage(store, course)
    else:
        experiment_group_configurations = None

    all_partitions = GroupConfiguration.get_all_user_partition_details(store, course)
    should_show_enrollment_track = False
    has_content_groups = False
    displayable_partitions = []
    for partition in all_partitions:
        partition['read_only'] = getattr(UserPartition.get_scheme(partition['scheme']), 'read_only', False)

        if partition['scheme'] == COHORT_SCHEME:
            has_content_groups = True
            displayable_partitions.append(partition)
        elif partition['scheme'] == CONTENT_TYPE_GATING_SCHEME:
            # Add it to the front of the list if it should be shown.
            if ContentTypeGatingConfig.current(course_key=course_key).studio_override_enabled:
                displayable_partitions.append(partition)
        elif partition['scheme'] == ENROLLMENT_SCHEME:
            should_show_enrollment_track = len(partition['groups']) > 1

            # Add it to the front of the list if it should be shown.
            if should_show_enrollment_track:
                displayable_partitions.insert(0, partition)
        elif partition['scheme'] == TEAM_SCHEME:
            should_show_team_partitions = len(partition['groups']) > 0 and CONTENT_GROUPS_FOR_TEAMS.is_enabled(
                course_key
            )
            if should_show_team_partitions:
                displayable_partitions.append(partition)
        elif partition['scheme'] != RANDOM_SCHEME:
            # Experiment group configurations are handled explicitly above. We don't
            # want to display their groups twice.
            displayable_partitions.append(partition)

    # Set the sort-order. Higher numbers sort earlier
    scheme_priority = defaultdict(lambda: -1, {
        ENROLLMENT_SCHEME: 1,
        CONTENT_TYPE_GATING_SCHEME: 0
    })
    displayable_partitions.sort(key=lambda p: scheme_priority[p['scheme']], reverse=True)
    # Add empty content group if there is no COHORT User Partition in the list.
    # This will add ability to add new groups in the view.
    if not has_content_groups:
        displayable_partitions.append(GroupConfiguration.get_or_create_content_group(store, course))

    context = {
        'context_course': course,
        'group_configuration_url': group_configuration_url,
        'course_outline_url': course_outline_url,
        'experiment_group_configurations': experiment_group_configurations,
        'should_show_experiment_groups': should_show_experiment_groups,
        'all_group_configurations': displayable_partitions,
        'should_show_enrollment_track': should_show_enrollment_track,
        'mfe_proctored_exam_settings_url': get_proctored_exam_settings_url(course.id),
    }

    return context


class StudioPermissionsService:
    """
    Service that can provide information about a user's permissions.

    Deprecated. To be replaced by a more general authorization service.

    Only used by LegacyLibraryContentBlock (and library_tools.py).
    """

    def __init__(self, user):
        self._user = user

    def can_read(self, course_key):
        """ Does the user have read access to the given course/library? """
        return has_studio_read_access(self._user, course_key)

    def can_write(self, course_key):
        """ Does the user have read access to the given course/library? """
        return has_studio_write_access(self._user, course_key)


def track_course_update_event(course_key, user, course_update_content=None):
    """
    Track course update event
    """
    event_name = 'edx.contentstore.course_update'
    event_data = {}
    html_content = course_update_content.get("content", "")
    str_content = re.sub(r"(\s|&nbsp;|//)+", " ", html_to_text(html_content))

    event_data['content'] = str_content
    event_data['date'] = course_update_content.get("date", "")
    event_data['id'] = course_update_content.get("id", "")
    event_data['status'] = course_update_content.get("status", "")
    event_data['course_id'] = str(course_key)
    event_data['user_id'] = str(user.id)
    event_data['user_forums_roles'] = [
        role.name for role in user.roles.filter(course_id=str(course_key))
    ]
    event_data['user_course_roles'] = [
        role.role for role in user.courseaccessrole_set.filter(course_id=str(course_key))
    ]

    context = contexts.course_context_from_course_id(course_key)
    with tracker.get_tracker().context(event_name, context):
        tracker.emit(event_name, event_data)


def clean_html_body(html_body):
    """
    Get html body, remove tags and limit to 500 characters
    """
    html_body = html.unescape(html_body).strip()
    html_body = BeautifulSoup(Truncator(html_body).chars(500, html=True), 'html.parser')
    text_content = html_body.get_text(separator=" ").strip()
    text_content = text_content.replace('\n', '').replace('\r', '')
    return text_content


def send_course_update_notification(course_key, content, user):
    """
    Send course update notification
    """
    text_content = re.sub(r"(\s|&nbsp;|//)+", " ", clean_html_body(content))
    course = modulestore().get_course(course_key)
    extra_context = {
        'author_id': user.id,
        'course_name': course.display_name,
    }
    notification_data = CourseNotificationData(
        course_key=course_key,
        content_context={
            "course_update_content": text_content,
            **extra_context,
        },
        notification_type="course_updates",
        content_url=f"{settings.LMS_ROOT_URL}/courses/{str(course_key)}/course/updates",
        app_name="updates",
        audience_filters={},
    )
    # .. event_implemented_name: COURSE_NOTIFICATION_REQUESTED
    # .. event_type: org.openedx.learning.course.notification.requested.v1
    COURSE_NOTIFICATION_REQUESTED.send_event(course_notification_data=notification_data)


def get_xblock_validation_messages(xblock):
    """
    Retrieves validation messages for a given xblock.

    Args:
        xblock: The xblock object to validate.

    Returns:
        list: A list of validation error messages.
    """
    validation_json = xblock.validate().to_json()
    return validation_json['messages']


def get_xblock_render_error(request, xblock):
    """
    Checks if there are any rendering errors for a given block and return these.

    Args:
        request: WSGI request object
        xblock: The xblock object to rendering.

    Returns:
        str: Error message which happened while rendering of xblock.
    """
    from cms.djangoapps.contentstore.views.preview import _load_preview_block
    from xmodule.studio_editable import has_author_view
    from xmodule.x_module import AUTHOR_VIEW, STUDENT_VIEW

    def get_xblock_render_context(request, block):
        """
        Return a dict of the data needs for render of each block.
        """
        can_edit = has_studio_write_access(request.user, block.usage_key.course_key)

        return {
            "is_unit_page": False,
            "can_edit": can_edit,
            "root_xblock": xblock,
            "reorderable_items": set(),
            "paging": None,
            "force_render": None,
            "item_url": "/container/{block.location}",
            "tags_count_map": {},
        }

    try:
        block = _load_preview_block(request, xblock)
        preview_view = AUTHOR_VIEW if has_author_view(block) else STUDENT_VIEW
        render_context = get_xblock_render_context(request, block)
        block.render(preview_view, render_context)
    except Exception as exc:  # pylint: disable=broad-except
        return str(exc)

    return ""


def _create_or_update_component_link(created: datetime | None, xblock):
    """
    Create or update upstream->downstream link for components in database for given xblock.
    """
    upstream_usage_key = UsageKeyV2.from_string(xblock.upstream)
    try:
        lib_component = get_component_from_usage_key(upstream_usage_key)
    except ObjectDoesNotExist:
        log.error(f"Library component not found for {upstream_usage_key}")
        lib_component = None

    top_level_parent_usage_key = None
    if xblock.top_level_downstream_parent_key is not None:
        block_key = BlockKey.from_string(xblock.top_level_downstream_parent_key)
        top_level_parent_usage_key = BlockUsageLocator(
            xblock.usage_key.course_key,
            block_key.type,
            block_key.id,
        )

    ComponentLink.update_or_create(
        lib_component,
        upstream_usage_key=upstream_usage_key,
        upstream_context_key=str(upstream_usage_key.context_key),
        downstream_context_key=xblock.usage_key.course_key,
        downstream_usage_key=xblock.usage_key,
        top_level_parent_usage_key=top_level_parent_usage_key,
        version_synced=xblock.upstream_version,
        version_declined=xblock.upstream_version_declined,
        downstream_customized=getattr(xblock, "downstream_customized", []),
        created=created,
    )


def _create_or_update_container_link(created: datetime | None, xblock):
    """
    Create or update upstream->downstream link for containers in database for given xblock.
    """
    upstream_container_key = LibraryContainerLocator.from_string(xblock.upstream)
    try:
        lib_component = get_container(upstream_container_key).container_pk
    except ObjectDoesNotExist:
        log.error(f"Library component not found for {upstream_container_key}")
        lib_component = None

    top_level_parent_usage_key = None
    if xblock.top_level_downstream_parent_key is not None:
        block_key = BlockKey.from_string(xblock.top_level_downstream_parent_key)
        top_level_parent_usage_key = BlockUsageLocator(
            xblock.usage_key.course_key,
            block_key.type,
            block_key.id,
        )

    ContainerLink.update_or_create(
        lib_component,
        upstream_container_key=upstream_container_key,
        upstream_context_key=str(upstream_container_key.context_key),
        downstream_context_key=xblock.usage_key.course_key,
        downstream_usage_key=xblock.usage_key,
        version_synced=xblock.upstream_version,
        top_level_parent_usage_key=top_level_parent_usage_key,
        version_declined=xblock.upstream_version_declined,
        downstream_customized=getattr(xblock, "downstream_customized", []),
        created=created,
    )


def create_or_update_xblock_upstream_link(xblock, created: datetime | None = None) -> None:
    """
    Create or update upstream->downstream link in database for given xblock.
    """
    if not xblock.upstream:
        return None
    try:
        # Try to create component link
        _create_or_update_component_link(created, xblock)
    except InvalidKeyError:
        # It is possible that the upstream is a container and UsageKeyV2 parse failed
        # Create upstream container link and raise InvalidKeyError if xblock.upstream is a valid key.
        _create_or_update_container_link(created, xblock)


def get_previous_run_course_key(course_key):
    """
    Retrieves the course key of the previous run for a given course.
    """
    try:
        rerun_state = CourseRerunState.objects.get(course_key=course_key)
    except CourseRerunState.DoesNotExist:
        log.warning(f'[Link Check] No rerun state found for course {course_key}. Cannot find previous run.')
        return None

    return rerun_state.source_course_key


def contains_course_reference(url, course_key):
    """
    Checks if a URL contains an exact reference to the specified course key.
    Uses specific delimiter matching to ensure exact matching and avoid partial matches.

    Args:
        url: The URL to check
        course_key: The course key to look for

    Returns:
        bool: True if URL contains exact reference to the course
    """
    if not course_key or not url:
        return False

    course_key_pattern = re.escape(str(course_key))

    # Ensure the course key is followed by '/' or end of string
    pattern = course_key_pattern + r'(?=/|$)'

    return bool(re.search(pattern, url, re.IGNORECASE))
