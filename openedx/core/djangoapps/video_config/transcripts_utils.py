"""
Utility functions for transcripts.
++++++++++++++++++++++++++++++++++
"""


import copy
import html
import logging
import os
import pathlib
import re
from functools import wraps

import requests
import simplejson as json
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.utils.translation import get_language_info
from lxml import etree
from opaque_keys.edx.keys import UsageKeyV2
from pysrt import SubRipFile, SubRipItem, SubRipTime
from pysrt.srtexc import Error
from opaque_keys.edx.locator import LibraryLocatorV2

from openedx.core.djangoapps.xblock.api import get_component_from_usage_key
from xmodule.contentstore.content import StaticContent
from xmodule.contentstore.django import contentstore
from xmodule.exceptions import NotFoundError

from xblocks_contrib.video.exceptions import TranscriptsGenerationException


try:
    from edxval import api as edxval_api
except ImportError:
    edxval_api = None


log = logging.getLogger(__name__)

NON_EXISTENT_TRANSCRIPT = 'non_existent_dummy_file_name'


class TranscriptException(Exception):
    pass


class GetTranscriptsFromYouTubeException(Exception):
    pass


class TranscriptsRequestValidationException(Exception):
    pass


def exception_decorator(func):
    """
    Generate NotFoundError for TranscriptsGenerationException, UnicodeDecodeError.

    Args:
    `func`: Input function

    Returns:
    'wrapper': Decorated function
    """
    @wraps(func)
    def wrapper(*args, **kwds):
        try:
            return func(*args, **kwds)
        except (TranscriptsGenerationException, UnicodeDecodeError) as ex:
            log.exception(str(ex))
            raise NotFoundError  # lint-amnesty, pylint: disable=raise-missing-from
    return wrapper


def generate_subs(speed, source_speed, source_subs):
    """
    Generate transcripts from one speed to another speed.

    Args:
    `speed`: float, for this speed subtitles will be generated,
    `source_speed`: float, speed of source_subs
    `source_subs`: dict, existing subtitles for speed `source_speed`.

    Returns:
    `subs`: dict, actual subtitles.
    """
    if speed == source_speed:
        return source_subs

    coefficient = 1.0 * speed / source_speed
    subs = {
        'start': [
            int(round(timestamp * coefficient)) for
            timestamp in source_subs['start']
        ],
        'end': [
            int(round(timestamp * coefficient)) for
            timestamp in source_subs['end']
        ],
        'text': source_subs['text']}
    return subs


def save_to_store(content, name, mime_type, location):
    """
    Save named content to store by location.

    Returns location of saved content.
    """
    content_location = Transcript.asset_location(location, name)
    content = StaticContent(content_location, name, mime_type, content)
    contentstore().save(content)
    return content_location


def save_subs_to_store(subs, subs_id, item, language='en'):
    """
    Save transcripts into `StaticContent`.

    Args:
    `subs_id`: str, subtitles id
    `item`: video block instance
    `language`: two chars str ('uk'), language of translation of transcripts

    Returns: location of saved subtitles.
    """
    filedata = json.dumps(subs, indent=2).encode('utf-8')
    filename = subs_filename(subs_id, language)
    return save_to_store(filedata, filename, 'application/json', item.location)


def get_transcript_link_from_youtube(youtube_id):
    """
    Get the link for YouTube transcript by parsing the source of the YouTube webpage.
    Inside the webpage, the details of the transcripts are located in a JSON object.
    After prettifying the object, it looks like:

    "captions": {
        "playerCaptionsTracklistRenderer": {
            "captionTracks": [
                {
                    "baseUrl": "...",
                    "name": {
                        "simpleText": "(Japanese in local language)"
                    },
                    "vssId": ".ja",
                    "languageCode": "ja",
                    "isTranslatable": true
                },
                {
                    "baseUrl": "...",
                    "name": {
                        "simpleText": "(French in local language)"
                    },
                    "vssId": ".fr",
                    "languageCode": "fr",
                    "isTranslatable": true
                },
                {
                    "baseUrl": "...",
                    "name": {
                        "simpleText": "(English in local language)"
                    },
                    "vssId": ".en",
                    "languageCode": "en",
                    "isTranslatable": true
                },
                ...
            ],
            "audioTracks": [...]
            "translationLanguages": ...
        },
        ...
    }

    So we use a regex to find the captionTracks JavaScript array, and then convert it
    to a Python dict and return the link for en caption
    """
    youtube_url_base = settings.YOUTUBE['TRANSCRIPTS']['YOUTUBE_URL_BASE']
    try:
        youtube_html = requests.get(f"{youtube_url_base}{youtube_id}")
        caption_re = settings.YOUTUBE['TRANSCRIPTS']['CAPTION_TRACKS_REGEX']
        caption_matched = re.search(caption_re, youtube_html.content.decode("utf-8"))
        if caption_matched:
            caption_tracks = json.loads(f'[{caption_matched.group("caption_tracks")}]')
            caption_links = {}
            for caption in caption_tracks:
                language_code = caption.get('languageCode', None)
                if language_code and not language_code == 'None':
                    link = caption.get("baseUrl")
                    caption_links[language_code] = link
            return None if not caption_links else caption_links
        return None
    except ConnectionError:
        return None


def get_transcript_links_from_youtube(youtube_id, settings, i18n, youtube_transcript_name=''):  # lint-amnesty, pylint: disable=redefined-outer-name
    """
    Gets transcripts from youtube for youtube_id.

    Parses only utf-8 encoded transcripts.
    Other encodings are not supported at the moment.

    Returns (status, transcripts): bool, dict.
    """
    _ = i18n.gettext
    transcript_links = get_transcript_link_from_youtube(youtube_id)

    if not transcript_links:
        msg = _("Can't get transcript link from Youtube for {youtube_id}.").format(
            youtube_id=youtube_id,
        )
        raise GetTranscriptsFromYouTubeException(msg)

    return transcript_links


def get_transcript_from_youtube(link, youtube_id, i18n):
    """
    Gets transcripts from youtube for youtube_id.

    Parses only utf-8 encoded transcripts.
    Other encodings are not supported at the moment.

    Returns (status, transcripts): bool, dict.
    """
    _ = i18n.gettext
    utf8_parser = etree.XMLParser(encoding='utf-8')
    data = requests.get(link)

    if data.status_code != 200 or not data.text:
        msg = _("Can't receive transcripts from Youtube for {youtube_id}. Status code: {status_code}.").format(
            youtube_id=youtube_id,
            status_code=data.status_code
        )
        raise GetTranscriptsFromYouTubeException(msg)

    sub_starts, sub_ends, sub_texts = [], [], []
    xmltree = etree.fromstring(data.content, parser=utf8_parser)
    for element in xmltree:
        if element.tag == "text":
            start = float(element.get("start"))
            duration = float(element.get("dur", 0))  # dur is not mandatory
            text = element.text
            end = start + duration

            if text:
                # Start and end should be ints representing the millisecond timestamp.
                sub_starts.append(int(start * 1000))
                sub_ends.append(int((end + 0.0001) * 1000))
                sub_texts.append(text.replace('\n', ' '))

    return {'start': sub_starts, 'end': sub_ends, 'text': sub_texts}


def download_youtube_subs(youtube_id, video_block, settings):  # lint-amnesty, pylint: disable=redefined-outer-name
    """
    Download transcripts from Youtube.

    Args:
        youtube_id: str, actual youtube_id of the video.
        video_block: video block instance.

    We save transcripts for 1.0 speed, as for other speed conversion is done on front-end.

    Returns:
        Serialized sjson transcript content, if transcripts were successfully downloaded and saved.

    Raises:
        GetTranscriptsFromYouTubeException, if fails.
    """
    i18n = video_block.runtime.service(video_block, "i18n")
    _ = i18n.gettext
    transcript_links = get_transcript_links_from_youtube(youtube_id, settings, i18n)
    subs = []
    for (language_code, link) in transcript_links.items():
        sub = get_transcript_from_youtube(link, youtube_id, i18n)
        subs.append([language_code, json.dumps(sub, indent=2)])
    return subs


def remove_subs_from_store(subs_id, item, lang='en'):
    """
    Remove from store, if transcripts content exists.
    """
    filename = subs_filename(subs_id, lang)
    Transcript.delete_asset(item.location, filename)


def generate_subs_from_source(speed_subs, subs_type, subs_filedata, block, language='en'):
    """Generate transcripts from source files (like SubRip format, etc.)
    and save them to assets for `item` module.
    We expect, that speed of source subs equal to 1

    :param speed_subs: dictionary {speed: sub_id, ...}
    :param subs_type: type of source subs: "srt", ...
    :param subs_filedata:unicode, content of source subs.
    :param block: course or block.
    :param language: str, language of translation of transcripts
    :returns: True, if all subs are generated and saved successfully.
    """
    _ = block.runtime.service(block, "i18n").gettext
    if subs_type.lower() != 'srt':
        raise TranscriptsGenerationException(_("We support only SubRip (*.srt) transcripts format."))
    try:
        srt_subs_obj = SubRipFile.from_string(subs_filedata)
    except Exception as ex:
        msg = _("Something wrong with SubRip transcripts file during parsing. Inner message is {error_message}").format(
            error_message=str(ex)
        )
        raise TranscriptsGenerationException(msg)  # lint-amnesty, pylint: disable=raise-missing-from
    if not srt_subs_obj:
        raise TranscriptsGenerationException(_("Something wrong with SubRip transcripts file during parsing."))

    sub_starts = []
    sub_ends = []
    sub_texts = []

    for sub in srt_subs_obj:
        sub_starts.append(sub.start.ordinal)
        sub_ends.append(sub.end.ordinal)
        sub_texts.append(sub.text.replace('\n', ' '))

    subs = {
        'start': sub_starts,
        'end': sub_ends,
        'text': sub_texts}

    for speed, subs_id in speed_subs.items():
        save_subs_to_store(
            generate_subs(speed, 1, subs),
            subs_id,
            block,
            language
        )

    return subs


def generate_srt_from_sjson(sjson_subs, speed):
    """Generate transcripts with speed = 1.0 from sjson to SubRip (*.srt).

    :param sjson_subs: "sjson" subs.
    :param speed: speed of `sjson_subs`.
    :returns: "srt" subs.
    """

    output = ''

    equal_len = len(sjson_subs['start']) == len(sjson_subs['end']) == len(sjson_subs['text'])
    if not equal_len:
        return output

    sjson_speed_1 = generate_subs(speed, 1, sjson_subs)

    for i in range(len(sjson_speed_1['start'])):
        item = SubRipItem(
            index=i,
            start=SubRipTime(milliseconds=sjson_speed_1['start'][i]),
            end=SubRipTime(milliseconds=sjson_speed_1['end'][i]),
            text=sjson_speed_1['text'][i]
        )
        output += (str(item))
        output += '\n'
    return output


def generate_sjson_from_srt(srt_subs):
    """
    Generate transcripts from sjson to SubRip (*.srt).

    Arguments:
        srt_subs(SubRip): "SRT" subs object

    Returns:
        Subs converted to "SJSON" format.
    """
    sub_starts = []
    sub_ends = []
    sub_texts = []
    for sub in srt_subs:
        sub_starts.append(sub.start.ordinal)
        sub_ends.append(sub.end.ordinal)
        sub_texts.append(sub.text.replace('\n', ' '))

    sjson_subs = {
        'start': sub_starts,
        'end': sub_ends,
        'text': sub_texts
    }
    return sjson_subs


def get_html5_ids(html5_sources):
    """
    Helper method to parse out an HTML5 source into the ideas
    NOTE: This assumes that '/' are not in the filename
    """
    html5_ids = [x.split('/')[-1].rsplit('.', 1)[0] for x in html5_sources]
    return html5_ids


def manage_video_subtitles_save(item, user_id, old_metadata=None, generate_translation=False):
    """
    Does some specific things, that can be done only on save.

    Video player item has some video fields: HTML5 ones and Youtube one.

    If value of `sub` field of `new_item` is cleared, transcripts should be removed.

    `item` is video block instance with updated values of fields,
    but actually have not been saved to store yet.

    `old_metadata` contains old values of XFields.

    # 1. convert /static/filename.srt  to filename.srt in self.transcripts.
    (it is done to allow user to enter both /static/filename.srt and filename.srt)

    # 2. Generate transcripts translation only  when user clicks `save` button, not while switching tabs.
    a) delete sjson translation for those languages, which were removed from `item.transcripts`.
        Note: we are not deleting old SRT files to give user more flexibility.
    b) For all SRT files in`item.transcripts` regenerate new SJSON files.
        (To avoid confusing situation if you attempt to correct a translation by uploading
        a new version of the SRT file with same name).
    """
    _ = item.runtime.service(item, "i18n").gettext

    # 1.
    if generate_translation:
        for lang, filename in item.transcripts.items():
            item.transcripts[lang] = os.path.split(filename)[-1]

    # 2.
    if generate_translation:
        old_langs = set(old_metadata.get('transcripts', {})) if old_metadata else set()
        new_langs = set(item.transcripts)

        html5_ids = get_html5_ids(item.html5_sources)
        possible_video_id_list = html5_ids + [item.youtube_id_1_0]

        for lang in old_langs.difference(new_langs):  # 3a
            for video_id in possible_video_id_list:
                if video_id:
                    remove_subs_from_store(video_id, item, lang)

        reraised_message = ''
        if not isinstance(item.usage_key.context_key, LibraryLocatorV2):
            for lang in new_langs:  # 3b
                try:
                    generate_sjson_for_all_speeds(
                        item,
                        item.transcripts[lang],
                        {speed: subs_id for subs_id, speed in youtube_speed_dict(item).items()},
                        lang,
                    )
                except TranscriptException:
                    pass
        if reraised_message:
            item.save_with_metadata(user_id)
            raise TranscriptException(reraised_message)


def youtube_speed_dict(item):
    """
    Returns {speed: youtube_ids, ...} dict for existing youtube_ids
    """
    yt_ids = [item.youtube_id_0_75, item.youtube_id_1_0, item.youtube_id_1_25, item.youtube_id_1_5]
    yt_speeds = [0.75, 1.00, 1.25, 1.50]
    youtube_ids = {p[0]: p[1] for p in zip(yt_ids, yt_speeds) if p[0]}
    return youtube_ids


def subs_filename(subs_id, lang='en'):
    """
    Generate proper filename for storage.
    """
    if lang == 'en':
        return f'subs_{subs_id}.srt.sjson'
    else:
        return f'{lang}_subs_{subs_id}.srt.sjson'


def generate_sjson_for_all_speeds(block, user_filename, result_subs_dict, lang):
    """
    Generates sjson from srt for given lang.
    """
    _ = block.runtime.service(block, "i18n").gettext

    try:
        srt_transcripts = contentstore().find(Transcript.asset_location(block.location, user_filename))
    except NotFoundError as ex:
        raise TranscriptException(_("{exception_message}: Can't find uploaded transcripts: {user_filename}").format(  # lint-amnesty, pylint: disable=raise-missing-from
            exception_message=str(ex),
            user_filename=user_filename
        ))

    if not lang:
        lang = block.transcript_language

    # Used utf-8-sig encoding type instead of utf-8 to remove BOM(Byte Order Mark), e.g. U+FEFF
    generate_subs_from_source(
        result_subs_dict,
        os.path.splitext(user_filename)[1][1:],
        srt_transcripts.data.decode('utf-8-sig'),
        block,
        lang
    )


def get_video_ids_info(edx_video_id, youtube_id_1_0, html5_sources):
    """
    Returns list internal or external video ids.

    Arguments:
        edx_video_id (unicode): edx_video_id
        youtube_id_1_0 (unicode): youtube id
        html5_sources (list): html5 video ids

    Returns:
        tuple: external or internal, video ids list
    """
    clean = lambda item: item.strip() if isinstance(item, str) else item
    external = not bool(clean(edx_video_id))

    video_ids = [edx_video_id, youtube_id_1_0] + get_html5_ids(html5_sources)

    # video_ids cleanup
    video_ids = [item for item in video_ids if bool(clean(item))]

    return external, video_ids


def clean_video_id(edx_video_id):
    """
    Cleans an edx video ID.

    Arguments:
        edx_video_id(unicode): edx-val's video identifier
    """
    return edx_video_id and edx_video_id.strip()


def get_video_transcript_content(edx_video_id, language_code):
    """
    Gets video transcript content, only if the corresponding feature flag is enabled for the given `course_id`.

    Arguments:
        language_code(unicode): Language code of the requested transcript
        edx_video_id(unicode): edx-val's video identifier

    Returns:
        A dict containing transcript's file name and its sjson content.
    """
    transcript = None
    edx_video_id = clean_video_id(edx_video_id)
    if edxval_api and edx_video_id:
        try:
            transcript = edxval_api.get_video_transcript_data(edx_video_id, language_code)
        except ValueError:
            log.exception(
                f"Error getting transcript from edx-val id: {edx_video_id}: language code {language_code}"
            )
            content = '{"start": [1],"end": [2],"text": ["An error occured obtaining the transcript."]}'
            transcript = dict(
                file_name='error-{edx_video_id}-{language_code}.srt',
                content=Transcript.convert(content, 'sjson', 'srt')
            )
    return transcript


def get_available_transcript_languages(edx_video_id):
    """
    Gets available transcript languages for a video.

    Arguments:
        edx_video_id(unicode): edx-val's video identifier

    Returns:
        A list containing distinct transcript language codes against all the passed video ids.
    """
    available_languages = []
    edx_video_id = clean_video_id(edx_video_id)
    if edxval_api and edx_video_id:
        available_languages = edxval_api.get_available_transcript_languages(video_id=edx_video_id)

    return available_languages


def convert_video_transcript(file_name, content, output_format):
    """
    Convert video transcript into desired format

    Arguments:
        file_name: name of transcript file along with its extension
        content: transcript content stream
        output_format: the format in which transcript will be converted

    Returns:
        A dict containing the new transcript filename and the content converted into desired format.
    """
    name_and_extension = os.path.splitext(file_name)
    basename, input_format = name_and_extension[0], name_and_extension[1][1:]
    filename = f'{basename}.{output_format}'
    converted_transcript = Transcript.convert(content, input_format=input_format, output_format=output_format)

    return dict(filename=filename, content=converted_transcript)


def clear_transcripts(block):
    """
    Deletes all transcripts of a video block from VAL
    """
    for language_code in block.transcripts.keys():
        edxval_api.delete_video_transcript(
            video_id=block.edx_video_id,
            language_code=language_code,
        )
    block.transcripts = {}


class Transcript:
    """
    Container for transcript methods.
    """
    SRT = 'srt'
    TXT = 'txt'
    SJSON = 'sjson'
    mime_types = {
        SRT: 'application/x-subrip; charset=utf-8',
        TXT: 'text/plain; charset=utf-8',
        SJSON: 'application/json',
    }

    @staticmethod
    def convert(content, input_format, output_format):
        """
        Convert transcript `content` from `input_format` to `output_format`.

        Accepted input formats: sjson, srt.
        Accepted output format: srt, txt, sjson.

        Raises:
            TranscriptsGenerationException: On parsing the invalid srt content during conversion from srt to sjson.
        """
        assert input_format in ('srt', 'sjson')
        assert output_format in ('txt', 'srt', 'sjson')

        if input_format == output_format:
            return content

        if input_format == 'srt':
            # Standardize content into bytes for later decoding.
            if isinstance(content, str):
                content = content.encode('utf-8')

            if output_format == 'txt':
                text = SubRipFile.from_string(content.decode('utf-8')).text
                return html.unescape(text)

            elif output_format == 'sjson':
                try:
                    srt_subs = SubRipFile.from_string(
                        # Skip byte order mark(BOM) character
                        content.decode('utf-8-sig'),
                        error_handling=SubRipFile.ERROR_RAISE
                    )
                except Error as ex:   # Base exception from pysrt
                    raise TranscriptsGenerationException(str(ex)) from ex

                return json.dumps(generate_sjson_from_srt(srt_subs))

        if input_format == 'sjson':
            # If the JSON file content is bytes, try UTF-8, then Latin-1
            if isinstance(content, bytes):
                try:
                    content_str = content.decode('utf-8')
                except UnicodeDecodeError:
                    content_str = content.decode('latin-1')
            else:
                content_str = content
            try:
                content_dict = json.loads(content_str)
            except ValueError:
                truncated = content_str[:100].strip()
                log.exception(
                    f"Failed to convert {input_format} to {output_format} for {repr(truncated)}..."
                )
                content_dict = {"start": [1], "end": [2], "text": ["An error occured obtaining the transcript."]}
            if output_format == 'txt':
                text = content_dict['text']
                text_without_none = [line if line else '' for line in text]
                return html.unescape("\n".join(text_without_none))
            elif output_format == 'srt':
                return generate_srt_from_sjson(content_dict, speed=1.0)

    @staticmethod
    def asset(location, subs_id, lang='en', filename=None):
        """
        Get asset from contentstore, asset location is built from subs_id and lang.

        `location` is block location.
        """
        # HACK Warning! this is temporary and will be removed once edx-val take over the
        # transcript module and contentstore will only function as fallback until all the
        # data is migrated to edx-val. It will be saving a contentstore hit for a hardcoded
        # dummy-non-existent-transcript name.
        if NON_EXISTENT_TRANSCRIPT in [subs_id, filename]:
            raise NotFoundError

        asset_filename = subs_filename(subs_id, lang) if not filename else filename
        return Transcript.get_asset(location, asset_filename)

    @staticmethod
    def get_asset(location, filename):
        """
        Return asset by location and filename.
        """
        return contentstore().find(Transcript.asset_location(location, filename))

    @staticmethod
    def asset_location(location, filename):
        """
        Return asset location. `location` is block location.
        """
        # If user transcript filename is empty, raise `TranscriptException` to avoid `InvalidKeyError`.
        if not filename:
            raise TranscriptException("Transcript not uploaded yet")
        return StaticContent.compute_location(location.course_key, filename)

    @staticmethod
    def delete_asset(location, filename):
        """
        Delete asset by location and filename.
        """
        try:
            contentstore().delete(Transcript.asset_location(location, filename))
            log.info("Transcript asset %s was removed from store.", filename)
        except NotFoundError:
            pass
        return StaticContent.compute_location(location.course_key, filename)


class VideoTranscriptsMixin:
    """Mixin class for transcript functionality.

    This is necessary for VideoBlock.
    """

    def get_default_transcript_language(self, transcripts, dest_lang=None):
        """
        Returns the default transcript language for this video block.

        Args:
            transcripts (dict): A dict with all transcripts and a sub.
            dest_lang (unicode): language coming from unit translation language selector.
        """
        sub, other_lang = transcripts["sub"], transcripts["transcripts"]

        if dest_lang:
            resolved_transcript_dest_lang = resolve_language_code_to_transcript_code(transcripts, dest_lang)
            if resolved_transcript_dest_lang:
                return resolved_transcript_dest_lang
            # language in plugin selector is english and empty transcripts or transcripts and sub exists
            if dest_lang == 'en' and (not other_lang or (other_lang and sub)):
                return 'en'

        if self.transcript_language in other_lang:
            return self.transcript_language

        if sub:
            return 'en'

        if len(other_lang) > 0:
            return sorted(other_lang)[0]

        return 'en'

    def get_transcripts_info(self, is_bumper=False):
        """
        Returns a transcript dictionary for the video.

        Arguments:
            is_bumper(bool): If True, the request is for the bumper transcripts
            include_val_transcripts(bool): If True, include edx-val transcripts as well
        """
        # TODO: This causes a circular import when imported at the top-level.
        #       This import will be removed as part of the VideoBlock extraction.
        #       https://github.com/openedx/edx-platform/issues/36282
        from xmodule.video_block.bumper_utils import get_bumper_settings

        if is_bumper:
            transcripts = copy.deepcopy(get_bumper_settings(self).get('transcripts', {}))
            sub = transcripts.pop("en", "")
        else:
            transcripts = self.transcripts if self.transcripts else {}
            sub = self.sub

        # Only attach transcripts that are not empty.
        transcripts = {
            language_code: transcript_file
            for language_code, transcript_file in transcripts.items() if transcript_file != ''
        }

        # bumper transcripts are stored in content store so we don't need to include val transcripts
        if not is_bumper:
            transcript_languages = get_available_transcript_languages(edx_video_id=self.edx_video_id)
            # HACK Warning! this is temporary and will be removed once edx-val take over the
            # transcript module and contentstore will only function as fallback until all the
            # data is migrated to edx-val.
            for language_code in transcript_languages:
                if language_code == 'en' and not sub:
                    sub = NON_EXISTENT_TRANSCRIPT
                elif not transcripts.get(language_code):
                    transcripts[language_code] = NON_EXISTENT_TRANSCRIPT

        return {
            "sub": sub,
            "transcripts": transcripts,
        }


@exception_decorator
def get_transcript_from_val(edx_video_id, lang=None, output_format=Transcript.SRT):
    """
    Get video transcript from edx-val.
    Arguments:
        edx_video_id (unicode): video identifier
        lang (unicode): transcript language
        output_format (unicode): transcript output format
    Returns:
        tuple containing content, filename, mimetype
    """
    transcript = get_video_transcript_content(edx_video_id, lang)
    if not transcript:
        raise NotFoundError(f'Transcript not found for {edx_video_id}, lang: {lang}')

    transcript_conversion_props = dict(transcript, output_format=output_format)
    transcript = convert_video_transcript(**transcript_conversion_props)
    filename = transcript['filename']
    content = transcript['content']
    mimetype = Transcript.mime_types[output_format]

    return content, filename, mimetype


def get_transcript_for_video(video_location, subs_id, file_name, language):
    """
    Get video transcript from content store. This is a lower level function and is used by
    `get_transcript_from_contentstore`. Prefer that function instead where possible. If you
    need to support getting transcripts from VAL or openedx_content as well, use the `get_transcript`
    function instead.

    NOTE: Transcripts can be searched from content store by two ways:
    1. by an id(a.k.a subs_id) which will be used to construct transcript filename
    2. by providing transcript filename

    Arguments:
        video_location (Locator): Video location
        subs_id (unicode): id for a transcript in content store
        file_name (unicode): file_name for a transcript in content store
        language (unicode): transcript language

    Returns:
        tuple containing transcript input_format, basename, content
    """
    try:
        if subs_id is None:
            raise NotFoundError
        content = Transcript.asset(video_location, subs_id, language).data.decode('utf-8')
        base_name = subs_id
        input_format = Transcript.SJSON
    except NotFoundError:
        content = Transcript.asset(video_location, None, language, file_name).data.decode('utf-8')
        base_name = os.path.splitext(file_name)[0]
        input_format = Transcript.SRT

    return input_format, base_name, content


@exception_decorator
def get_transcript_from_contentstore(video, language, output_format, transcripts_info, youtube_id=None):
    """
    Get video transcript from content store.

    Arguments:
        video (Video block): Video block
        language (unicode): transcript language
        output_format (unicode): transcript output format
        transcripts_info (dict): transcript info for a video
        youtube_id (unicode): youtube video id

    Returns:
        tuple containing content, filename, mimetype
    """
    input_format, base_name, transcript_content = None, None, None
    if output_format not in (Transcript.SRT, Transcript.SJSON, Transcript.TXT):
        raise NotFoundError(f'Invalid transcript format `{output_format}`')

    sub, other_languages = transcripts_info['sub'], transcripts_info['transcripts']
    transcripts = dict(other_languages)

    # this is sent in case of a translation dispatch and we need to use it as our subs_id.
    possible_sub_ids = [youtube_id, sub, video.youtube_id_1_0] + get_html5_ids(video.html5_sources)
    for sub_id in possible_sub_ids:
        try:
            transcripts['en'] = sub_id
            input_format, base_name, transcript_content = get_transcript_for_video(
                video.location,
                subs_id=sub_id,
                file_name=transcripts[language],
                language=language
            )
            break
        except (KeyError, NotFoundError):
            continue

    if transcript_content is None:
        raise NotFoundError('No transcript for `{lang}` language'.format(
            lang=language
        ))

    # add language prefix to transcript file only if language is not None
    language_prefix = f'{language}_' if language else ''
    transcript_name = f'{language_prefix}{base_name}.{output_format}'
    transcript_content = Transcript.convert(transcript_content, input_format=input_format, output_format=output_format)
    if not transcript_content.strip():
        raise NotFoundError('No transcript content')

    if youtube_id:
        youtube_ids = youtube_speed_dict(video)
        transcript_content = json.dumps(
            generate_subs(youtube_ids.get(youtube_id, 1), 1, json.loads(transcript_content))
        )

    return transcript_content, transcript_name, Transcript.mime_types[output_format]


def build_components_import_path(usage_key, file_path):
    """
    Build components import path
    """
    return f"components/{usage_key.block_type}/{usage_key.block_id}/{file_path}"


def get_transcript_from_openedx_content(video_block, language, output_format, transcripts_info):
    """
    Get video transcript from the openedx_content API.

    Limitation: This is only going to grab from the Draft version.

    openedx_content models a VideoBlock's data in a more generic thing it calls a
    Component. Each Component has its own virtual space for file-like data. The
    OLX for the VideoBlock itself is stored at the root of that space, as
    ``block.xml``. Static assets that are meant to be user-downloadable are
    placed in a `static/` directory for that Component, and this is where we
    expect to store transcript files.

    So if there is a ``video1-en.srt`` file for a particular VideoBlock, we
    expect that to be stored as ``static/video1-en.srt`` in the Component. Any
    other downloadable files would be here as well, such as thumbnails.

    Video XBlocks in Blockstore must set the 'transcripts' XBlock field to a
    JSON dictionary listing the filename of the transcript for each language:
        <video
            youtube_id_1_0="3_yD_cEKoCk"
            transcripts='{"en": "3_yD_cEKoCk-en.srt"}'
            display_name="Welcome Video with Transcript"
            download_track="true"
        />

      This method is tested in openedx/core/djangoapps/content_libraries/tests/test_static_assets.py

    Arguments:
        video_block (Video XBlock): The video XBlock
        language (str): transcript language
        output_format (str): transcript output format
        transcripts_info (dict): transcript info for a video, from video_block.get_transcripts_info()

    Returns:
        tuple containing content, filename, mimetype
    """
    usage_key = video_block.usage_key

    # Validate that the format is something we even support...
    if output_format not in (Transcript.SRT, Transcript.SJSON, Transcript.TXT):
        raise NotFoundError(f'Invalid transcript format `{output_format}`')

    # See if the requested language exists.
    transcripts = transcripts_info['transcripts']
    if language not in transcripts:
        raise NotFoundError(
            f"Video {usage_key} does not have a transcript file defined for the "
            f"'{language}' language in its OLX."
        )

    # Grab the underlying Component. There's no version parameter to this call,
    # so we're just going to grab the file associated with the latest draft
    # version for now.
    component = get_component_from_usage_key(usage_key)
    component_version = component.versioning.draft
    if not component_version:
        raise NotFoundError(
            f"No transcript for {usage_key} because Component {component.uuid} "
            "was soft-deleted."
        )

    file_path = pathlib.Path(f"static/{transcripts[language]}")
    if file_path.suffix != '.srt':
        # We want to standardize on .srt
        raise NotFoundError(
            "Video XBlocks in Content Libraries only support storing .srt "
            f"transcript files, but we tried to look up {file_path} for {usage_key}"
        )

    # TODO: There should be a openedx_content API call for this:
    try:
        media = (
            component_version
            .componentversionmedia_set
            .filter(media__has_file=True)
            .select_related('media')
            .get(key=file_path)
            .media
        )
        data = media.read_file().read()
    except ObjectDoesNotExist as exc:
        raise NotFoundError(
            f"No file {file_path} found for {usage_key} "
            f"(ComponentVersion {component_version.uuid})"
        ) from exc

    # Now convert the transcript data to the requested format:
    output_filename = f'{file_path.stem}.{output_format}'
    output_transcript = Transcript.convert(
        data.decode('utf-8'),
        input_format=Transcript.SRT,
        output_format=output_format,
    )
    if not output_transcript.strip():
        raise NotFoundError(
            f"Transcript file {file_path} found for {usage_key} "
            f"(ComponentVersion {component_version.uuid}), but it has no "
            "content or is malformed."
        )

    return output_transcript, output_filename, Transcript.mime_types[output_format]


def get_transcript(video, lang=None, output_format=Transcript.SRT, youtube_id=None, is_bumper=False):
    """
    Get video transcript from edx-val or content store.

    Arguments:
        video (Video block): Video block
        lang (unicode): transcript language
        output_format (unicode): transcript output format
        youtube_id (unicode): youtube video id

    Returns:
        tuple containing content, filename, mimetype
    """
    transcripts_info = video.get_transcripts_info(is_bumper)
    if is_bumper:
        return get_transcript_from_contentstore(
            video,
            lang,
            Transcript.SJSON,
            transcripts_info
        )
    if not lang:
        lang = video.get_default_transcript_language(transcripts_info)

    if isinstance(video.scope_ids.usage_id, UsageKeyV2):
        # This block is in openedx_content.
        return get_transcript_from_openedx_content(video, lang, output_format, transcripts_info)

    try:
        edx_video_id = clean_video_id(video.edx_video_id)
        if not edx_video_id:
            raise NotFoundError
        return get_transcript_from_val(edx_video_id, lang, output_format)
    except NotFoundError:
        return get_transcript_from_contentstore(
            video,
            lang,
            youtube_id=youtube_id,
            output_format=output_format,
            transcripts_info=transcripts_info
        )


def resolve_language_code_to_transcript_code(transcripts, dest_lang):
    """
    Attempts to match the requested dest lang with the existing transcript languages
    """
    sub, other_lang = transcripts["sub"], transcripts["transcripts"]
    # lang code exists in list of other transcript languages as-is
    if dest_lang in other_lang:
        return dest_lang

    # Language codes can be base languages, 2-3 characters, or they can include a
    # locale (`fr` for french, `fr-ca` for canadian french). Sometimes the part after the
    # dash is capitalized, sometimes it is not. Check both variants.
    dash_index = dest_lang.find('-')
    if dash_index >= 0:
        lowercase_dest_lang = dest_lang.lower()
        if lowercase_dest_lang in other_lang:
            log.debug("language code %s resolved to %s", dest_lang, lowercase_dest_lang)
            return lowercase_dest_lang

        generic_lang_code = lowercase_dest_lang[:dash_index]
        uppercase_dest_lang = generic_lang_code + lowercase_dest_lang[dash_index:].upper()
        if uppercase_dest_lang in other_lang:
            log.debug("language code %s resolved to %s", dest_lang, uppercase_dest_lang)
            return uppercase_dest_lang

        if generic_lang_code in other_lang:
            log.debug("language code %s resolved to generic %s", dest_lang, generic_lang_code)
            return generic_lang_code


def get_endonym_or_label(language_code):
    """
    Given a language code, attempt to look up the endonym, or local name, for that language
    """

    lowercase_code = language_code.lower()
    # LANGUAGE_DICT is an edx-configured mapping of language codes to endonym. It's a bit more
    # specific than the django utility, so try that first. All language codes in this dict will
    # be lowercase
    if local_name := settings.LANGUAGE_DICT.get(lowercase_code):
        return local_name

    # get_language_info attempts to look up language info in a hardcoded list in
    # django.conf.translations. It will do automatic "generalizations", i.e. it doesn't
    # have `es-419` so it then tries `es`. That's why we only do this after checking
    # LANGUAGE_DICT
    try:
        lang_info = get_language_info(language_code)
        return lang_info['name_local']
    except KeyError:
        pass

    # Last place to look is in settings.ALL_LANGUAGES. Ideally we find the actual code,
    # but also, check the 'generic' language. If even the generic language isn't found,
    # something is wrong, so log an error and throw an exception.
    first_dash_index = language_code.find('-')
    generic_code = None if first_dash_index == -1 else language_code[:first_dash_index]
    potential_generic_label = None
    for code, language_label in settings.ALL_LANGUAGES:
        # check for lowercase of the whole code, but as far as I can tell, the generic codes are
        # always lowercase
        if code in (language_code, lowercase_code):
            return language_label
        if generic_code and code == generic_code:
            potential_generic_label = language_label
        elif code > language_code:
            break
    if potential_generic_label:
        return potential_generic_label

    log.error("A label was requested for language code `%s` but the code is completely unknown", language_code)
    raise NotFoundError(f"Unknown language `{language_code}`")
