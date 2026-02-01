"""  # lint-amnesty, pylint: disable=cyclic-import
XFields for video block.
"""


import datetime

from xblock.fields import Boolean, DateTime, Dict, Float, List, RelativeTime, Scope, String

# Make '_' a no-op so we can scrape strings. Using lambda instead of
#  `django.utils.translation.ugettext_noop` because Django cannot be imported in this file
_ = lambda text: text


class VideoFields:
    """Fields for `VideoBlock`."""
    display_name = String(
        help=_("The display name for this component."),
        display_name=_("Component Display Name"),
        default="Video",
        scope=Scope.settings
    )

    saved_video_position = RelativeTime(
        help=_("Current position in the video."),
        scope=Scope.user_state,
        default=datetime.timedelta(seconds=0)
    )
    # TODO: This should be moved to Scope.content, but this will
    # require data migration to support the old video block.
    youtube_id_1_0 = String(
        help=_("Optional, for older browsers: the YouTube ID for the normal speed video."),
        display_name=_("YouTube ID"),
        scope=Scope.settings,
        default="3_yD_cEKoCk"
    )
    start_time = RelativeTime(  # datetime.timedelta object
        help=_(
            "Time you want the video to start if you don't want the entire video to play. "
            "Not supported in the native mobile app: the full video file will play. "
            "Formatted as HH:MM:SS. The maximum value is 23:59:59."
        ),
        display_name=_("Video Start Time"),
        scope=Scope.settings,
        default=datetime.timedelta(seconds=0)
    )
    end_time = RelativeTime(  # datetime.timedelta object
        help=_(
            "Time you want the video to stop if you don't want the entire video to play. "
            "Not supported in the native mobile app: the full video file will play. "
            "Formatted as HH:MM:SS. The maximum value is 23:59:59."
        ),
        display_name=_("Video Stop Time"),
        scope=Scope.settings,
        default=datetime.timedelta(seconds=0)
    )
    #front-end code of video player checks logical validity of (start_time, end_time) pair.

    download_video = Boolean(
        help=_("Allow students to download versions of this video in different formats if they cannot use the edX video"
               " player or do not have access to YouTube. You must add at least one non-YouTube URL "
               "in the Video File URLs field."),
        display_name=_("Video Download Allowed"),
        scope=Scope.settings,
        default=False
    )
    html5_sources = List(
        help=_("The URL or URLs where you've posted non-YouTube versions of the video. Each URL must end in .mpeg,"
               " .mp4, .ogg, or .webm and cannot be a YouTube URL. (For browser compatibility, we strongly recommend"
               " .mp4 and .webm format.) Students will be able to view the first listed video that's compatible with"
               " the student's computer. To allow students to download these videos, "
               "set Video Download Allowed to True."),
        display_name=_("Video File URLs"),
        scope=Scope.settings,
    )
    track = String(
        help=_("By default, students can download an .srt or .txt transcript when you set Download Transcript "
               "Allowed to True. If you want to provide a downloadable transcript in a different format, we recommend "
               "that you upload a handout by using the Upload a Handout field. If this isn't possible, you can post a "
               "transcript file on the Files & Uploads page or on the Internet, and then add the URL for the "
               "transcript here. Students see a link to download that transcript below the video."),
        display_name=_("Downloadable Transcript URL"),
        scope=Scope.settings,
        default=''
    )
    download_track = Boolean(
        help=_("Allow students to download the timed transcript. A link to download the file appears below the video."
               " By default, the transcript is an .srt or .txt file. If you want to provide the transcript for "
               "download in a different format, upload a file by using the Upload Handout field."),
        display_name=_("Download Transcript Allowed"),
        scope=Scope.settings,
        default=False
    )
    # `sub` is deprecated field and should not be used in future. Now, transcripts are primarily handled in VAL and
    # backward compatibility for the video blocks already using this field has been ensured.
    sub = String(
        help=_("The default transcript for the video, from the Default Timed Transcript field on the Basic tab. "
               "This transcript should be in English. You don't have to change this setting."),
        display_name=_("Default Timed Transcript"),
        scope=Scope.settings,
        default=""
    )
    show_captions = Boolean(
        help=_("Specify whether the transcripts appear with the video by default."),
        display_name=_("Show Transcript"),
        scope=Scope.settings,
        default=True
    )
    # Data format: {'de': 'german_translation', 'uk': 'ukrainian_translation'}
    transcripts = Dict(
        help=_("Add transcripts in different languages."
               " Click below to specify a language and upload an .srt transcript file for that language."),
        display_name=_("Transcript Languages"),
        scope=Scope.settings,
        default={}
    )
    transcript_language = String(
        help=_("Preferred language for transcript."),
        display_name=_("Preferred language for transcript"),
        scope=Scope.preferences,
        default="en"
    )
    transcript_download_format = String(
        help=_("Transcript file format to download by user."),
        scope=Scope.preferences,
        values=[
            # Translators: This is a type of file used for captioning in the video player.
            {"display_name": _("SubRip (.srt) file"), "value": "srt"},
            {"display_name": _("Text (.txt) file"), "value": "txt"}
        ],
        default='srt',
    )
    speed = Float(
        help=_("The last speed that the user specified for the video."),
        scope=Scope.user_state
    )
    global_speed = Float(
        help=_("The default speed for the video."),
        scope=Scope.preferences,
        default=1.0
    )
    auto_advance = Boolean(
        help=_("Specify whether to advance automatically to the next unit when the video ends."),
        scope=Scope.preferences,
        # The default is True because this field only has an effect when auto-advance controls are enabled
        # (globally enabled through feature flag and locally enabled through course setting); in that case
        # it's good to start auto-advancing and let the student disable it, instead of the other way around
        # (requiring the user to enable it). When auto-advance controls are hidden, this field won't be used.
        default=True,
    )
    youtube_is_available = Boolean(
        help=_("Specify whether YouTube is available for the user."),
        scope=Scope.user_info,
        default=True
    )
    handout = String(
        help=_("Upload a handout to accompany this video. Students can download the handout by "
               "clicking Download Handout under the video."),
        display_name=_("Upload Handout"),
        scope=Scope.settings,
    )
    only_on_web = Boolean(
        help=_(
            "Specify whether access to this video is limited to browsers only, or if it can be "
            "accessed from other applications including mobile apps."
        ),
        display_name=_("Video Available on Web Only"),
        scope=Scope.settings,
        default=False
    )
    edx_video_id = String(
        help=_("If you were assigned a Video ID by edX for the video to play in this component, enter the ID here."
               " In this case, do not enter values in the Default Video URL, the Video File URLs, "
               "and the YouTube ID fields. If you were not assigned a Video ID,"
               " enter values in those other fields and ignore this field."),
        display_name=_("Video ID"),
        scope=Scope.settings,
        default="",
    )
    bumper_last_view_date = DateTime(
        display_name=_("Date of the last view of the bumper"),
        scope=Scope.preferences,
    )
    bumper_do_not_show_again = Boolean(
        display_name=_("Do not show bumper again"),
        scope=Scope.preferences,
        default=False,
    )
    public_access = Boolean(
        help=_("Specify whether the video can be accessed publicly by learners."),
        display_name=_("Public Access"),
        scope=Scope.settings,
        default=False
    )
    # thumbnail is need as a field for the new video editor. The field is hidden in
    # the legacy modal.
    thumbnail = String(
        help=_("Add a specific thumbnail for learners to see before playing the video."),
        display_name=_("Thumbnail"),
        scope=Scope.settings,
        default="",
    )
