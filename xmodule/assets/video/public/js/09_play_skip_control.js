'use strict';

/**
 * Play/skip control module.
 * @exports video/09_play_skip_control.js
 * @constructor
 * @param {Object} state The object containing the state of the video
 * @param {Object} i18n The object containing strings with translations.
 * @return {jquery Promise}
 */
let PlaySkipControl = function(state, i18n) {
    if (!(this instanceof PlaySkipControl)) {
        return new PlaySkipControl(state, i18n);
    }

    _.bindAll(this, 'play', 'onClick', 'destroy');
    this.state = state;
    this.state.videoPlaySkipControl = this;
    this.i18n = i18n;
    this.initialize();

    return $.Deferred().resolve().promise();
};

PlaySkipControl.prototype = {
    template: [
        '<button class="control video_control play play-skip-control" title="',
        gettext('Play'),
        '">',
        '<span class="icon fa fa-play" aria-hidden="true"></span>',
        '</button>'
    ].join(''),

    destroy: function() {
        this.el.remove();
        this.state.el.off('destroy', this.destroy);
        delete this.state.videoPlaySkipControl;
    },

    /** Initializes the module. */
    initialize: function() {
        this.el = $(this.template);
        this.render();
        this.bindHandlers();
    },

    /**
     * Creates any necessary DOM elements, attach them, and set their,
     * initial configuration.
     */
    render: function() {
        this.state.el.find('.vcr').prepend(this.el);
    },

    /** Bind any necessary function callbacks to DOM events. */
    bindHandlers: function() {
        this.el.on('click', this.onClick);
        this.state.el.on({
            play: this.play,
            destroy: this.destroy
        });
    },

    onClick: function(event) {
        event.preventDefault();
        if (this.state.videoPlayer.isPlaying()) {
            this.state.videoCommands.execute('skip');
        } else {
            this.state.videoCommands.execute('play');
        }
    },

    play: function() {
        this.el
            .removeClass('play')
            .addClass('skip')
            .attr('title', gettext('Skip'))
            .find('.icon')
            .removeClass('fa-play')
            .addClass('fa-step-forward');
        // Disable possibility to pause the video.
        this.state.el.find('video').off('click');
    }
};

export default PlaySkipControl;
