"""
Urls of Studio.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib.admin import autodiscover as django_autodiscover
from django.urls import include
from django.urls import path, re_path
from django.utils.translation import gettext_lazy as _
from django.contrib import admin
from django.shortcuts import redirect
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from auth_backends.urls import oauth2_urlpatterns
from edx_api_doc_tools import make_docs_urls

import openedx.core.djangoapps.common_views.xblock
import openedx.core.djangoapps.debug.views
import openedx.core.djangoapps.lang_pref.views
from cms.djangoapps.contentstore import toggles
from cms.djangoapps.contentstore import views as contentstore_views
from cms.djangoapps.contentstore.views.block import xblock_edit_view
from cms.djangoapps.contentstore.views.organization import OrganizationListView
from openedx.core.apidocs import api_info
from openedx.core.djangoapps.password_policy import compliance as password_policy_compliance
from openedx.core.djangoapps.password_policy.forms import PasswordPolicyAwareAdminAuthForm
from openedx.core import toggles as core_toggles


django_autodiscover()
admin.site.site_header = _('Studio Administration')
admin.site.site_title = admin.site.site_header

if password_policy_compliance.should_enforce_compliance_on_login():
    admin.site.login_form = PasswordPolicyAwareAdminAuthForm

# Custom error pages
# These are used by Django to render these error codes. Do not remove.
# pylint: disable=invalid-name
handler404 = contentstore_views.render_404
handler500 = contentstore_views.render_500

# Pattern to match a course key or a library key
COURSELIKE_KEY_PATTERN = r'(?P<course_key_string>({}|{}))'.format(
    r'[^/]+/[^/]+/[^/]+', r'[^/:]+:[^/+]+\+[^/+]+(\+[^/]+)?'
)

# Pattern to match a library key only
LIBRARY_KEY_PATTERN = r'(?P<library_key_string>library-v1:[^/+]+\+[^/+]+)'

# oauth2_urlpatterns needs to be first to override any other login and
# logout related views.
urlpatterns = oauth2_urlpatterns + [
    path('', include('openedx.core.djangoapps.user_authn.urls_common')),
    path('', include('common.djangoapps.student.urls')),
    path('transcripts/upload', contentstore_views.upload_transcripts, name='upload_transcripts'),
    path('transcripts/download', contentstore_views.download_transcripts, name='download_transcripts'),
    path('transcripts/check', contentstore_views.check_transcripts, name='check_transcripts'),
    path('transcripts/choose', contentstore_views.choose_transcripts, name='choose_transcripts'),
    path('transcripts/replace', contentstore_views.replace_transcripts, name='replace_transcripts'),
    path('transcripts/rename', contentstore_views.rename_transcripts, name='rename_transcripts'),
    re_path(r'^preview/xblock/(?P<usage_key_string>.*?)/handler/(?P<handler>[^/]*)(?:/(?P<suffix>.*))?$',
            contentstore_views.preview_handler, name='preview_handler'),
    re_path(r'^xblock/(?P<usage_key_string>.*?)/handler/(?P<handler>[^/]*)(?:/(?P<suffix>.*))?$',
            contentstore_views.component_handler, name='component_handler'),
    re_path(r'^xblock/resource/(?P<block_type>[^/]*)/(?P<uri>.*)$',
            openedx.core.djangoapps.common_views.xblock.xblock_resource, name='xblock_resource_url'),
    path('', include('openedx.core.djangoapps.xblock.rest_api.urls', namespace='xblock_api')),
    path('not_found', contentstore_views.not_found, name='not_found'),
    path('server_error', contentstore_views.server_error, name='server_error'),
    path('organizations', OrganizationListView.as_view(), name='organizations'),
    path('api/toggles/', include('openedx.core.djangoapps.waffle_utils.urls')),

    # noop to squelch ajax errors
    path('event', contentstore_views.event, name='event'),
    path('heartbeat', include('openedx.core.djangoapps.heartbeat.urls')),
    path('i18n/', include('django.conf.urls.i18n')),

    # Course assets
    path('', include('openedx.core.djangoapps.contentserver.urls')),

    # User API endpoints
    path('api/user/', include('openedx.core.djangoapps.user_api.urls')),

    # Update session view
    path('lang_pref/update_language', openedx.core.djangoapps.lang_pref.views.update_language,
         name='update_language'
         ),

    # Darklang View to change the preview language (or dark language)
    path('update_lang/', lambda request: redirect(f'{settings.LMS_ROOT_URL}/update_lang/')),

    # For redirecting to help pages.
    path('help_token/', include('help_tokens.urls')),
    path('api/', include('cms.djangoapps.api.urls', namespace='api')),

    # restful api
    path('', contentstore_views.howitworks, name='homepage'),
    path('howitworks', contentstore_views.howitworks, name='howitworks'),
    path('signin_redirect_to_lms', contentstore_views.login_redirect_to_lms, name='login_redirect_to_lms'),
    path('request_course_creator', contentstore_views.request_course_creator, name='request_course_creator'),
    re_path(fr'^course_team/{COURSELIKE_KEY_PATTERN}(?:/(?P<email>.+))?$',
            contentstore_views.course_team_handler, name='course_team_handler'),
    re_path(fr'^course_info/{settings.COURSE_KEY_PATTERN}$', contentstore_views.course_info_handler,
            name='course_info_handler'),
    re_path(fr'^course_info_update/{settings.COURSE_KEY_PATTERN}/(?P<provided_id>\d+)?$',
            contentstore_views.course_info_update_handler, name='course_info_update_handler'
            ),
    re_path(r'^home/?$', contentstore_views.course_listing, name='home'),
    re_path(r'^home_library/?$', contentstore_views.library_listing, name='home_library'),
    re_path(fr'^course/{settings.COURSE_KEY_PATTERN}/search_reindex?$',
            contentstore_views.course_search_index_handler,
            name='course_search_index_handler'
            ),
    re_path(fr'^course/{settings.COURSE_KEY_PATTERN}?$', contentstore_views.course_handler, name='course_handler'),

    re_path(fr'^checklists/{settings.COURSE_KEY_PATTERN}?$',
            contentstore_views.checklists_handler,
            name='checklists_handler'),

    re_path(fr'^course_notifications/{settings.COURSE_KEY_PATTERN}/(?P<action_state_id>\d+)?$',
            contentstore_views.course_notifications_handler,
            name='course_notifications_handler'),
    re_path(fr'^course_rerun/{settings.COURSE_KEY_PATTERN}$', contentstore_views.course_rerun_handler,
            name='course_rerun_handler'),
    re_path(fr'^container/{settings.USAGE_KEY_PATTERN}$', contentstore_views.container_handler,
            name='container_handler'),
    re_path(fr'^container_embed/{settings.USAGE_KEY_PATTERN}$', contentstore_views.container_embed_handler,
            name='container_embed_handler'),
    re_path(fr'^orphan/{settings.COURSE_KEY_PATTERN}$', contentstore_views.orphan_handler,
            name='orphan_handler'),
    re_path(fr'^assets/{settings.COURSE_KEY_PATTERN}/{settings.ASSET_KEY_PATTERN}?$',
            contentstore_views.assets_handler,
            name='assets_handler'),
    re_path(fr'^assets/{settings.COURSE_KEY_PATTERN}/{settings.ASSET_KEY_PATTERN}/usage',
            contentstore_views.asset_usage_path_handler,
            name='asset_usage_path_handler'),
    re_path(fr'^import/{COURSELIKE_KEY_PATTERN}$', contentstore_views.import_handler,
            name='import_handler'),
    re_path(fr'^import_status/{COURSELIKE_KEY_PATTERN}/(?P<filename>.+)$',
            contentstore_views.import_status_handler, name='import_status_handler'),
    # rest api for course import/export
    path('api/courses/', include('cms.djangoapps.contentstore.api.urls', namespace='courses_api')
         ),
    path('api/modulestore_migrator/',
         include('cms.djangoapps.modulestore_migrator.rest_api.urls', namespace='modulestore_migrator_api')),
    re_path(fr'^export/{COURSELIKE_KEY_PATTERN}$', contentstore_views.export_handler,
            name='export_handler'),
    re_path(fr'^export_output/{COURSELIKE_KEY_PATTERN}$', contentstore_views.export_output_handler,
            name='export_output_handler'),
    re_path(fr'^export_status/{COURSELIKE_KEY_PATTERN}$', contentstore_views.export_status_handler,
            name='export_status_handler'),
    re_path(fr'^xblock/outline/{settings.USAGE_KEY_PATTERN}$', contentstore_views.xblock_outline_handler,
            name='xblock_outline_handler'),
    re_path(fr'^xblock/container/{settings.USAGE_KEY_PATTERN}$', contentstore_views.xblock_container_handler,
            name='xblock_container_handler'),
    re_path(fr'^xblock/{settings.USAGE_KEY_PATTERN}/action/edit$', xblock_edit_view,
            name='xblock_edit_handler'),
    re_path(fr'^xblock/{settings.USAGE_KEY_PATTERN}/(?P<view_name>[^/]+)$', contentstore_views.xblock_view_handler,
            name='xblock_view_handler'),
    re_path(fr'^xblock/{settings.USAGE_KEY_PATTERN}?$', contentstore_views.xblock_handler,
            name='xblock_handler'),
    re_path(fr'^tabs/{settings.COURSE_KEY_PATTERN}$', contentstore_views.tabs_handler,
            name='tabs_handler'),
    re_path(fr'^settings/details/{settings.COURSE_KEY_PATTERN}$', contentstore_views.settings_handler,
            name='settings_handler'),
    re_path(fr'^settings/grading/{settings.COURSE_KEY_PATTERN}(/)?(?P<grader_index>\d+)?$',
            contentstore_views.grading_handler, name='grading_handler'),
    re_path(fr'^settings/advanced/{settings.COURSE_KEY_PATTERN}$', contentstore_views.advanced_settings_handler,
            name='advanced_settings_handler'),
    re_path(fr'^textbooks/{settings.COURSE_KEY_PATTERN}$', contentstore_views.textbooks_list_handler,
            name='textbooks_list_handler'),
    re_path(fr'^textbooks/{settings.COURSE_KEY_PATTERN}/(?P<textbook_id>\d[^/]*)$',
            contentstore_views.textbooks_detail_handler, name='textbooks_detail_handler'),
    re_path(fr'^videos/{settings.COURSE_KEY_PATTERN}(?:/(?P<edx_video_id>[-\w]+))?$',
            contentstore_views.videos_handler, name='videos_handler'),
    re_path(fr'^generate_video_upload_link/{settings.COURSE_KEY_PATTERN}$',
            contentstore_views.generate_video_upload_link_handler, name='generate_video_upload_link'),
    re_path(fr'^video_images/{settings.COURSE_KEY_PATTERN}(?:/(?P<edx_video_id>[-\w]+))?$',
            contentstore_views.video_images_handler, name='video_images_handler'),
    path('video_images_upload_enabled', contentstore_views.video_images_upload_enabled,
         name='video_images_upload_enabled'),
    path('video_features/', contentstore_views.get_video_features, name='video_features'),
    re_path(fr'^transcript_preferences/{settings.COURSE_KEY_PATTERN}$',
            contentstore_views.transcript_preferences_handler, name='transcript_preferences_handler'),
    re_path(fr'^transcript_credentials/{settings.COURSE_KEY_PATTERN}$',
            contentstore_views.transcript_credentials_handler, name='transcript_credentials_handler'),
    path('transcript_download/', contentstore_views.transcript_download_handler, name='transcript_download_handler'),
    path('transcript_upload/', contentstore_views.transcript_upload_handler, name='transcript_upload_handler'),
    re_path(r'^transcript_delete/{}(?:/(?P<edx_video_id>[-\w]+))?(?:/(?P<language_code>[^/]*))?$'.format(
        settings.COURSE_KEY_PATTERN
    ), contentstore_views.transcript_delete_handler, name='transcript_delete_handler'),
    path('transcript_upload_api/', contentstore_views.transcript_upload_api, name='transcript_upload_api'),
    re_path(fr'^video_encodings_download/{settings.COURSE_KEY_PATTERN}$',
            contentstore_views.video_encodings_download, name='video_encodings_download'),
    re_path(fr'^group_configurations/{settings.COURSE_KEY_PATTERN}$',
            contentstore_views.group_configurations_list_handler,
            name='group_configurations_list_handler'),
    re_path(r'^group_configurations/{}/(?P<group_configuration_id>\d+)(/)?(?P<group_id>\d+)?$'.format(
        settings.COURSE_KEY_PATTERN), contentstore_views.group_configurations_detail_handler,
        name='group_configurations_detail_handler'),
    path('api/val/v0/', include('edxval.urls')),
    path('api/tasks/v0/', include('user_tasks.urls')),
    path('accessibility', contentstore_views.accessibility, name='accessibility'),
    re_path(fr'api/youtube/courses/{COURSELIKE_KEY_PATTERN}/edx-video-ids$',
            contentstore_views.get_course_youtube_edx_videos_ids, name='youtube_edx_video_ids'),
    re_path(fr'^api/courses/{settings.COURSE_KEY_PATTERN}/bulk_enable_disable_discussions$',
            contentstore_views.bulk_enable_disable_discussions,
            name='bulk_enable_disable_discussions'),
]

if not settings.DISABLE_DEPRECATED_SIGNIN_URL:
    # TODO: Remove deprecated signin url when traffic proves it is no longer in use
    urlpatterns += [
        path('signin', contentstore_views.login_redirect_to_lms),
    ]

if not settings.DISABLE_DEPRECATED_SIGNUP_URL:
    # TODO: Remove deprecated signup url when traffic proves it is no longer in use
    urlpatterns += [
        path('signup', contentstore_views.register_redirect_to_lms, name='register_redirect_to_lms'),
    ]

JS_INFO_DICT = {
    'domain': 'djangojs',
    # We need to explicitly include external Django apps that are not in LOCALE_PATHS.
    'packages': ('openassessment',),
}

urlpatterns += [
    path('openassessment/fileupload/', include('openassessment.fileupload.urls')),
]

if toggles.ENABLE_CONTENT_LIBRARIES:
    urlpatterns += [
        re_path(fr'^library/{LIBRARY_KEY_PATTERN}?$',
                contentstore_views.library_handler, name='library_handler'),
        re_path(fr'^library/{LIBRARY_KEY_PATTERN}/team/$',
                contentstore_views.manage_library_users, name='manage_library_users'),
    ]

if toggles.EXPORT_GIT.is_enabled():
    urlpatterns += [
        re_path(fr'^export_git/{settings.COURSE_KEY_PATTERN}$',
                contentstore_views.export_git,
                name='export_git')
    ]

if settings.FEATURES.get('ENABLE_SERVICE_STATUS'):
    urlpatterns.append(path('status/', include('openedx.core.djangoapps.service_status.urls')))

# The password pages in the admin tool are disabled so that all password
# changes go through our user portal and follow complexity requirements.
if not settings.FEATURES.get('ENABLE_CHANGE_USER_PASSWORD_ADMIN'):
    urlpatterns.append(re_path(r'^admin/auth/user/\d+/password/$', handler404))
urlpatterns.append(path('admin/password_change/', handler404))
urlpatterns.append(
    path('admin/login/', contentstore_views.redirect_to_lms_login_for_admin, name='redirect_to_lms_login_for_admin')
)
urlpatterns.append(path('admin/', admin.site.urls))

# enable entrance exams
if core_toggles.ENTRANCE_EXAMS.is_enabled():
    urlpatterns.append(re_path(fr'^course/{settings.COURSE_KEY_PATTERN}/entrance_exam/?$',
                       contentstore_views.entrance_exam))

# Enable Web/HTML Certificates
if settings.FEATURES.get('CERTIFICATES_HTML_VIEW'):
    from cms.djangoapps.contentstore.views.certificates import (
        CertificateActivationAPIView,
        CertificateDetailAPIView,
        certificates_list_handler,
        signatory_detail_handler,
    )

    urlpatterns += [
        re_path(fr'^certificates/activation/{settings.COURSE_KEY_PATTERN}/',
                CertificateActivationAPIView.as_view(),
                name='certificate_activation_handler'),
        re_path(r'^certificates/{}/(?P<certificate_id>\d+)/signatories/(?P<signatory_id>\d+)?$'.format(
            settings.COURSE_KEY_PATTERN), signatory_detail_handler, name='signatory_detail_handler'),
        re_path(fr'^certificates/{settings.COURSE_KEY_PATTERN}/(?P<certificate_id>\d+)?$',
                CertificateDetailAPIView.as_view(), name='certificates_detail_handler'),
        re_path(fr'^certificates/{settings.COURSE_KEY_PATTERN}$',
                certificates_list_handler, name='certificates_list_handler')
    ]


if settings.DEBUG:
    try:
        from .urls_dev import urlpatterns as dev_urlpatterns
        urlpatterns += dev_urlpatterns
    except ImportError:
        pass

    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

    urlpatterns += static(
        settings.VIDEO_IMAGE_SETTINGS['BASE_URL'],
        document_root=settings.VIDEO_IMAGE_SETTINGS['STORAGE_KWARGS']['location']
    )

    urlpatterns += static(
        settings.VIDEO_TRANSCRIPTS_SETTINGS['BASE_URL'],
        document_root=settings.VIDEO_TRANSCRIPTS_SETTINGS['STORAGE_KWARGS']['location']
    )

if 'debug_toolbar' in settings.INSTALLED_APPS:
    import debug_toolbar
    urlpatterns.append(path('__debug__/', include(debug_toolbar.urls)))

# UX reference templates
urlpatterns.append(path('template/<path:template>', openedx.core.djangoapps.debug.views.show_reference_template,
                        name='openedx.core.djangoapps.debug.views.show_reference_template'))

urlpatterns.append(
    path('api/learning_sequences/', include(
        ('openedx.core.djangoapps.content.learning_sequences.urls', 'learning_sequences'),
        namespace='learning_sequences'
    ),
    ),
)

urlpatterns.append(
    path('', include(('openedx.core.djangoapps.content.search.urls', 'content_search'), namespace='content_search')),
)

# display error page templates, for testing purposes
urlpatterns += [
    path('404', handler404),
    path('500', handler500),
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

# pylint: disable=wrong-import-position, wrong-import-order
from edx_django_utils.plugins import get_plugin_url_patterns  # isort:skip
# pylint: disable=wrong-import-position
from openedx.core.djangoapps.plugins.constants import ProjectType  # isort:skip

urlpatterns.extend(get_plugin_url_patterns(ProjectType.CMS))

# Contentstore REST APIs
urlpatterns += [
    path('api/contentstore/', include('cms.djangoapps.contentstore.rest_api.urls'))
]

# Content tagging
urlpatterns += [
    path('api/content_tagging/', include(('openedx.core.djangoapps.content_tagging.urls', 'content_tagging'))),
]

# Authoring-api specific API docs (using drf-spectacular and openapi-v3).
# This is separate from and in addition to the full studio swagger documentation already existing at /api-docs.
# Custom settings are provided in SPECTACULAR_SETTINGS as environment variables
# Filter function in cms/lib/spectacular.py determines paths that are swagger-documented.
urlpatterns += [
    re_path('^authoring-api/ui/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    re_path('^authoring-api/schema/', SpectacularAPIView.as_view(), name='schema'),
]
