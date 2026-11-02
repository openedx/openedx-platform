'use strict';

import _ from 'underscore';


/**
 * Events module.
 * @exports video/09_events_plugin.js
 * @constructor
 * @param {Object} state The object containing the state of the video
 * @param {Object} i18n The object containing strings with translations.
 * @param {Object} options
 * @return {jquery Promise}
 */
let EventsPlugin = function(state, i18n, options) {
    if (!(this instanceof EventsPlugin)) {
        return new EventsPlugin(state, i18n, options);
    }

    _.bindAll(this, 'onReady', 'onPlay', 'onPause', 'onComplete', 'onEnded', 'onSeek',
        'onSpeedChange', 'onAutoAdvanceChange', 'onShowLanguageMenu', 'onHideLanguageMenu',
        'onSkip', 'onShowTranscript', 'onHideTranscript', 'onShowCaptions', 'onHideCaptions',
        'destroy');

    this.state = state;
    this.options = _.extend({}, options);
    this.state.videoEventsPlugin = this;
    this.i18n = i18n;
    this.initialize();

    return $.Deferred().resolve().promise();
};

EventsPlugin.moduleName = 'EventsPlugin';
EventsPlugin.prototype = {
    destroy: function() {
        this.state.el.off(this.events);
        delete this.state.videoEventsPlugin;
    },

    initialize: function() {
        this.events = {
            ready: this.onReady,
            play: this.onPlay,
            pause: this.onPause,
            complete: this.onComplete,
            'ended stop': this.onEnded,
            seek: this.onSeek,
            skip: this.onSkip,
            speedchange: this.onSpeedChange,
            autoadvancechange: this.onAutoAdvanceChange,
            'language_menu:show': this.onShowLanguageMenu,
            'language_menu:hide': this.onHideLanguageMenu,
            'transcript:show': this.onShowTranscript,
            'transcript:hide': this.onHideTranscript,
            'captions:show': this.onShowCaptions,
            'captions:hide': this.onHideCaptions,
            destroy: this.destroy
        };
        this.bindHandlers();
        this.emitPlayVideoEvent = true;
    },

    bindHandlers: function() {
        this.state.el.on(this.events);
    },

    onReady: function() {
        this.log('load_video');
    },

    onPlay: function() {
        if (this.emitPlayVideoEvent) {
            this.log('play_video', {currentTime: this.getCurrentTime()});
            this.emitPlayVideoEvent = false;
        }
    },

    onPause: function() {
        this.log('pause_video', {currentTime: this.getCurrentTime()});
        this.emitPlayVideoEvent = true;
    },

    onComplete: function() {
        this.log('complete_video', {currentTime: this.getCurrentTime()});
    },

    onEnded: function() {
        this.log('stop_video', {currentTime: this.getCurrentTime()});
        this.emitPlayVideoEvent = true;
    },

    onSkip: function(event, doNotShowAgain) {
        let info = {currentTime: this.getCurrentTime()};
        let eventName = doNotShowAgain ? 'do_not_show_again_video' : 'skip_video';
        this.log(eventName, info);
    },

    onSeek: function(event, time, oldTime, type) {
        this.log('seek_video', {
            old_time: oldTime,
            new_time: time,
            type: type
        });
        this.emitPlayVideoEvent = true;
    },

    onSpeedChange: function(event, newSpeed, oldSpeed) {
        this.log('speed_change_video', {
            current_time: this.getCurrentTime(),
            old_speed: this.state.speedToString(oldSpeed),
            new_speed: this.state.speedToString(newSpeed)
        });
    },

    onAutoAdvanceChange: function(event, enabled) {
        this.log('auto_advance_change_video', {
            enabled: enabled
        });
    },

    onShowLanguageMenu: function() {
        this.log('edx.video.language_menu.shown');
    },

    onHideLanguageMenu: function() {
        this.log('edx.video.language_menu.hidden', {language: this.getCurrentLanguage()});
    },

    onShowTranscript: function() {
        this.log('show_transcript', {current_time: this.getCurrentTime()});
    },

    onHideTranscript: function() {
        this.log('hide_transcript', {current_time: this.getCurrentTime()});
    },

    onShowCaptions: function() {
        this.log('edx.video.closed_captions.shown', {current_time: this.getCurrentTime()});
    },

    onHideCaptions: function() {
        this.log('edx.video.closed_captions.hidden', {current_time: this.getCurrentTime()});
    },

    getCurrentTime: function() {
        let player = this.state.videoPlayer;
        let startTime = this.state.config.startTime;
        let currentTime = player ? player.currentTime : 0;
        // if video didn't start from 0(it's a subsection of video), subtract the additional time at start
        if (startTime) {
            currentTime = currentTime ? currentTime - startTime : 0;
        }
        return currentTime;
    },

    getCurrentLanguage: function() {
        let language = this.state.lang;
        return language;
    },

    log: function(eventName, data) {
        // use startTime and endTime to calculate the duration to handle the case where only a subsection of video is used
        let endTime = this.state.config.endTime || this.state.duration;
        let startTime = this.state.config.startTime || 0;

        let logInfo = _.extend({
            id: this.state.id,
            // eslint-disable-next-line no-nested-ternary
            code: this.state.isYoutubeType() ? this.state.youtubeId() : this.state.canPlayHLS ? 'hls' : 'html5',
            duration: endTime - startTime
        }, data, this.options.data);
        Logger.log(eventName, logInfo);
    }
};

export default EventsPlugin;
