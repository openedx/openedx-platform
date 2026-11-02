'use strict';

import _ from 'underscore';
import { convert, format, formatFull } from './utils/time.js';


/**
 * Save state module.
 * @exports video/09_save_state_plugin.js
 * @constructor
 * @param {Object} state The object containing the state of the video
 * @param {Object} i18n The object containing strings with translations.
 * @param {Object} options
 * @return {jquery Promise}
 */
let SaveStatePlugin = function(state, i18n, options) {
    if (!(this instanceof SaveStatePlugin)) {
        return new SaveStatePlugin(state, i18n, options);
    }

    _.bindAll(this, 'onSpeedChange', 'onAutoAdvanceChange', 'saveStateHandler', 'bindUnloadHandler', 'onUnload',
        'onYoutubeAvailability', 'onLanguageChange', 'destroy');
    this.state = state;
    this.options = _.extend({events: []}, options);
    this.state.videoSaveStatePlugin = this;
    this.i18n = i18n;
    this.initialize();

    return $.Deferred().resolve().promise();
};

SaveStatePlugin.moduleName = 'SaveStatePlugin';
SaveStatePlugin.prototype = {
    destroy: function() {
        this.state.el.off(this.events).off('destroy', this.destroy);
        $(window).off('unload', this.onUnload);
        delete this.state.videoSaveStatePlugin;
    },

    initialize: function() {
        this.events = {
            speedchange: this.onSpeedChange,
            autoadvancechange: this.onAutoAdvanceChange,
            play: this.bindUnloadHandler,
            'pause destroy': this.saveStateHandler,
            'language_menu:change': this.onLanguageChange,
            youtube_availability: this.onYoutubeAvailability
        };
        this.bindHandlers();
    },

    bindHandlers: function() {
        if (this.options.events.length) {
            _.each(this.options.events, function(eventName) {
                let callback;
                if (_.has(this.events, eventName)) {
                    callback = this.events[eventName];
                    this.state.el.on(eventName, callback);
                }
            }, this);
        } else {
            this.state.el.on(this.events);
        }
        this.state.el.on('destroy', this.destroy);
    },

    bindUnloadHandler: _.once(function() {
        $(window).on('unload.video', this.onUnload);
    }),

    onSpeedChange: function(event, newSpeed) {
        this.saveState(true, {speed: newSpeed});
        this.state.storage.setItem('speed', newSpeed, true);
        this.state.storage.setItem('general_speed', newSpeed);
    },

    onAutoAdvanceChange: function(event, enabled) {
        this.saveState(true, {auto_advance: enabled});
        this.state.storage.setItem('auto_advance', enabled);
    },

    saveStateHandler: function() {
        this.saveState(true);
    },

    onUnload: function() {
        this.saveState();
    },

    onLanguageChange: function(event, langCode) {
        this.state.storage.setItem('language', langCode);
    },

    onYoutubeAvailability: function(event, youtubeIsAvailable) {
        // Compare what the client-side code has determined Youtube
        // availability to be (true/false) vs. what the LMS recorded for
        // this user. The LMS will assume YouTube is available by default.
        if (youtubeIsAvailable !== this.state.config.recordedYoutubeIsAvailable) {
            this.saveState(true, {youtube_is_available: youtubeIsAvailable});
        }
    },

    saveState: function(async, data) {
        if (this.state.config.saveStateEnabled) {
            if (!($.isPlainObject(data))) {
                data = {
                    saved_video_position: this.state.videoPlayer.currentTime
                };
            }

            if (data.speed) {
                this.state.storage.setItem('speed', data.speed, true);
            }

            if (_.has(data, 'saved_video_position')) {
                this.state.storage.setItem('savedVideoPosition', data.saved_video_position, true);
                data.saved_video_position = formatFull(data.saved_video_position);
            }

            $.ajax({
                url: this.state.config.saveStateUrl,
                type: 'POST',
                async: !!async,
                dataType: 'json',
                data: data
            });
        }
    }
};

export default SaveStatePlugin;
