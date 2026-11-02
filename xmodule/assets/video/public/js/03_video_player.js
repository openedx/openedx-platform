/* eslint-disable no-console, no-param-reassign */
import HTML5Video from './02_html5_video.js';
import HTML5HLSVideo from './02_html5_hls_video.js';
import Resizer from './00_resizer.js';
import HLS from 'hls';
import _ from 'underscore';
import * as Time from './utils/time.js';

let dfd = $.Deferred();

let VideoPlayer = function(state) {
    state.videoPlayer = {};
    _makeFunctionsPublic(state);
    _initialize(state);
    // No callbacks to DOM events (click, mousemove, etc.).

    return dfd.promise();
};

let methodsDict = {
    destroy: destroy,
    duration: duration,
    handlePlaybackQualityChange: handlePlaybackQualityChange,

    // Added for finer graded seeking control.
    // Please see:
    //     https://developers.google.com/youtube/js_api_reference#Events
    isBuffering: isBuffering,
    // https://developers.google.com/youtube/js_api_reference#cueVideoById
    isCued: isCued,

    isEnded: isEnded,
    isPlaying: isPlaying,
    isUnstarted: isUnstarted,
    onCaptionSeek: onSeek,
    onEnded: onEnded,
    onError: onError,
    onPause: onPause,
    onPlay: onPlay,
    runTimer: runTimer,
    stopTimer: stopTimer,
    onLoadMetadataHtml5: onLoadMetadataHtml5,
    onPlaybackQualityChange: onPlaybackQualityChange,
    onReady: onReady,
    onSlideSeek: onSeek,
    onSpeedChange: onSpeedChange,
    onAutoAdvanceChange: onAutoAdvanceChange,
    onStateChange: onStateChange,
    onUnstarted: onUnstarted,
    onVolumeChange: onVolumeChange,
    pause: pause,
    play: play,
    seekTo: seekTo,
    setPlaybackRate: setPlaybackRate,
    update: update,
    figureOutStartEndTime: figureOutStartEndTime,
    figureOutStartingTime: figureOutStartingTime,
    updatePlayTime: updatePlayTime
};
/* eslint-enable no-use-before-define */

VideoPlayer.prototype = methodsDict;

export default VideoPlayer;

// ***************************************************************
// Private functions start here.
// ***************************************************************

// function _makeFunctionsPublic(state)
//
//     Functions which will be accessible via 'state' object. When called,
//     these functions will get the 'state' object as a context.
function _makeFunctionsPublic(state) {
    let debouncedF = _.debounce(
        function(params) {
            // Can't cancel a queued debounced function on destroy
            if (state.videoPlayer) {
                return onSeek.call(this, params);
            }
        }.bind(state),
        300
    );

    state.bindTo(methodsDict, state.videoPlayer, state);

    state.videoPlayer.onSlideSeek = debouncedF;
    state.videoPlayer.onCaptionSeek = debouncedF;
}

// Updates players state, once metadata is loaded for html5 player.
function onLoadMetadataHtml5() {
    let player = this.videoPlayer.player.videoEl;
    let videoWidth = player[0].videoWidth || player.width();
    let videoHeight = player[0].videoHeight || player.height();

    _resize(this, videoWidth, videoHeight);
    _updateVcrAndRegion(this);
}

// function _initialize(state)
//
//     Create any necessary DOM elements, attach them, and set their
//     initial configuration. Also make the created DOM elements available
//     via the 'state' object. Much easier to work this way - you don't
//     have to do repeated jQuery element selects.
// eslint-disable-next-line no-underscore-dangle
function _initialize(state) {
    let youTubeId;
    let player;
    let userAgent;
    let commonPlayerConfig;
    let eventToBeTriggered = 'loadedmetadata';

    // The function is called just once to apply pre-defined configurations
    // by student before video starts playing. Waits until the video's
    // metadata is loaded, which normally happens just after the video
    // starts playing. Just after that configurations can be applied.
    state.videoPlayer.ready = _.once(function() {
        if (!state.isFlashMode() && state.speed != '1.0') {
            state.videoPlayer.setPlaybackRate(state.speed);
        }
    });

    if (state.isYoutubeType()) {
        state.videoPlayer.PlayerState = YT.PlayerState;
        state.videoPlayer.PlayerState.UNSTARTED = -1;
    } else {
        state.videoPlayer.PlayerState = HTML5Video.PlayerState;
    }

    state.videoPlayer.currentTime = 0;

    state.videoPlayer.goToStartTime = true;
    state.videoPlayer.stopAtEndTime = true;

    state.videoPlayer.playerVars = {
        controls: 0,
        wmode: 'transparent',
        rel: 0,
        showinfo: 0,
        enablejsapi: 1,
        modestbranding: 1,
        cc_load_policy: 0
    };

    if (!state.isFlashMode()) {
        state.videoPlayer.playerVars.html5 = 1;
    }

    // Detect the current browser for several browser-specific work-arounds.
    userAgent = navigator.userAgent.toLowerCase();
    state.browserIsFirefox = userAgent.indexOf('firefox') > -1;
    state.browserIsChrome = userAgent.indexOf('chrome') > -1;
    // Chrome includes both "Chrome" and "Safari" in the user agent.
    state.browserIsSafari = (userAgent.indexOf('safari') > -1
                     && !state.browserIsChrome);

    // Browser can play HLS videos if either `Media Source Extensions`
    // feature is supported or browser is safari (native HLS support)
    state.canPlayHLS = state.HLSVideoSources.length > 0 && (HLS.isSupported() || state.browserIsSafari);
    state.HLSOnlySources = state.config.sources.length > 0
                   && state.config.sources.length === state.HLSVideoSources.length;

    commonPlayerConfig = {
        playerVars: state.videoPlayer.playerVars,
        videoSources: state.config.sources,
        poster: state.config.poster,
        browserIsSafari: state.browserIsSafari,
        events: {
            onReady: state.videoPlayer.onReady,
            onStateChange: state.videoPlayer.onStateChange,
            onError: state.videoPlayer.onError
        }
    };

    if (state.videoType === 'html5') {
        if (state.canPlayHLS || state.HLSOnlySources) {
            state.videoPlayer.player = new HTML5HLSVideo.Player(
                state.el,
                _.extend({}, commonPlayerConfig, {
                    state: state,
                    onReadyHLS: function() { dfd.resolve(); },
                    videoSources: state.HLSVideoSources,
                    canPlayHLS: state.canPlayHLS,
                    HLSOnlySources: state.HLSOnlySources
                })
            );
            // `loadedmetadata` event triggered too early on Safari due
            // to which correct video dimensions were not calculated
            eventToBeTriggered = state.browserIsSafari ? 'loadeddata' : eventToBeTriggered;
        } else {
            state.videoPlayer.player = new HTML5Video.Player(state.el, commonPlayerConfig);
        }
        // eslint-disable-next-line no-multi-assign
        player = state.videoEl = state.videoPlayer.player.videoEl;
        player[0].addEventListener(eventToBeTriggered, state.videoPlayer.onLoadMetadataHtml5, false);
        player.on('remove', state.videoPlayer.destroy);
    } else {
        youTubeId = state.youtubeId();

        state.videoPlayer.player = new YT.Player(state.id, {
            playerVars: state.videoPlayer.playerVars,
            videoId: youTubeId,
            events: {
                onReady: state.videoPlayer.onReady,
                onStateChange: state.videoPlayer.onStateChange,
                onPlaybackQualityChange: state.videoPlayer.onPlaybackQualityChange,
                onError: state.videoPlayer.onError
            }
        });

        state.el.on('initialize', function() {
            // eslint-disable-next-line no-shadow, no-multi-assign
            let player = state.videoEl = state.el.find('iframe');
            let videoWidth = player.attr('width') || player.width();
            let videoHeight = player.attr('height') || player.height();

            player.on('remove', state.videoPlayer.destroy);

            _resize(state, videoWidth, videoHeight);
            _updateVcrAndRegion(state, true);
        });
    }

    if (state.isTouch) {
        dfd.resolve();
    }
}

function _updateVcrAndRegion(state, isYoutube) {
    // eslint-disable-next-line no-shadow
    let update = function(state) {
        // eslint-disable-next-line no-shadow
        let duration = state.videoPlayer.duration();
        let time = state.videoPlayer.figureOutStartingTime(duration);

        // Update the VCR.
        state.trigger(
            'videoControl.updateVcrVidTime',
            {
                time: time,
                duration: duration
            }
        );

        // Update the time slider.
        state.trigger(
            'videoProgressSlider.updateStartEndTimeRegion',
            {
                duration: duration
            }
        );
        state.trigger(
            'videoProgressSlider.updatePlayTime',
            {
                time: time,
                duration: duration
            }
        );
    };

    // After initialization, update the VCR with total time.
    // At this point only the metadata duration is available (not
    // very precise), but it is better than having 00:00:00 for
    // total time.
    if (state.youtubeMetadataReceived || !isYoutube) {
        // Metadata was already received, and is available.
        update(state);
    } else {
        // We wait for metadata to arrive, before we request the update
        // of the VCR video time, and of the start-end time region.
        // Metadata contains duration of the video.
        state.el.on('metadata_received', function() {
            update(state);
        });
    }
}

function _resize(state, videoWidth, videoHeight) {
    state.resizer = new Resizer({
        element: state.videoEl,
        elementRatio: videoWidth / videoHeight,
        container: state.container
    })
        .callbacks.once(function() {
            state.el.trigger('caption:resize');
        })
        .setMode('width');

    // Update captions size when controls becomes visible on iPad or Android
    if (/iPad|Android/i.test(state.isTouch[0])) {
        state.el.on('controls:show', function() {
            state.el.trigger('caption:resize');
        });
    }

    $(window).on('resize.video', _.debounce(function() {
        state.trigger('videoFullScreen.updateControlsHeight', null);
        state.el.trigger('caption:resize');
        state.resizer.align();
    }, 100));
}

// function _restartUsingFlash(state)
//
//     When we are about to play a YouTube video in HTML5 mode and discover
//     that we only have one available playback rate, we will switch to
//     Flash mode. In Flash speed switching is done by reloading videos
//     recorded at different frame rates.
function _restartUsingFlash(state) {
    // Remove from the page current iFrame with HTML5 video.
    state.videoPlayer.player.destroy();

    state.setPlayerMode('flash');

    console.log('[Video info]: Changing YouTube player mode to "flash".');

    // Removed configuration option that requests the HTML5 mode.
    delete state.videoPlayer.playerVars.html5;

    // Request for the creation of a new Flash player
    state.videoPlayer.player = new YT.Player(state.id, {
        playerVars: state.videoPlayer.playerVars,
        videoId: state.youtubeId(),
        events: {
            onReady: state.videoPlayer.onReady,
            onStateChange: state.videoPlayer.onStateChange,
            onPlaybackQualityChange: state.videoPlayer.onPlaybackQualityChange,
            onError: state.videoPlayer.onError
        }
    });

    _updateVcrAndRegion(state, true);
    state.el.trigger('caption:fetch');
    state.resizer.setElement(state.el.find('iframe')).align();
}

// ***************************************************************
// Public functions start here.
// These are available via the 'state' object. Their context ('this'
// keyword) is the 'state' object. The magic private function that makes
// them available and sets up their context is makeFunctionsPublic().
// ***************************************************************

function destroy() {
    let player = this.videoPlayer.player;
    this.el.removeClass([
        'is-unstarted', 'is-playing', 'is-paused', 'is-buffered',
        'is-ended', 'is-cued'
    ].join(' '));
    $(window).off('.video');
    this.el.trigger('destroy');
    this.el.off();
    this.videoPlayer.stopTimer();
    if (this.resizer && this.resizer.destroy) {
        this.resizer.destroy();
    }
    if (player && player.video) {
        player.video.removeEventListener('loadedmetadata', this.videoPlayer.onLoadMetadataHtml5, false);
    }
    if (player && _.isFunction(player.destroy)) {
        player.destroy();
    }
    if (this.canPlayHLS && player.hls) {
        player.hls.destroy();
    }
    delete this.videoPlayer;
}

function pause() {
    if (this.videoPlayer.player.pauseVideo) {
        this.videoPlayer.player.pauseVideo();
    }
}

function play() {
    if (this.videoPlayer.player.playVideo) {
        if (this.videoPlayer.isEnded()) {
            // When the video will start playing again from the start, the
            // start-time and end-time will come back into effect.
            this.videoPlayer.goToStartTime = true;
        }

        this.videoPlayer.player.playVideo();
    }
}

// This function gets the video's current play position in time
// (currentTime) and its duration.
// It is called at a regular interval when the video is playing.
function update(time) {
    this.videoPlayer.currentTime = time || this.videoPlayer.player.getCurrentTime();

    if (isFinite(this.videoPlayer.currentTime)) {
        this.videoPlayer.updatePlayTime(this.videoPlayer.currentTime);

        // We need to pause the video if current time is smaller (or equal)
        // than end-time. Also, we must make sure that this is only done
        // once per video playing from start to end.
        if (
            this.videoPlayer.endTime !== null
    && this.videoPlayer.endTime <= this.videoPlayer.currentTime
        ) {
            this.videoPlayer.pause();

            this.trigger('videoProgressSlider.notifyThroughHandleEnd', {
                end: true
            });

            this.el.trigger('stop');
        }
        this.el.trigger('timeupdate', [this.videoPlayer.currentTime]);
    }
}

function setPlaybackRate(newSpeed) {
    this.videoPlayer.player.setPlaybackRate(newSpeed);
}

function onSpeedChange(newSpeed) {
    let time = this.videoPlayer.currentTime;

    if (this.isFlashMode()) {
        this.videoPlayer.currentTime = Time.convert(
            time,
            parseFloat(this.speed),
            newSpeed
        );
    }

    newSpeed = parseFloat(newSpeed);
    this.setSpeed(newSpeed);
    this.videoPlayer.setPlaybackRate(newSpeed);
}

function onAutoAdvanceChange(enabled) {
    this.setAutoAdvance(enabled);
}

// Every 200 ms, if the video is playing, we call the function update, via
// clearInterval. This interval is called updateInterval.
// It is created on a onPlay event. Cleared on a onPause event.
// Reinitialized on a onSeek event.
function onSeek(params) {
    let time = params.time,
        type = params.type,
        oldTime = this.videoPlayer.currentTime;
    // After the user seeks, the video will start playing from
    // the sought point, and stop playing at the end.
    this.videoPlayer.goToStartTime = false;

    this.videoPlayer.seekTo(time);

    this.el.trigger('seek', [time, oldTime, type]);
}

function seekTo(time) {
    // eslint-disable-next-line no-shadow
    let duration = this.videoPlayer.duration();

    if ((typeof time !== 'number') || (time > duration) || (time < 0)) {
        return false;
    }

    this.el.off('play.seek');

    if (this.videoPlayer.isPlaying()) {
        this.videoPlayer.stopTimer();
    }
    let isUnplayed = this.videoPlayer.isUnstarted()
             || this.videoPlayer.isCued();

    // Use `cueVideoById` method for youtube video that is not played before.
    if (isUnplayed && this.isYoutubeType()) {
        this.videoPlayer.player.cueVideoById(this.youtubeId(), time);
    } else {
        // Youtube video cannot be rewinded during bufferization, so wait to
        // finish bufferization and then rewind the video.
        if (this.isYoutubeType() && this.videoPlayer.isBuffering()) {
            this.el.on('play.seek', function() {
                this.videoPlayer.player.seekTo(time, true);
            }.bind(this));
        } else {
            // Otherwise, just seek the video
            this.videoPlayer.player.seekTo(time, true);
        }
    }

    this.videoPlayer.updatePlayTime(time, true);

    // the timer is stopped above; restart it.
    if (this.videoPlayer.isPlaying()) {
        this.videoPlayer.runTimer();
    }
    // Update the the current time when user seek. (YoutubePlayer)
    this.videoPlayer.currentTime = time;
}

function runTimer() {
    if (!this.videoPlayer.updateInterval) {
        this.videoPlayer.updateInterval = window.setInterval(
            this.videoPlayer.update, 200
        );

        this.videoPlayer.update();
    }
}

function stopTimer() {
    window.clearInterval(this.videoPlayer.updateInterval);
    delete this.videoPlayer.updateInterval;
}

function onEnded() {
    let time = this.videoPlayer.duration();

    this.trigger('videoProgressSlider.notifyThroughHandleEnd', {
        end: true
    });

    if (this.videoPlayer.skipOnEndedStartEndReset) {
        this.videoPlayer.skipOnEndedStartEndReset = undefined;
    }
    // Sometimes `onEnded` events fires when `currentTime` not equal
    // `duration`. In this case, slider doesn't reach the end point of
    // timeline.
    this.videoPlayer.updatePlayTime(time);

    // Emit 'pause_video' event when a video ends if Player is of Youtube
    if (this.isYoutubeType()) {
        this.el.trigger('pause', arguments);
    }
    this.el.trigger('ended', arguments);
}

function onPause() {
    this.videoPlayer.stopTimer();
    this.el.trigger('pause', arguments);
}

function onPlay() {
    this.videoPlayer.runTimer();
    this.trigger('videoProgressSlider.notifyThroughHandleEnd', {
        end: false
    });
    this.videoPlayer.ready();
    this.el.trigger('play', arguments);
}

function onUnstarted() { }

function handlePlaybackQualityChange(value) {
    this.videoPlayer.player.setPlaybackQuality(value);
}

function onPlaybackQualityChange() {
    let quality;

    quality = this.videoPlayer.player.getPlaybackQuality();

    this.trigger('videoQualityControl.onQualityChange', quality);
    this.el.trigger('qualitychange', arguments);
}

function onReady() {
    let _this = this,
        availablePlaybackRates, baseSpeedSubs,
        player, videoWidth, videoHeight;

    dfd.resolve();

    this.el.on('speedchange', function(event, speed) {
        _this.videoPlayer.onSpeedChange(speed);
    });

    this.el.on('autoadvancechange', function(event, enabled) {
        _this.videoPlayer.onAutoAdvanceChange(enabled);
    });

    this.el.on('volumechange volumechange:silent', function(event, volume) {
        _this.videoPlayer.onVolumeChange(volume);
    });

    availablePlaybackRates = this.videoPlayer.player
        .getAvailablePlaybackRates();

    // Because of problems with muting sound outside of range 0.25 and
    // 5.0, we should filter our available playback rates.
    // Issues:
    //   https://code.google.com/p/chromium/issues/detail?id=264341
    //   https://bugzilla.mozilla.org/show_bug.cgi?id=840745
    //   https://developer.mozilla.org/en-US/docs/DOM/HTMLMediaElement

    availablePlaybackRates = _.filter(
        availablePlaybackRates,
        function(item) {
            let speed = Number(item);
            return speed > 0.25 && speed <= 5;
        }
    );

    // Because of a recent change in the YouTube API (not documented), sometimes
    // HTML5 mode loads after Flash mode has been loaded. In this case we have
    // multiple speeds available but the variable `this.currentPlayerMode` is
    // set to "flash". This is impossible because in Flash mode we can have
    // only one speed available. Therefore we must execute the following code
    // block if we have multiple speeds or if `this.currentPlayerMode` is set to
    // "html5". If any of the two conditions are true, we then set the variable
    // `this.currentPlayerMode` to "html5".
    //
    // For more information, please see the PR that introduced this change:
    //     https://github.com/openedx/edx-platform/pull/2841
    if (
        (this.isHtml5Mode() || availablePlaybackRates.length > 1)
&& this.isYoutubeType()
    ) {
        if (availablePlaybackRates.length === 1 && !this.isTouch) {
            // This condition is needed in cases when Firefox version is
            // less than 20. In those versions HTML5 playback could only
            // happen at 1 speed (no speed changing). Therefore, in this
            // case, we need to switch back to Flash.
            //
            // This might also happen in other browsers, therefore when we
            // have 1 speed available, we fall back to Flash.

            _restartUsingFlash(this);
            return false;
        } else if (availablePlaybackRates.length > 1) {
            this.setPlayerMode('html5');

            // We need to synchronize available frame rates with the ones
            // that the user specified.

            baseSpeedSubs = this.videos['1.0'];
            // this.videos is a dictionary containing various frame rates
            // and their associated subs.

            // First clear the dictionary.
            $.each(this.videos, function(index, value) {
                delete _this.videos[index];
            });
            this.speeds = [];
            // Recreate it with the supplied frame rates.
            $.each(availablePlaybackRates, function(index, value) {
                let key = value.toFixed(2).replace(/\.00$/, '.0');

                _this.videos[key] = baseSpeedSubs;
                _this.speeds.push(key);
            });

            this.setSpeed(this.speed);
            this.el.trigger('speed:render', [this.speeds, this.speed]);
        }
    }

    if (this.isFlashMode()) {
        this.setSpeed(this.speed);
        this.el.trigger('speed:set', [this.speed]);
    }

    if (this.isHtml5Mode()) {
        this.videoPlayer.player.setPlaybackRate(this.speed);
    }

    // eslint-disable-next-line no-shadow
    let duration = this.videoPlayer.duration(),
        time = this.videoPlayer.figureOutStartingTime(duration);

    // this.duration will be set initially only if duration is coming from edx-val
    this.duration = this.duration || duration;

    if (time > 0 && this.videoPlayer.goToStartTime) {
        this.videoPlayer.seekTo(time);
    }

    this.el.trigger('ready', arguments);

    if (this.config.autoplay) {
        this.videoPlayer.play();
    }
}

function onStateChange(event) {
    this.el.removeClass([
        'is-unstarted', 'is-playing', 'is-paused', 'is-buffered',
        'is-ended', 'is-cued'
    ].join(' '));

    // eslint-disable-next-line default-case
    switch (event.data) {
    case this.videoPlayer.PlayerState.UNSTARTED:
        this.el.addClass('is-unstarted');
        this.videoPlayer.onUnstarted();
        break;
    case this.videoPlayer.PlayerState.PLAYING:
        this.el.addClass('is-playing');
        this.videoPlayer.onPlay();
        break;
    case this.videoPlayer.PlayerState.PAUSED:
        this.el.addClass('is-paused');
        this.videoPlayer.onPause();
        break;
    case this.videoPlayer.PlayerState.BUFFERING:
        this.el.addClass('is-buffered');
        this.el.trigger('buffering');
        break;
    case this.videoPlayer.PlayerState.ENDED:
        this.el.addClass('is-ended');
        this.videoPlayer.onEnded();
        break;
    case this.videoPlayer.PlayerState.CUED:
        this.el.addClass('is-cued');
        if (this.isFlashMode()) {
            this.videoPlayer.play();
        }
        break;
    }
}

function onError(code) {
    this.el.trigger('error', [code]);
}

// eslint-disable-next-line no-shadow
function figureOutStartEndTime(duration) {
    let videoPlayer = this.videoPlayer;

    videoPlayer.startTime = this.config.startTime;
    if (videoPlayer.startTime >= duration) {
        videoPlayer.startTime = 0;
    } else if (this.isFlashMode()) {
        videoPlayer.startTime /= Number(this.speed);
    }

    videoPlayer.endTime = this.config.endTime;
    if (
        videoPlayer.endTime <= videoPlayer.startTime
            || videoPlayer.endTime >= duration
    ) {
        videoPlayer.endTime = null;
    } else if (this.isFlashMode()) {
        videoPlayer.endTime /= Number(this.speed);
    }
}

// eslint-disable-next-line no-shadow
function figureOutStartingTime(duration) {
    let savedVideoPosition = this.config.savedVideoPosition,

        // Default starting time is 0. This is the case when
        // there is not start-time, no previously saved position,
        // or one (or both) of those values is incorrect.
        time = 0,

        startTime, endTime;

    this.videoPlayer.figureOutStartEndTime(duration);

    startTime = this.videoPlayer.startTime;
    endTime = this.videoPlayer.endTime;

    if (startTime > 0) {
        if (
            startTime < savedVideoPosition
&& (endTime > savedVideoPosition || endTime === null)

// We do not want to jump to the end of the video.
// We subtract 1 from the duration for a 1 second
// safety net.
&& savedVideoPosition < duration - 1
        ) {
            time = savedVideoPosition;
        } else {
            time = startTime;
        }
    } else if (
        savedVideoPosition > 0
&& (endTime > savedVideoPosition || endTime === null)

// We do not want to jump to the end of the video.
// We subtract 1 from the duration for a 1 second
// safety net.
&& savedVideoPosition < duration - 1
    ) {
        time = savedVideoPosition;
    }

    return time;
}

function updatePlayTime(time, skip_seek) {
    let videoPlayer = this.videoPlayer,
        endTime = this.videoPlayer.duration(),
        youTubeId;

    if (this.config.endTime) {
        endTime = Math.min(this.config.endTime, endTime);
    }

    this.trigger(
        'videoProgressSlider.updatePlayTime',
        {
            time: time,
            duration: endTime
        }
    );

    this.trigger(
        'videoControl.updateVcrVidTime',
        {
            time: time,
            duration: endTime
        }
    );

    this.el.trigger('caption:update', [time]);
}

function isEnded() {
    let playerState = this.videoPlayer.player.getPlayerState(),
        ENDED = this.videoPlayer.PlayerState.ENDED;

    return playerState === ENDED;
}

function isPlaying() {
    let playerState = this.videoPlayer.player.getPlayerState();

    return playerState === this.videoPlayer.PlayerState.PLAYING;
}

function isBuffering() {
    let playerState = this.videoPlayer.player.getPlayerState();

    return playerState === this.videoPlayer.PlayerState.BUFFERING;
}

function isCued() {
    let playerState = this.videoPlayer.player.getPlayerState();

    return playerState === this.videoPlayer.PlayerState.CUED;
}

function isUnstarted() {
    let playerState = this.videoPlayer.player.getPlayerState();

    return playerState === this.videoPlayer.PlayerState.UNSTARTED;
}

/*
 * Return the duration of the video in seconds.
 *
 * First, try to use the native player API call to get the duration.
 * If the value returned by the native function is not valid, resort to
 * the value stored in the metadata for the video. Note that the metadata
 * is available only for YouTube videos.
 *
 * IMPORTANT! It has been observed that sometimes, after initial playback
 * of the video, when operations "pause" and "play" are performed (in that
 * sequence), the function will start returning a slightly different value.
 *
 * For example: While playing for the first time, the function returns 31.
 * After pausing the video and then resuming once more, the function will
 * start returning 31.950656.
 *
 * This instability is internal to the player API (or browser internals).
 */
function duration() {
    let dur;

    // Sometimes the YouTube API doesn't finish instantiating all of it's
    // methods, but the execution point arrives here.
    //
    // This happens when you have start-time and end-time set, and click "Edit"
    // in Studio, and then "Save". The Video editor dialog closes, the
    // video reloads, but the start-end range is not visible.
    if (this.videoPlayer.player.getDuration) {
        dur = this.videoPlayer.player.getDuration();
    }

    // For YouTube videos, before the video starts playing, the API
    // function player.getDuration() will return 0. This means that the VCR
    // will show total time as 0 when the page just loads (before the user
    // clicks the Play button).
    //
    // We can do betterin a case when dur is 0 (or less than 0). We can ask
    // the getDuration() function for total time, which will query the
    // metadata for a duration.
    //
    // Be careful! Often the metadata duration is not very precise. It
    // might differ by one or two seconds against the actual time as will
    // be reported later on by the player.getDuration() API function.
    if (!isFinite(dur) || dur <= 0) {
        if (this.isYoutubeType()) {
            dur = this.getDuration();
        }
    }

    // Just in case the metadata is garbled, or something went wrong, we
    // have a final check.
    if (!isFinite(dur) || dur <= 0) {
        dur = 0;
    }

    return Math.floor(dur);
}

function onVolumeChange(volume) {
    this.videoPlayer.player.setVolume(volume);
}
