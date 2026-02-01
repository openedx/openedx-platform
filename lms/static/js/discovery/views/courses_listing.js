(function(define) {
    define([
        'jquery',
        'underscore',
        'backbone',
        'gettext',
        'js/discovery/views/course_card',
        'edx-ui-toolkit/js/utils/html-utils'
    ], function($, _, Backbone, gettext, CourseCardView, HtmlUtils) {
        'use strict';

        return Backbone.View.extend({

            el: 'div.courses',
            $window: $(window),
            $document: $(document),

            initialize: function() {
                this.$list = this.$el.find('.courses-listing');
                this.attachScrollHandler();
            },

            render: function() {
                this.$list.empty();
                this.renderItems();
                return this;
            },

            renderNext: function() {
                this.renderItems();
                this.isLoading = false;
            },

            renderItems: function() {
                /* eslint no-param-reassign: [2, { "props": true }] */
                var latest = this.model.latest();
                var items = latest.map(function(result) {
                    result.userPreferences = this.model.userPreferences;
                    var item = new CourseCardView({model: result});
                    return item.render().el;
                }, this);
                console.log("Rendering", items.length, "course cards");

                HtmlUtils.append(
                    this.$list,
                    HtmlUtils.HTML(items)
                );
                /* eslint no-param-reassign: [2, { "props": false }] */
            },

            attachScrollHandler: function() {
                this.$window.on('scroll', _.throttle(this.scrollHandler.bind(this), 400));
            },

            scrollHandler: function() {
                if (this.isNearBottom() && !this.isLoading) {
                    this.trigger('next');
                    this.isLoading = true;
                }
            },

            isNearBottom: function() {
                var scrollBottom = this.$window.scrollTop() + this.$window.height();
                var threshold = this.$document.height() - 200;
                return scrollBottom >= threshold;
            }

        });
    });
}(define || RequireJS.define));
