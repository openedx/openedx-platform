"""
Handlers for video block.

StudentViewHandlers are handlers for video block instance.
StudioViewHandlers are handlers for video descriptor instance.
"""


import json
import logging
import math

from django.utils.timezone import now
from opaque_keys.edx.locator import CourseLocator
from webob import Response
from xblock.core import XBlock
from xblock.exceptions import JsonHandlerError
from xblock.fields import RelativeTime

from xmodule.exceptions import NotFoundError

from openedx.core.djangoapps.video_config.transcripts_utils import (
    Transcript,
    clean_video_id,
    subs_filename,
)
from xblocks_contrib.video.exceptions import (
    TranscriptsGenerationException,
    TranscriptNotFoundError,
)

log = logging.getLogger(__name__)


def get_transcript(
    video_block,
    lang: str | None = None,
    output_format: str = 'srt',
    youtube_id: str | None = None,
    is_bumper: bool = False,
) -> tuple[bytes, str, str]:
    """
    Retrieve a transcript using a video block's configuration service.

    Returns:
        tuple(bytes, str, str): transcript content, filename, and mimetype.

    Raises:
        Exception: If the video config service is not available or the transcript cannot be retrieved.
    """
    video_config_service = video_block.runtime.service(video_block, 'video_config')
    if not video_config_service:
        raise Exception("Video config service not found")
    return video_config_service.get_transcript(video_block, lang, output_format, youtube_id, is_bumper)


# Disable no-member warning:
# pylint: disable=no-member

def to_boolean(value):
    """
    Convert a value from a GET or POST request parameter to a bool
    """
    if isinstance(value, bytes):
        value = value.decode('ascii', errors='replace')
    if isinstance(value, str):
        return value.lower() == 'true'
    else:
        return bool(value)


class VideoStudentViewHandlers:
    """
    Handlers for video block instance.
    """
    global_speed = None
    transcript_language = None

    def handle_ajax(self, dispatch, data):
        """
        Update values of xfields, that were changed by student.
        """
        accepted_keys = [
            'speed', 'auto_advance', 'saved_video_position', 'transcript_language',
            'transcript_download_format', 'youtube_is_available',
            'bumper_last_view_date', 'bumper_do_not_show_again'
        ]

        conversions = {
            'speed': json.loads,
            'auto_advance': json.loads,
            'saved_video_position': RelativeTime.isotime_to_timedelta,
            'youtube_is_available': json.loads,
            'bumper_last_view_date': to_boolean,
            'bumper_do_not_show_again': to_boolean,
        }

        if dispatch == 'save_user_state':
            for key in data:
                if key in accepted_keys:
                    if key in conversions:
                        value = conversions[key](data[key])
                    else:
                        value = data[key]

                    if key == 'bumper_last_view_date':
                        value = now()

                    if key == 'speed' and math.isnan(value):
                        message = f"Invalid speed value {value}, must be a float."
                        log.warning(message)
                        return json.dumps({'success': False, 'error': message})

                    setattr(self, key, value)

                    if key == 'speed':
                        self.global_speed = self.speed

            return json.dumps({'success': True})

        log.debug(f"GET {data}")
        log.debug(f"DISPATCH {dispatch}")

        raise NotFoundError('Unexpected dispatch type')

    def get_static_transcript(self, request, transcripts):
        """
        Courses that are imported with the --nostatic flag do not show
        transcripts/captions properly even if those captions are stored inside
        their static folder. This adds a last resort method of redirecting to
        the static asset path of the course if the transcript can't be found
        inside the contentstore and the course has the static_asset_path field
        set.

        transcripts (dict): A dict with all transcripts and a sub.
        """
        response = Response(status=404)
        # Only do redirect for English
        if not self.transcript_language == 'en':
            return response

        # If this video lives in library, the code below is not relevant and will error.
        if not isinstance(self.course_id, CourseLocator):
            return response

        video_id = request.GET.get('videoId', None)
        if video_id:
            transcript_name = video_id
        else:
            transcript_name = transcripts["sub"]

        if transcript_name:
            # Get the asset path for course
            asset_path = None
            course = self.runtime.modulestore.get_course(self.course_id)
            if course.static_asset_path:
                asset_path = course.static_asset_path
            else:
                # It seems static_asset_path is not set in any XMLModuleStore courses.
                asset_path = getattr(course, 'data_dir', '')

            if asset_path:
                response = Response(
                    status=307,
                    location='/static/{}/{}'.format(
                        asset_path,
                        subs_filename(transcript_name, self.transcript_language)
                    )
                )
        return response

    @XBlock.json_handler
    def publish_completion(self, data, dispatch):  # pylint: disable=unused-argument
        """
        Entry point for completion for student_view.

        Parameters:
            data: JSON dict:
                key: "completion"
                value: float in range [0.0, 1.0]

            dispatch: Ignored.
        Return value: JSON response (200 on success, 400 for malformed data)
        """
        completion_service = self.runtime.service(self, 'completion')
        if completion_service is None:
            raise JsonHandlerError(500, "No completion service found")
        if not completion_service.completion_tracking_enabled():
            raise JsonHandlerError(404, "Completion tracking is not enabled and API calls are unexpected")
        if not isinstance(data['completion'], (int, float)):
            message = "Invalid completion value {}. Must be a float in range [0.0, 1.0]"
            raise JsonHandlerError(400, message.format(data['completion']))
        if not 0.0 <= data['completion'] <= 1.0:
            message = "Invalid completion value {}. Must be in range [0.0, 1.0]"
            raise JsonHandlerError(400, message.format(data['completion']))
        self.runtime.publish(self, "completion", data)
        return {"result": "ok"}

    @staticmethod
    def make_transcript_http_response(content, filename, language, content_type, add_attachment_header=True):
        """
        Construct `Response` object.

        Arguments:
            content (unicode): transcript content
            filename (unicode): transcript filename
            language (unicode): transcript language
            mimetype (unicode): transcript content type
            add_attachment_header (bool): whether to add attachment header or not
        """
        headerlist = [
            ('Content-Language', language),
        ]

        if add_attachment_header:
            headerlist.append(
                (
                    'Content-Disposition',
                    f'attachment; filename="{filename}"'
                )
            )

        response = Response(
            content,
            headerlist=headerlist,
            charset='utf8'
        )
        response.content_type = content_type

        return response

    @XBlock.handler
    def transcript(self, request, dispatch):
        """
        Entry point for transcript handlers for student_view.

        Request GET contains:
            (optional) `videoId` for `translation` dispatch.
            `is_bumper=1` flag for bumper case.

        Dispatches, (HTTP GET):
            /translation/[language_id]
            /download
            /available_translations/

        Explanations:
            `download`: returns SRT or TXT file.
            `translation`: depends on HTTP methods:
                    Provide translation for requested language, SJSON format is sent back on success,
                    Proper language_id should be in url.
            `available_translations`:
                    Returns list of languages, for which transcript files exist.
                    For 'en' check if SJSON exists. For non-`en` check if SRT file exists.
        """
        is_bumper = request.GET.get('is_bumper', False)
        transcripts = self.get_transcripts_info(is_bumper)

        if dispatch.startswith('translation'):
            language = dispatch.replace('translation', '').strip('/')

            # Because scrapers hit video blocks, verify that a user exists.
            # use the _request attr to get the django request object.
            if not request._request.user:  # pylint: disable=protected-access
                log.info("Transcript: user must be logged or public view enabled to get transcript")
                return Response(status=403)

            if not language:
                log.info("Invalid /translation request: no language.")
                return Response(status=400)

            if language not in ['en'] + list(transcripts["transcripts"].keys()):
                log.info("Video: transcript facilities are not available for given language.")
                return Response(status=404)

            if language != self.transcript_language:
                self.transcript_language = language

            try:
                youtube_id = None if is_bumper else request.GET.get('videoId')
                content, filename, mimetype = get_transcript(
                    self,
                    lang=self.transcript_language,
                    output_format=Transcript.SJSON,
                    youtube_id=youtube_id,
                    is_bumper=is_bumper
                )
                response = self.make_transcript_http_response(
                    content,
                    filename,
                    self.transcript_language,
                    mimetype,
                    add_attachment_header=False
                )
            except TranscriptNotFoundError as exc:
                edx_video_id = clean_video_id(self.edx_video_id)
                log.warning(
                    '[Translation Dispatch] %s: %s',
                    self.location,
                    exc if is_bumper else f'Transcript not found for {edx_video_id}, lang: {self.transcript_language}',
                )
                response = self.get_static_transcript(request, transcripts)

        elif dispatch == 'download':
            lang = request.GET.get('lang', None)

            try:
                content, filename, mimetype = get_transcript(self, lang, output_format=self.transcript_download_format)
            except TranscriptNotFoundError:
                return Response(status=404)

            response = self.make_transcript_http_response(
                content,
                filename,
                self.transcript_language,
                mimetype
            )
        elif dispatch.startswith('available_translations'):
            video_config_service = self.runtime.service(self, 'video_config')
            if not video_config_service:
                return Response(status=404)
            available_translations = video_config_service.available_translations(
                self,
                transcripts,
                verify_assets=True,
                is_bumper=is_bumper
            )
            if available_translations:
                response = Response(json.dumps(available_translations))
                response.content_type = 'application/json'
            else:
                response = Response(status=404)
        else:  # unknown dispatch
            log.debug("Dispatch is not allowed")
            response = Response(status=404)

        return response

    @XBlock.handler
    def student_view_user_state(self, request, suffix=''):  # lint-amnesty, pylint: disable=unused-argument
        """
        Endpoint to get user-specific state, like current position and playback speed,
        without rendering the full student_view HTML. This is similar to student_view_state,
        but that one cannot contain user-specific info.
        """
        view_state = self.student_view_data()
        view_state.update({
            "saved_video_position": self.saved_video_position.total_seconds(),
            "speed": self.speed,
        })
        return Response(
            json.dumps(view_state),
            content_type='application/json',
            charset='UTF-8'
        )

    @XBlock.handler
    def yt_video_metadata(self, request, suffix=''):  # lint-amnesty, pylint: disable=unused-argument
        """
        Endpoint to get YouTube metadata.
        This handler is only used in the openedx_content-based runtime. The old
        runtime uses a similar REST API that's not an XBlock handler.
        """
        from lms.djangoapps.courseware.views.views import load_metadata_from_youtube
        if not self.youtube_id_1_0:
            # TODO: more informational response to explain that yt_video_metadata not supported for non-youtube videos.
            return Response('{}', status=400)

        metadata, status_code = load_metadata_from_youtube(video_id=self.youtube_id_1_0, request=request)
        response = Response(json.dumps(metadata), status=status_code)
        response.content_type = 'application/json'
        return response


class VideoStudioViewHandlers:
    """
    Handlers for Studio view.
    """
    def validate_transcript_upload_data(self, data):
        """
        Validates video transcript file.
        Arguments:
            data: Transcript data to be validated.
        Returns:
            None or String
            If there is error returns error message otherwise None.
        """
        error = None
        _ = self.runtime.service(self, "i18n").ugettext
        # Validate the must have attributes - this error is unlikely to be faced by common users.
        must_have_attrs = ['edx_video_id', 'language_code', 'new_language_code']
        missing = [attr for attr in must_have_attrs if attr not in data]

        # Get available transcript languages.
        transcripts = self.get_transcripts_info()
        video_config_service = self.runtime.service(self, 'video_config')
        if not video_config_service:
            return error
        available_translations = video_config_service.available_translations(
            self,
            transcripts,
            verify_assets=True
        )

        if missing:
            error = _('The following parameters are required: {missing}.').format(missing=', '.join(missing))
        elif (
            data['language_code'] != data['new_language_code'] and data['new_language_code'] in available_translations
        ):
            error = _('A transcript with the "{language_code}" language code already exists.').format(
                language_code=data['new_language_code'],
            )
        elif 'file' not in data:
            error = _('A transcript file is required.')

        return error

    @XBlock.handler
    def studio_transcript(self, request, dispatch):
        """
        Entry point for Studio transcript handlers.

        Dispatches:
            /translation/[language_id] - language_id sould be in url.

        `translation` dispatch support following HTTP methods:
            `POST`:
                Upload srt file. Check possibility of generation of proper sjson files.
                For now, it works only for self.transcripts, not for `en`.
            `GET:
                Return filename from storage. SRT format is sent back on success. Filename should be in GET dict.

        We raise all exceptions right in Studio:
            NotFoundError:
                Video or asset was deleted from module/contentstore, but request came later.
                Seems impossible to be raised. block_render.py catches NotFoundErrors from here.

            /translation POST:
                TypeError:
                    Unjsonable filename or content.
                TranscriptsGenerationException, TranscriptException:
                    no SRT extension or not parse-able by PySRT
                UnicodeDecodeError: non-UTF8 uploaded file content encoding.
        """
        if dispatch.startswith('translation'):

            if request.method == 'POST':
                response = self._studio_transcript_upload(request)
            elif request.method == 'DELETE':
                response = self._studio_transcript_delete(request)
            elif request.method == 'GET':
                response = self._studio_transcript_get(request)
            else:
                # Any other HTTP method is not allowed.
                response = Response(status=404)

        else:  # unknown dispatch
            log.debug("Dispatch is not allowed")
            response = Response(status=404)

        return response

    def _studio_transcript_upload(self, request):
        """
        Upload transcript. Used in "POST" method in `studio_transcript`
        """
        _ = self.runtime.service(self, "i18n").ugettext
        video_config_service = self.runtime.service(self, 'video_config')
        if not video_config_service:
            return Response(json={'error': _('Runtime does not support transcripts.')}, status=400)
        error = self.validate_transcript_upload_data(data=request.POST)
        if error:
            return Response(json={'error': error}, status=400)
        edx_video_id = (request.POST['edx_video_id'] or "").strip()
        language_code = request.POST['language_code']
        new_language_code = request.POST['new_language_code']
        try:
            video_config_service.upload_transcript(
                video_block=self,  # NOTE: .edx_video_id and .transcripts may get mutated
                edx_video_id=edx_video_id,
                language_code=language_code,
                new_language_code=new_language_code,
                transcript_file=request.POST['file'].file,
            )
            return Response(
                json.dumps(
                    {
                        "edx_video_id": edx_video_id or self.edx_video_id,
                        "language_code": new_language_code,
                    }
                ),
                status=201,
            )
        except (TranscriptsGenerationException, UnicodeDecodeError):
            return Response(
                json={
                    'error': _(
                        'There is a problem with this transcript file. Try to upload a different file.'
                    )
                },
                status=400
            )

    def _studio_transcript_delete(self, request):
        """
        Delete transcript. Used in "DELETE" method in `studio_transcript`
        """
        _ = self.runtime.service(self, "i18n").ugettext
        video_config_service = self.runtime.service(self, 'video_config')
        if not video_config_service:
            return Response(json={'error': _('Runtime does not support transcripts.')}, status=400)
        request_data = request.json
        if 'lang' not in request_data or 'edx_video_id' not in request_data:
            return Response(status=400)
        video_config_service.delete_transcript(
            video_block=self,
            edx_video_id=request_data['edx_video_id'],
            language_code=request_data['lang'],
        )
        return Response(status=200)

    def _studio_transcript_get(self, request):
        """
        Get transcript. Used in "GET" method in `studio_transcript`
        """
        _ = self.runtime.service(self, "i18n").ugettext
        language = request.GET.get('language_code')
        if not language:
            return Response(json={'error': _('Language is required.')}, status=400)

        try:
            video_config_service = self.runtime.service(self, 'video_config')
            if not video_config_service:
                return Response(status=404)
            transcript_content, transcript_name, mime_type = video_config_service.get_transcript(
                self, lang=language, output_format=Transcript.SRT
            )
            response = Response(transcript_content, headerlist=[
                (
                    'Content-Disposition',
                    f'attachment; filename="{transcript_name}"'
                ),
                ('Content-Language', language),
                ('Content-Type', mime_type)
            ])
        except (
            UnicodeDecodeError,
            TranscriptsGenerationException,
            TranscriptNotFoundError
        ):
            response = Response(status=404)
        return response
