"""
Actions manager for transcripts ajax calls.
+++++++++++++++++++++++++++++++++++++++++++

Blocks do not support rollback (pressing "Cancel" button in Studio)
All user changes are saved immediately.
"""


import json
import logging
import os

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.http import Http404, HttpResponse
from django.utils.translation import gettext as _
from edxval.api import create_external_video, create_or_update_video_transcript
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import UsageKey, UsageKeyV2
from opaque_keys.edx.locator import LibraryLocatorV2

from cms.djangoapps.contentstore.video_storage_handlers import TranscriptProvider
from common.djangoapps.student.auth import has_course_author_access
from common.djangoapps.util.json_request import JsonResponse
from xmodule.contentstore.content import StaticContent  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.contentstore.django import contentstore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.exceptions import NotFoundError  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.exceptions import ItemNotFoundError  # lint-amnesty, pylint: disable=wrong-import-order
from openedx.core.djangoapps.video_config.transcripts_utils import (  # lint-amnesty, pylint: disable=wrong-import-order
    GetTranscriptsFromYouTubeException,
    Transcript,
    TranscriptsRequestValidationException,
    clean_video_id,
    download_youtube_subs,
    get_transcript,
    get_transcript_for_video,
    get_transcript_from_val,
    get_transcript_from_youtube,
    get_transcript_link_from_youtube,
    get_transcript_links_from_youtube,
)
from xblocks_contrib.video.exceptions import TranscriptsGenerationException
from openedx.core.djangoapps.content_libraries import api as lib_api
from openedx.core.djangoapps.xblock import api as xblock_api
from openedx.core.djangoapps.xblock.data import CheckPerm

__all__ = [
    'upload_transcripts',
    'download_transcripts',
    'check_transcripts',
    'choose_transcripts',
    'replace_transcripts',
    'rename_transcripts',
]

log = logging.getLogger(__name__)


def error_response(response, message, status_code=400):
    """
    Simplify similar actions: log message and return JsonResponse with message included in response.

    By default return 400 (Bad Request) Response.
    """
    log.debug(message)
    response['status'] = message
    return JsonResponse(response, status_code)


def link_video_to_component(video_component, user):
    """
    Links a VAL video to the video component.

    Arguments:
        video_component: video block.
        user: A requesting user.

    Returns:
        A cleaned Video ID.
    """
    edx_video_id = clean_video_id(video_component.edx_video_id)
    if not edx_video_id:
        edx_video_id = create_external_video(display_name='external video')
        video_component.edx_video_id = edx_video_id
        video_component.save_with_metadata(user.id)

    return edx_video_id


def save_video_transcript_in_openedx_content(
    usage_key,
    input_format,
    transcript_content,
    language_code
):
    """
    Saves a video transcript with the openedx_content API.

    openedx_content uses the standard `.srt` format for subtitles.
    Note: SJSON is an edx-specific format that we're trying to move away from,
    so for all new stuff related to openedx_content should only use `.srt`.

    Arguments:
        usage_key: UsageKey of the block
        input_format: Input transcript format for content being passed.
        transcript_content: Content of the transcript file
        language_code: transcript language code

    Returns:
        result: A boolean indicating whether the transcript was saved or not.
        video_key: Key used in video filename
    """
    try:
        srt_content = Transcript.convert(
            content=transcript_content,
            input_format=input_format,
            output_format=Transcript.SRT
        ).encode()

        filename = f"static/transcript-{language_code}.srt"
        lib_api.add_library_block_static_asset_file(
            usage_key,
            filename,
            srt_content,
        )
        result = True
    except (TranscriptsGenerationException, UnicodeDecodeError):
        result = False

    return result


def save_video_transcript(edx_video_id, input_format, transcript_content, language_code):
    """
    Saves a video transcript to the VAL and its content to the configured django storage(DS).

    Arguments:
        edx_video_id: A Video ID to associate the transcript.
        input_format: Input transcript format for content being passed.
        transcript_content: Content of the transcript file
        language_code: transcript language code

    Returns:
        A boolean indicating whether the transcript was saved or not.
    """
    try:
        # Convert the transcript into the 'sjson' and upload it to
        # configured transcript storage. For example, S3.
        sjson_subs = Transcript.convert(
            content=transcript_content,
            input_format=input_format,
            output_format=Transcript.SJSON
        ).encode()
        create_or_update_video_transcript(
            video_id=edx_video_id,
            language_code=language_code,
            metadata={
                'provider': TranscriptProvider.CUSTOM,
                'file_format': Transcript.SJSON,
                'language_code': language_code
            },
            file_data=ContentFile(sjson_subs),
        )

        result = True
    except (TranscriptsGenerationException, UnicodeDecodeError):
        result = False

    return result


def validate_video_block(request, locator):
    """
    Validates video block given its locator and request. Also, checks
    if requesting user has course authoring access.

    Arguments:
        request: WSGI request.
        locator: video locator.

    Returns:
        A tuple containing error(or None) and video block(i.e. if validation succeeds).

    Raises:
        PermissionDenied: if requesting user does not have access to author the video component.
    """
    error, item = None, None
    try:
        item = _get_item(request, {'locator': locator})
        if item.category != 'video':
            error = _('Transcripts are supported only for "video" blocks.')

    except (InvalidKeyError, ItemNotFoundError):
        error = _('Cannot find item by locator.')

    return error, item


def validate_transcript_upload_data(request):
    """
    Validates video transcript file.

    Arguments:
        request: A WSGI request's data part.

    Returns:
        Tuple containing an error and validated data
        If there is a validation error then, validated data will be empty.
    """
    error, validated_data = None, {}
    data, files = request.POST, request.FILES
    video_locator = data.get('locator')
    edx_video_id = data.get('edx_video_id')
    if not video_locator:
        error = _('Video locator is required.')
    elif 'transcript-file' not in files:
        error = _('A transcript file is required.')
    elif os.path.splitext(files['transcript-file'].name)[1][1:] != Transcript.SRT:
        error = _('This transcript file type is not supported.')
    elif 'edx_video_id' not in data:
        error = _('Video ID is required.')

    if not error:
        error, video = validate_video_block(request, video_locator)
        if not error:
            validated_data.update({
                'video': video,
                'edx_video_id': clean_video_id(edx_video_id) or clean_video_id(video.edx_video_id),
                'transcript_file': files['transcript-file']
            })

    return error, validated_data


@login_required
def upload_transcripts(request):
    """
    Upload transcripts for current block.

    returns: response dict::

        status: 'Success' and HTTP 200 or 'Error' and HTTP 400.
        subs: Value of uploaded and saved html5 sub field in video item.
    """
    error, validated_data = validate_transcript_upload_data(request)
    if error:
        response = JsonResponse({'status': error}, status=400)
    else:
        video = validated_data['video']
        edx_video_id = validated_data['edx_video_id']
        transcript_file = validated_data['transcript_file']
        # check if we need to create an external VAL video to associate the transcript
        # and save its ID on the video component.
        if not edx_video_id:
            edx_video_id = create_external_video(display_name='external video')
            video.edx_video_id = edx_video_id
            video.save_with_metadata(request.user.id)

        response = JsonResponse({'edx_video_id': edx_video_id, 'status': 'Success'}, status=200)

        try:
            # Convert 'srt' transcript into the 'sjson' and upload it to
            # configured transcript storage. For example, S3.
            sjson_subs = Transcript.convert(
                content=transcript_file.read().decode('utf-8'),
                input_format=Transcript.SRT,
                output_format=Transcript.SJSON
            ).encode()
            transcript_created = create_or_update_video_transcript(
                video_id=edx_video_id,
                language_code='en',
                metadata={
                    'provider': TranscriptProvider.CUSTOM,
                    'file_format': Transcript.SJSON,
                    'language_code': 'en'
                },
                file_data=ContentFile(sjson_subs),
            )

            video.transcripts['en'] = f"{edx_video_id}-en.srt"
            video.save_with_metadata(request.user.id)
            if transcript_created is None:
                response = JsonResponse({'status': 'Invalid Video ID'}, status=400)

        except (TranscriptsGenerationException, UnicodeDecodeError):

            response = JsonResponse({
                'status': _('There is a problem with this transcript file. Try to upload a different file.')
            }, status=400)

    return response


@login_required
def download_transcripts(request):
    """
    Passes to user requested transcripts file.

    Raises Http404 if unsuccessful.
    """
    error, video = validate_video_block(request, locator=request.GET.get('locator'))
    if error:
        raise Http404

    try:
        content, filename, mimetype = get_transcript(video, lang='en')
    except NotFoundError:
        raise Http404  # lint-amnesty, pylint: disable=raise-missing-from

    # Construct an HTTP response
    response = HttpResponse(content, content_type=mimetype)
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
def check_transcripts(request):  # lint-amnesty, pylint: disable=too-many-statements
    """
    Check state of transcripts availability.

    request.GET['data'] has key `videos`, which can contain any of the following::

        [
            {u'type': u'youtube', u'video': u'OEoXaMPEzfM', u'mode': u'youtube'},
            {u'type': u'html5',    u'video': u'video1',             u'mode': u'mp4'}
            {u'type': u'html5',    u'video': u'video2',             u'mode': u'webm'}
        ]
        `type` is youtube or html5
        `video` is html5 or youtube video_id
        `mode` is youtube, ,p4 or webm

    Returns transcripts_presence dict::

        html5_local: list of html5 ids, if subtitles exist locally for them;
        is_youtube_mode: bool, if we have youtube_id, and as youtube mode is of higher priority, reflect this with flag;
        youtube_local: bool, if youtube transcripts exist locally;
        youtube_server: bool, if youtube transcripts exist on server;
        youtube_diff: bool, if youtube transcripts exist on youtube server, and are different from local youtube ones;
        current_item_subs: string, value of item.sub field;
        status: string, 'Error' or 'Success';
        subs: string, new value of item.sub field, that should be set in module;
        command: string, action to front-end what to do and what to show to user.
    """
    transcripts_presence = {
        'html5_local': [],
        'html5_equal': False,
        'is_youtube_mode': False,
        'youtube_local': False,
        'youtube_server': False,
        'youtube_diff': True,
        'current_item_subs': None,
        'status': 'Error',
    }

    try:
        __, videos, item = _validate_transcripts_data(request)
    except TranscriptsRequestValidationException as e:
        return error_response(transcripts_presence, str(e))

    transcripts_presence['status'] = 'Success'

    try:
        edx_video_id = clean_video_id(videos.get('edx_video_id'))
        get_transcript_from_val(edx_video_id=edx_video_id, lang='en')
        command = 'found'
    except NotFoundError:
        # Check for youtube transcripts presence
        youtube_id = videos.get('youtube', None)
        if youtube_id:
            _check_youtube_transcripts(
                transcripts_presence,
                youtube_id,
                item,
            )

        if not isinstance(item.usage_key, UsageKeyV2):
            filename = f'subs_{item.sub}.srt.sjson'
            content_location = StaticContent.compute_location(item.location.course_key, filename)
            try:
                contentstore().find(content_location).data.decode('utf-8')
                transcripts_presence['current_item_subs'] = item.sub
            except NotFoundError:
                pass

            # Check for html5 local transcripts presence
            html5_subs = []
            for html5_id in videos['html5']:
                filename = f'subs_{html5_id}.srt.sjson'
                content_location = StaticContent.compute_location(item.location.course_key, filename)
                try:
                    html5_subs.append(contentstore().find(content_location).data)
                    transcripts_presence['html5_local'].append(html5_id)
                except NotFoundError:
                    log.debug("Can't find transcripts in storage for non-youtube video_id: %s", html5_id)
                if len(html5_subs) == 2:  # check html5 transcripts for equality
                    transcripts_presence['html5_equal'] = (
                        json.loads(html5_subs[0].decode('utf-8')) == json.loads(html5_subs[1].decode('utf-8'))
                    )

        command, __ = _transcripts_logic(transcripts_presence, videos)

    transcripts_presence.update({'command': command})
    return JsonResponse(transcripts_presence)


def _check_youtube_transcripts(transcripts_presence, youtube_id, item):
    """
    Check for youtube transcripts presence
    """
    transcripts_presence['is_youtube_mode'] = True

    if get_transcript_link_from_youtube(youtube_id):
        transcripts_presence['youtube_server'] = True

    if not isinstance(item.usage_key, UsageKeyV2):
        # youtube local
        filename = f'subs_{youtube_id}.srt.sjson'
        content_location = StaticContent.compute_location(item.location.course_key, filename)
        try:
            local_transcripts = contentstore().find(content_location).data.decode('utf-8')
            transcripts_presence['youtube_local'] = True
        except NotFoundError:
            log.debug("Can't find transcripts in storage for youtube id: %s", youtube_id)

    # check youtube local and server transcripts for equality
    if transcripts_presence['youtube_server'] and transcripts_presence['youtube_local']:
        try:
            transcript_links = get_transcript_links_from_youtube(
                youtube_id,
                settings,
                item.runtime.service(item, "i18n")
            )
            for (_, link) in transcript_links.items():
                youtube_server_subs = get_transcript_from_youtube(
                    link, youtube_id, item.runtime.service(item, "i18n")
                )
                if json.loads(local_transcripts) == youtube_server_subs:  # check transcripts for equality
                    transcripts_presence['youtube_diff'] = False
        except GetTranscriptsFromYouTubeException:
            pass


def _transcripts_logic(transcripts_presence, videos):
    """
    By `transcripts_presence` content, figure what show to user:

    returns: `command` and `subs`.

    `command`: string,  action to front-end what to do and what show to user.
    `subs`: string, new value of item.sub field, that should be set in module.

    `command` is one of::

        replace: replace local youtube subtitles with server one's
        found: subtitles are found
        import: import subtitles from youtube server
        choose: choose one from two html5 subtitles
        not found: subtitles are not found
    """
    command = None

    # new value of item.sub field, that should be set in module.
    subs = ''

    # youtube transcripts are of high priority than html5 by design
    if (
            transcripts_presence['youtube_diff'] and
            transcripts_presence['youtube_local'] and
            transcripts_presence['youtube_server']):  # youtube server and local exist
        command = 'replace'
        subs = videos['youtube']
    elif transcripts_presence['youtube_local']:  # only youtube local exist
        command = 'found'
        subs = videos['youtube']
    elif transcripts_presence['youtube_server']:  # only youtube server exist
        command = 'import'
    else:  # html5 part
        if transcripts_presence['html5_local']:  # can be 1 or 2 html5 videos
            if len(transcripts_presence['html5_local']) == 1 or transcripts_presence['html5_equal']:
                command = 'found'
                subs = transcripts_presence['html5_local'][0]
            else:
                command = 'choose'
                subs = transcripts_presence['html5_local'][0]
        else:  # html5 source have no subtitles
            # check if item sub has subtitles
            if transcripts_presence['current_item_subs'] and not transcripts_presence['is_youtube_mode']:
                log.debug("Command is use existing %s subs", transcripts_presence['current_item_subs'])
                command = 'use_existing'
            else:
                command = 'not_found'
    log.debug(
        "Resulted command: %s, current transcripts: %s, youtube mode: %s",
        command,
        transcripts_presence['current_item_subs'],
        transcripts_presence['is_youtube_mode']
    )
    return command, subs


def _validate_transcripts_data(request):
    """
    Validates, that request contains all proper data for transcripts processing.

    Returns tuple of 3 elements::

        data: dict, loaded json from request,
        videos: parsed `data` to useful format,
        item:  video item from storage or library

    Raises `TranscriptsRequestValidationException` if validation is unsuccessful
    or `PermissionDenied` if user has no access.
    """
    data = json.loads(request.GET.get('data', '{}'))
    if not data:
        raise TranscriptsRequestValidationException(_('Incoming video data is empty.'))
    try:
        item = _get_item(request, data)
    except (InvalidKeyError, ItemNotFoundError):
        raise TranscriptsRequestValidationException(_("Can't find item by locator."))  # lint-amnesty, pylint: disable=raise-missing-from

    if item.category != 'video':
        raise TranscriptsRequestValidationException(_('Transcripts are supported only for "video" blocks.'))

    # parse data form request.GET.['data']['video'] to useful format
    videos = {'youtube': '', 'html5': {}}
    for video_data in data.get('videos'):
        if video_data['type'] == 'youtube':
            videos['youtube'] = video_data['video']
        elif video_data['type'] == 'edx_video_id':
            if clean_video_id(video_data['video']):
                videos['edx_video_id'] = video_data['video']
        else:  # do not add same html5 videos
            if videos['html5'].get('video') != video_data['video']:
                videos['html5'][video_data['video']] = video_data['mode']

    return data, videos, item


def validate_transcripts_request(request, include_yt=False, include_html5=False):
    """
    Validates transcript handler's request.

    NOTE: This is one central validation flow for `choose_transcripts`,
    `check_transcripts` and `replace_transcripts` handlers.

    Returns:
        A tuple containing:
            1. An error message in case of validation failure.
            2. validated video data
    """
    error = None
    validated_data = {'video': None, 'youtube': '', 'html5': {}}
    # Loads the request data
    data = json.loads(request.GET.get('data', '{}'))
    if not data:
        error = _('Incoming video data is empty.')
    else:
        error, video = validate_video_block(request, locator=data.get('locator'))
        if not error:
            validated_data.update({'video': video})

    videos = data.get('videos', [])
    if include_yt:
        validated_data.update({
            video['type']: video['video']
            for video in videos
            if video['type'] == 'youtube'
        })

    if include_html5:
        validated_data['chosen_html5_id'] = data.get('html5_id')
        validated_data['html5'] = {
            video['video']: video['mode']
            for video in videos
            if video['type'] != 'youtube'
        }
    return error, validated_data


@login_required
def choose_transcripts(request):
    """
    Create/Update edx transcript in DS with chosen html5 subtitles from contentstore.

    Returns:
        status `Success` and resulted `edx_video_id` value
        Or error in case of validation failures.
    """
    error, validated_data = validate_transcripts_request(request, include_html5=True)
    edx_video_id = None
    if error:
        response = error_response({}, error)
    else:
        # 1. Retrieve transcript file for `chosen_html5_id` from contentstore.
        try:
            video = validated_data['video']
            chosen_html5_id = validated_data['chosen_html5_id']
            input_format, __, transcript_content = get_transcript_for_video(
                video.location,
                subs_id=chosen_html5_id,
                file_name=chosen_html5_id,
                language='en'
            )
        except NotFoundError:
            return error_response({}, _('No such transcript.'))

        # 2. Link a video to video component if its not already linked to one.
        if not isinstance(video.usage_key.context_key, LibraryLocatorV2):
            edx_video_id = link_video_to_component(video, request.user)

        # 3. Upload the retrieved transcript to DS for the linked video ID.
        if isinstance(video.usage_key.context_key, LibraryLocatorV2):
            success = save_video_transcript_in_openedx_content(
                video.usage_key,
                input_format,
                transcript_content,
                language_code='en',
            )
        else:
            success = save_video_transcript(
                edx_video_id,
                input_format,
                transcript_content,
                language_code='en',
            )
        if success:
            response = JsonResponse({'edx_video_id': edx_video_id, 'status': 'Success'}, status=200)
        else:
            response = error_response({}, _('There is a problem with the chosen transcript file.'))

    return response


@login_required
def rename_transcripts(request):
    """
    Copies existing transcript on video component's `sub`(from contentstore) into the
    DS for a video.

    Returns:
        status `Success` and resulted `edx_video_id` value
        Or error in case of validation failures.
    """
    error, validated_data = validate_transcripts_request(request)
    edx_video_id = None
    if error:
        response = error_response({}, error)
    else:
        # 1. Retrieve transcript file for `video.sub` from contentstore.
        try:
            video = validated_data['video']
            input_format, __, transcript_content = get_transcript_for_video(
                video.location,
                subs_id=video.sub,
                file_name=video.sub,
                language='en'
            )
        except NotFoundError:
            return error_response({}, _('No such transcript.'))

        # 2. Link a video to video component if its not already linked to one.
        if not isinstance(video.usage_key.context_key, LibraryLocatorV2):
            edx_video_id = link_video_to_component(video, request.user)

        # 3. Upload the retrieved transcript to DS for the linked video ID.
        if isinstance(video.usage_key.context_key, LibraryLocatorV2):
            success = save_video_transcript_in_openedx_content(
                video.usage_key,
                input_format,
                transcript_content,
                language_code='en',
            )
        else:
            success = save_video_transcript(
                edx_video_id,
                input_format,
                transcript_content,
                language_code='en',
            )
        if success:
            response = JsonResponse({'edx_video_id': edx_video_id, 'status': 'Success'}, status=200)
        else:
            response = error_response(
                {}, _('There is a problem with the existing transcript file. Please upload a different file.')
            )

    return response


@login_required
def replace_transcripts(request):
    """
    Downloads subtitles from youtube and replaces edx transcripts in DS with youtube ones.

    Returns:
        status `Success` and resulted `edx_video_id` value
        Or error on validation failures.
    """
    error, validated_data = validate_transcripts_request(request, include_yt=True)
    youtube_id = validated_data['youtube']
    edx_video_id = None
    if error:
        response = error_response({}, error)
    elif not youtube_id:
        response = error_response({}, _('YouTube ID is required.'))
    else:
        # 1. Download transcript from YouTube.
        try:
            video = validated_data['video']
            transcript_content = download_youtube_subs(youtube_id, video, settings)
        except GetTranscriptsFromYouTubeException as e:
            return error_response({}, str(e))

        # 2. Link a video to video component if its not already linked to one.
        if not isinstance(video.usage_key.context_key, LibraryLocatorV2):
            edx_video_id = link_video_to_component(video, request.user)

        # 3. Upload YT transcript to DS for the linked video ID.
        success = True
        for transcript in transcript_content:
            [language_code, json_content] = transcript
            if isinstance(video.usage_key.context_key, LibraryLocatorV2):
                success = save_video_transcript_in_openedx_content(
                    video.usage_key,
                    Transcript.SJSON,
                    json_content,
                    language_code,
                )
                filename = f"transcript-{language_code}.srt"
            else:
                success = save_video_transcript(
                    edx_video_id,
                    Transcript.SJSON,
                    json_content,
                    language_code,
                )
                filename = f"{edx_video_id}-{language_code}.srt"
            if not success:
                break
            video.transcripts[language_code] = filename
        if success:
            video.save()
            response = JsonResponse({'edx_video_id': edx_video_id, 'status': 'Success'}, status=200)
        else:
            response = error_response({}, _('There is a problem with the YouTube transcript file.'))

    return response


def _get_item(request, data):
    """
    Obtains from 'data' the locator for an item.
    Next, gets that item from the modulestore (allowing any errors to raise up)
    or from library API if is a library content.
    Finally, verifies that the user has access to the item.

    Returns the item and a boolean if is a library content.
    """
    usage_key = UsageKey.from_string(data.get('locator'))

    context_key = usage_key.context_key
    if not context_key.is_course:
        if isinstance(context_key, LibraryLocatorV2):
            return xblock_api.load_block(
                usage_key,
                request.user,
                check_permission=CheckPerm.CAN_EDIT,
            )
        raise TranscriptsRequestValidationException(_('Transcripts are not yet supported for this type of block'))
    # This is placed before has_course_author_access() to validate the location,
    # because has_course_author_access() raises error if location is invalid.
    item = modulestore().get_item(usage_key)

    # use the item's course_key, because the usage_key might not have the run
    if not has_course_author_access(request.user, item.location.course_key):
        raise PermissionDenied()

    return item
