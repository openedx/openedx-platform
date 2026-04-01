"""
URLs for LMS
"""

from config_models.views import ConfigurationModelCurrentAPIView
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.admin import autodiscover as django_autodiscover
from django.urls import include, path, re_path
from django.utils.translation import gettext_lazy as _
from django.views.generic.base import RedirectView
from edx_api_doc_tools import make_docs_urls
from edx_django_utils.plugins import get_plugin_url_patterns
from submissions import urls as submissions_urls

from common.djangoapps.student import views as student_views
from common.djangoapps.util import views as util_views
from lms.djangoapps.branding import views as branding_views
from lms.djangoapps.courseware.masquerade import MasqueradeView
from lms.djangoapps.courseware.block_render import (
    handle_xblock_callback,
    handle_xblock_callback_noauth,
    xblock_view,
    xqueue_callback
)
from lms.djangoapps.courseware.views import views as courseware_views
from lms.djangoapps.courseware.views.index import CoursewareIndex
from lms.djangoapps.courseware.views.views import CourseTabView, EnrollStaffView, StaticCourseTabView
from lms.djangoapps.debug import views as debug_views
from lms.djangoapps.discussion import views as discussion_views
from lms.djangoapps.discussion.config.settings import is_forum_daily_digest_enabled
from lms.djangoapps.discussion.notification_prefs import views as notification_prefs_views
from lms.djangoapps.instructor.views import instructor_dashboard as instructor_dashboard_views
from lms.djangoapps.instructor_task import views as instructor_task_views
from lms.djangoapps.static_template_view import views as static_template_view_views
from lms.djangoapps.staticbook import views as staticbook_views
from openedx.core.apidocs import api_info
from openedx.core.djangoapps.auth_exchange.views import LoginWithAccessTokenView
from openedx.core.djangoapps.catalog.models import CatalogIntegration
from openedx.core.djangoapps.common_views.xblock import xblock_resource
from openedx.core.djangoapps.cors_csrf import views as cors_csrf_views
from openedx.core.djangoapps.course_groups import views as course_groups_views
from openedx.core.djangoapps.debug import views as openedx_debug_views
from openedx.core.djangoapps.lang_pref import views as lang_pref_views
from openedx.core.djangoapps.password_policy import compliance as password_policy_compliance
from openedx.core.djangoapps.password_policy.forms import PasswordPolicyAwareAdminAuthForm
from openedx.core.djangoapps.plugins.constants import ProjectType
from openedx.core.djangoapps.programs.models import ProgramsApiConfig
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_authn.views.login import redirect_to_lms_login
from openedx.features.enterprise_support.api import enterprise_enabled

RESET_COURSE_DEADLINES_NAME = 'reset_course_deadlines'
RENDER_XBLOCK_NAME = 'render_xblock'
RENDER_VIDEO_XBLOCK_NAME = 'render_public_video_xblock'
RENDER_VIDEO_XBLOCK_EMBED_NAME = 'render_public_video_xblock_embed'
COURSE_PROGRESS_NAME = 'progress'

if settings.DEBUG or settings.FEATURES.get('ENABLE_DJANGO_ADMIN_SITE'):
    django_autodiscover()
    admin.site.site_header = _('LMS Administration')
    admin.site.site_title = admin.site.site_header

    if password_policy_compliance.should_enforce_compliance_on_login():
        admin.site.login_form = PasswordPolicyAwareAdminAuthForm

# Custom error pages
# These are used by Django to render these error codes. Do not remove.
# pylint: disable=invalid-name
handler403 = static_template_view_views.render_403
handler404 = static_template_view_views.render_404
handler429 = static_template_view_views.render_429
handler500 = static_template_view_views.render_500

notification_prefs_urls = [
    path('notification_prefs/enable/', notification_prefs_views.ajax_enable),
    path('notification_prefs/disable/', notification_prefs_views.ajax_disable),
    path('notification_prefs/status/', notification_prefs_views.ajax_status),

    re_path(
        r'^notification_prefs/unsubscribe/(?P<token>[a-zA-Z0-9-_=]+)/',
        notification_prefs_views.set_subscription,
        {'subscribe': False},
        name='unsubscribe_forum_update',
    ),
    re_path(
        r'^notification_prefs/resubscribe/(?P<token>[a-zA-Z0-9-_=]+)/',
        notification_prefs_views.set_subscription,
        {'subscribe': True},
        name='resubscribe_forum_update',
    ),
]


urlpatterns = [
    path('', branding_views.index, name='root'),  # Main marketing page, or redirect to courseware

    path('', include('common.djangoapps.student.urls')),
    # TODO: Move lms specific student views out of common code
    re_path(r'^dashboard/?$', student_views.student_dashboard, name='dashboard'),
    path('change_enrollment', student_views.change_enrollment, name='change_enrollment'),

    # Event tracking endpoints
    path('', include('common.djangoapps.track.urls')),

    # Static template view endpoints like blog, faq, etc.
    path('', include('lms.djangoapps.static_template_view.urls')),

    path('heartbeat', include('openedx.core.djangoapps.heartbeat.urls')),

    path('i18n/', include('django.conf.urls.i18n')),

    # Course assets
    path('', include('openedx.core.djangoapps.contentserver.urls')),

    # Enrollment API RESTful endpoints
    path('api/enrollment/v1/', include('openedx.core.djangoapps.enrollments.urls')),

    # Agreements API RESTful endpoints
    path('api/agreements/v1/', include('openedx.core.djangoapps.agreements.urls')),

    # Entitlement API RESTful endpoints
    path(
        'api/entitlements/',
        include(
            ('common.djangoapps.entitlements.rest_api.urls', 'common.djangoapps.entitlements'),
            namespace='entitlements_api',
        ),
    ),

    # Courseware search endpoints
    path('search/', include('search.urls')),

    # Course API
    path('api/courses/', include('lms.djangoapps.course_api.urls')),

    # User API endpoints
    path('api/user/', include('openedx.core.djangoapps.user_api.urls')),
    # Note: these are older versions of the User API that will eventually be
    # subsumed by api/user listed above.
    path('', include('openedx.core.djangoapps.user_api.legacy_urls')),

    # Profile Images API endpoints
    path('api/profile_images/', include('openedx.core.djangoapps.profile_images.urls')),

    # Video Abstraction Layer used to allow video teams to manage video assets
    # independently of courseware. https://github.com/openedx/edx-val
    path('api/val/v0/', include('edxval.urls')),

    path(
        'api/commerce/',
        include(
            ('lms.djangoapps.commerce.api.urls', 'lms.djangoapps.commerce'),
            namespace='commerce_api',
        ),
    ),
    path('api/credit/', include('openedx.core.djangoapps.credit.urls')),
    path('api/toggles/', include('openedx.core.djangoapps.waffle_utils.urls')),
    path('rss_proxy/', include('lms.djangoapps.rss_proxy.urls')),
    path('api/organizations/', include('organizations.urls', namespace='organizations')),

    path('catalog/', include(('openedx.core.djangoapps.catalog.urls', 'openedx.core.djangoapps.catalog'),
                             namespace='catalog')),

    # Update session view
    path('lang_pref/update_language', lang_pref_views.update_language, name='update_language'),

    # Multiple course modes and identity verification
    path(
        'course_modes/',
        include('common.djangoapps.course_modes.urls'),
    ),
    path(
        'api/course_modes/',
        include(
            ('common.djangoapps.course_modes.rest_api.urls', 'common.djangoapps.course_mods'),
            namespace='course_modes_api',
        )
    ),

    path('verify_student/', include('lms.djangoapps.verify_student.urls')),

    # URLs for managing dark launches of languages
    path('update_lang/', include(('openedx.core.djangoapps.dark_lang.urls', 'openedx.core.djangoapps.dark_lang'),
                                 namespace='dark_lang')),

    # For redirecting to help pages.
    path('help_token/', include('help_tokens.urls')),

    # URLs for API access management
    path('api-admin/', include(('openedx.core.djangoapps.api_admin.urls', 'openedx.core.djangoapps.api_admin'),
                               namespace='api_admin')),

    # Learner Home and Program Dashboard
    path('api/learner_home/', include('lms.djangoapps.learner_home.urls', namespace='learner_home')),
    path('dashboard/', include('lms.djangoapps.learner_dashboard.urls')),
    # This is the legacy URL for the program dashboard API when the legacy learner dashboard existed.
    # Current-and-future advertised URLs for this API will be under 'api/learner_home'
    path('api/dashboard/', include('openedx.core.djangoapps.programs.rest_api.urls', namespace='dashboard_api')),

    path(
        'api/experiments/',
        include(
            ('lms.djangoapps.experiments.urls', 'lms.djangoapps.experiments'),
            namespace='api_experiments',
        ),
    ),
    path('api/discounts/', include(('openedx.features.discounts.urls', 'openedx.features.discounts'),
                                   namespace='api_discounts')),

    # Provide URLs where we can see the rendered error pages without having to force an error.
    path('403', handler403, name='render_403'),
    path('404', handler404, name='render_404'),
    path('429', handler429, name='render_429'),
    path('500', handler500, name='render_500'),
]

if settings.FEATURES.get('ENABLE_MOBILE_REST_API'):
    urlpatterns += [
        re_path(r'^api/mobile/(?P<api_version>v(4|3|2|1|0.5))/', include('lms.djangoapps.mobile_api.urls')),
    ]

urlpatterns += [
    path('openassessment/fileupload/', include('openassessment.fileupload.urls')),
]

urlpatterns += [
    path('support/', include('lms.djangoapps.support.urls')),
    # Support API RESTful endpoints
    path(
        'api/support/',
        include(
            ('lms.djangoapps.support.rest_api.urls', 'lms.djangoapps.support'),
            namespace='support_api',
        )
    ),
]

# Favicon
favicon_path = configuration_helpers.get_value('favicon_path', settings.FAVICON_PATH)  # pylint: disable=invalid-name
urlpatterns += [
    re_path(r'^favicon\.ico$', RedirectView.as_view(url=settings.STATIC_URL + favicon_path, permanent=True)),
]

# Multicourse wiki (Note: wiki urls must be above the courseware ones because of
# the custom tab catch-all)
if settings.WIKI_ENABLED:
    from django_notify.urls import get_pattern as notify_pattern
    from wiki.urls import get_pattern as wiki_pattern

    from lms.djangoapps.course_wiki import views as course_wiki_views

    wiki_url_patterns, wiki_app_name = wiki_pattern()
    notify_url_patterns, notify_app_name = notify_pattern()

    urlpatterns += [
        # First we include views from course_wiki that we use to override the default views.
        # They come first in the urlpatterns so they get resolved first
        path('wiki/create-root/', course_wiki_views.root_create, name='root_create'),
        path('wiki/', include((wiki_url_patterns, wiki_app_name), namespace='wiki')),
        path('notify/', include((notify_url_patterns, notify_app_name), namespace='notify')),

        # These urls are for viewing the wiki in the context of a course. They should
        # never be returned by a reverse() so they come after the other url patterns
        re_path(fr'^courses/{settings.COURSE_ID_PATTERN}/course_wiki/?$',
                course_wiki_views.course_wiki_redirect, name='course_wiki'),
        re_path(fr'^courses/{settings.COURSE_KEY_REGEX}/wiki/',
                include((wiki_url_patterns, 'course_wiki_do_not_reverse'), namespace='course_wiki_do_not_reverse')),
    ]

urlpatterns += [
    # jump_to URLs for direct access to a location in the course
    re_path(
        r'^courses/{}/jump_to/(?P<location>.*)$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        courseware_views.jump_to,
        name='jump_to',
    ),
    re_path(
        r'^courses/{}/jump_to_id/(?P<module_id>.*)$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        courseware_views.jump_to_id,
        name='jump_to_id',
    ),

    # xblock Handler APIs
    re_path(
        r'^courses/{course_key}/xblock/{usage_key}/handler/(?P<handler>[^/]*)(?:/(?P<suffix>.*))?$'.format(
            course_key=settings.COURSE_ID_PATTERN,
            usage_key=settings.USAGE_ID_PATTERN,
        ),
        handle_xblock_callback,
        name='xblock_handler',
    ),
    re_path(
        r'^courses/{course_key}/xblock/{usage_key}/handler_noauth/(?P<handler>[^/]*)(?:/(?P<suffix>.*))?$'.format(
            course_key=settings.COURSE_ID_PATTERN,
            usage_key=settings.USAGE_ID_PATTERN,
        ),
        handle_xblock_callback_noauth,
        name='xblock_handler_noauth',
    ),

    # xblock View API
    # (unpublished) API that returns JSON with the HTML fragment and related resources
    # for the xBlock's requested view.
    re_path(
        r'^courses/{course_key}/xblock/{usage_key}/view/(?P<view_name>[^/]*)$'.format(
            course_key=settings.COURSE_ID_PATTERN,
            usage_key=settings.USAGE_ID_PATTERN,
        ),
        xblock_view,
        name='xblock_view',
    ),

    # xblock Rendering View URL
    # URL to provide an HTML view of an xBlock. The view type (e.g., student_view) is
    # passed as a 'view' parameter to the URL.
    # Note: This is not an API. Compare this with the xblock_view API above.
    re_path(
        fr'^xblock/{settings.USAGE_KEY_PATTERN}$',
        courseware_views.render_xblock,
        name=RENDER_XBLOCK_NAME,
    ),
    re_path(
        fr'^videos/embed/{settings.USAGE_KEY_PATTERN}$',
        courseware_views.PublicVideoXBlockEmbedView.as_view(),
        name=RENDER_VIDEO_XBLOCK_EMBED_NAME,
    ),
    re_path(
        fr'^videos/{settings.USAGE_KEY_PATTERN}$',
        courseware_views.PublicVideoXBlockView.as_view(),
        name=RENDER_VIDEO_XBLOCK_NAME,
    ),


    # xblock Resource URL
    re_path(
        r'xblock/resource/(?P<block_type>[^/]+)/(?P<uri>.*)$',
        xblock_resource,
        name='xblock_resource_url',
    ),

    # New XBlock REST API, based on the openedx_content API
    path('', include(('openedx.core.djangoapps.xblock.rest_api.urls', 'openedx.core.djangoapps.xblock'),
                     namespace='xblock_api')),

    re_path(
        r'^courses/{}/xqueue/(?P<userid>[^/]*)/(?P<mod_id>.*?)/(?P<dispatch>[^/]*)$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        xqueue_callback,
        name='xqueue_callback',
    ),

    re_path(
        r'^courses/{}/xqueue/(?P<userid>[^/]*)/(?P<mod_id>.*?)/(?P<dispatch>[^/]*)$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        xqueue_callback,
        name='callback_submission',
    ),

    # TODO: These views need to be updated before they work
    path('calculate', util_views.calculate),

    path(
        'reset_deadlines',
        util_views.reset_course_deadlines,
        name=RESET_COURSE_DEADLINES_NAME,
    ),

    re_path(r'^courses/?$', branding_views.courses, name='courses'),

    # About the course
    re_path(
        r'^courses/{}/about$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        courseware_views.course_about,
        name='about_course',
    ),
    path(
        'courses/yt_video_metadata',
        courseware_views.yt_video_metadata,
        name='yt_video_metadata',
    ),
    re_path(
        r'^courses/{}/enroll_staff$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        EnrollStaffView.as_view(),
        name='enroll_staff',
    ),

    # Inside the course
    re_path(
        r'^courses/{}/$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        courseware_views.course_about,
        name='course_root',
    ),
    # TODO arjun remove when custom tabs in place, see courseware/courses.py
    re_path(
        r'^courses/{}/syllabus$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        courseware_views.syllabus,
        name='syllabus',
    ),

    # Survey associated with a course
    re_path(
        r'^courses/{}/survey$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        courseware_views.course_survey,
        name='course_survey',
    ),

    re_path(
        r'^courses/{}/book/(?P<book_index>\d+)/$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        staticbook_views.index,
        name='book',
    ),
    re_path(
        r'^courses/{}/book/(?P<book_index>\d+)/(?P<page>\d+)$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        staticbook_views.index,
        name='book',
    ),

    re_path(
        r'^courses/{}/pdfbook/(?P<book_index>\d+)/$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        staticbook_views.pdf_index,
        name='pdf_book',
    ),
    re_path(
        r'^courses/{}/pdfbook/(?P<book_index>\d+)/(?P<page>\d+)$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        staticbook_views.pdf_index,
        name='pdf_book',
    ),

    re_path(
        r'^courses/{}/pdfbook/(?P<book_index>\d+)/chapter/(?P<chapter>\d+)/$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        staticbook_views.pdf_index,
        name='pdf_book',
    ),
    re_path(
        r'^courses/{}/pdfbook/(?P<book_index>\d+)/chapter/(?P<chapter>\d+)/(?P<page>\d+)$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        staticbook_views.pdf_index,
        name='pdf_book',
    ),

    re_path(
        r'^courses/{}/htmlbook/(?P<book_index>\d+)/$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        staticbook_views.html_index,
        name='html_book',
    ),
    re_path(
        r'^courses/{}/htmlbook/(?P<book_index>\d+)/chapter/(?P<chapter>\d+)/$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        staticbook_views.html_index,
        name='html_book',
    ),

    re_path(
        r'^courses/{}/courseware/?$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        CoursewareIndex.as_view(),
        name='courseware',
    ),
    re_path(
        r'^courses/{}/courseware/(?P<section>[^/]*)/$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        CoursewareIndex.as_view(),
        name='courseware_section',
    ),
    re_path(
        r'^courses/{}/courseware/(?P<section>[^/]*)/(?P<subsection>[^/]*)/$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        CoursewareIndex.as_view(),
        name='courseware_subsection',
    ),
    re_path(
        r'^courses/{}/courseware/(?P<section>[^/]*)/(?P<subsection>[^/]*)/(?P<position>[^/]*)/?$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        CoursewareIndex.as_view(),
        name='courseware_position',
    ),

    # progress page
    re_path(
        r'^courses/{}/progress$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        courseware_views.progress,
        name=COURSE_PROGRESS_NAME,
    ),

    # dates page (no longer functional, just redirects to MFE)
    re_path(r'^courses/{}/dates'.format(settings.COURSE_ID_PATTERN), courseware_views.dates, name='dates'),

    # Takes optional student_id for instructor use--shows profile as that student sees it.
    re_path(
        r'^courses/{}/progress/(?P<student_id>[^/]*)/$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        courseware_views.progress,
        name='student_progress',
    ),

    re_path(
        r'^programs/{}/about'.format(
            r'(?P<program_uuid>[0-9a-f-]+)',
        ),
        courseware_views.program_marketing,
        name='program_marketing_view',
    ),

    # For the instructor
    re_path(
        r'^courses/{}/instructor$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        instructor_dashboard_views.instructor_dashboard_2,
        name='instructor_dashboard',
    ),

    re_path(
        r'^courses/{}/set_course_mode_price$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        instructor_dashboard_views.set_course_mode_price,
        name='set_course_mode_price',
    ),

    # Discussions Management
    re_path(
        r'^courses/{}/discussions/settings$'.format(
            settings.COURSE_KEY_PATTERN,
        ),
        discussion_views.course_discussions_settings_handler,
        name='course_discussions_settings',
    ),

    # Cohorts management API
    path('api/cohorts/', include(
        ('openedx.core.djangoapps.course_groups.urls', 'openedx.core.djangoapps.course_groups'),
        namespace='api_cohorts')),

    # Cohorts management
    re_path(
        r'^courses/{}/cohorts/settings$'.format(
            settings.COURSE_KEY_PATTERN,
        ),
        course_groups_views.course_cohort_settings_handler,
        name='course_cohort_settings',
    ),
    re_path(
        r'^courses/{}/cohorts/(?P<cohort_id>[0-9]+)?$'.format(
            settings.COURSE_KEY_PATTERN,
        ),
        course_groups_views.cohort_handler,
        name='cohorts',
    ),
    re_path(
        r'^courses/{}/cohorts/(?P<cohort_id>[0-9]+)$'.format(
            settings.COURSE_KEY_PATTERN,
        ),
        course_groups_views.users_in_cohort,
        name='list_cohort',
    ),
    re_path(
        r'^courses/{}/cohorts/(?P<cohort_id>[0-9]+)/add$'.format(
            settings.COURSE_KEY_PATTERN,
        ),
        course_groups_views.add_users_to_cohort,
        name='add_to_cohort',
    ),
    re_path(
        r'^courses/{}/cohorts/(?P<cohort_id>[0-9]+)/delete$'.format(
            settings.COURSE_KEY_PATTERN,
        ),
        course_groups_views.remove_user_from_cohort,
        name='remove_from_cohort',
    ),
    re_path(
        r'^courses/{}/cohorts/debug$'.format(
            settings.COURSE_KEY_PATTERN,
        ),
        course_groups_views.debug_cohort_mgmt,
        name='debug_cohort_mgmt',
    ),
    re_path(
        r'^courses/{}/discussion/topics$'.format(
            settings.COURSE_KEY_PATTERN,
        ),
        discussion_views.discussion_topics,
        name='discussion_topics',
    ),

    # LTI endpoints listing
    re_path(
        r'^courses/{}/lti_rest_endpoints/'.format(
            settings.COURSE_ID_PATTERN,
        ),
        courseware_views.get_course_lti_endpoints,
        name='lti_rest_endpoints',
    ),

    # Student Notes
    re_path(
        r'^courses/{}/edxnotes/'.format(
            settings.COURSE_ID_PATTERN,
        ),
        include('lms.djangoapps.edxnotes.urls'),
        name='edxnotes_endpoints',
    ),

    # Student Notes API
    path(
        'api/edxnotes/v1/',
        include('lms.djangoapps.edxnotes.api_urls'),
    ),

    # Branding API
    path(
        'api/branding/v1/',
        include('lms.djangoapps.branding.api_urls')
    ),

    # Course experience
    re_path(
        r'^courses/{}/course/'.format(
            settings.COURSE_ID_PATTERN,
        ),
        include('openedx.features.course_experience.urls'),
    ),

    # Course bookmarks UI in LMS
    re_path(
        r'^courses/{}/bookmarks/'.format(
            settings.COURSE_ID_PATTERN,
        ),
        include('openedx.features.course_bookmarks.urls'),
    ),

    # Calendar Sync UI in LMS
    re_path(
        fr'^courses/{settings.COURSE_ID_PATTERN}/',
        include('openedx.features.calendar_sync.urls'),
    ),

    # Survey Report
    re_path(
        fr'^survey_report/',
        include('openedx.features.survey_report.urls'),
    ),
]

if settings.FEATURES.get('ENABLE_TEAMS'):
    # Teams endpoints
    urlpatterns += [
        path(
            'api/team/',
            include('lms.djangoapps.teams.api_urls')
        ),
        re_path(
            r'^courses/{}/teams/'.format(
                settings.COURSE_ID_PATTERN,
            ),
            include('lms.djangoapps.teams.urls'),
            name='teams_endpoints',
        ),
    ]

# allow course staff to change to student view of courseware
if settings.FEATURES.get('ENABLE_MASQUERADE'):
    urlpatterns += [
        re_path(
            r'^courses/{}/masquerade$'.format(
                settings.COURSE_KEY_PATTERN,
            ),
            MasqueradeView.as_view(),
            name='masquerade_update',
        ),
    ]

urlpatterns += [
    re_path(
        r'^courses/{}/generate_user_cert'.format(
            settings.COURSE_ID_PATTERN,
        ),
        courseware_views.generate_user_cert,
        name='generate_user_cert',
    ),
]

# discussion forums live within courseware, so courseware must be enabled first
if settings.FEATURES.get('ENABLE_DISCUSSION_SERVICE'):
    urlpatterns += [
        path(
            'api/discussion/',
            include('lms.djangoapps.discussion.rest_api.urls')
        ),
        re_path(
            r'^courses/{}/discussion/'.format(
                settings.COURSE_ID_PATTERN,
            ),
            include('lms.djangoapps.discussion.django_comment_client.urls')
        ),
    ]

if is_forum_daily_digest_enabled():
    urlpatterns += notification_prefs_urls

urlpatterns += [
    path('bulk_email/', include('lms.djangoapps.bulk_email.urls')),
]

urlpatterns += [
    re_path(
        r'^courses/{}/tab/(?P<tab_type>[^/]+)/$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        CourseTabView.as_view(),
        name='course_tab_view',
    ),
]

urlpatterns += [
    re_path(
        r'^courses/{}/courseware-search/enabled/$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        courseware_views.courseware_mfe_search_enabled,
        name='courseware_search_enabled_view',
    ),
    re_path(
        fr'^courses/{settings.COURSE_ID_PATTERN}/courseware-navigation-sidebar/toggles/$',
        courseware_views.courseware_mfe_navigation_sidebar_toggles,
        name='courseware_navigation_sidebar_toggles_view',
    ),
]

urlpatterns += [
    re_path(
        r'^courses/{}/lti_tab/(?P<provider_uuid>[^/]+)/$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        CourseTabView.as_view(),
        name='lti_course_tab',
        kwargs={'tab_type': 'lti_tab'},
    ),
]

urlpatterns += [
    # This MUST be the last view in the courseware--it's a catch-all for custom tabs.
    re_path(
        r'^courses/{}/(?P<tab_slug>[^/]+)/$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        StaticCourseTabView.as_view(),
        name='static_tab',
    ),
]

if settings.FEATURES.get('ENABLE_STUDENT_HISTORY_VIEW'):
    urlpatterns += [
        re_path(
            r'^courses/{}/submission_history/(?P<learner_identifier>[^/]*)/(?P<location>.*?)$'.format(
                settings.COURSE_ID_PATTERN
            ),
            courseware_views.submission_history,
            name='submission_history',
        ),
    ]

if settings.DEBUG or settings.FEATURES.get('ENABLE_DJANGO_ADMIN_SITE'):
    # Jasmine and admin

    # The password pages in the admin tool are disabled so that all password
    # changes go through our user portal and follow complexity requirements.
    # The form to change another user's password is conditionally enabled
    # for backwards compatibility.
    if not settings.FEATURES.get('ENABLE_CHANGE_USER_PASSWORD_ADMIN'):
        urlpatterns += [
            re_path(r'^admin/auth/user/\d+/password/$', handler404),
        ]
    urlpatterns += [
        path('admin/password_change/', handler404),
        # We are enforcing users to login through third party auth in site's
        # login page so we are disabling the admin panel's login page.
        path('admin/login/', redirect_to_lms_login),
        path('admin/', admin.site.urls),
    ]

if configuration_helpers.get_value('ENABLE_BULK_ENROLLMENT_VIEW', settings.FEATURES.get('ENABLE_BULK_ENROLLMENT_VIEW')):
    urlpatterns += [
        path('api/bulk_enroll/v1/', include('lms.djangoapps.bulk_enroll.urls')),
    ]

# Embargo
if settings.FEATURES.get('EMBARGO'):
    urlpatterns += [
        path('embargo/', include(('openedx.core.djangoapps.embargo.urls', 'openedx.core.djangoapps.embargo'),
                                 namespace='embargo')),
        path('api/embargo/', include(('openedx.core.djangoapps.embargo.urls', 'openedx.core.djangoapps.embargo'),
                                     namespace='api_embargo')),
    ]

# Survey Djangoapp
urlpatterns += [
    path('survey/', include('lms.djangoapps.survey.urls')),
]

urlpatterns += [
    # These URLs dispatch to django-oauth-toolkit or django-oauth2-provider as appropriate.
    # Developers should use these routes, to maintain compatibility for existing client code
    path('oauth2/', include('openedx.core.djangoapps.oauth_dispatch.urls')),
    # The /_o/ prefix exists to provide a target for code in django-oauth-toolkit that
    # uses reverse() with the 'oauth2_provider' namespace.  Developers should not access these
    # views directly, but should rather use the wrapped views at /oauth2/
    path('_o/', include('oauth2_provider.urls', namespace='oauth2_provider')),
]

if settings.FEATURES.get('ENABLE_SERVICE_STATUS'):
    urlpatterns += [
        path('status/', include('openedx.core.djangoapps.service_status.urls')),
    ]

if settings.FEATURES.get('ENABLE_INSTRUCTOR_BACKGROUND_TASKS'):
    urlpatterns += [
        path(
            'instructor_task_status/',
            instructor_task_views.instructor_task_status,
            name='instructor_task_status'
        ),
    ]

if settings.FEATURES.get('ENABLE_DEBUG_RUN_PYTHON'):
    urlpatterns += [
        path('debug/run_python', debug_views.run_python),
    ]

urlpatterns += [
    path('debug/show_parameters', debug_views.show_parameters),
]

# Third-party auth.
if settings.FEATURES.get('ENABLE_THIRD_PARTY_AUTH'):
    urlpatterns += [
        path('', include('common.djangoapps.third_party_auth.urls')),
        path('api/third_party_auth/', include('common.djangoapps.third_party_auth.api.urls')),
    ]

# Enterprise
if enterprise_enabled():
    urlpatterns += [
        path('', include('enterprise.urls')),
        path('', include('channel_integrations.urls')),
    ]

# OAuth token exchange
urlpatterns += [
    path(
        'oauth2/login/',
        LoginWithAccessTokenView.as_view(),
        name='login_with_access_token'
    ),
]

# Certificates
urlpatterns += [
    path('certificates/', include('lms.djangoapps.certificates.urls')),

    # REST APIs
    path('api/certificates/', include(('lms.djangoapps.certificates.apis.urls', 'lms.djangoapps.certificates'),
                                      namespace='certificates_api')),
]

# XDomain proxy
urlpatterns += [
    re_path(r'^xdomain_proxy.html$', cors_csrf_views.xdomain_proxy, name='xdomain_proxy'),
]

# Custom courses on edX (CCX) URLs
if settings.FEATURES.get('CUSTOM_COURSES_EDX'):
    urlpatterns += [
        re_path(fr'^courses/{settings.COURSE_ID_PATTERN}/', include('lms.djangoapps.ccx.urls')),
        path('api/ccx/', include(('lms.djangoapps.ccx.api.urls', 'lms.djangoapps.ccx'), namespace='ccx_api')),
    ]

# Access to courseware as an LTI provider
if settings.FEATURES.get('ENABLE_LTI_PROVIDER'):
    urlpatterns += [
        path('lti_provider/', include('lms.djangoapps.lti_provider.urls')),
    ]

urlpatterns += [
    path('config/programs', ConfigurationModelCurrentAPIView.as_view(model=ProgramsApiConfig)),
    path('config/catalog', ConfigurationModelCurrentAPIView.as_view(model=CatalogIntegration)),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    # profile image urls must come before the media url to work
    urlpatterns += static(
        settings.PROFILE_IMAGE_BACKEND['options']['base_url'],
        document_root=settings.PROFILE_IMAGE_BACKEND['options']['location']
    )
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# UX reference templates
urlpatterns += [
    path('template/<path:template>', openedx_debug_views.show_reference_template),
]

if 'debug_toolbar' in settings.INSTALLED_APPS:
    import debug_toolbar

    urlpatterns += [
        path('__debug__/', include(debug_toolbar.urls)),
    ]

if settings.FEATURES.get('ENABLE_FINANCIAL_ASSISTANCE_FORM'):
    urlpatterns += [
        path(
            'financial-assistance/',
            courseware_views.financial_assistance,
            name='financial_assistance'
        ),
        path(
            'financial-assistance/apply/',
            courseware_views.financial_assistance_form,
            name='financial_assistance_form'
        ),
        path(
            'financial-assistance/submit/',
            courseware_views.financial_assistance_request,
            name='submit_financial_assistance_request'
        ),
        path(
            'financial-assistance_v2/submit/',
            courseware_views.financial_assistance_request_v2,
            name='submit_financial_assistance_request_v2'
        ),
        re_path(
            fr'financial-assistance/{settings.COURSE_ID_PATTERN}/apply/',
            courseware_views.financial_assistance_form,
            name='financial_assistance_form_v2'
        ),
        re_path(
            fr'financial-assistance/{settings.COURSE_ID_PATTERN}',
            courseware_views.financial_assistance,
            name='financial_assistance_v2'
        )
    ]

# API docs.
urlpatterns += make_docs_urls(api_info)

# edx-drf-extensions csrf app
urlpatterns += [
    path('', include('csrf.urls')),
]

if 'openedx.testing.coverage_context_listener' in settings.INSTALLED_APPS:
    urlpatterns += [
        path('coverage_context', include('openedx.testing.coverage_context_listener.urls'))
    ]

urlpatterns.append(
    path(
        'api/learning_sequences/',
        include(
            ('openedx.core.djangoapps.content.learning_sequences.urls', 'learning_sequences'),
            namespace='learning_sequences'
        ),
    ),
)

urlpatterns.extend(get_plugin_url_patterns(ProjectType.LMS))

# Course Home API urls
urlpatterns += [
    # This is a BFF ("backend for frontend") djangoapp for the Learning MFE (like courseware_api).
    # It will change and morph as needed for the frontend, and is not a stable API on which other code can rely.
    path('api/course_home/', include(('lms.djangoapps.course_home_api.urls', 'course-home'))),

    # This v1 version is just kept for transitional reasons and is going away as soon as the MFE stops referencing it.
    # We don't promise any sort of versioning stability.
    path('api/course_home/v1/', include(('lms.djangoapps.course_home_api.urls', 'course-home-v1'))),
]

# User Tour API urls
urlpatterns += [
    path('api/user_tours/', include('lms.djangoapps.user_tours.urls')),
]

# Course Experience API urls
urlpatterns += [
    path('api/course_experience/', include('openedx.features.course_experience.api.v1.urls')),
]

# Bulk User Retirement API urls
if settings.FEATURES.get('ENABLE_BULK_USER_RETIREMENT'):
    urlpatterns += [
        path('', include('lms.djangoapps.bulk_user_retirement.urls')),
    ]

# Provider States urls
if getattr(settings, 'PROVIDER_STATES_URL', None):
    from lms.djangoapps.courseware.tests.pacts.views import provider_state as courseware_xblock_handler_provider_state
    urlpatterns += [
        path(
            'courses/xblock/handler/provider_states',
            courseware_xblock_handler_provider_state,
            name='courseware_xblock_handler_provider_state',
        )
    ]

# Enhanced Staff Grader (ESG) URLs
urlpatterns += [
    path('api/ora_staff_grader/', include('lms.djangoapps.ora_staff_grader.urls', 'ora-staff-grader')),
]

# Scheduled Bulk Email (Instructor Task) URLs
urlpatterns += [
    path('api/instructor_task/', include('lms.djangoapps.instructor_task.rest_api.urls')),
]

# MFE API urls
urlpatterns += [
    path('api/mfe_config/v1', include(('lms.djangoapps.mfe_config_api.urls', 'lms.djangoapps.mfe_config_api'), namespace='mfe_config_api'))
]

urlpatterns += [
    path('api/notifications/', include('openedx.core.djangoapps.notifications.urls')),
]

urlpatterns += [
    path('xqueue/', include((submissions_urls, 'submissions'), namespace='submissions')),
]
