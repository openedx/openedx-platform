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
                meanings: meanings
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
                if (filters.get(type)) {
                    removeFilter(type);
                } else {
                    filters.add({type: type, query: query, name: name});
                    search.refineSearch(filters.getTerms());
                }
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

            function quote(string) {
                return '"' + string + '"';
            }
        };
    });
}(define || RequireJS.define));
