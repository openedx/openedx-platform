(function(define) {
    define([
        'jquery',
        'underscore',
        'backbone',
        'gettext',
        'js/discovery/models/filter',
        'js/discovery/views/filter_label',
        'edx-ui-toolkit/js/utils/html-utils'
    ], function($, _, Backbone, gettext, Filter, FilterLabel, HtmlUtils) {
        'use strict';

        return Backbone.View.extend({

            el: '#filter-bar',
            templateId: '#filter_bar-tpl',

            events: {
                'click #clear-all-filters': 'clearAll',
                'click li .discovery-button': 'clearFilter'
            },

            initialize: function() {
                this.tpl = HtmlUtils.template($(this.templateId).html());
                this.render();
                this.listenTo(this.collection, 'remove', this.hideIfEmpty);
                this.listenTo(this.collection, 'add', this.addFilter);
                this.listenTo(this.collection, 'reset', this.resetFilters);
            },

            render: function() {
                HtmlUtils.setHtml(
                    this.$el,
                    this.tpl()
                );
                this.$ul = this.$el.find('ul');
                this.$el.addClass('is-animated');
                return this;
            },

            addFilter: function(filter) {
                var label = new FilterLabel({model: filter});
                this.$ul.append(label.render().el);
                this.show();
            },

            hideIfEmpty: function() {
                if (this.collection.isEmpty()) {
                    this.hide();
                }
            },

            resetFilters: function() {
                this.$ul.empty();
                this.hide();
            },

            // clearFilter: function(event) {
            //     var $target = $(event.currentTarget);
            //     var filter = this.collection.get($target.data('type'));
            //     this.trigger('clearFilter', filter.id);
            // },
            clearFilter: function(event) {
                var $target = $(event.currentTarget);  // event.currentTarget is .discovery-button
                var type = $target.data('type');
                var query = $target.data('query');

                if (!type || !query) {
                    console.warn('Missing data-type or data-query');
                    return;
                }

                var uid = type + '|' + query;
                var filter = this.collection.get(uid);

                if (!filter) {
                    console.warn('Filter not found for uid:', uid);
                    return;
                }

                this.collection.remove(filter);
                   // ✅ Uncheck the matching checkbox
                   // Unselect the corresponding filter button in the facet list
                var selector = 'button.facet-option.discovery-button[data-facet="' + type + '"][data-value="' + query + '"]';
                var $button = $(selector);

                if ($button.length) {
                    $button.removeClass('selected');
                } else {
                    console.warn('Filter button not found to unselect:', selector);
                }

                // ✅ Trigger new search with updated filter state
                 this.trigger('clearFilter');                
            },
            clearAll: function(event) {
                this.trigger('clearAll');
            },

            show: function() {
                this.$el.removeClass('is-collapsed');
            },

            hide: function() {
                this.$ul.empty();
                this.$el.addClass('is-collapsed');
            }

        });
    });
}(define || RequireJS.define));
