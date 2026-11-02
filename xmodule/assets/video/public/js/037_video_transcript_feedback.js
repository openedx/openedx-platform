// VideoTranscriptFeedbackHandler module.
'use strict';

import _ from 'underscore';

/**
 * @desc VideoTranscriptFeedback module exports a function.
 *
 * @type {function}
 * @access public
 *
 * @param {object} state - The object containing the state of the video
 *     player. All other modules, their parameters, public variables, etc.
 *     are available via this object.
 *
 * @this {object} The global window object.
 *
 */

let VideoTranscriptFeedback = function(state) {
    if (!(this instanceof VideoTranscriptFeedback)) {
        return new VideoTranscriptFeedback(state);
    }

    _.bindAll(this, 'destroy', 'getFeedbackForCurrentTranscript', 'markAsPositiveFeedback', 'markAsNegativeFeedback', 'markAsEmptyFeedback',
        'selectThumbsUp', 'selectThumbsDown', 'unselectThumbsUp', 'unselectThumbsDown', 'thumbsUpClickHandler', 'thumbsDownClickHandler',
        'sendFeedbackForCurrentTranscript', 'onHideLanguageMenu', 'getCurrentLanguage', 'loadAndSetVisibility', 'showWidget', 'hideWidget'
    );

    this.state = state;
    this.state.videoTranscriptFeedback = this;
    this.currentTranscriptLanguage = this.state.lang;
    this.transcriptLanguages = this.state.config.transcriptLanguages;

    if (this.state.el.find('.wrapper-transcript-feedback').length) {
        this.initialize();
    }

    return false;
};

VideoTranscriptFeedback.prototype = {
    destroy: function () {
        this.state.el.off(this.events);
    },

    initialize: function () {
        this.el = this.state.el.find('.wrapper-transcript-feedback');

        this.videoId = this.el.data('video-id');
        this.userId = this.el.data('user-id');
        this.aiTranslationsUrl = this.state.config.aiTranslationsUrl;

        this.thumbsUpButton = this.el.find('.thumbs-up-btn');
        this.thumbsDownButton = this.el.find('.thumbs-down-btn');
        this.thumbsUpButton.on('click', this.thumbsUpClickHandler);
        this.thumbsDownButton.on('click', this.thumbsDownClickHandler);

        this.events = {
            'language_menu:hide': this.onHideLanguageMenu,
            destroy: this.destroy
        };
        this.loadAndSetVisibility();
        this.bindHandlers();
    },

    bindHandlers: function () {
        this.state.el.on(this.events);
    },

    getFeedbackForCurrentTranscript: function () {
        let self = this;
        let url = self.aiTranslationsUrl + '/transcript-feedback' + '?transcript_language=' + self.currentTranscriptLanguage + '&video_id=' + self.videoId + '&user_id=' + self.userId;

        $.ajax({
            url: url,
            type: 'GET',
            success: function (data) {
                if (data && data.value === true) {
                    self.markAsPositiveFeedback();
                    self.currentFeedback = true;
                } else {
                    if (data && data.value === false) {
                        self.markAsNegativeFeedback();
                        self.currentFeedback = false;
                    } else {
                        self.markAsEmptyFeedback();
                        self.currentFeedback = null;
                    }
                }
            },
            error: function (error) {
                self.markAsEmptyFeedback();
                self.currentFeedback = null;
            }
        });
    },

    markAsPositiveFeedback: function () {
        this.selectThumbsUp();
        this.unselectThumbsDown();
    },

    markAsNegativeFeedback: function () {
        this.selectThumbsDown();
        this.unselectThumbsUp();
    },

    markAsEmptyFeedback: function () {
        this.unselectThumbsUp();
        this.unselectThumbsDown();
    },

    selectThumbsUp: function () {
        let thumbsUpIcon = this.thumbsUpButton.find('.thumbs-up-icon');
        if (thumbsUpIcon[0].classList.contains('fa-thumbs-o-up')) {
            thumbsUpIcon[0].classList.remove("fa-thumbs-o-up");
            thumbsUpIcon[0].classList.add("fa-thumbs-up");
        }
    },

    selectThumbsDown: function () {
        let thumbsDownIcon = this.thumbsDownButton.find('.thumbs-down-icon');
        if (thumbsDownIcon[0].classList.contains('fa-thumbs-o-down')) {
            thumbsDownIcon[0].classList.remove("fa-thumbs-o-down");
            thumbsDownIcon[0].classList.add("fa-thumbs-down");
        }
    },

    unselectThumbsUp: function () {
        let thumbsUpIcon = this.thumbsUpButton.find('.thumbs-up-icon');
        if (thumbsUpIcon[0].classList.contains('fa-thumbs-up')) {
            thumbsUpIcon[0].classList.remove("fa-thumbs-up");
            thumbsUpIcon[0].classList.add("fa-thumbs-o-up");
        }
    },

    unselectThumbsDown: function () {
        let thumbsDownIcon = this.thumbsDownButton.find('.thumbs-down-icon');
        if (thumbsDownIcon[0].classList.contains('fa-thumbs-down')) {
            thumbsDownIcon[0].classList.remove("fa-thumbs-down");
            thumbsDownIcon[0].classList.add("fa-thumbs-o-down");
        }
    },

    thumbsUpClickHandler: function () {
        if (this.currentFeedback) {
            this.sendFeedbackForCurrentTranscript(null);
        } else {
            this.sendFeedbackForCurrentTranscript(true);
        }
    },

    thumbsDownClickHandler: function () {
        if (this.currentFeedback === false) {
            this.sendFeedbackForCurrentTranscript(null);
        } else {
            this.sendFeedbackForCurrentTranscript(false);
        }
    },

    sendFeedbackForCurrentTranscript: function (feedbackValue) {
        let self = this;
        let url = self.aiTranslationsUrl + '/transcript-feedback/';
        $.ajax({
            url: url,
            type: 'POST',
            dataType: 'json',
            data: {
                transcript_language: self.currentTranscriptLanguage,
                video_id: self.videoId,
                user_id: self.userId,
                value: feedbackValue,
            },
            success: function (data) {
                if (data && data.value === true) {
                    self.markAsPositiveFeedback();
                    self.currentFeedback = true;
                } else {
                    if (data && data.value === false) {
                        self.markAsNegativeFeedback();
                        self.currentFeedback = false;
                    } else {
                        self.markAsEmptyFeedback();
                        self.currentFeedback = null;
                    }
                }
            },
            error: function () {
                self.markAsEmptyFeedback();
                self.currentFeedback = null;
            }
        });
    },

    onHideLanguageMenu: function () {
        let newLanguageSelected = this.getCurrentLanguage();
        if (this.currentTranscriptLanguage !== newLanguageSelected) {
            this.currentTranscriptLanguage = this.getCurrentLanguage();
            this.loadAndSetVisibility();
        }
    },

    getCurrentLanguage: function () {
        let language = this.state.lang;
        return language;
    },

    loadAndSetVisibility: function () {
        let self = this;
        let url = self.aiTranslationsUrl + '/video-transcript' + '?transcript_language=' + self.currentTranscriptLanguage + '&video_id=' + self.videoId;

        $.ajax({
            url: url,
            type: 'GET',
            async: false,
            success: function (data) {
                if (data && data.status === 'Completed') {
                    self.showWidget();
                    self.getFeedbackForCurrentTranscript();
                } else {
                    self.hideWidget();
                }
            },
            error: function (error) {
                self.hideWidget();
            }
        });
    },

    showWidget: function () {
        this.el.show();
    },

    hideWidget: function () {
        this.el.hide();
    }
};

export default VideoTranscriptFeedback;
