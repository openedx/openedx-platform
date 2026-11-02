'use strict';

import VideoStorage from './00_video_storage.js';
import initialize from './01_initialize.js';
import FocusGrabber from './025_focus_grabber.js';
import VideoAccessibleMenu from './035_video_accessible_menu.js';
import VideoControl from './04_video_control.js';
import VideoFullScreen from './04_video_full_screen.js';
import VideoQualityControl from './05_video_quality_control.js';
import VideoProgressSlider from './06_video_progress_slider.js';
import VideoVolumeControl from './07_video_volume_control.js';
import VideoSpeedControl from './08_video_speed_control.js';
import VideoAutoAdvanceControl from './08_video_auto_advance_control.js';
import VideoCaption from './09_video_caption.js';
import VideoPlayPlaceholder from './09_play_placeholder.js';
import VideoPlayPauseControl from './09_play_pause_control.js';
import VideoPlaySkipControl from './09_play_skip_control.js';
import VideoSkipControl from './09_skip_control.js';
import VideoBumper from './09_bumper.js';
import VideoSaveStatePlugin from './09_save_state_plugin.js';
import VideoEventsPlugin from './09_events_plugin.js';
import VideoEventsBumperPlugin from './09_events_bumper_plugin.js';
import VideoPoster from './09_poster.js';
import VideoCompletionHandler from './09_completion.js';
import VideoCommands from './10_commands.js';
import VideoContextMenu from './095_video_context_menu.js';
import VideoSocialSharing from './036_video_social_sharing.js';
import VideoTranscriptFeedback from './037_video_transcript_feedback.js';
import VideoAudioDescription from './09_video_audio_description.js';

let youtubeXhr = null;

window.Video = function (runtime, element) {
    let el = $(element).find('.video'),
        id = el.attr('id').replace(/video_/, ''),
        storage = VideoStorage('VideoState', id),
        bumperMetadata = el.data('bumper-metadata'),
        autoAdvanceEnabled = el.data('autoadvance-enabled') === 'True',
        mainVideoModules = [
            FocusGrabber, VideoControl, VideoPlayPlaceholder,
            VideoPlayPauseControl, VideoProgressSlider, VideoSpeedControl,
            VideoVolumeControl, VideoQualityControl, VideoFullScreen,
            VideoAudioDescription,
            VideoCaption, VideoCommands,
            VideoContextMenu, VideoSaveStatePlugin, VideoEventsPlugin, VideoCompletionHandler, VideoTranscriptFeedback
        ].concat(autoAdvanceEnabled ? [VideoAutoAdvanceControl] : []),
        bumperVideoModules = [VideoControl, VideoPlaySkipControl, VideoSkipControl,
            VideoVolumeControl, VideoCaption, VideoCommands, VideoSaveStatePlugin, VideoTranscriptFeedback,
            VideoEventsBumperPlugin, VideoCompletionHandler],
        state = {
            el: el,
            id: id,
            metadata: el.data('metadata'),
            storage: storage,
            options: {},
            youtubeXhr: youtubeXhr,
            modules: mainVideoModules
        };

    let getBumperState = function (metadata) {
        let bumperState = $.extend(true, {
            el: el,
            id: id,
            storage: storage,
            options: {},
            youtubeXhr: youtubeXhr
        }, {metadata: metadata});

        bumperState.modules = bumperVideoModules;
        bumperState.options = {
            SaveStatePlugin: {events: ['language_menu:change']}
        };
        return bumperState;
    };

    let player = function (innerState) {
        return function () {
            _.extend(innerState.metadata, {autoplay: true, focusFirstControl: true});
            initialize(innerState, element);
        };
    };
    let onSequenceChange;

    VideoAccessibleMenu(el, {
        storage: storage,
        saveStateUrl: state.metadata.saveStateUrl
    });

    VideoSocialSharing(el);

    if (bumperMetadata) {
        VideoPoster(el, {
            poster: el.data('poster'),
            onClick: _.once(function () {
                let mainVideoPlayer = player(state);
                let bumper, bumperState;
                if (storage.getItem('isBumperShown')) {
                    mainVideoPlayer();
                } else {
                    bumperState = getBumperState(bumperMetadata);
                    bumper = new VideoBumper(player(bumperState), bumperState);
                    state.bumperState = bumperState;
                    bumper.getPromise().done(function () {
                        delete state.bumperState;
                        mainVideoPlayer();
                    });
                }
            })
        });
    } else {
        initialize(state, element);
    }

    if (!youtubeXhr) {
        youtubeXhr = state.youtubeXhr;
    }

    el.data('video-player-state', state);
    onSequenceChange = function () {
        if (state && state.videoPlayer) {
            state.videoPlayer.destroy();
        }
        $('.sequence').off('sequence:change', onSequenceChange);
    };
    $('.sequence').on('sequence:change', onSequenceChange);

    // Because the 'state' object is only available inside this closure, we will also make it available to
    // the caller by returning it. This is necessary so that we can test Video with Jasmine.
    return state;
}

window.Video.clearYoutubeXhr = function () {
    youtubeXhr = null;
};

window.Video.loadYouTubeIFrameAPI = initialize.prototype.loadYouTubeIFrameAPI;
