'use strict';

import _ from 'underscore';


let VideoPoster = function(element, options) {
    if (!(this instanceof VideoPoster)) {
        return new VideoPoster(element, options);
    }

    _.bindAll(this, 'onClick', 'destroy');
    this.element = element;
    this.container = element.find('.video-player');
    this.options = options || {};
    this.initialize();
};

VideoPoster.moduleName = 'Poster';
VideoPoster.prototype = {
    template: _.template([
        '<div class="video-pre-roll is-<%- type %> poster" ',
        'style="background-image: url(<%- url %>)">',
        '<button class="btn-play btn-pre-roll">',
        '<img src="/static/images/play.png" alt="">',
        '<span class="sr">', gettext('Play video'), '</span>',
        '</button>',
        '</div>'
    ].join('')),

    initialize: function() {
        this.el = $(this.template({
            url: this.options.poster.url,
            type: this.options.poster.type
        }));
        this.element.addClass('is-pre-roll');
        this.render();
        this.bindHandlers();
    },

    bindHandlers: function() {
        this.el.on('click', this.onClick);
        this.element.on('destroy', this.destroy);
    },

    render: function() {
        this.container.append(this.el);
    },

    onClick: function() {
        if (_.isFunction(this.options.onClick)) {
            this.options.onClick();
        }
        this.destroy();
    },

    destroy: function() {
        this.element.off('destroy', this.destroy).removeClass('is-pre-roll');
        this.el.remove();
    }
};

export default VideoPoster;
