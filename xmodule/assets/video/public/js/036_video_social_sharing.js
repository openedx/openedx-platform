// eslint-disable-next-line lines-around-directive
'use strict';

import _ from 'underscore';


/**
 * Video Social Sharing control module.
 * @exports video/036_video_social_sharing.js
 * @constructor
 * @param {jquery Element} element
 * @param {Object} options
 */
let VideoSocialSharingHandler = function(element, options) {
    if (!(this instanceof VideoSocialSharingHandler)) {
        return new VideoSocialSharingHandler(element, options);
    }

    _.bindAll(this, 'clickHandler');
    _.bindAll(this, 'copyHandler');
    _.bindAll(this, 'hideHandler');
    _.bindAll(this, 'showHandler');

    this.container = element;

    if (this.container.find('.wrapper-downloads .wrapper-social-share')) {
        this.initialize();
    }

    return false;
};

VideoSocialSharingHandler.prototype = {

    // Initializes the module.
    initialize: function() {
        this.el = this.container.find('.wrapper-social-share');
        this.baseVideoUrl = this.el.data('url');
        this.course_id = this.container.data('courseId');
        this.block_id = this.container.data('blockId');
        this.el.on('click', '.social-share-link', this.clickHandler);

        this.closeBtn = this.el.find('.close-btn');
        this.toggleBtn = this.el.find('.social-toggle-btn');
        this.copyBtn = this.el.find('.public-video-copy-btn');
        this.shareContainer = this.el.find('.container-social-share');
        this.closeBtn.on('click', this.hideHandler);
        this.toggleBtn.on('click', this.showHandler);
        this.copyBtn.on('click', this.copyHandler);
    },

    // Fire an analytics event on share button click.
    clickHandler: function(event) {
        let source = $(event.currentTarget).data('source');
        this.sendAnalyticsEvent(source);
    },

    hideHandler: function(event) {
        this.shareContainer.hide();
        this.toggleBtn.show();
    },

    showHandler: function(event) {
        this.shareContainer.show();
        this.toggleBtn.hide();
    },

    copyHandler: function(event) {
        navigator.clipboard.writeText(this.copyBtn.data('url'));
    },

    // Send an analytics event for share button tracking.
    sendAnalyticsEvent: function(source) {
        window.analytics.track(
            'edx.social.video.share_button.clicked',
            {
                source,
                video_block_id: this.container.data('blockId'),
                course_id: this.container.data('courseId'),
            }
        );
    }
};

export default VideoSocialSharingHandler;
