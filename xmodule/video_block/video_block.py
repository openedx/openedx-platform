"""Video is ungraded Xmodule for support video content.
It's new improved video block, which support additional feature:
- Can play non-YouTube video sources via in-browser HTML5 video player.
- YouTube defaults to HTML5 mode from the start.
- Speed changes in both YouTube and non-YouTube videos happen via
in-browser HTML5 video method (when in HTML5 mode).
- Navigational subtitles can be disabled altogether via an attribute
in XML.
Examples of html5 videos for manual testing:
    https://s3.amazonaws.com/edx-course-videos/edx-intro/edX-FA12-cware-1_100.mp4
    https://s3.amazonaws.com/edx-course-videos/edx-intro/edX-FA12-cware-1_100.webm
    https://s3.amazonaws.com/edx-course-videos/edx-intro/edX-FA12-cware-1_100.ogv
"""


import copy
import json
import logging
from collections import OrderedDict, defaultdict
from operator import itemgetter

from django.conf import settings
from edx_django_utils.cache import RequestCache
from lxml import etree
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import AssetLocator
from web_fragments.fragment import Fragment
from xblock.completable import XBlockCompletionMode
from xblock.core import XBlock
from xblock.fields import ScopeIds
from xblock.runtime import KvsFieldData
from xblocks_contrib.video import VideoBlock as _ExtractedVideoBlock
from xblock.utils.resources import ResourceLoader

from common.djangoapps.xblock_django.constants import ATTR_KEY_REQUEST_COUNTRY_CODE, ATTR_KEY_USER_ID
from openedx.core.lib.cache_utils import request_cached
from openedx.core.lib.license import LicenseMixin
from xmodule.contentstore.content import StaticContent
from xmodule.editing_block import EditingMixin
from xmodule.exceptions import NotFoundError
from xmodule.modulestore.inheritance import InheritanceKeyValueStore
from xmodule.raw_block import EmptyDataRawMixin
from xmodule.util.builtin_assets import add_css_to_fragment, add_webpack_js_to_fragment
from xmodule.validation import StudioValidation, StudioValidationMessage
from xmodule.x_module import (
    PUBLIC_VIEW, STUDENT_VIEW,
    ResourceTemplates,
    XModuleMixin, XModuleToXBlockMixin,
)
from xmodule.xml_block import XmlMixin, deserialize_field, is_pointer_tag, name_to_pathname
from .bumper_utils import bumperize
from openedx.core.djangoapps.video_config.transcripts_utils import (
    Transcript,
    VideoTranscriptsMixin,
    clean_video_id,
    get_endonym_or_label,
    get_html5_ids,
    subs_filename
)
from .video_handlers import VideoStudentViewHandlers, VideoStudioViewHandlers
from .video_utils import create_youtube_string, format_xml_exception_message, get_poster, rewrite_video_url
from .video_xfields import VideoFields

from xblocks_contrib.video.exceptions import TranscriptNotFoundError

# The following import/except block for edxval is temporary measure until
# edxval is a proper XBlock Runtime Service.
#
# Here's the deal: the VideoBlock should be able to take advantage of edx-val
# (https://github.com/openedx/edx-val) to figure out what URL to give for video
# resources that have an edx_video_id specified. edx-val is a Django app, and
# including it causes tests to fail because we run common/lib tests standalone
# without Django dependencies. The alternatives seem to be:
#
# 1. Move VideoBlock out of edx-platform.
# 2. Accept the Django dependency in common/lib.
# 3. Try to import, catch the exception on failure, and check for the existence
#    of edxval_api before invoking it in the code.
# 4. Make edxval an XBlock Runtime Service
#
# (1) is a longer term goal. VideoBlock should be made into an XBlock and
# extracted from edx-platform entirely. But that's expensive to do because of
# the various dependencies (like templates). Need to sort this out.
# (2) is explicitly discouraged.
# (3) is what we're doing today. The code is still functional when called within
# the context of the LMS, but does not cause failure on import when running
# standalone tests. Most VideoBlock tests tend to be in the LMS anyway,
# probably for historical reasons, so we're not making things notably worse.
# (4) is one of the next items on the backlog for edxval, and should get rid
# of this particular import silliness. It's just that I haven't made one before,
# and I was worried about trying it with my deadline constraints.
try:
    import edxval.api as edxval_api
except ImportError:
    edxval_api = None

log = logging.getLogger(__name__)
loader = ResourceLoader("lms")

# Make '_' a no-op so we can scrape strings. Using lambda instead of
#  `django.utils.translation.ugettext_noop` because Django cannot be imported in this file
_ = lambda text: text

EXPORT_IMPORT_COURSE_DIR = 'course'
EXPORT_IMPORT_STATIC_DIR = 'static'


@XBlock.wants('settings', 'completion', 'request_cache', 'video_config')
@XBlock.needs('mako', 'user', 'i18n')
class _BuiltInVideoBlock(
        VideoFields, VideoTranscriptsMixin, VideoStudioViewHandlers, VideoStudentViewHandlers,
        EmptyDataRawMixin, XmlMixin, EditingMixin, XModuleToXBlockMixin,
        ResourceTemplates, XModuleMixin, LicenseMixin):
    """
    XML source example:
        <video show_captions="true"
            youtube="0.75:jNCf2gIqpeE,1.0:ZwkTiUPN0mg,1.25:rsq9auxASqI,1.50:kMyNdzVHHgg"
            url_name="lecture_21_3" display_name="S19V3: Vacancies"
        >
            <source src=".../mit-3091x/M-3091X-FA12-L21-3_100.mp4"/>
            <source src=".../mit-3091x/M-3091X-FA12-L21-3_100.webm"/>
            <source src=".../mit-3091x/M-3091X-FA12-L21-3_100.ogv"/>
        </video>
    """
    is_extracted = False
    has_custom_completion = True
    completion_mode = XBlockCompletionMode.COMPLETABLE

    video_time = 0
    icon_class = 'video'

    show_in_read_only_mode = True

    tabs = [
        {
            'name': _("Basic"),
            'template': "video/transcripts.html",
            'current': True
        },
        {
            'name': _("Advanced"),
            'template': "tabs/metadata-edit-tab.html"
        }
    ]

    mako_template = "widgets/tabs-aggregator.html"
    js_module_name = "TabsEditingDescriptor"

    uses_xmodule_styles_setup = True

    def get_transcripts_for_student(self, transcripts, dest_lang=None):
        """Return transcript information necessary for rendering the XModule student view.
        This is more or less a direct extraction from `get_html`.

        Args:
            transcripts (dict): A dict with all transcripts and a sub.

        Returns:
            Tuple of (track_url, transcript_language, sorted_languages)
            track_url -> subtitle download url
            transcript_language -> default transcript language
            sorted_languages -> dictionary of available transcript languages
        """
        track_url = None
        sub, other_lang = transcripts["sub"], transcripts["transcripts"]
        if self.download_track:
            if self.track:
                track_url = self.track
            elif sub or other_lang:
                track_url = self.runtime.handler_url(self, 'transcript', 'download').rstrip('/?')

        transcript_language = self.get_default_transcript_language(transcripts, dest_lang)
        languages = {}
        for lang_code in other_lang:
            try:
                label = get_endonym_or_label(lang_code)
                languages[lang_code] = label
            except NotFoundError:
                continue

        if not other_lang or (other_lang and sub):
            languages['en'] = 'English'

        # OrderedDict for easy testing of rendered context in tests
        sorted_languages = sorted(list(languages.items()), key=itemgetter(1))

        sorted_languages = OrderedDict(sorted_languages)
        return track_url, transcript_language, sorted_languages

    def is_hls_playback_enabled(self, course_id: CourseKey) -> bool:
        """
        Check if HLS playback is enabled for the course.
        """
        video_config_service = self.runtime.service(self, 'video_config')
        return video_config_service.is_hls_playback_enabled(course_id) if video_config_service else False

    @property
    def youtube_deprecated(self):
        """
        Return True if youtube is deprecated and hls as primary playback is enabled else False
        """
        video_config_service = self.runtime.service(self, 'video_config')
        # Return False if `hls` playback feature is disabled.
        if not self.is_hls_playback_enabled(self.location.course_key):
            return False

        # check if youtube has been deprecated and hls as primary playback
        # is enabled for this course
        return video_config_service.is_youtube_deprecated(self.location.course_key) if video_config_service else False

    def youtube_disabled_for_course(self):  # lint-amnesty, pylint: disable=missing-function-docstring
        if not self.location.context_key.is_course:
            return False  # Only courses have this flag
        request_cache = RequestCache('youtube_disabled_for_course')
        cache_response = request_cache.get_cached_response(self.location.context_key)
        if cache_response.is_found:
            return cache_response.value

        video_config_service = self.runtime.service(self, 'video_config')
        youtube_is_disabled = video_config_service.is_youtube_blocked_for_course(
            self.location.course_key) if video_config_service else False
        request_cache.set(self.location.context_key, youtube_is_disabled)
        return youtube_is_disabled

    def prioritize_hls(self, youtube_streams, html5_sources):
        """
        Decide whether hls can be prioritized as primary playback or not.

        If both the youtube and hls sources are present then make decision on flag
        If only either youtube or hls is present then play whichever is present
        """
        yt_present = bool(youtube_streams.strip()) if youtube_streams else False
        hls_present = any(source for source in html5_sources)

        if yt_present and hls_present:
            return self.youtube_deprecated

        return False

    def student_view(self, context):
        """
        Return the student view.
        """
        fragment = Fragment(self.get_html(context=context))
        add_css_to_fragment(fragment, 'VideoBlockDisplay.css')
        add_webpack_js_to_fragment(fragment, 'VideoBlockDisplay')
        fragment.initialize_js('Video')
        return fragment

    def author_view(self, context):
        """
        Renders the Studio preview view.
        """
        return self.student_view(context)

    def public_view(self, context):
        """
        Returns a fragment that contains the html for the public view
        """
        is_embed = context.get("public_video_embed")

        if not is_embed and getattr(self.runtime, 'suppports_state_for_anonymous_users', False):
            # The new runtime can support anonymous users as fully as regular users:
            return self.student_view(context)

        fragment = Fragment(self.get_html(view=PUBLIC_VIEW, context=context))
        add_css_to_fragment(fragment, 'VideoBlockDisplay.css')
        add_webpack_js_to_fragment(fragment, 'VideoBlockDisplay')
        fragment.initialize_js('Video')
        return fragment

    def get_html(self, view=STUDENT_VIEW, context=None):  # lint-amnesty, pylint: disable=arguments-differ, too-many-statements
        """
        Return html for a given view of this block.
        """
        context = context or {}
        track_status = (self.download_track and self.track)
        transcript_download_format = self.transcript_download_format if not track_status else None
        sources = [source for source in self.html5_sources if source]

        download_video_link = None
        youtube_streams = ""
        video_duration = None
        video_status = None

        # Determine if there is an alternative source for this video
        # based on user locale.  This exists to support cases where
        # we leverage a geography specific CDN, like China.
        default_cdn_url = getattr(settings, 'VIDEO_CDN_URL', {}).get('default')
        user_location = self.runtime.service(self, 'user').get_current_user().opt_attrs[ATTR_KEY_REQUEST_COUNTRY_CODE]
        cdn_url = getattr(settings, 'VIDEO_CDN_URL', {}).get(user_location, default_cdn_url)

        # If we have an edx_video_id, we prefer its values over what we store
        # internally for download links (source, html5_sources) and the youtube
        # stream.
        if self.edx_video_id and edxval_api:  # lint-amnesty, pylint: disable=too-many-nested-blocks
            try:
                val_profiles = ["youtube", "desktop_webm", "desktop_mp4"]

                if self.is_hls_playback_enabled(self.course_id):
                    val_profiles.append('hls')

                # strip edx_video_id to prevent ValVideoNotFoundError error if unwanted spaces are there. TNL-5769
                val_video_urls = edxval_api.get_urls_for_profiles(self.edx_video_id.strip(), val_profiles)

                # VAL will always give us the keys for the profiles we asked for, but
                # if it doesn't have an encoded video entry for that Video + Profile, the
                # value will map to `None`

                # add the non-youtube urls to the list of alternative sources
                # use the last non-None non-youtube non-hls url as the link to download the video
                for url in [val_video_urls[p] for p in val_profiles if p != "youtube"]:
                    if url:
                        if url not in sources:
                            sources.append(url)
                        # don't include hls urls for download
                        if self.download_video and not url.endswith('.m3u8'):
                            # function returns None when the url cannot be re-written
                            rewritten_link = rewrite_video_url(cdn_url, url)
                            if rewritten_link:
                                download_video_link = rewritten_link
                            else:
                                download_video_link = url

                # set the youtube url
                if val_video_urls["youtube"]:
                    youtube_streams = "1.00:{}".format(val_video_urls["youtube"])

                # get video duration
                video_data = edxval_api.get_video_info(self.edx_video_id.strip())
                video_duration = video_data.get('duration')
                video_status = video_data.get('status')

            except (edxval_api.ValInternalError, edxval_api.ValVideoNotFoundError):
                # VAL raises this exception if it can't find data for the edx video ID. This can happen if the
                # course data is ported to a machine that does not have the VAL data. So for now, pass on this
                # exception and fallback to whatever we find in the VideoBlock.
                log.warning("Could not retrieve information from VAL for edx Video ID: %s.", self.edx_video_id)

        # If the user comes from China use China CDN for html5 videos.
        # 'CN' is China ISO 3166-1 country code.
        # Video caching is disabled for Studio. User_location is always None in Studio.
        # CountryMiddleware disabled for Studio.
        if getattr(self, 'video_speed_optimizations', True) and cdn_url:

            if self.edx_video_id and edxval_api and video_status != 'external':
                for index, source_url in enumerate(sources):
                    new_url = rewrite_video_url(cdn_url, source_url)
                    if new_url:
                        sources[index] = new_url

        # If there was no edx_video_id, or if there was no download specified
        # for it, we fall back on whatever we find in the VideoBlock.
        if not download_video_link and self.download_video:
            if self.html5_sources:
                # If there are multiple html5 sources, we use the first non HLS video urls
                download_video_link = next((url for url in self.html5_sources if not url.endswith('.m3u8')), None)

        transcripts = self.get_transcripts_info()
        track_url, transcript_language, sorted_languages = self.get_transcripts_for_student(
            transcripts=transcripts,
            dest_lang=context.get("dest_lang")
        )

        cdn_eval = False
        cdn_exp_group = None

        if self.youtube_disabled_for_course():
            self.youtube_streams = ''  # lint-amnesty, pylint: disable=attribute-defined-outside-init
        else:
            self.youtube_streams = youtube_streams or create_youtube_string(self)  # pylint: disable=W0201

        settings_service = self.runtime.service(self, 'settings')  # lint-amnesty, pylint: disable=unused-variable

        poster = self._poster()
        completion_service = self.runtime.service(self, 'completion')
        if completion_service:
            completion_enabled = completion_service.completion_tracking_enabled()
        else:
            completion_enabled = False

        # This is the setting that controls whether the autoadvance button will be visible, not whether the
        # video will autoadvance or not.
        # For autoadvance controls to be shown, both the feature flag and the course setting must be true.
        # This allows to enable the feature for certain courses only.
        autoadvance_enabled = settings.FEATURES.get('ENABLE_AUTOADVANCE_VIDEOS', False) and \
            getattr(self, 'video_auto_advance', False)

        # This is the current status of auto-advance (not the control visibility).
        # But when controls aren't visible we force it to off. The student might have once set the preference to
        # true, but now staff or admin have hidden the autoadvance button and the student won't be able to disable
        # it anymore; therefore we force-disable it in this case (when controls aren't visible).
        autoadvance_this_video = self.auto_advance and autoadvance_enabled
        is_embed = context.get('public_video_embed', False)
        is_public_view = view == PUBLIC_VIEW
        metadata = {
            'autoAdvance': autoadvance_this_video,
            # For now, the option "data-autohide-html5" is hard coded. This option
            # either enables or disables autohiding of controls and captions on mouse
            # inactivity. If set to true, controls and captions will autohide for
            # HTML5 sources (non-YouTube) after a period of mouse inactivity over the
            # whole video. When the mouse moves (or a key is pressed while any part of
            # the video player is focused), the captions and controls will be shown
            # once again.
            #
            # There is no option in the "Advanced Editor" to set this option. However,
            # this option will have an effect if changed to "True". The code on
            # front-end exists.
            'autohideHtml5': False,
            'autoplay': settings.FEATURES.get('AUTOPLAY_VIDEOS', False),
            # This won't work when we move to data that
            # isn't on the filesystem
            'captionDataDir': getattr(self, 'data_dir', None),
            'completionEnabled': completion_enabled,
            'completionPercentage': settings.COMPLETION_VIDEO_COMPLETE_PERCENTAGE,
            'duration': video_duration,
            'end': self.end_time.total_seconds(),  # pylint: disable=no-member
            'generalSpeed': self.global_speed,
            'lmsRootURL': settings.LMS_ROOT_URL,
            'poster': poster,
            'prioritizeHls': self.prioritize_hls(self.youtube_streams, sources),
            'publishCompletionUrl': self.runtime.handler_url(self, 'publish_completion', '').rstrip('?'),
            # This is the server's guess at whether youtube is available for
            # this user, based on what was recorded the last time we saw the
            # user, and defaulting to True.
            'recordedYoutubeIsAvailable': self.youtube_is_available,
            'savedVideoPosition': self.saved_video_position.total_seconds(),  # pylint: disable=no-member
            'saveStateEnabled': not is_public_view,
            'saveStateUrl': self.ajax_url + '/save_user_state',
            # Despite the setting on the block, don't show transcript by default
            # if the video is embedded in social media
            'showCaptions': json.dumps(self.show_captions and not is_embed),
            'sources': sources,
            'speed': self.speed,
            'start': self.start_time.total_seconds(),  # pylint: disable=no-member
            'streams': self.youtube_streams,
            'transcriptAvailableTranslationsUrl': self.runtime.handler_url(
                self, 'transcript', 'available_translations'
            ).rstrip('/?'),
            'aiTranslationsUrl': settings.AI_TRANSLATIONS_API_URL,
            'transcriptLanguage': transcript_language,
            'transcriptLanguages': sorted_languages,
            'transcriptTranslationUrl': self.runtime.handler_url(
                self, 'transcript', 'translation/__lang__'
            ).rstrip('/?'),
            'ytApiUrl': settings.YOUTUBE['API'],
            'ytMetadataEndpoint': (
                # In the new runtime, get YouTube metadata via a handler. The handler supports anonymous users and
                # can work in sandboxed iframes. In the old runtime, the JS will call the LMS's yt_video_metadata
                # API endpoint directly (not an XBlock handler).
                self.runtime.handler_url(self, 'yt_video_metadata')
                if getattr(self.runtime, 'suppports_state_for_anonymous_users', False) else ''
            ),
            'ytTestTimeout': settings.YOUTUBE['TEST_TIMEOUT'],
        }

        bumperize(self)

        is_video_from_same_origin = bool(download_video_link and cdn_url and download_video_link.startswith(cdn_url))

        template_context = {
            'autoadvance_enabled': autoadvance_enabled,
            'bumper_metadata': json.dumps(self.bumper['metadata']),  # pylint: disable=E1101
            'cdn_eval': cdn_eval,
            'cdn_exp_group': cdn_exp_group,
            'display_name': self.display_name_with_default,
            'download_video_link': download_video_link,
            'is_video_from_same_origin': is_video_from_same_origin,
            'handout': self.handout,
            'hide_downloads': is_public_view or is_embed,
            'id': self.location.html_id(),
            'block_id': str(self.location),
            'course_id': str(self.context_key),
            'video_id': str(self.edx_video_id),
            'user_id': self.get_user_id(),
            'is_embed': is_embed,
            'license': getattr(self, "license", None),
            'metadata': json.dumps(OrderedDict(metadata)),
            'poster': json.dumps(get_poster(self)),
            'track': track_url,
            'transcript_download_format': transcript_download_format,
            'transcript_download_formats_list': self.fields['transcript_download_format'].values,  # lint-amnesty, pylint: disable=unsubscriptable-object
            'transcript_feedback_enabled': self.is_transcript_feedback_enabled(),
        }
        video_config_service = self.runtime.service(self, 'video_config')
        if video_config_service:
            template_context.update(video_config_service.get_public_sharing_context(self, self.course_id))

        return loader.render_django_template("templates/video.html", template_context)

    def is_transcript_feedback_enabled(self):
        """
        Is transcript feedback enabled for this video?
        """
        if not self.context_key.is_course:
            return False  # Only courses support this feature at all (not libraries)
        try:
            # Video transcript feedback must be enabled in order to show the widget
            video_config_service = self.runtime.service(self, 'video_config')
            feature_enabled = video_config_service.is_transcript_feedback_enabled(
                self.context_key) if video_config_service else False
        except Exception as err:  # pylint: disable=broad-except
            log.exception(f"Error retrieving course for course ID: {self.context_key}")
            return False
        return feature_enabled

    def get_user_id(self):
        return self.runtime.service(self, 'user').get_current_user().opt_attrs.get(ATTR_KEY_USER_ID)

    def get_public_video_url(self):
        """
        Returns the public video url
        """
        video_config_service = self.runtime.service(self, 'video_config')
        return video_config_service.get_public_video_url(self) if video_config_service else None

    def validate(self):
        """
        Validates the state of this Video XBlock instance. This
        is the override of the general XBlock method, and it will also ask
        its superclass to validate.
        """
        validation = super().validate()
        if not isinstance(validation, StudioValidation):
            validation = StudioValidation.copy(validation)

        no_transcript_lang = []
        for lang_code, transcript in self.transcripts.items():
            if not transcript:
                no_transcript_lang.append([label for code, label in settings.ALL_LANGUAGES if code == lang_code][0])

        if no_transcript_lang:
            ungettext = self.runtime.service(self, "i18n").ungettext
            validation.set_summary(
                StudioValidationMessage(
                    StudioValidationMessage.WARNING,
                    ungettext(
                        'There is no transcript file associated with the {lang} language.',
                        'There are no transcript files associated with the {lang} languages.',
                        len(no_transcript_lang)
                    ).format(lang=', '.join(sorted(no_transcript_lang)))
                )
            )
        return validation

    def editor_saved(self, user, old_metadata, old_content):  # lint-amnesty, pylint: disable=unused-argument
        """
        Used to update video values during `self`:save method from CMS.
        old_metadata: dict, values of fields of `self` with scope=settings which were explicitly set by user.
        old_content, same as `old_metadata` but for scope=content.
        Due to nature of code flow in block.py::_save_item, before current function is called,
        fields of `self` instance have been already updated, but not yet saved.
        To obtain values, which were changed by user input,
        one should compare own_metadata(self) and old_medatada.
        Video player has two tabs, and due to nature of sync between tabs,
        metadata from Basic tab is always sent when video player is edited and saved first time, for example:
        {'youtube_id_1_0': u'3_yD_cEKoCk', 'display_name': u'Video', 'sub': u'3_yD_cEKoCk', 'html5_sources': []},
        that's why these fields will always present in old_metadata after first save. This should be fixed.
        At consequent save requests html5_sources are always sent too, disregard of their change by user.
        That means that html5_sources are always in list of fields that were changed (`metadata` param in save_item).
        This should be fixed too.
        """
        video_config_service = self.runtime.service(self, 'video_config')
        if video_config_service:
            video_config_service.handle_editor_saved(self, user.id, old_metadata)

    def save_with_metadata(self, user_id):
        """
        Save block with updated metadata to database."
        """
        self.save()
        self.runtime.modulestore.update_item(self, user_id)

    @property
    def editable_metadata_fields(self):
        editable_fields = super().editable_metadata_fields

        settings_service = self.runtime.service(self, 'settings')
        if settings_service:
            xb_settings = settings_service.get_settings_bucket(self)
            if not xb_settings.get("licensing_enabled", False) and "license" in editable_fields:
                del editable_fields["license"]

        # Default Timed Transcript a.k.a `sub` has been deprecated and end users shall
        # not be able to modify it.
        editable_fields.pop('sub')

        languages = [{'label': label, 'code': lang} for lang, label in settings.ALL_LANGUAGES]
        languages.sort(key=lambda l: l['label'])
        editable_fields['transcripts']['custom'] = True
        editable_fields['transcripts']['languages'] = languages
        editable_fields['transcripts']['type'] = 'VideoTranslations'

        # We need to send ajax requests to show transcript status
        # whenever edx_video_id changes on frontend. Thats why we
        # are changing type to `VideoID` so that a specific
        # Backbonjs view can handle it.
        editable_fields['edx_video_id']['type'] = 'VideoID'

        # `public_access` is a boolean field and by default backbonejs code render it as a dropdown with 2 options
        # but in our case we also need to show an input field with dropdown, the input field will show the url to
        # be shared with leaners. This is not possible with default rendering logic in backbonjs code, that is why
        # we are setting a new type and then do a custom rendering in backbonejs code to render the desired UI.
        editable_fields['public_access']['type'] = 'PublicAccess'
        editable_fields['public_access']['url'] = self.get_public_video_url()

        # construct transcripts info and also find if `en` subs exist
        transcripts_info = self.get_transcripts_info()
        possible_sub_ids = [self.sub, self.youtube_id_1_0] + get_html5_ids(self.html5_sources)
        video_config_service = self.runtime.service(self, 'video_config')
        if video_config_service:
            for sub_id in possible_sub_ids:
                try:
                    _, sub_id, _ = video_config_service.get_transcript(self, lang='en', output_format=Transcript.TXT)
                    transcripts_info['transcripts'] = dict(transcripts_info['transcripts'], en=sub_id)
                    break
                except TranscriptNotFoundError:
                    continue

        editable_fields['transcripts']['value'] = transcripts_info['transcripts']
        editable_fields['transcripts']['urlRoot'] = self.runtime.handler_url(
            self,
            'studio_transcript',
            'translation'
        ).rstrip('/?')
        editable_fields['handout']['type'] = 'FileUploader'

        return editable_fields

    @classmethod
    def parse_xml_new_runtime(cls, node, runtime, keys):
        """
        Implement the video block's special XML parsing requirements for the
        new runtime only. For all other runtimes, use .parse_xml().
        """
        video_block = runtime.construct_xblock_from_class(cls, keys)
        field_data = cls.parse_video_xml(node)
        for key, val in field_data.items():
            if key not in cls.fields:  # lint-amnesty, pylint: disable=unsupported-membership-test
                continue  # parse_video_xml returns some old non-fields like 'source'
            setattr(video_block, key, cls.fields[key].from_json(val))  # lint-amnesty, pylint: disable=unsubscriptable-object
        # Don't use VAL in the new runtime:
        video_block.edx_video_id = None
        return video_block

    @classmethod
    def parse_xml(cls, node, runtime, _keys):
        """
        Use `node` to construct a new block.

        See XmlMixin.parse_xml for the detailed description.
        """
        url_name = node.get('url_name')
        block_type = 'video'
        definition_id = runtime.id_generator.create_definition(block_type, url_name)
        usage_id = runtime.id_generator.create_usage(definition_id)
        aside_children = []
        if is_pointer_tag(node):
            filepath = cls._format_filepath(node.tag, name_to_pathname(url_name))
            node = cls.load_file(filepath, runtime.resources_fs, usage_id)
            aside_children = runtime.parse_asides(node, definition_id, usage_id, runtime.id_generator)
        field_data = cls.parse_video_xml(node, runtime.id_generator)
        kvs = InheritanceKeyValueStore(initial_values=field_data)
        field_data = KvsFieldData(kvs)
        video = runtime.construct_xblock_from_class(
            cls,
            # We're loading a descriptor, so student_id is meaningless
            # We also don't have separate notions of definition and usage ids yet,
            # so we use the location for both
            ScopeIds(None, block_type, definition_id, usage_id),
            field_data,
        )

        # Update VAL with info extracted from `node`
        video.edx_video_id = video.import_video_info_into_val(
            node,
            runtime.resources_fs,
            getattr(runtime.id_generator, 'target_course_id', None)
        )

        if aside_children:
            cls.add_applicable_asides_to_block(video, runtime, aside_children)

        return video

    def definition_to_xml(self, resource_fs):  # lint-amnesty, pylint: disable=too-many-statements
        """
        Returns an xml string representing this block.
        """
        xml = etree.Element('video')
        youtube_string = create_youtube_string(self)
        if youtube_string:
            xml.set('youtube', str(youtube_string))
        xml.set('url_name', self.url_name)
        attrs = [
            ('display_name', self.display_name),
            ('show_captions', json.dumps(self.show_captions)),
            ('start_time', self.start_time),
            ('end_time', self.end_time),
            ('sub', self.sub),
            ('download_track', json.dumps(self.download_track)),
            ('download_video', json.dumps(self.download_video))
        ]
        for key, value in attrs:
            # Mild workaround to ensure that tests pass -- if a field
            # is set to its default value, we don't write it out.
            if value:
                if key in self.fields and self.fields[key].is_set_on(self):  # lint-amnesty, pylint: disable=unsubscriptable-object, unsupported-membership-test
                    try:
                        xml.set(key, str(value))
                    except UnicodeDecodeError:
                        exception_message = format_xml_exception_message(self.location, key, value)
                        log.exception(exception_message)
                        # If exception is UnicodeDecodeError set value using unicode 'utf-8' scheme.
                        log.info("Setting xml value using 'utf-8' scheme.")
                        xml.set(key, str(value, 'utf-8'))
                    except ValueError:
                        exception_message = format_xml_exception_message(self.location, key, value)
                        log.exception(exception_message)
                        raise

        for source in self.html5_sources:
            ele = etree.Element('source')
            ele.set('src', source)
            xml.append(ele)

        if self.track:
            ele = etree.Element('track')
            ele.set('src', self.track)
            xml.append(ele)

        if self.handout:
            ele = etree.Element('handout')
            ele.set('src', self.handout)
            xml.append(ele)

        transcripts = {}
        if self.transcripts is not None:
            transcripts.update(self.transcripts)

        edx_video_id = clean_video_id(self.edx_video_id)
        if edxval_api and edx_video_id:
            try:
                # Create static dir if not created earlier.
                resource_fs.makedirs(EXPORT_IMPORT_STATIC_DIR, recreate=True)

                # Backward compatible exports
                # edxval exports new transcripts into the course OLX and returns a transcript
                # files map so that it can also be rewritten in old transcript metadata fields
                # (i.e. `self.transcripts`) on import and older open-releases (<= ginkgo),
                # who do not have deprecated contentstore yet, can also import and use new-style
                # transcripts into their openedX instances.
                exported_metadata = edxval_api.export_to_xml(
                    video_id=edx_video_id,
                    resource_fs=resource_fs,
                    static_dir=EXPORT_IMPORT_STATIC_DIR,
                    course_id=self.scope_ids.usage_id.context_key.for_branch(None),
                )
                # Update xml with edxval metadata
                xml.append(exported_metadata['xml'])

                # we don't need sub if english transcript
                # is also in new transcripts.
                new_transcripts = exported_metadata['transcripts']
                transcripts.update(new_transcripts)
                if new_transcripts.get('en'):
                    xml.set('sub', '')

            except edxval_api.ValVideoNotFoundError:
                pass
        else:
            if transcripts.get('en'):
                xml.set('sub', '')

        if transcripts:
            # Update `transcripts` attribute in the xml
            xml.set('transcripts', json.dumps(transcripts, sort_keys=True))

            # Sorting transcripts for easy testing of resulting xml
            for transcript_language in sorted(transcripts.keys()):
                ele = etree.Element('transcript')
                ele.set('language', transcript_language)
                ele.set('src', transcripts[transcript_language])
                xml.append(ele)

        # handle license specifically
        self.add_license_to_xml(xml)

        return xml

    def create_youtube_url(self, youtube_id):
        """

        Args:
            youtube_id: The ID of the video to create a link for

        Returns:
            A full youtube url to the video whose ID is passed in
        """
        if youtube_id:
            return f'https://www.youtube.com/watch?v={youtube_id}'
        else:
            return ''

    def get_context(self):
        """
        Extend context by data for transcript basic tab.
        """
        _context = {
            'editable_metadata_fields': self.editable_metadata_fields
        }
        _context.update({
            'tabs': self.tabs,
            'html_id': self.location.html_id(),  # element_id
            'data': self.data,
        })

        metadata_fields = copy.deepcopy(self.editable_metadata_fields)

        display_name = metadata_fields['display_name']
        video_url = metadata_fields['html5_sources']
        video_id = metadata_fields['edx_video_id']
        youtube_id_1_0 = metadata_fields['youtube_id_1_0']

        def get_youtube_link(video_id):
            """
            Returns the fully-qualified YouTube URL for the given video identifier
            """
            # First try a lookup in VAL. If we have a YouTube entry there, it overrides the
            # one passed in.
            if self.edx_video_id and edxval_api:
                val_youtube_id = edxval_api.get_url_for_profile(self.edx_video_id, "youtube")
                if val_youtube_id:
                    video_id = val_youtube_id

            return self.create_youtube_url(video_id)

        _ = self.runtime.service(self, "i18n").ugettext
        video_url.update({
            'help': _('The URL for your video. This can be a YouTube URL or a link to an .mp4, .ogg, or '
                      '.webm video file hosted elsewhere on the Internet.'),
            'display_name': _('Default Video URL'),
            'field_name': 'video_url',
            'type': 'VideoList',
            'default_value': [get_youtube_link(youtube_id_1_0['default_value'])]
        })

        source_url = self.create_youtube_url(youtube_id_1_0['value'])
        # First try a lookup in VAL. If any video encoding is found given the video id then
        # override the source_url with it.
        if self.edx_video_id and edxval_api:

            val_profiles = ['youtube', 'desktop_webm', 'desktop_mp4']
            if self.is_hls_playback_enabled(self.scope_ids.usage_id.context_key.for_branch(None)):
                val_profiles.append('hls')

            # Get video encodings for val profiles.
            val_video_encodings = edxval_api.get_urls_for_profiles(self.edx_video_id, val_profiles)

            # VAL's youtube source has greater priority over external youtube source.
            if val_video_encodings.get('youtube'):
                source_url = self.create_youtube_url(val_video_encodings['youtube'])

            # If no youtube source is provided externally or in VAl, update source_url in order: hls > mp4 and webm
            if not source_url:
                if val_video_encodings.get('hls'):
                    source_url = val_video_encodings['hls']
                elif val_video_encodings.get('desktop_mp4'):
                    source_url = val_video_encodings['desktop_mp4']
                elif val_video_encodings.get('desktop_webm'):
                    source_url = val_video_encodings['desktop_webm']

        # Only add if html5 sources do not already contain source_url.
        if source_url and source_url not in video_url['value']:
            video_url['value'].insert(0, source_url)

        metadata = {
            'display_name': display_name,
            'video_url': video_url,
            'edx_video_id': video_id
        }

        _context.update({'transcripts_basic_tab_metadata': metadata})
        return _context

    @classmethod
    def _parse_youtube(cls, data):
        """
        Parses a string of Youtube IDs such as "1.0:AXdE34_U,1.5:VO3SxfeD"
        into a dictionary. Necessary for backwards compatibility with
        XML-based courses.
        """
        ret = {'0.75': '', '1.00': '', '1.25': '', '1.50': ''}

        videos = data.split(',')
        for video in videos:
            pieces = video.split(':')
            try:
                speed = '%.2f' % float(pieces[0])  # normalize speed

                # Handle the fact that youtube IDs got double-quoted for a period of time.
                # Note: we pass in "VideoFields.youtube_id_1_0" so we deserialize as a String--
                # it doesn't matter what the actual speed is for the purposes of deserializing.
                youtube_id = deserialize_field(cls.youtube_id_1_0, pieces[1])
                ret[speed] = youtube_id
            except (ValueError, IndexError):
                log.warning('Invalid YouTube ID: %s', video)
        return ret

    @classmethod
    def parse_video_xml(cls, xml, id_generator=None):
        """
        Parse video fields out of xml_data. The fields are set if they are
        present in the XML.

        Arguments:
            id_generator is used to generate course-specific urls and identifiers
        """
        if isinstance(xml, str):
            xml = etree.fromstring(xml)

        field_data = {}

        # Convert between key types for certain attributes --
        # necessary for backwards compatibility.
        conversions = {
            # example: 'start_time': cls._example_convert_start_time
        }

        # Convert between key names for certain attributes --
        # necessary for backwards compatibility.
        compat_keys = {
            'from': 'start_time',
            'to': 'end_time'
        }
        sources = xml.findall('source')
        if sources:
            field_data['html5_sources'] = [ele.get('src') for ele in sources]

        track = xml.find('track')
        if track is not None:
            field_data['track'] = track.get('src')

        handout = xml.find('handout')
        if handout is not None:
            field_data['handout'] = handout.get('src')

        transcripts = xml.findall('transcript')
        if transcripts:
            field_data['transcripts'] = {tr.get('language'): tr.get('src') for tr in transcripts}

        for attr, value in xml.items():
            if attr in compat_keys:  # lint-amnesty, pylint: disable=consider-using-get
                attr = compat_keys[attr]
            if attr in cls.metadata_to_strip + ('url_name', 'name'):
                continue
            if attr == 'youtube':
                speeds = cls._parse_youtube(value)
                for speed, youtube_id in speeds.items():
                    # should have made these youtube_id_1_00 for
                    # cleanliness, but hindsight doesn't need glasses
                    normalized_speed = speed[:-1] if speed.endswith('0') else speed
                    # If the user has specified html5 sources, make sure we don't use the default video
                    if youtube_id != '' or 'html5_sources' in field_data:
                        field_data['youtube_id_{}'.format(normalized_speed.replace('.', '_'))] = youtube_id
            elif attr in conversions:
                field_data[attr] = conversions[attr](value)
            elif attr not in cls.fields:  # lint-amnesty, pylint: disable=unsupported-membership-test
                field_data.setdefault('xml_attributes', {})[attr] = value
            else:
                # We export values with json.dumps (well, except for Strings, but
                # for about a month we did it for Strings also).
                field_data[attr] = deserialize_field(cls.fields[attr], value)  # lint-amnesty, pylint: disable=unsubscriptable-object

        course_id = getattr(id_generator, 'target_course_id', None)
        # Update the handout location with current course_id
        if 'handout' in field_data and course_id:
            handout_location = StaticContent.get_location_from_path(field_data['handout'])
            if isinstance(handout_location, AssetLocator):
                handout_new_location = StaticContent.compute_location(course_id, handout_location.path)
                field_data['handout'] = StaticContent.serialize_asset_key_with_slash(handout_new_location)

        # For backwards compatibility: Add `source` if XML doesn't have `download_video`
        # attribute.
        if 'download_video' not in field_data and sources:
            field_data['source'] = field_data['html5_sources'][0]

        # For backwards compatibility: if XML doesn't have `download_track` attribute,
        # it means that it is an old format. So, if `track` has some value,
        # `download_track` needs to have value `True`.
        if 'download_track' not in field_data and track is not None:
            field_data['download_track'] = True

        # load license if it exists
        field_data = LicenseMixin.parse_license_from_xml(field_data, xml)

        return field_data

    def import_video_info_into_val(self, xml, resource_fs, course_id):
        """
        Import parsed video info from `xml` into edxval.

        Arguments:
            xml (lxml object): xml representation of video to be imported.
            resource_fs (OSFS): Import file system.
            course_id (str): course id
        """
        edx_video_id = clean_video_id(self.edx_video_id)

        # Create video_asset is not already present.
        video_asset_elem = xml.find('video_asset')
        if video_asset_elem is None:
            video_asset_elem = etree.Element('video_asset')

        # This will be a dict containing the list of names of the external transcripts.
        # Example:
        # {
        #     'en': ['The_Flash.srt', 'Harry_Potter.srt'],
        #     'es': ['Green_Arrow.srt']
        # }
        external_transcripts = defaultdict(list)

        # Add trancript from self.sub and self.youtube_id_1_0 fields.
        external_transcripts['en'] = [
            subs_filename(transcript, 'en')
            for transcript in [self.sub, self.youtube_id_1_0] if transcript
        ]

        for language_code, transcript in self.transcripts.items():
            external_transcripts[language_code].append(transcript)

        if edxval_api:
            edx_video_id = edxval_api.import_from_xml(
                video_asset_elem,
                edx_video_id,
                resource_fs,
                EXPORT_IMPORT_STATIC_DIR,
                external_transcripts,
                course_id=course_id
            )
        return edx_video_id

    def index_dictionary(self):
        xblock_body = super().index_dictionary()
        video_body = {
            "display_name": self.display_name,
        }

        def _update_transcript_for_index(language=None):
            """ Find video transcript - if not found, don't update index """
            video_config_service = self.runtime.service(self, 'video_config')
            if video_config_service:
                try:
                    transcript = video_config_service.get_transcript(
                        self, lang=language, output_format=Transcript.TXT
                    )[0].replace("\n", " ")
                    transcript_index_name = f"transcript_{language if language else self.transcript_language}"
                    video_body.update({transcript_index_name: transcript})
                except TranscriptNotFoundError:
                    pass

        if self.sub:
            _update_transcript_for_index()

        # Check to see if there are transcripts in other languages besides default transcript
        if self.transcripts:
            for language in self.transcripts.keys():
                _update_transcript_for_index(language)

        if "content" in xblock_body:
            xblock_body["content"].update(video_body)
        else:
            xblock_body["content"] = video_body
        xblock_body["content_type"] = "Video"

        return xblock_body

    @property
    def request_cache(self):
        """
        Returns the request_cache from the runtime.
        """
        return self.runtime.service(self, "request_cache")

    @classmethod
    @request_cached(
        request_cache_getter=lambda args, kwargs: args[1],
    )
    def get_cached_val_data_for_course(cls, request_cache, video_profile_names, course_id):  # lint-amnesty, pylint: disable=unused-argument
        """
        Returns the VAL data for the requested video profiles for the given course.
        """
        return edxval_api.get_video_info_for_course_and_profiles(str(course_id), video_profile_names)

    def student_view_data(self, context=None):
        """
        Returns a JSON representation of the student_view of this XModule.
        The contract of the JSON content is between the caller and the particular XModule.
        """
        context = context or {}

        # If the "only_on_web" field is set on this video, do not return the rest of the video's data
        # in this json view, since this video is to be accessed only through its web view."
        if self.only_on_web:
            return {"only_on_web": True}

        encoded_videos = {}
        val_video_data = {}
        all_sources = self.html5_sources or []

        # Check in VAL data first if edx_video_id exists
        if self.edx_video_id:
            video_profile_names = context.get("profiles", ["mobile_low", 'desktop_mp4', 'desktop_webm', 'mobile_high'])
            if self.is_hls_playback_enabled(self.location.course_key) and 'hls' not in video_profile_names:
                video_profile_names.append('hls')

            # get and cache bulk VAL data for course
            val_course_data = self.get_cached_val_data_for_course(
                self.request_cache,
                video_profile_names,
                self.location.course_key,
            )
            val_video_data = val_course_data.get(self.edx_video_id, {})

            # Get the encoded videos if data from VAL is found
            if val_video_data:
                encoded_videos = val_video_data.get('profiles', {})

            # If information for this edx_video_id is not found in the bulk course data, make a
            # separate request for this individual edx_video_id, unless cache misses are disabled.
            # This is useful/required for videos that don't have a course designated, such as the introductory video
            # that is shared across many courses.  However, this results in a separate database request so watch
            # out for any performance hit if many such videos exist in a course.  Set the 'allow_cache_miss' parameter
            # to False to disable this fall back.
            elif context.get("allow_cache_miss", "True").lower() == "true":
                try:
                    val_video_data = edxval_api.get_video_info(self.edx_video_id)
                    # Unfortunately, the VAL API is inconsistent in how it returns the encodings, so remap here.
                    for enc_vid in val_video_data.pop('encoded_videos'):
                        if enc_vid['profile'] in video_profile_names:
                            encoded_videos[enc_vid['profile']] = {key: enc_vid[key] for key in ["url", "file_size"]}
                except edxval_api.ValVideoNotFoundError:
                    pass

        # Fall back to other video URLs in the video block if not found in VAL
        if not encoded_videos:
            if all_sources:
                encoded_videos["fallback"] = {
                    "url": all_sources[0],
                    "file_size": 0,  # File size is unknown for fallback URLs
                }

            # Include youtube link if there is no encoding for mobile- ie only a fallback URL or no encodings at all
            # We are including a fallback URL for older versions of the mobile app that don't handle Youtube urls
            if self.youtube_id_1_0:
                encoded_videos["youtube"] = {
                    "url": self.create_youtube_url(self.youtube_id_1_0),
                    "file_size": 0,  # File size is not relevant for external link
                }

        video_config_service = self.runtime.service(self, 'video_config')
        if video_config_service:
            available_translations = video_config_service.available_translations(
                self,
                self.get_transcripts_info()
            )
        else:
            available_translations = []
        transcripts = {
            lang: self.runtime.handler_url(self, 'transcript', 'download', query="lang=" + lang, thirdparty=True)
            for lang in available_translations
        }

        return {
            "only_on_web": self.only_on_web,
            "duration": val_video_data.get('duration', None),
            "transcripts": transcripts,
            "encoded_videos": encoded_videos,
            "all_sources": all_sources,
        }

    def _poster(self):
        """
        Helper to get poster info from edxval
        """
        if edxval_api and self.edx_video_id:
            return edxval_api.get_course_video_image_url(
                course_id=self.scope_ids.usage_id.context_key.for_branch(None),
                edx_video_id=self.edx_video_id.strip()
            )
        return None


VideoBlock = (
    _ExtractedVideoBlock if settings.USE_EXTRACTED_VIDEO_BLOCK
    else _BuiltInVideoBlock
)
VideoBlock.__name__ = "VideoBlock"
