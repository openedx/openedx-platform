"""
This is the common settings file, intended to set sane defaults.

If you wish to override some of the settings set here without needing to specify
everything, you should create a new settings file that imports the content of this
one and then overrides anything you wish to make overridable.

Some known files that extend this one:

- `production.py` - This file loads overrides from a yaml settings file and uses that
    to override the settings set in this file.


Conventions
-----------

1. Extending a List Setting

    Sometimes settings take the form of a list and rather than replacing the
    whole list, we want to add items to the list. eg. CELERY_IMPORTS.

    In this case, it is recommended that a new variable created in your extended
    file that contains the word `EXTRA` and enough of the base variable to easily
    let people map between the two items.

    Examples:
        - CELERY_EXTRA_IMPORTS  (preferred format)
        - EXTRA_MIDDLEWARE_CLASSES
        - XBLOCK_EXTRA_MIXINS  (preferred format)

    The preferred format for the name of the new setting (e.g. `CELERY_EXTRA_IMPORTS`) is to use
    the same prefix (e.g. `CELERY`) of the setting that is being appended (e.g. `CELERY_IMPORTS`).
"""

# We intentionally define lots of variables that aren't used
# pylint: disable=unused-import

# Pylint gets confused by path.py instances, which report themselves as class
# objects. As a result, pylint applies the wrong regex in validating names,
# and throws spurious errors. Therefore, we disable invalid-name checking.
# pylint: disable=invalid-name

import os

from corsheaders.defaults import default_headers as corsheaders_default_headers
from path import Path as path
from django.utils.translation import gettext_lazy as _
from enterprise.constants import (
    ENTERPRISE_ADMIN_ROLE,
    ENTERPRISE_LEARNER_ROLE,
    ENTERPRISE_CATALOG_ADMIN_ROLE,
    ENTERPRISE_DASHBOARD_ADMIN_ROLE,
    ENTERPRISE_ENROLLMENT_API_ADMIN_ROLE,
    ENTERPRISE_FULFILLMENT_OPERATOR_ROLE,
    ENTERPRISE_REPORTING_CONFIG_ADMIN_ROLE,
    ENTERPRISE_SSO_ORCHESTRATOR_OPERATOR_ROLE,
    ENTERPRISE_OPERATOR_ROLE,
    SYSTEM_ENTERPRISE_PROVISIONING_ADMIN_ROLE,
    PROVISIONING_ENTERPRISE_CUSTOMER_ADMIN_ROLE,
    PROVISIONING_PENDING_ENTERPRISE_CUSTOMER_ADMIN_ROLE,
    DEFAULT_ENTERPRISE_ENROLLMENT_INTENTIONS_ROLE,
)
from openedx_content.settings_api import openedx_content_backcompat_apps_to_install

from openedx.core.lib.derived import Derived
from openedx.envs.common import *  # pylint: disable=wildcard-import

from openedx.core.lib.features_setting_proxy import FeaturesProxy

# A proxy for feature flags stored in the settings namespace
FEATURES = FeaturesProxy(globals())

################################### FEATURES ###################################

CC_MERCHANT_NAME = Derived(lambda settings: settings.PLATFORM_NAME)

# .. toggle_name: settings.DISPLAY_DEBUG_INFO_TO_STAFF
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: Add a "Staff Debug" button to course blocks for debugging
#   by course staff.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2015-09-04
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/2425
DISPLAY_DEBUG_INFO_TO_STAFF = True

# .. toggle_name: settings.DISPLAY_HISTOGRAMS_TO_STAFF
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: This displays histograms in the Staff Debug Info panel to course staff.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2014-02-13
# .. toggle_warning: Generating histograms requires scanning the courseware_studentmodule table on each view. This
#   can make staff access to courseware very slow on large courses.
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/2425
DISPLAY_HISTOGRAMS_TO_STAFF = False  # For large courses this slows down courseware access for staff.

REROUTE_ACTIVATION_EMAIL = False  # nonempty string = address for all activation emails

ENABLE_DISCUSSION_HOME_PANEL = False

# .. toggle_name: settings.ENABLE_DISCUSSION_EMAIL_DIGEST
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Set this to True if you want the discussion digest emails
#   enabled automatically for new users. This will be set on all new account
#   registrations.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2014-08-19
# .. toggle_target_removal_date: None
# .. toggle_warning: It is not recommended to enable this feature if ENABLE_DISCUSSION_HOME_PANEL is not enabled,
#   since subscribers who receive digests in that case will only be able to unsubscribe via links embedded in
#   their emails, and they will have no way to resubscribe.
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/4891
ENABLE_DISCUSSION_EMAIL_DIGEST = False

# .. toggle_name: settings.ENABLE_UNICODE_USERNAME
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Set this to True to allow unicode characters in username. Enabling this will also
#   automatically enable SOCIAL_AUTH_CLEAN_USERNAMES. When this is enabled, usernames will have to match the
#   regular expression defined by USERNAME_REGEX_PARTIAL.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2017-06-27
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/14729
ENABLE_UNICODE_USERNAME = False

# .. toggle_name: settings.ENABLE_DJANGO_ADMIN_SITE
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: Set to False if you want to disable Django's admin site.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2013-09-26
# .. toggle_warning: It is not recommended to disable this feature as there are many settings available on
#  Django's admin site and will be inaccessible to the superuser.
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/829
ENABLE_DJANGO_ADMIN_SITE = True
ENABLE_LMS_MIGRATION = False

# .. toggle_name: settings.ENABLE_MASQUERADE
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: None
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2013-04-13
ENABLE_MASQUERADE = True

# .. toggle_name: settings.DISABLE_LOGIN_BUTTON
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Removes the display of the login button in the navigation bar.
#   Change is only at the UI level. Used in systems where login is automatic, eg MIT SSL
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2013-12-03
DISABLE_LOGIN_BUTTON = False

# .. toggle_name: settings.ENABLE_XBLOCK_VIEW_ENDPOINT
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Enable an API endpoint, named "xblock_view", to serve rendered XBlock views. This might be
#   used by external applications. See for instance jquery-xblock (now unmaintained):
#   https://github.com/openedx/jquery-xblock
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2014-03-14
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/2968
ENABLE_XBLOCK_VIEW_ENDPOINT = False

# Can be turned off if course lists need to be hidden. Effects views and templates.
# .. toggle_name: settings.COURSES_ARE_BROWSABLE
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: When this is set to True, all the courses will be listed on the /courses page and Explore
#   Courses link will be visible. Set to False if courses list and Explore Courses link need to be hidden.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2013-09-28
# .. toggle_warning: This Effects views and templates.
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/1073
COURSES_ARE_BROWSABLE = True

# .. toggle_name: settings.HIDE_DASHBOARD_COURSES_UNTIL_ACTIVATED
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When set, it hides the Courses list on the Learner Dashboard page if the learner has not
#   yet activated the account and not enrolled in any courses.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2018-05-18
# .. toggle_tickets: https://openedx.atlassian.net/browse/OSPR-1814
HIDE_DASHBOARD_COURSES_UNTIL_ACTIVATED = False

# .. toggle_name: settings.ENABLE_STUDENT_HISTORY_VIEW
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: This provides a UI to show a student's submission history in a problem by the Staff Debug
#   tool. Set to False if you want to hide Submission History from the courseware page.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2013-02-15
# .. toggle_tickets: https://github.com/openedx/edx-platform/commit/8f17e6ae9ed76fa75b3caf867b65ccb632cb6870
ENABLE_STUDENT_HISTORY_VIEW = True

# Turn on a page that lets staff enter Python code to be run in the
# sandbox, for testing whether it's enabled properly.
ENABLE_DEBUG_RUN_PYTHON = False

# Enable instructor dash to submit background tasks
ENABLE_INSTRUCTOR_BACKGROUND_TASKS = True

# Enable instructor to assign individual due dates
# Note: In order for this feature to work, you must also add
# 'lms.djangoapps.courseware.student_field_overrides.IndividualStudentOverrideProvider' to
# the setting FIELD_OVERRIDE_PROVIDERS, in addition to setting this flag to
# True.
INDIVIDUAL_DUE_DATES = False

# Toggle to enable certificates of courses on dashboard
ENABLE_VERIFIED_CERTIFICATES = False

# .. toggle_name: settings.DISABLE_HONOR_CERTIFICATES
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Set to True to disable honor certificates. Typically used when your installation only
#   allows verified certificates, like courses.edx.org.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2019-05-14
# .. toggle_tickets: https://openedx.atlassian.net/browse/PROD-269
DISABLE_HONOR_CERTIFICATES = False  # Toggle to disable honor certificates

DISABLE_AUDIT_CERTIFICATES = False  # Toggle to disable audit certificates

# .. toggle_name: settings.ENABLE_LOGIN_MICROFRONTEND
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Enable the login micro frontend.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2018-05-07
# .. toggle_warning: The login MFE domain name should be listed in LOGIN_REDIRECT_WHITELIST.
ENABLE_LOGIN_MICROFRONTEND = False

# .. toggle_name: settings.SKIP_EMAIL_VALIDATION
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Turn this on to skip sending emails for user validation.
#   Beware, as this leaves the door open to potential spam abuse.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2018-05-07
# .. toggle_warning: The login MFE domain name should be listed in LOGIN_REDIRECT_WHITELIST.
SKIP_EMAIL_VALIDATION = False

# .. toggle_name: settings.ENABLE_COSMETIC_DISPLAY_PRICE
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Enable the display of "cosmetic_display_price", set in a course advanced settings. This
#   cosmetic price is used when there is no registration price associated to the course.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2014-10-10
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/6876
# .. toggle_warning: The use case of this feature toggle is uncertain.
ENABLE_COSMETIC_DISPLAY_PRICE = False

# Automatically approve student identity verification attempts
# .. toggle_name: settings.AUTOMATIC_VERIFY_STUDENT_IDENTITY_FOR_TESTING
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: If set to True, then we want to skip posting anything to Software Secure. Bypass posting
#   anything to Software Secure if the auto verify feature for testing is enabled. We actually don't even create
#   the message because that would require encryption and message signing that rely on settings.VERIFY_STUDENT
#   values that aren't set in dev. So we just pretend like we successfully posted and automatically approve student
#   identity verification attempts.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2013-10-03
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/1184
AUTOMATIC_VERIFY_STUDENT_IDENTITY_FOR_TESTING = False

# Maximum number of rows to include in the csv file for downloading problem responses.
MAX_PROBLEM_RESPONSES_COUNT = 5000

ENABLED_PAYMENT_REPORTS = [
    "refund_report",
    "itemized_purchase_report",
    "university_revenue_share",
    "certificate_status"
]

ENABLE_MAX_FAILED_LOGIN_ATTEMPTS = True

# Hide any Personally Identifiable Information from application logs
SQUELCH_PII_IN_LOGS = True

# Whether the Wiki subsystem should be accessible via the direct /wiki/ paths. Setting this to True means
# that people can submit content and modify the Wiki in any arbitrary manner. We're leaving this as True in the
# defaults, so that we maintain current behavior
ALLOW_WIKI_ROOT_ACCESS = True

# .. toggle_name: settings.ENABLE_THIRD_PARTY_AUTH
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Turn on third-party auth. Disabled for now because full implementations are not yet
#   available. Remember to run migrations if you enable this; we don't create tables by default. This feature can
#   be enabled on a per-site basis. When enabling this feature, remember to define the allowed authentication
#   backends with the AUTHENTICATION_BACKENDS setting.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2014-09-15
ENABLE_THIRD_PARTY_AUTH = False

# Prevent concurrent logins per user
PREVENT_CONCURRENT_LOGINS = True

# .. toggle_name: settings.ALWAYS_REDIRECT_HOMEPAGE_TO_DASHBOARD_FOR_AUTHENTICATED_USER
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: When a logged in user goes to the homepage ('/') the user will be redirected to the
#   dashboard page when this flag is set to True - this is default Open edX behavior. Set to False to not redirect
#   the user.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2014-09-16
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/5220
ALWAYS_REDIRECT_HOMEPAGE_TO_DASHBOARD_FOR_AUTHENTICATED_USER = True

# .. toggle_name: settings.ENABLE_COURSE_SORTING_BY_START_DATE
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: When a user goes to the homepage ('/') the user sees the courses listed in the
#   announcement dates order - this is default Open edX behavior. Set to True to change the course sorting behavior
#   by their start dates, latest first.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2015-03-27
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/7548
ENABLE_COURSE_SORTING_BY_START_DATE = True

# .. toggle_name: settings.ENABLE_COURSE_HOME_REDIRECT
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: When enabled, along with the ENABLE_MKTG_SITE feature toggle, users who attempt to access a
#   course "about" page will be redirected to the course home url.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2019-01-15
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/19604
ENABLE_COURSE_HOME_REDIRECT = True

# .. toggle_name: settings.ENABLE_COMBINED_LOGIN_REGISTRATION_FOOTER
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Display the standard footer in the login page. This feature can be overridden by a site-
#   specific configuration.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2016-06-24
# .. toggle_tickets: https://openedx.atlassian.net/browse/OSPR-1320
ENABLE_COMBINED_LOGIN_REGISTRATION_FOOTER = False

# Enable organizational email opt-in
ENABLE_MKTG_EMAIL_OPT_IN = False

# .. toggle_name: settings.ENABLE_FOOTER_MOBILE_APP_LINKS
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Set to True if you want show the mobile app links (Apple App Store & Google Play Store) in
#   the footer.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2015-01-13
# .. toggle_warning: If you set this to True then you should also set your mobile application's app store and play
#   store URLs in the MOBILE_STORE_URLS settings dictionary. These links are not part of the default theme. If you
#   want these links on your footer then you should use the edx.org theme.
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/6588
ENABLE_FOOTER_MOBILE_APP_LINKS = False

# For easily adding modes to courses during acceptance testing
MODE_CREATION_FOR_TESTING = False

# For caching programs in contexts where the LMS can only
# be reached over HTTP.
EXPOSE_CACHE_PROGRAMS_ENDPOINT = False

# Courseware search feature
# .. toggle_name: settings.ENABLE_COURSEWARE_SEARCH
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When enabled, this adds a Search the course widget on the course outline and courseware
#   pages for searching courseware data.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2015-01-29
# .. toggle_warning: In order to get this working, your courses data should be indexed in Elasticsearch. You will
#   see the search widget on the courseware page only if the DISABLE_COURSE_OUTLINE_PAGE_FLAG is set.
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/6506
ENABLE_COURSEWARE_SEARCH = False

# .. toggle_name: settings.ENABLE_COURSEWARE_SEARCH_FOR_COURSE_STAFF
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When enabled, this adds a Search the course widget on the course outline and courseware
#   pages for searching courseware data but for course staff users only.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2019-12-06
# .. toggle_warning: In order to get this working, your courses data should be indexed in Elasticsearch. If
#   ENABLE_COURSEWARE_SEARCH is enabled then the search widget will be visible to all learners and this flag's
#   value does not matter in that case. This flag is enabled in devstack by default.
# .. toggle_tickets: https://openedx.atlassian.net/browse/TNL-6931
ENABLE_COURSEWARE_SEARCH_FOR_COURSE_STAFF = False

# Dashboard search feature
# .. toggle_name: settings.ENABLE_DASHBOARD_SEARCH
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When enabled, this adds a Search Your Courses widget on the dashboard page for searching
#   courseware data.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2015-01-29
# .. toggle_warning: In order to get this working, your courses data should be indexed in Elasticsearch.
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/6506
ENABLE_DASHBOARD_SEARCH = False

# log all information from cybersource callbacks
LOG_POSTPAY_CALLBACKS = True

# .. toggle_name: settings.CUSTOM_CERTIFICATE_TEMPLATES_ENABLED
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Set to True to enable custom certificate templates which are configured via Django admin.
# .. toggle_warning: None
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2015-08-13
# .. toggle_target_removal_date: None
# .. toggle_tickets: https://openedx.atlassian.net/browse/SOL-1044
CUSTOM_CERTIFICATE_TEMPLATES_ENABLED = False

# .. toggle_name: settings.ENABLE_COURSE_DISCOVERY
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Add a course search widget to the LMS for searching courses. When this is enabled, the
#   latest courses are no longer displayed on the LMS landing page. Also, an "Explore Courses" item is added to the
#   navbar.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2015-04-23
# .. toggle_target_removal_date: None
# .. toggle_warning: The COURSE_DISCOVERY_MEANINGS setting should be properly defined.
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/7845
ENABLE_COURSE_DISCOVERY = False

# .. toggle_name: settings.ENABLE_COURSE_FILENAME_CCX_SUFFIX
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: If set to True, CCX ID will be included in the generated filename for CCX courses.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2021-03-16
# .. toggle_target_removal_date: None
# .. toggle_tickets: None
# .. toggle_warning: Turning this feature ON will affect all generated filenames which are related to CCX courses.
ENABLE_COURSE_FILENAME_CCX_SUFFIX = False

# Setting for overriding default filtering facets for Course discovery
# COURSE_DISCOVERY_FILTERS = ["org", "language", "modes"]

# Software secure fake page feature flag
ENABLE_SOFTWARE_SECURE_FAKE = False

# Teams feature
ENABLE_TEAMS = True

# Show video bumper in LMS
ENABLE_VIDEO_BUMPER = False

# How many seconds to show the bumper again, default is 7 days:
SHOW_BUMPER_PERIODICITY = 7 * 24 * 3600

# .. toggle_name: settings.ENABLE_SPECIAL_EXAMS
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Enable to use special exams, aka timed and proctored exams.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2015-09-04
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/9744
ENABLE_SPECIAL_EXAMS = False

# .. toggle_name: settings.ENABLE_LTI_PROVIDER
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When set to True, Open edX site can be used as an LTI Provider to other systems
#    and applications.
# .. toggle_warning: After enabling this feature flag there are multiple steps involved to configure edX
#    as LTI provider. Full guide is available here:
#    https://docs.openedx.org/en/latest/site_ops/install_configure_run_guide/configuration/lti/index.html
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2015-04-24
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/7689
ENABLE_LTI_PROVIDER = False

# .. toggle_name: settings.ENABLE_COOKIE_CONSENT
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Enable header banner for cookie consent using this service:
#   https://cookieconsent.insites.com/
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2017-03-03
# .. toggle_tickets: https://openedx.atlassian.net/browse/OSPR-1560
ENABLE_COOKIE_CONSENT = False

# Enable one click program purchase
# See LEARNER-493
ENABLE_ONE_CLICK_PROGRAM_PURCHASE = False

# .. toggle_name: settings.ALLOW_EMAIL_ADDRESS_CHANGE
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: Allow users to change their email address on the Account Settings page. If this is
#   disabled, users will not be able to change their email address.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2017-06-26
# .. toggle_tickets: https://openedx.atlassian.net/browse/OSPR-1735
ALLOW_EMAIL_ADDRESS_CHANGE = True

# .. toggle_name: settings.ENABLE_BULK_ENROLLMENT_VIEW
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When set to True the bulk enrollment view is enabled and one can use it to enroll multiple
#   users in a course using bulk enrollment API endpoint (/api/bulk_enroll/v1/bulk_enroll).
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2017-07-15
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/15006
ENABLE_BULK_ENROLLMENT_VIEW = False

# Set to enable Enterprise integration
ENABLE_ENTERPRISE_INTEGRATION = False

# .. toggle_name: settings.ENABLE_HTML_XBLOCK_STUDENT_VIEW_DATA
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Whether HTML Block returns HTML content with the Course Blocks API when the API
#   is called with student_view_data=html query parameter.
# .. toggle_warning: Because the Course Blocks API caches its data, the cache must be cleared (e.g. by
#   re-publishing the course) for changes to this flag to take effect.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2017-08-28
# .. toggle_tickets: https://openedx.atlassian.net/browse/OSPR-1880
ENABLE_HTML_XBLOCK_STUDENT_VIEW_DATA = False

# Sets the default browser support. For more information go to http://browser-update.org/customize.html
UNSUPPORTED_BROWSER_ALERT_VERSIONS = "{i:10,f:-3,o:-3,s:-3,c:-3}"

# .. toggle_name: settings.ENABLE_ACCOUNT_DELETION
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: Whether to display the account deletion section on Account Settings page. Set to False to
#   hide this section.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2018-06-01
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/18298
ENABLE_ACCOUNT_DELETION = True

# .. toggle_name: settings.ENABLE_AUTHN_MICROFRONTEND
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Supports staged rollout of a new micro-frontend-based implementation of the logistration.
# .. toggle_use_cases: temporary, open_edx
# .. toggle_creation_date: 2020-09-08
# .. toggle_target_removal_date: None
# .. toggle_tickets: 'https://github.com/openedx/edx-platform/pull/24908'
# .. toggle_warning: Also set settings.AUTHN_MICROFRONTEND_URL for rollout. This temporary feature
#   toggle does not have a target removal date.
ENABLE_AUTHN_MICROFRONTEND = os.environ.get("EDXAPP_ENABLE_AUTHN_MFE", False)

# .. toggle_name: settings.ENABLE_CATALOG_MICROFRONTEND
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Supports staged rollout of a new micro-frontend-based implementation of the catalog.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2025-05-15
# .. toggle_target_removal_date: 2025-11-01
ENABLE_CATALOG_MICROFRONTEND = False

### ORA Feature Flags ###

# .. toggle_name: settings.ENABLE_ORA_USERNAMES_ON_DATA_EXPORT
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Set to True to add deanonymized usernames to ORA data
#   report.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2020-06-11
# .. toggle_target_removal_date: None
# .. toggle_tickets: https://openedx.atlassian.net/browse/TNL-7273
# .. toggle_warning: This temporary feature toggle does not have a target removal date.
ENABLE_ORA_USERNAMES_ON_DATA_EXPORT = False

# .. toggle_name: settings.ENABLE_COURSE_ASSESSMENT_GRADE_CHANGE_SIGNAL
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Set to True to start sending signals for assessment level grade updates. Notably, the only
#   handler of this signal at the time of this writing sends assessment updates to enterprise integrated channels.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2020-12-09
# .. toggle_target_removal_date: 2021-02-01
# .. toggle_tickets: https://openedx.atlassian.net/browse/ENT-3818
ENABLE_COURSE_ASSESSMENT_GRADE_CHANGE_SIGNAL = False

# .. toggle_name: settings.ALLOW_ADMIN_ENTERPRISE_COURSE_ENROLLMENT_DELETION
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: If true, allows for the deletion of EnterpriseCourseEnrollment records via Django Admin.
# .. toggle_use_cases: opt_in
# .. toggle_creation_date: 2021-01-27
# .. toggle_tickets: https://openedx.atlassian.net/browse/ENT-4022
ALLOW_ADMIN_ENTERPRISE_COURSE_ENROLLMENT_DELETION = False

# .. toggle_name: settings.ENABLE_BULK_USER_RETIREMENT
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Set to True to enable bulk user retirement through REST API. This is disabled by
#   default.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2021-03-11
# .. toggle_target_removal_date: None
# .. toggle_warning: None
# .. toggle_tickets: 'https://openedx.atlassian.net/browse/OSPR-5290'
ENABLE_BULK_USER_RETIREMENT = False

# .. toggle_name: settings.ENABLE_NEW_BULK_EMAIL_EXPERIENCE
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When true, replaces the bulk email tool found on the
#   instructor dashboard with a link to the new communications MFE version instead.
#   Setting the tool to false will leave the old bulk email tool experience in place.
# .. toggle_use_cases: opt_in
# .. toggle_creation_date: 2022-03-21
# .. toggle_target_removal_date: None
# .. toggle_tickets: 'https://openedx.atlassian.net/browse/MICROBA-1758'
ENABLE_NEW_BULK_EMAIL_EXPERIENCE = False

# .. toggle_name: settings.ENABLE_CERTIFICATES_IDV_REQUIREMENT
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Whether to enforce ID Verification requirements for course certificates generation
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2022-04-26
# .. toggle_target_removal_date: None
# .. toggle_tickets: 'https://openedx.atlassian.net/browse/MST-1458'
ENABLE_CERTIFICATES_IDV_REQUIREMENT = False

# .. toggle_name: settings.DISABLE_ALLOWED_ENROLLMENT_IF_ENROLLMENT_CLOSED
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Set to True to disable enrollment for user invited to a course
# .. if user is registering before enrollment start date or after enrollment end date
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2022-06-06
# .. toggle_tickets: 'https://github.com/openedx/edx-platform/pull/29538'
DISABLE_ALLOWED_ENROLLMENT_IF_ENROLLMENT_CLOSED = False

# .. toggle_name: settings.SEND_LEARNING_CERTIFICATE_LIFECYCLE_EVENTS_TO_BUS
# .. toggle_implementation: SettingToggle
# .. toggle_default: False
# .. toggle_description: When True, the system will publish certificate lifecycle signals to the event bus.
#    This toggle is used to create the EVENT_BUS_PRODUCER_CONFIG setting.
# .. toggle_warning: The default may be changed in a later release. See
#    https://github.com/openedx/openedx-events/issues/265
# .. toggle_use_cases: opt_in
# .. toggle_creation_date: 2023-10-10
# .. toggle_tickets: https://github.com/openedx/openedx-events/issues/210
SEND_LEARNING_CERTIFICATE_LIFECYCLE_EVENTS_TO_BUS = False

# .. toggle_name: settings.ENABLE_COURSEWARE_SEARCH_VERIFIED_REQUIRED
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When enabled, the courseware search feature will only be enabled
#   for users in a verified enrollment track.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2024-04-24
ENABLE_COURSEWARE_SEARCH_VERIFIED_ENROLLMENT_REQUIRED = False

# .. toggle_name: settings.BADGES_ENABLED
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Set to True to enable badges functionality.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2024-04-02
# .. toggle_target_removal_date: None
BADGES_ENABLED = False

################ Enable credit eligibility feature ####################
# .. toggle_name: settings.ENABLE_CREDIT_ELIGIBILITY
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: When enabled, it is possible to define a credit eligibility criteria in the CMS. A "Credit
#   Eligibility" section then appears for those courses in the LMS.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2015-06-17
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/8550
ENABLE_CREDIT_ELIGIBILITY = True

ENABLE_CROSS_DOMAIN_CSRF_COOKIE = False

# .. toggle_name: ENABLE_REQUIRE_THIRD_PARTY_AUTH
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Set to True to prevent using username/password login and registration and only allow
#   authentication with third party auth
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2020-09-16
# .. toggle_warning: Requires configuration of third party auth
ENABLE_REQUIRE_THIRD_PARTY_AUTH = False

# Specifies extra XBlock fields that should available when requested via the Course Blocks API
# Should be a list of tuples of (block_type, field_name), where block_type can also be "*" for all block types.
# e.g. COURSE_BLOCKS_API_EXTRA_FIELDS = [  ('course', 'other_course_settings'), ("problem", "weight")  ]
COURSE_BLOCKS_API_EXTRA_FIELDS = []

# Used for A/B testing
DEFAULT_GROUPS = []

# .. setting_name: GRADEBOOK_FREEZE_DAYS
# .. setting_default: 30
# .. setting_description: Sets the number of days after which the gradebook will freeze following the course's end.
GRADEBOOK_FREEZE_DAYS = 30

RETRY_CALENDAR_SYNC_EMAIL_MAX_ATTEMPTS = 5

############################# SET PATH INFORMATION #############################

PROJECT_ROOT = path(__file__).abspath().dirname().dirname()  # /edx-platform/lms
NODE_MODULES_ROOT = REPO_ROOT / "node_modules"

# Where to look for a status message
STATUS_MESSAGE_PATH = ENV_ROOT / "status_message.json"

############################ Global Database Configuration #####################

DATABASE_ROUTERS.append('edx_django_utils.db.read_replica.ReadReplicaRouter')

################################## DJANGO OAUTH TOOLKIT #######################################

# Scope description strings are presented to the user
# on the application authorization page. See
# lms/templates/oauth2_provider/authorize.html for details.
# Non-default scopes should be added directly to OAUTH2_PROVIDER['SCOPES'] below.
OAUTH2_DEFAULT_SCOPES = {
    'read': _('Read access'),
    'write': _('Write access'),
    'email': _('Know your email address'),
    'profile': _('Know your name and username'),
}

OAUTH2_PROVIDER = {
    'OAUTH2_VALIDATOR_CLASS': 'openedx.core.djangoapps.oauth_dispatch.dot_overrides.validators.EdxOAuth2Validator',
    # 3 months and then we expire refresh tokens using edx_clear_expired_tokens (length is mobile app driven)
    'REFRESH_TOKEN_EXPIRE_SECONDS': 7776000,
    'SCOPES_BACKEND_CLASS': 'openedx.core.djangoapps.oauth_dispatch.scopes.ApplicationModelScopes',
    'SCOPES': dict(OAUTH2_DEFAULT_SCOPES, **{
        'certificates:read': _('Retrieve your course certificates'),
        'grades:read': _('Retrieve your grades for your enrolled courses'),
        'tpa:read': _('Retrieve your third-party authentication username mapping'),
        # user_id is added in code as a default scope for JWT cookies and all password grant_type JWTs
        'user_id': _('Know your user identifier'),
    }),
    'DEFAULT_SCOPES': OAUTH2_DEFAULT_SCOPES,
    'REQUEST_APPROVAL_PROMPT': 'auto_even_if_expired',
    'ERROR_RESPONSE_WITH_SCOPES': True,
}

# Automatically clean up edx-django-oauth2-provider tokens on use
OAUTH_DELETE_EXPIRED = True
OAUTH_ID_TOKEN_EXPIRATION = 60 * 60
OAUTH_ENFORCE_SECURE = True
OAUTH_EXPIRE_CONFIDENTIAL_CLIENT_DAYS = 365
OAUTH_EXPIRE_PUBLIC_CLIENT_DAYS = 30

################################## THIRD_PARTY_AUTH CONFIGURATION #############################
TPA_PROVIDER_BURST_THROTTLE = '10/min'
TPA_PROVIDER_SUSTAINED_THROTTLE = '50/hr'

# .. toggle_name: TPA_AUTOMATIC_LOGOUT_ENABLED
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Redirect the user to the TPA logout URL if this flag is enabled, the
#   TPA logout URL is configured, and the user logs in through TPA.
# .. toggle_use_cases: opt_in
# .. toggle_warning: Enabling this toggle skips rendering logout.html, which is used to log the user out
#   from the different IDAs. To ensure the user is logged out of all the IDAs be sure to redirect
#   back to <LMS>/logout after logging out of the TPA.
# .. toggle_creation_date: 2023-05-07
TPA_AUTOMATIC_LOGOUT_ENABLED = False

################################## TEMPLATE CONFIGURATION #####################################

MAKO_TEMPLATE_DIRS_BASE = lms_mako_template_dirs_base

CONTEXT_PROCESSORS.remove('django.contrib.messages.context_processors.messages')
CONTEXT_PROCESSORS[5:5] = [
    # Added for django-wiki
    'django.template.context_processors.media',
    'django.template.context_processors.tz',
    'django.contrib.messages.context_processors.messages',
    'sekizai.context_processors.sekizai',

    # Hack to get required link URLs to password reset templates
    'common.djangoapps.edxmako.shortcuts.marketing_link_context_processor',

    # Timezone processor (sends language and time_zone preference)
    'lms.djangoapps.courseware.context_processor.user_timezone_locale_prefs',
]
CONTEXT_PROCESSORS += [
    # Mobile App processor (Detects if request is from the mobile app)
    'lms.djangoapps.mobile_api.context_processor.is_from_mobile_app',

    # Context processor necessary for the survey report message appear on the admin site
    'openedx.features.survey_report.context_processors.admin_extra_context',
]

DEFAULT_TEMPLATE_ENGINE_DIRS = Derived(lambda settings: settings.TEMPLATES[0]['DIRS'][:])

###############################################################################################

STUDENT_FILEUPLOAD_MAX_SIZE = 4 * 1000 * 1000  # 4 MB
MAX_FILEUPLOADS_PER_INPUT = 20

# Configuration option for when we want to grab server error pages
STATIC_GRAB = False
DEV_CONTENT = True

SEARCH_COURSEWARE_CONTENT_LOG_PARAMS = False

# .. setting_name: ELASTIC_SEARCH_INDEX_PREFIX
# .. setting_default: ''
# .. setting_description: Specifies the prefix used when naming elasticsearch indexes related to edx-search.
ELASTICSEARCH_INDEX_PREFIX = ""

EDX_API_KEY = None

LOGIN_REDIRECT_URL = EDX_ROOT_URL + '/login'
LOGIN_URL = EDX_ROOT_URL + '/login'

CERT_QUEUE = 'test-pull'

ALTERNATE_WORKER_QUEUES = 'cms'

DATA_DIR = '/edx/var/edxapp/data'

MAINTENANCE_BANNER_TEXT = None

# Set certificate issued date format. It supports all formats supported by
# `common.djangoapps.util.date_utils.strftime_localized`.
CERTIFICATE_DATE_FORMAT = "%B %-d, %Y"

### Dark code. Should be enabled in local settings for devel.

ENABLE_MULTICOURSE = False  # set to False to disable multicourse display (see lib.util.views.edXhome)

###

# IP addresses that are allowed to reload the course, etc.
# TODO (vshnayder): Will probably need to change as we get real access control in.
LMS_MIGRATION_ALLOWED_IPS = []

############################## EVENT TRACKING #################################
LMS_SEGMENT_KEY = None

DEBUG_TRACK_LOG = False

TRACKING_IGNORE_URL_PATTERNS += [r'^/segmentio/event', r'^/performance']

TRACKING_SEGMENTIO_WEBHOOK_SECRET = None
TRACKING_SEGMENTIO_ALLOWED_TYPES = ['track']
TRACKING_SEGMENTIO_DISALLOWED_SUBSTRING_NAMES = []
TRACKING_SEGMENTIO_SOURCE_MAP = {
    'analytics-android': 'mobile',
    'analytics-ios': 'mobile',
}

######################## GOOGLE ANALYTICS ###########################
GOOGLE_SITE_VERIFICATION_ID = None
GOOGLE_ANALYTICS_LINKEDIN = None
GOOGLE_ANALYTICS_TRACKING_ID = None
GOOGLE_ANALYTICS_4_ID = None

######################## BRANCH.IO ###########################
BRANCH_IO_KEY = None

######################## HOTJAR ###########################
HOTJAR_SITE_ID = 00000

######################## subdomain specific settings ###########################
COURSE_LISTINGS = {}

############# ModuleStore Configuration ##########

CONTENTSTORE['DOC_STORE_CONFIG']['password'] = 'password'
CONTENTSTORE['DOC_STORE_CONFIG']['read_preference'] = 'SECONDARY_PREFERRED'

MODULESTORE_BRANCH = 'published-only'

HOSTNAME_MODULESTORE_DEFAULT_MAPPINGS = {}
MONGODB_LOG = {}

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
    },

    # Overrides to default configurable 'limits' (above).
    # Keys should be course run ids (or, in the special case of code running
    # on the /debug/run_python page, the key is 'debug_run_python').
    # Values should be dictionaries that look like 'limits'.
    "limit_overrides": {},
}

# .. setting_name: PYTHON_LIB_FILENAME
# .. setting_default: python_lib.zip
# .. setting_description: Name of the course file to make available to code in
#   custom Python-graded problems. By default, this file will not be downloadable
#   by learners.
PYTHON_LIB_FILENAME = 'python_lib.zip'

############################### DJANGO BUILT-INS ###############################

# django-session-cookie middleware
DCS_SESSION_COOKIE_SAMESITE = 'None'
DCS_SESSION_COOKIE_SAMESITE_FORCE_ALL = True

# LMS base
LMS_BASE = 'localhost:18000'

# CMS base
CMS_BASE = 'studio.edx.org'

# Studio name
STUDIO_NAME = 'Studio'
STUDIO_SHORT_NAME = 'Studio'

# Platform Email
EMAIL_FILE_PATH = Derived(lambda settings: path(settings.DATA_DIR) / "emails" / "lms")
DEFAULT_FROM_EMAIL = 'registration@example.com'
DEFAULT_FEEDBACK_EMAIL = 'feedback@example.com'
SERVER_EMAIL = 'devops@example.com'
TECH_SUPPORT_EMAIL = 'technical@example.com'
CONTACT_EMAIL = 'info@example.com'
BUGS_EMAIL = 'bugs@example.com'
UNIVERSITY_EMAIL = 'university@example.com'
PRESS_EMAIL = 'press@example.com'
FINANCE_EMAIL = ''

# Platform mailing address
CONTACT_MAILING_ADDRESS = 'SET-ME-PLEASE'

# Account activation email sender address
ACTIVATION_EMAIL_FROM_ADDRESS = ''

# Static content
STATIC_URL = '/static/'
STATIC_ROOT = os.environ.get('STATIC_ROOT_LMS', ENV_ROOT / "staticfiles")

STATICFILES_DIRS.insert(2, NODE_MODULES_ROOT / "@edx")

# Guidelines for translators
TRANSLATORS_GUIDE = 'https://docs.openedx.org/en/latest/translators/index.html'

#################################### AWS #######################################
# The number of seconds that a generated URL is valid for.
AWS_QUERYSTRING_EXPIRE = 10 * 365 * 24 * 60 * 60  # 10 years

################################# SIMPLEWIKI ###################################
SIMPLE_WIKI_REQUIRE_LOGIN_EDIT = True
SIMPLE_WIKI_REQUIRE_LOGIN_VIEW = False

################################# WIKI ###################################
from lms.djangoapps.course_wiki import settings as course_wiki_settings  # pylint: disable=wrong-import-position

# .. toggle_name: WIKI_ACCOUNT_HANDLING
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: We recommend you leave this as 'False' for an Open edX installation
#   to get the proper behavior for register, login and logout. For the original docs see:
#   https://github.com/openedx/django-wiki/blob/edx_release/wiki/conf/settings.py
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2012-08-13
WIKI_ACCOUNT_HANDLING = False
WIKI_EDITOR = 'lms.djangoapps.course_wiki.editors.CodeMirror'
WIKI_SHOW_MAX_CHILDREN = 0  # We don't use the little menu that shows children of an article in the breadcrumb
# .. toggle_name: WIKI_ANONYMOUS
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Enabling this allows access to anonymous users.
#   For the original docs, see:
#   https://github.com/openedx/django-wiki/blob/edx_release/wiki/conf/settings.py
# .. toggle_warning: Setting allow anonymous access to `True` may have styling issues.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2012-08-21
WIKI_ANONYMOUS = False

WIKI_CAN_DELETE = course_wiki_settings.CAN_DELETE
WIKI_CAN_MODERATE = course_wiki_settings.CAN_MODERATE
WIKI_CAN_CHANGE_PERMISSIONS = course_wiki_settings.CAN_CHANGE_PERMISSIONS
WIKI_CAN_ASSIGN = course_wiki_settings.CAN_ASSIGN
# .. toggle_name: WIKI_USE_BOOTSTRAP_SELECT_WIDGET
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Enabling this will use the bootstrap select widget.
#   For the original docs, see:
#   https://github.com/openedx/django-wiki/blob/edx_release/wiki/conf/settings.py
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2012-08-22
WIKI_USE_BOOTSTRAP_SELECT_WIDGET = False
# .. toggle_name: WIKI_LINK_LIVE_LOOKUPS
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: This setting is defined in the original docs:
#   https://github.com/openedx/django-wiki/blob/edx_release/wiki/conf/settings.py
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2012-08-23
WIKI_LINK_LIVE_LOOKUPS = False
WIKI_LINK_DEFAULT_LEVEL = 2

##### Zendesk #####
ZENDESK_USER = None
ZENDESK_API_KEY = None

# Setting for PAID_COURSE_REGISTRATION, DOES NOT AFFECT VERIFIED STUDENTS
PAID_COURSE_REGISTRATION_CURRENCY = ['usd', '$']

################################# EdxNotes config  #########################

# Configure the LMS to use our stub EdxNotes implementation
# .. setting_name: EDXNOTES_PUBLIC_API
# .. setting_default: http://localhost:18120/api/v1
# .. setting_description: Set the public API endpoint LMS will use in the frontend to
#     interact with the edx_notes_api service.
# .. setting_warning: This setting must be a publicly accessible endpoint. It is only used
#     when the setting settings.ENABLE_EDXNOTES is activated.
EDXNOTES_PUBLIC_API = 'http://localhost:18120/api/v1'
# .. setting_name: EDXNOTES_INTERNAL_API
# .. setting_default: http://localhost:18120/api/v1
# .. setting_description: Set the internal API endpoint LMS will use in the backend to
#     interact with the edx_notes_api service.
# .. setting_warning: Normally set to the same value of EDXNOTES_PUBLIC_API. It is not
#     mandatory for this setting to be a publicly accessible endpoint, but to be accessible
#     by the LMS service. It is only used when the setting settings.ENABLE_EDXNOTES is
#     activated.
EDXNOTES_INTERNAL_API = 'http://localhost:18120/api/v1'
# .. setting_name: EDXNOTES_CLIENT_NAME
# .. setting_default: edx-notes
# .. setting_description: Set the name of the Oauth client used by LMS to authenticate with
#     the edx_notes_api service.
# .. setting_warning: The Oauth client must be created in the platform Django admin in the
#     path /admin/oauth2_provider/application/, setting the name field of the client as the
#     value of this setting.
EDXNOTES_CLIENT_NAME = "edx-notes"
# .. setting_name: EDXNOTES_CONNECT_TIMEOUT
# .. setting_default: 0.5
# .. setting_description: Set the number of seconds LMS will wait to establish an internal
#     connection to the edx_notes_api service.
EDXNOTES_CONNECT_TIMEOUT = 0.5  # time in seconds
# .. setting_name: EDXNOTES_READ_TIMEOUT
# .. setting_default: 1.5
# .. setting_description: Set the number of seconds LMS will wait for a response from the
#     edx_notes_api service internal endpoint.
EDXNOTES_READ_TIMEOUT = 1.5  # time in seconds

######################### Branded Footer ###################################
# Constants for the footer used on the site and shared with other sites
# (such as marketing and the blog) via the branding API.

# URL for Open edX displayed in the footer
FOOTER_OPENEDX_URL = "https://open.edx.org"

# URL for the OpenEdX logo image
# We use logo images served from files.edx.org so we can (roughly) track
# how many OpenEdX installations are running.
# Site operators can choose from these logo options:
# * https://logos.openedx.org/open-edx-logo-tag.png
# * https://logos.openedx.org/open-edx-logo-tag-light.png"
# * https://logos.openedx.org/open-edx-logo-tag-dark.png
FOOTER_OPENEDX_LOGO_IMAGE = "https://logos.openedx.org/open-edx-logo-tag.png"

# These are referred to both by the Django asset pipeline
# AND by the branding footer API, which needs to decide which
# version of the CSS to serve.
FOOTER_CSS = {
    "openedx": {
        "ltr": "style-lms-footer",
        "rtl": "style-lms-footer-rtl",
    },
    "edx": {
        "ltr": "style-lms-footer-edx",
        "rtl": "style-lms-footer-edx-rtl",
    },
}

# Cache expiration for the version of the footer served
# by the branding API.
FOOTER_CACHE_TIMEOUT = 30 * 60

# Max age cache control header for the footer (controls browser caching).
FOOTER_BROWSER_CACHE_MAX_AGE = 5 * 60

# Credit api notification cache timeout
CREDIT_NOTIFICATION_CACHE_TIMEOUT = 5 * 60 * 60

################################# Middleware ###################################

MIDDLEWARE = [
    'openedx.core.lib.x_forwarded_for.middleware.XForwardedForMiddleware',
    'edx_django_utils.security.csp.middleware.content_security_policy_middleware',

    'crum.CurrentRequestUserMiddleware',

    # Resets the request cache.
    'edx_django_utils.cache.middleware.RequestCacheMiddleware',

    # Various monitoring middleware
    'edx_django_utils.monitoring.CachedCustomMonitoringMiddleware',
    'edx_django_utils.monitoring.CodeOwnerMonitoringMiddleware',
    'edx_django_utils.monitoring.CookieMonitoringMiddleware',
    'edx_django_utils.monitoring.DeploymentMonitoringMiddleware',
    'edx_django_utils.monitoring.FrontendMonitoringMiddleware',

    # Monitoring and logging for ignored errors
    'openedx.core.lib.request_utils.IgnoredErrorMiddleware',

    'lms.djangoapps.mobile_api.middleware.AppVersionUpgrade',
    'openedx.core.djangoapps.header_control.middleware.HeaderControlMiddleware',
    'lms.djangoapps.discussion.django_comment_client.middleware.AjaxExceptionMiddleware',
    'django.middleware.common.CommonMiddleware',

    'django.contrib.sites.middleware.CurrentSiteMiddleware',
    'edx_rest_framework_extensions.auth.jwt.middleware.JwtAuthCookieMiddleware',

    # Allows us to define redirects via Django admin
    'django_sites_extensions.middleware.RedirectMiddleware',

    # Instead of SessionMiddleware, we use a more secure version
    # 'django.contrib.sessions.middleware.SessionMiddleware',
    'openedx.core.djangoapps.safe_sessions.middleware.SafeSessionMiddleware',

    # Instead of AuthenticationMiddleware, we use a cached backed version
    #'django.contrib.auth.middleware.AuthenticationMiddleware',
    'openedx.core.djangoapps.cache_toolbox.middleware.CacheBackedAuthenticationMiddleware',

    # Middleware to flush user's session in other browsers when their email is changed.
    'openedx.core.djangoapps.safe_sessions.middleware.EmailChangeMiddleware',

    'common.djangoapps.student.middleware.UserStandingMiddleware',

    # Adds user tags to tracking events
    # Must go before TrackMiddleware, to get the context set up
    'openedx.core.djangoapps.user_api.middleware.UserTagsEventContextMiddleware',

    'django.contrib.messages.middleware.MessageMiddleware',
    'common.djangoapps.track.middleware.TrackMiddleware',

    # CORS and CSRF
    'django.middleware.csrf.CsrfViewMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'openedx.core.djangoapps.cors_csrf.middleware.CorsCSRFMiddleware',
    'openedx.core.djangoapps.cors_csrf.middleware.CsrfCrossDomainCookieMiddleware',

    'openedx.core.djangoapps.geoinfo.middleware.CountryMiddleware',
    'openedx.core.djangoapps.embargo.middleware.EmbargoMiddleware',

    # Allows us to use enterprise customer's language as the learner's default language
    # This middleware must come before `LanguagePreferenceMiddleware` middleware
    'enterprise.middleware.EnterpriseLanguagePreferenceMiddleware',

    # Allows us to set user preferences
    'openedx.core.djangoapps.lang_pref.middleware.LanguagePreferenceMiddleware',

    # Allows us to dark-launch particular languages.
    # Must be after LangPrefMiddleware, so ?preview-lang query params can override
    # user's language preference. ?clear-lang resets to user's language preference.
    'openedx.core.djangoapps.dark_lang.middleware.DarkLangMiddleware',

    # Detects user-requested locale from 'accept-language' header in http request.
    # Must be after DarkLangMiddleware.
    'django.middleware.locale.LocaleMiddleware',

    'lms.djangoapps.discussion.django_comment_client.utils.ViewNameMiddleware',
    'codejail.django_integration.ConfigureCodeJailMiddleware',

    # for expiring inactive sessions
    'openedx.core.djangoapps.session_inactivity_timeout.middleware.SessionInactivityTimeout',

    # use Django built in clickjacking protection
    'django.middleware.clickjacking.XFrameOptionsMiddleware',

    # to redirected unenrolled students to the course info page
    'lms.djangoapps.courseware.middleware.CacheCourseIdMiddleware',
    'lms.djangoapps.courseware.middleware.RedirectMiddleware',

    'lms.djangoapps.course_wiki.middleware.WikiAccessMiddleware',

    'openedx.core.djangoapps.theming.middleware.CurrentSiteThemeMiddleware',

    'waffle.middleware.WaffleMiddleware',

    # Enables force_django_cache_miss functionality for TieredCache.
    'edx_django_utils.cache.middleware.TieredCacheMiddleware',

    # Adds monitoring attributes to requests.
    'edx_rest_framework_extensions.middleware.RequestCustomAttributesMiddleware',

    'edx_rest_framework_extensions.auth.jwt.middleware.EnsureJWTAuthSettingsMiddleware',

    # Handles automatically storing user ids in django-simple-history tables when possible.
    'simple_history.middleware.HistoryRequestMiddleware',

    # This must be last
    'openedx.core.djangoapps.site_configuration.middleware.SessionCookieDomainOverrideMiddleware',
]

############################### PIPELINE #######################################

PIPELINE['JS_COMPRESSOR'] = 'pipeline.compressors.uglifyjs.UglifyJSCompressor'

from openedx.core.lib.rooted_paths import rooted_glob  # pylint: disable=wrong-import-position

courseware_js = [
    'js/ajax-error.js',
    'js/courseware.js',
    'js/histogram.js',
    'js/navigation.js',
    'js/modules/tab.js',
]


# Before a student accesses courseware, we do not
# need many of the JS dependencies.  This includes
# only the dependencies used everywhere in the LMS
# (including the dashboard/account/profile pages)
# Currently, this partially duplicates the "main vendor"
# JavaScript file, so only one of the two should be included
# on a page at any time.
# In the future, we will likely refactor this to use
# RequireJS and an optimizer.
base_vendor_js = [
    'common/js/vendor/jquery.js',
    'common/js/vendor/jquery-migrate.js',
    'js/vendor/jquery.cookie.js',
    'js/vendor/url.min.js',
    'common/js/vendor/underscore.js',
    'common/js/vendor/underscore.string.js',

    # Make some edX UI Toolkit utilities available in the global "edx" namespace
    'edx-ui-toolkit/js/utils/global-loader.js',
    'edx-ui-toolkit/js/utils/string-utils.js',
    'edx-ui-toolkit/js/utils/html-utils.js',

    # Finally load RequireJS and dependent vendor libraries
    'common/js/vendor/require.js',
    'js/RequireJS-namespace-undefine.js',
    'js/vendor/URI.min.js',
    'common/js/vendor/backbone.js'
]

main_vendor_js = base_vendor_js + [
    'js/vendor/json2.js',
    'js/vendor/jquery-ui.min.js',
    'js/vendor/jquery.qtip.min.js',
    'js/vendor/jquery.ba-bbq.min.js',
]

# Common files used by both RequireJS code and non-RequireJS code
base_application_js = [
    'js/src/utility.js',
    'js/src/logger.js',
    'js/user_dropdown_v1.js',  # Custom dropdown keyboard handling for legacy pages
    'js/dialog_tab_controls.js',
    'js/src/string_utils.js',
    'js/form.ext.js',
    'js/src/ie_shim.js',
    'js/src/accessibility_tools.js',
    'js/toggle_login_modal.js',
    'js/src/lang_edx.js',
]

dashboard_js = (
    sorted(rooted_glob(PROJECT_ROOT / 'static', 'js/dashboard/**/*.js'))
)
discussion_js = (
    rooted_glob(PROJECT_ROOT / 'static', 'js/customwmd.js') +
    rooted_glob(PROJECT_ROOT / 'static', 'js/mathjax_accessible.js') +
    rooted_glob(PROJECT_ROOT / 'static', 'js/mathjax_delay_renderer.js') +
    sorted(rooted_glob(COMMON_ROOT / 'static', 'common/js/discussion/**/*.js'))
)

discussion_vendor_js = [
    'js/Markdown.Converter.js',
    'js/Markdown.Sanitizer.js',
    'js/Markdown.Editor.js',
    'js/vendor/jquery.timeago.js',
    'js/src/jquery.timeago.locale.js',
    'js/vendor/jquery.truncate.js',
    'js/jquery.ajaxfileupload.js',
    'js/split.js'
]

instructor_dash_js = sorted(rooted_glob(PROJECT_ROOT / 'static', 'js/instructor_dashboard/**/*.js'))

verify_student_js = [
    'js/sticky_filter.js',
    'js/query-params.js',
    'js/verify_student/models/verification_model.js',
    'js/verify_student/views/error_view.js',
    'js/verify_student/views/image_input_view.js',
    'js/verify_student/views/webcam_photo_view.js',
    'js/verify_student/views/step_view.js',
    'js/verify_student/views/intro_step_view.js',
    'js/verify_student/views/make_payment_step_view.js',
    'js/verify_student/views/face_photo_step_view.js',
    'js/verify_student/views/id_photo_step_view.js',
    'js/verify_student/views/review_photos_step_view.js',
    'js/verify_student/views/enrollment_confirmation_step_view.js',
    'js/verify_student/views/pay_and_verify_view.js',
    'js/verify_student/pay_and_verify.js',
]

reverify_js = [
    'js/verify_student/views/error_view.js',
    'js/verify_student/views/image_input_view.js',
    'js/verify_student/views/webcam_photo_view.js',
    'js/verify_student/views/step_view.js',
    'js/verify_student/views/face_photo_step_view.js',
    'js/verify_student/views/id_photo_step_view.js',
    'js/verify_student/views/review_photos_step_view.js',
    'js/verify_student/views/reverify_success_step_view.js',
    'js/verify_student/models/verification_model.js',
    'js/verify_student/views/reverify_view.js',
    'js/verify_student/reverify.js',
]

incourse_reverify_js = [
    'js/verify_student/views/error_view.js',
    'js/verify_student/views/image_input_view.js',
    'js/verify_student/views/webcam_photo_view.js',
    'js/verify_student/models/verification_model.js',
    'js/verify_student/views/incourse_reverify_view.js',
    'js/verify_student/incourse_reverify.js',
]

ccx_js = sorted(rooted_glob(PROJECT_ROOT / 'static', 'js/ccx/**/*.js'))

certificates_web_view_js = [
    'common/js/vendor/jquery.js',
    'common/js/vendor/jquery-migrate.js',
    'js/vendor/jquery.cookie.js',
    'js/src/logger.js',
    'js/utils/facebook.js',
]

credit_web_view_js = [
    'common/js/vendor/jquery.js',
    'common/js/vendor/jquery-migrate.js',
    'js/vendor/jquery.cookie.js',
    'js/src/logger.js',
]

PIPELINE['STYLESHEETS'] = {
    'style-vendor': {
        'source_filenames': [
            'css/vendor/font-awesome.css',
            'css/vendor/jquery.qtip.min.css',
        ],
        'output_filename': 'css/lms-style-vendor.css',
    },
    'style-vendor-tinymce-content': {
        'source_filenames': [
            'js/vendor/tinymce/js/tinymce/skins/ui/studio-tmce5/content.min.css'
        ],
        'output_filename': 'css/lms-style-vendor-tinymce-content.css',
    },
    'style-vendor-tinymce-skin': {
        'source_filenames': [
            'js/vendor/tinymce/js/tinymce/skins/ui/studio-tmce5/skin.min.css'
        ],
        'output_filename': 'css/lms-style-vendor-tinymce-skin.css',
    },
    'style-main-v1': {
        'source_filenames': [
            'css/lms-main-v1.css',
        ],
        'output_filename': 'css/lms-main-v1.css',
    },
    'style-main-v1-rtl': {
        'source_filenames': [
            'css/lms-main-v1-rtl.css',
        ],
        'output_filename': 'css/lms-main-v1-rtl.css',
    },
    'style-course-vendor': {
        'source_filenames': [
            'js/vendor/CodeMirror/codemirror.css',
            'css/vendor/jquery.treeview.css',
            'css/vendor/ui-lightness/jquery-ui-1.8.22.custom.css',
        ],
        'output_filename': 'css/lms-style-course-vendor.css',
    },
    'style-course': {
        'source_filenames': [
            'css/lms-course.css',
        ],
        'output_filename': 'css/lms-course.css',
    },
    'style-course-rtl': {
        'source_filenames': [
            'css/lms-course-rtl.css',
        ],
        'output_filename': 'css/lms-course-rtl.css',
    },
    'style-student-notes': {
        'source_filenames': [
            'css/vendor/edxnotes/annotator.min.css',
        ],
        'output_filename': 'css/lms-style-student-notes.css',
    },
    'style-inline-discussion': {
        'source_filenames': [
            'css/discussion/inline-discussion.css',
        ],
        'output_filename': 'css/discussion/inline-discussion.css',
    },
    'style-inline-discussion-rtl': {
        'source_filenames': [
            'css/discussion/inline-discussion-rtl.css',
        ],
        'output_filename': 'css/discussion/inline-discussion-rtl.css',
    },
    FOOTER_CSS['openedx']['ltr']: {
        'source_filenames': [
            'css/lms-footer.css',
        ],
        'output_filename': 'css/lms-footer.css',
    },
    FOOTER_CSS['openedx']['rtl']: {
        'source_filenames': [
            'css/lms-footer-rtl.css',
        ],
        'output_filename': 'css/lms-footer-rtl.css'
    },
    FOOTER_CSS['edx']['ltr']: {
        'source_filenames': [
            'css/lms-footer-edx.css',
        ],
        'output_filename': 'css/lms-footer-edx.css'
    },
    FOOTER_CSS['edx']['rtl']: {
        'source_filenames': [
            'css/lms-footer-edx-rtl.css',
        ],
        'output_filename': 'css/lms-footer-edx-rtl.css'
    },
    'style-certificates': {
        'source_filenames': [
            'certificates/css/main-ltr.css',
            'css/vendor/font-awesome.css',
        ],
        'output_filename': 'css/certificates-style.css'
    },
    'style-certificates-rtl': {
        'source_filenames': [
            'certificates/css/main-rtl.css',
            'css/vendor/font-awesome.css',
        ],
        'output_filename': 'css/certificates-style-rtl.css'
    },
    'style-mobile': {
        'source_filenames': [
            'css/lms-mobile.css',
        ],
        'output_filename': 'css/lms-mobile.css',
    },
    'style-mobile-rtl': {
        'source_filenames': [
            'css/lms-mobile-rtl.css',
        ],
        'output_filename': 'css/lms-mobile-rtl.css',
    },
}

common_js = [
    'js/src/ajax_prefix.js',
    'js/src/jquery.immediateDescendents.js',
    'js/src/xproblem.js',
]
xblock_runtime_js = [
    'common/js/xblock/core.js',
    'common/js/xblock/runtime.v1.js',
    'lms/js/xblock/lms.runtime.v1.js',
]
lms_application_js = [
    'js/calculator.js',
    'js/feedback_form.js',
    'js/main.js',
]

PIPELINE['JAVASCRIPT'] = {
    'base_application': {
        'source_filenames': base_application_js,
        'output_filename': 'js/lms-base-application.js',
    },
    'application': {
        'source_filenames': (
            common_js + xblock_runtime_js + base_application_js + lms_application_js +
            [
                'js/sticky_filter.js',
                'js/query-params.js',
                'common/js/vendor/moment-with-locales.js',
                'common/js/vendor/moment-timezone-with-data.js',
            ]
        ),
        'output_filename': 'js/lms-application.js',
    },
    'courseware': {
        'source_filenames': courseware_js,
        'output_filename': 'js/lms-courseware.js',
    },
    'base_vendor': {
        'source_filenames': base_vendor_js,
        'output_filename': 'js/lms-base-vendor.js',
    },
    'main_vendor': {
        'source_filenames': main_vendor_js,
        'output_filename': 'js/lms-main_vendor.js',
    },
    'discussion': {
        'source_filenames': discussion_js,
        'output_filename': 'js/discussion.js',
    },
    'discussion_vendor': {
        'source_filenames': discussion_vendor_js,
        'output_filename': 'js/discussion_vendor.js',
    },
    'instructor_dash': {
        'source_filenames': instructor_dash_js,
        'output_filename': 'js/instructor_dash.js',
    },
    'dashboard': {
        'source_filenames': dashboard_js,
        'output_filename': 'js/dashboard.js'
    },
    'verify_student': {
        'source_filenames': verify_student_js,
        'output_filename': 'js/verify_student.js'
    },
    'reverify': {
        'source_filenames': reverify_js,
        'output_filename': 'js/reverify.js'
    },
    'incourse_reverify': {
        'source_filenames': incourse_reverify_js,
        'output_filename': 'js/incourse_reverify.js'
    },
    'ccx': {
        'source_filenames': ccx_js,
        'output_filename': 'js/ccx.js'
    },
    'footer_edx': {
        'source_filenames': ['js/footer-edx.js'],
        'output_filename': 'js/footer-edx.js'
    },
    'certificates_wv': {
        'source_filenames': certificates_web_view_js,
        'output_filename': 'js/certificates/web_view.js'
    },
    'credit_wv': {
        'source_filenames': credit_web_view_js,
        'output_filename': 'js/credit/web_view.js'
    }
}

################################# DJANGO-REQUIRE ###############################

# The name of the require.js script used by your project, relative to REQUIRE_BASE_URL.
REQUIRE_JS = "common/js/vendor/require.js"

# In production, the Django pipeline appends a file hash to JavaScript file names.
# This makes it difficult for RequireJS to load its requirements, since module names
# specified in JavaScript code do not include the hash.
# For this reason, we calculate the actual path including the hash on the server
# when rendering the page.  We then override the default paths provided to RequireJS
# so it can resolve the module name to the correct URL.
#
# If you want to load JavaScript dependencies using RequireJS
# but you don't want to include those dependencies in the JS bundle for the page,
# then you need to add the js urls in this list.
REQUIRE_JS_PATH_OVERRIDES = {
    'course_bookmarks/js/views/bookmark_button': 'course_bookmarks/js/views/bookmark_button.js',
    'js/views/message_banner': 'js/views/message_banner.js',
    'moment': 'common/js/vendor/moment-with-locales.js',
    'moment-timezone': 'common/js/vendor/moment-timezone-with-data.js',
    'js/courseware/course_info_events': 'js/courseware/course_info_events.js',
    'js/courseware/accordion_events': 'js/courseware/accordion_events.js',
    'js/dateutil_factory': 'js/dateutil_factory.js',
    'js/courseware/link_clicked_events': 'js/courseware/link_clicked_events.js',
    'js/courseware/toggle_element_visibility': 'js/courseware/toggle_element_visibility.js',
    'js/student_account/logistration_factory': 'js/student_account/logistration_factory.js',
    'js/courseware/courseware_factory': 'js/courseware/courseware_factory.js',
    'js/groups/views/cohorts_dashboard_factory': 'js/groups/views/cohorts_dashboard_factory.js',
    'js/groups/discussions_management/discussions_dashboard_factory':
        'js/discussions_management/views/discussions_dashboard_factory.js',
    'draggabilly': 'js/vendor/draggabilly.js',
    'hls': 'common/js/vendor/hls.js'
}

############################ SERVICE_VARIANT ##################################

SERVICE_VARIANT = 'lms'

################################# CELERY ######################################

CELERY_IMPORTS = [
    # Since xblock-poll is not a Django app, and XBlocks don't get auto-imported
    # by celery workers, its tasks will not get auto-discovered:
    'poll.tasks',
]

# .. setting_name: CELERY_EXTRA_IMPORTS
# .. setting_default: []
# .. setting_description: Adds extra packages that don't get auto-imported (Example: XBlocks).
#    These packages are added in addition to those added by CELERY_IMPORTS.
CELERY_EXTRA_IMPORTS = []

# Name the exchange and queues w.r.t the SERVICE_VARIANT
HIGH_PRIORITY_QUEUE = f'edx.{SERVICE_VARIANT}.core.high'
DEFAULT_PRIORITY_QUEUE = f'edx.{SERVICE_VARIANT}.core.default'
HIGH_MEM_QUEUE = f'edx.{SERVICE_VARIANT}.core.high_mem'

CELERY_QUEUES = {
    HIGH_PRIORITY_QUEUE: {},
    DEFAULT_PRIORITY_QUEUE: {},
    HIGH_MEM_QUEUE: {},
}

CELERY_ROUTES = "openedx.core.lib.celery.routers.route_task"
CELERYBEAT_SCHEDULE = {}  # For scheduling tasks, entries can be added to this dict

# let logging work as configured:
CELERYD_HIJACK_ROOT_LOGGER = False

BROKER_USE_SSL = False

################################ Bulk Email ###################################

# Initial delay used for retrying tasks.  Additional retries use
# longer delays.  Value is in seconds.
BULK_EMAIL_DEFAULT_RETRY_DELAY = 30

# Maximum number of retries per task for errors that are not related
# to throttling.
BULK_EMAIL_MAX_RETRIES = 5

# Maximum number of retries per task for errors that are related to
# throttling.  If this is not set, then there is no cap on such retries.
BULK_EMAIL_INFINITE_RETRY_CAP = 1000

# We want Bulk Email running on the high-priority queue, so we define the
# routing key that points to it.  At the moment, the name is the same.
BULK_EMAIL_ROUTING_KEY = Derived(lambda settings: settings.HIGH_PRIORITY_QUEUE)

# We also define a queue for smaller jobs so that large courses don't block
# smaller emails (see BULK_EMAIL_JOB_SIZE_THRESHOLD setting)
BULK_EMAIL_ROUTING_KEY_SMALL_JOBS = Derived(lambda settings: settings.DEFAULT_PRIORITY_QUEUE)

# For emails with fewer than these number of recipients, send them through
# a different queue to avoid large courses blocking emails that are meant to be
# sent to self and staff
BULK_EMAIL_JOB_SIZE_THRESHOLD = 100

# Delay in seconds to sleep between individual mail messages being sent,
# when a bulk email task is retried for rate-related reasons.  Choose this
# value depending on the number of workers that might be sending email in
# parallel, and what the SES rate is.
BULK_EMAIL_RETRY_DELAY_BETWEEN_SENDS = 0.02

############################# Email Opt In ####################################

# Minimum age for organization-wide email opt in
EMAIL_OPTIN_MINIMUM_AGE = PARENTAL_CONSENT_AGE_LIMIT

################################### APPS ######################################

# The order of INSTALLED_APPS is important, when adding new apps here remember to check that you are not creating new
# RemovedInDjango19Warnings in the test logs.
#
# If you want to add a new djangoapp that isn't suitable for everyone, you have some options:
# - Add it to OPTIONAL_APPS below (registered if importable)
# - Add it to the ADDL_INSTALLED_APPS configuration variable (acts like EXTRA_APPS in other IDAs)
# - Make it a plugin (which are auto-registered) and add it to the EDXAPP_PRIVATE_REQUIREMENTS configuration variable
#   (See https://github.com/openedx/edx-django-utils/tree/master/edx_django_utils/plugins)
INSTALLED_APPS = [
    # Standard ones that are always installed...
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.humanize',
    'django.contrib.messages',
    'django.contrib.redirects',
    'django.contrib.sessions',
    'django.contrib.sites',

    'dal',
    'dal_select2',

    # Tweaked version of django.contrib.staticfiles
    'openedx.core.djangoapps.staticfiles.apps.EdxPlatformStaticFilesConfig',

    'django_celery_results',

    # Common Initialization
    'openedx.core.djangoapps.common_initialization.apps.CommonInitializationConfig',

    # LMS-specific Initialization
    'lms.djangoapps.lms_initialization.apps.LMSInitializationConfig',

    # Common views
    'openedx.core.djangoapps.common_views',

    # History tables
    'simple_history',

    # Database-backed configuration
    'config_models',
    'openedx.core.djangoapps.config_model_utils',
    'waffle',

    # Monitor the status of services
    'openedx.core.djangoapps.service_status',

    # Display status message to students
    'common.djangoapps.status',

    # For asset pipelining
    'common.djangoapps.edxmako.apps.EdxMakoConfig',
    'pipeline',
    'common.djangoapps.static_replace',
    'webpack_loader',

    # For user interface plugins
    'web_fragments',
    'openedx.core.djangoapps.plugin_api',

    # For content serving
    'openedx.core.djangoapps.contentserver',

    # Site configuration for theming and behavioral modification
    'openedx.core.djangoapps.site_configuration',

    # Video block configs (This will be moved to Video once it becomes an XBlock)
    'openedx.core.djangoapps.video_config',

    # edX Video Pipeline integration
    'openedx.core.djangoapps.video_pipeline',

    # Our courseware
    'lms.djangoapps.courseware',
    'lms.djangoapps.coursewarehistoryextended',
    'common.djangoapps.student.apps.StudentConfig',
    'common.djangoapps.split_modulestore_django.apps.SplitModulestoreDjangoBackendAppConfig',

    'lms.djangoapps.static_template_view',
    'lms.djangoapps.staticbook',
    'common.djangoapps.track',
    'eventtracking.django.apps.EventTrackingConfig',
    'common.djangoapps.util',
    'lms.djangoapps.certificates.apps.CertificatesConfig',
    'lms.djangoapps.instructor_task',
    'openedx.core.djangoapps.course_groups',
    'lms.djangoapps.bulk_email',
    'lms.djangoapps.branding',

    # Course home api
    'lms.djangoapps.course_home_api',

    # User tours
    'lms.djangoapps.user_tours',

    # New (Learning-Core-based) XBlock runtime
    'openedx.core.djangoapps.xblock.apps.LmsXBlockAppConfig',

    # Student support tools
    'lms.djangoapps.support',

    # django-oauth-toolkit
    'oauth2_provider',
    'openedx.core.djangoapps.oauth_dispatch.apps.OAuthDispatchAppConfig',

    'common.djangoapps.third_party_auth',

    # System Wide Roles
    'openedx.core.djangoapps.system_wide_roles',

    'openedx.core.djangoapps.auth_exchange',

    # For the wiki
    'wiki',  # The new django-wiki from benjaoming
    'django_notify',
    'lms.djangoapps.course_wiki',  # Our customizations
    'mptt',
    'sekizai',
    #'wiki.plugins.attachments',
    'wiki.plugins.links',
    # Notifications were enabled, but only 11 people used it in three years. It
    # got tangled up during the Django 1.8 migration, so we are disabling it.
    # See TNL-3783 for details.
    #'wiki.plugins.notifications',
    'lms.djangoapps.course_wiki.plugins.markdownedx',

    # For testing
    'django.contrib.admin',  # only used in DEBUG mode
    'lms.djangoapps.debug',
    'openedx.core.djangoapps.util.apps.UtilConfig',

    # Discussion forums
    'openedx.core.djangoapps.django_comment_common',

    # Notes
    'lms.djangoapps.edxnotes',

    # Django Rest Framework
    'rest_framework',

    # REST framework JWT Auth
    'rest_framework_jwt',

    # User API
    'openedx.core.djangoapps.user_api',

    # Different Course Modes
    'common.djangoapps.course_modes.apps.CourseModesConfig',

    # Enrollment API
    'openedx.core.djangoapps.enrollments.apps.EnrollmentsConfig',

    # Entitlement API
    'common.djangoapps.entitlements.apps.EntitlementsConfig',

    # Bulk Enrollment API
    'lms.djangoapps.bulk_enroll',

    # Student Identity Verification
    'lms.djangoapps.verify_student.apps.VerifyStudentConfig',

    # Dark-launching languages
    'openedx.core.djangoapps.dark_lang',

    # RSS Proxy
    'lms.djangoapps.rss_proxy',

    # Country embargo support
    'openedx.core.djangoapps.embargo',

    # Course action state
    'common.djangoapps.course_action_state',

    # Country list
    'django_countries',

    # edX Mobile API
    'lms.djangoapps.mobile_api.apps.MobileApiConfig',
    'social_django',

    # Surveys
    'lms.djangoapps.survey.apps.SurveyConfig',

    'lms.djangoapps.lms_xblock.apps.LMSXBlockConfig',

    # Course data caching
    'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig',
    'openedx.core.djangoapps.content.block_structure.apps.BlockStructureConfig',
    'lms.djangoapps.course_blocks',

    # Mailchimp Syncing
    'lms.djangoapps.mailing',

    # CORS and cross-domain CSRF
    'corsheaders',
    'openedx.core.djangoapps.cors_csrf',

    'lms.djangoapps.commerce.apps.CommerceConfig',

    # Credit courses
    'openedx.core.djangoapps.credit.apps.CreditConfig',

    # Course teams
    'lms.djangoapps.teams',

    'common.djangoapps.xblock_django',

    # programs support
    'openedx.core.djangoapps.programs.apps.ProgramsConfig',

    # Catalog integration
    'openedx.core.djangoapps.catalog',

    'sorl.thumbnail',

    # edx-milestones service
    'milestones',

    # Gating of course content
    'lms.djangoapps.gating.apps.GatingConfig',

    # Static i18n support
    'statici18n',

    # API access administration
    'openedx.core.djangoapps.api_admin',

    # Verified Track Content Cohorting (Beta feature that will hopefully be removed)
    'openedx.core.djangoapps.verified_track_content',

    # Learner's dashboard
    'lms.djangoapps.learner_dashboard',

    # Needed whether or not enabled, due to migrations
    'lms.djangoapps.badges.apps.BadgesConfig',

    # Enables default site and redirects
    'django_sites_extensions',

    # additional release utilities to ease automation
    'release_util',

    # rule-based authorization
    'rules.apps.AutodiscoverRulesConfig',
    'bridgekeeper',

    # management of user-triggered async tasks (course import/export, etc.)
    'user_tasks',

    # Customized celery tasks, including persisting failed tasks so they can
    # be retried
    'celery_utils',

    # Ability to detect and special-case crawler behavior
    'openedx.core.djangoapps.crawlers',

    # Unusual migrations
    'common.djangoapps.database_fixups',

    'openedx.core.djangoapps.waffle_utils',

    # Course Goals
    'lms.djangoapps.course_goals.apps.CourseGoalsConfig',

    # Tagging
    'openedx_tagging',
    'openedx.core.djangoapps.content_tagging',

    # Features
    'openedx.features.calendar_sync',
    'openedx.features.course_bookmarks',
    'openedx.features.course_experience',
    'openedx.features.enterprise_support.apps.EnterpriseSupportConfig',
    'openedx.features.course_duration_limits',
    'openedx.features.content_type_gating',
    'openedx.features.discounts',
    'openedx.features.effort_estimation',
    'openedx.features.name_affirmation_api.apps.NameAffirmationApiConfig',

    'lms.djangoapps.experiments',

    # DRF filters
    'django_filters',

    # API Documentation
    'drf_yasg',

    # edx-drf-extensions
    'csrf.apps.CsrfAppConfig',  # Enables frontend apps to retrieve CSRF tokens.
    'xss_utils',

    # so sample_task is available to celery workers
    'openedx.core.djangoapps.heartbeat',

    # signal handlers to capture course dates into edx-when
    'openedx.core.djangoapps.course_date_signals',

    # Management of external user ids
    'openedx.core.djangoapps.external_user_ids',

    # Management of per-user schedules
    'openedx.core.djangoapps.schedules',

    # Learning Sequence Navigation
    'openedx.core.djangoapps.content.learning_sequences.apps.LearningSequencesConfig',

    # Database-backed Organizations App (http://github.com/openedx/edx-organizations)
    'organizations',

    # Bulk User Retirement
    'lms.djangoapps.bulk_user_retirement',

    # Agreements
    'openedx.core.djangoapps.agreements',

    # Survey reports
    'openedx.features.survey_report',

    # User and group management via edx-django-utils
    'edx_django_utils.user',

    # Content Library LTI 1.3 Support.
    'pylti1p3.contrib.django.lti1p3_tool_config',

    # For edx ace template tags
    'edx_ace',

    # MFE API
    'lms.djangoapps.mfe_config_api',

    # Notifications
    'openedx.core.djangoapps.notifications',

    'openedx_events',

    # Core apps that power libraries
    "openedx_content",
    *openedx_content_backcompat_apps_to_install(),
]

# Add LMS specific optional apps
OPTIONAL_APPS += [
    # Channel Integrations Apps
    ('channel_integrations.integrated_channel', None),
    ('channel_integrations.degreed2', None),
    ('channel_integrations.sap_success_factors', None),
    ('channel_integrations.cornerstone', None),
    ('channel_integrations.xapi', None),
    ('channel_integrations.blackboard', None),
    ('channel_integrations.canvas', None),
    ('channel_integrations.moodle', None),

    # Required by the Enterprise App
    ('django_object_actions', None),  # https://github.com/crccheck/django-object-actions
]

add_optional_apps(OPTIONAL_APPS, INSTALLED_APPS)

######################### Django Rest Framework ########################

SWAGGER_SETTINGS = {
    'DEFAULT_INFO': 'openedx.core.apidocs.api_info',
    'DEEP_LINKING': True,
}

######################### MARKETING SITE ###############################

MKTG_URL_LINK_MAP.update({
    'ABOUT': 'about',
    'CONTACT': 'contact',
    'FAQ': 'help',
    'COURSES': 'courses',
    'ROOT': 'root',
    'TOS': 'tos',
    'HONOR': 'honor',  # If your site does not have an honor code, simply delete this line.
    'TOS_AND_HONOR': 'edx-terms-service',
    'PRIVACY': 'privacy',
    'PRESS': 'press',
    'BLOG': 'blog',
    'DONATE': 'donate',
    'SITEMAP.XML': 'sitemap_xml',

    # Verified Certificates
    'WHAT_IS_VERIFIED_CERT': 'verified-certificate',
})

STATIC_TEMPLATE_VIEW_DEFAULT_FILE_EXTENSION = 'html'

SEND_ACTIVATION_EMAIL_URL = ''

# .. setting_name: SECURITY_PAGE_URL
# .. setting_default: None
# .. setting_description: A link to the site's security disclosure/reporting policy,
#   to display in the site footer. This will only appear for sites using themes that
#   use the links produced by ``lms.djangoapps.branding.api.get_footer``.
SECURITY_PAGE_URL = None

# Days before the expired date that we warn the user
ENTITLEMENT_EXPIRED_ALERT_PERIOD = 90

############################# SOCIAL MEDIA SHARING #############################
# Social Media Sharing on Student Dashboard
SOCIAL_SHARING_SETTINGS.update({
    'FACEBOOK_BRAND': None,
    'CERTIFICATE_FACEBOOK_TEXT': None,
    'TWITTER_BRAND': None,
    'CERTIFICATE_TWITTER_TEXT': None,
    'DASHBOARD_TWITTER_TEXT': None,
})

################# Social Media Footer Links #######################
# The names list controls the order of social media
# links in the footer.
SOCIAL_MEDIA_FOOTER_NAMES = [
    "facebook",
    "twitter",
    # "youtube", see PROD-816 for more details
    "linkedin",
    "instagram",
    "reddit",
]

# The mobile store URLs dictionary maps mobile store names
# to URLs defined in configuration.
MOBILE_STORE_ACE_URLS = {
    'google': 'https://play.google.com/store/apps/details?id=org.edx.mobile',
    'apple': 'https://itunes.apple.com/us/app/edx/id945480667?mt=8',
}

# The mobile store logo urls dictionary maps mobile store names
# to the respective icons
MOBILE_STORE_LOGO_URLS = {
    'google': 'http://email-media.s3.amazonaws.com/edX/2021/store_google_253x78.jpg',
    'apple': 'http://email-media.s3.amazonaws.com/edX/2021/store_apple_229x78.jpg',
}


# The display dictionary defines the title
# and icon class for each social media link.
SOCIAL_MEDIA_FOOTER_DISPLAY = {
    "facebook": {
        # Translators: This is the website name of www.facebook.com.  Please
        # translate this the way that Facebook advertises in your language.
        "title": _("Facebook"),
        "icon": "fa-facebook-square",
        "action": _("Like {platform_name} on Facebook")
    },
    "twitter": {
        # Translators: This is the website name of www.twitter.com.  Please
        # translate this the way that Twitter advertises in your language.
        "title": _("Twitter"),
        "icon": "fa-twitter-square",
        "action": _("Follow {platform_name} on Twitter")
    },
    "linkedin": {
        # Translators: This is the website name of www.linkedin.com.  Please
        # translate this the way that LinkedIn advertises in your language.
        "title": _("LinkedIn"),
        "icon": "fa-linkedin-square",
        "action": _("Follow {platform_name} on LinkedIn")
    },
    "instagram": {
        # Translators: This is the website name of www.instagram.com.  Please
        # translate this the way that Instagram advertises in your language.
        "title": _("Instagram"),
        "icon": "fa-instagram",
        "action": _("Follow {platform_name} on Instagram")
    },
    "tumblr": {
        # Translators: This is the website name of www.tumblr.com.  Please
        # translate this the way that Tumblr advertises in your language.
        "title": _("Tumblr"),
        "icon": "fa-tumblr"
    },
    "meetup": {
        # Translators: This is the website name of www.meetup.com.  Please
        # translate this the way that MeetUp advertises in your language.
        "title": _("Meetup"),
        "icon": "fa-calendar"
    },
    "reddit": {
        # Translators: This is the website name of www.reddit.com.  Please
        # translate this the way that Reddit advertises in your language.
        "title": _("Reddit"),
        "icon": "fa-reddit-square",
        "action": _("Subscribe to the {platform_name} subreddit"),
    },
    "vk": {
        # Translators: This is the website name of https://vk.com.  Please
        # translate this the way that VK advertises in your language.
        "title": _("VK"),
        "icon": "fa-vk"
    },
    "weibo": {
        # Translators: This is the website name of http://www.weibo.com.  Please
        # translate this the way that Weibo advertises in your language.
        "title": _("Weibo"),
        "icon": "fa-weibo"
    },
    "youtube": {
        # Translators: This is the website name of www.youtube.com.  Please
        # translate this the way that YouTube advertises in your language.
        "title": _("Youtube"),
        "icon": "fa-youtube-square",
        "action": _("Subscribe to the {platform_name} YouTube channel")
    }
}

#################SOCAIL AUTH OAUTH######################
SOCIAL_AUTH_OAUTH_SECRETS = {}

################# Student Verification Expiry Email #################
VERIFICATION_EXPIRY_EMAIL = {
    "RESEND_DAYS": 15,
    "DAYS_RANGE": 1,
    "DEFAULT_EMAILS": 2,
}

############# Cross-domain requests #################

# Set CORS_ALLOW_HEADERS regardless of whether we've enabled ENABLE_CORS_HEADERS
# because that decision might happen in a later config file. (The headers to
# allow is an application logic, and not site policy.)
CORS_ALLOW_HEADERS = corsheaders_default_headers + (
    'cache-control',
    'expires',
    'pragma',
    'use-jwt-cookie',
)

# Default cache expiration for the cross-domain proxy HTML page.
# This is a static page that can be iframed into an external page
# to simulate cross-domain requests.
XDOMAIN_PROXY_CACHE_TIMEOUT = 60 * 15

###################### Registration ##################################

REGISTRATION_FIELD_ORDER = [
    "name",
    "first_name",
    "last_name",
    "username",
    "email",
    "confirm_email",
    "password",
    "city",
    "state",
    "country",
    "year_of_birth",
    "level_of_education",
    "gender",
    "specialty",
    "profession",
    "company",
    "title",
    "mailing_address",
    "goals",
    "honor_code",
    "terms_of_service",
]

REGISTRATION_CODE_LENGTH = 8

########################## CERTIFICATE NAME ########################
CERT_NAME_SHORT = "Certificate"
CERT_NAME_LONG = "Certificate of Achievement"

###################### Grade Downloads ######################
# These keys are used for all of our asynchronous downloadable files, including
# the ones that contain information other than grades.
GRADES_DOWNLOAD_ROUTING_KEY = Derived(lambda settings: settings.HIGH_MEM_QUEUE)

############################ ORA 2 ############################################
ORA_WORKFLOW_UPDATE_ROUTING_KEY = "edx.lms.core.ora_workflow_update"

# By default, don't use a file prefix
ORA2_FILE_PREFIX = None

# .. setting_name: ORA_PEER_LEASE_EXPIRATION_HOURS
# .. setting_default: 8
# .. setting_description: Amount of time before a lease on a peer submission expires
ORA_PEER_LEASE_EXPIRATION_HOURS = 8

# .. setting_name: ORA_STAFF_LEASE_EXPIRATION_HOURS
# .. setting_default: 8
# .. setting_description: Amount of time before a lease on a staff submission expires
ORA_STAFF_LEASE_EXPIRATION_HOURS = 8

##### LMS DEADLINE DISPLAY TIME_ZONE #######
TIME_ZONE_DISPLAYED_FOR_DEADLINES = 'UTC'

### Analytics API
ANALYTICS_API_KEY = ""
ANALYTICS_API_URL = "http://localhost:18100"

# REGISTRATION CODES DISPLAY INFORMATION SUBTITUTIONS IN THE INVOICE ATTACHMENT
INVOICE_CORP_ADDRESS = "Please place your corporate address\nin this configuration"
INVOICE_PAYMENT_INSTRUCTIONS = "This is where you can\nput directions on how people\nbuying registration codes"

# Country code overrides
# Used by django-countries
COUNTRIES_OVERRIDE = {
    # Taiwan is specifically not translated to avoid it being translated as "Taiwan (Province of China)"
    "TW": "Taiwan",
    'XK': _('Kosovo'),
}

# Enrollment API Cache Timeout
ENROLLMENT_COURSE_DETAILS_CACHE_TIMEOUT = 60

# These tabs are currently disabled
NOTES_DISABLED_TABS = ['course_structure', 'tags']

# Configuration used for generating PDF Receipts/Invoices
PDF_RECEIPT_TAX_ID = '00-0000000'
PDF_RECEIPT_FOOTER_TEXT = 'Enter your receipt footer text here.'
PDF_RECEIPT_DISCLAIMER_TEXT = 'ENTER YOUR RECEIPT DISCLAIMER TEXT HERE.'
PDF_RECEIPT_BILLING_ADDRESS = 'Enter your receipt billing address here.'
PDF_RECEIPT_TERMS_AND_CONDITIONS = 'Enter your receipt terms and conditions here.'
PDF_RECEIPT_TAX_ID_LABEL = 'fake Tax ID'
PDF_RECEIPT_LOGO_PATH = PROJECT_ROOT + '/static/images/openedx-logo-tag.png'
# Height of the Logo in mm
PDF_RECEIPT_LOGO_HEIGHT_MM = 12
PDF_RECEIPT_COBRAND_LOGO_PATH = PROJECT_ROOT + '/static/images/logo.png'
# Height of the Co-brand Logo in mm
PDF_RECEIPT_COBRAND_LOGO_HEIGHT_MM = 12

# Use LMS specific search initializer
SEARCH_INITIALIZER = "lms.lib.courseware_search.lms_search_initializer.LmsSearchInitializer"
# Use the LMS specific result processor
SEARCH_RESULT_PROCESSOR = "lms.lib.courseware_search.lms_result_processor.LmsSearchResultProcessor"
# Use the LMS specific filter generator
SEARCH_FILTER_GENERATOR = "lms.lib.courseware_search.lms_filter_generator.LmsSearchFilterGenerator"
# Override to skip enrollment start date filtering in course search
SEARCH_SKIP_ENROLLMENT_START_DATE_FILTERING = False
# .. toggle_name: SEARCH_SKIP_INVITATION_ONLY_FILTERING
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: If enabled, invitation-only courses will appear in search results.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2021-08-27
SEARCH_SKIP_INVITATION_ONLY_FILTERING = True
# .. toggle_name: SEARCH_SKIP_SHOW_IN_CATALOG_FILTERING
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: If enabled, courses with a catalog_visibility set to "none" will still
#    appear in search results.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2021-08-27
SEARCH_SKIP_SHOW_IN_CATALOG_FILTERING = True

# The configuration visibility of account fields.
ACCOUNT_VISIBILITY_CONFIGURATION = {
    # Default visibility level for accounts without a specified value
    # The value is one of: 'all_users', 'private'
    "default_visibility": "all_users",

    # The list of account fields that are always public
    "public_fields": [
        'account_privacy',
        'profile_image',
        'username',
    ],
}

# .. setting_name: PROFILE_INFORMATION_REPORT_PRIVATE_FIELDS
# .. setting_default: ["year_of_birth"]
# .. setting_description: List of private fields that will be hidden from the profile information report.
# .. setting_use_cases: open_edx
# .. setting_creation_date: 2025-07-07
# .. setting_tickets: https://github.com/openedx/edx-platform/pull/36688
PROFILE_INFORMATION_REPORT_PRIVATE_FIELDS = ["year_of_birth"]

# The list of all fields that are shared with other users using the bulk 'all_users' privacy setting
ACCOUNT_VISIBILITY_CONFIGURATION["bulk_shareable_fields"] = (
    ACCOUNT_VISIBILITY_CONFIGURATION["public_fields"] + [
        'bio',
        'course_certificates',
        'country',
        'date_joined',
        'language_proficiencies',
        "level_of_education",
        'social_links',
        'time_zone',
    ]
)

# The list of all fields that can be shared selectively with other users using the 'custom' privacy setting
ACCOUNT_VISIBILITY_CONFIGURATION["custom_shareable_fields"] = (
    ACCOUNT_VISIBILITY_CONFIGURATION["bulk_shareable_fields"] + [
        "name",
    ]
)

# The list of account fields that are visible only to staff and users viewing their own profiles
ACCOUNT_VISIBILITY_CONFIGURATION["admin_fields"] = (
    ACCOUNT_VISIBILITY_CONFIGURATION["custom_shareable_fields"] + [
        "email",
        "id",
        "verified_name",
        "extended_profile",
        "gender",
        "state",
        "goals",
        "is_active",
        "last_login",
        "mailing_address",
        "requires_parental_consent",
        "secondary_email",
        "secondary_email_enabled",
        "year_of_birth",
        "phone_number",
        "activation_key",
        "pending_name_change",
    ]
)

# The current list of social platforms to be shown to the user.
#
# url_stub represents the host URL, it must end with a forward
# slash and represent the profile at https://www.[url_stub][username]
#
# The example will be used as a placeholder in the social link
# input field as well as in some messaging describing an example of a
# valid link.
SOCIAL_PLATFORMS = {
    'facebook': {
        'display_name': 'Facebook',
        'url_stub': 'facebook.com/',
        'example': 'https://www.facebook.com/username'
    },
    'x': {
        'display_name': 'X',
        'url_stub': 'x.com/',
        'example': 'https://www.x.com/username'
    },
    'linkedin': {
        'display_name': 'LinkedIn',
        'url_stub': 'linkedin.com/in/',
        'example': 'www.linkedin.com/in/username'
    }
}

# Enable First Purchase Discount offer override
FIRST_PURCHASE_DISCOUNT_OVERRIDE_CODE = ''
FIRST_PURCHASE_DISCOUNT_OVERRIDE_PERCENTAGE = 15

# E-Commerce API Configuration
ECOMMERCE_API_TIMEOUT = 5
ECOMMERCE_ORDERS_API_CACHE_TIMEOUT = 3600
ECOMMERCE_SERVICE_WORKER_USERNAME = 'ecommerce_worker'

############## Settings for JWT token handling ##############
TOKEN_SIGNING = {
    'JWT_ISSUER': 'http://127.0.0.1:8740',
    'JWT_SIGNING_ALGORITHM': 'RS512',
    'JWT_SUPPORTED_VERSION': '1.2.0',
    'JWT_PRIVATE_SIGNING_JWK': None,
    'JWT_PUBLIC_SIGNING_JWK_SET': None,
}

# NOTE: In order to create both JWT_PRIVATE_SIGNING_JWK and JWT_PUBLIC_SIGNING_JWK_SET,
# in an  lms shell  run the following  command:
# > python manage.py lms generate_jwt_signing_key
# This will output asymmetric JWTs to use here. Read more on this on:
# https://github.com/openedx/edx-platform/blob/master/openedx/core/djangoapps/oauth_dispatch/docs/decisions/0008-use-asymmetric-jwts.rst

COMMENTS_SERVICE_URL = ''
COMMENTS_SERVICE_KEY = ''

# Reverification checkpoint name pattern
CHECKPOINT_PATTERN = r'(?P<checkpoint_name>[^/]+)'

# For the fields override feature
# If using settings.INDIVIDUAL_DUE_DATES, you should add
# 'lms.djangoapps.courseware.student_field_overrides.IndividualStudentOverrideProvider' to
# this setting.
FIELD_OVERRIDE_PROVIDERS = ()

# Modulestore-level field override providers. These field override providers don't
# require student context.
MODULESTORE_FIELD_OVERRIDE_PROVIDERS = ('openedx.features.content_type_gating.'
                                        'field_override.ContentTypeGatingFieldOverride',)

# Sets the maximum number of courses listed on the homepage
# If set to None, all courses will be listed on the homepage
HOMEPAGE_COURSE_MAX = None

# .. setting_name: COURSE_MEMBER_API_ENROLLMENT_LIMIT
# .. setting_implementation: DjangoSetting
# .. setting_default: 1000
# .. setting_description: This limits the response size of the `get_course_members` API, throwing an exception
#    if the number of Enrolled users is greater than this number. This is needed to limit the dataset size
#    since the API does most of the calculation in Python to avoid expensive database queries.
# .. setting_use_cases: open_edx
# .. setting_creation_date: 2021-05-18
# .. setting_tickets: https://openedx.atlassian.net/browse/TNL-7330
COURSE_MEMBER_API_ENROLLMENT_LIMIT = 1000

################################ Settings for Credit Courses ################################

# The Help link to the FAQ page about the credit
CREDIT_HELP_LINK_URL = ""

# Default domain for the e-mail address associated with users who are created
# via the LTI Provider feature. Note that the generated e-mail addresses are
# not expected to be active; this setting simply allows administrators to
# route any messages intended for LTI users to a common domain.
LTI_USER_EMAIL_DOMAIN = 'lti.example.com'

# An aggregate score is one derived from multiple problems (such as the
# cumulative score for a vertical element containing many problems). Sending
# aggregate scores immediately introduces two issues: one is a race condition
# between the view method and the Celery task where the updated score may not
# yet be visible to the database if the view has not yet returned (and committed
# its transaction). The other is that the student is likely to receive a stream
# of notifications as the score is updated with every problem. Waiting a
# reasonable period of time allows the view transaction to end, and allows us to
# collapse multiple score updates into a single message.
# The time value is in seconds.
LTI_AGGREGATE_SCORE_PASSBACK_DELAY = 15 * 60


# .. setting_name: LTI_CUSTOM_PARAMS
# .. setting_default: []
# .. setting_description: This expands the list of optional LTI parameters that the
#    platform accepts. These parameters are not used by the platform, but can then
#    be used by other plugins.
# .. setting_creation_date: 2025-08-22
# .. setting_tickets:
LTI_CUSTOM_PARAMS = []

# Credit notifications settings
NOTIFICATION_EMAIL_CSS = "templates/credit_notifications/credit_notification.css"
NOTIFICATION_EMAIL_EDX_LOGO = "templates/credit_notifications/edx-logo-header.png"


################################ Settings for JWTs ################################

# Allows JWT authentication to find the LMS user id for verification
EDX_DRF_EXTENSIONS['VERIFY_LMS_USER_ID_PROPERTY_NAME'] = 'id'

################################ Settings for rss_proxy ################################

RSS_PROXY_CACHE_TIMEOUT = 3600  # The length of time we cache RSS retrieved from remote URLs in seconds

#### Custom Courses for EDX (CCX) configuration

# .. setting_name: CCX_MAX_STUDENTS_ALLOWED
# .. setting_default: 200
# .. setting_description: Maximum number of students allowed in a CCX (Custom Courses for edX), This is an arbitrary
#   hard limit, chosen so that a CCX does not compete with public MOOCs.
CCX_MAX_STUDENTS_ALLOWED = 200

# Financial assistance settings

# Maximum and minimum length of answers, in characters, for the
# financial assistance form
FINANCIAL_ASSISTANCE_MIN_LENGTH = 1250
FINANCIAL_ASSISTANCE_MAX_LENGTH = 2500

#### Registration form extension. ####
# Only used if combined login/registration is enabled.
# This can be used to add fields to the registration page.
# It must be a path to a valid form, in dot-separated syntax.
# IE: custom_form_app.forms.RegistrationExtensionForm
# Note: If you want to use a model to store the results of the form, you will
# need to add the model's app to the ADDL_INSTALLED_APPS array in your
# lms.yml file.

REGISTRATION_EXTENSION_FORM = None

# Identifier included in the User Agent from Open edX mobile apps.
MOBILE_APP_USER_AGENT_REGEXES = [
    r'edX/org.edx.mobile',
]

# set course limit for mobile search
MOBILE_SEARCH_COURSE_LIMIT = 100

# cache timeout in seconds for Mobile App Version Upgrade
APP_UPGRADE_CACHE_TIMEOUT = 3600

# Offset for courseware.StudentModuleHistoryExtended which is used to
# calculate the starting primary key for the underlying table.  This gap
# should be large enough that you do not generate more than N courseware.StudentModuleHistory
# records before you have deployed the app to write to coursewarehistoryextended.StudentModuleHistoryExtended
# if you want to avoid an overlap in ids while searching for history across the two tables.
STUDENTMODULEHISTORYEXTENDED_OFFSET = 10000

################################ Settings for Credentials Service ################################

CREDENTIALS_GENERATION_ROUTING_KEY = Derived(lambda settings: settings.DEFAULT_PRIORITY_QUEUE)
CREDENTIALS_COURSE_COMPLETION_STATE = 'awarded'

# Queue to use for award program certificates
PROGRAM_CERTIFICATES_ROUTING_KEY = Derived(lambda settings: settings.DEFAULT_PRIORITY_QUEUE)

############## OPEN EDX ENTERPRISE SERVICE CONFIGURATION ######################
# The Open edX Enterprise service is currently hosted via the LMS container/process.
# However, for all intents and purposes this service is treated as a standalone IDA.
# These configuration settings are specific to the Enterprise service and you should
# not find references to them within the edx-platform project.
#
# Only used if settings.ENABLE_ENTERPRISE_INTEGRATION == True.

ENTERPRISE_PUBLIC_ENROLLMENT_API_URL = Derived(
    lambda settings: (settings.LMS_ROOT_URL or '') + settings.LMS_ENROLLMENT_API_PATH
)
ENTERPRISE_COURSE_ENROLLMENT_AUDIT_MODES = ['audit', 'honor']
ENTERPRISE_SUPPORT_URL = ''
ENTERPRISE_CUSTOMER_SUCCESS_EMAIL = "customersuccess@edx.org"
ENTERPRISE_INTEGRATIONS_EMAIL = "enterprise-integrations@edx.org"

############## ENTERPRISE SERVICE API CLIENT CONFIGURATION ######################
# The LMS communicates with the Enterprise service via the requests.Session() client
# These default settings are utilized by the LMS when interacting with the service,
# and are overridden by the configuration parameter accessors defined in production.py

DEFAULT_ENTERPRISE_API_URL = Derived(
    lambda settings: (
        None if settings.LMS_INTERNAL_ROOT_URL is None
        else settings.LMS_INTERNAL_ROOT_URL + '/enterprise/api/v1/'
    )
)
ENTERPRISE_API_URL = DEFAULT_ENTERPRISE_API_URL
DEFAULT_ENTERPRISE_CONSENT_API_URL = Derived(
    lambda settings: (
        None if settings.LMS_INTERNAL_ROOT_URL is None
        else settings.LMS_INTERNAL_ROOT_URL + '/consent/api/v1/'
    )
)
ENTERPRISE_CONSENT_API_URL = DEFAULT_ENTERPRISE_CONSENT_API_URL
ENTERPRISE_CUSTOMER_LOGO_IMAGE_SIZE = 512   # Enterprise logo image size limit in KB's
# Defines the usernames of service users who should be throttled
# at a higher rate than normal users when making requests of enterprise endpoints.
ENTERPRISE_ALL_SERVICE_USERNAMES = [
    'ecommerce_worker',
    'enterprise_worker',
    'license_manager_worker',
    'enterprise_catalog_worker',
    'enterprise_channel_worker',
    'enterprise_access_worker',
    'enterprise_subsidy_worker',
]


############## ENTERPRISE SERVICE LMS CONFIGURATION ##################################
# The LMS has some features embedded that are related to the Enterprise service, but
# which are not provided by the Enterprise service. These settings provide base values
# for those features.

ENTERPRISE_PLATFORM_WELCOME_TEMPLATE = _('Welcome to {platform_name}.')
ENTERPRISE_SPECIFIC_BRANDED_WELCOME_TEMPLATE = _(
    'You have left the {start_bold}{enterprise_name}{end_bold} website and are now on the {platform_name} site. '
    '{enterprise_name} has partnered with {platform_name} to offer you high-quality, always available learning '
    'programs to help you advance your knowledge and career. '
    '{line_break}Please note that {platform_name} has a different {privacy_policy_link_start}Privacy Policy'
    '{privacy_policy_link_end} from {enterprise_name}.'
)
ENTERPRISE_PROXY_LOGIN_WELCOME_TEMPLATE = _(
    '{start_bold}{enterprise_name}{end_bold} has partnered with {start_bold}{platform_name}{end_bold} '
    'to offer you high-quality learning opportunities from the world\'s best institutions and universities.'
)
ENTERPRISE_TAGLINE = ''
ENTERPRISE_EXCLUDED_REGISTRATION_FIELDS = {
    'age',
    'level_of_education',
    'gender',
    'goals',
    'year_of_birth',
    'mailing_address',
}
ENTERPRISE_READONLY_ACCOUNT_FIELDS = [
    'username',
    'name',
    'email',
    'country',
]
ENTERPRISE_CUSTOMER_COOKIE_NAME = 'enterprise_customer_uuid'

SYSTEM_TO_FEATURE_ROLE_MAPPING = {
    ENTERPRISE_LEARNER_ROLE: [
        DEFAULT_ENTERPRISE_ENROLLMENT_INTENTIONS_ROLE,
    ],
    ENTERPRISE_ADMIN_ROLE: [
        ENTERPRISE_DASHBOARD_ADMIN_ROLE,
        ENTERPRISE_CATALOG_ADMIN_ROLE,
        ENTERPRISE_ENROLLMENT_API_ADMIN_ROLE,
        ENTERPRISE_REPORTING_CONFIG_ADMIN_ROLE,
        DEFAULT_ENTERPRISE_ENROLLMENT_INTENTIONS_ROLE,
    ],
    ENTERPRISE_OPERATOR_ROLE: [
        ENTERPRISE_DASHBOARD_ADMIN_ROLE,
        ENTERPRISE_CATALOG_ADMIN_ROLE,
        ENTERPRISE_ENROLLMENT_API_ADMIN_ROLE,
        ENTERPRISE_REPORTING_CONFIG_ADMIN_ROLE,
        ENTERPRISE_FULFILLMENT_OPERATOR_ROLE,
        ENTERPRISE_SSO_ORCHESTRATOR_OPERATOR_ROLE,
        DEFAULT_ENTERPRISE_ENROLLMENT_INTENTIONS_ROLE,
    ],
    SYSTEM_ENTERPRISE_PROVISIONING_ADMIN_ROLE: [
        PROVISIONING_ENTERPRISE_CUSTOMER_ADMIN_ROLE,
        PROVISIONING_PENDING_ENTERPRISE_CUSTOMER_ADMIN_ROLE,
    ],
}

DATA_CONSENT_SHARE_CACHE_TIMEOUT = 8 * 60 * 60  # 8 hours

############## Settings for the Discovery App ######################

COURSES_API_CACHE_TIMEOUT = 3600  # Value is in seconds

############## Settings for Completion API #########################

COMPLETION_BY_VIEWING_DELAY_MS = 5000

############### Settings for Django Rate limit #####################

# .. toggle_name: RATELIMIT_ENABLE
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: When enabled, RATELIMIT_RATE is applied.
#    When disabled, RATELIMIT_RATE is not applied.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2018-01-08
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/16951
RATELIMIT_ENABLE = True

# .. setting_name: RATELIMIT_RATE
# .. setting_default: 120/m
# .. setting_description: Due to some reports about attack on /oauth2/access_token/ which took LMS down,
#    this setting was introduced to rate-limit all endpoints of AccessTokenView up to
#    120 requests per IP Address in a minute by default.
# .. setting_warning: RATELIMIT_ENABLE flag must also be enabled/set to True to use this RATELIMIT_RATE setting.
# .. setting_use_cases: open_edx
# .. setting_creation_date: 2018-01-08
# .. setting_tickets: https://github.com/openedx/edx-platform/pull/16951
RATELIMIT_RATE = '120/m'

# Keeping this for back compatibility with learner dashboard api
GENERAL_RECOMMENDATION = {}

############## Settings for Microfrontends  #########################
# If running a Gradebook container locally,
# modify lms/envs/private.py to give it a non-null value
WRITABLE_GRADEBOOK_URL = None
# .. setting_name: PROFILE_MICROFRONTEND_URL
# .. setting_default: None
# .. setting_description: Base URL of the micro-frontend-based profile page.
# .. setting_warning: Also set site's ENABLE_PROFILE_MICROFRONTEND and
#     learner_profile.redirect_to_microfrontend waffle flag
PROFILE_MICROFRONTEND_URL = None
ORDER_HISTORY_MICROFRONTEND_URL = None

AUTHN_MICROFRONTEND_URL = None
AUTHN_MICROFRONTEND_DOMAIN = None
PROGRAM_CONSOLE_MICROFRONTEND_URL = None
# .. setting_name: ORA_GRADING_MICROFRONTEND_URL
# .. setting_default: None
# .. setting_description: Base URL of the micro-frontend-based openassessment grading page.
#     This is will be show in the open response tab list data.
# .. setting_warning: Also set site's openresponseassessment.enhanced_staff_grader
#     waffle flag.
ORA_GRADING_MICROFRONTEND_URL = None
# .. setting_name: ORA_MICROFRONTEND_URL
# .. setting_default: None
# .. setting_description: Base URL for modern openassessment app.
#     This is will be show in the open response tab list data.
# .. setting_warning: Also set site's openresponseassessment.mfe_views
#     waffle flag.
ORA_MICROFRONTEND_URL = None
# .. setting_name: EXAMS_DASHBOARD_MICROFRONTEND_URL
# .. setting_default: None
# .. setting_description: Base URL of the exams dashboard micro-frontend for instructors.
EXAMS_DASHBOARD_MICROFRONTEND_URL = None
# .. setting_name: CATALOG_MICROFRONTEND_URL
# .. setting_default: None
# .. setting_description: Base URL of the micro-frontend-based course catalog page.
CATALOG_MICROFRONTEND_URL = None
# .. setting_name: INSTRUCTOR_MICROFRONTEND_URL
# .. setting_default: None
# .. setting_description: Base URL of the micro-frontend-based instructor app.
INSTRUCTOR_MICROFRONTEND_URL = None
# .. setting_name: DISCUSSION_SPAM_URLS
# .. setting_default: []
# .. setting_description: Urls to filter from discussion content to avoid spam
DISCUSSION_SPAM_URLS = []

# .. setting_name: CONTENT_FOR_SPAM_POSTS
# .. setting_default: ""
# .. setting_description: Content to replace spam posts with
CONTENT_FOR_SPAM_POSTS = ""

# .. toggle_name: ENABLE_AUTHN_RESET_PASSWORD_HIBP_POLICY
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When enabled, this toggle prevents the use of known-vulnerable passwords in
#   the password reset flow.
#   See ENABLE_AUTHN_LOGIN_BLOCK_HIBP_POLICY for more details.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2021-12-03
ENABLE_AUTHN_RESET_PASSWORD_HIBP_POLICY = False

# .. toggle_name: ENABLE_AUTHN_REGISTER_HIBP_POLICY
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When enabled, this toggle prevents the use of known-vulnerable passwords in
#   the registration flow if their frequency exceeds a threshold.
#   See ENABLE_AUTHN_LOGIN_BLOCK_HIBP_POLICY for more details.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2022-03-25
ENABLE_AUTHN_REGISTER_HIBP_POLICY = False
# .. setting_name: HIBP_REGISTRATION_PASSWORD_FREQUENCY_THRESHOLD
# .. setting_default: 3.0
# .. setting_description: Log10 threshold in effect for ENABLE_AUTHN_REGISTER_HIBP_POLICY.
#   See ENABLE_AUTHN_LOGIN_BLOCK_HIBP_POLICY for more details.
HIBP_REGISTRATION_PASSWORD_FREQUENCY_THRESHOLD = 3.0

# .. toggle_name: ENABLE_AUTHN_LOGIN_NUDGE_HIBP_POLICY
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When enabled, the login flow detects vulnerable passwords
#   and prompts users to change their password if their frequency exceeds a threshold.
#   See ENABLE_AUTHN_LOGIN_BLOCK_HIBP_POLICY for more details.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2022-03-29
ENABLE_AUTHN_LOGIN_NUDGE_HIBP_POLICY = False
# .. setting_name: HIBP_LOGIN_NUDGE_PASSWORD_FREQUENCY_THRESHOLD
# .. setting_default: 3.0
# .. setting_description: Log10 threshold in effect for ENABLE_AUTHN_LOGIN_NUDGE_HIBP_POLICY.
#   See ENABLE_AUTHN_LOGIN_BLOCK_HIBP_POLICY for more details.
HIBP_LOGIN_NUDGE_PASSWORD_FREQUENCY_THRESHOLD = 3.0

# .. toggle_name: ENABLE_AUTHN_LOGIN_BLOCK_HIBP_POLICY
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When enabled, this toggle prevents the use of known-vulnerable passwords in
#   the login flow if their frequency exceeds a threshold. Passwords are assessed by calling the
#   Pwned Passwords service using a k-anonymity method that does not expose the password. The
#   service tells us whether the password has been seen in any data breaches, and if so, how
#   often. This count is converted to a "frequency" by taking the logarithm base 10. The login flow
#   can reject all vulnerable passwords, or only passwords with a frequency above a configured
#   threshold. In existing deployments, the threshold should be set high and tightened
#   gradually in order to avoid large spikes in password resets and support requests. For example,
#   setting ``HIBP_LOGIN_BLOCK_PASSWORD_FREQUENCY_THRESHOLD`` to 5 would reject logins when the
#   password has been seen 100,000 or more times in the Pwned Passwords dataset. The goal should be
#   to gradually reduce this to 0, meaning even a single occurrence will cause a rejection. (The
#   threshold can take any real-number value.)
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2022-03-29
ENABLE_AUTHN_LOGIN_BLOCK_HIBP_POLICY = False
# .. setting_name: HIBP_LOGIN_BLOCK_PASSWORD_FREQUENCY_THRESHOLD
# .. setting_default: 5.0
# .. setting_description: Log10 threshold in effect for ENABLE_AUTHN_LOGIN_BLOCK_HIBP_POLICY.
#   See ENABLE_AUTHN_LOGIN_BLOCK_HIBP_POLICY for more details.
HIBP_LOGIN_BLOCK_PASSWORD_FREQUENCY_THRESHOLD = 5.0

############## Settings for EmailChangeMiddleware ###############

# .. toggle_name: ENFORCE_SESSION_EMAIL_MATCH
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When enabled, this setting invalidates sessions in other browsers
#       upon email change, while preserving the session validity in the browser where the
#       email change occurs. This toggle is just being used for rollout.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2023-12-07
# .. toggle_target_removal_date: 2024-04-01
# .. toggle_tickets: https://2u-internal.atlassian.net/browse/VAN-1797
ENFORCE_SESSION_EMAIL_MATCH = False

############### Settings for the ace_common plugin #################
# Note that all settings are actually defined by the plugin
# pylint: disable=wrong-import-position
from openedx.core.djangoapps.ace_common.settings import common as ace_common_settings
ACE_ROUTING_KEY = ace_common_settings.ACE_ROUTING_KEY

############### Settings for facebook ##############################
FACEBOOK_APP_ID = None
FACEBOOK_APP_SECRET = None
FACEBOOK_API_VERSION = None

############### Settings for user-state-client ##################
# Maximum number of rows to fetch in XBlockUserStateClient calls. Adjust for performance
USER_STATE_BATCH_SIZE = 5000

############## Plugin Django Apps #########################

from edx_django_utils.plugins import get_plugin_apps, add_plugins  # pylint: disable=wrong-import-position,wrong-import-order
from openedx.core.djangoapps.plugins.constants import ProjectType, SettingsType  # pylint: disable=wrong-import-position
INSTALLED_APPS.extend(get_plugin_apps(ProjectType.LMS))
add_plugins(__name__, ProjectType.LMS, SettingsType.COMMON)

PROCTORED_EXAM_VIEWABLE_PAST_DUE = False

######################### rate limit for yt_video_metadata api ##############

RATE_LIMIT_FOR_VIDEO_METADATA_API = '10/minute'

########################## MAILCHIMP SETTINGS #################################
MAILCHIMP_NEW_USER_LIST_ID = ""

GITHUB_REPO_ROOT = '/edx/var/edxapp/data'

##################### SUPPORT URL ############################
SUPPORT_HOW_TO_UNENROLL_LINK = ''

######################## Setting for content libraries ########################
MAX_BLOCKS_PER_CONTENT_LIBRARY = 1000

######################## Setting for django-countries ########################
# django-countries provides an option to make the desired countries come up in
# selection forms, if left empty countries will come up in ascending order as before.
# This accepts a list of ISO3166-1 two letter country code, For example,
# COUNTRIES_FIRST = ['SA', 'BH', 'QA'] will display these countries on top of the list
# https://github.com/SmileyChris/django-countries#show-certain-countries-first
COUNTRIES_FIRST = []

################# Documentation links for course apps #################

COURSE_BULK_EMAIL_HELP_URL = "https://docs.openedx.org/en/latest/educators/references/communication/bulk_email.html"

################# Bulk Course Email Settings #################
# If set, recipients of bulk course email messages will be filtered based on the last_login date of their User account.
# The expected value is an Integer representing the cutoff point (in months) for inclusion to the message. Example:
# a value of `3` would include learners who have logged in within the past 3 months.
BULK_COURSE_EMAIL_LAST_LOGIN_ELIGIBILITY_PERIOD = None

################ Settings for the Discussion Service #########
# Provide a list of reason codes for moderators editing posts and
# comments, as a mapping from the internal reason code representation,
# to an internationalizable label to be shown to moderators in the form UI.
DISCUSSION_MODERATION_EDIT_REASON_CODES = {
    "grammar-spelling": _("Has grammar / spelling issues"),
    "needs-clarity": _("Content needs clarity"),
    "academic-integrity": _("Has academic integrity concern"),
    "inappropriate-language": _("Has inappropriate language"),
    "format-change": _("Formatting changes needed"),
    "post-type-change": _("Post type needs change"),
    "contains-pii": _("Contains personally identifiable information"),
    "violates-guidelines": _("Violates community guidelines"),
}
# Provide a list of reason codes for moderators to close posts, as a mapping
# from the internal reason code representation, to  an internationalizable label
#  to be shown to moderators in the form UI.
DISCUSSION_MODERATION_CLOSE_REASON_CODES = {
    "academic-integrity": _("Post violates honour code or academic integrity"),
    "read-only": _("Post should be read-only"),
    "duplicate": _("Post is a duplicate"),
    "off-topic": _("Post is off-topic"),
}

################# Settings for edx-financial-assistance #################
IS_ELIGIBLE_FOR_FINANCIAL_ASSISTANCE_URL = '/core/api/course_eligibility/'
FINANCIAL_ASSISTANCE_APPLICATION_STATUS_URL = "/core/api/financial_assistance_application/status/"
CREATE_FINANCIAL_ASSISTANCE_APPLICATION_URL = '/core/api/financial_assistance_applications'

# .. toggle_name: ENABLE_MFE_CONFIG_API
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Set to True to enable MFE Config API. This is disabled by
#   default.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2022-05-20
# .. toggle_target_removal_date: None
# .. toggle_warnings: None
# .. toggle_tickets: None
ENABLE_MFE_CONFIG_API = False

# .. setting_name: MFE_CONFIG
# .. setting_implementation: DjangoSetting
# .. setting_default: {}
# .. setting_description: Is a configuration that will be exposed by the MFE Config API to be consumed by the MFEs.
#   Contains configuration common to all MFEs. When a specific MFE's configuration is requested, these values
#   will be treated as a base and then overriden/supplemented by those in `MFE_CONFIG_OVERRIDES`.
#   Example: {
#     "BASE_URL": "https://name_of_mfe.example.com",
#     "LANGUAGE_PREFERENCE_COOKIE_NAME": "example-language-preference",
#     "CREDENTIALS_BASE_URL": "https://credentials.example.com",
#     "DISCOVERY_API_BASE_URL": "https://discovery.example.com",
#     "LMS_BASE_URL": "https://courses.example.com",
#     "LOGIN_URL": "https://courses.example.com/login",
#     "LOGOUT_URL": "https://courses.example.com/logout",
#     "STUDIO_BASE_URL": "https://studio.example.com",
#     "LOGO_URL": "https://courses.example.com/logo.png"
#   }
# .. setting_use_cases: open_edx
# .. setting_creation_date: 2022-08-05
MFE_CONFIG = {}

# .. setting_name: MFE_CONFIG_OVERRIDES
# .. setting_implementation: DjangoSetting
# .. setting_default: {}
# .. setting_description: Overrides or additions to `MFE_CONFIG` for when a specific MFE is requested
#   by the MFE Config API. Top-level keys are APP_IDs, a.k.a. the name of the MFE (for example,
#   for an MFE named "frontend-app-xyz", the top-level key would be "xyz").
#   Example: {
#     "gradebook": {
#        "BASE_URL": "https://gradebook.example.com",
#     },
#     "profile": {
#        "BASE_URL": "https://profile.example.com",
#        "ENABLE_LEARNER_RECORD_MFE": "true",
#     },
#   }
# .. setting_use_cases: open_edx
# .. setting_creation_date: 2022-08-05
MFE_CONFIG_OVERRIDES = {}

# .. setting_name: MFE_CONFIG_API_CACHE_TIMEOUT
# .. setting_default: 60*5
# .. setting_description: The MFE Config API response will be cached during the
#   specified time
MFE_CONFIG_API_CACHE_TIMEOUT = 60 * 5

######################## Settings for Outcome Surveys plugin ########################
OUTCOME_SURVEYS_EVENTS_ENABLED = True

######################## Settings for cancel retirement in Support Tools ########################
COOL_OFF_DAYS = 14

############ Settings for externally hosted executive education courses ############
EXEC_ED_LANDING_PAGE = "https://www.getsmarter.com/account"

############ Internal Enterprise Settings ############
ENTERPRISE_VSF_UUID = "e815503343644ac7845bc82325c34460"
############ Internal Enterprise Settings ############

ENTERPRISE_MANUAL_REPORTING_CUSTOMER_UUIDS = []

AVAILABLE_DISCUSSION_TOURS = []

############## SELF PACED EMAIL ##############
SELF_PACED_BANNER_URL = ""
SELF_PACED_CLOUD_URL = ""

############## GOAL REMINDER EMAIL ##############
GOAL_REMINDER_BANNER_URL = ""
GOAL_REMINDER_PROFILE_URL = ""

############## NUDGE EMAILS ###############
# .. setting_name: DISABLED_ORGS_FOR_PROGRAM_NUDGE
# .. setting_default: []
# .. setting_description: List of organization codes that should be disabled
# .. for program nudge emails.
# .. eg ['BTDx', 'MYTx']
DISABLED_ORGS_FOR_PROGRAM_NUDGE = []


#### Event bus producing ####

def _should_send_certificate_events(settings):
    return settings.SEND_LEARNING_CERTIFICATE_LIFECYCLE_EVENTS_TO_BUS

EVENT_BUS_PRODUCER_CONFIG.update({
    'org.openedx.learning.certificate.created.v1': {
        'learning-certificate-lifecycle':
            {'event_key_field': 'certificate.course.course_key', 'enabled': Derived(_should_send_certificate_events)},
    },
    'org.openedx.learning.certificate.revoked.v1': {
        'learning-certificate-lifecycle':
            {'event_key_field': 'certificate.course.course_key', 'enabled': Derived(_should_send_certificate_events)},
    },
    'org.openedx.learning.course.unenrollment.completed.v1': {
        'course-unenrollment-lifecycle':
            {'event_key_field': 'enrollment.course.course_key',
             # .. toggle_name: EVENT_BUS_PRODUCER_CONFIG['org.openedx.learning.course.unenrollment.completed.v1']
             #    ['course-unenrollment-lifecycle']['enabled']
             # .. toggle_implementation: DjangoSetting
             # .. toggle_default: False
             # .. toggle_description: Enables sending COURSE_UNENROLLMENT_COMPLETED events over the event bus.
             # .. toggle_use_cases: opt_in
             # .. toggle_creation_date: 2023-09-18
             # .. toggle_warning: The default may be changed in a later release. See
             #   https://github.com/openedx/openedx-events/issues/265
             # .. toggle_tickets: https://github.com/openedx/openedx-events/issues/210
             'enabled': False},
    },
    'org.openedx.learning.xblock.skill.verified.v1': {
        'learning-xblock-skill-verified':
            {'event_key_field': 'xblock_info.usage_key',
             # .. toggle_name: EVENT_BUS_PRODUCER_CONFIG['org.openedx.learning.xblock.skill.verified.v1']
             #    ['learning-xblock-skill-verified']['enabled']
             # .. toggle_implementation: DjangoSetting
             # .. toggle_default: False
             # .. toggle_description: Enables sending xblock_skill_verified events over the event bus.
             # .. toggle_use_cases: opt_in
             # .. toggle_creation_date: 2023-09-18
             # .. toggle_warning: The default may be changed in a later release. See
             #   https://github.com/openedx/openedx-events/issues/265
             # .. toggle_tickets: https://github.com/openedx/openedx-events/issues/210
             'enabled': False}
    },
    'org.openedx.learning.user.course_access_role.added.v1': {
        'learning-course-access-role-lifecycle':
            {'event_key_field': 'course_access_role_data.course_key', 'enabled': False},
    },
    'org.openedx.learning.user.course_access_role.removed.v1': {
        'learning-course-access-role-lifecycle':
            {'event_key_field': 'course_access_role_data.course_key', 'enabled': False},
    },
    'org.openedx.enterprise.learner_credit_course_enrollment.revoked.v1': {
        'learner-credit-course-enrollment-lifecycle':
            {'event_key_field': 'learner_credit_course_enrollment.uuid', 'enabled': False},
    },
    "org.openedx.learning.external_grader.score.submitted.v1": {
        "learning-external-grader-score-lifecycle": {
            "event_key_field": "score.submission_id",
            "enabled": False
        },
    },
})

#### Survey Report ####
# .. toggle_name: SURVEY_REPORT_ENABLE
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: Set to True to enable the feature to generate and send survey reports.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2024-01-30
SURVEY_REPORT_ENABLE = True
# .. setting_name: SURVEY_REPORT_ENDPOINT
# .. setting_default: Open edX organization endpoint
# .. setting_description: Endpoint where the report will be sent.
SURVEY_REPORT_ENDPOINT = 'https://hooks.zapier.com/hooks/catch/11595998/3ouwv7m/'
# .. toggle_name: ANONYMOUS_SURVEY_REPORT
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: If enable, the survey report will be send a UUID as ID instead of use lms site name.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2023-02-21
ANONYMOUS_SURVEY_REPORT = False
# .. setting_name: SURVEY_REPORT_CHECK_THRESHOLD
# .. setting_default: every 6 months
# .. setting_description: Survey report banner will appear if a survey report is not sent in the months defined.
SURVEY_REPORT_CHECK_THRESHOLD = 6
# .. setting_name: SURVEY_REPORT_EXTRA_DATA
# .. setting_default: empty dictionary
# .. setting_description: Dictionary with additional information that you want to share in the report.
SURVEY_REPORT_EXTRA_DATA = {}

LMS_COMM_DEFAULT_FROM_EMAIL = "no-reply@example.com"

# .. setting_name: RECAPTCHA_PRIVATE_KEY
# .. setting_default: empty string
# .. setting_description: Add recaptcha private key to use captcha feature in discussion app.
RECAPTCHA_PRIVATE_KEY = ""

# .. setting_name: RECAPTCHA_SITE_KEYS
# .. setting_default: empty dictionary
# .. setting_description: Add recaptcha site keys to use captcha feature in discussion app.
# .. setting_warning: This setting is used to configure the reCAPTCHA keys for web,
#     iOS, and Android platforms.
#     The keys are expected to be in the format:
#     {
#         'web': 'your-web-site-key',
#         'ios': 'your-ios-site-key',
#         'android': 'your-android-site-key',
#     }
RECAPTCHA_SITE_KEYS = {
    'web': None,
    'ios': None,
    'android': None,
}

# .. setting_name: RECAPTCHA_PROJECT_ID
# .. setting_default: None
# .. setting_description: Add recaptcha project id to use captcha feature in discussion app.
# .. setting_warning: This setting is used to configure the reCAPTCHA project ID for the discussion app.
#     The project ID is used to identify the reCAPTCHA project in the Google Cloud Console
#     and is required for the reCAPTCHA service to function correctly.
#     The project ID should be obtained from the Google Cloud Console when creating a reCAPTCHA
RECAPTCHA_PROJECT_ID = None

############################## Miscellaneous ###############################

# To limit the number of courses displayed on learner dashboard
DASHBOARD_COURSE_LIMIT = None

ENTITLEMENTS_EXPIRATION_ROUTING_KEY = Derived(lambda settings: settings.DEFAULT_PRIORITY_QUEUE)

# TODO: We believe these were part of the DEPR'd sysadmin dashboard, and can likely be removed.
SSL_AUTH_EMAIL_DOMAIN = "MIT.EDU"
SSL_AUTH_DN_FORMAT_STRING = (
    "/C=US/ST=Massachusetts/O=Massachusetts Institute of Technology/OU=Client CA v1/CN={0}/emailAddress={1}"
)
