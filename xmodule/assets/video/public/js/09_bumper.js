'use strict';

/**
 * VideoBumper module.
 * @exports video/09_bumper.js
 * @constructor
 * @param {Object} player The player factory.
 * @param {Object} state The object containing the state of the video
 * @return {jquery Promise}
 */
let VideoBumper = function(player, state) {
    if (!(this instanceof VideoBumper)) {
        return new VideoBumper(player, state);
    }

    _.bindAll(
        this, 'showMainVideoHandler', 'destroy', 'skipByDuration', 'destroyAndResolve'
    );
    this.dfd = $.Deferred();
    this.element = state.el;
    this.element.addClass('is-bumper');
    this.player = player;
    this.state = state;
    this.doNotShowAgain = false;
    this.state.videoBumper = this;
    this.bindHandlers();
    this.initialize();
    this.maxBumperDuration = 35; // seconds
};

VideoBumper.prototype = {
    initialize: function() {
        this.player();
    },

    getPromise: function() {
        return this.dfd.promise();
    },

    showMainVideoHandler: function() {
        this.state.storage.setItem('isBumperShown', true);
        setTimeout(function() {
            this.saveState();
            this.showMainVideo();
        }.bind(this), 20);
    },

    destroyAndResolve: function() {
        this.destroy();
        this.dfd.resolve();
    },

    showMainVideo: function() {
        if (this.state.videoPlayer) {
            this.destroyAndResolve();
        } else {
            this.state.el.on('initialize', this.destroyAndResolve);
        }
    },

    skip: function() {
        this.element.trigger('skip', [this.doNotShowAgain]);
        this.showMainVideoHandler();
    },

    skipAndDoNotShowAgain: function() {
        this.doNotShowAgain = true;
        this.skip();
    },

    skipByDuration: function(event, time) {
        if (time > this.maxBumperDuration) {
            this.element.trigger('ended');
        }
    },

    bindHandlers: function() {
        let events = ['ended', 'error'].join(' ');
        this.element.on(events, this.showMainVideoHandler);
        this.element.on('timeupdate', this.skipByDuration);
    },

    saveState: function() {
        let info = {bumper_last_view_date: true};
        if (this.doNotShowAgain) {
            _.extend(info, {bumper_do_not_show_again: true});
        }
        if (this.state.videoSaveStatePlugin) {
            this.state.videoSaveStatePlugin.saveState(true, info);
        }
    },

    destroy: function() {
        let events = ['ended', 'error'].join(' ');
        this.element.off(events, this.showMainVideoHandler);
        this.element.off({
            timeupdate: this.skipByDuration,
            initialize: this.destroyAndResolve
        });
        this.element.removeClass('is-bumper');
        if (_.isFunction(this.state.videoPlayer.destroy)) {
            this.state.videoPlayer.destroy();
        }
        delete this.state.videoBumper;
    }
};

export default VideoBumper;
