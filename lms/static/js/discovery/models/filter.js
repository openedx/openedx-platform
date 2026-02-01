(function(define) {
    define(['backbone'], function(Backbone) {
        'use strict';
        return Backbone.Model.extend({

            defaults: {
                type: 'search_query',
                query: '',
                name: ''
            },
            initialize: function() {
                // Manually set model ID used by collection.get()
                this.set('id', this.get('type') + '|' + this.get('query'));
            }
        });
    });
}(define || RequireJS.define));
