/* eslint-disable no-console, no-param-reassign */
/**
 * HTML5 video player module to support HLS video playback.
 *
 */

'use strict';

import _ from 'underscore';
import HTML5Video from './02_html5_video.js';
import Hls from 'hls.js';

let HLSVideo = {};

HLSVideo.Player = (function() {
    /**
     * Initialize HLS video player.
     *
     * @param {jQuery} el  Reference to video player container element
     * @param {Object} config  Contains common config for video player
     */
    function Player(el, config) {
        let self = this;

        this.config = config;

        // do common initialization independent of player type
        this.init(el, config);

        // set a default audio codec if not provided, this helps reduce issues
        // switching audio codecs during playback
        if (!this.config.defaultAudioCodec) {
            this.config.defaultAudioCodec = "mp4a.40.5";
        }

        _.bindAll(this, 'playVideo', 'pauseVideo', 'onReady');

        // If we have only HLS sources and browser doesn't support HLS then show error message.
        if (config.HLSOnlySources && !config.canPlayHLS) {
            this.showErrorMessage(null, '.video-hls-error');
            return;
        }

        this.config.state.el.on('initialize', _.once(function() {
            console.log('[HLS Video]: HLS Player initialized');
            self.showPlayButton();
        }));

        // Safari has native support to play HLS videos
        if (config.browserIsSafari) {
            this.videoEl.attr('src', config.videoSources[0]);
        } else {
            // load auto start if auto_advance is enabled
            if (config.state.auto_advance) {
                this.hls = new Hls({autoStartLoad: true});
            } else {
                this.hls = new Hls({autoStartLoad: false});
            }
            this.hls.loadSource(config.videoSources[0]);
            this.hls.attachMedia(this.video);

            this.hls.on(Hls.Events.ERROR, this.onError.bind(this));

            this.hls.on(Hls.Events.MANIFEST_PARSED, function(event, data) {
                console.log(
                    '[HLS Video]: MANIFEST_PARSED, qualityLevelsInfo: ',
                    data.levels.map(function(level) {
                        return {
                            bitrate: level.bitrate,
                            resolution: level.width + 'x' + level.height
                        };
                    })
                );
                self.config.onReadyHLS();
            });
            this.hls.on(Hls.Events.LEVEL_SWITCHED, function(event, data) {
                let level = self.hls.levels[data.level];
                console.log(
                    '[HLS Video]: LEVEL_SWITCHED, qualityLevelInfo: ',
                    {
                        bitrate: level.bitrate,
                        resolution: level.width + 'x' + level.height
                    }
                );
            });
        }
    }

    Player.prototype = Object.create(HTML5Video.Player.prototype);
    Player.prototype.constructor = Player;

    Player.prototype.playVideo = function() {
        HTML5Video.Player.prototype.updatePlayerLoadingState.apply(this, ['show']);
        if (!this.config.browserIsSafari) {
            this.hls.startLoad();
        }
        HTML5Video.Player.prototype.playVideo.apply(this);
    };

    Player.prototype.pauseVideo = function() {
        HTML5Video.Player.prototype.pauseVideo.apply(this);
        HTML5Video.Player.prototype.updatePlayerLoadingState.apply(this, ['hide']);
    };

    Player.prototype.onPlaying = function() {
        HTML5Video.Player.prototype.onPlaying.apply(this);
        HTML5Video.Player.prototype.updatePlayerLoadingState.apply(this, ['hide']);
    };

    Player.prototype.onReady = function() {
        this.config.events.onReady(null);
    };

    /**
     * Handler for HLS video errors. This only takes care of fatal erros, non-fatal errors
     * are automatically handled by hls.js
     *
     * @param {String} event `hlsError`
     * @param {Object} data  Contains the information regarding error occurred.
     */
    Player.prototype.onError = function(event, data) {
        if (data.fatal) {
            switch (data.type) {
            case Hls.ErrorTypes.NETWORK_ERROR:
                console.error(
                    '[HLS Video]: Fatal network error encountered, try to recover. Details: %s',
                    data.details
                );
                this.hls.startLoad();
                break;
            case Hls.ErrorTypes.MEDIA_ERROR:
                console.error(
                    '[HLS Video]: Fatal media error encountered, try to recover. Details: %s',
                    data.details
                );
                this.hls.recoverMediaError();
                break;
            default:
                console.error(
                    '[HLS Video]: Unrecoverable error encountered. Details: %s',
                    data.details
                );
                break;
            }
        }
    };

    return Player;
}());

export default HLSVideo;
