"""
This is the common settings file, intended to set sane defaults. If you have a
piece of configuration that's dependent on a set of feature flags being set,
then create a function that returns the calculated value based on the value of
FEATURES[...]. Modules that extend this one can change the feature
configuration in an environment specific config file and re-calculate those
values.

We should make a method that calls all these config methods so that you just
make one call at the end of your site-specific dev file to reset all the
dependent variables (like INSTALLED_APPS) for you.

Longer TODO:
1. Right now our treatment of static content in general and in particular
   course-specific static content is haphazard.
2. We should have a more disciplined approach to feature flagging, even if it
   just means that we stick them in a dict called FEATURES.
3. We need to handle configuration for multiple courses. This could be as
   multiple sites, but we do need a way to map their data assets.

When refering to XBlocks, we use the entry-point name. For example,
|   setup(
|       name='xblock-foobar',
|       version='0.1',
|       packages=[
|           'foobar_xblock',
|       ],
|       entry_points={
|           'xblock.v1': [
|               'foobar-block = foobar_xblock:FoobarBlock',
|           #    ^^^^^^^^^^^^ This is the one you want.
|           ]
|       },
|   )
"""

# We intentionally define lots of variables that aren't used, and
# want to import all variables from base settings files
# pylint: disable=unused-import, useless-suppression, wrong-import-order, wrong-import-position

import importlib.util
import json
import os
import sys

from corsheaders.defaults import default_headers as corsheaders_default_headers
from datetime import timedelta
import lms.envs.common
# Although this module itself may not use these imported variables, other dependent modules may.
# Warning: Do NOT add any new variables to this list. This is incompatible with future plans to
#   have more logical separation between LMS and Studio (CMS). It is also incompatible with the
#   direction documented in OEP-45: Configuring and Operating Open edX:
#   https://open-edx-proposals.readthedocs.io/en/latest/oep-0045-arch-ops-and-config.html
from lms.envs.common import (
    USE_TZ, ALL_LANGUAGES, ASSET_IGNORE_REGEX,
    PARENTAL_CONSENT_AGE_LIMIT, REGISTRATION_EMAIL_PATTERNS_ALLOWED,
    # The following PROFILE_IMAGE_* settings are included as they are
    # indirectly accessed through the email opt-in API, which is
    # technically accessible through the CMS via legacy URLs.
    PROFILE_IMAGE_BACKEND, PROFILE_IMAGE_DEFAULT_FILENAME, PROFILE_IMAGE_DEFAULT_FILE_EXTENSION,
    PROFILE_IMAGE_HASH_SEED, PROFILE_IMAGE_MIN_BYTES, PROFILE_IMAGE_MAX_BYTES, PROFILE_IMAGE_SIZES_MAP,
    # The following setting is included as it is used to check whether to
    # display credit eligibility table on the CMS or not.
    COURSE_MODE_DEFAULTS, DEFAULT_COURSE_ABOUT_IMAGE_URL,

    # User-uploaded content
    MEDIA_ROOT,
    MEDIA_URL,

    # Lazy Gettext
    _,

    # Django REST framework configuration
    REST_FRAMEWORK,

    STATICI18N_OUTPUT_DIR,

    # Heartbeat
    HEARTBEAT_CHECKS,
    HEARTBEAT_EXTENDED_CHECKS,
    HEARTBEAT_CELERY_TIMEOUT,
    HEARTBEAT_CELERY_ROUTING_KEY,

    # Default site to use if no site exists matching request headers
    SITE_ID,

    # constants for redirects app
    REDIRECT_CACHE_TIMEOUT,
    REDIRECT_CACHE_KEY_PREFIX,

    # This is required for the migrations in oauth_dispatch.models
    # otherwise it fails saying this attribute is not present in Settings
    # Although Studio does not enable OAuth2 Provider capability, the new approach
    # to generating test databases will discover and try to create all tables
    # and this setting needs to be present
    OAUTH2_PROVIDER_APPLICATION_MODEL,
    JWT_AUTH,

    USERNAME_REGEX_PARTIAL,
    USERNAME_PATTERN,

    # django-debug-toolbar
    DEBUG_TOOLBAR_PATCH_SETTINGS,

    COURSE_ENROLLMENT_MODES,
    CONTENT_TYPE_GATE_GROUP_IDS,

    DISABLE_ACCOUNT_ACTIVATION_REQUIREMENT_SWITCH,

    GENERATE_PROFILE_SCORES,

    # Enterprise service settings
    ENTERPRISE_CATALOG_INTERNAL_ROOT_URL,
    ENTERPRISE_BACKEND_SERVICE_EDX_OAUTH2_KEY,
    ENTERPRISE_BACKEND_SERVICE_EDX_OAUTH2_SECRET,
    ENTERPRISE_BACKEND_SERVICE_EDX_OAUTH2_PROVIDER_URL,

    # Methods to derive settings
    _make_mako_template_dirs,
    _make_locale_paths,

    # Password Validator Settings
    AUTH_PASSWORD_VALIDATORS
)
from lms.envs.common import (
    USE_EXTRACTED_WORD_CLOUD_BLOCK,
    USE_EXTRACTED_ANNOTATABLE_BLOCK,
    USE_EXTRACTED_POLL_QUESTION_BLOCK,
    USE_EXTRACTED_LTI_BLOCK,
    USE_EXTRACTED_HTML_BLOCK,
    USE_EXTRACTED_DISCUSSION_BLOCK,
    USE_EXTRACTED_PROBLEM_BLOCK,
    USE_EXTRACTED_VIDEO_BLOCK,
)
from path import Path as path
from django.urls import reverse_lazy

from lms.djangoapps.lms_xblock.mixin import LmsBlockMixin
from cms.lib.xblock.authoring_mixin import AuthoringMixin
from cms.lib.xblock.upstream_sync import UpstreamSyncMixin
from xmodule.modulestore.edit_info import EditInfoMixin
from openedx.core.djangoapps.theming.helpers_dirs import (
    get_themes_unchecked,
    get_theme_base_dirs_from_settings
)
from openedx.core.lib.license import LicenseMixin
from openedx.core.lib.derived import Derived
from openedx.core.release import doc_version

# pylint: enable=useless-suppression

################ Enable credit eligibility feature ####################
ENABLE_CREDIT_ELIGIBILITY = True

################################ Block Structures ###################################
BLOCK_STRUCTURES_SETTINGS = dict(
    # Delay, in seconds, after a new edit of a course is published
    # before updating the block structures cache.  This is needed
    # for a better chance at getting the latest changes when there
    # are secondary reads in sharded mongoDB clusters. See TNL-5041
    # for more info.
    COURSE_PUBLISH_TASK_DELAY=30,

    # Delay, in seconds, between retry attempts if a task fails.
    TASK_DEFAULT_RETRY_DELAY=30,

    # Maximum number of retries per task.
    TASK_MAX_RETRIES=5,
)

############################ FEATURE CONFIGURATION #############################

PLATFORM_NAME = _('Your Platform Name Here')

CONTACT_MAILING_ADDRESS = _('Your Contact Mailing Address Here')

PLATFORM_DESCRIPTION = _('Your Platform Description Here')

PLATFORM_FACEBOOK_ACCOUNT = "http://www.facebook.com/YourPlatformFacebookAccount"
PLATFORM_TWITTER_ACCOUNT = "@YourPlatformTwitterAccount"

# Dummy secret key for dev/test
SECRET_KEY = 'dev key'
FAVICON_PATH = 'images/favicon.ico'


# .. setting_name: STUDIO_NAME
# .. setting_default: Your Platform Studio
# .. setting_description: The name that will appear on the landing page of Studio, as well as in various emails and
#   templates.
STUDIO_NAME = _("Your Platform Studio")
STUDIO_SHORT_NAME = _("Studio")
FEATURES = {
    'GITHUB_PUSH': False,

    # See annotations in lms/envs/common.py for details.
    'ENABLE_DISCUSSION_SERVICE': True,
    # See annotations in lms/envs/common.py for details.
    'ENABLE_TEXTBOOK': True,

    # When True, all courses will be active, regardless of start date
    # DO NOT SET TO True IN THIS FILE
    # Doing so will cause all courses to be released on production
    'DISABLE_START_DATES': False,

    # email address for studio staff (eg to request course creation)
    'STUDIO_REQUEST_EMAIL': '',

    # Segment - must explicitly turn it on for production
    'CMS_SEGMENT_KEY': None,

    # Enable URL that shows information about the status of various services
    'ENABLE_SERVICE_STATUS': False,

    # Don't autoplay videos for course authors
    'AUTOPLAY_VIDEOS': False,

    # Move the course author to next page when a video finishes. Set to True to
    # show an auto-advance button in videos. If False, videos never auto-advance.
    'ENABLE_AUTOADVANCE_VIDEOS': False,

    # If set to True, new Studio users won't be able to author courses unless
    # an Open edX admin has added them to the course creator group.
    'ENABLE_CREATOR_GROUP': True,

    # If set to True, organization staff members can create libraries for their specific
    # organization and no other organizations. They do not need to be course creators,
    # even when ENABLE_CREATOR_GROUP is True.
    'ENABLE_ORGANIZATION_STAFF_ACCESS_FOR_CONTENT_LIBRARIES': True,

    # Turn off account locking if failed login attempts exceeds a limit
    'ENABLE_MAX_FAILED_LOGIN_ATTEMPTS': False,

    # .. toggle_name: FEATURES['EDITABLE_SHORT_DESCRIPTION']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: This feature flag allows editing of short descriptions on the Schedule & Details page in
    #   Open edX Studio. Set to False if you want to disable the editing of the course short description.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2014-02-13
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/2334
    'EDITABLE_SHORT_DESCRIPTION': True,

    # Hide any Personally Identifiable Information from application logs
    'SQUELCH_PII_IN_LOGS': False,

    # Toggles the embargo functionality, which blocks users
    # based on their location.
    'EMBARGO': False,

    # Allow creating courses with non-ascii characters in the course id
    'ALLOW_UNICODE_COURSE_ID': False,

    # Prevent concurrent logins per user
    'PREVENT_CONCURRENT_LOGINS': False,

    # Turn off Video Upload Pipeline through Studio, by default
    'ENABLE_VIDEO_UPLOAD_PIPELINE': False,

    # See annotations in lms/envs/common.py for details.
    'ENABLE_EDXNOTES': False,

    # Toggle to enable coordination with the Publisher tool (keep in sync with lms/envs/common.py)
    'ENABLE_PUBLISHER': False,

    # Show a new field in "Advanced settings" that can store custom data about a
    # course and that can be read from themes
    'ENABLE_OTHER_COURSE_SETTINGS': False,

    # Write new CSM history to the extended table.
    # This will eventually default to True and may be
    # removed since all installs should have the separate
    # extended history table. This is needed in the LMS and CMS
    # for migration consistency.
    'ENABLE_CSMH_EXTENDED': True,

    # Enable support for content libraries. Note that content libraries are
    # only supported in courses using split mongo.
    'ENABLE_CONTENT_LIBRARIES': True,

    # .. toggle_name: FEATURES['ENABLE_CONTENT_LIBRARIES_LTI_TOOL']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: When set to True, Content Libraries in
    #    Studio can be used as an LTI 1.3 tool by external LTI platforms.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2021-08-17
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/27411
    'ENABLE_CONTENT_LIBRARIES_LTI_TOOL': False,

    # Milestones application flag
    'MILESTONES_APP': False,

    # Prerequisite courses feature flag
    'ENABLE_PREREQUISITE_COURSES': False,

    # Toggle course entrance exams feature
    'ENTRANCE_EXAMS': False,

    # Toggle platform-wide course licensing
    'LICENSING': False,

    # Enable the courseware search functionality
    'ENABLE_COURSEWARE_INDEX': False,

    # Enable content libraries (modulestore) search functionality
    'ENABLE_LIBRARY_INDEX': False,

    # .. toggle_name: FEATURES['ALLOW_COURSE_RERUNS']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: This will allow staff member to re-run the course from the studio home page and will
    #   always use the split modulestore. When this is set to False, the Re-run Course link will not be available on
    #   the studio home page.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2015-02-13
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/6965
    'ALLOW_COURSE_RERUNS': True,

    # Certificates Web/HTML Views
    'CERTIFICATES_HTML_VIEW': False,

    # Teams feature
    'ENABLE_TEAMS': True,

    # Show video bumper in Studio
    'ENABLE_VIDEO_BUMPER': False,

    # How many seconds to show the bumper again, default is 7 days:
    'SHOW_BUMPER_PERIODICITY': 7 * 24 * 3600,

    # Enable credit eligibility feature
    'ENABLE_CREDIT_ELIGIBILITY': ENABLE_CREDIT_ELIGIBILITY,

    # Special Exams, aka Timed and Proctored Exams
    'ENABLE_SPECIAL_EXAMS': False,

    # Show the language selector in the header
    'SHOW_HEADER_LANGUAGE_SELECTOR': False,

    # At edX it's safe to assume that English transcripts are always available
    # This is not the case for all installations.
    # The default value in {lms,cms}/envs/common.py and xmodule/tests/test_video.py should be consistent.
    'FALLBACK_TO_ENGLISH_TRANSCRIPTS': True,

    # Set this to False to facilitate cleaning up invalid xml from your modulestore.
    'ENABLE_XBLOCK_XML_VALIDATION': True,

    # Allow public account creation
    'ALLOW_PUBLIC_ACCOUNT_CREATION': True,

    # Allow showing the registration links
    'SHOW_REGISTRATION_LINKS': True,

    # Whether or not the dynamic EnrollmentTrackUserPartition should be registered.
    'ENABLE_ENROLLMENT_TRACK_USER_PARTITION': True,

    'ENABLE_PASSWORD_RESET_FAILURE_EMAIL': False,

    # Whether archived courses (courses with end dates in the past) should be
    # shown in Studio in a separate list.
    'ENABLE_SEPARATE_ARCHIVED_COURSES': True,

    # For acceptance and load testing
    'AUTOMATIC_AUTH_FOR_TESTING': False,

    # Prevent auto auth from creating superusers or modifying existing users
    'RESTRICT_AUTOMATIC_AUTH': True,

    'PREVIEW_LMS_BASE': "preview.localhost:18000",
    'ENABLE_GRADE_DOWNLOADS': True,
    'ENABLE_MKTG_SITE': False,
    'ENABLE_DISCUSSION_HOME_PANEL': True,
    'ENABLE_CORS_HEADERS': False,
    'ENABLE_CROSS_DOMAIN_CSRF_COOKIE': False,
    'ENABLE_COUNTRY_ACCESS': False,
    'ENABLE_CREDIT_API': False,
    'ENABLE_OAUTH2_PROVIDER': False,
    'ENABLE_MOBILE_REST_API': False,
    'CUSTOM_COURSES_EDX': False,
    'ENABLE_READING_FROM_MULTIPLE_HISTORY_TABLES': True,
    'SHOW_FOOTER_LANGUAGE_SELECTOR': False,
    'ENABLE_ENROLLMENT_RESET': False,
    # Settings for course import olx validation
    'ENABLE_COURSE_OLX_VALIDATION': False,
    # .. toggle_name: FEATURES['DISABLE_MOBILE_COURSE_AVAILABLE']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Set to True to remove Mobile Course Available UI Flag from Studio's Advanced Settings
    #   page else Mobile Course Available UI Flag will be available on Studio side.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2020-02-14
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/23073
    'DISABLE_MOBILE_COURSE_AVAILABLE': False,

    # .. toggle_name: FEATURES['ENABLE_CHANGE_USER_PASSWORD_ADMIN']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Set to True to enable changing a user password through django admin. This is disabled by
    #   default because enabling allows a method to bypass password policy.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2020-02-21
    # .. toggle_tickets: 'https://github.com/openedx/edx-platform/pull/21616'
    'ENABLE_CHANGE_USER_PASSWORD_ADMIN': False,

    ### ORA Feature Flags ###
    # .. toggle_name: FEATURES['ENABLE_ORA_ALL_FILE_URLS']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: A "work-around" feature toggle meant to help in cases where some file uploads are not
    #   discoverable. If enabled, will iterate through all possible file key suffixes up to the max for displaying file
    #   metadata in staff assessments.
    # .. toggle_use_cases: temporary
    # .. toggle_creation_date: 2020-03-03
    # .. toggle_target_removal_date: None
    # .. toggle_tickets: https://openedx.atlassian.net/browse/EDUCATOR-4951
    # .. toggle_warning: This temporary feature toggle does not have a target removal date.
    'ENABLE_ORA_ALL_FILE_URLS': False,

    # .. toggle_name: FEATURES['ENABLE_ORA_USER_STATE_UPLOAD_DATA']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: A "work-around" feature toggle meant to help in cases where some file uploads are not
    #   discoverable. If enabled, will pull file metadata from StudentModule.state for display in staff assessments.
    # .. toggle_use_cases: temporary
    # .. toggle_creation_date: 2020-03-03
    # .. toggle_target_removal_date: None
    # .. toggle_tickets: https://openedx.atlassian.net/browse/EDUCATOR-4951
    # .. toggle_warning: This temporary feature toggle does not have a target removal date.
    'ENABLE_ORA_USER_STATE_UPLOAD_DATA': False,

    # .. toggle_name: FEATURES['DEPRECATE_OLD_COURSE_KEYS_IN_STUDIO']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: Warn about removing support for deprecated course keys.
    #      To enable, set to True.
    #      To disable, set to False.
    #      To enable with a custom support deadline, set to an ISO-8601 date string:
    #        eg: '2020-09-01'
    # .. toggle_use_cases: temporary
    # .. toggle_creation_date: 2020-06-12
    # .. toggle_target_removal_date: 2021-04-01
    # .. toggle_warning: This can be removed once support is removed for deprecated
    #   course keys.
    # .. toggle_tickets: https://openedx.atlassian.net/browse/DEPR-58
    'DEPRECATE_OLD_COURSE_KEYS_IN_STUDIO': True,

    # .. toggle_name: FEATURES['DISABLE_COURSE_CREATION']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: If set to True, it disables the course creation functionality and hides the "New Course"
    #   button in studio.
    #   It is important to note that the value of this flag only affects if the user doesn't have a staff role,
    #   otherwise the course creation functionality will work as it should.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2013-12-02
    # .. toggle_warning: Another toggle DISABLE_LIBRARY_CREATION overrides DISABLE_COURSE_CREATION, if present.
    'DISABLE_COURSE_CREATION': False,

    # Can be turned off to disable the help link in the navbar
    # .. toggle_name: FEATURES['ENABLE_HELP_LINK']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: When True, a help link is displayed on the main navbar. Set False to hide it.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2021-03-05
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/26106
    'ENABLE_HELP_LINK': True,

    # .. toggle_name: FEATURES['ENABLE_INTEGRITY_SIGNATURE']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Whether to replace ID verification course/certificate requirement
    # with an in-course Honor Code agreement
    # (https://github.com/edx/edx-name-affirmation)
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2022-02-15
    # .. toggle_target_removal_date: None
    # .. toggle_tickets: 'https://openedx.atlassian.net/browse/MST-1348'
    'ENABLE_INTEGRITY_SIGNATURE': False,

    # .. toggle_name: FEATURES['ENABLE_LTI_PII_ACKNOWLEDGEMENT']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Enables the lti pii acknowledgement feature for a course
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2023-10
    # .. toggle_target_removal_date: None
    # .. toggle_tickets: 'https://2u-internal.atlassian.net/browse/MST-2055'
    'ENABLE_LTI_PII_ACKNOWLEDGEMENT': False,

    # .. toggle_name: MARK_LIBRARY_CONTENT_BLOCK_COMPLETE_ON_VIEW
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: If enabled, the Library Content Block is marked as complete when users view it.
    #   Otherwise (by default), all children of this block must be completed.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2022-03-22
    # .. toggle_target_removal_date: None
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/28268
    # .. toggle_warning: For consistency in user-experience, keep the value in sync with the setting of the same name
    #   in the LMS and CMS.
    'MARK_LIBRARY_CONTENT_BLOCK_COMPLETE_ON_VIEW': False,

    # .. toggle_name: FEATURES['DISABLE_UNENROLLMENT']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Set to True to disable self-unenrollments via REST API.
    #   This also hides the "Unenroll" button on the Learner Dashboard.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2021-10-11
    # .. toggle_warning: For consistency in user experience, keep the value in sync with the setting of the same name
    #   in the LMS and CMS.
    # .. toggle_tickets: 'https://github.com/open-craft/edx-platform/pull/429'
    'DISABLE_UNENROLLMENT': False,

    # .. toggle_name: FEATURES['DISABLE_ADVANCED_SETTINGS']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Set to `True` to disable the advanced settings page in Studio for all users except those
    #   having `is_superuser` or `is_staff` set to `True`.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2023-03-31
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/32015
    'DISABLE_ADVANCED_SETTINGS': False,

    # .. toggle_name: FEATURES['ENABLE_SEND_XBLOCK_LIFECYCLE_EVENTS_OVER_BUS']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Enables sending xblock lifecycle events over the event bus. Used to create the
    #   EVENT_BUS_PRODUCER_CONFIG setting
    # .. toggle_use_cases: opt_in
    # .. toggle_creation_date: 2023-10-10
    # .. toggle_target_removal_date: 2023-10-12
    # .. toggle_warning: The default may be changed in a later release. See
    #   https://github.com/openedx/openedx-events/issues/265
    # .. toggle_tickets: https://github.com/edx/edx-arch-experiments/issues/381
    'ENABLE_SEND_XBLOCK_LIFECYCLE_EVENTS_OVER_BUS': False,

    # .. toggle_name: FEATURES['ENABLE_HIDE_FROM_TOC_UI']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: When enabled, exposes hide_from_toc xblock attribute so course authors can configure it as
    #  a section visibility option in Studio.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2024-02-29
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/33952
    'ENABLE_HIDE_FROM_TOC_UI': False,

    # .. toggle_name: FEATURES['ENABLE_GRADING_METHOD_IN_PROBLEMS']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Enables the grading method feature in capa problems.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2024-03-22
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/33911
    'ENABLE_GRADING_METHOD_IN_PROBLEMS': False,

    # .. toggle_name: FEATURES['BADGES_ENABLED']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Set to True to enable the Badges feature.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2024-04-10
    'BADGES_ENABLED': False,

    # .. toggle_name: FEATURES['IN_CONTEXT_DISCUSSION_ENABLED_DEFAULT']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: Set to False to disable in-context discussion for units by default.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2024-09-02
    'IN_CONTEXT_DISCUSSION_ENABLED_DEFAULT': True,
}

# .. toggle_name: ENABLE_COPPA_COMPLIANCE
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When True, inforces COPPA compliance and removes YOB field from registration form and accounnt
# .. settings page. Also hide YOB banner from profile page.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2021-10-27
# .. toggle_tickets: 'https://openedx.atlassian.net/browse/VAN-622'
ENABLE_COPPA_COMPLIANCE = False

ENABLE_JASMINE = False

MARKETING_EMAILS_OPT_IN = False

# List of logout URIs for each IDA that the learner should be logged out of when they logout of the LMS. Only applies to
# IDA for which the social auth flow uses DOT (Django OAuth Toolkit).
IDA_LOGOUT_URI_LIST = []

############################# MICROFRONTENDS ###################################
COURSE_AUTHORING_MICROFRONTEND_URL = None
DISCUSSIONS_MICROFRONTEND_URL = None
DISCUSSIONS_MFE_FEEDBACK_URL = None
# .. toggle_name: ENABLE_AUTHN_RESET_PASSWORD_HIBP_POLICY
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When enabled, this toggle activates the use of the password validation
#   HIBP Policy.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2021-12-03
# .. toggle_tickets: https://openedx.atlassian.net/browse/VAN-666
ENABLE_AUTHN_RESET_PASSWORD_HIBP_POLICY = False
# .. toggle_name: ENABLE_AUTHN_REGISTER_HIBP_POLICY
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When enabled, this toggle activates the use of the password validation
#   HIBP Policy on Authn MFE's registration.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2022-03-25
# .. toggle_tickets: https://openedx.atlassian.net/browse/VAN-669
ENABLE_AUTHN_REGISTER_HIBP_POLICY = False
HIBP_REGISTRATION_PASSWORD_FREQUENCY_THRESHOLD = 3

# .. toggle_name: ENABLE_AUTHN_LOGIN_NUDGE_HIBP_POLICY
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When enabled, this toggle activates the use of the password validation
#   on Authn MFE's login.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2022-03-29
# .. toggle_target_removal_date: None
# .. toggle_tickets: https://openedx.atlassian.net/browse/VAN-668
ENABLE_AUTHN_LOGIN_NUDGE_HIBP_POLICY = False
HIBP_LOGIN_NUDGE_PASSWORD_FREQUENCY_THRESHOLD = 3

# .. toggle_name: ENABLE_AUTHN_LOGIN_BLOCK_HIBP_POLICY
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When enabled, this toggle activates the use of the password validation
#   on Authn MFE's login.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2022-03-29
# .. toggle_target_removal_date: None
# .. toggle_tickets: https://openedx.atlassian.net/browse/VAN-667
ENABLE_AUTHN_LOGIN_BLOCK_HIBP_POLICY = False
HIBP_LOGIN_BLOCK_PASSWORD_FREQUENCY_THRESHOLD = 5

# .. toggle_name: ENABLE_DYNAMIC_REGISTRATION_FIELDS
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When enabled, this toggle adds fields configured in
# REGISTRATION_EXTRA_FIELDS to Authn MFE
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2022-04-21
# .. toggle_target_removal_date: None
# .. toggle_tickets: https://openedx.atlassian.net/browse/VAN-838
ENABLE_DYNAMIC_REGISTRATION_FIELDS = False

############################# SOCIAL MEDIA SHARING #############################
SOCIAL_SHARING_SETTINGS = {
    # Note: Ensure 'CUSTOM_COURSE_URLS' has a matching value in lms/envs/common.py
    'CUSTOM_COURSE_URLS': False,
    'DASHBOARD_FACEBOOK': False,
    'CERTIFICATE_FACEBOOK': False,
    'CERTIFICATE_TWITTER': False,
    'DASHBOARD_TWITTER': False
}

############################# SET PATH INFORMATION #############################
PROJECT_ROOT = path(__file__).abspath().dirname().dirname()  # /edx-platform/cms
REPO_ROOT = PROJECT_ROOT.dirname()
COMMON_ROOT = REPO_ROOT / "common"
OPENEDX_ROOT = REPO_ROOT / "openedx"
CMS_ROOT = REPO_ROOT / "cms"
LMS_ROOT = REPO_ROOT / "lms"
ENV_ROOT = REPO_ROOT.dirname()  # virtualenv dir /edx-platform is in
COURSES_ROOT = ENV_ROOT / "data"
XMODULE_ROOT = REPO_ROOT / "xmodule"

GITHUB_REPO_ROOT = ENV_ROOT / "data"

# For geolocation ip database
GEOIP_PATH = REPO_ROOT / "common/static/data/geoip/GeoLite2-Country.mmdb"

DATA_DIR = COURSES_ROOT

DJFS = {
    'type': 'osfs',
    'directory_root': '/edx/var/edxapp/django-pyfs/static/django-pyfs',
    'url_root': '/static/django-pyfs',
}
######################## BRANCH.IO ###########################
BRANCH_IO_KEY = ''

######################## OPTIMIZELY ###########################
OPTIMIZELY_PROJECT_ID = None
OPTIMIZELY_FULLSTACK_SDK_KEY = None

######################## GOOGLE ANALYTICS ###########################
GOOGLE_ANALYTICS_ACCOUNT = None

######################## HOTJAR ###########################
HOTJAR_ID = 00000

############################# TEMPLATE CONFIGURATION #############################
# Mako templating
import tempfile
MAKO_MODULE_DIR = os.path.join(tempfile.gettempdir(), 'mako_cms')
MAKO_TEMPLATE_DIRS_BASE = [
    PROJECT_ROOT / 'templates',
    COMMON_ROOT / 'templates',
    COMMON_ROOT / 'djangoapps' / 'pipeline_mako' / 'templates',
    COMMON_ROOT / 'static',  # required to statically include common Underscore templates
    OPENEDX_ROOT / 'core' / 'djangoapps' / 'cors_csrf' / 'templates',
    OPENEDX_ROOT / 'core' / 'djangoapps' / 'dark_lang' / 'templates',
    OPENEDX_ROOT / 'core' / 'lib' / 'license' / 'templates',
    CMS_ROOT / 'djangoapps' / 'pipeline_js' / 'templates',
]

CONTEXT_PROCESSORS = (
    'django.template.context_processors.request',
    'django.template.context_processors.static',
    'django.contrib.messages.context_processors.messages',
    'django.template.context_processors.i18n',
    'django.contrib.auth.context_processors.auth',  # this is required for admin
    'django.template.context_processors.csrf',
    'help_tokens.context_processor',
    'openedx.core.djangoapps.site_configuration.context_processors.configuration_context',
)

# Django templating
TEMPLATES = [
    {
        'NAME': 'django',
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        # Don't look for template source files inside installed applications.
        'APP_DIRS': False,
        # Instead, look for template source files in these dirs.
        'DIRS': Derived(_make_mako_template_dirs),
        # Options specific to this backend.
        'OPTIONS': {
            'loaders': (
                # We have to use mako-aware template loaders to be able to include
                # mako templates inside django templates (such as main_django.html).
                'openedx.core.djangoapps.theming.template_loaders.ThemeTemplateLoader',
                'common.djangoapps.edxmako.makoloader.MakoFilesystemLoader',
                'common.djangoapps.edxmako.makoloader.MakoAppDirectoriesLoader',
            ),
            'context_processors': CONTEXT_PROCESSORS,
            # Change 'debug' in your environment settings files - not here.
            'debug': False
        }
    },
    {
        'NAME': 'mako',
        'BACKEND': 'common.djangoapps.edxmako.backend.Mako',
        'APP_DIRS': False,
        'DIRS': Derived(_make_mako_template_dirs),
        'OPTIONS': {
            'context_processors': CONTEXT_PROCESSORS,
            'debug': False,
        }
    },
    {
        # This separate copy of the Mako backend is used to render previews using the LMS templates
        'NAME': 'preview',
        'BACKEND': 'common.djangoapps.edxmako.backend.Mako',
        'APP_DIRS': False,
        'DIRS': lms.envs.common.MAKO_TEMPLATE_DIRS_BASE,
        'OPTIONS': {
            'context_processors': CONTEXT_PROCESSORS,
            'debug': False,
            'namespace': 'lms.main',
        }
    },
]
DEFAULT_TEMPLATE_ENGINE = TEMPLATES[0]

#################################### AWS #######################################
AWS_SES_REGION_NAME = 'us-east-1'
AWS_SES_REGION_ENDPOINT = 'email.us-east-1.amazonaws.com'
AWS_ACCESS_KEY_ID = None
AWS_SECRET_ACCESS_KEY = None
AWS_SECURITY_TOKEN = None
AWS_QUERYSTRING_AUTH = False
AWS_STORAGE_BUCKET_NAME = 'SET-ME-PLEASE (ex. bucket-name)'
AWS_S3_CUSTOM_DOMAIN = 'SET-ME-PLEASE (ex. bucket-name.s3.amazonaws.com)'

##############################################################################

EDX_ROOT_URL = ''

# use the ratelimit backend to prevent brute force attacks
AUTHENTICATION_BACKENDS = [
    'auth_backends.backends.EdXOAuth2',
    'rules.permissions.ObjectPermissionBackend',
    'openedx.core.djangoapps.content_libraries.auth.LtiAuthenticationBackend',
    'django.contrib.auth.backends.AllowAllUsersModelBackend',
    'bridgekeeper.backends.RulePermissionBackend',
]

STATIC_ROOT_BASE = '/edx/var/edxapp/staticfiles'

# License for serving content in China
ICP_LICENSE = None
ICP_LICENSE_INFO = {}

LOGGING_ENV = 'sandbox'

LMS_BASE = 'localhost:18000'
LMS_ROOT_URL = "https://localhost:18000"
LMS_INTERNAL_ROOT_URL = LMS_ROOT_URL

# Use LMS SSO for login, once enabled by setting LOGIN_URL (see docs/guides/studio_oauth.rst)
SOCIAL_AUTH_STRATEGY = 'auth_backends.strategies.EdxDjangoStrategy'
LOGIN_REDIRECT_URL = EDX_ROOT_URL + '/home/'
LOGIN_URL = '/login/'
FRONTEND_LOGIN_URL = LOGIN_URL
# Warning: Must have trailing slash to activate correct logout view
# (auth_backends, not LMS user_authn)
FRONTEND_LOGOUT_URL = '/logout/'
FRONTEND_REGISTER_URL = Derived(lambda settings: settings.LMS_ROOT_URL + '/register')

LMS_ENROLLMENT_API_PATH = "/api/enrollment/v1/"
ENTERPRISE_API_URL = LMS_INTERNAL_ROOT_URL + '/enterprise/api/v1/'
ENTERPRISE_CONSENT_API_URL = LMS_INTERNAL_ROOT_URL + '/consent/api/v1/'
ENTERPRISE_MARKETING_FOOTER_QUERY_PARAMS = {}

# Setting for Open API key and prompts used by edx-enterprise.
CHAT_COMPLETION_API = 'https://example.com/chat/completion'
CHAT_COMPLETION_API_KEY = 'i am a key'
LEARNER_ENGAGEMENT_PROMPT_FOR_ACTIVE_CONTRACT = ''
LEARNER_ENGAGEMENT_PROMPT_FOR_NON_ACTIVE_CONTRACT = ''
LEARNER_PROGRESS_PROMPT_FOR_ACTIVE_CONTRACT = ''
LEARNER_PROGRESS_PROMPT_FOR_NON_ACTIVE_CONTRACT = ''

# Public domain name of Studio (should be resolvable from the end-user's browser)
CMS_BASE = 'localhost:18010'
CMS_ROOT_URL = "https://localhost:18010"

LOG_DIR = '/edx/var/log/edx'

LOCAL_LOGLEVEL = "INFO"

MAINTENANCE_BANNER_TEXT = 'Sample banner message'

WIKI_ENABLED = True

CERT_QUEUE = 'certificates'
# List of logout URIs for each IDA that the learner should be logged out of when they logout of
# Studio. Only applies to IDA for which the social auth flow uses DOT (Django OAuth Toolkit).
IDA_LOGOUT_URI_LIST = []

ELASTIC_SEARCH_CONFIG = [
    {
        'use_ssl': False,
        'host': 'localhost',
        'port': 9200
    }
]

# These are standard regexes for pulling out info like course_ids, usage_ids, etc.
# They are used so that URLs with deprecated-format strings still work.
from lms.envs.common import (
    COURSE_KEY_PATTERN, COURSE_KEY_REGEX, COURSE_ID_PATTERN, USAGE_KEY_PATTERN, ASSET_KEY_PATTERN
)

######################### CSRF #########################################

# Forwards-compatibility with Django 1.7
CSRF_COOKIE_AGE = 60 * 60 * 24 * 7 * 52
# It is highly recommended that you override this in any environment accessed by
# end users
CSRF_COOKIE_SECURE = False

CROSS_DOMAIN_CSRF_COOKIE_DOMAIN = ''
CROSS_DOMAIN_CSRF_COOKIE_NAME = ''
CSRF_TRUSTED_ORIGINS = []
CSRF_TRUSTED_ORIGINS_WITH_SCHEME = []

#################### CAPA External Code Evaluation #############################
XQUEUE_WAITTIME_BETWEEN_REQUESTS = 5  # seconds
XQUEUE_INTERFACE = {
    'url': 'http://localhost:18040',
    'basic_auth': ['edx', 'edx'],
    'django_auth': {
        'username': 'lms',
        'password': 'password'
    }
}

################################# Middleware ###################################

MIDDLEWARE = [
    'openedx.core.lib.x_forwarded_for.middleware.XForwardedForMiddleware',
    'edx_django_utils.security.csp.middleware.content_security_policy_middleware',

    'crum.CurrentRequestUserMiddleware',

    # Resets the request cache.
    'edx_django_utils.cache.middleware.RequestCacheMiddleware',

    # Various monitoring middleware
    'edx_django_utils.monitoring.CookieMonitoringMiddleware',
    'edx_django_utils.monitoring.DeploymentMonitoringMiddleware',
    'edx_django_utils.monitoring.FrontendMonitoringMiddleware',
    'edx_django_utils.monitoring.MonitoringMemoryMiddleware',

    # Before anything that looks at cookies, especially the session middleware
    'openedx.core.djangoapps.cookie_metadata.middleware.CookieNameChange',

    'openedx.core.djangoapps.header_control.middleware.HeaderControlMiddleware',
    'django.middleware.cache.UpdateCacheMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.contrib.sites.middleware.CurrentSiteMiddleware',

    # CORS and CSRF
    'django.middleware.csrf.CsrfViewMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'openedx.core.djangoapps.cors_csrf.middleware.CorsCSRFMiddleware',
    'openedx.core.djangoapps.cors_csrf.middleware.CsrfCrossDomainCookieMiddleware',

    # JWT auth
    'edx_rest_framework_extensions.auth.jwt.middleware.JwtAuthCookieMiddleware',

    # Allows us to define redirects via Django admin
    'django_sites_extensions.middleware.RedirectMiddleware',

    # Instead of SessionMiddleware, we use a more secure version
    # 'django.contrib.sessions.middleware.SessionMiddleware',
    'openedx.core.djangoapps.safe_sessions.middleware.SafeSessionMiddleware',

    'method_override.middleware.MethodOverrideMiddleware',

    # Instead of AuthenticationMiddleware, we use a cache-backed version
    'openedx.core.djangoapps.cache_toolbox.middleware.CacheBackedAuthenticationMiddleware',

    'common.djangoapps.student.middleware.UserStandingMiddleware',

    'django.contrib.messages.middleware.MessageMiddleware',
    'common.djangoapps.track.middleware.TrackMiddleware',

    # This is used to set or update the user language preferences.
    'openedx.core.djangoapps.lang_pref.middleware.LanguagePreferenceMiddleware',

    # Allows us to dark-launch particular languages
    'openedx.core.djangoapps.dark_lang.middleware.DarkLangMiddleware',

    'openedx.core.djangoapps.embargo.middleware.EmbargoMiddleware',

    # Detects user-requested locale from 'accept-language' header in http request
    'django.middleware.locale.LocaleMiddleware',

    'codejail.django_integration.ConfigureCodeJailMiddleware',

    # for expiring inactive sessions
    'openedx.core.djangoapps.session_inactivity_timeout.middleware.SessionInactivityTimeout',

    'openedx.core.djangoapps.theming.middleware.CurrentSiteThemeMiddleware',

    # use Django built in clickjacking protection
    'django.middleware.clickjacking.XFrameOptionsMiddleware',

    'waffle.middleware.WaffleMiddleware',

    # Enables force_django_cache_miss functionality for TieredCache.
    'edx_django_utils.cache.middleware.TieredCacheMiddleware',

    # Adds monitoring attributes to requests.
    'edx_rest_framework_extensions.middleware.RequestCustomAttributesMiddleware',

    'edx_rest_framework_extensions.auth.jwt.middleware.EnsureJWTAuthSettingsMiddleware',

    # Handles automatically storing user ids in django-simple-history tables when possible.
    'simple_history.middleware.HistoryRequestMiddleware',

    # This must be last so that it runs first in the process_response chain
    'openedx.core.djangoapps.site_configuration.middleware.SessionCookieDomainOverrideMiddleware',
]

EXTRA_MIDDLEWARE_CLASSES = []

# Clickjacking protection can be disabled by setting this to 'ALLOW'
X_FRAME_OPTIONS = 'DENY'

# Platform for Privacy Preferences header
P3P_HEADER = 'CP="Open EdX does not have a P3P policy."'

############# XBlock Configuration ##########

# Import after sys.path fixup
from xmodule.modulestore.inheritance import InheritanceMixin
from xmodule.x_module import XModuleMixin

# These are the Mixins that will be added to every Blocklike upon instantiation.
# DO NOT EXPAND THIS LIST!! We want it eventually to be EMPTY. Why? Because dynamically adding functions/behaviors to
# objects at runtime is confusing for both developers and static tooling (pylint/mypy). Instead...
#  - to add special Blocklike behaviors just for your site: override `XBLOCK_EXTRA_MIXINS` with your own XBlockMixins.
#  - to add new functionality to all Blocklikes: add it to the base Blocklike class in the core openedx/XBlock repo.
XBLOCK_MIXINS = (
    # TODO: For each of these, either
    #  (a) merge their functionality into the base Blocklike class, or
    #  (b) refactor their functionality out of the Blocklike objects and into the edx-platform block runtimes.
    LmsBlockMixin,
    InheritanceMixin,
    XModuleMixin,
    EditInfoMixin,
    AuthoringMixin,
    UpstreamSyncMixin,
)

# .. setting_name: XBLOCK_EXTRA_MIXINS
# .. setting_default: ()
# .. setting_description: Custom mixins that will be dynamically added to every XBlock and XBlockAside instance.
#     These can be classes or dotted-path references to classes.
#     For example: `XBLOCK_EXTRA_MIXINS = ('my_custom_package.my_module.MyCustomMixin',)`
XBLOCK_EXTRA_MIXINS = ()

# Paths to wrapper methods which should be applied to every XBlock's FieldData.
XBLOCK_FIELD_DATA_WRAPPERS = ()

# .. setting_name: XBLOCK_RUNTIME_V2_EPHEMERAL_DATA_CACHE
# .. setting_default: default
# .. setting_description: The django cache key of the cache to use for storing anonymous user state for XBlocks.
XBLOCK_RUNTIME_V2_EPHEMERAL_DATA_CACHE = 'default'

############################ ORA 2 ############################################

# By default, don't use a file prefix
ORA2_FILE_PREFIX = 'default_env-default_deployment/ora2'

# Default File Upload Storage bucket and prefix. Used by the FileUpload Service.
FILE_UPLOAD_STORAGE_BUCKET_NAME = 'SET-ME-PLEASE (ex. bucket-name)'
FILE_UPLOAD_STORAGE_PREFIX = 'submissions_attachments'

############################ Modulestore Configuration ################################

DOC_STORE_CONFIG = {
    'db': 'edxapp',
    'host': 'localhost',
    'replicaSet': '',
    'user': 'edxapp',
    'port': 27017,
    'collection': 'modulestore',
    'ssl': False,
    # https://api.mongodb.com/python/2.9.1/api/pymongo/mongo_client.html#module-pymongo.mongo_client
    # default is never timeout while the connection is open,
    #this means it needs to explicitly close raising pymongo.errors.NetworkTimeout
    'socketTimeoutMS': 6000,
    'connectTimeoutMS': 2000,  # default is 20000, I believe raises pymongo.errors.ConnectionFailure
    # Not setting waitQueueTimeoutMS and waitQueueMultiple since pymongo defaults to nobody being allowed to wait
    'auth_source': None,
    'read_preference': 'PRIMARY'
    # If 'asset_collection' defined, it'll be used
    # as the collection name for asset metadata.
    # Otherwise, a default collection name will be used.
}

CONTENTSTORE = {
    'ENGINE': 'xmodule.contentstore.mongo.MongoContentStore',
    # connection strings are duplicated temporarily for
    # backward compatibility
    'OPTIONS': {
        'db': 'edxapp',
        'host': 'localhost',
        'password': 'password',
        'port': 27017,
        'user': 'edxapp',
        'ssl': False,
        'auth_source': None
    },
    'ADDITIONAL_OPTIONS': {},
    'DOC_STORE_CONFIG': DOC_STORE_CONFIG
}

MODULESTORE_BRANCH = 'draft-preferred'

MODULESTORE = {
    'default': {
        'ENGINE': 'xmodule.modulestore.mixed.MixedModuleStore',
        'OPTIONS': {
            'mappings': {},
            'stores': [
                {
                    'NAME': 'split',
                    'ENGINE': 'xmodule.modulestore.split_mongo.split_draft.DraftVersioningModuleStore',
                    'DOC_STORE_CONFIG': DOC_STORE_CONFIG,
                    'OPTIONS': {
                        'default_class': 'xmodule.hidden_block.HiddenBlock',
                        'fs_root': DATA_DIR,
                        'render_template': 'common.djangoapps.edxmako.shortcuts.render_to_string',
                    }
                },
                {
                    'NAME': 'draft',
                    'ENGINE': 'xmodule.modulestore.mongo.DraftMongoModuleStore',
                    'DOC_STORE_CONFIG': DOC_STORE_CONFIG,
                    'OPTIONS': {
                        'default_class': 'xmodule.hidden_block.HiddenBlock',
                        'fs_root': DATA_DIR,
                        'render_template': 'common.djangoapps.edxmako.shortcuts.render_to_string',
                    }
                }
            ]
        }
    }
}

# Modulestore-level field override providers. These field override providers don't
# require student context.
MODULESTORE_FIELD_OVERRIDE_PROVIDERS = ()

DATABASES = {
    # edxapp's edxapp-migrate scripts and the edxapp_migrate play
    # will ensure that any DB not named read_replica will be migrated
    # for both the lms and cms.
    'default': {
        'ATOMIC_REQUESTS': True,
        'CONN_MAX_AGE': 0,
        'ENGINE': 'django.db.backends.mysql',
        'HOST': '127.0.0.1',
        'NAME': 'edxapp',
        'OPTIONS': {},
        'PASSWORD': 'password',
        'PORT': '3306',
        'USER': 'edxapp001'
    },
    'read_replica': {
        'CONN_MAX_AGE': 0,
        'ENGINE': 'django.db.backends.mysql',
        'HOST': '127.0.0.1',
        'NAME': 'edxapp',
        'OPTIONS': {},
        'PASSWORD': 'password',
        'PORT': '3306',
        'USER': 'edxapp001'
    },
    'student_module_history': {
        'CONN_MAX_AGE': 0,
        'ENGINE': 'django.db.backends.mysql',
        'HOST': '127.0.0.1',
        'NAME': 'edxapp_csmh',
        'OPTIONS': {},
        'PASSWORD': 'password',
        'PORT': '3306',
        'USER': 'edxapp001'
    }
}

DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'
DEFAULT_HASHING_ALGORITHM = 'sha256'

#################### Python sandbox ############################################

CODE_JAIL = {
    # from https://github.com/openedx/codejail/blob/master/codejail/django_integration.py#L24, '' should be same as None
    'python_bin': '/edx/app/edxapp/venvs/edxapp-sandbox/bin/python',
    # User to run as in the sandbox.
    'user': 'sandbox',

    # Configurable limits.
    'limits': {
        # How many CPU seconds can jailed code use?
        'CPU': 1,
        # Limit the memory of the jailed process to something high but not
        # infinite (512MiB in bytes)
        'VMEM': 536870912,
        # Time in seconds that the jailed process has to run.
        'REALTIME': 3,
        'PROXY': 0,
        # Needs to be non-zero so that jailed code can use it as their temp directory.(1MiB in bytes)
        'FSIZE': 1048576,
    },

    # Overrides to default configurable 'limits' (above).
    # Keys should be course run ids.
    # Values should be dictionaries that look like 'limits'.
    "limit_overrides": {},
}

# Some courses are allowed to run unsafe code. This is a list of regexes, one
# of them must match the course id for that course to run unsafe code.
#
# For example:
#
#   COURSES_WITH_UNSAFE_CODE = [
#       r"Harvard/XY123.1/.*"
#   ]

COURSES_WITH_UNSAFE_CODE = []

# Cojail REST service
ENABLE_CODEJAIL_REST_SERVICE = False
# .. setting_name: CODE_JAIL_REST_SERVICE_REMOTE_EXEC
# .. setting_default: 'xmodule.capa.safe_exec.remote_exec.send_safe_exec_request_v0'
# .. setting_description: Set the python package.module.function that is reponsible of
#   calling the remote service in charge of jailed code execution
CODE_JAIL_REST_SERVICE_REMOTE_EXEC = 'xmodule.capa.safe_exec.remote_exec.send_safe_exec_request_v0'
# .. setting_name: CODE_JAIL_REST_SERVICE_HOST
# .. setting_default: 'http://127.0.0.1:8550'
# .. setting_description: Set the codejail remote service host
CODE_JAIL_REST_SERVICE_HOST = 'http://127.0.0.1:8550'
# .. setting_name: CODE_JAIL_REST_SERVICE_CONNECT_TIMEOUT
# .. setting_default: 0.5
# .. setting_description: Set the number of seconds CMS will wait to establish an internal
#   connection to the codejail remote service.
CODE_JAIL_REST_SERVICE_CONNECT_TIMEOUT = 0.5  # time in seconds
# .. setting_name: CODE_JAIL_REST_SERVICE_READ_TIMEOUT
# .. setting_default: 3.5
# .. setting_description: Set the number of seconds CMS will wait for a response from the
#   codejail remote service endpoint.
CODE_JAIL_REST_SERVICE_READ_TIMEOUT = 3.5  # time in seconds

############################ DJANGO_BUILTINS ################################
# Change DEBUG in your environment settings files, not here
DEBUG = False
SESSION_COOKIE_SECURE = False
SESSION_SAVE_EVERY_REQUEST = False
SESSION_SERIALIZER = 'openedx.core.lib.session_serializers.PickleSerializer'
SESSION_COOKIE_DOMAIN = ""
SESSION_COOKIE_NAME = 'sessionid'

# This is the domain that is used to set shared cookies between various sub-domains.
SHARED_COOKIE_DOMAIN = ""

# Site info
SITE_NAME = "localhost"
HTTPS = 'on'
ROOT_URLCONF = 'cms.urls'

COURSE_IMPORT_EXPORT_BUCKET = ''
COURSE_METADATA_EXPORT_BUCKET = ''

ALTERNATE_WORKER_QUEUES = 'lms'

STATIC_URL_BASE = '/static/'

X_FRAME_OPTIONS = 'DENY'

# .. setting_name: GIT_REPO_EXPORT_DIR
# .. setting_default: '/edx/var/edxapp/export_course_repos'
# .. setting_description: When courses are exported to git, either with the export_git management command or the git
#   export view from the studio (when FEATURES['ENABLE_EXPORT_GIT'] is True), they are stored in this directory, which
#   must exist at the time of the export.
GIT_REPO_EXPORT_DIR = '/edx/var/edxapp/export_course_repos'
# .. setting_name: GIT_EXPORT_DEFAULT_IDENT
# .. setting_default: {'name': 'STUDIO_EXPORT_TO_GIT', 'email': 'STUDIO_EXPORT_TO_GIT@example.com'}
# .. setting_description: When courses are exported to git, commits are signed with this name/email git identity.
GIT_EXPORT_DEFAULT_IDENT = {
    'name': 'STUDIO_EXPORT_TO_GIT',
    'email': 'STUDIO_EXPORT_TO_GIT@example.com'
}

# Email
TECH_SUPPORT_EMAIL = 'technical@example.com'
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'localhost'
EMAIL_PORT = 25
EMAIL_USE_TLS = False
EMAIL_HOST_USER = ''
EMAIL_HOST_PASSWORD = ''
DEFAULT_FROM_EMAIL = 'registration@example.com'
DEFAULT_FEEDBACK_EMAIL = 'feedback@example.com'
TECH_SUPPORT_EMAIL = 'technical@example.com'
CONTACT_EMAIL = 'info@example.com'
BUGS_EMAIL = 'bugs@example.com'
SERVER_EMAIL = 'devops@example.com'
UNIVERSITY_EMAIL = 'university@example.com'
PRESS_EMAIL = 'press@example.com'
ADMINS = []
MANAGERS = ADMINS

# Initialize to 'release', but read from JSON in production.py
EDX_PLATFORM_REVISION = 'release'

# Static content
STATIC_URL = '/static/studio/'
STATIC_ROOT = os.environ.get('STATIC_ROOT_CMS', ENV_ROOT / 'staticfiles' / 'studio')

STATICFILES_DIRS = [
    COMMON_ROOT / "static",
    PROJECT_ROOT / "static",
    # Temporarily adding the following static path as we are migrating the built-in blocks' Sass to vanilla CSS.
    # Once all of the built-in blocks are extracted from edx-platform, we can remove this static path.
    # Relevant ticket: https://github.com/openedx/edx-platform/issues/35300
    XMODULE_ROOT / "static",
]

# Locale/Internationalization
CELERY_TIMEZONE = 'UTC'
TIME_ZONE = 'UTC'
LANGUAGE_CODE = 'en'  # http://www.i18nguy.com/unicode/language-identifiers.html
LANGUAGES_BIDI = lms.envs.common.LANGUAGES_BIDI

LANGUAGE_COOKIE_NAME = lms.envs.common.LANGUAGE_COOKIE_NAME

LANGUAGES = lms.envs.common.LANGUAGES
LANGUAGE_DICT = dict(LANGUAGES)

# Languages supported for custom course certificate templates
CERTIFICATE_TEMPLATE_LANGUAGES = {
    'en': 'English',
    'es': 'Español',
}

USE_I18N = True
USE_L10N = True

STATICI18N_FILENAME_FUNCTION = 'statici18n.utils.legacy_filename'
STATICI18N_ROOT = PROJECT_ROOT / "static"

LOCALE_PATHS = Derived(_make_locale_paths)

# Messages
MESSAGE_STORAGE = 'django.contrib.messages.storage.session.SessionStorage'

COURSE_IMPORT_EXPORT_STORAGE = 'django.core.files.storage.FileSystemStorage'
COURSE_METADATA_EXPORT_STORAGE = 'django.core.files.storage.FileSystemStorage'


##### EMBARGO #####
EMBARGO_SITE_REDIRECT_URL = None

##### custom vendor plugin variables #####

############################### PIPELINE #######################################

PIPELINE = {
    'PIPELINE_ENABLED': True,
    # Don't use compression by default
    'CSS_COMPRESSOR': None,
    'JS_COMPRESSOR': None,
    # Don't wrap JavaScript as there is code that depends upon updating the global namespace
    'DISABLE_WRAPPER': True,
    # Specify the UglifyJS binary to use
    'UGLIFYJS_BINARY': 'node_modules/.bin/uglifyjs',
    'COMPILERS': (),
    'YUI_BINARY': 'yui-compressor',
}

STATICFILES_STORAGE = 'openedx.core.storage.ProductionStorage'
STATICFILES_STORAGE_KWARGS = {}

# List of finder classes that know how to find static files in various locations.
# Note: the pipeline finder is included to be able to discover optimized files
STATICFILES_FINDERS = [
    'openedx.core.djangoapps.theming.finders.ThemeFilesFinder',
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
    'openedx.core.lib.xblock_pipeline.finder.XBlockPipelineFinder',
    'pipeline.finders.PipelineFinder',
]

from openedx.core.lib.rooted_paths import rooted_glob

PIPELINE['STYLESHEETS'] = {
    'style-vendor': {
        'source_filenames': [
            'css/vendor/normalize.css',
            'css/vendor/font-awesome.css',
            'css/vendor/html5-input-polyfills/number-polyfill.css',
            'js/vendor/CodeMirror/codemirror.css',
            'css/vendor/ui-lightness/jquery-ui-1.8.22.custom.css',
            'css/vendor/jquery.qtip.min.css',
            'js/vendor/markitup/skins/simple/style.css',
            'js/vendor/markitup/sets/wiki/style.css',
        ],
        'output_filename': 'css/cms-style-vendor.css',
    },
    'style-vendor-tinymce-content': {
        'source_filenames': [
            'css/tinymce-studio-content-fonts.css',
            'js/vendor/tinymce/js/tinymce/skins/ui/studio-tmce5/content.min.css',
            'css/tinymce-studio-content.css'
        ],
        'output_filename': 'css/cms-style-vendor-tinymce-content.css',
    },
    'style-vendor-tinymce-skin': {
        'source_filenames': [
            'js/vendor/tinymce/js/tinymce/skins/ui/studio-tmce5/skin.min.css'
        ],
        'output_filename': 'css/cms-style-vendor-tinymce-skin.css',
    },
    'style-main-v1': {
        'source_filenames': [
            'css/studio-main-v1.css',
        ],
        'output_filename': 'css/studio-main-v1.css',
    },
    'style-main-v1-rtl': {
        'source_filenames': [
            'css/studio-main-v1-rtl.css',
        ],
        'output_filename': 'css/studio-main-v1-rtl.css',
    },
    'style-xmodule-annotations': {
        'source_filenames': [
            'css/vendor/ova/annotator.css',
            'css/vendor/ova/edx-annotator.css',
            'css/vendor/ova/video-js.min.css',
            'css/vendor/ova/rangeslider.css',
            'css/vendor/ova/share-annotator.css',
            'css/vendor/ova/richText-annotator.css',
            'css/vendor/ova/tags-annotator.css',
            'css/vendor/ova/flagging-annotator.css',
            'css/vendor/ova/diacritic-annotator.css',
            'css/vendor/ova/grouping-annotator.css',
            'css/vendor/ova/ova.css',
            'js/vendor/ova/catch/css/main.css'
        ],
        'output_filename': 'css/cms-style-xmodule-annotations.css',
    },
    'course-unit-mfe-iframe-bundle': {
        'source_filenames': [
            'css/course-unit-mfe-iframe-bundle.css',
        ],
        'output_filename': 'css/course-unit-mfe-iframe-bundle.css',
    },
}

base_vendor_js = [
    'js/src/utility.js',
    'js/src/logger.js',
    'common/js/vendor/jquery.js',
    'common/js/vendor/jquery-migrate.js',
    'js/vendor/jquery.cookie.js',
    'js/vendor/url.min.js',
    'common/js/vendor/underscore.js',
    'common/js/vendor/underscore.string.js',
    'common/js/vendor/backbone.js',
    'js/vendor/URI.min.js',

    # Make some edX UI Toolkit utilities available in the global "edx" namespace
    'edx-ui-toolkit/js/utils/global-loader.js',
    'edx-ui-toolkit/js/utils/string-utils.js',
    'edx-ui-toolkit/js/utils/html-utils.js',

    # Here we were loading Bootstrap and supporting libraries, but it no longer seems to be needed for any Studio UI.
    # 'common/js/vendor/bootstrap.bundle.js',

    # Finally load RequireJS
    'common/js/vendor/require.js'
]

# test_order: Determines the position of this chunk of javascript on
# the jasmine test page
PIPELINE['JAVASCRIPT'] = {
    'base_vendor': {
        'source_filenames': base_vendor_js,
        'output_filename': 'js/cms-base-vendor.js',
    },
}

STATICFILES_IGNORE_PATTERNS = (
    "*.py",
    "*.pyc",

    # It would be nice if we could do, for example, "**/*.scss",
    # but these strings get passed down to the `fnmatch` module,
    # which doesn't support that. :(
    # http://docs.python.org/2/library/fnmatch.html
    "sass/*.scss",
    "sass/*/*.scss",
    "sass/*/*/*.scss",
    "sass/*/*/*/*.scss",

    # Ignore tests
    "spec",
    "spec_helpers",

    # Symlinks used by js-test-tool
    "xmodule_js",
    "common_static",
)

################################# DJANGO-REQUIRE ###############################


# The baseUrl to pass to the r.js optimizer, relative to STATIC_ROOT.
REQUIRE_BASE_URL = "./"

# The name of a build profile to use for your project, relative to REQUIRE_BASE_URL.
# A sensible value would be 'app.build.js'. Leave blank to use the built-in default build profile.
# Set to False to disable running the default profile (e.g. if only using it to build Standalone
# Modules)
REQUIRE_BUILD_PROFILE = "cms/js/build.js"

# The name of the require.js script used by your project, relative to REQUIRE_BASE_URL.
REQUIRE_JS = "js/vendor/requiresjs/require.js"

# Whether to run django-require in debug mode.
REQUIRE_DEBUG = False

########################## DJANGO WEBPACK LOADER ##############################

WEBPACK_LOADER = {
    'DEFAULT': {
        'BUNDLE_DIR_NAME': 'bundles/',
        'STATS_FILE': os.path.join(STATIC_ROOT, 'webpack-stats.json'),
    },
    'WORKERS': {
        'BUNDLE_DIR_NAME': 'bundles/',
        'STATS_FILE': os.path.join(STATIC_ROOT, 'webpack-worker-stats.json')
    }
}


############################ SERVICE_VARIANT ##################################

# SERVICE_VARIANT specifies name of the variant used, which decides what JSON
# configuration files are read during startup.
SERVICE_VARIANT = os.environ.get('SERVICE_VARIANT', 'cms')

# CONFIG_PREFIX specifies the prefix of the JSON configuration files,
# based on the service variant. If no variant is use, don't use a
# prefix.
CONFIG_PREFIX = SERVICE_VARIANT + "." if SERVICE_VARIANT else ""


################################# CELERY ######################################

# Message configuration

CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'

CELERY_MESSAGE_COMPRESSION = 'gzip'

# Results configuration

CELERY_IGNORE_RESULT = False
CELERY_STORE_ERRORS_EVEN_IF_IGNORED = True

# Events configuration

CELERY_TRACK_STARTED = True

CELERY_SEND_EVENTS = True
CELERY_SEND_TASK_SENT_EVENT = True

# Exchange configuration

CELERY_DEFAULT_EXCHANGE = 'edx.core'
CELERY_DEFAULT_EXCHANGE_TYPE = 'direct'

# Name the exchange and queues for each variant

QUEUE_VARIANT = CONFIG_PREFIX.lower()

CELERY_DEFAULT_EXCHANGE = f'edx.{QUEUE_VARIANT}core'

HIGH_PRIORITY_QUEUE = f'edx.{QUEUE_VARIANT}core.high'
DEFAULT_PRIORITY_QUEUE = f'edx.{QUEUE_VARIANT}core.default'
LOW_PRIORITY_QUEUE = f'edx.{QUEUE_VARIANT}core.low'

CELERY_DEFAULT_QUEUE = DEFAULT_PRIORITY_QUEUE
CELERY_DEFAULT_ROUTING_KEY = DEFAULT_PRIORITY_QUEUE

CELERY_QUEUES = {
    HIGH_PRIORITY_QUEUE: {},
    DEFAULT_PRIORITY_QUEUE: {},
    LOW_PRIORITY_QUEUE: {},
}

# Queues configuration

CELERY_QUEUE_HA_POLICY = 'all'

CELERY_CREATE_MISSING_QUEUES = True

CELERY_BROKER_TRANSPORT = 'amqp'
CELERY_BROKER_HOSTNAME = 'localhost'
CELERY_BROKER_USER = 'celery'
CELERY_BROKER_PASSWORD = 'celery'
CELERY_BROKER_VHOST = ''
CELERY_BROKER_USE_SSL = False
CELERY_EVENT_QUEUE_TTL = None

############################## Video ##########################################

YOUTUBE = {
    # YouTube JavaScript API
    'API': 'https://www.youtube.com/iframe_api',

    'TEST_TIMEOUT': 1500,

    # URL to get YouTube metadata
    'METADATA_URL': 'https://www.googleapis.com/youtube/v3/videos',

    # Web page mechanism for scraping transcript information from youtube video pages
    'TRANSCRIPTS': {
        'CAPTION_TRACKS_REGEX': r"captionTracks\"\:\[(?P<caption_tracks>[^\]]+)",
        'YOUTUBE_URL_BASE': 'https://www.youtube.com/watch?v=',
        'ALLOWED_LANGUAGE_CODES': ["en", "en-US", "en-GB"],
    },

    'IMAGE_API': 'http://img.youtube.com/vi/{youtube_id}/0.jpg',  # /maxresdefault.jpg for 1920*1080
}

YOUTUBE_API_KEY = 'PUT_YOUR_API_KEY_HERE'

############################# SETTINGS FOR VIDEO UPLOAD PIPELINE #############################

VIDEO_UPLOAD_PIPELINE = {
    'VEM_S3_BUCKET': '',
    'BUCKET': '',
    'ROOT_PATH': '',
    'CONCURRENT_UPLOAD_LIMIT': 4,
}

############################ APPS #####################################

# The order of INSTALLED_APPS is important, when adding new apps here
# remember to check that you are not creating new
# RemovedInDjango19Warnings in the test logs.
INSTALLED_APPS = [
    'lms.djangoapps.news',
    'lms.djangoapps.univerapi',

    # Standard apps
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.humanize',
    'django.contrib.redirects',
    'django.contrib.sessions',
    'django.contrib.sites',
    'django.contrib.messages',

    # Tweaked version of django.contrib.staticfiles
    'openedx.core.djangoapps.staticfiles.apps.EdxPlatformStaticFilesConfig',

    'django_celery_results',

    'method_override',

    # Common Initialization
    'openedx.core.djangoapps.common_initialization.apps.CommonInitializationConfig',

    # Common views
    'openedx.core.djangoapps.common_views',

    # API access administration
    'openedx.core.djangoapps.api_admin',

    # CORS and cross-domain CSRF
    'corsheaders',
    'openedx.core.djangoapps.cors_csrf',

    # Provides the 'django_markup' template library so we can use 'interpolate_html' in django templates
    'xss_utils',

    # History tables
    'simple_history',

    # Database-backed configuration
    'config_models',
    'openedx.core.djangoapps.config_model_utils',
    'waffle',

    # Monitor the status of services
    'openedx.core.djangoapps.service_status',

    # Video block configs (This will be moved to Video once it becomes an XBlock)
    'openedx.core.djangoapps.video_config',

    # edX Video Pipeline integration
    'openedx.core.djangoapps.video_pipeline',

    # For CMS
    'cms.djangoapps.contentstore.apps.ContentstoreConfig',
    'common.djangoapps.split_modulestore_django.apps.SplitModulestoreDjangoBackendAppConfig',

    'openedx.core.djangoapps.contentserver',
    'cms.djangoapps.course_creators',
    'common.djangoapps.student.apps.StudentConfig',  # misleading name due to sharing with lms
    'openedx.core.djangoapps.course_groups',  # not used in cms (yet), but tests run
    'cms.djangoapps.xblock_config.apps.XBlockConfig',
    'cms.djangoapps.export_course_metadata.apps.ExportCourseMetadataConfig',
    'cms.djangoapps.import_from_modulestore.apps.ImportFromModulestoreConfig',

    # New (Learning-Core-based) XBlock runtime
    'openedx.core.djangoapps.xblock.apps.StudioXBlockAppConfig',

    # Maintenance tools
    'cms.djangoapps.maintenance',
    'openedx.core.djangoapps.util.apps.UtilConfig',

    # Tracking
    'common.djangoapps.track',
    'eventtracking.django.apps.EventTrackingConfig',

    # For asset pipelining
    'common.djangoapps.edxmako.apps.EdxMakoConfig',
    'pipeline',
    'common.djangoapps.static_replace',
    'require',
    'webpack_loader',

    # Site configuration for theming and behavioral modification
    'openedx.core.djangoapps.site_configuration',

    # Ability to detect and special-case crawler behavior
    'openedx.core.djangoapps.crawlers',

    # Discussion
    'openedx.core.djangoapps.django_comment_common',

    # Notifications
    'openedx.core.djangoapps.notifications',

    # for course creator table
    'django.contrib.admin',

    # for managing course modes
    'common.djangoapps.course_modes.apps.CourseModesConfig',

    # Verified Track Content Cohorting (Beta feature that will hopefully be removed)
    'openedx.core.djangoapps.verified_track_content',

    # Dark-launching languages
    'openedx.core.djangoapps.dark_lang',

    #
    # User preferences
    'wiki',
    'django_notify',
    'lms.djangoapps.course_wiki',  # Our customizations
    'mptt',
    'sekizai',
    'openedx.core.djangoapps.user_api',

    # Country embargo support
    'openedx.core.djangoapps.embargo',

    # Course action state
    'common.djangoapps.course_action_state',

    'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig',
    'openedx.core.djangoapps.content.block_structure.apps.BlockStructureConfig',

    # edx-milestones service
    'milestones',

    # Credit courses
    'openedx.core.djangoapps.credit.apps.CreditConfig',

    'common.djangoapps.xblock_django',

    # Catalog integration
    'openedx.core.djangoapps.catalog',

    # Programs support
    'openedx.core.djangoapps.programs.apps.ProgramsConfig',

    # django-oauth-toolkit
    'oauth2_provider',

    # These are apps that aren't strictly needed by Studio, but are imported by
    # other apps that are.  Django 1.8 wants to have imported models supported
    # by installed apps.
    'openedx.core.djangoapps.oauth_dispatch.apps.OAuthDispatchAppConfig',
    'lms.djangoapps.courseware',
    'lms.djangoapps.coursewarehistoryextended',
    'lms.djangoapps.survey.apps.SurveyConfig',
    'lms.djangoapps.verify_student.apps.VerifyStudentConfig',
    'completion',

    # System Wide Roles
    'openedx.core.djangoapps.system_wide_roles',

    # Static i18n support
    'statici18n',

    # Tagging
    'cms.lib.xblock.tagging',

    # Enables default site and redirects
    'django_sites_extensions',

    # additional release utilities to ease automation
    'release_util',

    # rule-based authorization
    'rules.apps.AutodiscoverRulesConfig',
    'bridgekeeper',

    # management of user-triggered async tasks (course import/export, etc.)
    'user_tasks',

    # CMS specific user task handling
    'cms.djangoapps.cms_user_tasks.apps.CmsUserTasksConfig',

    # Unusual migrations
    'common.djangoapps.database_fixups',

    # Customized celery tasks, including persisting failed tasks so they can
    # be retried
    'celery_utils',

    # Waffle related utilities
    'openedx.core.djangoapps.waffle_utils',

    # DRF filters
    'django_filters',
    'cms.djangoapps.api',

    # edx-drf-extensions
    'csrf.apps.CsrfAppConfig',  # Enables frontend apps to retrieve CSRF tokens.

    # Entitlements, used in openedx tests
    'common.djangoapps.entitlements',

    # Asset management for mako templates
    'common.djangoapps.pipeline_mako',

    # API Documentation
    'drf_yasg',

    # Tagging
    'openedx_tagging.core.tagging.apps.TaggingConfig',
    'openedx.core.djangoapps.content_tagging',

    # Search
    'openedx.core.djangoapps.content.search',

    # For Programs API
    'lms.djangoapps.program_enrollments',

    'openedx.features.course_duration_limits',
    'openedx.features.content_type_gating',
    'openedx.features.discounts',
    'openedx.features.effort_estimation',
    'lms.djangoapps.experiments',

    'openedx.core.djangoapps.external_user_ids',
    # so sample_task is available to celery workers
    'openedx.core.djangoapps.heartbeat',

    # signal handlers to capture course dates into edx-when
    'openedx.core.djangoapps.course_date_signals',

    # Management of per-user schedules
    'openedx.core.djangoapps.schedules',
    'rest_framework_jwt',

    # Learning Sequence Navigation
    'openedx.core.djangoapps.content.learning_sequences.apps.LearningSequencesConfig',

    # Database-backed Organizations App (http://github.com/openedx/edx-organizations)
    'organizations',

    # User and group management via edx-django-utils
    'edx_django_utils.user',

    # Allow Studio to use LMS for SSO
    'social_django',

    # Content Library LTI 1.3 Support.
    'pylti1p3.contrib.django.lti1p3_tool_config',

    # For edx ace template tags
    'edx_ace',

    # alternative swagger generator for CMS API
    'drf_spectacular',

    'openedx_events',

    # Learning Core Apps, used by v2 content libraries (content_libraries app)
    "openedx_learning.apps.authoring.collections",
    "openedx_learning.apps.authoring.components",
    "openedx_learning.apps.authoring.contents",
    "openedx_learning.apps.authoring.publishing",
    "openedx_learning.apps.authoring.units",
    "openedx_learning.apps.authoring.subsections",
    "openedx_learning.apps.authoring.sections",
]


################# EDX MARKETING SITE ##################################

EDXMKTG_LOGGED_IN_COOKIE_NAME = 'edxloggedin'
EDXMKTG_USER_INFO_COOKIE_NAME = 'edx-user-info'
EDXMKTG_USER_INFO_COOKIE_VERSION = 1

MKTG_URLS = {}
MKTG_URL_OVERRIDES = {}
MKTG_URL_LINK_MAP = {

}

SUPPORT_SITE_LINK = ''
ID_VERIFICATION_SUPPORT_LINK = ''
PASSWORD_RESET_SUPPORT_LINK = ''
ACTIVATION_EMAIL_SUPPORT_LINK = ''
LOGIN_ISSUE_SUPPORT_LINK = ''

############################## EVENT TRACKING #################################

CMS_SEGMENT_KEY = None
TRACK_MAX_EVENT = 50000

TRACKING_BACKENDS = {
    'logger': {
        'ENGINE': 'common.djangoapps.track.backends.logger.LoggerBackend',
        'OPTIONS': {
            'name': 'tracking'
        }
    }
}

# We're already logging events, and we don't want to capture user
# names/passwords.  Heartbeat events are likely not interesting.
TRACKING_IGNORE_URL_PATTERNS = [r'^/event', r'^/login', r'^/heartbeat']

EVENT_TRACKING_ENABLED = True
EVENT_TRACKING_BACKENDS = {
    'tracking_logs': {
        'ENGINE': 'eventtracking.backends.routing.RoutingBackend',
        'OPTIONS': {
            'backends': {
                'logger': {
                    'ENGINE': 'eventtracking.backends.logger.LoggerBackend',
                    'OPTIONS': {
                        'name': 'tracking',
                        'max_event_size': TRACK_MAX_EVENT,
                    }
                }
            },
            'processors': [
                {'ENGINE': 'common.djangoapps.track.shim.LegacyFieldMappingProcessor'},
                {'ENGINE': 'common.djangoapps.track.shim.PrefixedEventProcessor'}
            ]
        }
    },
    'segmentio': {
        'ENGINE': 'eventtracking.backends.routing.RoutingBackend',
        'OPTIONS': {
            'backends': {
                'segment': {'ENGINE': 'eventtracking.backends.segment.SegmentBackend'}
            },
            'processors': [
                {
                    'ENGINE': 'eventtracking.processors.whitelist.NameWhitelistProcessor',
                    'OPTIONS': {
                        'whitelist': []
                    }
                },
                {
                    'ENGINE': 'common.djangoapps.track.shim.GoogleAnalyticsProcessor'
                }
            ]
        }
    }
}
EVENT_TRACKING_PROCESSORS = []

EVENT_TRACKING_SEGMENTIO_EMIT_WHITELIST = []

#### PASSWORD POLICY SETTINGS #####
PASSWORD_POLICY_COMPLIANCE_ROLLOUT_CONFIG = {
    'ENFORCE_COMPLIANCE_ON_LOGIN': False
}

##### ACCOUNT LOCKOUT DEFAULT PARAMETERS #####
MAX_FAILED_LOGIN_ATTEMPTS_ALLOWED = 6
MAX_FAILED_LOGIN_ATTEMPTS_LOCKOUT_PERIOD_SECS = 30 * 60


### Apps only installed in some instances
# The order of INSTALLED_APPS matters, so this tuple is the app name and the item in INSTALLED_APPS
# that this app should be inserted *before*. A None here means it should be appended to the list.
OPTIONAL_APPS = (
    ('problem_builder', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),
    ('edx_sga', None),

    # edx-ora2
    ('submissions', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),
    ('openassessment', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),
    ('openassessment.assessment', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),
    ('openassessment.fileupload', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),
    ('openassessment.staffgrader', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),
    ('openassessment.workflow', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),
    ('openassessment.xblock', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),

    # edxval
    ('edxval', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),

    # Enterprise App (http://github.com/openedx/edx-enterprise)
    ('enterprise', None),
    ('consent', None),
    ('integrated_channels.integrated_channel', None),
    ('integrated_channels.degreed', None),
    ('integrated_channels.degreed2', None),
    ('integrated_channels.sap_success_factors', None),
    ('integrated_channels.xapi', None),
    ('integrated_channels.cornerstone', None),
    ('integrated_channels.blackboard', None),
    ('integrated_channels.canvas', None),
    ('integrated_channels.moodle', None),
)


for app_name, insert_before in OPTIONAL_APPS:
    # First attempt to only find the module rather than actually importing it,
    # to avoid circular references - only try to import if it can't be found
    # by find_spec, which doesn't work with import hooks
    if importlib.util.find_spec(app_name) is None:
        try:
            __import__(app_name)
        except ImportError:
            continue

    try:
        INSTALLED_APPS.insert(INSTALLED_APPS.index(insert_before), app_name)
    except (IndexError, ValueError):
        INSTALLED_APPS.append(app_name)


### External auth usage -- prefixes for ENROLLMENT_DOMAIN
SHIBBOLETH_DOMAIN_PREFIX = 'shib:'

# Set request limits for maximum size of a request body and maximum number of GET/POST parameters. (>=Django 1.10)
# Limits are currently disabled - but can be used for finer-grained denial-of-service protection.
DATA_UPLOAD_MAX_MEMORY_SIZE = None
DATA_UPLOAD_MAX_NUMBER_FIELDS = None

### Size of chunks into which asset uploads will be divided
UPLOAD_CHUNK_SIZE_IN_MB = 10

### Max size of asset uploads to GridFS
MAX_ASSET_UPLOAD_FILE_SIZE_IN_MB = 20

# FAQ url to direct users to if they upload
# a file that exceeds the above size
MAX_ASSET_UPLOAD_FILE_SIZE_URL = ""

### Default value for entrance exam minimum score
ENTRANCE_EXAM_MIN_SCORE_PCT = 50

### Default language for a new course
DEFAULT_COURSE_LANGUAGE = "en"

# Specify XBlocks that should be treated as advanced problems. Each entry is a
# dict:
#       'component': the entry-point name of the XBlock.
#       'boilerplate_name': an optional YAML template to be used.  Specify as
#               None to omit.
#
ADVANCED_PROBLEM_TYPES = [
    {
        'component': 'drag-and-drop-v2',
        'boilerplate_name': None
    },
    {
        'component': 'staffgradedxblock',
        'boilerplate_name': None
    }
]

LIBRARY_BLOCK_TYPES = [
    {
        'component': 'library_content',
        'boilerplate_name': None
    }
]

############### Settings for Retirement #####################
# See annotations in lms/envs/common.py for details.
RETIRED_USERNAME_PREFIX = 'retired__user_'
# See annotations in lms/envs/common.py for details.
RETIRED_EMAIL_PREFIX = 'retired__user_'
# See annotations in lms/envs/common.py for details.
RETIRED_EMAIL_DOMAIN = 'retired.invalid'
# See annotations in lms/envs/common.py for details.
RETIRED_USERNAME_FMT = Derived(lambda settings: settings.RETIRED_USERNAME_PREFIX + '{}')
# See annotations in lms/envs/common.py for details.
RETIRED_EMAIL_FMT = Derived(lambda settings: settings.RETIRED_EMAIL_PREFIX + '{}@' + settings.RETIRED_EMAIL_DOMAIN)
# See annotations in lms/envs/common.py for details.
RETIRED_USER_SALTS = ['abc', '123']
# See annotations in lms/envs/common.py for details.
RETIREMENT_SERVICE_WORKER_USERNAME = 'RETIREMENT_SERVICE_USER'

# These states are the default, but are designed to be overridden in configuration.
# See annotations in lms/envs/common.py for details.
RETIREMENT_STATES = [
    'PENDING',

    'LOCKING_ACCOUNT',
    'LOCKING_COMPLETE',

    # Use these states only when ENABLE_DISCUSSION_SERVICE is True.
    'RETIRING_FORUMS',
    'FORUMS_COMPLETE',

    # TODO - Change these states to be the LMS-only email opt-out - PLAT-2189
    'RETIRING_EMAIL_LISTS',
    'EMAIL_LISTS_COMPLETE',

    'RETIRING_ENROLLMENTS',
    'ENROLLMENTS_COMPLETE',

    # Use these states only when ENABLE_STUDENT_NOTES is True.
    'RETIRING_NOTES',
    'NOTES_COMPLETE',

    'RETIRING_LMS',
    'LMS_COMPLETE',

    'ERRORED',
    'ABORTED',
    'COMPLETE',
]

USERNAME_REPLACEMENT_WORKER = "REPLACE WITH VALID USERNAME"

# Files and Uploads type filter values

FILES_AND_UPLOAD_TYPE_FILTERS = {
    "Images": ['image/png', 'image/jpeg', 'image/jpg', 'image/gif', 'image/tiff', 'image/tif', 'image/x-icon',
               'image/svg+xml', 'image/bmp', 'image/x-ms-bmp', ],
    "Documents": [
        'application/pdf',
        'text/plain',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.template',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'application/vnd.openxmlformats-officedocument.presentationml.slideshow',
        'application/vnd.openxmlformats-officedocument.presentationml.template',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.template',
        'application/msword',
        'application/vnd.ms-excel',
        'application/vnd.ms-powerpoint',
        'application/csv',
        'application/vnd.ms-excel.sheet.macroEnabled.12',
        'text/x-tex',
        'application/x-pdf',
        'application/vnd.ms-excel.sheet.macroenabled.12',
        'file/pdf',
        'image/pdf',
        'text/csv',
        'text/pdf',
        'text/x-sh',
        '\"application/pdf\"',
    ],
    "Audio": ['audio/mpeg', 'audio/mp3', 'audio/x-wav', 'audio/ogg', 'audio/wav', 'audio/aac', 'audio/x-m4a',
              'audio/mp4', 'audio/x-ms-wma', ],
    "Code": ['application/json', 'text/html', 'text/javascript', 'application/javascript', 'text/css', 'text/x-python',
             'application/x-java-jnlp-file', 'application/xml', 'application/postscript', 'application/x-javascript',
             'application/java-vm', 'text/x-c++src', 'text/xml', 'text/x-scss', 'application/x-python-code',
             'application/java-archive', 'text/x-python-script', 'application/x-ruby', 'application/mathematica',
             'text/coffeescript', 'text/x-matlab', 'application/sql', 'text/php', ]
}

# Default to no Search Engine
SEARCH_ENGINE = None
ELASTIC_FIELD_MAPPINGS = {
    "start_date": {
        "type": "date"
    }
}

XBLOCK_SETTINGS = {}
XBLOCK_FS_STORAGE_BUCKET = None
XBLOCK_FS_STORAGE_PREFIX = None

STUDIO_FRONTEND_CONTAINER_URL = None

################################ Settings for Credit Course Requirements ################################
# Initial delay used for retrying tasks.
# Additional retries use longer delays.
# Value is in seconds.
CREDIT_TASK_DEFAULT_RETRY_DELAY = 30

# Maximum number of retries per task for errors that are not related
# to throttling.
CREDIT_TASK_MAX_RETRIES = 5

# Maximum age in seconds of timestamps we will accept
# when a credit provider notifies us that a student has been approved
# or denied for credit.
CREDIT_PROVIDER_TIMESTAMP_EXPIRATION = 15 * 60

CREDIT_PROVIDER_SECRET_KEYS = {}

# .. setting_name: COMPREHENSIVE_THEME_DIRS
# .. setting_default: []
# .. setting_description: A list of paths to directories, each of which will
#   be searched for comprehensive themes. Do not override this Django setting directly.
#   Instead, set the COMPREHENSIVE_THEME_DIRS environment variable, using colons (:) to
#   separate paths.
COMPREHENSIVE_THEME_DIRS = os.environ.get("COMPREHENSIVE_THEME_DIRS", "").split(":")

# .. setting_name: COMPREHENSIVE_THEME_LOCALE_PATHS
# .. setting_default: []
# .. setting_description: See LMS annotation.
#   "COMPREHENSIVE_THEME_LOCALE_PATHS" : ["/edx/src/edx-themes/conf/locale"].
COMPREHENSIVE_THEME_LOCALE_PATHS = []

# .. setting_name: PREPEND_LOCALE_PATHS
# .. setting_default: []
# .. setting_description: A list of the paths to locale directories to load first e.g.
#   "PREPEND_LOCALE_PATHS" : ["/edx/my-locales/"].
PREPEND_LOCALE_PATHS = []

# .. setting_name: DEFAULT_SITE_THEME
# .. setting_default: None
# .. setting_description: See LMS annotation.
DEFAULT_SITE_THEME = None

# .. toggle_name: ENABLE_COMPREHENSIVE_THEMING
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: See LMS annotation.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2016-06-30
ENABLE_COMPREHENSIVE_THEMING = False

# .. setting_name: CUSTOM_RESOURCE_TEMPLATES_DIRECTORY
# .. setting_default: None
# .. setting_description: Path to an existing directory of YAML files containing
#    html content to be used with the subclasses of xmodule.x_module.ResourceTemplates.
#    Default example templates can be found in xmodule/templates/html.
#    Note that the extension used is ".yaml" and not ".yml".
#    See xmodule.x_module.ResourceTemplates for usage.
#   "CUSTOM_RESOURCE_TEMPLATES_DIRECTORY" : null
CUSTOM_RESOURCE_TEMPLATES_DIRECTORY = None

############################ Global Database Configuration #####################

DATABASE_ROUTERS = [
    'openedx.core.lib.django_courseware_routers.StudentModuleHistoryExtendedRouter',
]

############################ Cache Configuration ###############################

CACHES = {
    'course_structure_cache': {
        'KEY_PREFIX': 'course_structure',
        'KEY_FUNCTION': 'common.djangoapps.util.memcache.safe_key',
        'LOCATION': ['localhost:11211'],
        'TIMEOUT': '604800',  # 1 week
        'BACKEND': 'django.core.cache.backends.memcached.PyMemcacheCache',
        'OPTIONS': {
            'no_delay': True,
            'ignore_exc': True,
            'use_pooling': True,
            'connect_timeout': 0.5
        }
    },
    'celery': {
        'KEY_PREFIX': 'celery',
        'KEY_FUNCTION': 'common.djangoapps.util.memcache.safe_key',
        'LOCATION': ['localhost:11211'],
        'TIMEOUT': '7200',
        'BACKEND': 'django.core.cache.backends.memcached.PyMemcacheCache',
        'OPTIONS': {
            'no_delay': True,
            'ignore_exc': True,
            'use_pooling': True,
            'connect_timeout': 0.5
        }
    },
    'mongo_metadata_inheritance': {
        'KEY_PREFIX': 'mongo_metadata_inheritance',
        'KEY_FUNCTION': 'common.djangoapps.util.memcache.safe_key',
        'LOCATION': ['localhost:11211'],
        'TIMEOUT': 300,
        'BACKEND': 'django.core.cache.backends.memcached.PyMemcacheCache',
        'OPTIONS': {
            'no_delay': True,
            'ignore_exc': True,
            'use_pooling': True,
            'connect_timeout': 0.5
        }
    },
    'staticfiles': {
        'KEY_FUNCTION': 'common.djangoapps.util.memcache.safe_key',
        'LOCATION': ['localhost:11211'],
        'KEY_PREFIX': 'staticfiles_general',
        'BACKEND': 'django.core.cache.backends.memcached.PyMemcacheCache',
        'OPTIONS': {
            'no_delay': True,
            'ignore_exc': True,
            'use_pooling': True,
            'connect_timeout': 0.5
        }
    },
    'default': {
        'VERSION': '1',
        'KEY_FUNCTION': 'common.djangoapps.util.memcache.safe_key',
        'LOCATION': ['localhost:11211'],
        'KEY_PREFIX': 'default',
        'BACKEND': 'django.core.cache.backends.memcached.PyMemcacheCache',
        'OPTIONS': {
            'no_delay': True,
            'ignore_exc': True,
            'use_pooling': True,
            'connect_timeout': 0.5
        }
    },
    'configuration': {
        'KEY_FUNCTION': 'common.djangoapps.util.memcache.safe_key',
        'LOCATION': ['localhost:11211'],
        'KEY_PREFIX': 'configuration',
        'BACKEND': 'django.core.cache.backends.memcached.PyMemcacheCache',
        'OPTIONS': {
            'no_delay': True,
            'ignore_exc': True,
            'use_pooling': True,
            'connect_timeout': 0.5
        }
    },
    'general': {
        'KEY_FUNCTION': 'common.djangoapps.util.memcache.safe_key',
        'LOCATION': ['localhost:11211'],
        'KEY_PREFIX': 'general',
        'BACKEND': 'django.core.cache.backends.memcached.PyMemcacheCache',
        'OPTIONS': {
            'no_delay': True,
            'ignore_exc': True,
            'use_pooling': True,
            'connect_timeout': 0.5
        }
    },
}

############################ OAUTH2 Provider ###################################

# 5 minute expiration time for JWT id tokens issued for external API requests.
OAUTH_ID_TOKEN_EXPIRATION = 5 * 60

# Partner support link for CMS footer
PARTNER_SUPPORT_EMAIL = ''

# Affiliate cookie tracking
AFFILIATE_COOKIE_NAME = 'dev_affiliate_id'

# API access management
API_ACCESS_MANAGER_EMAIL = 'api-access@example.com'
API_ACCESS_FROM_EMAIL = 'api-requests@example.com'
API_DOCUMENTATION_URL = 'https://course-catalog-api-guide.readthedocs.io/en/latest/'
AUTH_DOCUMENTATION_URL = 'https://course-catalog-api-guide.readthedocs.io/en/latest/authentication/index.html'

EDX_DRF_EXTENSIONS = {
    # Set this value to an empty dict in order to prevent automatically updating
    # user data from values in (possibly stale) JWTs.
    'JWT_PAYLOAD_USER_ATTRIBUTE_MAPPING': {},
}

############## Settings for Studio Context Sensitive Help ##############

HELP_TOKENS_INI_FILE = REPO_ROOT / "cms" / "envs" / "help_tokens.ini"
HELP_TOKENS_LANGUAGE_CODE = Derived(lambda settings: settings.LANGUAGE_CODE)
HELP_TOKENS_VERSION = Derived(lambda settings: doc_version())
HELP_TOKENS_BOOKS = {
    'learner': 'https://edx.readthedocs.io/projects/open-edx-learner-guide',
    'course_author': 'https://edx.readthedocs.io/projects/open-edx-building-and-running-a-course',
}

# Used with Email sending
RETRY_ACTIVATION_EMAIL_MAX_ATTEMPTS = 5
RETRY_ACTIVATION_EMAIL_TIMEOUT = 0.5

# Software Secure request retry settings
# Time in seconds before a retry of the task should be 60 mints.
SOFTWARE_SECURE_REQUEST_RETRY_DELAY = 60 * 60
# Maximum of 6 retries before giving up.
SOFTWARE_SECURE_RETRY_MAX_ATTEMPTS = 6

############## DJANGO-USER-TASKS ##############

# How long until database records about the outcome of a task and its artifacts get deleted?
USER_TASKS_MAX_AGE = timedelta(days=7)

############## Settings for the Enterprise App ######################

ENTERPRISE_ENROLLMENT_API_URL = LMS_ROOT_URL + LMS_ENROLLMENT_API_PATH
ENTERPRISE_SERVICE_WORKER_USERNAME = 'enterprise_worker'
ENTERPRISE_API_CACHE_TIMEOUT = 3600  # Value is in seconds
# The default value of this needs to be a 16 character string
ENTERPRISE_CUSTOMER_CATALOG_DEFAULT_CONTENT_FILTER = {}

# The setting key maps to the channel code (e.g. 'SAP' for success factors), Channel code is defined as
# part of django model of each integrated channel in edx-enterprise.
# The absence of a key/value pair translates to NO LIMIT on the number of "chunks" transmitted per cycle.
INTEGRATED_CHANNELS_API_CHUNK_TRANSMISSION_LIMIT = {}

BASE_COOKIE_DOMAIN = 'localhost'

############## Settings for the Discovery App ######################

COURSE_CATALOG_URL_ROOT = 'http://localhost:8008'
COURSE_CATALOG_API_URL = f'{COURSE_CATALOG_URL_ROOT}/api/v1'

# which access.py permission name to check in order to determine if a course is visible in
# the course catalog. We default this to the legacy permission 'see_exists'.
COURSE_CATALOG_VISIBILITY_PERMISSION = 'see_exists'

# which access.py permission name to check in order to determine if a course about page is
# visible. We default this to the legacy permission 'see_exists'.
COURSE_ABOUT_VISIBILITY_PERMISSION = 'see_exists'

DEFAULT_COURSE_VISIBILITY_IN_CATALOG = "both"
DEFAULT_MOBILE_AVAILABLE = False


# How long to cache OpenAPI schemas and UI, in seconds.
OPENAPI_CACHE_TIMEOUT = 0

############################# Persistent Grades ####################################

# Queue to use for updating persistent grades
RECALCULATE_GRADES_ROUTING_KEY = DEFAULT_PRIORITY_QUEUE

# Queue to use for updating grades due to grading policy change
POLICY_CHANGE_GRADES_ROUTING_KEY = 'edx.lms.core.default'

# Queue to use for individual learner course regrades
SINGLE_LEARNER_COURSE_REGRADE_ROUTING_KEY = 'edx.lms.core.default'

SOFTWARE_SECURE_VERIFICATION_ROUTING_KEY = 'edx.lms.core.default'

# Rate limit for regrading tasks that a grading policy change can kick off
POLICY_CHANGE_TASK_RATE_LIMIT = '900/h'

# .. setting_name: DEFAULT_GRADE_DESIGNATIONS
# .. setting_default: ['A', 'B', 'C', 'D']
# .. setting_description: The default 'pass' grade cutoff designations to be used. The failure grade
#     is always 'F' and should not be included in this list.
# .. setting_warning: The DEFAULT_GRADE_DESIGNATIONS list must have more than one designation,
#     or else ['A', 'B', 'C', 'D'] will be used as the default grade designations. Also, only the first
#     11 grade designations are used by the UI, so it's advisable to restrict the list to 11 items.
DEFAULT_GRADE_DESIGNATIONS = ['A', 'B', 'C', 'D']

########## Settings for video transcript migration tasks ############
VIDEO_TRANSCRIPT_MIGRATIONS_JOB_QUEUE = DEFAULT_PRIORITY_QUEUE

########## Settings youtube thumbnails scraper tasks ############
SCRAPE_YOUTUBE_THUMBNAILS_JOB_QUEUE = DEFAULT_PRIORITY_QUEUE

########## Settings update search index task ############
UPDATE_SEARCH_INDEX_JOB_QUEUE = DEFAULT_PRIORITY_QUEUE

###################### VIDEO IMAGE STORAGE ######################

VIDEO_IMAGE_DEFAULT_FILENAME = 'images/video-images/default_video_image.png'
VIDEO_IMAGE_SUPPORTED_FILE_FORMATS = {
    '.bmp': 'image/bmp',
    '.bmp2': 'image/x-ms-bmp',   # PIL gives x-ms-bmp format
    '.gif': 'image/gif',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png'
}
VIDEO_IMAGE_MAX_FILE_SIZE_MB = '2 MB'
VIDEO_IMAGE_MIN_FILE_SIZE_KB = '2 KB'
VIDEO_IMAGE_MAX_WIDTH = 1280
VIDEO_IMAGE_MAX_HEIGHT = 720
VIDEO_IMAGE_MIN_WIDTH = 640
VIDEO_IMAGE_MIN_HEIGHT = 360
VIDEO_IMAGE_ASPECT_RATIO = 16 / 9.0
VIDEO_IMAGE_ASPECT_RATIO_TEXT = '16:9'
VIDEO_IMAGE_ASPECT_RATIO_ERROR_MARGIN = 0.1

###################### ZENDESK ######################
ZENDESK_URL = ''
ZENDESK_USER = ''
ZENDESK_API_KEY = ''
ZENDESK_CUSTOM_FIELDS = {}
ZENDESK_OAUTH_ACCESS_TOKEN = ''
# A mapping of string names to Zendesk Group IDs
# To get the IDs of your groups you can go to
# {zendesk_url}/api/v2/groups.json
ZENDESK_GROUP_ID_MAPPING = {}

############## Settings for Completion API #########################

# Once a user has watched this percentage of a video, mark it as complete:
# (0.0 = 0%, 1.0 = 100%)
COMPLETION_VIDEO_COMPLETE_PERCENTAGE = 0.95

############### Settings for edx-rbac  ###############
SYSTEM_WIDE_ROLE_CLASSES = []

############## Installed Django Apps #########################

from edx_django_utils.plugins import get_plugin_apps, add_plugins
from openedx.core.djangoapps.plugins.constants import ProjectType, SettingsType

INSTALLED_APPS.extend(get_plugin_apps(ProjectType.CMS))
add_plugins(__name__, ProjectType.CMS, SettingsType.COMMON)

# Course exports streamed in blocks of this size. 8192 or 8kb is the default
# setting for the FileWrapper class used to iterate over the export file data.
# See: https://docs.python.org/2/library/wsgiref.html#wsgiref.util.FileWrapper
COURSE_EXPORT_DOWNLOAD_CHUNK_SIZE = 8192

# E-Commerce API Configuration
ECOMMERCE_PUBLIC_URL_ROOT = 'http://localhost:8002'
ECOMMERCE_API_URL = 'http://localhost:8002/api/v2'
ECOMMERCE_API_SIGNING_KEY = 'SET-ME-PLEASE'

CREDENTIALS_INTERNAL_SERVICE_URL = 'http://localhost:8005'
CREDENTIALS_PUBLIC_SERVICE_URL = 'http://localhost:8005'
CREDENTIALS_SERVICE_USERNAME = 'credentials_service_user'
# time between scheduled runs, in seconds
NOTIFY_CREDENTIALS_FREQUENCY = 14400

ANALYTICS_DASHBOARD_URL = 'http://localhost:18110/courses'
ANALYTICS_DASHBOARD_NAME = 'Your Platform Name Here Insights'

COMMENTS_SERVICE_URL = 'http://localhost:18080'
COMMENTS_SERVICE_KEY = 'password'

EXAMS_SERVICE_URL = 'http://localhost:18740/api/v1'
EXAMS_SERVICE_USERNAME = 'edx_exams_worker'

FINANCIAL_REPORTS = {
    'STORAGE_TYPE': 'localfs',
    'BUCKET': None,
    'ROOT_PATH': 'sandbox',
}

############# CORS headers for cross-domain requests #################
if FEATURES.get('ENABLE_CORS_HEADERS'):
    CORS_ALLOW_CREDENTIALS = True
    CORS_ORIGIN_WHITELIST = ()
    CORS_ORIGIN_ALLOW_ALL = False
    CORS_ALLOW_INSECURE = False

# Set CORS_ALLOW_HEADERS regardless of whether we've enabled ENABLE_CORS_HEADERS
# because that decision might happen in a later config file. (The headers to
# allow is an application logic, and not site policy.)
CORS_ALLOW_HEADERS = corsheaders_default_headers + (
    'use-jwt-cookie',
    'content-range',
    'content-disposition',
)

LOGIN_REDIRECT_WHITELIST = []

DEPRECATED_ADVANCED_COMPONENT_TYPES = []

########################## VIDEO IMAGE STORAGE ############################

VIDEO_IMAGE_SETTINGS = dict(
    VIDEO_IMAGE_MAX_BYTES=2 * 1024 * 1024,    # 2 MB
    VIDEO_IMAGE_MIN_BYTES=2 * 1024,       # 2 KB
    # Backend storage
    # STORAGE_CLASS='storages.backends.s3boto3.S3Boto3Storage',
    # STORAGE_KWARGS=dict(bucket='video-image-bucket'),
    STORAGE_KWARGS=dict(
        location=MEDIA_ROOT,
    ),
    DIRECTORY_PREFIX='video-images/',
    BASE_URL=MEDIA_URL,
)

VIDEO_IMAGE_MAX_AGE = 31536000

########################## VIDEO TRANSCRIPTS STORAGE ############################
VIDEO_TRANSCRIPTS_SETTINGS = dict(
    VIDEO_TRANSCRIPTS_MAX_BYTES=3 * 1024 * 1024,    # 3 MB
    # Backend storage
    # STORAGE_CLASS='storages.backends.s3boto3.S3Boto3Storage',
    # STORAGE_KWARGS=dict(bucket='video-transcripts-bucket'),
    STORAGE_KWARGS=dict(
        location=MEDIA_ROOT,
    ),
    DIRECTORY_PREFIX='video-transcripts/',
    BASE_URL=MEDIA_URL,
)

VIDEO_TRANSCRIPTS_MAX_AGE = 31536000
TRANSCRIPT_LANG_CACHE_TIMEOUT = 60 * 60 * 24


##### shoppingcart Payment #####
PAYMENT_SUPPORT_EMAIL = 'billing@example.com'

################################ Bulk Email ###################################
# Parameters for breaking down course enrollment into subtasks.
BULK_EMAIL_EMAILS_PER_TASK = 500

# Suffix used to construct 'from' email address for bulk emails.
# A course-specific identifier is prepended.
BULK_EMAIL_DEFAULT_FROM_EMAIL = 'no-reply@example.com'

# Flag to indicate if individual email addresses should be logged as they are sent
# a bulk email message.
BULK_EMAIL_LOG_SENT_EMAILS = False

############### Settings for django file storage ##################
DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'

###################### Grade Downloads ######################
# These keys are used for all of our asynchronous downloadable files, including
# the ones that contain information other than grades.
GRADES_DOWNLOAD = {
    'STORAGE_CLASS': 'django.core.files.storage.FileSystemStorage',
    'STORAGE_KWARGS': {
        'location': '/tmp/edx-s3/grades',
    },
    'STORAGE_TYPE': None,
    'BUCKET': None,
    'ROOT_PATH': None,
}

############### Settings swift #####################################
SWIFT_USERNAME = None
SWIFT_KEY = None
SWIFT_TENANT_ID = None
SWIFT_TENANT_NAME = None
SWIFT_AUTH_URL = None
SWIFT_AUTH_VERSION = None
SWIFT_REGION_NAME = None
SWIFT_USE_TEMP_URLS = False
SWIFT_TEMP_URL_KEY = None
SWIFT_TEMP_URL_DURATION = 1800  # seconds

############### The SAML private/public key values ################
SOCIAL_AUTH_SAML_SP_PRIVATE_KEY = ""
SOCIAL_AUTH_SAML_SP_PUBLIC_CERT = ""
SOCIAL_AUTH_SAML_SP_PRIVATE_KEY_DICT = {}
SOCIAL_AUTH_SAML_SP_PUBLIC_CERT_DICT = {}

############### Settings for facebook ##############################
FACEBOOK_APP_ID = 'FACEBOOK_APP_ID'
FACEBOOK_APP_SECRET = 'FACEBOOK_APP_SECRET'
FACEBOOK_API_VERSION = 'v2.1'

############### Settings for django-fernet-fields ##################
FERNET_KEYS = [
    'DUMMY KEY CHANGE BEFORE GOING TO PRODUCTION',
]

### Proctoring configuration (redirct URLs and keys shared between systems) ####
PROCTORING_BACKENDS = {
    'DEFAULT': 'null',
    # The null key needs to be quoted because
    # null is a language independent type in YAML
    'null': {}
}

PROCTORING_SETTINGS = {}

###################### LEARNER PORTAL ################################
LEARNER_PORTAL_URL_ROOT = 'https://learner-portal-localhost:18000'

############################ JWT #################################
JWT_ISSUER = 'http://127.0.0.1:8000/oauth2'
DEFAULT_JWT_ISSUER = {
    'ISSUER': 'http://127.0.0.1:8000/oauth2',
    'AUDIENCE': 'SET-ME-PLEASE',
    'SECRET_KEY': 'SET-ME-PLEASE'
}
JWT_EXPIRATION = 30
JWT_PRIVATE_SIGNING_KEY = None


SYSLOG_SERVER = ''
FEEDBACK_SUBMISSION_EMAIL = ''
REGISTRATION_EXTRA_FIELDS = {
    'confirm_email': 'hidden',
    'level_of_education': 'optional',
    'gender': 'optional',
    'year_of_birth': 'optional',
    'mailing_address': 'optional',
    'goals': 'optional',
    'honor_code': 'required',
    'terms_of_service': 'hidden',
    'city': 'hidden',
    'country': 'hidden',
    'marketing_emails_opt_in': 'hidden',
}
EDXAPP_PARSE_KEYS = {}

############## NOTIFICATIONS EXPIRY ##############
NOTIFICATIONS_EXPIRY = 60
EXPIRED_NOTIFICATIONS_DELETE_BATCH_SIZE = 10000
NOTIFICATION_CREATION_BATCH_SIZE = 76

############################ AI_TRANSLATIONS ##################################
AI_TRANSLATIONS_API_URL = 'http://localhost:18760/api/v1'

###################### DEPRECATED URLS ##########################

# .. toggle_name: DISABLE_DEPRECATED_SIGNIN_URL
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Toggle for removing the deprecated /signin url.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2019-12-02
# .. toggle_target_removal_date: 2020-06-01
# .. toggle_warning: This url can be removed once it no longer has any real traffic.
# .. toggle_tickets: ARCH-1253
DISABLE_DEPRECATED_SIGNIN_URL = False

# .. toggle_name: DISABLE_DEPRECATED_SIGNUP_URL
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Toggle for removing the deprecated /signup url.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2019-12-02
# .. toggle_target_removal_date: 2020-06-01
# .. toggle_warning: This url can be removed once it no longer has any real traffic.
# .. toggle_tickets: ARCH-1253
DISABLE_DEPRECATED_SIGNUP_URL = False

##### LOGISTRATION RATE LIMIT SETTINGS #####
LOGISTRATION_RATELIMIT_RATE = '3000/5m'
LOGISTRATION_PER_EMAIL_RATELIMIT_RATE = '300/5m'
LOGISTRATION_API_RATELIMIT = '1000/m'
LOGIN_AND_REGISTER_FORM_RATELIMIT = '3000/5m'
RESET_PASSWORD_TOKEN_VALIDATE_API_RATELIMIT = '30/7d'
RESET_PASSWORD_API_RATELIMIT = '30/7d'

##### REGISTRATION RATE LIMIT SETTINGS #####
REGISTRATION_VALIDATION_RATELIMIT = '3000/7d'
REGISTRATION_RATELIMIT = '6000/7d'
OPTIONAL_FIELD_API_RATELIMIT = '100/h'

##### PASSWORD RESET RATE LIMIT SETTINGS #####
PASSWORD_RESET_IP_RATE = '1/m'
PASSWORD_RESET_EMAIL_RATE = '2/h'

######################## Setting for content libraries ########################
MAX_BLOCKS_PER_CONTENT_LIBRARY = 100_000

################# Student Verification #################
VERIFY_STUDENT = {
    "DAYS_GOOD_FOR": 365,  # How many days is a verficiation good for?
    # The variable represents the window within which a verification is considered to be "expiring soon."
    "EXPIRING_SOON_WINDOW": 28,
}

######################## Organizations ########################

# .. toggle_name: ORGANIZATIONS_AUTOCREATE
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: When enabled, creating a course run or content library with
#   an "org slug" that does not map to an Organization in the database will trigger the
#   creation of a new Organization, with its name and short_name set to said org slug.
#   When disabled, creation of such content with an unknown org slug will instead
#   result in a validation error.
#   If you want the Organization table to be an authoritative information source in
#   Studio, then disable this; however, if you want the table to just be a reflection of
#   the orgs referenced in Studio content, then leave it enabled.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2020-11-02
# .. toggle_tickets: https://github.com/openedx/edx-organizations/blob/master/docs/decisions/0001-phase-in-db-backed-organizations-to-all.rst
ORGANIZATIONS_AUTOCREATE = True

################# Settings for brand logos. #################
LOGO_IMAGE_EXTRA_TEXT = ''
LOGO_URL = None
LOGO_URL_PNG = None
LOGO_TRADEMARK_URL = None
FAVICON_URL = None
DEFAULT_EMAIL_LOGO_URL = 'https://edx-cdn.org/v3/default/logo.png'

############## Settings for course import olx validation ############################
COURSE_OLX_VALIDATION_STAGE = 1
COURSE_OLX_VALIDATION_IGNORE_LIST = None

################# show account activate cta after register ########################
SHOW_ACTIVATE_CTA_POPUP_COOKIE_NAME = 'show-account-activation-popup'
SHOW_ACCOUNT_ACTIVATION_CTA = False

################# Documentation links for course apps #################

# pylint: disable=line-too-long
CALCULATOR_HELP_URL = "https://docs.openedx.org/en/latest/educators/how-tos/course_development/exercise_tools/add_calculator.html"
DISCUSSIONS_HELP_URL = "https://docs.openedx.org/en/latest/educators/concepts/communication/about_course_discussions.html"
EDXNOTES_HELP_URL = "https://docs.openedx.org/en/latest/educators/how-tos/course_development/exercise_tools/enable_notes.html"
PROGRESS_HELP_URL = "https://docs.openedx.org/en/latest/educators/references/data/progress_page.html"
TEAMS_HELP_URL = "https://docs.openedx.org/en/latest/educators/navigation/advanced_features.html#use-teams-in-your-course"
TEXTBOOKS_HELP_URL = "https://docs.openedx.org/en/latest/educators/how-tos/course_development/manage_textbooks.html"
WIKI_HELP_URL = "https://docs.openedx.org/en/latest/educators/concepts/communication/about_course_wiki.html"
CUSTOM_PAGES_HELP_URL = "https://docs.openedx.org/en/latest/educators/how-tos/course_development/manage_custom_page.html"
COURSE_LIVE_HELP_URL = "https://docs.openedx.org/en/latest/educators/how-tos/course_development/add_course_live.html"
ORA_SETTINGS_HELP_URL = "https://docs.openedx.org/en/latest/educators/how-tos/course_development/exercise_tools/Manage_ORA_Assignment.html"
# pylint: enable=line-too-long

# keys for  big blue button live provider
COURSE_LIVE_GLOBAL_CREDENTIALS = {}

######################## Registration ########################

# Social-core setting that allows inactive users to be able to
# log in. The only case it's used is when user registering a new account through the LMS.
INACTIVE_USER_LOGIN = True

# Redirect URL for inactive user. If not set, user will be redirected to /login after the login itself (loop)
INACTIVE_USER_URL = f'http://{CMS_BASE}'

# String length for the configurable part of the auto-generated username
AUTO_GENERATED_USERNAME_RANDOM_STRING_LENGTH = 4

######################## Discussion Forum settings ########################

# Feedback link in upgraded discussion notification alert
DISCUSSIONS_INCONTEXT_FEEDBACK_URL = ''

# Learn More link in upgraded discussion notification alert
# pylint: disable=line-too-long
DISCUSSIONS_INCONTEXT_LEARNMORE_URL = "https://docs.openedx.org/en/latest/educators/concepts/communication/about_course_discussions.html"
# pylint: enable=line-too-long

#### django-simple-history##
# disable indexing on date field its coming django-simple-history.
SIMPLE_HISTORY_DATE_INDEX = False

#### Event bus producing ####


def _should_send_xblock_events(settings):
    return settings.FEATURES['ENABLE_SEND_XBLOCK_LIFECYCLE_EVENTS_OVER_BUS']


def _should_send_learning_badge_events(settings):
    return settings.FEATURES['BADGES_ENABLED']


# .. setting_name: EVENT_BUS_PRODUCER_CONFIG
# .. setting_default: all events disabled
# .. setting_description: Dictionary of event_types mapped to dictionaries of topic to topic-related configuration.
#    Each topic configuration dictionary contains
#    * `enabled`: a toggle denoting whether the event will be published to the topic. These should be annotated
#       according to
#       https://docs.openedx.org/projects/edx-toggles/en/latest/how_to/documenting_new_feature_toggles.html
#    * `event_key_field` which is a period-delimited string path to event data field to use as event key.
#    Note: The topic names should not include environment prefix as it will be dynamically added based on
#    EVENT_BUS_TOPIC_PREFIX setting.

EVENT_BUS_PRODUCER_CONFIG = {
    'org.openedx.content_authoring.course.catalog_info.changed.v1': {
        'course-catalog-info-changed':
            {'event_key_field': 'catalog_info.course_key',
             # .. toggle_name: EVENT_BUS_PRODUCER_CONFIG['org.openedx.content_authoring.course.catalog_info.changed.v1']
             #    ['course-catalog-info-changed']['enabled']
             # .. toggle_implementation: DjangoSetting
             # .. toggle_default: False
             # .. toggle_description: if enabled, will publish COURSE_CATALOG_INFO_CHANGED events to the event bus on
             #    the course-catalog-info-changed topics
             # .. toggle_warning: The default may be changed in a later release. See
             #    https://github.com/openedx/openedx-events/issues/265
             # .. toggle_use_cases: opt_in
             # .. toggle_creation_date: 2023-10-10
             'enabled': False},
    },
    'org.openedx.content_authoring.xblock.published.v1': {
        'course-authoring-xblock-lifecycle':
            {'event_key_field': 'xblock_info.usage_key', 'enabled': Derived(_should_send_xblock_events)},
    },
    'org.openedx.content_authoring.xblock.deleted.v1': {
        'course-authoring-xblock-lifecycle':
            {'event_key_field': 'xblock_info.usage_key', 'enabled': Derived(_should_send_xblock_events)},
    },
    'org.openedx.content_authoring.xblock.duplicated.v1': {
        'course-authoring-xblock-lifecycle':
            {'event_key_field': 'xblock_info.usage_key', 'enabled': Derived(_should_send_xblock_events)},
    },
    # LMS events. These have to be copied over here because lms.common adds some derived entries as well,
    # and the derivation fails if the keys are missing. If we ever remove the import of lms.common, we can remove these.
    'org.openedx.learning.certificate.created.v1': {
        'learning-certificate-lifecycle':
            {'event_key_field': 'certificate.course.course_key', 'enabled': False},
    },
    'org.openedx.learning.certificate.revoked.v1': {
        'learning-certificate-lifecycle':
            {'event_key_field': 'certificate.course.course_key', 'enabled': False},
    },
    "org.openedx.learning.course.passing.status.updated.v1": {
        "learning-badges-lifecycle": {
            "event_key_field": "course_passing_status.course.course_key",
            "enabled": Derived(_should_send_learning_badge_events),
        },
    },
    "org.openedx.learning.ccx.course.passing.status.updated.v1": {
        "learning-badges-lifecycle": {
            "event_key_field": "course_passing_status.course.ccx_course_key",
            "enabled": Derived(_should_send_learning_badge_events),
        },
    },
}

################### Authoring API ######################

# This affects the Authoring API swagger docs but not the legacy swagger docs under /api-docs/.
REST_FRAMEWORK['DEFAULT_SCHEMA_CLASS'] = 'drf_spectacular.openapi.AutoSchema'

BEAMER_PRODUCT_ID = ""

################### Studio Search (beta), using Meilisearch ###################

# Enable Studio search features (powered by Meilisearch) (beta, off by default)
MEILISEARCH_ENABLED = False
# Meilisearch URL that the python backend can use. Often points to another docker container or k8s service.
MEILISEARCH_URL = "http://meilisearch"
# URL that browsers (end users) can use to reach Meilisearch. Should be HTTPS in production.
MEILISEARCH_PUBLIC_URL = "http://meilisearch.example.com"
# To support multi-tenancy, you can prefix all indexes with a common key like "sandbox7-"
# and use a restricted tenant token in place of an API key, so that this Open edX instance
# can only use the index(es) that start with this prefix.
# See https://www.meilisearch.com/docs/learn/security/tenant_tokens
MEILISEARCH_INDEX_PREFIX = ""
MEILISEARCH_API_KEY = "devkey"

# .. setting_name: LIBRARY_ENABLED_BLOCKS
# .. setting_default: ['problem', 'video', 'html', 'drag-and-drop-v2']
# .. setting_description: List of block types that are ready/enabled to be created/used
# .. in libraries. Both basic blocks and advanced blocks can be included.
# .. In the future, we will support individual configuration per library - see
# .. openedx/core/djangoapps/content_libraries/api.py::get_allowed_block_types()
LIBRARY_ENABLED_BLOCKS = [
    'problem',
    'video',
    'html',
    'drag-and-drop-v2',
    'openassessment',
    'conditional',
    'done',
    'edx_sga',
    'freetextresponse',
    'google-calendar',
    'google-document',
    'invideoquiz',
    'lti',
    'lti_consumer',
    'pdf',
    'poll',
    'survey',
    'word_cloud',
]
