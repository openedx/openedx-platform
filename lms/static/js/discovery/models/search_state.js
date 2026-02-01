(function(define) {
    define([
        'underscore',
        'backbone',
        'js/discovery/models/course_discovery',
        'js/discovery/collections/filters'
    ], function(_, Backbone, CourseDiscovery, Filters) {
        'use strict';
    // ✅ Add the conversion function inside the define scope
        function convertAggsToFacets(aggs) {
            const facets = {};

            for (const facetName in aggs) {
                if (!aggs.hasOwnProperty(facetName)) continue;

                const facetAgg = aggs[facetName];
                const terms = facetAgg.terms || {};
                const termsList = [];

                for (const term in terms) {
                    if (!terms.hasOwnProperty(term)) continue;

                    if (['total', 'other'].includes(term)) continue; // skip metadata
                    termsList.push({
                        name: term,
                        count: terms[term],
                    });
                }

                facets[facetName] = {
                    displayName: facetName,
                    name: facetName,
                    terms: termsList,
                };
            }

            return facets;
        }

        return Backbone.Model.extend({

            page: 0,
            pageSize: 20,
            searchTerm: '',
            terms: {},
            jqhxr: null,

            initialize: function() {
                this.discovery = new CourseDiscovery();
                this.listenTo(this.discovery, 'sync', this.onSync, this);
                this.listenTo(this.discovery, 'error', this.onError, this);
            },

            performSearch: function(searchTerm, otherTerms) {
                this.reset();
                this.searchTerm = searchTerm;
                if (otherTerms) {
                    this.terms = otherTerms;
                }
                this.sendQuery(this.buildQuery(0));
            },

            refineSearch: function(terms) {
                this.reset();

                if (terms) {
                        this.terms = terms;
                   
                } else {
                    this.terms = {};
                }

                const data = this.buildQuery(0);
                console.log('sending data:', data);
                this.sendQuery(data);
            },
            loadNextPage: function() {
                if (this.hasNextPage()) {
                    this.sendQuery(this.buildQuery(this.page + 1));
                }
            },

            hasNextPage: function() {
                var total = this.discovery.get('totalCount');
                return total - ((this.page + 1) * this.pageSize) > 0;
            },

            sendQuery: function(data) {
                if (this.jqhxr) {
                    this.jqhxr.abort();
                }
                console.log('Sending data to backend:', data);

                this.jqhxr = this.discovery.fetch({
                    type: 'POST',
                    data: JSON.stringify(data),
                    contentType: 'application/json',
                    dataType: 'json'                     
                });

                return this.jqhxr;
            },
            buildQuery: function(pageIndex) {
                var data = {
                    search_string: this.searchTerm,
                    page_size: this.pageSize,
                    page_index: pageIndex
                };
                _.extend(data, this.terms);
                return data;
            },
            // this groupTerms added to group the search terms and send to refineSearch
            groupTerms: function(termsList) {
                const grouped = {};
                _.each(termsList, function(termObj) {
                    if (!grouped[termObj.type]) {
                        grouped[termObj.type] = [];
                    }
                    grouped[termObj.type].push(termObj.query);
                });
                return grouped;
            },
            reset: function() {
                this.discovery.reset();
                this.page = 0;
                this.errorMessage = '';
            },

            onError: function(collection, response, options) {
                if (response.statusText !== 'abort') {
                    this.errorMessage = response.responseJSON.error;
                    this.trigger('error');
                }
            },
            onSync: function(collection, response, options) {
                // ✅ Convert aggs to facets
                if (!response.facets && response.aggs) {
                    response.facets = convertAggsToFacets(response.aggs);
                    console.log('✅ Converted facets:', response.facets);
                }

                // ✅ Safely parse options.data
                let pageIndex = 0;
                try {
                    const parsedData = typeof options.data === 'string' ? JSON.parse(options.data) : options.data;
                    pageIndex = parsedData.page_index || 0;
                } catch (e) {
                    console.warn('Failed to parse options.data:', e);
                }
                var total = this.discovery.get('totalCount');
                var originalSearchTerm = this.searchTerm;
                if (pageIndex === 0) {
                    if (total === 0) {
                    // list all courses
                        this.cachedDiscovery().done(function(cached) {
                            this.discovery.courseCards.reset(cached.courseCards.toJSON());
                            this.discovery.facetOptions.reset(cached.facetOptions.toJSON());
                            this.discovery.set('latestCount', cached.get('latestCount'));
                            this.trigger('search', originalSearchTerm, total);
                        });
                        this.searchTerm = '';
                        this.terms = {};
                    } else {
                        _.each(this.terms, function(term, facet) {
                            if (facet !== 'search_query') {
                                var option = this.discovery.facetOptions.findWhere({
                                    facet: facet,
                                    term: term
                                });
                                if (option) {
                                    option.set('selected', true);
                                }
                            }
                        }, this);
                        this.trigger('search', this.searchTerm, total);
                    }
                } else {
                    this.page = pageIndex;
                    this.trigger('next');
                }
            },

            // lazy load
            cachedDiscovery: function() {
                var deferred = $.Deferred();
                var self = this;

                if (this.cached) {
                    deferred.resolveWith(this, [this.cached]);
                } else {
                    this.cached = new CourseDiscovery();
                    this.cached.fetch({
                        type: 'POST',
                        data: {
                            search_string: '',
                            page_size: this.pageSize,
                            page_index: 0
                        },
                        success: function(model, response, options) {
                            deferred.resolveWith(self, [model]);
                        }
                    });
                }
                return deferred.promise();
            }

        });
    });
}(define || RequireJS.define));
