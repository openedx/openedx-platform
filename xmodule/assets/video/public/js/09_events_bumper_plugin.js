'use strict';

import _ from 'underscore';


/**
 * Events module.
 * @exports video/09_events_bumper_plugin.js
 * @constructor
 * @param {Object} state The object containing the state of the video
 * @param {Object} i18n The object containing strings with translations.
 * @param {Object} options
 * @return {jquery Promise}
 */
let EventsBumperPlugin = function(state, i18n, options) {
    if (!(this instanceof EventsBumperPlugin)) {
        return new EventsBumperPlugin(state, i18n, options);
    }

    _.bindAll(this, 'onReady', 'onPlay', 'onEnded', 'onShowLanguageMenu', 'onHideLanguageMenu', 'onSkip',
        'onShowCaptions', 'onHideCaptions', 'destroy');
    this.state = state;
    this.options = _.extend({}, options);
    this.state.videoEventsBumperPlugin = this;
    this.i18n = i18n;
    this.initialize();

    return $.Deferred().resolve().promise();
};

EventsBumperPlugin.moduleName = 'EventsBumperPlugin';
EventsBumperPlugin.prototype = {
    destroy: function() {
        this.state.el.off(this.events);
        delete this.state.videoEventsBumperPlugin;
    },

    initialize: function() {
        this.events = {
            ready: this.onReady,
            play: this.onPlay,
            'ended stop': this.onEnded,
            skip: this.onSkip,
            'language_menu:show': this.onShowLanguageMenu,
            'language_menu:hide': this.onHideLanguageMenu,
            'captions:show': this.onShowCaptions,
            'captions:hide': this.onHideCaptions,
            destroy: this.destroy
        };
        this.bindHandlers();
    },

    bindHandlers: function() {
        this.state.el.on(this.events);
    },

    onReady: function() {
        this.log('edx.video.bumper.loaded');
    },

    onPlay: function() {
        this.log('edx.video.bumper.played', {currentTime: this.getCurrentTime()});
    },

    onEnded: function() {
        this.log('edx.video.bumper.stopped', {currentTime: this.getCurrentTime()});
    },

    onSkip: function(event, doNotShowAgain) {
        let info = {currentTime: this.getCurrentTime()};
        let eventName = 'edx.video.bumper.' + (doNotShowAgain ? 'dismissed' : 'skipped');
        this.log(eventName, info);
    },

    onShowLanguageMenu: function() {
        this.log('edx.video.bumper.transcript.menu.shown');
    },

    onHideLanguageMenu: function() {
        this.log('edx.video.bumper.transcript.menu.hidden');
    },

    onShowCaptions: function() {
        this.log('edx.video.bumper.transcript.shown', {currentTime: this.getCurrentTime()});
    },

    onHideCaptions: function() {
        this.log('edx.video.bumper.transcript.hidden', {currentTime: this.getCurrentTime()});
    },

    getCurrentTime: function() {
        let player = this.state.videoPlayer;
        return player ? player.currentTime : 0;
    },

    getDuration: function() {
        let player = this.state.videoPlayer;
        return player ? player.duration() : 0;
    },

    log: function(eventName, data) {
        let logInfo = _.extend({
            host_component_id: this.state.id,
            bumper_id: this.state.config.sources[0] || '',
            duration: this.getDuration(),
            code: 'html5'
        }, data, this.options.data);
        Logger.log(eventName, logInfo);
    }
};

export default EventsBumperPlugin;
