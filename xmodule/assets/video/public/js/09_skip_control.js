'use strict';

import _ from 'underscore';


/**
 * Video skip control module.
 * @exports video/09_skip_control.js
 * @constructor
 * @param {Object} state The object containing the state of the video
 * @param {Object} i18n The object containing strings with translations.
 * @return {jquery Promise}
 */
let SkipControl = function(state, i18n) {
    if (!(this instanceof SkipControl)) {
        return new SkipControl(state, i18n);
    }

    _.bindAll(this, 'onClick', 'render', 'destroy');
    this.state = state;
    this.state.videoSkipControl = this;
    this.i18n = i18n;
    this.initialize();

    return $.Deferred().resolve().promise();
};

SkipControl.prototype = {
    template: [
        '<button class="control video_control skip skip-control" aria-disabled="false" title="',
        gettext('Do not show again'),
        '">',
        '<span class="icon fa fa-step-forward" aria-hidden="true"></span>',
        '</button>'
    ].join(''),

    destroy: function() {
        this.el.remove();
        this.state.el.off('.skip');
        delete this.state.videoSkipControl;
    },

    /** Initializes the module. */
    initialize: function() {
        this.el = $(this.template);
        this.bindHandlers();
    },

    /**
     * Creates any necessary DOM elements, attach them, and set their,
     * initial configuration.
     */
    render: function() {
        this.state.el.find('.vcr .control').after(this.el);
    },

    /** Bind any necessary function callbacks to DOM events. */
    bindHandlers: function() {
        this.el.on('click', this.onClick);
        this.state.el.on({
            'play.skip': _.once(this.render),
            'destroy.skip': this.destroy
        });
    },

    onClick: function(event) {
        event.preventDefault();
        this.state.videoCommands.execute('skip', true);
    }
};

export default SkipControl;
