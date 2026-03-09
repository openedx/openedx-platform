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

import importlib.util
import sys
import os

import django
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

from openedx.core.constants import COURSE_KEY_REGEX, COURSE_KEY_PATTERN, COURSE_ID_PATTERN
from openedx.core.djangoapps.theming.helpers_dirs import (
    get_themes_unchecked,
    get_theme_base_dirs_from_settings
)
from openedx.core.lib.derived import Derived
from openedx.core.release import doc_version
from lms.djangoapps.lms_xblock.mixin import LmsBlockMixin

################################### FEATURES ###################################
# .. setting_name: PLATFORM_NAME
# .. setting_default: Your Platform Name Here
# .. setting_description: The display name of the platform to be used in
#     templates/emails/etc.
PLATFORM_NAME = _('Your Platform Name Here')
PLATFORM_DESCRIPTION = _('Your Platform Description Here')
CC_MERCHANT_NAME = PLATFORM_NAME

PLATFORM_FACEBOOK_ACCOUNT = "http://www.facebook.com/YourPlatformFacebookAccount"
PLATFORM_TWITTER_ACCOUNT = "@YourPlatformTwitterAccount"

ENABLE_JASMINE = False

LMS_ROOT_URL = 'https://localhost:18000'
LMS_INTERNAL_ROOT_URL = LMS_ROOT_URL
LMS_ENROLLMENT_API_PATH = "/api/enrollment/v1/"

# List of logout URIs for each IDA that the learner should be logged out of when they logout of the LMS. Only applies to
# IDA for which the social auth flow uses DOT (Django OAuth Toolkit).
IDA_LOGOUT_URI_LIST = []

# Features
FEATURES = {
    # .. toggle_name: FEATURES['DISPLAY_DEBUG_INFO_TO_STAFF']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: Add a "Staff Debug" button to course blocks for debugging
    #   by course staff.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2015-09-04
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/2425
    'DISPLAY_DEBUG_INFO_TO_STAFF': True,

    # .. toggle_name: FEATURES['DISPLAY_HISTOGRAMS_TO_STAFF']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: This displays histograms in the Staff Debug Info panel to course staff.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2014-02-13
    # .. toggle_warning: Generating histograms requires scanning the courseware_studentmodule table on each view. This
    #   can make staff access to courseware very slow on large courses.
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/2425
    'DISPLAY_HISTOGRAMS_TO_STAFF': False,  # For large courses this slows down courseware access for staff.

    'REROUTE_ACTIVATION_EMAIL': False,  # nonempty string = address for all activation emails

    # .. toggle_name: FEATURES['DISABLE_START_DATES']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: When True, all courses will be active, regardless of start
    #   date.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2012-07-24
    # .. toggle_warning: This will cause ALL courses to be immediately visible.
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/17913
    ## DO NOT SET TO True IN THIS FILE
    ## Doing so will cause all courses to be released on production
    'DISABLE_START_DATES': False,

    # .. toggle_name: FEATURES['ENABLE_DISCUSSION_SERVICE']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: When True, it will enable the Discussion tab in courseware for all courses. Setting this
    #   to False will not contain inline discussion components and discussion tab in any courses.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2012-08-14
    # .. toggle_warning: If the discussion panel is present in the course and the value for this flag is False then,
    #   attempting to expand those components will cause errors. So, this should only be set to False with an LMS that
    #   is running courses that do not contain discussion components.
    #   For consistency in user-experience, keep the value in sync with the setting of the same name in the CMS.
    'ENABLE_DISCUSSION_SERVICE': True,

    # .. toggle_name: FEATURES['ENABLE_TEXTBOOK']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: Add PDF and HTML textbook tabs to the courseware.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2014-03-27
    # .. toggle_warning: For consistency in user-experience, keep the value in sync with the setting of the same name
    #   in the CMS.
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/3064
    'ENABLE_TEXTBOOK': True,

    # .. toggle_name: FEATURES['ENABLE_DISCUSSION_HOME_PANEL']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: Hides or displays a welcome panel under the Discussion tab, which includes a subscription
    #   on/off setting for discussion digest emails.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2013-07-30
    # .. toggle_warning: This should remain off in production until digest notifications are online.
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/520
    'ENABLE_DISCUSSION_HOME_PANEL': False,

    # .. toggle_name: FEATURES['ENABLE_DISCUSSION_EMAIL_DIGEST']
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
    'ENABLE_DISCUSSION_EMAIL_DIGEST': False,

    # .. toggle_name: FEATURES['ENABLE_UNICODE_USERNAME']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Set this to True to allow unicode characters in username. Enabling this will also
    #   automatically enable SOCIAL_AUTH_CLEAN_USERNAMES. When this is enabled, usernames will have to match the
    #   regular expression defined by USERNAME_REGEX_PARTIAL.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2017-06-27
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/14729
    'ENABLE_UNICODE_USERNAME': False,

    # .. toggle_name: FEATURES['ENABLE_DJANGO_ADMIN_SITE']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: Set to False if you want to disable Django's admin site.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2013-09-26
    # .. toggle_warning: It is not recommended to disable this feature as there are many settings available on
    #  Django's admin site and will be inaccessible to the superuser.
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/829
    'ENABLE_DJANGO_ADMIN_SITE': True,
    'ENABLE_LMS_MIGRATION': False,

    # .. toggle_name: FEATURES['ENABLE_MASQUERADE']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: None
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2013-04-13
    'ENABLE_MASQUERADE': True,

    # .. toggle_name: FEATURES['DISABLE_LOGIN_BUTTON']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Removes the display of the login button in the navigation bar.
    #   Change is only at the UI level. Used in systems where login is automatic, eg MIT SSL
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2013-12-03
    'DISABLE_LOGIN_BUTTON': False,

    # .. toggle_name: FEATURES['ENABLE_OAUTH2_PROVIDER']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Enable this feature to allow this Open edX platform to be an OAuth2 authentication
    #   provider. This is necessary to enable some other features, such as the REST API for the mobile application.
    # .. toggle_use_cases: temporary
    # .. toggle_creation_date: 2014-09-09
    # .. toggle_target_removal_date: None
    # .. toggle_warning: This temporary feature toggle does not have a target removal date.
    'ENABLE_OAUTH2_PROVIDER': False,

    # .. toggle_name: FEATURES['ENABLE_XBLOCK_VIEW_ENDPOINT']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Enable an API endpoint, named "xblock_view", to serve rendered XBlock views. This might be
    #   used by external applications. See for instance jquery-xblock (now unmaintained):
    #   https://github.com/openedx/jquery-xblock
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2014-03-14
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/2968
    'ENABLE_XBLOCK_VIEW_ENDPOINT': False,

    # Allows to configure the LMS to provide CORS headers to serve requests from other
    # domains
    'ENABLE_CORS_HEADERS': False,

    # Can be turned off if course lists need to be hidden. Effects views and templates.
    # .. toggle_name: FEATURES['COURSES_ARE_BROWSABLE']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: When this is set to True, all the courses will be listed on the /courses page and Explore
    #   Courses link will be visible. Set to False if courses list and Explore Courses link need to be hidden.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2013-09-28
    # .. toggle_warning: This Effects views and templates.
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/1073
    'COURSES_ARE_BROWSABLE': True,

    # Can be turned off to disable the help link in the navbar
    # .. toggle_name: FEATURES['ENABLE_HELP_LINK']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: When True, a help link is displayed on the main navbar. Set False to hide it.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2021-03-05
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/26106
    'ENABLE_HELP_LINK': True,

    # .. toggle_name: FEATURES['HIDE_DASHBOARD_COURSES_UNTIL_ACTIVATED']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: When set, it hides the Courses list on the Learner Dashboard page if the learner has not
    #   yet activated the account and not enrolled in any courses.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2018-05-18
    # .. toggle_tickets: https://openedx.atlassian.net/browse/OSPR-1814
    'HIDE_DASHBOARD_COURSES_UNTIL_ACTIVATED': False,

    # .. toggle_name: FEATURES['ENABLE_STUDENT_HISTORY_VIEW']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: This provides a UI to show a student's submission history in a problem by the Staff Debug
    #   tool. Set to False if you want to hide Submission History from the courseware page.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2013-02-15
    # .. toggle_tickets: https://github.com/openedx/edx-platform/commit/8f17e6ae9ed76fa75b3caf867b65ccb632cb6870
    'ENABLE_STUDENT_HISTORY_VIEW': True,

    # Turn on a page that lets staff enter Python code to be run in the
    # sandbox, for testing whether it's enabled properly.
    'ENABLE_DEBUG_RUN_PYTHON': False,

    # Enable URL that shows information about the status of various services
    'ENABLE_SERVICE_STATUS': False,

    # Don't autoplay videos for students
    'AUTOPLAY_VIDEOS': False,

    # Move the student to next page when a video finishes. Set to True to show
    # an auto-advance button in videos. If False, videos never auto-advance.
    'ENABLE_AUTOADVANCE_VIDEOS': False,

    # Enable instructor dash to submit background tasks
    'ENABLE_INSTRUCTOR_BACKGROUND_TASKS': True,

    # Enable instructor to assign individual due dates
    # Note: In order for this feature to work, you must also add
    # 'lms.djangoapps.courseware.student_field_overrides.IndividualStudentOverrideProvider' to
    # the setting FIELD_OVERRIDE_PROVIDERS, in addition to setting this flag to
    # True.
    'INDIVIDUAL_DUE_DATES': False,

    # .. toggle_name: CUSTOM_COURSES_EDX
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Set to True to enable Custom Courses for edX, a feature that is more commonly known as
    #   CCX. Documentation for configuring and using this feature is available at
    #   https://docs.openedx.org/en/latest/site_ops/install_configure_run_guide/configuration/enable_ccx.html
    # .. toggle_warning: When set to true, 'lms.djangoapps.ccx.overrides.CustomCoursesForEdxOverrideProvider' will
    #    be added to MODULESTORE_FIELD_OVERRIDE_PROVIDERS
    # .. toggle_use_cases: opt_in, circuit_breaker
    # .. toggle_creation_date: 2015-04-10
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/6636
    'CUSTOM_COURSES_EDX': False,

    # Toggle to enable certificates of courses on dashboard
    'ENABLE_VERIFIED_CERTIFICATES': False,
    # Settings for course import olx validation
    'ENABLE_COURSE_OLX_VALIDATION': False,

    # .. toggle_name: FEATURES['DISABLE_HONOR_CERTIFICATES']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Set to True to disable honor certificates. Typically used when your installation only
    #   allows verified certificates, like courses.edx.org.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2019-05-14
    # .. toggle_tickets: https://openedx.atlassian.net/browse/PROD-269
    'DISABLE_HONOR_CERTIFICATES': False,  # Toggle to disable honor certificates

    'DISABLE_AUDIT_CERTIFICATES': False,  # Toggle to disable audit certificates

    # .. toggle_name: FEATURES['AUTOMATIC_AUTH_FOR_TESTING']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Set to True to perform acceptance and load test. Auto auth view is responsible for load
    #    testing and is controlled by this feature flag. Session verification (of CacheBackedAuthenticationMiddleware)
    #    is a security feature, but it can be turned off by enabling this feature flag.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2013-07-25
    # .. toggle_warning: If this has been set to True then the account activation email will be skipped.
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/417
    'AUTOMATIC_AUTH_FOR_TESTING': False,

    # .. toggle_name: FEATURES['RESTRICT_AUTOMATIC_AUTH']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: Prevent auto auth from creating superusers or modifying existing users. Auto auth is a
    #   mechanism where superusers can simply modify attributes of other users by accessing the "/auto_auth url" with
    #   the right
    #   querystring parameters.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2018-05-07
    # .. toggle_tickets: https://openedx.atlassian.net/browse/TE-2545
    'RESTRICT_AUTOMATIC_AUTH': True,

    # .. toggle_name: FEATURES['ENABLE_LOGIN_MICROFRONTEND']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Enable the login micro frontend.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2018-05-07
    # .. toggle_warning: The login MFE domain name should be listed in LOGIN_REDIRECT_WHITELIST.
    'ENABLE_LOGIN_MICROFRONTEND': False,

    # .. toggle_name: FEATURES['SKIP_EMAIL_VALIDATION']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Turn this on to skip sending emails for user validation.
    #   Beware, as this leaves the door open to potential spam abuse.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2018-05-07
    # .. toggle_warning: The login MFE domain name should be listed in LOGIN_REDIRECT_WHITELIST.
    'SKIP_EMAIL_VALIDATION': False,

    # .. toggle_name: FEATURES['ENABLE_COSMETIC_DISPLAY_PRICE']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Enable the display of "cosmetic_display_price", set in a course advanced settings. This
    #   cosmetic price is used when there is no registration price associated to the course.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2014-10-10
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/6876
    # .. toggle_warning: The use case of this feature toggle is uncertain.
    'ENABLE_COSMETIC_DISPLAY_PRICE': False,

    # Automatically approve student identity verification attempts
    # .. toggle_name: FEATURES['AUTOMATIC_VERIFY_STUDENT_IDENTITY_FOR_TESTING']
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
    'AUTOMATIC_VERIFY_STUDENT_IDENTITY_FOR_TESTING': False,

    # Maximum number of rows to include in the csv file for downloading problem responses.
    'MAX_PROBLEM_RESPONSES_COUNT': 5000,

    'ENABLED_PAYMENT_REPORTS': [
        "refund_report",
        "itemized_purchase_report",
        "university_revenue_share",
        "certificate_status"
    ],

    # Turn off account locking if failed login attempts exceeds a limit
    # .. toggle_name: FEATURES['ENABLE_MAX_FAILED_LOGIN_ATTEMPTS']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: This feature will keep track of the number of failed login attempts on a given user's
    #   email. If the number of consecutive failed login attempts - without a successful login at some point - reaches
    #   a configurable threshold (default 6), then the account will be locked for a configurable amount of seconds
    #   (30 minutes) which will prevent additional login attempts until this time period has passed. If a user
    #   successfully logs in, all the counter which tracks the number of failed attempts will be reset back to 0. If
    #   set to False then account locking will be disabled for failed login attempts.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2014-01-30
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/2331
    'ENABLE_MAX_FAILED_LOGIN_ATTEMPTS': True,

    # Hide any Personally Identifiable Information from application logs
    'SQUELCH_PII_IN_LOGS': True,

    # .. toggle_name: FEATURES['EMBARGO']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Turns on embargo functionality, which blocks users from
    #   the site or courses based on their location. Embargo can restrict users by states
    #   and whitelist/blacklist (IP Addresses (ie. 10.0.0.0), Networks (ie. 10.0.0.0/24)), or the user profile country.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2014-02-27
    # .. toggle_target_removal_date: None
    # .. toggle_warning: reverse proxy should be configured appropriately for example Client IP address headers
    #   (e.g HTTP_X_FORWARDED_FOR) should be configured.
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/2749
    'EMBARGO': False,

    # Whether the Wiki subsystem should be accessible via the direct /wiki/ paths. Setting this to True means
    # that people can submit content and modify the Wiki in any arbitrary manner. We're leaving this as True in the
    # defaults, so that we maintain current behavior
    'ALLOW_WIKI_ROOT_ACCESS': True,

    # .. toggle_name: FEATURES['ENABLE_THIRD_PARTY_AUTH']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Turn on third-party auth. Disabled for now because full implementations are not yet
    #   available. Remember to run migrations if you enable this; we don't create tables by default. This feature can
    #   be enabled on a per-site basis. When enabling this feature, remember to define the allowed authentication
    #   backends with the AUTHENTICATION_BACKENDS setting.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2014-09-15
    'ENABLE_THIRD_PARTY_AUTH': False,

    # .. toggle_name: FEATURES['ENABLE_MKTG_SITE']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Toggle to enable alternate urls for marketing links.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2014-03-24
    # .. toggle_warning: When this is enabled, the MKTG_URLS setting should be defined. The use case of this feature
    #   toggle is uncertain.
    'ENABLE_MKTG_SITE': False,

    # Prevent concurrent logins per user
    'PREVENT_CONCURRENT_LOGINS': True,

    # .. toggle_name: FEATURES['ALWAYS_REDIRECT_HOMEPAGE_TO_DASHBOARD_FOR_AUTHENTICATED_USER']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: When a logged in user goes to the homepage ('/') the user will be redirected to the
    #   dashboard page when this flag is set to True - this is default Open edX behavior. Set to False to not redirect
    #   the user.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2014-09-16
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/5220
    'ALWAYS_REDIRECT_HOMEPAGE_TO_DASHBOARD_FOR_AUTHENTICATED_USER': True,

    # .. toggle_name: FEATURES['ENABLE_COURSE_SORTING_BY_START_DATE']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: When a user goes to the homepage ('/') the user sees the courses listed in the
    #   announcement dates order - this is default Open edX behavior. Set to True to change the course sorting behavior
    #   by their start dates, latest first.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2015-03-27
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/7548
    'ENABLE_COURSE_SORTING_BY_START_DATE': True,

    # .. toggle_name: FEATURES['ENABLE_COURSE_HOME_REDIRECT']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: When enabled, along with the ENABLE_MKTG_SITE feature toggle, users who attempt to access a
    #   course "about" page will be redirected to the course home url.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2019-01-15
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/19604
    'ENABLE_COURSE_HOME_REDIRECT': True,

    # Expose Mobile REST API. Note that if you use this, you must also set
    # ENABLE_OAUTH2_PROVIDER to True
    'ENABLE_MOBILE_REST_API': False,

    # .. toggle_name: FEATURES['ENABLE_COMBINED_LOGIN_REGISTRATION_FOOTER']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Display the standard footer in the login page. This feature can be overridden by a site-
    #   specific configuration.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2016-06-24
    # .. toggle_tickets: https://openedx.atlassian.net/browse/OSPR-1320
    'ENABLE_COMBINED_LOGIN_REGISTRATION_FOOTER': False,

    # Enable organizational email opt-in
    'ENABLE_MKTG_EMAIL_OPT_IN': False,

    # .. toggle_name: FEATURES['ENABLE_FOOTER_MOBILE_APP_LINKS']
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
    'ENABLE_FOOTER_MOBILE_APP_LINKS': False,

    # Let students save and manage their annotations
    # .. toggle_name: FEATURES['ENABLE_EDXNOTES']
    # .. toggle_implementation: SettingToggle
    # .. toggle_default: False
    # .. toggle_description: This toggle enables the students to save and manage their annotations in the
    #   course using the notes service. The bulk of the actual work in storing the notes is done by
    #   a separate service (see the edx-notes-api repo).
    # .. toggle_warning: Requires the edx-notes-api service properly running and to have configured the django settings
    #   EDXNOTES_INTERNAL_API and EDXNOTES_PUBLIC_API. If you update this setting, also update it in Studio.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2015-01-04
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/6321
    'ENABLE_EDXNOTES': False,

    # Toggle to enable coordination with the Publisher tool (keep in sync with cms/envs/common.py)
    'ENABLE_PUBLISHER': False,

    # Milestones application flag
    'MILESTONES_APP': False,

    # Prerequisite courses feature flag
    'ENABLE_PREREQUISITE_COURSES': False,

    # For easily adding modes to courses during acceptance testing
    'MODE_CREATION_FOR_TESTING': False,

    # For caching programs in contexts where the LMS can only
    # be reached over HTTP.
    'EXPOSE_CACHE_PROGRAMS_ENDPOINT': False,

    # Courseware search feature
    # .. toggle_name: FEATURES['ENABLE_COURSEWARE_SEARCH']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: When enabled, this adds a Search the course widget on the course outline and courseware
    #   pages for searching courseware data.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2015-01-29
    # .. toggle_warning: In order to get this working, your courses data should be indexed in Elasticsearch. You will
    #   see the search widget on the courseware page only if the DISABLE_COURSE_OUTLINE_PAGE_FLAG is set.
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/6506
    'ENABLE_COURSEWARE_SEARCH': False,

    # .. toggle_name: FEATURES['ENABLE_COURSEWARE_SEARCH_FOR_COURSE_STAFF']
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
    'ENABLE_COURSEWARE_SEARCH_FOR_COURSE_STAFF': False,

    # Dashboard search feature
    # .. toggle_name: FEATURES['ENABLE_DASHBOARD_SEARCH']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: When enabled, this adds a Search Your Courses widget on the dashboard page for searching
    #   courseware data.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2015-01-29
    # .. toggle_warning: In order to get this working, your courses data should be indexed in Elasticsearch.
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/6506
    'ENABLE_DASHBOARD_SEARCH': False,

    # log all information from cybersource callbacks
    'LOG_POSTPAY_CALLBACKS': True,

    # .. toggle_name: FEATURES['LICENSING']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Toggle platform-wide course licensing. The course.license attribute is then used to append
    #   license information to the courseware.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2015-05-14
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/7315
    'LICENSING': False,

    # .. toggle_name: FEATURES['CERTIFICATES_HTML_VIEW']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Set to True to enable course certificates on your instance of Open edX.
    # .. toggle_warning: You must enable this feature flag in both Studio and the LMS and complete the configuration tasks
    #   described here:
    #   https://docs.openedx.org/en/latest/site_ops/install_configure_run_guide/configuration/enable_certificates.html  pylint: disable=line-too-long,useless-suppression
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2015-03-13
    # .. toggle_target_removal_date: None
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/7113
    'CERTIFICATES_HTML_VIEW': False,

    # .. toggle_name: FEATURES['CUSTOM_CERTIFICATE_TEMPLATES_ENABLED']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Set to True to enable custom certificate templates which are configured via Django admin.
    # .. toggle_warning: None
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2015-08-13
    # .. toggle_target_removal_date: None
    # .. toggle_tickets: https://openedx.atlassian.net/browse/SOL-1044
    'CUSTOM_CERTIFICATE_TEMPLATES_ENABLED': False,

    # .. toggle_name: FEATURES['ENABLE_COURSE_DISCOVERY']
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
    'ENABLE_COURSE_DISCOVERY': False,

    # .. toggle_name: FEATURES['ENABLE_COURSE_FILENAME_CCX_SUFFIX']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: If set to True, CCX ID will be included in the generated filename for CCX courses.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2021-03-16
    # .. toggle_target_removal_date: None
    # .. toggle_tickets: None
    # .. toggle_warning: Turning this feature ON will affect all generated filenames which are related to CCX courses.
    'ENABLE_COURSE_FILENAME_CCX_SUFFIX': False,

    # Setting for overriding default filtering facets for Course discovery
    # COURSE_DISCOVERY_FILTERS = ["org", "language", "modes"]

    # Software secure fake page feature flag
    'ENABLE_SOFTWARE_SECURE_FAKE': False,

    # Teams feature
    'ENABLE_TEAMS': True,

    # Show video bumper in LMS
    'ENABLE_VIDEO_BUMPER': False,

    # How many seconds to show the bumper again, default is 7 days:
    'SHOW_BUMPER_PERIODICITY': 7 * 24 * 3600,

    # .. toggle_name: FEATURES['ENABLE_SPECIAL_EXAMS']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Enable to use special exams, aka timed and proctored exams.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2015-09-04
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/9744
    'ENABLE_SPECIAL_EXAMS': False,

    # .. toggle_name: FEATURES['ENABLE_LTI_PROVIDER']
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
    'ENABLE_LTI_PROVIDER': False,

    # .. toggle_name: FEATURES['SHOW_HEADER_LANGUAGE_SELECTOR']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: When set to True, language selector will be visible in the header.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2017-05-25
    # .. toggle_warning: You should set the languages in the DarkLangConfig table to get this working. If you have
    #   not set any languages in the DarkLangConfig table then the language selector will not be visible in the header.
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/15133
    'SHOW_HEADER_LANGUAGE_SELECTOR': False,

    # At edX it's safe to assume that English transcripts are always available
    # This is not the case for all installations.
    # The default value in {lms,cms}/envs/common.py and xmodule/tests/test_video.py should be consistent.
    'FALLBACK_TO_ENGLISH_TRANSCRIPTS': True,

    # .. toggle_name: FEATURES['SHOW_FOOTER_LANGUAGE_SELECTOR']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: When set to True, language selector will be visible in the footer.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2017-05-25
    # .. toggle_warning: LANGUAGE_COOKIE_NAME is required to use footer-language-selector, set it if it has not been set.
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/15133
    'SHOW_FOOTER_LANGUAGE_SELECTOR': False,

    # .. toggle_name: FEATURES['ENABLE_CSMH_EXTENDED']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: Write Courseware Student Module History (CSMH) to the extended table: this logs all
    #   student activities to MySQL, in a separate database.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2020-11-05
    # .. toggle_warning: Even though most Open edX instances run with a separate CSMH database, it may not always be
    #   the case. When disabling this feature flag, remember to remove "lms.djangoapps.coursewarehistoryextended"
    #   from the INSTALLED_APPS and the "StudentModuleHistoryExtendedRouter" from the DATABASE_ROUTERS.
    'ENABLE_CSMH_EXTENDED': True,

    # Read from both the CSMH and CSMHE history tables.
    # This is the default, but can be disabled if all history
    # lives in the Extended table, saving the frontend from
    # making multiple queries.
    'ENABLE_READING_FROM_MULTIPLE_HISTORY_TABLES': True,

    # Set this to False to facilitate cleaning up invalid xml from your modulestore.
    'ENABLE_XBLOCK_XML_VALIDATION': True,

    # .. toggle_name: FEATURES['ALLOW_PUBLIC_ACCOUNT_CREATION']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: Allow public account creation. If this is disabled, users will no longer have access to
    #   the signup page.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2017-04-12
    # .. toggle_tickets: https://openedx.atlassian.net/browse/YONK-513
    'ALLOW_PUBLIC_ACCOUNT_CREATION': True,

    # .. toggle_name: FEATURES['SHOW_REGISTRATION_LINKS']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: Allow registration links. If this is disabled, users will no longer see buttons to the
    #   the signup page.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2023-03-27
    'SHOW_REGISTRATION_LINKS': True,

    # .. toggle_name: FEATURES['ENABLE_COOKIE_CONSENT']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Enable header banner for cookie consent using this service:
    #   https://cookieconsent.insites.com/
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2017-03-03
    # .. toggle_tickets: https://openedx.atlassian.net/browse/OSPR-1560
    'ENABLE_COOKIE_CONSENT': False,

    # Whether or not the dynamic EnrollmentTrackUserPartition should be registered.
    'ENABLE_ENROLLMENT_TRACK_USER_PARTITION': True,

    # Enable one click program purchase
    # See LEARNER-493
    'ENABLE_ONE_CLICK_PROGRAM_PURCHASE': False,

    # .. toggle_name: FEATURES['ALLOW_EMAIL_ADDRESS_CHANGE']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: Allow users to change their email address on the Account Settings page. If this is
    #   disabled, users will not be able to change their email address.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2017-06-26
    # .. toggle_tickets: https://openedx.atlassian.net/browse/OSPR-1735
    'ALLOW_EMAIL_ADDRESS_CHANGE': True,

    # .. toggle_name: FEATURES['ENABLE_BULK_ENROLLMENT_VIEW']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: When set to True the bulk enrollment view is enabled and one can use it to enroll multiple
    #   users in a course using bulk enrollment API endpoint (/api/bulk_enroll/v1/bulk_enroll).
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2017-07-15
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/15006
    'ENABLE_BULK_ENROLLMENT_VIEW': False,

    # Set to enable Enterprise integration
    'ENABLE_ENTERPRISE_INTEGRATION': False,

    # .. toggle_name: FEATURES['ENABLE_HTML_XBLOCK_STUDENT_VIEW_DATA']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Whether HTML Block returns HTML content with the Course Blocks API when the API
    #   is called with student_view_data=html query parameter.
    # .. toggle_warning: Because the Course Blocks API caches its data, the cache must be cleared (e.g. by
    #   re-publishing the course) for changes to this flag to take effect.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2017-08-28
    # .. toggle_tickets: https://openedx.atlassian.net/browse/OSPR-1880
    'ENABLE_HTML_XBLOCK_STUDENT_VIEW_DATA': False,

    # .. toggle_name: FEATURES['ENABLE_PASSWORD_RESET_FAILURE_EMAIL']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Whether to send an email for failed password reset attempts or not. This happens when a
    #   user asks for a password reset but they don't have an account associated to their email. This is useful for
    #   notifying users that they don't have an account associated with email addresses they believe they've registered
    #   with. This setting can be overridden by a site-specific configuration.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2017-07-20
    # .. toggle_tickets: https://openedx.atlassian.net/browse/OSPR-1832
    'ENABLE_PASSWORD_RESET_FAILURE_EMAIL': False,

    # Sets the default browser support. For more information go to http://browser-update.org/customize.html
    'UNSUPPORTED_BROWSER_ALERT_VERSIONS': "{i:10,f:-3,o:-3,s:-3,c:-3}",

    # .. toggle_name: FEATURES['ENABLE_ACCOUNT_DELETION']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: Whether to display the account deletion section on Account Settings page. Set to False to
    #   hide this section.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2018-06-01
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/18298
    'ENABLE_ACCOUNT_DELETION': True,

    # Enable feature to remove enrollments and users. Used to reset state of master's integration environments
    'ENABLE_ENROLLMENT_RESET': False,
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

    # .. toggle_name: FEATURES['ENABLE_AUTHN_MICROFRONTEND']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Supports staged rollout of a new micro-frontend-based implementation of the logistration.
    # .. toggle_use_cases: temporary, open_edx
    # .. toggle_creation_date: 2020-09-08
    # .. toggle_target_removal_date: None
    # .. toggle_tickets: 'https://github.com/openedx/edx-platform/pull/24908'
    # .. toggle_warning: Also set settings.AUTHN_MICROFRONTEND_URL for rollout. This temporary feature
    #   toggle does not have a target removal date.
    'ENABLE_AUTHN_MICROFRONTEND': os.environ.get("EDXAPP_ENABLE_AUTHN_MFE", False),

    ### ORA Feature Flags ###
    # .. toggle_name: FEATURES['ENABLE_ORA_ALL_FILE_URLS']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: A "work-around" feature toggle meant to help in cases where some file uploads are not
    #   discoverable.  If enabled, will iterate through all possible file key suffixes up to the max for displaying
    #   file metadata in staff assessments.
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
    #   discoverable.  If enabled, will pull file metadata from StudentModule.state for display in staff assessments.
    # .. toggle_use_cases: temporary
    # .. toggle_creation_date: 2020-03-03
    # .. toggle_target_removal_date: None
    # .. toggle_tickets: https://openedx.atlassian.net/browse/EDUCATOR-4951
    # .. toggle_warning: This temporary feature toggle does not have a target removal date.
    'ENABLE_ORA_USER_STATE_UPLOAD_DATA': False,

    # .. toggle_name: FEATURES['ENABLE_ORA_USERNAMES_ON_DATA_EXPORT']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Set to True to add deanonymized usernames to ORA data
    #   report.
    # .. toggle_use_cases: temporary
    # .. toggle_creation_date: 2020-06-11
    # .. toggle_target_removal_date: None
    # .. toggle_tickets: https://openedx.atlassian.net/browse/TNL-7273
    # .. toggle_warning: This temporary feature toggle does not have a target removal date.
    'ENABLE_ORA_USERNAMES_ON_DATA_EXPORT': False,

    # .. toggle_name: FEATURES['ENABLE_COURSE_ASSESSMENT_GRADE_CHANGE_SIGNAL']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Set to True to start sending signals for assessment level grade updates. Notably, the only
    #   handler of this signal at the time of this writing sends assessment updates to enterprise integrated channels.
    # .. toggle_use_cases: temporary
    # .. toggle_creation_date: 2020-12-09
    # .. toggle_target_removal_date: 2021-02-01
    # .. toggle_tickets: https://openedx.atlassian.net/browse/ENT-3818
    'ENABLE_COURSE_ASSESSMENT_GRADE_CHANGE_SIGNAL': False,

    # .. toggle_name: FEATURES['ALLOW_ADMIN_ENTERPRISE_COURSE_ENROLLMENT_DELETION']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: If true, allows for the deletion of EnterpriseCourseEnrollment records via Django Admin.
    # .. toggle_use_cases: opt_in
    # .. toggle_creation_date: 2021-01-27
    # .. toggle_tickets: https://openedx.atlassian.net/browse/ENT-4022
    'ALLOW_ADMIN_ENTERPRISE_COURSE_ENROLLMENT_DELETION': False,

    # .. toggle_name: FEATURES['ENABLE_BULK_USER_RETIREMENT']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Set to True to enable bulk user retirement through REST API. This is disabled by
    #   default.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2021-03-11
    # .. toggle_target_removal_date: None
    # .. toggle_warning: None
    # .. toggle_tickets: 'https://openedx.atlassian.net/browse/OSPR-5290'
    'ENABLE_BULK_USER_RETIREMENT': False,

    # .. toggle_name: FEATURES['ENABLE_INTEGRITY_SIGNATURE']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Whether to display honor code agreement for learners before their first grade assignment
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

    # .. toggle_name: FEATURES['ENABLE_NEW_BULK_EMAIL_EXPERIENCE']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: When true, replaces the bulk email tool found on the
    #   instructor dashboard with a link to the new communications MFE version instead.
    #   Setting the tool to false will leave the old bulk email tool experience in place.
    # .. toggle_use_cases: opt_in
    # .. toggle_creation_date: 2022-03-21
    # .. toggle_target_removal_date: None
    # .. toggle_tickets: 'https://openedx.atlassian.net/browse/MICROBA-1758'
    'ENABLE_NEW_BULK_EMAIL_EXPERIENCE': False,

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

    # .. toggle_name: FEATURES['ENABLE_CERTIFICATES_IDV_REQUIREMENT']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Whether to enforce ID Verification requirements for course certificates generation
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2022-04-26
    # .. toggle_target_removal_date: None
    # .. toggle_tickets: 'https://openedx.atlassian.net/browse/MST-1458'
    'ENABLE_CERTIFICATES_IDV_REQUIREMENT': False,

    # .. toggle_name: FEATURES['DISABLE_ALLOWED_ENROLLMENT_IF_ENROLLMENT_CLOSED']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Set to True to disable enrollment for user invited to a course
    # .. if user is registering before enrollment start date or after enrollment end date
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2022-06-06
    # .. toggle_tickets: 'https://github.com/openedx/edx-platform/pull/29538'
    'DISABLE_ALLOWED_ENROLLMENT_IF_ENROLLMENT_CLOSED': False,

    # .. toggle_name: FEATURES['SEND_LEARNING_CERTIFICATE_LIFECYCLE_EVENTS_TO_BUS']
    # .. toggle_implementation: SettingToggle
    # .. toggle_default: False
    # .. toggle_description: When True, the system will publish certificate lifecycle signals to the event bus.
    #    This toggle is used to create the EVENT_BUS_PRODUCER_CONFIG setting.
    # .. toggle_warning: The default may be changed in a later release. See
    #    https://github.com/openedx/openedx-events/issues/265
    # .. toggle_use_cases: opt_in
    # .. toggle_creation_date: 2023-10-10
    # .. toggle_tickets: https://github.com/openedx/openedx-events/issues/210
    'SEND_LEARNING_CERTIFICATE_LIFECYCLE_EVENTS_TO_BUS': False,

    # .. toggle_name: FEATURES['ENABLE_GRADING_METHOD_IN_PROBLEMS']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Enables the grading method feature in capa problems.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2024-03-22
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/33911
    'ENABLE_GRADING_METHOD_IN_PROBLEMS': False,

    # .. toggle_name: FEATURES['ENABLE_COURSEWARE_SEARCH_VERIFIED_REQUIRED']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: When enabled, the courseware search feature will only be enabled
    #   for users in a verified enrollment track.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2024-04-24
    'ENABLE_COURSEWARE_SEARCH_VERIFIED_ENROLLMENT_REQUIRED': False,

    # .. toggle_name: FEATURES['BADGES_ENABLED']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Set to True to enable badges functionality.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2024-04-02
    # .. toggle_target_removal_date: None
    'BADGES_ENABLED': False,

    # .. toggle_name: FEATURES['ENABLE_LEGACY_MD5_HASH_FOR_ANONYMOUS_USER_ID']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Whether to enable the legacy MD5 hashing algorithm to generate anonymous user id
    #   instead of the newer SHAKE128 hashing algorithm
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2022-08-08
    # .. toggle_target_removal_date: None
    # .. toggle_tickets: 'https://github.com/openedx/edx-platform/pull/30832'
    'ENABLE_LEGACY_MD5_HASH_FOR_ANONYMOUS_USER_ID': False,

    # .. toggle_name: FEATURES['ENABLE_ESHE_INSTRUCTOR_ROLE']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Whether to enable the ESHE Instructor role
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2023-07-31
    # .. toggle_target_removal_date: None
    # .. toggle_tickets: 'https://github.com/open-craft/edx-platform/pull/561/files'
    'ENABLE_ESHE_INSTRUCTOR_ROLE': False,

    # .. toggle_name: FEATURES['ENABLE_TEACHING_ASSISTANT_ROLE']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Whether to enable the Teaching Assistant role
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2024-02-12
    # .. toggle_target_removal_date: None
    # .. toggle_tickets: 'https://github.com/open-craft/edx-platform/pull/632/files'
    'ENABLE_TEACHING_ASSISTANT_ROLE': False,

    # .. toggle_name: FEATURES['DISABLE_DATES_TAB']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Disables dates tab for all courses.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2024-04-15
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/34511
    'DISABLE_DATES_TAB': False,
}

# Specifies extra XBlock fields that should available when requested via the Course Blocks API
# Should be a list of tuples of (block_type, field_name), where block_type can also be "*" for all block types.
# e.g. COURSE_BLOCKS_API_EXTRA_FIELDS = [  ('course', 'other_course_settings'), ("problem", "weight")  ]
COURSE_BLOCKS_API_EXTRA_FIELDS = []


ASSET_IGNORE_REGEX = r"(^\._.*$)|(^\.DS_Store$)|(^.*~$)"

# Used for A/B testing
DEFAULT_GROUPS = []

# If this is true, random scores will be generated for the purpose of debugging the profile graphs
GENERATE_PROFILE_SCORES = False

# .. setting_name: GRADEBOOK_FREEZE_DAYS
# .. setting_default: 30
# .. setting_description: Sets the number of days after which the gradebook will freeze following the course's end.
GRADEBOOK_FREEZE_DAYS = 30

# Used with XQueue
XQUEUE_WAITTIME_BETWEEN_REQUESTS = 5  # seconds
XQUEUE_INTERFACE = {
    'url': 'http://localhost:18040',
    'basic_auth': ['edx', 'edx'],
    'django_auth': {
        'username': 'lms',
        'password': 'password'
    }
}

# Used with Email sending
RETRY_ACTIVATION_EMAIL_MAX_ATTEMPTS = 5
RETRY_ACTIVATION_EMAIL_TIMEOUT = 0.5

# Software Secure request retry settings
# Time in seconds before a retry of the task should be 60 mints.
SOFTWARE_SECURE_REQUEST_RETRY_DELAY = 60 * 60
# Maximum of 6 retries before giving up.
SOFTWARE_SECURE_RETRY_MAX_ATTEMPTS = 6

RETRY_CALENDAR_SYNC_EMAIL_MAX_ATTEMPTS = 5

MARKETING_EMAILS_OPT_IN = False

# .. toggle_name: ENABLE_COPPA_COMPLIANCE
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When True, enforces COPPA compliance and removes YOB field from registration form and account
# .. settings page. Also hide YOB banner from profile page.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2021-10-27
# .. toggle_tickets: 'https://openedx.atlassian.net/browse/VAN-622'
ENABLE_COPPA_COMPLIANCE = False

############################# SET PATH INFORMATION #############################
PROJECT_ROOT = path(__file__).abspath().dirname().dirname()  # /edx-platform/lms
REPO_ROOT = PROJECT_ROOT.dirname()
COMMON_ROOT = REPO_ROOT / "common"
OPENEDX_ROOT = REPO_ROOT / "openedx"
XMODULE_ROOT = REPO_ROOT / "xmodule"
ENV_ROOT = REPO_ROOT.dirname()  # virtualenv dir /edx-platform is in
COURSES_ROOT = ENV_ROOT / "data"
NODE_MODULES_ROOT = REPO_ROOT / "node_modules"

DATA_DIR = COURSES_ROOT

# For geolocation ip database
GEOIP_PATH = REPO_ROOT / "common/static/data/geoip/GeoLite2-Country.mmdb"
# Where to look for a status message
STATUS_MESSAGE_PATH = ENV_ROOT / "status_message.json"

############################ Global Database Configuration #####################

DATABASE_ROUTERS = [
    'openedx.core.lib.django_courseware_routers.StudentModuleHistoryExtendedRouter',
    'edx_django_utils.db.read_replica.ReadReplicaRouter',
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
OAUTH_EXPIRE_CONFIDENTIAL_CLIENT_DAYS = 365
OAUTH_EXPIRE_PUBLIC_CLIENT_DAYS = 30

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
# This is required for the migrations in oauth_dispatch.models
# otherwise it fails saying this attribute is not present in Settings
OAUTH2_PROVIDER_APPLICATION_MODEL = 'oauth2_provider.Application'

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
# Mako templating
import tempfile  # pylint: disable=wrong-import-position,wrong-import-order
MAKO_MODULE_DIR = os.path.join(tempfile.gettempdir(), 'mako_lms')
MAKO_TEMPLATE_DIRS_BASE = [
    PROJECT_ROOT / 'templates',
    COMMON_ROOT / 'templates',
    XMODULE_ROOT / 'capa' / 'templates',
    COMMON_ROOT / 'djangoapps' / 'pipeline_mako' / 'templates',
    OPENEDX_ROOT / 'core' / 'djangoapps' / 'cors_csrf' / 'templates',
    OPENEDX_ROOT / 'core' / 'djangoapps' / 'dark_lang' / 'templates',
    OPENEDX_ROOT / 'core' / 'lib' / 'license' / 'templates',
    OPENEDX_ROOT / 'features' / 'course_experience' / 'templates',
]


def _make_mako_template_dirs(settings):
    """
    Derives the final Mako template directories list from other settings.
    """
    if settings.ENABLE_COMPREHENSIVE_THEMING:
        themes_dirs = get_theme_base_dirs_from_settings(settings.COMPREHENSIVE_THEME_DIRS)
        for theme in get_themes_unchecked(themes_dirs, settings.PROJECT_ROOT):
            if theme.themes_base_dir not in settings.MAKO_TEMPLATE_DIRS_BASE:
                settings.MAKO_TEMPLATE_DIRS_BASE.insert(0, theme.themes_base_dir)
    return settings.MAKO_TEMPLATE_DIRS_BASE


CONTEXT_PROCESSORS = [
    'django.template.context_processors.request',
    'django.template.context_processors.static',
    'django.template.context_processors.i18n',
    'django.contrib.auth.context_processors.auth',  # this is required for admin
    'django.template.context_processors.csrf',

    # Added for django-wiki
    'django.template.context_processors.media',
    'django.template.context_processors.tz',
    'django.contrib.messages.context_processors.messages',
    'sekizai.context_processors.sekizai',

    # Hack to get required link URLs to password reset templates
    'common.djangoapps.edxmako.shortcuts.marketing_link_context_processor',

    # Timezone processor (sends language and time_zone preference)
    'lms.djangoapps.courseware.context_processor.user_timezone_locale_prefs',

    # Online contextual help
    'help_tokens.context_processor',
    'openedx.core.djangoapps.site_configuration.context_processors.configuration_context',

    # Mobile App processor (Detects if request is from the mobile app)
    'lms.djangoapps.mobile_api.context_processor.is_from_mobile_app',

    # Context processor necessary for the survey report message appear on the admin site
    'openedx.features.survey_report.context_processors.admin_extra_context'


]

# Django templating
TEMPLATES = [
    {
        'NAME': 'django',
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        # Don't look for template source files inside installed applications.
        'APP_DIRS': False,
        # Instead, look for template source files in these dirs.
        'DIRS': [
            PROJECT_ROOT / "templates",
            COMMON_ROOT / 'templates',
            XMODULE_ROOT / 'capa' / 'templates',
            COMMON_ROOT / 'djangoapps' / 'pipeline_mako' / 'templates',
            COMMON_ROOT / 'static',  # required to statically include common Underscore templates
        ],
        # Options specific to this backend.
        'OPTIONS': {
            'loaders': [
                # We have to use mako-aware template loaders to be able to include
                # mako templates inside django templates (such as main_django.html).
                'openedx.core.djangoapps.theming.template_loaders.ThemeTemplateLoader',
                'common.djangoapps.edxmako.makoloader.MakoFilesystemLoader',
                'common.djangoapps.edxmako.makoloader.MakoAppDirectoriesLoader',
            ],
            'context_processors': CONTEXT_PROCESSORS,
            # Change 'debug' in your environment settings files - not here.
            'debug': False
        }
    },
    {
        'NAME': 'mako',
        'BACKEND': 'common.djangoapps.edxmako.backend.Mako',
        # Don't look for template source files inside installed applications.
        'APP_DIRS': False,
        # Instead, look for template source files in these dirs.
        'DIRS': Derived(_make_mako_template_dirs),
        # Options specific to this backend.
        'OPTIONS': {
            'context_processors': CONTEXT_PROCESSORS,
            # Change 'debug' in your environment settings files - not here.
            'debug': False,
        }
    },
]
DEFAULT_TEMPLATE_ENGINE = TEMPLATES[0]
DEFAULT_TEMPLATE_ENGINE_DIRS = DEFAULT_TEMPLATE_ENGINE['DIRS'][:]

###############################################################################################

AUTHENTICATION_BACKENDS = [
    'rules.permissions.ObjectPermissionBackend',
    'django.contrib.auth.backends.AllowAllUsersModelBackend',
    'bridgekeeper.backends.RulePermissionBackend',
]

STUDENT_FILEUPLOAD_MAX_SIZE = 4 * 1000 * 1000  # 4 MB
MAX_FILEUPLOADS_PER_INPUT = 20

# Set request limits for maximum size of a request body and maximum number of GET/POST parameters. (>=Django 1.10)
# Limits are currently disabled - but can be used for finer-grained denial-of-service protection.
DATA_UPLOAD_MAX_MEMORY_SIZE = None
DATA_UPLOAD_MAX_NUMBER_FIELDS = None

# Configuration option for when we want to grab server error pages
STATIC_GRAB = False
DEV_CONTENT = True

# License for serving content in China
ICP_LICENSE = None
ICP_LICENSE_INFO = {}

ELASTIC_SEARCH_CONFIG = [
    {
        'use_ssl': False,
        'host': 'localhost',
        'port': 9200
    }
]

SEARCH_COURSEWARE_CONTENT_LOG_PARAMS = False


# .. setting_name: ELASTIC_SEARCH_INDEX_PREFIX
# .. setting_default: ''
# .. setting_description: Specifies the prefix used when naming elasticsearch indexes related to edx-search.
ELASTICSEARCH_INDEX_PREFIX = ""

VIDEO_CDN_URL = {
    'EXAMPLE_COUNTRY_CODE': "http://example.com/edx/video?s3_url="
}

STATIC_ROOT_BASE = '/edx/var/edxapp/staticfiles'

LOGGING_ENV = 'sandbox'

EDX_ROOT_URL = ''
EDX_API_KEY = "PUT_YOUR_API_KEY_HERE"

LOGIN_REDIRECT_URL = EDX_ROOT_URL + '/login'
LOGIN_URL = EDX_ROOT_URL + '/login'

PARTNER_SUPPORT_EMAIL = ''

CERT_QUEUE = 'certificates'

ALTERNATE_WORKER_QUEUES = 'cms'

LOCAL_LOGLEVEL = "INFO"

LOG_DIR = '/edx/var/log/edx'

DATA_DIR = '/edx/var/edxapp/data'

# .. setting_name: MAINTENANCE_BANNER_TEXT
# .. setting_default: 'Sample banner message'
# .. setting_description: Specifies the text that is rendered on the maintenance banner.
# .. setting_warning: Depends on the `open_edx_util.display_maintenance_warning` waffle switch.
#   The banner is only rendered when the switch is activated.
MAINTENANCE_BANNER_TEXT = 'Sample banner message'

DJFS = {
    'type': 'osfs',
    'directory_root': '/edx/var/edxapp/django-pyfs/static/django-pyfs',
    'url_root': '/static/django-pyfs',
}

# Set certificate issued date format. It supports all formats supported by
# `common.djangoapps.util.date_utils.strftime_localized`.
CERTIFICATE_DATE_FORMAT = "%B %-d, %Y"

### Dark code. Should be enabled in local settings for devel.

ENABLE_MULTICOURSE = False  # set to False to disable multicourse display (see lib.util.views.edXhome)

# .. toggle_name: WIKI_ENABLED
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: This setting allows us to have a collaborative tool to contribute or
#   modify content of course related materials.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2012-07-13
WIKI_ENABLED = True

###

COURSE_MODE_DEFAULTS = {
    'android_sku': None,
    'bulk_sku': None,
    'currency': 'usd',
    'description': None,
    'expiration_datetime': None,
    'ios_sku': None,
    'min_price': 0,
    'name': _('Audit'),
    'sku': None,
    'slug': 'audit',
    'suggested_prices': '',
}

# IP addresses that are allowed to reload the course, etc.
# TODO (vshnayder): Will probably need to change as we get real access control in.
LMS_MIGRATION_ALLOWED_IPS = []

USAGE_KEY_PATTERN = r'(?P<usage_key_string>(?:i4x://?[^/]+/[^/]+/[^/]+/[^@]+(?:@[^/]+)?)|(?:[^/]+))'
ASSET_KEY_PATTERN = r'(?P<asset_key_string>(?:/?c4x(:/)?/[^/]+/[^/]+/[^/]+/[^@]+(?:@[^/]+)?)|(?:[^/]+))'
USAGE_ID_PATTERN = r'(?P<usage_id>(?:i4x://?[^/]+/[^/]+/[^/]+/[^@]+(?:@[^/]+)?)|(?:[^/]+))'


# The space is required for space-dependent languages like Arabic and Farsi.
# However, backward compatibility with Ficus older releases is still maintained (space is still not valid)
# in the AccountCreationForm and the user_api through the ENABLE_UNICODE_USERNAME feature flag.
USERNAME_REGEX_PARTIAL = r'[\w .@_+-]+'
USERNAME_PATTERN = fr'(?P<username>{USERNAME_REGEX_PARTIAL})'


############################## EVENT TRACKING #################################
LMS_SEGMENT_KEY = None

# FIXME: Should we be doing this truncation?
TRACK_MAX_EVENT = 50000

DEBUG_TRACK_LOG = False

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
TRACKING_IGNORE_URL_PATTERNS = [r'^/event', r'^/login', r'^/heartbeat', r'^/segmentio/event', r'^/performance']

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

TRACKING_SEGMENTIO_WEBHOOK_SECRET = None
TRACKING_SEGMENTIO_ALLOWED_TYPES = ['track']
TRACKING_SEGMENTIO_DISALLOWED_SUBSTRING_NAMES = []
TRACKING_SEGMENTIO_SOURCE_MAP = {
    'analytics-android': 'mobile',
    'analytics-ios': 'mobile',
}

######################## GOOGLE ANALYTICS ###########################
GOOGLE_ANALYTICS_ACCOUNT = None
GOOGLE_SITE_VERIFICATION_ID = ''
GOOGLE_ANALYTICS_LINKEDIN = 'GOOGLE_ANALYTICS_LINKEDIN_DUMMY'
GOOGLE_ANALYTICS_TRACKING_ID = None
GOOGLE_ANALYTICS_4_ID = None

######################## BRANCH.IO ###########################
BRANCH_IO_KEY = ''

######################## OPTIMIZELY ###########################
OPTIMIZELY_PROJECT_ID = None
OPTIMIZELY_FULLSTACK_SDK_KEY = None

######################## HOTJAR ###########################
HOTJAR_SITE_ID = 00000

######################## subdomain specific settings ###########################
COURSE_LISTINGS = {}

############# XBlock Configuration ##########

# Import after sys.path fixup
from xmodule.modulestore.edit_info import EditInfoMixin  # lint-amnesty, pylint: disable=wrong-import-order, wrong-import-position
from xmodule.modulestore.inheritance import InheritanceMixin  # lint-amnesty, pylint: disable=wrong-import-order, wrong-import-position
from xmodule.x_module import XModuleMixin  # lint-amnesty, pylint: disable=wrong-import-order, wrong-import-position

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
)

# .. setting_name: XBLOCK_EXTRA_MIXINS
# .. setting_default: ()
# .. setting_description: Custom mixins that will be dynamically added to every XBlock and XBlockAside instance.
#     These can be classes or dotted-path references to classes.
#     For example: `XBLOCK_EXTRA_MIXINS = ('my_custom_package.my_module.MyCustomMixin',)`
XBLOCK_EXTRA_MIXINS = ()

# .. setting_name: XBLOCK_FIELD_DATA_WRAPPERS
# .. setting_default: ()
# .. setting_description: Paths to wrapper methods which should be applied to every XBlock's FieldData.
XBLOCK_FIELD_DATA_WRAPPERS = ()

XBLOCK_FS_STORAGE_BUCKET = None
XBLOCK_FS_STORAGE_PREFIX = None

# .. setting_name: XBLOCK_SETTINGS
# .. setting_default: {}
# .. setting_description: Dictionary containing server-wide configuration of XBlocks on a per-type basis.
#     By default, keys should match the XBlock `block_settings_key` attribute/property. If the attribute/property
#     is not defined, use the XBlock class name. Check `xmodule.services.SettingsService`
#     for more reference.
XBLOCK_SETTINGS = {}

# .. setting_name: XBLOCK_RUNTIME_V2_EPHEMERAL_DATA_CACHE
# .. setting_default: default
# .. setting_description: The django cache key of the cache to use for storing anonymous user state for XBlocks.
XBLOCK_RUNTIME_V2_EPHEMERAL_DATA_CACHE = 'default'

############# ModuleStore Configuration ##########

MODULESTORE_BRANCH = 'published-only'

DOC_STORE_CONFIG = {
    'db': 'edxapp',
    'host': 'localhost',
    'replicaSet': '',
    'password': 'password',
    'port': 27017,
    'user': 'edxapp',
    'collection': 'modulestore',
    'ssl': False,
    # https://api.mongodb.com/python/2.9.1/api/pymongo/mongo_client.html#module-pymongo.mongo_client
    # default is never timeout while the connection is open,
    #this means it needs to explicitly close raising pymongo.errors.NetworkTimeout
    'socketTimeoutMS': 6000,
    'connectTimeoutMS': 2000,  # default is 20000, I believe raises pymongo.errors.ConnectionFailure
    # Not setting waitQueueTimeoutMS and waitQueueMultiple since pymongo defaults to nobody being allowed to wait
    'auth_source': None,
    'read_preference': 'SECONDARY_PREFERRED'
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
                        'fs_root': Derived(lambda settings: settings.DATA_DIR),
                        'render_template': 'common.djangoapps.edxmako.shortcuts.render_to_string',
                    }
                },
                {
                    'NAME': 'draft',
                    'ENGINE': 'xmodule.modulestore.mongo.DraftMongoModuleStore',
                    'DOC_STORE_CONFIG': DOC_STORE_CONFIG,
                    'OPTIONS': {
                        'default_class': 'xmodule.hidden_block.HiddenBlock',
                        'fs_root': Derived(lambda settings: settings.DATA_DIR),
                        'render_template': 'common.djangoapps.edxmako.shortcuts.render_to_string',
                    }
                }
            ]
        }
    }
}


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
    },

    # Overrides to default configurable 'limits' (above).
    # Keys should be course run ids (or, in the special case of code running
    # on the /debug/run_python page, the key is 'debug_run_python').
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

# Code jail REST service
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
# .. setting_description: Set the number of seconds LMS will wait to establish an internal
#   connection to the codejail remote service.
CODE_JAIL_REST_SERVICE_CONNECT_TIMEOUT = 0.5  # time in seconds
# .. setting_name: CODE_JAIL_REST_SERVICE_READ_TIMEOUT
# .. setting_default: 3.5
# .. setting_description: Set the number of seconds LMS will wait for a response from the
#   codejail remote service endpoint.
CODE_JAIL_REST_SERVICE_READ_TIMEOUT = 3.5  # time in seconds


############################### DJANGO BUILT-INS ###############################
# Change DEBUG in your environment settings files, not here
DEBUG = False
USE_TZ = True
SESSION_COOKIE_SECURE = False
SESSION_SAVE_EVERY_REQUEST = False
SESSION_SERIALIZER = 'openedx.core.lib.session_serializers.PickleSerializer'
SESSION_COOKIE_DOMAIN = ""
SESSION_COOKIE_NAME = 'sessionid'

# django-session-cookie middleware
DCS_SESSION_COOKIE_SAMESITE = 'None'
DCS_SESSION_COOKIE_SAMESITE_FORCE_ALL = True

# This is the domain that is used to set shared cookies between various sub-domains.
SHARED_COOKIE_DOMAIN = ""

# CMS base
CMS_BASE = 'localhost:18010'

# LMS base
LMS_BASE = 'localhost:18000'

# Studio name
STUDIO_NAME = 'Studio'
STUDIO_SHORT_NAME = 'Studio'

# Site info
SITE_NAME = "localhost"
HTTPS = 'on'
ROOT_URLCONF = 'lms.urls'
# NOTE: Please set ALLOWED_HOSTS to some sane value, as we do not allow the default '*'
# Platform Email
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'localhost'
EMAIL_PORT = 25
EMAIL_USE_TLS = False
EMAIL_HOST_USER = ''
EMAIL_HOST_PASSWORD = ''
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

ADMINS = ()
MANAGERS = ADMINS

# Static content
STATIC_URL = '/static/'
STATIC_ROOT = os.environ.get('STATIC_ROOT_LMS', ENV_ROOT / "staticfiles")
STATIC_URL_BASE = '/static/'

STATICFILES_DIRS = [
    COMMON_ROOT / "static",
    PROJECT_ROOT / "static",
    NODE_MODULES_ROOT / "@edx",
    # Temporarily adding the following static path as we are migrating the built-in blocks' Sass to vanilla CSS.
    # Once all of the built-in blocks are extracted from edx-platform, we can remove this static path.
    # Relevant ticket: https://github.com/openedx/edx-platform/issues/35300
    XMODULE_ROOT / "static",
]

FAVICON_PATH = 'images/favicon.ico'
DEFAULT_COURSE_ABOUT_IMAGE_URL = 'images/pencils.jpg'

# User-uploaded content
MEDIA_ROOT = '/edx/var/edxapp/media/'
MEDIA_URL = '/media/'

# Locale/Internationalization
CELERY_TIMEZONE = 'UTC'
TIME_ZONE = 'UTC'
LANGUAGE_CODE = 'en'  # http://www.i18nguy.com/unicode/language-identifiers.html
# these languages display right to left
LANGUAGES_BIDI = ("he", "ar", "fa", "ur", "fa-ir", "rtl")

LANGUAGE_COOKIE_NAME = "openedx-language-preference"

# Sourced from http://www.localeplanet.com/icu/ and wikipedia
LANGUAGES = [
    ('en', 'English'),
    ('rtl', 'Right-to-Left Test Language'),
    ('eo', 'Dummy Language (Esperanto)'),  # Dummy languaged used for testing

    ('am', 'አማርኛ'),  # Amharic
    ('ar', 'العربية'),  # Arabic
    ('az', 'azərbaycanca'),  # Azerbaijani
    ('bg-bg', 'български (България)'),  # Bulgarian (Bulgaria)
    ('bn-bd', 'বাংলা (বাংলাদেশ)'),  # Bengali (Bangladesh)
    ('bn-in', 'বাংলা (ভারত)'),  # Bengali (India)
    ('bs', 'bosanski'),  # Bosnian
    ('ca', 'Català'),  # Catalan
    ('ca@valencia', 'Català (València)'),  # Catalan (Valencia)
    ('cs', 'Čeština'),  # Czech
    ('cy', 'Cymraeg'),  # Welsh
    ('da', 'dansk'),  # Danish
    ('de-de', 'Deutsch (Deutschland)'),  # German (Germany)
    ('el', 'Ελληνικά'),  # Greek
    ('en-uk', 'English (United Kingdom)'),  # English (United Kingdom)
    ('en@lolcat', 'LOLCAT English'),  # LOLCAT English
    ('en@pirate', 'Pirate English'),  # Pirate English
    ('es-419', 'Español (Latinoamérica)'),  # Spanish (Latin America)
    ('es-ar', 'Español (Argentina)'),  # Spanish (Argentina)
    ('es-ec', 'Español (Ecuador)'),  # Spanish (Ecuador)
    ('es-es', 'Español (España)'),  # Spanish (Spain)
    ('es-mx', 'Español (México)'),  # Spanish (Mexico)
    ('es-pe', 'Español (Perú)'),  # Spanish (Peru)
    ('et-ee', 'Eesti (Eesti)'),  # Estonian (Estonia)
    ('eu-es', 'euskara (Espainia)'),  # Basque (Spain)
    ('fa', 'فارسی'),  # Persian
    ('fa-ir', 'فارسی (ایران)'),  # Persian (Iran)
    ('fi-fi', 'Suomi (Suomi)'),  # Finnish (Finland)
    ('fil', 'Filipino'),  # Filipino
    ('fr', 'Français'),  # French
    ('gl', 'Galego'),  # Galician
    ('gu', 'ગુજરાતી'),  # Gujarati
    ('he', 'עברית'),  # Hebrew
    ('hi', 'हिन्दी'),  # Hindi
    ('hr', 'hrvatski'),  # Croatian
    ('hu', 'magyar'),  # Hungarian
    ('hy-am', 'Հայերեն (Հայաստան)'),  # Armenian (Armenia)
    ('id', 'Bahasa Indonesia'),  # Indonesian
    ('it-it', 'Italiano (Italia)'),  # Italian (Italy)
    ('ja-jp', '日本語 (日本)'),  # Japanese (Japan)
    ('kk-kz', 'қазақ тілі (Қазақстан)'),  # Kazakh (Kazakhstan)
    ('km-kh', 'ភាសាខ្មែរ (កម្ពុជា)'),  # Khmer (Cambodia)
    ('kn', 'ಕನ್ನಡ'),  # Kannada
    ('ko-kr', '한국어 (대한민국)'),  # Korean (Korea)
    ('lt-lt', 'Lietuvių (Lietuva)'),  # Lithuanian (Lithuania)
    ('ml', 'മലയാളം'),  # Malayalam
    ('mn', 'Монгол хэл'),  # Mongolian
    ('mr', 'मराठी'),  # Marathi
    ('ms', 'Bahasa Melayu'),  # Malay
    ('nb', 'Norsk bokmål'),  # Norwegian Bokmål
    ('ne', 'नेपाली'),  # Nepali
    ('nl-nl', 'Nederlands (Nederland)'),  # Dutch (Netherlands)
    ('or', 'ଓଡ଼ିଆ'),  # Oriya
    ('pl', 'Polski'),  # Polish
    ('pt-br', 'Português (Brasil)'),  # Portuguese (Brazil)
    ('pt-pt', 'Português (Portugal)'),  # Portuguese (Portugal)
    ('ro', 'română'),  # Romanian
    ('ru', 'Русский'),  # Russian
    ('si', 'සිංහල'),  # Sinhala
    ('sk', 'Slovenčina'),  # Slovak
    ('sl', 'Slovenščina'),  # Slovenian
    ('sq', 'shqip'),  # Albanian
    ('sr', 'Српски'),  # Serbian
    ('sv', 'svenska'),  # Swedish
    ('sw', 'Kiswahili'),  # Swahili
    ('ta', 'தமிழ்'),  # Tamil
    ('te', 'తెలుగు'),  # Telugu
    ('th', 'ไทย'),  # Thai
    ('tr-tr', 'Türkçe (Türkiye)'),  # Turkish (Turkey)
    ('uk', 'Українська'),  # Ukranian
    ('ur', 'اردو'),  # Urdu
    ('vi', 'Tiếng Việt'),  # Vietnamese
    ('uz', 'Ўзбек'),  # Uzbek
    ('zh-cn', '中文 (简体)'),  # Chinese (China)
    ('zh-hk', '中文 (香港)'),  # Chinese (Hong Kong)
    ('zh-tw', '中文 (台灣)'),  # Chinese (Taiwan)
]

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
STATICI18N_OUTPUT_DIR = "js/i18n"


# Localization strings (e.g. django.po) are under these directories
def _make_locale_paths(settings):  # pylint: disable=missing-function-docstring
    locale_paths = list(settings.PREPEND_LOCALE_PATHS)
    locale_paths += [settings.REPO_ROOT + '/conf/locale']  # edx-platform/conf/locale/

    if settings.ENABLE_COMPREHENSIVE_THEMING:
        # Add locale paths to settings for comprehensive theming.
        for locale_path in settings.COMPREHENSIVE_THEME_LOCALE_PATHS:
            locale_paths += (path(locale_path), )
    return locale_paths
LOCALE_PATHS = Derived(_make_locale_paths)

# Messages
MESSAGE_STORAGE = 'django.contrib.messages.storage.session.SessionStorage'

# Guidelines for translators
TRANSLATORS_GUIDE = 'https://docs.openedx.org/en/latest/translators/index.html'

#################################### AWS #######################################
# The number of seconds that a generated URL is valid for.
AWS_QUERYSTRING_EXPIRE = 10 * 365 * 24 * 60 * 60  # 10 years
AWS_SES_REGION_NAME = 'us-east-1'
AWS_SES_REGION_ENDPOINT = 'email.us-east-1.amazonaws.com'
AWS_ACCESS_KEY_ID = None
AWS_SECRET_ACCESS_KEY = None
AWS_QUERYSTRING_AUTH = False
AWS_STORAGE_BUCKET_NAME = "SET-ME-PLEASE (ex. bucket-name)"
AWS_S3_CUSTOM_DOMAIN = "SET-ME-PLEASE (ex. bucket-name.s3.amazonaws.com)"

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
ZENDESK_URL = ''
ZENDESK_USER = ''
ZENDESK_API_KEY = ''
ZENDESK_CUSTOM_FIELDS = {}
ZENDESK_OAUTH_ACCESS_TOKEN = ''
# A mapping of string names to Zendesk Group IDs
# To get the IDs of your groups you can go to
# {zendesk_url}/api/v2/groups.json
ZENDESK_GROUP_ID_MAPPING = {}

##### EMBARGO #####
EMBARGO_SITE_REDIRECT_URL = None

##### shoppingcart Payment #####
PAYMENT_SUPPORT_EMAIL = 'billing@example.com'

# Setting for PAID_COURSE_REGISTRATION, DOES NOT AFFECT VERIFIED STUDENTS
PAID_COURSE_REGISTRATION_CURRENCY = ['usd', '$']

################################# EdxNotes config  #########################

# Configure the LMS to use our stub EdxNotes implementation
# .. setting_name: EDXNOTES_PUBLIC_API
# .. setting_default: http://localhost:18120/api/v1
# .. setting_description: Set the public API endpoint LMS will use in the frontend to
#     interact with the edx_notes_api service.
# .. setting_warning: This setting must be a publicly accessible endpoint. It is only used
#     when the setting FEATURES['ENABLE_EDXNOTES'] is activated.
EDXNOTES_PUBLIC_API = 'http://localhost:18120/api/v1'
# .. setting_name: EDXNOTES_INTERNAL_API
# .. setting_default: http://localhost:18120/api/v1
# .. setting_description: Set the internal API endpoint LMS will use in the backend to
#     interact with the edx_notes_api service.
# .. setting_warning: Normally set to the same value of EDXNOTES_PUBLIC_API. It is not
#     mandatory for this setting to be a publicly accessible endpoint, but to be accessible
#     by the LMS service. It is only used when the setting FEATURES['ENABLE_EDXNOTES'] is
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

########################## Parental controls config  #######################

# The age at which a learner no longer requires parental consent, or None
# if parental consent is never required.
PARENTAL_CONSENT_AGE_LIMIT = 13

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

    # Before anything that looks at cookies, especially the session middleware
    'openedx.core.djangoapps.cookie_metadata.middleware.CookieNameChange',

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

# Clickjacking protection can be disbaled by setting this to 'ALLOW'
X_FRAME_OPTIONS = 'DENY'

# Platform for Privacy Preferences header
P3P_HEADER = 'CP="Open EdX does not have a P3P policy."'

############################### PIPELINE #######################################

PIPELINE = {
    'PIPELINE_ENABLED': True,
    'CSS_COMPRESSOR': None,
    'JS_COMPRESSOR': 'pipeline.compressors.uglifyjs.UglifyJSCompressor',
    # Don't wrap JavaScript as there is code that depends upon updating the global namespace
    'DISABLE_WRAPPER': True,
    # Specify the UglifyJS binary to use
    'UGLIFYJS_BINARY': 'node_modules/.bin/uglifyjs',
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
)


################################# DJANGO-REQUIRE ###############################

# The baseUrl to pass to the r.js optimizer, relative to STATIC_ROOT.
REQUIRE_BASE_URL = "./"

# The name of a build profile to use for your project, relative to REQUIRE_BASE_URL.
# A sensible value would be 'app.build.js'. Leave blank to use the built-in default build profile.
# Set to False to disable running the default profile (e.g. if only using it to build Standalone
# Modules)
REQUIRE_BUILD_PROFILE = "lms/js/build.js"

# The name of the require.js script used by your project, relative to REQUIRE_BASE_URL.
REQUIRE_JS = "common/js/vendor/require.js"

# Whether to run django-require in debug mode.
REQUIRE_DEBUG = False

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

########################## DJANGO DEBUG TOOLBAR ###############################

# We don't enable Django Debug Toolbar universally, but whenever we do, we want
# to avoid patching settings.  Patched settings can cause circular import
# problems: https://django-debug-toolbar.readthedocs.org/en/1.0/installation.html#explicit-setup

DEBUG_TOOLBAR_PATCH_SETTINGS = False

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


# SERVICE_VARIANT specifies name of the variant used, which decides what JSON
# configuration files are read during startup.
SERVICE_VARIANT = os.environ.get('SERVICE_VARIANT', "lms")

# CONFIG_PREFIX specifies the prefix of the JSON configuration files,
# based on the service variant. If no variant is use, don't use a
# prefix.
CONFIG_PREFIX = SERVICE_VARIANT + "." if SERVICE_VARIANT else ""

# Queues configuration

# Name the exchange and queues w.r.t the SERVICE_VARIANT
QUEUE_VARIANT = CONFIG_PREFIX.lower()

CELERY_DEFAULT_EXCHANGE = f'edx.{QUEUE_VARIANT}core'

HIGH_PRIORITY_QUEUE = f'edx.{QUEUE_VARIANT}core.high'
DEFAULT_PRIORITY_QUEUE = f'edx.{QUEUE_VARIANT}core.default'
HIGH_MEM_QUEUE = f'edx.{QUEUE_VARIANT}core.high_mem'

CELERY_DEFAULT_QUEUE = DEFAULT_PRIORITY_QUEUE
CELERY_DEFAULT_ROUTING_KEY = DEFAULT_PRIORITY_QUEUE

CELERY_QUEUES = {
    HIGH_PRIORITY_QUEUE: {},
    DEFAULT_PRIORITY_QUEUE: {},
    HIGH_MEM_QUEUE: {},
}

CELERY_ROUTES = "openedx.core.lib.celery.routers.route_task"
CELERYBEAT_SCHEDULE = {}  # For scheduling tasks, entries can be added to this dict

CELERY_QUEUE_HA_POLICY = 'all'

CELERY_CREATE_MISSING_QUEUES = True

# let logging work as configured:
CELERYD_HIJACK_ROOT_LOGGER = False

CELERY_BROKER_VHOST = ''
CELERY_BROKER_USE_SSL = False
CELERY_EVENT_QUEUE_TTL = None

CELERY_BROKER_TRANSPORT = 'amqp'
CELERY_BROKER_HOSTNAME = 'localhost'
CELERY_BROKER_USER = 'celery'
CELERY_BROKER_PASSWORD = 'celery'

############################## HEARTBEAT ######################################

# Checks run in normal mode by the heartbeat djangoapp
HEARTBEAT_CHECKS = [
    'openedx.core.djangoapps.heartbeat.default_checks.check_modulestore',
    'openedx.core.djangoapps.heartbeat.default_checks.check_database',
]

# Other checks to run by default in "extended"/heavy mode
HEARTBEAT_EXTENDED_CHECKS = (
    'openedx.core.djangoapps.heartbeat.default_checks.check_celery',
)

HEARTBEAT_CELERY_TIMEOUT = 5
HEARTBEAT_CELERY_ROUTING_KEY = HIGH_PRIORITY_QUEUE

################################ Block Structures ###################################

# .. setting_name: BLOCK_STRUCTURES_SETTINGS
# .. setting_default: dict of settings
# .. setting_description: Stores all the settings used by block structures and block structure
#   related tasks. See BLOCK_STRUCTURES_SETTINGS[XXX] documentation for details of each setting.
#   For more information, check https://github.com/openedx/edx-platform/pull/13388.
BLOCK_STRUCTURES_SETTINGS = dict(
    # .. setting_name: BLOCK_STRUCTURES_SETTINGS['COURSE_PUBLISH_TASK_DELAY']
    # .. setting_default: 30
    # .. setting_description: Delay, in seconds, after a new edit of a course is published before
    #   updating the block structures cache. This is needed for a better chance at getting
    #   the latest changes when there are secondary reads in sharded mongoDB clusters.
    #   For more information, check https://github.com/openedx/edx-platform/pull/13388 and
    #   https://github.com/openedx/edx-platform/pull/14571.
    COURSE_PUBLISH_TASK_DELAY=30,

    # .. setting_name: BLOCK_STRUCTURES_SETTINGS['TASK_DEFAULT_RETRY_DELAY']
    # .. setting_default: 30
    # .. setting_description: Delay, in seconds, between retry attempts if a block structure task
    #   fails. For more information, check https://github.com/openedx/edx-platform/pull/13388 and
    #   https://github.com/openedx/edx-platform/pull/14571.
    TASK_DEFAULT_RETRY_DELAY=30,

    # .. setting_name: BLOCK_STRUCTURES_SETTINGS['TASK_MAX_RETRIES']
    # .. setting_default: 5
    # .. setting_description: Maximum number of retries per block structure task.
    #   If the maximum number of retries is exceeded, then you can attempt to either manually run
    #   the celery task, or wait for it to be triggered again.
    #   For more information, check https://github.com/openedx/edx-platform/pull/13388 and
    #   https://github.com/openedx/edx-platform/pull/14571.
    TASK_MAX_RETRIES=5,
)

################################ Bulk Email ###################################

# Suffix used to construct 'from' email address for bulk emails.
# A course-specific identifier is prepended.
BULK_EMAIL_DEFAULT_FROM_EMAIL = 'no-reply@example.com'

# Parameters for breaking down course enrollment into subtasks.
BULK_EMAIL_EMAILS_PER_TASK = 500

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
BULK_EMAIL_ROUTING_KEY = HIGH_PRIORITY_QUEUE

# We also define a queue for smaller jobs so that large courses don't block
# smaller emails (see BULK_EMAIL_JOB_SIZE_THRESHOLD setting)
BULK_EMAIL_ROUTING_KEY_SMALL_JOBS = 'edx.lms.core.default'

# For emails with fewer than these number of recipients, send them through
# a different queue to avoid large courses blocking emails that are meant to be
# sent to self and staff
BULK_EMAIL_JOB_SIZE_THRESHOLD = 100

# Flag to indicate if individual email addresses should be logged as they are sent
# a bulk email message.
BULK_EMAIL_LOG_SENT_EMAILS = False

# Delay in seconds to sleep between individual mail messages being sent,
# when a bulk email task is retried for rate-related reasons.  Choose this
# value depending on the number of workers that might be sending email in
# parallel, and what the SES rate is.
BULK_EMAIL_RETRY_DELAY_BETWEEN_SENDS = 0.02

############################# Email Opt In ####################################

# Minimum age for organization-wide email opt in
EMAIL_OPTIN_MINIMUM_AGE = PARENTAL_CONSENT_AGE_LIMIT

############################## Video ##########################################

YOUTUBE = {
    # YouTube JavaScript API
    'API': 'https://www.youtube.com/iframe_api',

    'TEST_TIMEOUT': 1500,

    # URL to get YouTube metadata
    'METADATA_URL': 'https://www.googleapis.com/youtube/v3/videos/',

    # Web page mechanism for scraping transcript information from youtube video pages
    'TRANSCRIPTS': {
        'CAPTION_TRACKS_REGEX': r"captionTracks\"\:\[(?P<caption_tracks>[^\]]+)",
        'YOUTUBE_URL_BASE': 'https://www.youtube.com/watch?v=',
        'ALLOWED_LANGUAGE_CODES': ["en", "en-US", "en-GB"],
    },

    'IMAGE_API': 'http://img.youtube.com/vi/{youtube_id}/0.jpg',  # /maxresdefault.jpg for 1920*1080
}
YOUTUBE_API_KEY = 'PUT_YOUR_API_KEY_HERE'

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
    'openedx_tagging.core.tagging.apps.TaggingConfig',
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

    # Learning Core Apps, used by v2 content libraries (content_libraries app)
    "openedx_learning.apps.authoring.collections",
    "openedx_learning.apps.authoring.components",
    "openedx_learning.apps.authoring.contents",
    "openedx_learning.apps.authoring.publishing",
    "openedx_learning.apps.authoring.units",
    "openedx_learning.apps.authoring.subsections",
    "openedx_learning.apps.authoring.sections",
]


######################### CSRF #########################################

# Forwards-compatibility with Django 1.7
CSRF_COOKIE_AGE = 60 * 60 * 24 * 7 * 52
# It is highly recommended that you override this in any environment accessed by
# end users
CSRF_COOKIE_SECURE = False
CSRF_TRUSTED_ORIGINS = []
CSRF_TRUSTED_ORIGINS_WITH_SCHEME = []

# If setting a cross-domain cookie, it's really important to choose
# a name for the cookie that is DIFFERENT than the cookies used
# by each subdomain.  For example, suppose the applications
# at these subdomains are configured to use the following cookie names:
#
# 1) foo.example.com --> "csrftoken"
# 2) baz.example.com --> "csrftoken"
# 3) bar.example.com --> "csrftoken"
#
# For the cross-domain version of the CSRF cookie, you need to choose
# a name DIFFERENT than "csrftoken"; otherwise, the new token configured
# for ".example.com" could conflict with the other cookies,
# non-deterministically causing 403 responses.
CROSS_DOMAIN_CSRF_COOKIE_NAME = ''

# When setting the domain for the "cross-domain" version of the CSRF
# cookie, you should choose something like: ".example.com"
# (note the leading dot), where both the referer and the host
# are subdomains of "example.com".
#
# Browser security rules require that
# the cookie domain matches the domain of the server; otherwise
# the cookie won't get set.  And once the cookie gets set, the client
# needs to be on a domain that matches the cookie domain, otherwise
# the client won't be able to read the cookie.
CROSS_DOMAIN_CSRF_COOKIE_DOMAIN = ''


######################### Django Rest Framework ########################

REST_FRAMEWORK = {
    # These default classes add observability around endpoints using defaults, and should
    # not be used anywhere else.
    # Notes on Order:
    # 1. `JwtAuthentication` does not check `is_active`, so email validation does not affect it. However,
    #    `SessionAuthentication` does. These work differently, and order changes in what way, which really stinks. See
    #    https://github.com/openedx/public-engineering/issues/165 for details.
    # 2. `JwtAuthentication` may also update the database based on contents. Since the LMS creates these JWTs, this
    #    shouldn't have any affect at this time. But it could, when and if another service started creating the JWTs.
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'openedx.core.djangolib.default_auth_classes.DefaultJwtAuthentication',
        'openedx.core.djangolib.default_auth_classes.DefaultSessionAuthentication',
    ],
    'DEFAULT_PAGINATION_CLASS': 'edx_rest_framework_extensions.paginators.DefaultPagination',
    'DEFAULT_RENDERER_CLASSES': (
        'rest_framework.renderers.JSONRenderer',
    ),
    'EXCEPTION_HANDLER': 'openedx.core.lib.request_utils.ignored_error_exception_handler',
    'PAGE_SIZE': 10,
    'URL_FORMAT_OVERRIDE': None,
    'DEFAULT_THROTTLE_RATES': {
        'user': '60/minute',
        'service_user': '800/minute',
        'registration_validation': '30/minute',
        'high_service_user': '2000/minute',
    },
}

# .. setting_name: REGISTRATION_VALIDATION_RATELIMIT
# .. setting_default: 30/7d
# .. setting_description: Whenever a user tries to register on edx, the data entered during registration
#    is validated via RegistrationValidationView.
#    It's POST endpoint is rate-limited up to 30 requests per IP Address in a week by default.
#    It was introduced because an attacker can guess or brute force a series of names to enumerate valid users.
# .. setting_tickets: https://github.com/openedx/edx-platform/pull/24664
REGISTRATION_VALIDATION_RATELIMIT = '30/7d'

# .. setting_name: REGISTRATION_RATELIMIT
# .. setting_default: 60/7d
# .. setting_description: New users are registered on edx via RegistrationView.
#    It's POST end-point is rate-limited up to 60 requests per IP Address in a week by default.
#    Purpose of this setting is to restrict an attacker from registering numerous fake accounts.
# .. setting_tickets: https://github.com/openedx/edx-platform/pull/27060
REGISTRATION_RATELIMIT = '60/7d'

SWAGGER_SETTINGS = {
    'DEFAULT_INFO': 'openedx.core.apidocs.api_info',
    'DEEP_LINKING': True,
}

# How long to cache OpenAPI schemas and UI, in seconds.
OPENAPI_CACHE_TIMEOUT = 0

######################### MARKETING SITE ###############################
EDXMKTG_LOGGED_IN_COOKIE_NAME = 'edxloggedin'
EDXMKTG_USER_INFO_COOKIE_NAME = 'edx-user-info'
EDXMKTG_USER_INFO_COOKIE_VERSION = 1

MKTG_URLS = {}
MKTG_URL_OVERRIDES = {}
MKTG_URL_LINK_MAP = {
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
}

STATIC_TEMPLATE_VIEW_DEFAULT_FILE_EXTENSION = 'html'

SUPPORT_SITE_LINK = ''
ID_VERIFICATION_SUPPORT_LINK = ''
PASSWORD_RESET_SUPPORT_LINK = ''
ACTIVATION_EMAIL_SUPPORT_LINK = ''
LOGIN_ISSUE_SUPPORT_LINK = ''

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
SOCIAL_SHARING_SETTINGS = {
    # Note: Ensure 'CUSTOM_COURSE_URLS' has a matching value in cms/envs/common.py
    'CUSTOM_COURSE_URLS': False,
    'DASHBOARD_FACEBOOK': False,
    'FACEBOOK_BRAND': None,
    'CERTIFICATE_FACEBOOK': False,
    'CERTIFICATE_FACEBOOK_TEXT': None,
    'CERTIFICATE_TWITTER': False,
    'CERTIFICATE_TWITTER_TEXT': None,
    'DASHBOARD_TWITTER': False,
    'DASHBOARD_TWITTER_TEXT': None,
    'TWITTER_BRAND': None
}

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

# The footer URLs dictionary maps social footer names
# to URLs defined in configuration.
SOCIAL_MEDIA_FOOTER_ACE_URLS = {
    'reddit': 'http://www.reddit.com/r/edx',
    'twitter': 'https://twitter.com/edXOnline',
    'linkedin': 'http://www.linkedin.com/company/edx',
    'facebook': 'http://www.facebook.com/EdxOnline',
}

# The mobile store URLs dictionary maps mobile store names
# to URLs defined in configuration.
MOBILE_STORE_ACE_URLS = {
    'google': 'https://play.google.com/store/apps/details?id=org.edx.mobile',
    'apple': 'https://itunes.apple.com/us/app/edx/id945480667?mt=8',
}

# The social media logo urls dictionary maps social media names
# to the respective icons
SOCIAL_MEDIA_LOGO_URLS = {
    'reddit': 'http://email-media.s3.amazonaws.com/edX/2021/social_5_reddit.png',
    'twitter': 'http://email-media.s3.amazonaws.com/edX/2021/social_2_twitter.png',
    'linkedin': 'http://email-media.s3.amazonaws.com/edX/2021/social_3_linkedin.png',
    'facebook': 'http://email-media.s3.amazonaws.com/edX/2021/social_1_fb.png',
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

################# Student Verification #################
VERIFY_STUDENT = {
    "DAYS_GOOD_FOR": 365,  # How many days is a verficiation good for?
    # The variable represents the window within which a verification is considered to be "expiring soon."
    "EXPIRING_SOON_WINDOW": 28,
}

################# Student Verification Expiry Email #################
VERIFICATION_EXPIRY_EMAIL = {
    "RESEND_DAYS": 15,
    "DAYS_RANGE": 1,
    "DEFAULT_EMAILS": 2,
}

DISABLE_ACCOUNT_ACTIVATION_REQUIREMENT_SWITCH = "verify_student_disable_account_activation_requirement"

################ Enable credit eligibility feature ####################
ENABLE_CREDIT_ELIGIBILITY = True
# .. toggle_name: FEATURES['ENABLE_CREDIT_ELIGIBILITY']
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: When enabled, it is possible to define a credit eligibility criteria in the CMS. A "Credit
#   Eligibility" section then appears for those courses in the LMS.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2015-06-17
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/8550
FEATURES['ENABLE_CREDIT_ELIGIBILITY'] = ENABLE_CREDIT_ELIGIBILITY

############# Cross-domain requests #################

if FEATURES.get('ENABLE_CORS_HEADERS'):
    CORS_ALLOW_CREDENTIALS = True
    CORS_ORIGIN_WHITELIST = ()
    CORS_ORIGIN_ALLOW_ALL = False
    CORS_ALLOW_INSECURE = False

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

# .. setting_name: LOGIN_REDIRECT_WHITELIST
# .. setting_default: empty list ([])
# .. setting_description: While logout, if logout request has a redirect-url as query strings,
#   then the redirect-url is validated through LOGIN_REDIRECT_WHITELIST.
LOGIN_REDIRECT_WHITELIST = []

###################### Registration ##################################

# .. setting_name: REGISTRATION_EXTRA_FIELDS
# .. setting_default: {'confirm_email': 'hidden', 'level_of_education': 'optional', 'gender': 'optional',
#   'year_of_birth': 'optional', 'mailing_address': 'optional', 'goals': 'optional', 'honor_code': 'required',
#   'terms_of_service': 'hidden', 'city': 'hidden', 'country': 'hidden'}
# .. setting_description: The signup form may contain extra fields that are presented to every user. For every field, we
#   can specifiy whether it should be "required": to display the field, and make it mandatory; "optional": to display
#   the optional field as part of a toggled input field list; "optional-exposed": to display the optional fields among
#   the required fields, and make it non-mandatory; "hidden": to not display the field.
#   When the terms of service are not visible and agreement to the honor code is required (the default), the signup page
#   includes a paragraph that links to the honor code page (defined my MKTG_URLS["HONOR"]). This page might not be
#   available for all Open edX platforms. In such cases, the "honor_code" registration field should be "hidden".
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
}

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

# Optional setting to restrict registration / account creation to only emails
# that match a regex in this list. Set to None to allow any email (default).
REGISTRATION_EMAIL_PATTERNS_ALLOWED = None

# String length for the configurable part of the auto-generated username
AUTO_GENERATED_USERNAME_RANDOM_STRING_LENGTH = 4

########################## CERTIFICATE NAME ########################
CERT_NAME_SHORT = "Certificate"
CERT_NAME_LONG = "Certificate of Achievement"

###################### Grade Downloads ######################
# These keys are used for all of our asynchronous downloadable files, including
# the ones that contain information other than grades.
GRADES_DOWNLOAD_ROUTING_KEY = HIGH_MEM_QUEUE

POLICY_CHANGE_GRADES_ROUTING_KEY = 'edx.lms.core.default'

SINGLE_LEARNER_COURSE_REGRADE_ROUTING_KEY = 'edx.lms.core.default'

RECALCULATE_GRADES_ROUTING_KEY = 'edx.lms.core.default'

SOFTWARE_SECURE_VERIFICATION_ROUTING_KEY = 'edx.lms.core.default'

GRADES_DOWNLOAD = {
    'STORAGE_CLASS': 'django.core.files.storage.FileSystemStorage',
    'STORAGE_KWARGS': {
        'location': '/tmp/edx-s3/grades',
    },
    'STORAGE_TYPE': None,
    'BUCKET': None,
    'ROOT_PATH': None,
}

FINANCIAL_REPORTS = {
    'STORAGE_TYPE': 'localfs',
    'BUCKET': None,
    'ROOT_PATH': 'sandbox',
}

#### Grading policy change-related settings #####
# Rate limit for regrading tasks that a grading policy change can kick off
POLICY_CHANGE_TASK_RATE_LIMIT = '900/h'

#### PASSWORD POLICY SETTINGS #####
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "common.djangoapps.util.password_policy_validators.MinimumLengthValidator",
        "OPTIONS": {
            "min_length": 8
        }
    },
    {
        "NAME": "common.djangoapps.util.password_policy_validators.MaximumLengthValidator",
        "OPTIONS": {
            "max_length": 75
        }
    },
]

PASSWORD_POLICY_COMPLIANCE_ROLLOUT_CONFIG = {
    'ENFORCE_COMPLIANCE_ON_LOGIN': False
}

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

# Default File Upload Storage bucket and prefix. Used by the FileUpload Service.
FILE_UPLOAD_STORAGE_BUCKET_NAME = 'SET-ME-PLEASE (ex. bucket-name)'
FILE_UPLOAD_STORAGE_PREFIX = 'submissions_attachments'

##### ACCOUNT LOCKOUT DEFAULT PARAMETERS #####
# .. setting_name: MAX_FAILED_LOGIN_ATTEMPTS_ALLOWED
# .. setting_default: 6
# .. setting_description: Specifies the maximum failed login attempts allowed to users. Once the user reaches this
#   failure threshold then the account will be locked for a configurable amount of seconds (30 minutes) which will
#   prevent additional login attempts until this time period has passed. This setting is related with
#   MAX_FAILED_LOGIN_ATTEMPTS_LOCKOUT_PERIOD_SECS and only used when ENABLE_MAX_FAILED_LOGIN_ATTEMPTS is enabled.
MAX_FAILED_LOGIN_ATTEMPTS_ALLOWED = 6

# .. setting_name: MAX_FAILED_LOGIN_ATTEMPTS_LOCKOUT_PERIOD_SECS
# .. setting_default: 30 * 60
# .. setting_description: Specifies the lockout period in seconds for consecutive failed login attempts. Once the user
#   reaches the threshold of the login failure, then the account will be locked for the given amount of seconds
#   (30 minutes) which will prevent additional login attempts until this time period has passed. This setting is
#   related with MAX_FAILED_LOGIN_ATTEMPTS_ALLOWED and only used when ENABLE_MAX_FAILED_LOGIN_ATTEMPTS is enabled.
MAX_FAILED_LOGIN_ATTEMPTS_LOCKOUT_PERIOD_SECS = 30 * 60


##### LMS DEADLINE DISPLAY TIME_ZONE #######
TIME_ZONE_DISPLAYED_FOR_DEADLINES = 'UTC'


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

# Source:
# http://loc.gov/standards/iso639-2/ISO-639-2_utf-8.txt according to http://en.wikipedia.org/wiki/ISO_639-1
# Note that this is used as the set of choices to the `code` field of the
# `LanguageProficiency` model.
ALL_LANGUAGES = [
    ["aa", "Afar"],
    ["ab", "Abkhazian"],
    ["af", "Afrikaans"],
    ["ak", "Akan"],
    ["sq", "Albanian"],
    ["am", "Amharic"],
    ["ar", "Arabic"],
    ["an", "Aragonese"],
    ["hy", "Armenian"],
    ["as", "Assamese"],
    ["av", "Avaric"],
    ["ae", "Avestan"],
    ["ay", "Aymara"],
    ["az", "Azerbaijani"],
    ["ba", "Bashkir"],
    ["bm", "Bambara"],
    ["eu", "Basque"],
    ["be", "Belarusian"],
    ["bn", "Bengali"],
    ["bh", "Bihari languages"],
    ["bi", "Bislama"],
    ["bs", "Bosnian"],
    ["br", "Breton"],
    ["bg", "Bulgarian"],
    ["my", "Burmese"],
    ["ca", "Catalan"],
    ["ch", "Chamorro"],
    ["ce", "Chechen"],
    ["zh", "Chinese"],
    ["zh_HANS", "Simplified Chinese"],
    ["zh_HANT", "Traditional Chinese"],
    ["cu", "Church Slavic"],
    ["cv", "Chuvash"],
    ["kw", "Cornish"],
    ["co", "Corsican"],
    ["cr", "Cree"],
    ["cs", "Czech"],
    ["da", "Danish"],
    ["dv", "Divehi"],
    ["nl", "Dutch"],
    ["dz", "Dzongkha"],
    ["en", "English"],
    ["eo", "Esperanto"],
    ["et", "Estonian"],
    ["ee", "Ewe"],
    ["fo", "Faroese"],
    ["fj", "Fijian"],
    ["fi", "Finnish"],
    ["fr", "French"],
    ["fy", "Western Frisian"],
    ["ff", "Fulah"],
    ["ka", "Georgian"],
    ["de", "German"],
    ["gd", "Gaelic"],
    ["ga", "Irish"],
    ["gl", "Galician"],
    ["gv", "Manx"],
    ["el", "Greek"],
    ["gn", "Guarani"],
    ["gu", "Gujarati"],
    ["ht", "Haitian"],
    ["ha", "Hausa"],
    ["he", "Hebrew"],
    ["hz", "Herero"],
    ["hi", "Hindi"],
    ["ho", "Hiri Motu"],
    ["hr", "Croatian"],
    ["hu", "Hungarian"],
    ["ig", "Igbo"],
    ["is", "Icelandic"],
    ["io", "Ido"],
    ["ii", "Sichuan Yi"],
    ["iu", "Inuktitut"],
    ["ie", "Interlingue"],
    ["ia", "Interlingua"],
    ["id", "Indonesian"],
    ["ik", "Inupiaq"],
    ["it", "Italian"],
    ["jv", "Javanese"],
    ["ja", "Japanese"],
    ["kl", "Kalaallisut"],
    ["kn", "Kannada"],
    ["ks", "Kashmiri"],
    ["kr", "Kanuri"],
    ["kk", "Kazakh"],
    ["km", "Central Khmer"],
    ["ki", "Kikuyu"],
    ["rw", "Kinyarwanda"],
    ["ky", "Kirghiz"],
    ["kv", "Komi"],
    ["kg", "Kongo"],
    ["ko", "Korean"],
    ["kj", "Kuanyama"],
    ["ku", "Kurdish"],
    ["lo", "Lao"],
    ["la", "Latin"],
    ["lv", "Latvian"],
    ["li", "Limburgan"],
    ["ln", "Lingala"],
    ["lt", "Lithuanian"],
    ["lb", "Luxembourgish"],
    ["lu", "Luba-Katanga"],
    ["lg", "Ganda"],
    ["mk", "Macedonian"],
    ["mh", "Marshallese"],
    ["ml", "Malayalam"],
    ["mi", "Maori"],
    ["mr", "Marathi"],
    ["ms", "Malay"],
    ["mg", "Malagasy"],
    ["mt", "Maltese"],
    ["mn", "Mongolian"],
    ["na", "Nauru"],
    ["nv", "Navajo"],
    ["nr", "Ndebele, South"],
    ["nd", "Ndebele, North"],
    ["ng", "Ndonga"],
    ["ne", "Nepali"],
    ["nn", "Norwegian Nynorsk"],
    ["nb", "Bokmål, Norwegian"],
    ["no", "Norwegian"],
    ["ny", "Chichewa"],
    ["oc", "Occitan"],
    ["oj", "Ojibwa"],
    ["or", "Oriya"],
    ["om", "Oromo"],
    ["os", "Ossetian"],
    ["pa", "Panjabi"],
    ["fa", "Persian"],
    ["pi", "Pali"],
    ["pl", "Polish"],
    ["pt", "Portuguese"],
    ["ps", "Pushto"],
    ["qu", "Quechua"],
    ["rm", "Romansh"],
    ["ro", "Romanian"],
    ["rn", "Rundi"],
    ["ru", "Russian"],
    ["sg", "Sango"],
    ["sa", "Sanskrit"],
    ["si", "Sinhala"],
    ["sk", "Slovak"],
    ["sl", "Slovenian"],
    ["se", "Northern Sami"],
    ["sm", "Samoan"],
    ["sn", "Shona"],
    ["sd", "Sindhi"],
    ["so", "Somali"],
    ["st", "Sotho, Southern"],
    ["es", "Spanish"],
    ["sc", "Sardinian"],
    ["sr", "Serbian"],
    ["ss", "Swati"],
    ["su", "Sundanese"],
    ["sw", "Swahili"],
    ["sv", "Swedish"],
    ["ty", "Tahitian"],
    ["ta", "Tamil"],
    ["tt", "Tatar"],
    ["te", "Telugu"],
    ["tg", "Tajik"],
    ["tl", "Tagalog"],
    ["th", "Thai"],
    ["bo", "Tibetan"],
    ["ti", "Tigrinya"],
    ["to", "Tonga (Tonga Islands)"],
    ["tn", "Tswana"],
    ["ts", "Tsonga"],
    ["tk", "Turkmen"],
    ["tr", "Turkish"],
    ["tw", "Twi"],
    ["ug", "Uighur"],
    ["uk", "Ukrainian"],
    ["ur", "Urdu"],
    ["uz", "Uzbek"],
    ["ve", "Venda"],
    ["vi", "Vietnamese"],
    ["vo", "Volapük"],
    ["cy", "Welsh"],
    ["wa", "Walloon"],
    ["wo", "Wolof"],
    ["xh", "Xhosa"],
    ["yi", "Yiddish"],
    ["yo", "Yoruba"],
    ["za", "Zhuang"],
    ["zu", "Zulu"]
]


### Apps only installed in some instances
# The order of INSTALLED_APPS matters, so this tuple is the app name and the item in INSTALLED_APPS
# that this app should be inserted *before*. A None here means it should be appended to the list.
OPTIONAL_APPS = [
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

    # Enterprise Apps (http://github.com/openedx/edx-enterprise)
    ('enterprise', None),
    ('consent', None),
    ('integrated_channels.integrated_channel', None),
    ('integrated_channels.degreed', None),
    ('integrated_channels.degreed2', None),
    ('integrated_channels.sap_success_factors', None),
    ('integrated_channels.cornerstone', None),
    ('integrated_channels.xapi', None),
    ('integrated_channels.blackboard', None),
    ('integrated_channels.canvas', None),
    ('integrated_channels.moodle', None),

    # Required by the Enterprise App
    ('django_object_actions', None),  # https://github.com/crccheck/django-object-actions
]

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

### Analytics API
ANALYTICS_API_KEY = ""
ANALYTICS_API_URL = "http://localhost:18100"
ANALYTICS_DASHBOARD_URL = 'http://localhost:18110/courses'
ANALYTICS_DASHBOARD_NAME = 'Your Platform Name Here Insights'

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

# which access.py permission name to check in order to determine if a course is visible in
# the course catalog. We default this to the legacy permission 'see_exists'.
COURSE_CATALOG_VISIBILITY_PERMISSION = 'see_exists'

# which access.py permission name to check in order to determine if a course about page is
# visible. We default this to the legacy permission 'see_exists'.
COURSE_ABOUT_VISIBILITY_PERMISSION = 'see_exists'

DEFAULT_COURSE_VISIBILITY_IN_CATALOG = "both"

# .. toggle_name: DEFAULT_MOBILE_AVAILABLE
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: This specifies if the courses are available for mobile by default. To make any individual
#   course available for mobile one can set the value of Mobile Course Available to true in Advanced Settings from the
#   studio when this is False.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2021-01-26
# .. toggle_tickets: https://openedx.atlassian.net/browse/OSPR-1985
DEFAULT_MOBILE_AVAILABLE = False

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

# Use None for the default search engine
SEARCH_ENGINE = None
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
    'twitter': {
        'display_name': 'Twitter',
        'url_stub': 'twitter.com/',
        'example': 'https://www.twitter.com/username'
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
ECOMMERCE_PUBLIC_URL_ROOT = 'http://localhost:8002'
ECOMMERCE_API_URL = 'http://localhost:8002/api/v2'
ECOMMERCE_API_TIMEOUT = 5
ECOMMERCE_ORDERS_API_CACHE_TIMEOUT = 3600
ECOMMERCE_SERVICE_WORKER_USERNAME = 'ecommerce_worker'
ECOMMERCE_API_SIGNING_KEY = 'SET-ME-PLEASE'

# Exam Service
EXAMS_SERVICE_URL = 'http://localhost:18740/api/v1'

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

COURSE_CATALOG_URL_ROOT = 'http://localhost:8008'
COURSE_CATALOG_API_URL = f'{COURSE_CATALOG_URL_ROOT}/api/v1'

CREDENTIALS_INTERNAL_SERVICE_URL = 'http://localhost:8005'
CREDENTIALS_PUBLIC_SERVICE_URL = 'http://localhost:8005'
# time between scheduled runs, in seconds
NOTIFY_CREDENTIALS_FREQUENCY = 14400

COMMENTS_SERVICE_URL = 'http://localhost:18080'
COMMENTS_SERVICE_KEY = 'password'

# Reverification checkpoint name pattern
CHECKPOINT_PATTERN = r'(?P<checkpoint_name>[^/]+)'

# For the fields override feature
# If using FEATURES['INDIVIDUAL_DUE_DATES'], you should add
# 'lms.djangoapps.courseware.student_field_overrides.IndividualStudentOverrideProvider' to
# this setting.
FIELD_OVERRIDE_PROVIDERS = ()

# Modulestore-level field override providers. These field override providers don't
# require student context.
MODULESTORE_FIELD_OVERRIDE_PROVIDERS = ('openedx.features.content_type_gating.'
                                        'field_override.ContentTypeGatingFieldOverride',)

# PROFILE IMAGE CONFIG
# WARNING: Certain django storage backends do not support atomic
# file overwrites (including the default, OverwriteStorage) - instead
# there are separate calls to delete and then write a new file in the
# storage backend.  This introduces the risk of a race condition
# occurring when a user uploads a new profile image to replace an
# earlier one (the file will temporarily be deleted).
PROFILE_IMAGE_BACKEND = {
    'class': 'openedx.core.storage.OverwriteStorage',
    'options': {
        'location': os.path.join(MEDIA_ROOT, 'profile-images/'),
        'base_url': os.path.join(MEDIA_URL, 'profile-images/'),
    },
}
PROFILE_IMAGE_DEFAULT_FILENAME = 'images/profiles/default'
PROFILE_IMAGE_DEFAULT_FILE_EXTENSION = 'png'
# This key is used in generating unguessable URLs to users'
# profile images. Once it has been set, changing it will make the
# platform unaware of current image URLs.
PROFILE_IMAGE_HASH_SEED = 'placeholder_secret_key'
PROFILE_IMAGE_MAX_BYTES = 1024 * 1024
PROFILE_IMAGE_MIN_BYTES = 100
PROFILE_IMAGE_SIZES_MAP = {
    'full': 500,
    'large': 120,
    'medium': 50,
    'small': 30
}

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
# Initial delay used for retrying tasks.
# Additional retries use longer delays.
# Value is in seconds.
CREDIT_TASK_DEFAULT_RETRY_DELAY = 30

# Maximum number of retries per task for errors that are not related
# to throttling.
CREDIT_TASK_MAX_RETRIES = 5

# Dummy secret key for dev/test
SECRET_KEY = 'dev key'

# Secret keys shared with credit providers.
# Used to digitally sign credit requests (us --> provider)
# and validate responses (provider --> us).
# Each key in the dictionary is a credit provider ID, and
# the value is the 32-character key.
CREDIT_PROVIDER_SECRET_KEYS = {}

# Maximum age in seconds of timestamps we will accept
# when a credit provider notifies us that a student has been approved
# or denied for credit.
CREDIT_PROVIDER_TIMESTAMP_EXPIRATION = 15 * 60

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

# Credit notifications settings
NOTIFICATION_EMAIL_CSS = "templates/credit_notifications/credit_notification.css"
NOTIFICATION_EMAIL_EDX_LOGO = "templates/credit_notifications/edx-logo-header.png"


################################ Settings for JWTs ################################

JWT_ISSUER = 'http://127.0.0.1:8000/oauth2'
DEFAULT_JWT_ISSUER = {
    'ISSUER': 'http://127.0.0.1:8000/oauth2',
    'AUDIENCE': 'change-me',
    'SECRET_KEY': 'SET-ME-PLEASE'
}
JWT_EXPIRATION = 30
JWT_PRIVATE_SIGNING_KEY = None

JWT_AUTH = {
    'JWT_VERIFY_EXPIRATION': True,

    'JWT_PAYLOAD_GET_USERNAME_HANDLER': lambda d: d.get('username'),
    'JWT_LEEWAY': 1,
    'JWT_DECODE_HANDLER': 'edx_rest_framework_extensions.auth.jwt.decoder.jwt_decode_handler',

    'JWT_AUTH_COOKIE': 'edx-jwt-cookie',

    # Number of seconds before JWTs expire
    'JWT_EXPIRATION': 30,
    'JWT_IN_COOKIE_EXPIRATION': 60 * 60,

    'JWT_LOGIN_CLIENT_ID': 'login-service-client-id',
    'JWT_LOGIN_SERVICE_USERNAME': 'login_service_user',

    'JWT_SUPPORTED_VERSION': '1.2.0',

    'JWT_ALGORITHM': 'HS256',
    'JWT_SECRET_KEY': SECRET_KEY,

    'JWT_SIGNING_ALGORITHM': 'RS512',
    'JWT_PRIVATE_SIGNING_JWK': None,
    'JWT_PUBLIC_SIGNING_JWK_SET': None,

    'JWT_ISSUER': 'http://127.0.0.1:8000/oauth2',
    'JWT_AUDIENCE': 'change-me',
    'JWT_ISSUERS': [
        {
            'ISSUER': 'http://127.0.0.1:8000/oauth2',
            'AUDIENCE': 'change-me',
            'SECRET_KEY': SECRET_KEY
        }
    ],
    'JWT_AUTH_COOKIE_HEADER_PAYLOAD': 'edx-jwt-cookie-header-payload',
    'JWT_AUTH_COOKIE_SIGNATURE': 'edx-jwt-cookie-signature',
    'JWT_AUTH_HEADER_PREFIX': 'JWT',
}

EDX_DRF_EXTENSIONS = {
    # Set this value to an empty dict in order to prevent automatically updating
    # user data from values in (possibly stale) JWTs.
    'JWT_PAYLOAD_USER_ATTRIBUTE_MAPPING': {},
    # Allows JWT authentication to find the LMS user id for verification
    'VERIFY_LMS_USER_ID_PROPERTY_NAME': 'id',
}

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

CREDENTIALS_SERVICE_USERNAME = 'credentials_service_user'
CREDENTIALS_GENERATION_ROUTING_KEY = DEFAULT_PRIORITY_QUEUE
CREDENTIALS_COURSE_COMPLETION_STATE = 'awarded'

# Queue to use for award program certificates
PROGRAM_CERTIFICATES_ROUTING_KEY = 'edx.lms.core.default'

# Settings for Comprehensive Theming app

# See https://github.com/openedx/edx-django-sites-extensions for more info
# Default site to use if site matching request headers does not exist
SITE_ID = 1

# .. setting_name: COMPREHENSIVE_THEME_DIRS
# .. setting_default: []
# .. setting_description: A list of paths to directories, each of which will
#   be searched for comprehensive themes. Do not override this Django setting directly.
#   Instead, set the COMPREHENSIVE_THEME_DIRS environment variable, using colons (:) to
#   separate paths.
COMPREHENSIVE_THEME_DIRS = os.environ.get("COMPREHENSIVE_THEME_DIRS", "").split(":")

# .. setting_name: COMPREHENSIVE_THEME_LOCALE_PATHS
# .. setting_default: []
# .. setting_description: A list of the paths to themes locale directories e.g.
#   "COMPREHENSIVE_THEME_LOCALE_PATHS" : ["/edx/src/edx-themes/conf/locale"].
COMPREHENSIVE_THEME_LOCALE_PATHS = []


# .. setting_name: PREPEND_LOCALE_PATHS
# .. setting_default: []
# .. setting_description: A list of the paths to locale directories to load first e.g.
#   "PREPEND_LOCALE_PATHS" : ["/edx/my-locales/"].
PREPEND_LOCALE_PATHS = []

# .. setting_name: DEFAULT_SITE_THEME
# .. setting_default: None
# .. setting_description: Theme to use when no site or site theme is defined, for example
#   "dark-theme". Set to None if you want to use openedx default theme.
# .. setting_warning: The theme folder needs to be in 'edx-platform/themes' or define the path
#   to the theme folder in COMPREHENSIVE_THEME_DIRS. To be effective, ENABLE_COMPREHENSIVE_THEMING
#   has to be enabled.
DEFAULT_SITE_THEME = None

# .. toggle_name: ENABLE_COMPREHENSIVE_THEMING
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When enabled, this toggle activates the use of the custom theme
#   defined by DEFAULT_SITE_THEME.
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

# API access management
API_ACCESS_MANAGER_EMAIL = 'api-access@example.com'
API_ACCESS_FROM_EMAIL = 'api-requests@example.com'
API_DOCUMENTATION_URL = 'https://course-catalog-api-guide.readthedocs.io/en/latest/'
AUTH_DOCUMENTATION_URL = 'https://course-catalog-api-guide.readthedocs.io/en/latest/authentication/index.html'

# Affiliate cookie tracking
AFFILIATE_COOKIE_NAME = 'dev_affiliate_id'

############## Settings for RedirectMiddleware ###############

# Setting this to None causes Redirect data to never expire
# The cache is cleared when Redirect models are saved/deleted
REDIRECT_CACHE_TIMEOUT = None  # The length of time we cache Redirect model data
REDIRECT_CACHE_KEY_PREFIX = 'redirects'

############## Settings for LMS Context Sensitive Help ##############

HELP_TOKENS_INI_FILE = REPO_ROOT / "lms" / "envs" / "help_tokens.ini"
HELP_TOKENS_LANGUAGE_CODE = Derived(lambda settings: settings.LANGUAGE_CODE)
HELP_TOKENS_VERSION = Derived(lambda settings: doc_version())
HELP_TOKENS_BOOKS = {
    'learner': 'https://edx.readthedocs.io/projects/open-edx-learner-guide',
    'course_author': 'https://edx.readthedocs.io/projects/open-edx-building-and-running-a-course',
}

############## OPEN EDX ENTERPRISE SERVICE CONFIGURATION ######################
# The Open edX Enterprise service is currently hosted via the LMS container/process.
# However, for all intents and purposes this service is treated as a standalone IDA.
# These configuration settings are specific to the Enterprise service and you should
# not find references to them within the edx-platform project.
#
# Only used if FEATURES['ENABLE_ENTERPRISE_INTEGRATION'] == True.

ENTERPRISE_ENROLLMENT_API_URL = LMS_INTERNAL_ROOT_URL + LMS_ENROLLMENT_API_PATH
ENTERPRISE_PUBLIC_ENROLLMENT_API_URL = LMS_ROOT_URL + LMS_ENROLLMENT_API_PATH
ENTERPRISE_COURSE_ENROLLMENT_AUDIT_MODES = ['audit', 'honor']
ENTERPRISE_SUPPORT_URL = ''
ENTERPRISE_CUSTOMER_CATALOG_DEFAULT_CONTENT_FILTER = {}
ENTERPRISE_CUSTOMER_SUCCESS_EMAIL = "customersuccess@edx.org"
ENTERPRISE_INTEGRATIONS_EMAIL = "enterprise-integrations@edx.org"


# The setting key maps to the channel code (e.g. 'SAP' for success factors), Channel code is defined as
# part of django model of each integrated channel in edx-enterprise.
# The absence of a key/value pair translates to NO LIMIT on the number of "chunks" transmitted per cycle.
INTEGRATED_CHANNELS_API_CHUNK_TRANSMISSION_LIMIT = {}

############## ENTERPRISE SERVICE API CLIENT CONFIGURATION ######################
# The LMS communicates with the Enterprise service via the requests.Session() client
# These default settings are utilized by the LMS when interacting with the service,
# and are overridden by the configuration parameter accessors defined in production.py

ENTERPRISE_API_URL = 'https://localhost:18000/enterprise/api/v1'
ENTERPRISE_CONSENT_API_URL = LMS_INTERNAL_ROOT_URL + '/consent/api/v1/'
ENTERPRISE_SERVICE_WORKER_USERNAME = 'enterprise_worker'
ENTERPRISE_API_CACHE_TIMEOUT = 3600  # Value is in seconds
ENTERPRISE_CUSTOMER_LOGO_IMAGE_SIZE = 512   # Enterprise logo image size limit in KB's
ENTERPRISE_CATALOG_INTERNAL_ROOT_URL = 'http://enterprise.catalog.app:18160'
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

# Setting for Open API key and prompts used by edx-enterprise.
CHAT_COMPLETION_API = 'https://example.com/chat/completion'
CHAT_COMPLETION_API_KEY = 'i am a key'
LEARNER_ENGAGEMENT_PROMPT_FOR_ACTIVE_CONTRACT = ''
LEARNER_ENGAGEMENT_PROMPT_FOR_NON_ACTIVE_CONTRACT = ''
LEARNER_PROGRESS_PROMPT_FOR_ACTIVE_CONTRACT = ''
LEARNER_PROGRESS_PROMPT_FOR_NON_ACTIVE_CONTRACT = ''


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
BASE_COOKIE_DOMAIN = 'localhost'
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

ENTERPRISE_MARKETING_FOOTER_QUERY_PARAMS = {}
ENTERPRISE_TAGLINE = ''
TRANSCRIPT_LANG_CACHE_TIMEOUT = 60 * 60 * 24  # 24 hours

############## Settings for Course Enrollment Modes ######################
# The min_price key refers to the minimum price allowed for an instance
# of a particular type of course enrollment mode. This is not to be confused
# with the min_price field of the CourseMode model, which refers to the actual
# price of the CourseMode.
COURSE_ENROLLMENT_MODES = {
    "audit": {
        "id": 1,
        "slug": "audit",
        "display_name": _("Audit"),
        "min_price": 0,
    },
    "verified": {
        "id": 2,
        "slug": "verified",
        "display_name": _("Verified"),
        "min_price": 1,
    },
    "professional": {
        "id": 3,
        "slug": "professional",
        "display_name": _("Professional"),
        "min_price": 1,
    },
    "no-id-professional": {
        "id": 4,
        "slug": "no-id-professional",
        "display_name": _("No-Id-Professional"),
        "min_price": 0,
    },
    "credit": {
        "id": 5,
        "slug": "credit",
        "display_name": _("Credit"),
        "min_price": 0,
    },
    "honor": {
        "id": 6,
        "slug": "honor",
        "display_name": _("Honor"),
        "min_price": 0,
    },
    "masters": {
        "id": 7,
        "slug": "masters",
        "display_name": _("Master's"),
        "min_price": 0,
    },
    "executive-education": {
        "id": 8,
        "slug": "executive-educations",
        "display_name": _("Executive Education"),
        "min_price": 1
    },
    "unpaid-executive-education": {
        "id": 9,
        "slug": "unpaid-executive-education",
        "display_name": _("Unpaid Executive Education"),
        "min_price": 0
    },
    "paid-executive-education": {
        "id": 10,
        "slug": "paid-executive-education",
        "display_name": _("Paid Executive Education"),
        "min_price": 1
    },
    "unpaid-bootcamp": {
        "id": 11,
        "slug": "unpaid-bootcamp",
        "display_name": _("Unpaid Bootcamp"),
        "min_price": 0
    },
    "paid-bootcamp": {
        "id": 12,
        "slug": "paid-bootcamp",
        "display_name": _("Paid Bootcamp"),
        "min_price": 1
    },
}

CONTENT_TYPE_GATE_GROUP_IDS = {
    'limited_access': 1,
    'full_access': 2,
}

############## Settings for the Discovery App ######################

COURSES_API_CACHE_TIMEOUT = 3600  # Value is in seconds


# Initialize to 'unknown', but read from JSON in production.py
EDX_PLATFORM_REVISION = 'release'

############## Settings for Completion API #########################

# Once a user has watched this percentage of a video, mark it as complete:
# (0.0 = 0%, 1.0 = 100%)
COMPLETION_VIDEO_COMPLETE_PERCENTAGE = 0.95
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

##### LOGISTRATION RATE LIMIT SETTINGS #####
LOGISTRATION_RATELIMIT_RATE = '100/5m'
LOGISTRATION_PER_EMAIL_RATELIMIT_RATE = '30/5m'
LOGISTRATION_API_RATELIMIT = '20/m'
LOGIN_AND_REGISTER_FORM_RATELIMIT = '100/5m'
RESET_PASSWORD_TOKEN_VALIDATE_API_RATELIMIT = '30/7d'
RESET_PASSWORD_API_RATELIMIT = '30/7d'
OPTIONAL_FIELD_API_RATELIMIT = '10/h'

##### PASSWORD RESET RATE LIMIT SETTINGS #####
PASSWORD_RESET_IP_RATE = '1/m'
PASSWORD_RESET_EMAIL_RATE = '2/h'

# Keeping this for back compatibility with learner dashboard api
GENERAL_RECOMMENDATION = {}

############### Settings for Retirement #####################
# .. setting_name: RETIRED_USERNAME_PREFIX
# .. setting_default: retired__user_
# .. setting_description: Set the prefix part of hashed usernames for retired users. Used by the derived
#     setting RETIRED_USERNAME_FMT.
RETIRED_USERNAME_PREFIX = 'retired__user_'
# .. setting_name: RETIRED_EMAIL_PREFIX
# .. setting_default: retired__user_
# .. setting_description: Set the prefix part of hashed emails for retired users. Used by the derived
#     setting RETIRED_EMAIL_FMT.
RETIRED_EMAIL_PREFIX = 'retired__user_'
# .. setting_name: RETIRED_EMAIL_DOMAIN
# .. setting_default: retired.invalid
# .. setting_description: Set the domain part of hashed emails for retired users. Used by the derived
#     setting RETIRED_EMAIL_FMT.
RETIRED_EMAIL_DOMAIN = 'retired.invalid'
# .. setting_name: RETIRED_USERNAME_FMT
# .. setting_default: retired__user_{}
# .. setting_description: Set the format a retired user username field gets transformed into, where {}
#     is replaced with the hash of the original username. This is a derived setting that depends on
#     RETIRED_USERNAME_PREFIX value.
RETIRED_USERNAME_FMT = Derived(lambda settings: settings.RETIRED_USERNAME_PREFIX + '{}')
# .. setting_name: RETIRED_EMAIL_FMT
# .. setting_default: retired__user_{}@retired.invalid
# .. setting_description: Set the format a retired user email field gets transformed into, where {} is
#     replaced with the hash of the original email. This is a derived setting that depends on
#     RETIRED_EMAIL_PREFIX and RETIRED_EMAIL_DOMAIN values.
RETIRED_EMAIL_FMT = Derived(lambda settings: settings.RETIRED_EMAIL_PREFIX + '{}@' + settings.RETIRED_EMAIL_DOMAIN)
# .. setting_name: RETIRED_USER_SALTS
# .. setting_default: ['abc', '123']
# .. setting_description: Set a list of salts used for hashing usernames and emails on users retirement.
# .. setting_warning: Only the last item in this list is used as a salt for all new retirements, but
#     historical salts are preserved in order to guarantee that all hashed usernames and emails can still
#     be checked.
RETIRED_USER_SALTS = ['abc', '123']
# .. setting_name: RETIREMENT_SERVICE_WORKER_USERNAME
# .. setting_default: RETIREMENT_SERVICE_USER
# .. setting_description: Set the username of the retirement service worker user. Retirement scripts
#     authenticate with LMS as this user with oauth client credentials.
RETIREMENT_SERVICE_WORKER_USERNAME = 'RETIREMENT_SERVICE_USER'

# These states are the default, but are designed to be overridden in configuration.
# .. setting_name: RETIREMENT_STATES
# .. setting_default:
#     [
#         'PENDING',
#         'LOCKING_ACCOUNT',
#         'LOCKING_COMPLETE',
#         'RETIRING_FORUMS',
#         'FORUMS_COMPLETE',
#         'RETIRING_EMAIL_LISTS',
#         'EMAIL_LISTS_COMPLETE',
#         'RETIRING_ENROLLMENTS',
#         'ENROLLMENTS_COMPLETE',
#         'RETIRING_NOTES',
#         'NOTES_COMPLETE',
#         'RETIRING_LMS',
#         'LMS_COMPLETE',
#         'ERRORED',
#         'ABORTED',
#         'COMPLETE',
#     ]
# .. setting_description: Set a list that defines the name and order of states for the retirement
#     workflow.
# .. setting_warning: These states are stored in the database and it is the responsibility of the
#     administrator to populate the state list since the states can vary across different installations.
#     There must be, at minimum, a PENDING state at the beginning, and COMPLETED, ERRORED, and ABORTED
#     states at the end of the list.
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
# .. setting_name: ACCOUNT_MICROFRONTEND_URL
# .. setting_default: None
# .. setting_description: Base URL of the micro-frontend-based account settings page.
# .. setting_warning: Also set site's ENABLE_ACCOUNT_MICROFRONTEND and
#     account.redirect_to_microfrontend waffle flag
ACCOUNT_MICROFRONTEND_URL = None
AUTHN_MICROFRONTEND_URL = None
AUTHN_MICROFRONTEND_DOMAIN = None
PROGRAM_CONSOLE_MICROFRONTEND_URL = None
# .. setting_name: LEARNING_MICROFRONTEND_URL
# .. setting_default: None
# .. setting_description: Base URL of the micro-frontend-based courseware page.
LEARNING_MICROFRONTEND_URL = None
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
# .. setting_name: DISCUSSIONS_MICROFRONTEND_URL
# .. setting_default: None
# .. setting_description: Base URL of the micro-frontend-based discussions page.
# .. setting_warning: Also set site's courseware.discussions_mfe waffle flag.
DISCUSSIONS_MICROFRONTEND_URL = None
# .. setting_name: DISCUSSIONS_MFE_FEEDBACK_URL
# .. setting_default: None
# .. setting_description: Base URL of the discussions micro-frontend google form based feedback.
DISCUSSIONS_MFE_FEEDBACK_URL = None
# .. setting_name: EXAMS_DASHBOARD_MICROFRONTEND_URL
# .. setting_default: None
# .. setting_description: Base URL of the exams dashboard micro-frontend for instructors.
EXAMS_DASHBOARD_MICROFRONTEND_URL = None
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

############### Settings swift #####################################
SWIFT_USERNAME = None
SWIFT_KEY = None
SWIFT_TENANT_ID = None
SWIFT_TENANT_NAME = None
SWIFT_AUTH_URL = None
SWIFT_AUTH_VERSION = None
SWIFT_REGION_NAME = None
SWIFT_USE_TEMP_URLS = None
SWIFT_TEMP_URL_KEY = None
SWIFT_TEMP_URL_DURATION = 1800  # seconds

############### Settings for facebook ##############################
FACEBOOK_APP_ID = 'FACEBOOK_APP_ID'
FACEBOOK_APP_SECRET = 'FACEBOOK_APP_SECRET'
FACEBOOK_API_VERSION = 'v2.1'

############### Settings for django-fernet-fields ##################
FERNET_KEYS = [
    'DUMMY KEY CHANGE BEFORE GOING TO PRODUCTION',
]

############### Settings for user-state-client ##################
# Maximum number of rows to fetch in XBlockUserStateClient calls. Adjust for performance
USER_STATE_BATCH_SIZE = 5000

############### Settings for edx-rbac  ###############
SYSTEM_WIDE_ROLE_CLASSES = []

############## Plugin Django Apps #########################

from edx_django_utils.plugins import get_plugin_apps, add_plugins  # pylint: disable=wrong-import-position,wrong-import-order
from openedx.core.djangoapps.plugins.constants import ProjectType, SettingsType  # pylint: disable=wrong-import-position
INSTALLED_APPS.extend(get_plugin_apps(ProjectType.LMS))
add_plugins(__name__, ProjectType.LMS, SettingsType.COMMON)

DEPRECATED_ADVANCED_COMPONENT_TYPES = []

############### Settings for video pipeline ##################
VIDEO_UPLOAD_PIPELINE = {
    'VEM_S3_BUCKET': '',
    'BUCKET': '',
    'ROOT_PATH': '',
}

############### Settings for django file storage ##################
DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'

### Proctoring configuration (redirct URLs and keys shared between systems) ####
PROCTORING_BACKENDS = {
    'DEFAULT': 'null',
    # The null key needs to be quoted because
    # null is a language independent type in YAML
    'null': {}
}

PROCTORED_EXAM_VIEWABLE_PAST_DUE = False

############### The SAML private/public key values ################
SOCIAL_AUTH_SAML_SP_PRIVATE_KEY = ""
SOCIAL_AUTH_SAML_SP_PUBLIC_CERT = ""
SOCIAL_AUTH_SAML_SP_PRIVATE_KEY_DICT = {}
SOCIAL_AUTH_SAML_SP_PUBLIC_CERT_DICT = {}

######################### rate limit for yt_video_metadata api ##############

RATE_LIMIT_FOR_VIDEO_METADATA_API = '10/minute'

########################## MAILCHIMP SETTINGS #################################
MAILCHIMP_NEW_USER_LIST_ID = ""

SYSLOG_SERVER = ''
FEEDBACK_SUBMISSION_EMAIL = ''
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

################# Settings for brand logos. #################
LOGO_IMAGE_EXTRA_TEXT = ''
LOGO_URL = None
LOGO_URL_PNG = None
LOGO_TRADEMARK_URL = None
FAVICON_URL = None
DEFAULT_EMAIL_LOGO_URL = 'https://edx-cdn.org/v3/default/logo.png'

################# Settings for olx validation. #################
COURSE_OLX_VALIDATION_STAGE = 1
COURSE_OLX_VALIDATION_IGNORE_LIST = None

################# show account activate cta after register ########################
SHOW_ACTIVATE_CTA_POPUP_COOKIE_NAME = 'show-account-activation-popup'
# .. toggle_name: SOME_FEATURE_NAME
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Flag would be used to show account activation popup after the registration
# .. toggle_use_cases: open_edx
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/27661
# .. toggle_creation_date: 2021-06-10
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
COURSE_BULK_EMAIL_HELP_URL = "https://docs.openedx.org/en/latest/educators/references/communication/bulk_email.html"
ORA_SETTINGS_HELP_URL = "https://docs.openedx.org/en/latest/educators/how-tos/course_development/exercise_tools/Manage_ORA_Assignment.html"

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

######################## Enterprise API Client ########################
ENTERPRISE_BACKEND_SERVICE_EDX_OAUTH2_KEY = "enterprise-backend-service-key"
ENTERPRISE_BACKEND_SERVICE_EDX_OAUTH2_SECRET = "enterprise-backend-service-secret"
ENTERPRISE_BACKEND_SERVICE_EDX_OAUTH2_PROVIDER_URL = "http://127.0.0.1:8000/oauth2"

# keys for  big blue button live provider
COURSE_LIVE_GLOBAL_CREDENTIALS = {}

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

############## NOTIFICATIONS ##############
NOTIFICATIONS_EXPIRY = 60
EXPIRED_NOTIFICATIONS_DELETE_BATCH_SIZE = 10000
NOTIFICATION_CREATION_BATCH_SIZE = 76
NOTIFICATIONS_DEFAULT_FROM_EMAIL = "no-reply@example.com"
NOTIFICATION_TYPE_ICONS = {}
DEFAULT_NOTIFICATION_ICON_URL = ""

############## NUDGE EMAILS ###############
# .. setting_name: DISABLED_ORGS_FOR_PROGRAM_NUDGE
# .. setting_default: []
# .. setting_description: List of organization codes that should be disabled
# .. for program nudge emails.
# .. eg ['BTDx', 'MYTx']
DISABLED_ORGS_FOR_PROGRAM_NUDGE = []

############################ AI_TRANSLATIONS ##################################
AI_TRANSLATIONS_API_URL = 'http://localhost:18760/api/v1'

#### django-simple-history##
# disable indexing on date field its coming from django-simple-history.
SIMPLE_HISTORY_DATE_INDEX = False


def _should_send_certificate_events(settings):
    return settings.FEATURES['SEND_LEARNING_CERTIFICATE_LIFECYCLE_EVENTS_TO_BUS']


#### Event bus producing ####

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
    # CMS events. These have to be copied over here because cms.common adds some derived entries as well,
    # and the derivation fails if the keys are missing. If we ever fully decouple the lms and cms settings,
    # we can remove these.
    'org.openedx.content_authoring.xblock.published.v1': {
        'course-authoring-xblock-lifecycle':
            {'event_key_field': 'xblock_info.usage_key', 'enabled': False},
    },
    'org.openedx.content_authoring.xblock.deleted.v1': {
        'course-authoring-xblock-lifecycle':
            {'event_key_field': 'xblock_info.usage_key', 'enabled': False},
    },
    'org.openedx.content_authoring.xblock.duplicated.v1': {
        'course-authoring-xblock-lifecycle':
            {'event_key_field': 'xblock_info.usage_key', 'enabled': False},
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

BEAMER_PRODUCT_ID = ""

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


####################### Setting for built-in Blocks Extraction #######################
# The following Django settings flags have been introduced temporarily to facilitate
# the rollout of the extracted built-in Blocks. Flags will use to toggle between
# the old and new block quickly without putting course content or user state at risk.
#
# Ticket: https://github.com/openedx/edx-platform/issues/35308

# .. toggle_name: USE_EXTRACTED_WORD_CLOUD_BLOCK
# .. toggle_default: False
# .. toggle_implementation: DjangoSetting
# .. toggle_description: Enables the use of the extracted Word Cloud XBlock, which has been shifted to the 'openedx/xblocks-contrib' repo.
# .. toggle_use_cases: temporary
# .. toggle_warning: Not production-ready until https://github.com/openedx/edx-platform/issues/34840 is done.
# .. toggle_creation_date: 2024-11-10
# .. toggle_target_removal_date: 2025-06-01
USE_EXTRACTED_WORD_CLOUD_BLOCK = False

# .. toggle_name: USE_EXTRACTED_ANNOTATABLE_BLOCK
# .. toggle_default: False
# .. toggle_implementation: DjangoSetting
# .. toggle_description: Enables the use of the extracted annotatable XBlock, which has been shifted to the 'openedx/xblocks-contrib' repo.
# .. toggle_use_cases: temporary
# .. toggle_warning: Not production-ready until https://github.com/openedx/edx-platform/issues/34841 is done.
# .. toggle_creation_date: 2024-11-10
# .. toggle_target_removal_date: 2025-06-01
USE_EXTRACTED_ANNOTATABLE_BLOCK = False

# .. toggle_name: USE_EXTRACTED_POLL_QUESTION_BLOCK
# .. toggle_default: False
# .. toggle_implementation: DjangoSetting
# .. toggle_description: Enables the use of the extracted poll question XBlock, which has been shifted to the 'openedx/xblocks-contrib' repo.
# .. toggle_use_cases: temporary
# .. toggle_warning: Not production-ready until https://github.com/openedx/edx-platform/issues/34839 is done.
# .. toggle_creation_date: 2024-11-10
# .. toggle_target_removal_date: 2025-06-01
USE_EXTRACTED_POLL_QUESTION_BLOCK = False

# .. toggle_name: USE_EXTRACTED_LTI_BLOCK
# .. toggle_default: False
# .. toggle_implementation: DjangoSetting
# .. toggle_description: Enables the use of the extracted LTI XBlock, which has been shifted to the 'openedx/xblocks-contrib' repo.
# .. toggle_use_cases: temporary
# .. toggle_warning: Not production-ready until relevant subtask https://github.com/openedx/edx-platform/issues/34827 is done.
# .. toggle_creation_date: 2024-11-10
# .. toggle_target_removal_date: 2025-06-01
USE_EXTRACTED_LTI_BLOCK = False

# .. toggle_name: USE_EXTRACTED_HTML_BLOCK
# .. toggle_default: False
# .. toggle_implementation: DjangoSetting
# .. toggle_description: Enables the use of the extracted HTML XBlock, which has been shifted to the 'openedx/xblocks-contrib' repo.
# .. toggle_use_cases: temporary
# .. toggle_warning: Not production-ready until relevant subtask https://github.com/openedx/edx-platform/issues/34827 is done.
# .. toggle_creation_date: 2024-11-10
# .. toggle_target_removal_date: 2025-06-01
USE_EXTRACTED_HTML_BLOCK = False

# .. toggle_name: USE_EXTRACTED_DISCUSSION_BLOCK
# .. toggle_default: False
# .. toggle_implementation: DjangoSetting
# .. toggle_description: Enables the use of the extracted Discussion XBlock, which has been shifted to the 'openedx/xblocks-contrib' repo.
# .. toggle_use_cases: temporary
# .. toggle_warning: Not production-ready until relevant subtask https://github.com/openedx/edx-platform/issues/34827 is done.
# .. toggle_creation_date: 2024-11-10
# .. toggle_target_removal_date: 2025-06-01
USE_EXTRACTED_DISCUSSION_BLOCK = False

# .. toggle_name: USE_EXTRACTED_PROBLEM_BLOCK
# .. toggle_default: False
# .. toggle_implementation: DjangoSetting
# .. toggle_description: Enables the use of the extracted Problem XBlock, which has been shifted to the 'openedx/xblocks-contrib' repo.
# .. toggle_use_cases: temporary
# .. toggle_warning: Not production-ready until relevant subtask https://github.com/openedx/edx-platform/issues/34827 is done.
# .. toggle_creation_date: 2024-11-10
# .. toggle_target_removal_date: 2025-06-01
USE_EXTRACTED_PROBLEM_BLOCK = False

# .. toggle_name: USE_EXTRACTED_VIDEO_BLOCK
# .. toggle_default: False
# .. toggle_implementation: DjangoSetting
# .. toggle_description: Enables the use of the extracted Video XBlock, which has been shifted to the 'openedx/xblocks-contrib' repo.
# .. toggle_use_cases: temporary
# .. toggle_warning: Not production-ready until relevant subtask https://github.com/openedx/edx-platform/issues/34827 is done.
# .. toggle_creation_date: 2024-11-10
# .. toggle_target_removal_date: 2025-06-01
USE_EXTRACTED_VIDEO_BLOCK = False
