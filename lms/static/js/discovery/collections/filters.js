(function(define) {
    define(['backbone', 'js/discovery/models/filter'], function(Backbone, Filter) {
        'use strict';

        return Backbone.Collection.extend({
            model: Filter,
            getTerms: function() {
                return this.reduce(function(memo, filter) {
                const type = filter.get('type');
                if (!memo[type]) memo[type] = [];
                    memo[type].push(filter.get('query'));
                    return memo;
            }, {});
            }
        });
    });
}(define || RequireJS.define));
