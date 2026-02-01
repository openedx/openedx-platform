(function(define) {
    'use strict';

    define(['backbone', 'js/discovery/models/search_state', 'js/discovery/collections/filters',
        'js/discovery/views/search_form', 'js/discovery/views/courses_listing',
        'js/discovery/views/filter_bar', 'js/discovery/views/refine_sidebar'],
    function(Backbone, SearchState, Filters, SearchForm, CoursesListing, FilterBar, RefineSidebar) {
        return function(meanings, searchQuery, userLanguage, userTimezone, setDefaultFilter) {
            var dispatcher = _.extend({}, Backbone.Events);
            var search = new SearchState();
            var filters = new Filters();
            var form = new SearchForm();
            var filterBar = new FilterBar({collection: filters});
            var refineSidebar = new RefineSidebar({
                collection: search.discovery.facetOptions,
                meanings: meanings || {},
                filtersCollection: filters
            });
            var listing;
            var courseListingModel = search.discovery;
            courseListingModel.userPreferences = {
                userLanguage: userLanguage,
                userTimezone: userTimezone
            };
            if (setDefaultFilter && userLanguage) {
                filters.add({
                    type: 'language',
                    query: userLanguage,
                    name: refineSidebar.termName('language', userLanguage)
                });
            }
            listing = new CoursesListing({model: courseListingModel});

            dispatcher.listenTo(form, "search", function (query) {
                form.showLoadingIndicator();
                if (!query || query.trim() === "") {
                    filters.remove("search_query");
                }
                search.performSearch(query, filters.getTerms());
            });

            dispatcher.listenTo(refineSidebar, 'selectOption', function(type, query, name) {
                form.showLoadingIndicator();
                const exist = filters.findWhere({ type: type, query: query });
                if (exist) {
                    filters.remove(exist);
                } else {
                    filters.add({ type: type, query: query, name: name });
                    refineSidebar.render();
                }

                const terms = groupTerms(filters.toJSON()); 
                const queryString = flattenTermsToQuery(terms);
                Backbone.history.navigate('search?' + queryString, { trigger: true });

                search.refineSearch(terms);
            });

            dispatcher.listenTo(filterBar, 'clearFilter', removeFilter);

            dispatcher.listenTo(filterBar, 'clearAll', function() {
                filters.reset();
                form.doSearch('');
            });

            dispatcher.listenTo(listing, 'next', function() {
                search.loadNextPage();
            });

            dispatcher.listenTo(search, 'next', function() {
                listing.renderNext();
            });

            dispatcher.listenTo(search, 'search', function(query, total) {
                if (total > 0) {
                    form.showFoundMessage(total);
                    if (query) {
                        filters.add(
                            {type: 'search_query', query: query, name: quote(query)},
                            {merge: true}
                        );
                    }
                } else {
                    form.showNotFoundMessage(query);
                    filters.reset();
                }
                form.hideLoadingIndicator();
                listing.render();
                refineSidebar.render();
            });

            dispatcher.listenTo(search, 'error', function() {
                form.showErrorMessage(search.errorMessage);
                form.hideLoadingIndicator();
            });

            // kick off search on page refresh
            form.doSearch(searchQuery);

            function removeFilter(type) {
                form.showLoadingIndicator();
                filters.remove(type);
                if (type === 'search_query') {
                    form.doSearch('');
                } else {
                    search.refineSearch(filters.getTerms());
                }
            }
 // Group flat list of terms into { type: [queries...] }
            function groupTerms(termsList) {
                const grouped = {};
                _.each(termsList, function(termObj) {
                    if (!grouped[termObj.type]) {
                        grouped[termObj.type] = [];
                    }
                    grouped[termObj.type].push(termObj.query);
                });
                return grouped;
            }

            // Flatten grouped terms into query string 
            function flattenTermsToQuery(terms) {
                const pairs = [];
                _.each(terms, function(values, key) {
                    _.each(values, function(val) {
                        pairs.push(encodeURIComponent(key) + '=' + encodeURIComponent(val));
                    });
                });
                return pairs.join('&');
            }

            function quote(str) {
                return '"' + str + '"';
            }
        };
    });
}(define || RequireJS.define));
