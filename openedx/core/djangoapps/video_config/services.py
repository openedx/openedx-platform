"""
Video Configuration Service for XBlock runtime.

This service provides video-related configuration and feature flags
that are specific to the edx-platform implementation
for the extracted video block in xblocks-contrib repository.
"""

import logging
from typing import Any

from django.conf import settings
from opaque_keys.edx.keys import CourseKey, UsageKey
from opaque_keys.edx.locator import LibraryLocatorV2
from xblocks_contrib.video.exceptions import TranscriptNotFoundError

from openedx.core.djangoapps.video_config import sharing
from django.core.files import File
from django.core.files.base import ContentFile
from edxval.api import create_external_video, create_or_update_video_transcript, delete_video_transcript
from organizations.api import get_course_organization
from openedx.core.djangoapps.video_config.models import (
    CourseYoutubeBlockedFlag,
    HLSPlaybackEnabledFlag,
)
from openedx.core.djangoapps.video_config.toggles import TRANSCRIPT_FEEDBACK
from openedx.core.djangoapps.video_pipeline.config.waffle import DEPRECATE_YOUTUBE
from openedx.core.djangoapps.content_libraries.api import (
    add_library_block_static_asset_file,
    delete_library_block_static_asset_file,
)
from openedx.core.djangoapps.video_config.transcripts_utils import (
    Transcript,
    clean_video_id,
    get_html5_ids,
    manage_video_subtitles_save,
    remove_subs_from_store,
    get_transcript_for_video,
    get_transcript,
)

from xmodule.exceptions import NotFoundError
from xmodule.modulestore.inheritance import own_metadata

# The following import/except block for edxval is temporary measure until
# edxval is a proper XBlock Runtime Service.
try:
    import edxval.api as edxval_api
except ImportError:
    edxval_api = None


log = logging.getLogger(__name__)


class VideoConfigService:
    """
    Service for providing video-related configuration and feature flags.

    This service abstracts away edx-platform specific functionality
    that the Video XBlock needs, allowing the Video XBlock to be
    extracted to a separate repository.

    TODO: This service could be improved in a few ways:
    https://github.com/openedx/edx-platform/issues/37656
    """

    def get_public_video_url(self, usage_id: UsageKey) -> str:
        """
        Returns the public video url
        """
        return sharing.get_public_video_url(usage_id)

    def get_public_sharing_context(self, video_block, course_key: CourseKey) -> dict:
        """
        Get the complete public sharing context for a video.

        Args:
            video_block: The video XBlock instance
            course_key: The course identifier

        Returns:
            dict: Context dictionary with sharing information, empty if sharing is disabled
        """
        context = {}

        if not sharing.is_public_sharing_enabled(video_block.location, video_block.public_access):
            return context

        public_video_url = sharing.get_public_video_url(video_block.location)
        context['public_sharing_enabled'] = True
        context['public_video_url'] = public_video_url

        organization = get_course_organization(course_key)

        from openedx.core.djangoapps.video_config.sharing_sites import sharing_sites_info_for_video
        sharing_sites_info = sharing_sites_info_for_video(
            public_video_url,
            organization=organization
        )
        context['sharing_sites_info'] = sharing_sites_info

        return context

    def is_transcript_feedback_enabled(self, course_id: CourseKey) -> bool:
        """
        Check if transcript feedback is enabled for the course.
        """
        return TRANSCRIPT_FEEDBACK.is_enabled(course_id)

    def is_youtube_deprecated(self, course_id: CourseKey) -> bool:
        """
        Check if YouTube is deprecated for the course.
        """
        return DEPRECATE_YOUTUBE.is_enabled(course_id)

    def is_youtube_blocked_for_course(self, course_id: CourseKey) -> bool:
        """
        Check if YouTube is blocked for the course.
        """
        return CourseYoutubeBlockedFlag.feature_enabled(course_id)

    def is_hls_playback_enabled(self, course_id: CourseKey) -> bool:
        """
        Check if HLS playback is enabled for the course.
        """
        return HLSPlaybackEnabledFlag.feature_enabled(course_id)

    def get_transcript(
        self,
        video_block,
        lang: str | None = None,
        output_format: str = 'srt',
        youtube_id: str | None = None,
        is_bumper=False,
    ) -> tuple[bytes, str, str]:
        """
        Retrieve a transcript from the runtime's storage.

        Returns:
            tuple(bytes, str, str): transcript content, filename, and mimetype.

        Raises:
            TranscriptsGenerationException: If the transcript cannot be found or retrieved
            TranscriptNotFoundError: If the transcript cannot be found or retrieved
        """
        try:
            return get_transcript(video_block, lang, output_format, youtube_id, is_bumper)
        except NotFoundError as exc:
            raise TranscriptNotFoundError(
                f"Failed to get transcript: {exc}"
            ) from exc

    def available_translations(
        self,
        video_block,
        transcripts: dict[str, Any],
        verify_assets: bool | None = None,
        is_bumper: bool = False,
    ) -> list[str]:
        """
        Return a list of language codes for which we have transcripts.

        Arguments:
            video_block: The video XBlock instance
            transcripts (dict): A dict with all transcripts and a sub.
            verify_assets (boolean): If True, checks to ensure that the transcripts
                really exist in the contentstore. If False, we just look at the
                VideoBlock fields and do not query the contentstore. One reason
                we might do this is to avoid slamming contentstore() with queries
                when trying to make a listing of videos and their languages.

                Defaults to `not FALLBACK_TO_ENGLISH_TRANSCRIPTS`.

            is_bumper (boolean): If True, indicates this is a bumper video.

        Returns:
            list[str]: List of language codes for available transcripts.
        """
        translations = []
        if verify_assets is None:
            verify_assets = not settings.FEATURES.get('FALLBACK_TO_ENGLISH_TRANSCRIPTS')

        sub, other_langs = transcripts["sub"], transcripts["transcripts"]

        if verify_assets:
            all_langs = dict(**other_langs)
            if sub:
                all_langs.update({'en': sub})

            for language, filename in all_langs.items():
                try:
                    # for bumper videos, transcripts are stored in content store only
                    if is_bumper:
                        get_transcript_for_video(video_block.location, filename, filename, language)
                    else:
                        get_transcript(video_block, language)
                except NotFoundError:
                    continue

                translations.append(language)
        else:
            # If we're not verifying the assets, we just trust our field values
            translations = list(other_langs)
            if not translations or sub:
                translations += ['en']

        # to clean redundant language codes.
        return list(set(translations))

    def upload_transcript(
        self,
        *,
        video_block,
        language_code: str,
        new_language_code: str | None,
        transcript_file: File,
        edx_video_id: str | None,
    ) -> None:
        """
        Store a transcript, however the runtime prefers to.

        Mutates:
        * video_block.transcripts
        * video_block.edx_video_id, iff a new video is created in edx-val.

        Can raise:
        * UnicodeDecodeError
        * TranscriptsGenerationException
        """
        is_library = isinstance(video_block.usage_key.context_key, LibraryLocatorV2)
        content: bytes = transcript_file.read()
        if is_library:
            # Save transcript as static asset in openedx_content if is a library component
            filename = f'static/transcript-{new_language_code}.srt'
            add_library_block_static_asset_file(video_block.usage_key, filename, content)
        else:
            edx_video_id = clean_video_id(edx_video_id)
            if not edx_video_id:
                # Back-populate the video ID for an external video.
                # pylint: disable=attribute-defined-outside-init
                edx_video_id = create_external_video(display_name='external video')
                video_block.edx_video_id = edx_video_id
            filename = f'{edx_video_id}-{new_language_code}.srt'
            # Convert SRT transcript into an SJSON format and upload it to S3 if a course component
            sjson_subs = Transcript.convert(
                content=content.decode('utf-8'),
                input_format=Transcript.SRT,
                output_format=Transcript.SJSON
            ).encode()
            create_or_update_video_transcript(
                video_id=edx_video_id,
                language_code=language_code,
                metadata={
                    'file_format': Transcript.SJSON,
                    'language_code': new_language_code,
                },
                file_data=ContentFile(sjson_subs),
            )

        # If a new transcript is added, then both new_language_code and
        # language_code fields will have the same value.
        if language_code != new_language_code:
            video_block.transcripts.pop(language_code, None)
        video_block.transcripts[new_language_code] = filename

        if is_library:
            _save_transcript_field(video_block)

    def delete_transcript(
        self,
        *,
        video_block,
        edx_video_id: str | None,
        language_code: str,
    ) -> None:
        """
        Delete a transcript from the runtime's storage.
        """
        edx_video_id = clean_video_id(edx_video_id)
        if edx_video_id:
            delete_video_transcript(video_id=edx_video_id, language_code=language_code)
        if isinstance(video_block.context_key, LibraryLocatorV2):
            transcript_name = video_block.transcripts.pop(language_code, None)
            if transcript_name:
                delete_library_block_static_asset_file(video_block.usage_key, f"static/{transcript_name}")
                _save_transcript_field(video_block)
        else:
            if language_code == 'en':
                # remove any transcript file from content store for the video ids
                possible_sub_ids = [
                    video_block.sub,  # pylint: disable=access-member-before-definition
                    video_block.youtube_id_1_0
                ] + get_html5_ids(video_block.html5_sources)
                for sub_id in possible_sub_ids:
                    remove_subs_from_store(sub_id, video_block, language_code)
                # update metadata as `en` can also be present in `transcripts` field
                remove_subs_from_store(
                    video_block.transcripts.pop(language_code, None), video_block, language_code
                )
                # also empty `sub` field
                video_block.sub = ''  # pylint: disable=attribute-defined-outside-init
            else:
                remove_subs_from_store(
                    video_block.transcripts.pop(language_code, None), video_block, language_code
                )

    def handle_editor_saved(
        self,
        video_block,
        user_id: int,
        old_metadata: dict | None
    ):
        """
        Handle video block editor save operations.
        Used to update video values during save method from CMS.
        """
        metadata_was_changed_by_user = old_metadata != own_metadata(video_block)

        # There is an edge case when old_metadata and own_metadata are same and we are importing transcript from youtube
        # then there is a syncing issue where html5_subs are not syncing with youtube sub, We can make sync better by
        # checking if transcript is present for the video and if any html5_ids transcript is not present then trigger
        # the manage_video_subtitles_save to create the missing transcript with particular html5_id.
        if not metadata_was_changed_by_user and video_block.sub and hasattr(video_block, 'html5_sources'):
            html5_ids = get_html5_ids(video_block.html5_sources)
            for subs_id in html5_ids:
                try:
                    Transcript.asset(video_block.location, subs_id)
                except NotFoundError:
                    # If a transcript does not not exist with particular html5_id then there is no need to check other
                    # html5_ids because we have to create a new transcript with this missing html5_id by turning on
                    # metadata_was_changed_by_user flag.
                    metadata_was_changed_by_user = True
                    break

        if metadata_was_changed_by_user:
            video_block.edx_video_id = video_block.edx_video_id and video_block.edx_video_id.strip()

            # We want to override `youtube_id_1_0` with val youtube profile in the first place when someone adds/edits
            # an `edx_video_id` or its underlying YT val profile. Without this, override will only happen when a user
            # saves the video second time. This is because of the syncing of basic and advanced video settings which
            # also syncs val youtube id from basic tab's `Video Url` to advanced tab's `Youtube ID`.
            if video_block.edx_video_id and edxval_api:
                val_youtube_id = edxval_api.get_url_for_profile(video_block.edx_video_id, 'youtube')
                if val_youtube_id and video_block.youtube_id_1_0 != val_youtube_id:
                    video_block.youtube_id_1_0 = val_youtube_id

            manage_video_subtitles_save(
                video_block,
                user_id,
                old_metadata if old_metadata else None,
                generate_translation=True
            )


def _save_transcript_field(video_block):
    """
    Hacky workaround to ensure that transcript field is saved for openedx_content video blocks.

    It's not clear why this is necessary.
    """
    field = video_block.fields['transcripts']
    if video_block.transcripts:
        transcripts_copy = video_block.transcripts.copy()
        # Need to delete to overwrite, it's weird behavior,
        # but it only works like this.
        field.delete_from(video_block)
        field.write_to(video_block, transcripts_copy)
    else:
        field.delete_from(video_block)
