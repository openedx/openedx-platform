"""
CMS feature toggles.
"""
from edx_toggles.toggles import SettingToggle, WaffleFlag
from openedx.core.djangoapps.content.search import api as search_api
from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag


# .. toggle_name: ENABLE_EXPORT_GIT
# .. toggle_implementation: SettingToggle
# .. toggle_default: False
# .. toggle_description: When enabled, a "Export to Git" menu item is added to the course studio for courses that have a
#   valid "giturl" attribute. Exporting a course to git causes the course to be exported in the directory indicated by
#   the GIT_REPO_EXPORT_DIR setting. Note that when this feature is disabled, courses can still be exported to git with
#   the git_export management command.
# .. toggle_warning: To enable this feature, the GIT_REPO_EXPORT_DIR setting must be properly defined and point to an
#   existing directory.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2014-02-13
EXPORT_GIT = SettingToggle(
    "ENABLE_EXPORT_GIT", default=False, module_name=__name__
)

# Namespace for studio dashboard waffle flags.
CONTENTSTORE_NAMESPACE = 'contentstore'
CONTENTSTORE_LOG_PREFIX = 'Contentstore: '

# .. toggle_name: contentstore.split_library_on_studio_dashboard
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Enables data new view for library on studio dashboard.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2020-07-8
# .. toggle_tickets: TNL-7536
SPLIT_LIBRARY_ON_DASHBOARD = WaffleFlag(
    f'{CONTENTSTORE_NAMESPACE}.split_library_on_studio_dashboard',
    __name__,
    CONTENTSTORE_LOG_PREFIX,
)

# .. toggle_name: contentstore.bypass_olx_failure
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Enables bypassing olx validation failures during course import.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2021-04-15
# .. toggle_target_removal_date: 2021-05-15
# .. toggle_tickets: TNL-8214
BYPASS_OLX_FAILURE = WaffleFlag(
    f'{CONTENTSTORE_NAMESPACE}.bypass_olx_failure',
    __name__,
    CONTENTSTORE_LOG_PREFIX,
)


def split_library_view_on_dashboard():
    """
    check if data new view for library is enabled on studio dashboard.
    """
    return SPLIT_LIBRARY_ON_DASHBOARD.is_enabled()


def bypass_olx_failure_enabled():
    """
    Check if bypass is enabled for course olx validation errors.
    """
    return BYPASS_OLX_FAILURE.is_enabled()


# .. toggle_name: legacy_studio.exam_settings
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Temporarily fall back to the old proctored exam settings view.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2025-03-14
# .. toggle_target_removal_date: 2025-09-14
# .. toggle_tickets: https://github.com/openedx/edx-platform/issues/36275
# .. toggle_warning: In Ulmo, this toggle will be removed. Only the new (React-based) experience will be available.
LEGACY_STUDIO_EXAM_SETTINGS = CourseWaffleFlag("legacy_studio.exam_settings", __name__)


def exam_setting_view_enabled(course_key):
    """
    Returns a boolean if proctoring exam setting mfe view is enabled.
    """
    return not LEGACY_STUDIO_EXAM_SETTINGS.is_enabled(course_key)


# .. toggle_name: legacy_studio.video_editor
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Temporarily fall back to the old Video component (a.k.a. video block) editor.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2025-03-14
# .. toggle_target_removal_date: 2025-09-14
# .. toggle_tickets: https://github.com/openedx/edx-platform/issues/36275
# .. toggle_warning: In Ulmo, this toggle will be removed. Only the new (React-based) experience will be available.
LEGACY_STUDIO_VIDEO_EDITOR = CourseWaffleFlag('legacy_studio.video_editor', __name__)


def use_new_video_editor(course_key):
    """
    Returns a boolean = true if new video editor is enabled
    """
    return not LEGACY_STUDIO_VIDEO_EDITOR.is_enabled(course_key)


# .. toggle_name: new_core_editors.use_video_gallery_flow
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: This flag enables the use the video selection gallery on the flow of the new core video xblock editor
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2023-04-03
# .. toggle_target_removal_date: 2023-6-01
# .. toggle_warning: You need to activate the `new_core_editors.use_new_video_editor` flag to use this new flow.
ENABLE_VIDEO_GALLERY_FLOW_FLAG = WaffleFlag('new_core_editors.use_video_gallery_flow', __name__)


def use_video_gallery_flow():
    """
    Returns a boolean = true if the video gallery flow is enabled
    """
    return ENABLE_VIDEO_GALLERY_FLOW_FLAG.is_enabled()


# .. toggle_name: contentstore.individualize_anonymous_user_id
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: This flag enables the use of unique anonymous_user_id during studio preview
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2022-05-04
# .. toggle_target_removal_date: 2022-05-30
# .. toggle_tickets: MST-1455
INDIVIDUALIZE_ANONYMOUS_USER_ID = CourseWaffleFlag(
    f'{CONTENTSTORE_NAMESPACE}.individualize_anonymous_user_id', __name__
)


def individualize_anonymous_user_id(course_id):
    """
    Returns a boolean if individualized anonymous_user_id is enabled on the course
    """
    return INDIVIDUALIZE_ANONYMOUS_USER_ID.is_enabled(course_id)


# .. toggle_name: contentstore.use_react_markdown_editor
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: This flag enables the use of the Markdown editor when creating or editing problems in the authoring MFE
# .. toggle_use_cases: opt_in
# .. toggle_creation_date: 2025-4-11
# .. toggle_tickets: https://openedx.atlassian.net/wiki/spaces/OEPM/pages/4517232656/Re-enable+Markdown+editing+of+CAPA+problems+to+meet+various+use+cases
ENABLE_REACT_MARKDOWN_EDITOR = CourseWaffleFlag(
    f'{CONTENTSTORE_NAMESPACE}.use_react_markdown_editor', __name__)


def use_react_markdown_editor(course_key):
    """
    Returns a boolean if new studio custom pages mfe is enabled
    """
    return ENABLE_REACT_MARKDOWN_EDITOR.is_enabled(course_key)


# .. toggle_name: legacy_studio.schedule_details
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Temporarily fall back to the old Studio Schedule & Details page.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2025-03-14
# .. toggle_target_removal_date: 2025-09-14
# .. toggle_tickets: https://github.com/openedx/edx-platform/issues/36275
# .. toggle_warning: In Ulmo, this toggle will be removed. Only the new (React-based) experience will be available.
LEGACY_STUDIO_SCHEDULE_DETAILS = CourseWaffleFlag('legacy_studio.schedule_details', __name__)


def use_new_schedule_details_page(course_key):
    """
    Returns a boolean if new studio schedule and details mfe is enabled
    """
    return not LEGACY_STUDIO_SCHEDULE_DETAILS.is_enabled(course_key)


# .. toggle_name: legacy_studio.advanced_settings
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Temporarily fall back to the old Studio Advanced Settings page.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2025-03-14
# .. toggle_target_removal_date: 2025-09-14
# .. toggle_tickets: https://github.com/openedx/edx-platform/issues/36275
# .. toggle_warning: In Ulmo, this toggle will be removed. Only the new (React-based) experience will be available.
LEGACY_STUDIO_ADVANCED_SETTINGS = CourseWaffleFlag('legacy_studio.advanced_settings', __name__)


def use_new_advanced_settings_page(course_key):
    """
    Returns a boolean if new studio advanced settings pafe mfe is enabled
    """
    return not LEGACY_STUDIO_ADVANCED_SETTINGS.is_enabled(course_key)


# .. toggle_name: legacy_studio.grading
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Temporarily fall back to the old Studio Course Grading page.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2025-03-14
# .. toggle_target_removal_date: 2025-09-14
# .. toggle_tickets: https://github.com/openedx/edx-platform/issues/36275
# .. toggle_warning: In Ulmo, this toggle will be removed. Only the new (React-based) experience will be available.
LEGACY_STUDIO_GRADING = CourseWaffleFlag('legacy_studio.grading', __name__)


def use_new_grading_page(course_key):
    """
    Returns a boolean if new studio grading mfe is enabled
    """
    return not LEGACY_STUDIO_GRADING.is_enabled(course_key)


# .. toggle_name: legacy_studio.import
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Temporarily fall back to the old Course Import page.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2025-03-14
# .. toggle_target_removal_date: 2025-09-14
# .. toggle_tickets: https://github.com/openedx/edx-platform/issues/36275
# .. toggle_warning: In Ulmo, this toggle will be removed. Only the new (React-based) experience will be available.
LEGACY_STUDIO_IMPORT = CourseWaffleFlag('legacy_studio.import', __name__)


def use_new_import_page(course_key):
    """
    Returns a boolean if new studio import mfe is enabled
    """
    return not LEGACY_STUDIO_IMPORT.is_enabled(course_key)


# .. toggle_name: legacy_studio.export
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Temporarily fall back to the old Course Export page.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2025-03-14
# .. toggle_target_removal_date: 2025-09-14
# .. toggle_tickets: https://github.com/openedx/edx-platform/issues/36275
# .. toggle_warning: In Ulmo, this toggle will be removed. Only the new (React-based) experience will be available.
LEGACY_STUDIO_EXPORT = CourseWaffleFlag('legacy_studio.export', __name__)


def use_new_export_page(course_key):
    """
    Returns a boolean if new studio export mfe is enabled
    """
    return not LEGACY_STUDIO_EXPORT.is_enabled(course_key)


# .. toggle_name: contentstore.new_studio_mfe.use_new_video_uploads_page
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: This flag enables the use of the new studio video uploads page MFE.
#      Note: This page only works on edx.org or other sites that have reverse-engineered
#      the edX video pipeline. It is off by default for the community.
# .. toggle_use_cases: opt_in
# .. toggle_creation_date: 2023-05-15
# .. toggle_tickets: https://github.com/openedx/openedx-platform/issues/37972
ENABLE_NEW_STUDIO_VIDEO_UPLOADS_PAGE = CourseWaffleFlag(
    f'{CONTENTSTORE_NAMESPACE}.new_studio_mfe.use_new_video_uploads_page', __name__)


def use_new_video_uploads_page(course_key):
    """
    Returns a boolean if new studio video uploads MFE is enabled.

    This is off by default because the video uploads page requires the edX
    video pipeline which is not available to the open source community.
    """
    return ENABLE_NEW_STUDIO_VIDEO_UPLOADS_PAGE.is_enabled(course_key)


# .. toggle_name: legacy_studio.course_outline
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Temporarily fall back to the old Studio Course Outline editor.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2025-03-14
# .. toggle_target_removal_date: 2025-09-14
# .. toggle_tickets: https://github.com/openedx/edx-platform/issues/36275
# .. toggle_warning: In Ulmo, this toggle will be removed. Only the new (React-based) experience will be available.
LEGACY_STUDIO_COURSE_OUTLINE = CourseWaffleFlag('legacy_studio.course_outline', __name__)


# .. toggle_name: legacy_studio.unit_editor
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Temporarily fall back to the old Studio unit editing page.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2025-03-14
# .. toggle_target_removal_date: 2025-09-14
# .. toggle_tickets: https://github.com/openedx/edx-platform/issues/36275
# .. toggle_warning: In Ulmo, this toggle will be removed. Only the new (React-based) experience will be available.
LEGACY_STUDIO_UNIT_EDITOR = CourseWaffleFlag('legacy_studio.unit_editor', __name__)


def use_new_unit_page(course_key):
    """
    Returns a boolean if new studio course outline mfe is enabled
    """
    return not LEGACY_STUDIO_UNIT_EDITOR.is_enabled(course_key)


# .. toggle_name: legacy_studio.course_team
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Temporarily fall back to the old Studio Course Team page.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2025-03-14
# .. toggle_target_removal_date: 2025-09-14
# .. toggle_tickets: https://github.com/openedx/edx-platform/issues/36275
# .. toggle_warning: In Ulmo, this toggle will be removed. Only the new (React-based) experience will be available.
LEGACY_STUDIO_COURSE_TEAM = CourseWaffleFlag('legacy_studio.course_team', __name__)


def use_new_course_team_page(course_key):
    """
    Returns a boolean if new studio course team mfe is enabled
    """
    return not LEGACY_STUDIO_COURSE_TEAM.is_enabled(course_key)


# .. toggle_name: legacy_studio.certificates
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Temporarily fall back to the old Studio Course Certificates page.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2025-03-14
# .. toggle_target_removal_date: 2025-09-14
# .. toggle_tickets: https://github.com/openedx/edx-platform/issues/36275
# .. toggle_warning: In Ulmo, this toggle will be removed. Only the new (React-based) experience will be available.
LEGACY_STUDIO_CERTIFICATES = CourseWaffleFlag('legacy_studio.certificates', __name__)


def use_new_certificates_page(course_key):
    """
    Returns a boolean if new studio certificates mfe is enabled
    """
    return not LEGACY_STUDIO_CERTIFICATES.is_enabled(course_key)


# .. toggle_name: legacy_studio.configurations
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Temporarily fall back to the old Studio Configurations page.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2025-03-14
# .. toggle_target_removal_date: 2025-09-14
# .. toggle_tickets: https://github.com/openedx/edx-platform/issues/36275
# .. toggle_warning: In Ulmo, this toggle will be removed. Only the new (React-based) experience will be available.
LEGACY_STUDIO_CONFIGURATIONS = CourseWaffleFlag('legacy_studio.configurations', __name__)


def use_new_group_configurations_page(course_key):
    """
    Returns a boolean if new studio group configurations mfe is enabled
    """
    return not LEGACY_STUDIO_CONFIGURATIONS.is_enabled(course_key)


# .. toggle_name: contentstore.mock_video_uploads
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: This flag mocks contentstore video uploads for local development, if you don't have access to AWS
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2023-7-25
# .. toggle_tickets: TNL-10897
# .. toggle_warning:
MOCK_VIDEO_UPLOADS = WaffleFlag(
    f'{CONTENTSTORE_NAMESPACE}.mock_video_uploads', __name__)


def use_mock_video_uploads():
    """
    Returns a boolean if video uploads should be mocked for local development
    """
    return MOCK_VIDEO_UPLOADS.is_enabled()


# .. toggle_name: contentstore.default_enable_flexible_peer_openassessments
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: This flag turns on the force_on_flexible_peer_openassessments
#      setting for course reruns or new courses, where enabled.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2023-06-27
# .. toggle_target_removal_date: 2024-01-27
# .. toggle_tickets: AU-1289
# .. toggle_warning:
DEFAULT_ENABLE_FLEXIBLE_PEER_OPENASSESSMENTS = CourseWaffleFlag(
    f'{CONTENTSTORE_NAMESPACE}.default_enable_flexible_peer_openassessments', __name__)


def default_enable_flexible_peer_openassessments(course_key):
    """
    Returns a boolean if ORA flexible peer grading should be toggled on for a
    course rerun or new course. We expect this to be set at the organization
    level to opt in/out of rolling forward this feature.
    """
    return DEFAULT_ENABLE_FLEXIBLE_PEER_OPENASSESSMENTS.is_enabled(course_key)


# .. toggle_name: ENABLE_CONTENT_LIBRARIES
# .. toggle_implementation: SettingToggle
# .. toggle_default: True
# .. toggle_description: Enables use of the legacy and v2 libraries waffle flags.
#    Note that legacy content libraries are only supported in courses using split mongo.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2015-03-06
# .. toggle_target_removal_date: 2025-04-09
# .. toggle_warning: This flag is deprecated in Sumac, and will be removed in favor of the disable_legacy_libraries and
#    disable_new_libraries waffle flags.
ENABLE_CONTENT_LIBRARIES = SettingToggle(
    "ENABLE_CONTENT_LIBRARIES", default=True, module_name=__name__
)

# .. toggle_name: contentstore.new_studio_mfe.disable_legacy_libraries
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Hides legacy (v1) Libraries tab in Authoring MFE.
#    This toggle interacts with ENABLE_CONTENT_LIBRARIES toggle: if this is disabled, then legacy libraries are also
#    disabled.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2024-10-02
# .. toggle_target_removal_date: 2025-04-09
# .. toggle_tickets: https://github.com/openedx/frontend-app-authoring/issues/1334
# .. toggle_warning: Legacy libraries are deprecated in Sumac, cf https://github.com/openedx/edx-platform/issues/32457
DISABLE_LEGACY_LIBRARIES = WaffleFlag(
    f'{CONTENTSTORE_NAMESPACE}.new_studio_mfe.disable_legacy_libraries',
    __name__,
    CONTENTSTORE_LOG_PREFIX,
)


def libraries_v1_enabled():
    """
    Returns a boolean if Libraries V2 is enabled in the new Studio Home.
    """
    return (
        ENABLE_CONTENT_LIBRARIES.is_enabled() and
        not DISABLE_LEGACY_LIBRARIES.is_enabled()
    )


# .. toggle_name: contentstore.new_studio_mfe.disable_new_libraries
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Hides new Libraries v2 tab in Authoring MFE.
#    This toggle interacts with settings.MEILISEARCH_ENABLED and ENABLE_CONTENT_LIBRARIES toggle: if these flags are
#    False, then v2 libraries are also disabled.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2024-10-02
# .. toggle_target_removal_date: 2025-04-09
# .. toggle_tickets: https://github.com/openedx/frontend-app-authoring/issues/1334
# .. toggle_warning: Libraries v2 are in beta for Sumac, will be fully supported in Teak.
DISABLE_NEW_LIBRARIES = WaffleFlag(
    f'{CONTENTSTORE_NAMESPACE}.new_studio_mfe.disable_new_libraries',
    __name__,
    CONTENTSTORE_LOG_PREFIX,
)


def libraries_v2_enabled():
    """
    Returns a boolean if Libraries V2 is enabled in the new Studio Home.

    Requires the ENABLE_CONTENT_LIBRARIES feature flag to be enabled, plus Meilisearch.
    """
    return (
        ENABLE_CONTENT_LIBRARIES.is_enabled() and
        search_api.is_meilisearch_enabled() and
        not DISABLE_NEW_LIBRARIES.is_enabled()
    )


# .. toggle_name: contentstore.enable_course_optimizer
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: This flag enables the course optimizer tool in the authoring MFE.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2025-01-17
# .. toggle_target_removal_date: 2025-05-30
# .. toggle_tickets: TNL-11837
ENABLE_COURSE_OPTIMIZER = CourseWaffleFlag(
    f'{CONTENTSTORE_NAMESPACE}.enable_course_optimizer', __name__
)


def enable_course_optimizer(course_id):
    """
    Returns a boolean if course optimizer is enabled on the course
    """
    return ENABLE_COURSE_OPTIMIZER.is_enabled(course_id)


# .. toggle_name: legacy_studio.logged_out_home
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Temporarily fall back to the old Studio "How it Works" page when unauthenticated
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2025-03-14
# .. toggle_target_removal_date: 2025-09-14
# .. toggle_tickets: https://github.com/openedx/edx-platform/issues/36275
# .. toggle_warning: In Ulmo, this toggle will be removed, along with the legacy page. The only available
#  behavior will be to send the user to the log-in page with a redirect to Studio Course Listing (/home).
LEGACY_STUDIO_LOGGED_OUT_HOME = WaffleFlag('legacy_studio.logged_out_home', __name__)


def use_legacy_logged_out_home():
    """
    Returns whether the old "how it works" page should be shown.

    If not, then we should just go to the login page w/ redirect to studio course listing.
    """
    return LEGACY_STUDIO_LOGGED_OUT_HOME.is_enabled()


# .. toggle_name: contentstore.enable_course_optimizer_check_prev_run_links
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: When enabled, allows the Course Optimizer to detect and update links pointing to previous course runs.
#   This feature enables instructors to fix internal course links that still point to old course runs
#   after creating a course rerun.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2025-07-21
# .. toggle_target_removal_date: 2026-02-25
ENABLE_COURSE_OPTIMIZER_CHECK_PREV_RUN_LINKS = CourseWaffleFlag(
    f'{CONTENTSTORE_NAMESPACE}.enable_course_optimizer_check_prev_run_links',
    __name__,
    CONTENTSTORE_LOG_PREFIX,
)


def enable_course_optimizer_check_prev_run_links(course_key):
    """
    Returns a boolean if previous run course optimizer feature is enabled for the given course.
    """
    return ENABLE_COURSE_OPTIMIZER_CHECK_PREV_RUN_LINKS.is_enabled(course_key)
