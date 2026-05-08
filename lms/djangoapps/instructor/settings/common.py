"""Common environment variables unique to the instructor plugin."""


from django.utils.translation import gettext_lazy as _


def plugin_settings(settings):
    """Settings for the instructor plugin."""
    ### Analytics Dashboard (Insights) settings
    settings.ANALYTICS_DASHBOARD_URL = ""
    settings.ANALYTICS_DASHBOARD_NAME = _('Your Platform Insights')
    # .. toggle_name: FEATURES['DISPLAY_ANALYTICS_ENROLLMENTS']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: Enable display of enrollment counts in instructor dashboard and
    #   analytics section.
    # .. toggle_use_cases: opt_out
    # .. toggle_creation_date: 2014-11-12
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/5838
    settings.DISPLAY_ANALYTICS_ENROLLMENTS = True

    # .. toggle_name: FEATURES['ENABLE_CCX_ANALYTICS_DASHBOARD_URL']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Display the 'Analytics' tab in the instructor dashboard for CCX courses.
    #   Note: This has no effect unless ANALYTICS_DASHBOARD_URL is already set, because without that
    #   setting, the tab does not show up for any courses.
    # .. toggle_use_cases: opt_in
    # .. toggle_creation_date: 2016-10-07
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/13196
    settings.ENABLE_CCX_ANALYTICS_DASHBOARD_URL = False

    # .. setting_name: FEATURES['MAX_ENROLLMENT_INSTR_BUTTONS']
    # .. setting_default: 200
    # .. setting_description: Disable instructor dashboard buttons for downloading course data
    #   when enrollment exceeds this number. The number indicates the maximum allowed enrollments
    #   for the course to be considered "small". Courses exceeding the upper limit of "small"
    #   courses will have disabled buttons at the instructor dashboard.
    settings.MAX_ENROLLMENT_INSTR_BUTTONS = 200

    # .. toggle_name: FEATURES['ENABLE_GRADE_DOWNLOADS']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Enable grade CSV downloads from the instructor dashboard. Grade
    #   calculation started from the instructor dashboard will write grades CSV files to the
    #   configured storage backend and give links for downloads.
    # .. toggle_use_cases: opt_in
    # .. toggle_creation_date: 2016-07-06
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/11286
    settings.ENABLE_GRADE_DOWNLOADS = False

    # .. toggle_name: FEATURES['ALLOW_COURSE_STAFF_GRADE_DOWNLOADS']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Enable to give course staff unrestricted access to grade downloads;
    #   if set to False, only edX superusers can perform the downloads.
    # .. toggle_use_cases: opt_in
    # .. toggle_creation_date: 2018-03-26
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/1750
    settings.ALLOW_COURSE_STAFF_GRADE_DOWNLOADS = False

    # .. toggle_name: FEATURES['ALLOW_AUTOMATED_SIGNUPS']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Enable to show a section in the membership tab of the instructor
    #   dashboard to allow an upload of a CSV file that contains a list of new accounts to create
    #   and register for course.
    # .. toggle_use_cases: opt_in
    # .. toggle_creation_date: 2014-10-21
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/5670
    settings.ALLOW_AUTOMATED_SIGNUPS = False

    # .. toggle_name: FEATURES['ENABLE_AUTOMATED_SIGNUPS_EXTRA_FIELDS']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: When True, the CSV file that contains a list of
    #   new accounts to create and register for a course in the membership
    #   tab of the instructor dashboard will accept the cohort name to
    #   assign the new user and the enrollment course mode.
    # .. toggle_use_cases: open_edx
    # .. toggle_creation_date: 2021-10-26
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/21260
    settings.ENABLE_AUTOMATED_SIGNUPS_EXTRA_FIELDS = False

    # .. toggle_name: FEATURES['CERTIFICATES_INSTRUCTOR_GENERATION']  # pylint: disable=annotation-missing-token
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Enable to allow batch generation of certificates from the instructor dashboard.
    #   In case of self-paced courses, the certificate generation button is hidden if certificate
    #   generation is not explicitly enabled globally or for the specific course.
    # .. toggle_use_cases: opt_in
    settings.CERTIFICATES_INSTRUCTOR_GENERATION = False

    # .. toggle_name: FEATURES['ENABLE_CERTIFICATES_INSTRUCTOR_MANAGE]  # pylint: disable=annotation-missing-token
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: False
    # .. toggle_description: Allow course instructors to manage certificates from the instructor dashboard.
    # .. toggle_use_cases: opt_in
    settings.ENABLE_CERTIFICATES_INSTRUCTOR_MANAGE = False

    # .. toggle_name: FEATURES['BATCH_ENROLLMENT_NOTIFY_USERS_DEFAULT']
    # .. toggle_implementation: DjangoSetting
    # .. toggle_default: True
    # .. toggle_description: Controls if the "Notify users by email" checkbox in the batch
    #   enrollment form on the instructor dashboard is already checked on page load or not.
    # .. toggle_use_cases: opt_out
    # .. toggle_creation_date: 2017-07-05
    # .. toggle_tickets: https://github.com/openedx/edx-platform/pull/15392
    settings.BATCH_ENROLLMENT_NOTIFY_USERS_DEFAULT = True
