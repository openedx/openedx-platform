define([
    'underscore',
    'URI',
    'sinon',
    'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
    'edx-ui-toolkit/js/pagination/paging-collection',
    'common/js/components/views/search_field'
], function(_, URI, sinon, AjaxHelpers, PagingCollection, SearchFieldView) {
    'use strict';

    describe('SearchFieldView', function() {
        var searchFieldView,
            requests,
            xhrFactory,
            mockUrl = '/api/mock_collection';

        var newCollection = function(size, perPage) {
            var results = _.map(_.range(size), function(i) { return {foo: i}; });
            var TestPagingCollection = PagingCollection.extend({
                state: {
                    pageSize: 5
                }
            });

            var collection = new TestPagingCollection({
                count: results.length,
                num_pages: Math.ceil(results.length / perPage),
                page: 1,
                results: _.first(results, perPage)
            }, {parse: true});

            collection.url = mockUrl;
            return collection;
        };

        var createSearchFieldView = function(options) {
            options = _.extend(
                {
                    type: 'test',
                    collection: newCollection(5, 4),
                    el: $('.test-search')
                },
                options || {}
            );
            return new SearchFieldView(options);
        };

        var assertQueryParams = function(request, expectedParameters) {
            var urlParams = new URI(request.url).query(true);
            _.each(expectedParameters, function(value, key) {
                expect(urlParams[key]).toBe(value);
            });
        };

        var assertNotInQueryParams = function(request, param) {
            var urlParams = new URI(request.url).query(true);
            return !urlParams.hasOwnProperty(param);
        };

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            setFixtures('<section class="test-search"></section>');
        });

        afterEach(function() {
            requests.restore();
        });

        it('correctly displays itself', function() {
            searchFieldView = createSearchFieldView().render();
            expect(searchFieldView.$('.search-field').val(), '');
            expect(searchFieldView.$('.action-clear')).toHaveClass('is-hidden');
        });

        it('can display with an initial search string', function() {
            searchFieldView = createSearchFieldView({
                searchString: 'foo'
            }).render();
            expect(searchFieldView.$('.search-field').val(), 'foo');
        });

        it('refreshes the collection when performing a search', function() {
            searchFieldView = createSearchFieldView().render();
            searchFieldView.$('.search-field').val('foo');
            searchFieldView.$('.action-search').click();
            assertQueryParams(requests[0], {
                page: '1',
                page_size: '5',
                text_search: 'foo'
            });

            AjaxHelpers.respondWithJson(requests, {
                count: 10,
                page: 1,
                num_pages: 1,
                results: []
            });
            expect(searchFieldView.$('.search-field').val(), 'foo');
        });

        it('can clear the search', function() {
            searchFieldView = createSearchFieldView({
                searchString: 'foo'
            }).render();
            searchFieldView.$('.action-clear').click();
            assertNotInQueryParams('text_search');

            AjaxHelpers.respondWithJson(requests, {
                count: 10,
                page: 1,
                num_pages: 1,
                results: []
            });
            expect(searchFieldView.$('.search-field').val(), '');
            expect(searchFieldView.$('.action-clear')).toHaveClass('is-hidden');
        });
    });
});
