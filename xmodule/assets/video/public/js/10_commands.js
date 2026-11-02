'use strict';

import _ from 'underscore';


/**
 * Video commands module.
 * @exports video/10_commands.js
 * @constructor
 * @param {Object} state The object containing the state of the video
 * @param {Object} i18n The object containing strings with translations.
 * @return {jquery Promise}
 */
let VideoCommands = function(state, i18n) {
    if (!(this instanceof VideoCommands)) {
        return new VideoCommands(state, i18n);
    }

    _.bindAll(this, 'destroy');
    this.state = state;
    this.state.videoCommands = this;
    this.i18n = i18n;
    this.commands = [];
    this.initialize();

    return $.Deferred().resolve().promise();
};

VideoCommands.prototype = {
    destroy: function() {
        this.state.el.off('destroy', this.destroy);
        delete this.state.videoCommands;
    },

    /** Initializes the module. */
    initialize: function() {
        this.commands = this.getCommands();
        this.state.el.on('destroy', this.destroy);
    },

    execute: function(command) {
        let args = [].slice.call(arguments, 1) || [];

        if (_.has(this.commands, command)) {
            this.commands[command].execute.apply(this, [this.state].concat(args));
        } else {
            console.log('Command "' + command + '" is not available.');
        }
    },

    getCommands: function() {
        let commands = {};
        let commandsList = [
            playCommand, pauseCommand, togglePlaybackCommand,
            toggleMuteCommand, toggleFullScreenCommand, setSpeedCommand,
            skipCommand
        ];

        _.each(commandsList, function(command) {
            commands[command.name] = command;
        }, this);

        return commands;
    }
};

let Command = function(name, execute) {
    this.name = name;
    this.execute = execute;
};

let playCommand = new Command('play', function(state) {
    state.videoPlayer.play();
});

let pauseCommand = new Command('pause', function(state) {
    state.videoPlayer.pause();
});

let togglePlaybackCommand = new Command('togglePlayback', function(state) {
    if (state.videoPlayer.isPlaying()) {
        pauseCommand.execute(state);
    } else {
        playCommand.execute(state);
    }
});

let toggleMuteCommand = new Command('toggleMute', function(state) {
    state.videoVolumeControl.toggleMute();
});

let toggleFullScreenCommand = new Command('toggleFullScreen', function(state) {
    state.videoFullScreen.toggle();
});

let setSpeedCommand = new Command('speed', function(state, speed) {
    state.videoSpeedControl.setSpeed(state.speedToString(speed));
});

let skipCommand = new Command('skip', function(state, doNotShowAgain) {
    if (doNotShowAgain) {
        state.videoBumper.skipAndDoNotShowAgain();
    } else {
        state.videoBumper.skip();
    }
});

export default VideoCommands;
