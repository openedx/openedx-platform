define([
    'jquery',
    'URI',
    'sinon',
    'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
    'edx-ui-toolkit/js/pagination/paging-collection',
    'js/views/paging',
    'js/views/paging_header'
],
function($, URI, sinon, AjaxHelpers, PagingCollection, PagingView, PagingHeader) {
    'use strict';

    var requests, xhrFactory;

    var createPageableItem = function(index) {
        var id = 'item_' + index;
        return {
            id: id,
            display_name: id,
            url: id
        };
    };

    var mockFirstPage = {
        results: [
            createPageableItem(1),
            createPageableItem(2),
            createPageableItem(3)
        ],
        num_pages: 2,
        page_size: 3,
        count: 4,
        page: 0,
        start: 0
    };
    var mockSecondPage = {
        results: [
            createPageableItem(4)
        ],
        num_pages: 2,
        page_size: 3,
        page: 1,
        count: 4,
        start: 3
    };
    var mockEmptyPage = {
        results: [],
        num_pages: 1,
        page_size: 3,
        count: 0,
        page: 0,
        start: 0
    };

    var respondWithMockItems = function(requests) {
        var request = AjaxHelpers.currentRequest(requests);
        var url = new URI(request.url);
        var queryParameters = url.query(true); // Returns an object with each query parameter stored as a value
        var page = queryParameters.page;
        var response = page === '0' ? mockFirstPage : mockSecondPage;
        AjaxHelpers.respondWithJson(requests, response);
    };

    var MockPagingView = PagingView.extend({
        renderPageItems: function() {},
        initialize: function() {
            this.registerSortableColumn('name-col', 'Name', 'name', 'asc');
            this.registerSortableColumn('date-col', 'Date', 'date', 'desc');
            this.setInitialSortColumn('date-col');
        }
    });

    describe('Paging', function() {
        var pagingView,
            TestPagingCollection = PagingCollection.extend({
                state: {
                    firstPage: 0,
                    currentPage: null,
                    pageSize: 3
                }
            });

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            var collection = new TestPagingCollection();
            collection.url = '/dummy/';
            pagingView = new MockPagingView({collection: collection});
        });

        afterEach(function() {
            requests.restore();
        });

        describe('PagingView', function() {
            describe('setPage', function() {
                it('can set the current page', function() {
                    pagingView.setPage(1);
                    respondWithMockItems(requests);
                    expect(pagingView.collection.getPageNumber()).toBe(1);
                    pagingView.setPage(2);
                    respondWithMockItems(requests);
                    expect(pagingView.collection.getPageNumber()).toBe(2);
                });

                it('should not change page after a server error', function() {
                    pagingView.setPage(1);
                    respondWithMockItems(requests);
                    pagingView.setPage(2);
                    requests[1].respond(500);

                    /* PagingCollection sets the currentPage to the old page in case of failure */
                    expect(pagingView.collection.getPageNumber()).toBe(1);
                });
            });

            describe('nextPage', function() {
                it('does not move forward after a server error', function() {
                    pagingView.setPage(1);
                    respondWithMockItems(requests);
                    pagingView.nextPage();
                    requests[1].respond(500);
                    expect(pagingView.collection.getPageNumber()).toBe(1);
                });

                it('can move to the next page', function() {
                    pagingView.setPage(1);
                    respondWithMockItems(requests);
                    pagingView.nextPage();
                    respondWithMockItems(requests);

                    /* PagingCollection now returns the normalized page number; adds one if zero indexed */
                    expect(pagingView.collection.getPageNumber()).toBe(2);
                });

                it('can not move forward from the final page', function() {
                    pagingView.setPage(2);
                    respondWithMockItems(requests);
                    pagingView.nextPage();
                    AjaxHelpers.expectNoRequests(requests);
                });
            });

            describe('previousPage', function() {
                it('can move back a page', function() {
                    pagingView.setPage(2);
                    respondWithMockItems(requests);
                    pagingView.previousPage();
                    respondWithMockItems(requests);
                    expect(pagingView.collection.getPageNumber()).toBe(1);
                });

                it('can not move back from the first page', function() {
                    pagingView.setPage(1);
                    respondWithMockItems(requests);
                    pagingView.previousPage();
                    AjaxHelpers.expectNoRequests(requests);
                });

                it('does not move back after a server error', function() {
                    pagingView.setPage(2);
                    respondWithMockItems(requests);
                    pagingView.previousPage();
                    requests[1].respond(500);
                    expect(pagingView.collection.getPageNumber()).toBe(2);
                });
            });

            describe('toggleSortOrder', function() {
                it('can toggle direction of the current sort', function() {
                    expect(pagingView.collection.sortDirection).toBe('desc');
                    pagingView.toggleSortOrder('date-col');
                    respondWithMockItems(requests);
                    expect(pagingView.collection.sortDirection).toBe('asc');
                    pagingView.toggleSortOrder('date-col');
                    respondWithMockItems(requests);
                    expect(pagingView.collection.sortDirection).toBe('desc');
                });

                it('sets the correct default sort direction for a column', function() {
                    pagingView.toggleSortOrder('name-col');
                    respondWithMockItems(requests);
                    expect(pagingView.sortDisplayName()).toBe('Name');
                    expect(pagingView.collection.sortDirection).toBe('asc');
                    pagingView.toggleSortOrder('date-col');
                    respondWithMockItems(requests);
                    expect(pagingView.sortDisplayName()).toBe('Date');
                    expect(pagingView.collection.sortDirection).toBe('desc');
                });
            });

            describe('sortableColumnInfo', function() {
                it('returns the registered info for a column', function() {
                    pagingView.registerSortableColumn('test-col', 'Test Column', 'testField', 'asc');
                    var sortInfo = pagingView.sortableColumnInfo('test-col');
                    expect(sortInfo.displayName).toBe('Test Column');
                    expect(sortInfo.fieldName).toBe('testField');
                    expect(sortInfo.defaultSortDirection).toBe('asc');
                });

                it('throws an exception for an unregistered column', function() {
                    expect(function() {
                        pagingView.sortableColumnInfo('no-such-column');
                    }).toThrow();
                });
            });
        });

        describe('PagingHeader', function() {
            var pagingHeader;

            beforeEach(function() {
                pagingHeader = new PagingHeader({view: pagingView});
            });

            describe('Next page button', function() {
                beforeEach(function() {
                    // Render the page and header so that they can react to events
                    pagingView.render();
                    pagingHeader.render();
                });

                it('does not move forward if a server error occurs', function() {
                    pagingView.setPage(1);
                    respondWithMockItems(requests);
                    pagingHeader.$('.next-page-link').click();
                    requests[1].respond(500);
                    expect(pagingView.collection.state.currentPage).toBe(0);
                    expect(pagingView.collection.getPageNumber()).toBe(1);
                });

                it('can move to the next page', function() {
                    pagingView.setPage(1);
                    respondWithMockItems(requests);
                    pagingHeader.$('.next-page-link').click();
                    respondWithMockItems(requests);
                    expect(pagingView.collection.getPageNumber()).toBe(2);
                });

                it('should be enabled when there is at least one more page', function() {
                    pagingView.setPage(1);
                    respondWithMockItems(requests);
                    expect(pagingHeader.$('.next-page-link')).not.toHaveClass('is-disabled');
                });

                it('should be disabled on the final page', function() {
                    pagingView.setPage(2);
                    respondWithMockItems(requests);
                    expect(pagingHeader.$('.next-page-link')).toHaveClass('is-disabled');
                });

                it('should be disabled on an empty page', function() {
                    pagingView.setPage(1);
                    AjaxHelpers.respondWithJson(requests, mockEmptyPage);
                    expect(pagingHeader.$('.next-page-link')).toHaveClass('is-disabled');
                });
            });

            describe('Previous page button', function() {
                beforeEach(function() {
                    // Render the page and header so that they can react to events
                    pagingView.render();
                    pagingHeader.render();
                });

                it('does not move back if a server error occurs', function() {
                    pagingView.setPage(2);
                    respondWithMockItems(requests);
                    pagingHeader.$('.previous-page-link').click();
                    requests[1].respond(500);
                    expect(pagingView.collection.getPageNumber()).toBe(2);
                });

                it('can go back a page', function() {
                    pagingView.setPage(2);
                    respondWithMockItems(requests);
                    pagingHeader.$('.previous-page-link').click();
                    respondWithMockItems(requests);
                    expect(pagingView.collection.getPageNumber()).toBe(1);
                });

                it('should be disabled on the first page', function() {
                    pagingView.setPage(1);
                    respondWithMockItems(requests);
                    expect(pagingHeader.$('.previous-page-link')).toHaveClass('is-disabled');
                });

                it('should be enabled on the second page', function() {
                    pagingView.setPage(2);
                    respondWithMockItems(requests);
                    expect(pagingHeader.$('.previous-page-link')).not.toHaveClass('is-disabled');
                });

                it('should be disabled for an empty page', function() {
                    pagingView.setPage(1);
                    AjaxHelpers.respondWithJson(requests, mockEmptyPage);
                    expect(pagingHeader.$('.previous-page-link')).toHaveClass('is-disabled');
                });
            });

            describe('Page metadata section', function() {
                it('shows the correct metadata for the current page', function() {
                    var message;
                    pagingView.setPage(1);
                    respondWithMockItems(requests);
                    message = pagingHeader.$('.meta').html().trim();
                    expect(message).toBe('<p>Showing <span class="count-current-shown">1-3</span>'
                            + ' out of <span class="count-total">4 total</span>, '
                            + 'sorted by <span class="sort-order">Date</span> descending</p>');
                });

                it('shows the correct metadata when sorted ascending', function() {
                    var message;
                    pagingView.setPage(1);
                    pagingView.toggleSortOrder('name-col');
                    respondWithMockItems(requests);
                    message = pagingHeader.$('.meta').html().trim();
                    expect(message).toBe('<p>Showing <span class="count-current-shown">1-3</span>'
                            + ' out of <span class="count-total">4 total</span>, '
                            + 'sorted by <span class="sort-order">Name</span> ascending</p>');
                });
            });

            describe('Item count label', function() {
                it('should show correct count on first page', function() {
                    pagingView.setPage(1);
                    respondWithMockItems(requests);
                    expect(pagingHeader.$('.count-current-shown')).toHaveHtml('1-3');
                });

                it('should show correct count on second page', function() {
                    pagingView.setPage(2);
                    respondWithMockItems(requests);
                    expect(pagingHeader.$('.count-current-shown')).toHaveHtml('4-4');
                });

                it('should show correct count for an empty collection', function() {
                    pagingView.setPage(1);
                    AjaxHelpers.respondWithJson(requests, mockEmptyPage);
                    expect(pagingHeader.$('.count-current-shown')).toHaveHtml('0-0');
                });
            });

            describe('Item total label', function() {
                it('should show correct total on the first page', function() {
                    pagingView.setPage(1);
                    respondWithMockItems(requests);
                    expect(pagingHeader.$('.count-total')).toHaveText('4 total');
                });

                it('should show correct total on the second page', function() {
                    pagingView.setPage(2);
                    respondWithMockItems(requests);
                    expect(pagingHeader.$('.count-total')).toHaveText('4 total');
                });

                it('should show zero total for an empty collection', function() {
                    pagingView.setPage(1);
                    AjaxHelpers.respondWithJson(requests, mockEmptyPage);
                    expect(pagingHeader.$('.count-total')).toHaveText('0 total');
                });
            });

            describe('Sort order label', function() {
                it('should show correct initial sort order', function() {
                    pagingView.setPage(1);
                    respondWithMockItems(requests);
                    expect(pagingHeader.$('.sort-order')).toHaveText('Date');
                });

                it('should show updated sort order', function() {
                    pagingView.toggleSortOrder('name-col');
                    respondWithMockItems(requests);
                    expect(pagingHeader.$('.sort-order')).toHaveText('Name');
                });
            });
        });
    });
});
