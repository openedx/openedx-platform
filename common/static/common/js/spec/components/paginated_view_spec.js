define([
    'jquery',
    'backbone',
    'underscore',
    'sinon',
    'edx-ui-toolkit/js/pagination/paging-collection',
    'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
    'common/js/components/views/paginated_view'
], function($, Backbone, _, sinon, PagingCollection, AjaxHelpers, PaginatedView) {
    'use strict';

    describe('PaginatedView', function() {
        var TestItemView = Backbone.View.extend({
                className: 'test-item',
                tagName: 'div',
                initialize: function() {
                    this.render();
                },
                render: function() {
                    this.$el.text(this.model.get('text'));
                    return this;
                }
            }),
            TestPaginatedView = PaginatedView.extend({type: 'test', itemViewClass: TestItemView}),
            testCollection,
            testView,
            requests,
            xhrFactory,
            initialItems,
            nextPageButtonCss = '.next-page-link',
            previousPageButtonCss = '.previous-page-link',
            generateItems = function(numItems) {
                return _.map(_.range(numItems), function(i) {
                    return {
                        text: 'item ' + i
                    };
                });
            };

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            setFixtures('<div class="test-container"></div>');
            initialItems = generateItems(5);
            var TestPagingCollection = PagingCollection.extend({
                state: {
                    pageSize: 5
                }
            });

            testCollection = new TestPagingCollection();
            testCollection.url = '/dummy/url';
            testCollection.set({
                count: 6,
                num_pages: 2,
                page: 1,
                results: initialItems
            }, {parse: true});
            testView = new TestPaginatedView({el: '.test-container', collection: testCollection}).render();
        });

        afterEach(function() {
            requests.restore();
        });

        /**
         * Verify that the view's header reflects the page we're currently viewing.
         * @param matchString the header we expect to see
         */
        function expectHeader(matchString) {
            expect(testView.$('.test-paging-header').text()).toMatch(matchString);
        }

        /**
         * Verify that the list view renders the expected items
         * @param expectedItems an array of topic objects we expect to see
         */
        function expectItems(expectedItems) {
            var $items = testView.$('.test-item');
            _.each(expectedItems, function(item, index) {
                var currentItem = $items.eq(index);
                expect(currentItem.text()).toMatch(item.text);
            });
        }

        /**
         * Verify that the footer reflects the current pagination
         * @param options a parameters hash containing:
         *  - currentPage: the one-indexed page we expect to be viewing
         *  - totalPages: the total number of pages to page through
         *  - isHidden: whether the footer is expected to be visible
         */
        function expectFooter(options) {
            var footerEl = testView.$('.test-paging-footer');
            expect(footerEl.text())
                .toMatch(new RegExp(options.currentPage + '\\s+out of\\s+\/\\s+' + options.totalPages));
            expect(footerEl.hasClass('hidden')).toBe(options.isHidden);
        }

        it('can render the first of many pages', function() {
            expectHeader('Showing 1-5 out of 6 total');
            expectItems(initialItems);
            expectFooter({currentPage: 1, totalPages: 2, isHidden: false});
        });

        it('can render the only page', function() {
            initialItems = generateItems(1);
            testCollection.set(
                {
                    count: 1,
                    num_pages: 1,
                    page: 1,
                    start: 0,
                    results: initialItems
                },
                {parse: true}
            );
            expectHeader('Showing 1 out of 1 total');
            expectItems(initialItems);
            expectFooter({currentPage: 1, totalPages: 1, isHidden: true});
        });

        it('can change to the next page', function() {
            var newItems = generateItems(1);
            expectHeader('Showing 1-5 out of 6 total');
            expectItems(initialItems);
            expectFooter({currentPage: 1, totalPages: 2, isHidden: false});
            AjaxHelpers.expectNoRequests(requests);
            testView.$(nextPageButtonCss).click();
            AjaxHelpers.respondWithJson(requests, {
                count: 6,
                num_pages: 2,
                page: 2,
                results: newItems
            });
            expectHeader('Showing 6-6 out of 6 total');
            expectItems(newItems);
            expectFooter({currentPage: 2, totalPages: 2, isHidden: false});
        });

        it('can change to the previous page', function() {
            var previousPageItems;
            initialItems = generateItems(1);
            testCollection.set(
                {
                    count: 6,
                    num_pages: 2,
                    page: 2,
                    results: initialItems
                },
                {parse: true}
            );
            expectHeader('Showing 6-6 out of 6 total');
            expectItems(initialItems);
            expectFooter({currentPage: 2, totalPages: 2, isHidden: false});
            testView.$(previousPageButtonCss).click();
            previousPageItems = generateItems(5);
            AjaxHelpers.respondWithJson(requests, {
                count: 6,
                num_pages: 2,
                page: 1,
                results: previousPageItems
            });
            expectHeader('Showing 1-5 out of 6 total');
            expectItems(previousPageItems);
            expectFooter({currentPage: 1, totalPages: 2, isHidden: false});
        });

        it('sets focus for screen readers', function() {
            spyOn($.fn, 'focus');
            testView.$(nextPageButtonCss).click();
            AjaxHelpers.respondWithJson(requests, {
                count: 6,
                num_pages: 2,
                page: 2,
                results: generateItems(1)
            });
            expect(testView.$('.sr-is-focusable').focus).toHaveBeenCalled();
        });

        it('does not change on server error', function() {
            var expectInitialState = function() {
                    expectHeader('Showing 1-5 out of 6 total');
                    expectItems(initialItems);
                    expectFooter({currentPage: 1, totalPages: 2, isHidden: false});
                };
            expectInitialState();
            testView.$(nextPageButtonCss).click();
            requests[0].respond(500);
            expectInitialState();
        });
    });
});
