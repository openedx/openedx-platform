define(['jquery', 'underscore', 'sinon', 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers', 'URI', 'js/models/xblock_info',
    'js/views/paged_container', 'js/views/paging_header',
    'common/js/components/views/paging_footer', 'js/views/xblock'],
function($, _, sinon, AjaxHelpers, URI, XBlockInfo, PagedContainer, PagingHeader, PagingFooter, XBlockView) {
    var requests, xhrFactory;
    var htmlResponseTpl = _.template(''
            + '<div class="xblock-container-paging-parameters" '
                + 'data-start="<%= start %>" '
                + 'data-displayed="<%= displayed %>" '
                + 'data-total="<%= total %>" '
                + 'data-previews="<%= previews %>"></div>'
    );

    function getResponseHtml(override_options) {
        var default_options = {
            start: 0,
            displayed: PAGE_SIZE,
            total: PAGE_SIZE + 1,
            previews: true
        };
        var options = _.extend(default_options, override_options);
        return '<div class="xblock" data-request-token="request_token">'
                + '<div class="container-paging-header"></div>'
                + htmlResponseTpl(options)
                + '<div class="container-paging-footer"></div>'
                + '</div>';
    }

    var makePage = function(html_parameters) {
        return {
            resources: [],
            html: getResponseHtml(html_parameters)
        };
    };

    var PAGE_SIZE = 3;

    var mockFirstPage = makePage({
        start: 0,
        displayed: PAGE_SIZE,
        total: PAGE_SIZE + 1
    });

    var mockSecondPage = makePage({
        start: PAGE_SIZE,
        displayed: 1,
        total: PAGE_SIZE + 1
    });

    var mockEmptyPage = makePage({
        start: 0,
        displayed: 0,
        total: 0
    });

    var respondWithMockPage = function(requests, mockPage) {
        var request = AjaxHelpers.currentRequest(requests);
        if (typeof mockPage === 'undefined') {
            var url = new URI(request.url);
            var queryParameters = url.query(true); // Returns an object with each query parameter stored as a value
            var page = queryParameters.page_number;
            mockPage = page === '0' ? mockFirstPage : mockSecondPage;
        }
        AjaxHelpers.respondWithJson(requests, mockPage);
    };

    var MockPagingView = PagedContainer.extend({
        view: 'container_preview',
        el: $("<div><div class='xblock' data-request-token='test_request_token'/></div>"),
        model: new XBlockInfo({}, {parse: true})
    });

    describe('Paging Container', function() {
        var pagingContainer;

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            pagingContainer = new MockPagingView({
                page_size: PAGE_SIZE,
                page: jasmine.createSpyObj('page', ['updatePreviewButton', 'renderAddXBlockComponents'])
            });
        });

        afterEach(function() {
            requests.restore();
        });

        describe('Container', function() {
            describe('rendering', function() {
                it('should set show_previews', function() {
                    expect(pagingContainer.collection.showChildrenPreviews).toBe(true); // precondition check

                    pagingContainer.setPage(0);
                    respondWithMockPage(requests, makePage({previews: false}));
                    expect(pagingContainer.collection.showChildrenPreviews).toBe(false);

                    pagingContainer.setPage(0);
                    respondWithMockPage(requests, makePage({previews: true}));
                    expect(pagingContainer.collection.showChildrenPreviews).toBe(true);
                });
            });

            describe('setPage', function() {
                it('can set the current page', function() {
                    pagingContainer.setPage(0);
                    respondWithMockPage(requests);
                    expect(pagingContainer.collection.currentPage).toBe(0);
                    pagingContainer.setPage(1);
                    respondWithMockPage(requests);
                    expect(pagingContainer.collection.currentPage).toBe(1);
                });

                it('should not change page after a server error', function() {
                    pagingContainer.setPage(0);
                    respondWithMockPage(requests);
                    pagingContainer.setPage(1);
                    requests[1].respond(500);
                    expect(pagingContainer.collection.currentPage).toBe(0);
                });
            });

            describe('nextPage', function() {
                it('does not move forward after a server error', function() {
                    pagingContainer.setPage(0);
                    respondWithMockPage(requests);
                    pagingContainer.nextPage();
                    requests[1].respond(500);
                    expect(pagingContainer.collection.currentPage).toBe(0);
                });

                it('can move to the next page', function() {
                    pagingContainer.setPage(0);
                    respondWithMockPage(requests);
                    pagingContainer.nextPage();
                    respondWithMockPage(requests);
                    expect(pagingContainer.collection.currentPage).toBe(1);
                });

                it('can not move forward from the final page', function() {
                    pagingContainer.setPage(1);
                    respondWithMockPage(requests);
                    pagingContainer.nextPage();
                    AjaxHelpers.expectNoRequests(requests);
                });
            });

            describe('previousPage', function() {
                it('can move back a page', function() {
                    pagingContainer.setPage(1);
                    respondWithMockPage(requests);
                    pagingContainer.previousPage();
                    respondWithMockPage(requests);
                    expect(pagingContainer.collection.currentPage).toBe(0);
                });

                it('can not move back from the first page', function() {
                    pagingContainer.setPage(0);
                    respondWithMockPage(requests);
                    pagingContainer.previousPage();
                    AjaxHelpers.expectNoRequests(requests);
                });

                it('does not move back after a server error', function() {
                    pagingContainer.setPage(1);
                    respondWithMockPage(requests);
                    pagingContainer.previousPage();
                    requests[1].respond(500);
                    expect(pagingContainer.collection.currentPage).toBe(1);
                });
            });
        });

        describe('PagingHeader', function() {
            describe('Next page button', function() {
                beforeEach(function() {
                    pagingContainer.render();
                    respondWithMockPage(requests);
                });

                it('does not move forward if a server error occurs', function() {
                    pagingContainer.setPage(0);
                    respondWithMockPage(requests);
                    pagingContainer.pagingHeader.$('.next-page-link').click();
                    requests[2].respond(500);
                    expect(pagingContainer.collection.currentPage).toBe(0);
                });

                it('can move to the next page', function() {
                    pagingContainer.setPage(0);
                    respondWithMockPage(requests);
                    pagingContainer.pagingHeader.$('.next-page-link').click();
                    respondWithMockPage(requests);
                    expect(pagingContainer.collection.currentPage).toBe(1);
                });

                it('should be enabled when there is at least one more page', function() {
                    pagingContainer.setPage(0);
                    respondWithMockPage(requests);
                    expect(pagingContainer.pagingHeader.$('.next-page-link')).not.toHaveClass('is-disabled');
                });

                it('should be disabled on the final page', function() {
                    pagingContainer.setPage(1);
                    respondWithMockPage(requests);
                    expect(pagingContainer.pagingHeader.$('.next-page-link')).toHaveClass('is-disabled');
                });

                it('should be disabled on an empty page', function() {
                    pagingContainer.setPage(0);
                    AjaxHelpers.respondWithJson(requests, mockEmptyPage);
                    expect(pagingContainer.pagingHeader.$('.next-page-link')).toHaveClass('is-disabled');
                });
            });

            describe('Previous page button', function() {
                beforeEach(function() {
                    pagingContainer.render();
                    respondWithMockPage(requests);
                });

                it('does not move back if a server error occurs', function() {
                    pagingContainer.setPage(1);
                    respondWithMockPage(requests);
                    pagingContainer.pagingHeader.$('.previous-page-link').click();
                    requests[2].respond(500);
                    expect(pagingContainer.collection.currentPage).toBe(1);
                });

                it('can go back a page', function() {
                    pagingContainer.setPage(1);
                    respondWithMockPage(requests);
                    pagingContainer.pagingHeader.$('.previous-page-link').click();
                    respondWithMockPage(requests);
                    expect(pagingContainer.collection.currentPage).toBe(0);
                });

                it('should be disabled on the first page', function() {
                    pagingContainer.setPage(0);
                    respondWithMockPage(requests);
                    expect(pagingContainer.pagingHeader.$('.previous-page-link')).toHaveClass('is-disabled');
                });

                it('should be enabled on the second page', function() {
                    pagingContainer.setPage(1);
                    respondWithMockPage(requests);
                    expect(pagingContainer.pagingHeader.$('.previous-page-link')).not.toHaveClass('is-disabled');
                });

                it('should be disabled for an empty page', function() {
                    pagingContainer.setPage(0);
                    AjaxHelpers.respondWithJson(requests, mockEmptyPage);
                    expect(pagingContainer.pagingHeader.$('.previous-page-link')).toHaveClass('is-disabled');
                });
            });

            describe('Page metadata section', function() {
                it('shows the correct metadata for the current page', function() {
                    var message;
                    pagingContainer.setPage(0);
                    respondWithMockPage(requests);
                    message = pagingContainer.pagingHeader.$('.meta').html().trim();
                    expect(message).toBe('<p>Showing <span class="count-current-shown">1-3</span>'
                            + ' out of <span class="count-total">4 total</span>, '
                            + 'sorted by <span class="sort-order">Date added</span> descending</p>');
                });
            });

            describe('Children count label', function() {
                it('should show correct count on first page', function() {
                    pagingContainer.setPage(0);
                    respondWithMockPage(requests);
                    expect(pagingContainer.pagingHeader.$('.count-current-shown')).toHaveHtml('1-3');
                });

                it('should show correct count on second page', function() {
                    pagingContainer.setPage(1);
                    respondWithMockPage(requests);
                    expect(pagingContainer.pagingHeader.$('.count-current-shown')).toHaveHtml('4-4');
                });

                it('should show correct count for an empty collection', function() {
                    pagingContainer.setPage(0);
                    AjaxHelpers.respondWithJson(requests, mockEmptyPage);
                    expect(pagingContainer.pagingHeader.$('.count-current-shown')).toHaveHtml('0-0');
                });
            });

            describe('Children total label', function() {
                it('should show correct total on the first page', function() {
                    pagingContainer.setPage(0);
                    respondWithMockPage(requests);
                    expect(pagingContainer.pagingHeader.$('.count-total')).toHaveText('4 total');
                });

                it('should show correct total on the second page', function() {
                    pagingContainer.setPage(1);
                    respondWithMockPage(requests);
                    expect(pagingContainer.pagingHeader.$('.count-total')).toHaveText('4 total');
                });

                it('should show zero total for an empty collection', function() {
                    pagingContainer.setPage(0);
                    AjaxHelpers.respondWithJson(requests, mockEmptyPage);
                    expect(pagingContainer.pagingHeader.$('.count-total')).toHaveText('0 total');
                });
            });
        });

        describe('PagingFooter', function() {
            describe('Next page button', function() {
                beforeEach(function() {
                    // Render the page and header so that they can react to events
                    pagingContainer.render();
                    respondWithMockPage(requests);
                });

                it('does not move forward if a server error occurs', function() {
                    pagingContainer.setPage(0);
                    respondWithMockPage(requests);
                    pagingContainer.pagingFooter.$('.next-page-link').click();
                    requests[2].respond(500);
                    expect(pagingContainer.collection.currentPage).toBe(0);
                });

                it('can move to the next page', function() {
                    pagingContainer.setPage(0);
                    respondWithMockPage(requests);
                    pagingContainer.pagingFooter.$('.next-page-link').click();
                    respondWithMockPage(requests);
                    expect(pagingContainer.collection.currentPage).toBe(1);
                });

                it('should be enabled when there is at least one more page', function() {
                    pagingContainer.setPage(0);
                    respondWithMockPage(requests);
                    expect(pagingContainer.pagingFooter.$('.next-page-link')).not.toHaveClass('is-disabled');
                });

                it('should be disabled on the final page', function() {
                    pagingContainer.setPage(1);
                    respondWithMockPage(requests);
                    expect(pagingContainer.pagingFooter.$('.next-page-link')).toHaveClass('is-disabled');
                });

                it('should be disabled on an empty page', function() {
                    pagingContainer.setPage(0);
                    AjaxHelpers.respondWithJson(requests, mockEmptyPage);
                    expect(pagingContainer.pagingFooter.$('.next-page-link')).toHaveClass('is-disabled');
                });
            });

            describe('Previous page button', function() {
                beforeEach(function() {
                    // Render the page and header so that they can react to events
                    pagingContainer.render();
                    respondWithMockPage(requests);
                });

                it('does not move back if a server error occurs', function() {
                    pagingContainer.setPage(1);
                    respondWithMockPage(requests);
                    pagingContainer.pagingFooter.$('.previous-page-link').click();
                    requests[2].respond(500);
                    expect(pagingContainer.collection.currentPage).toBe(1);
                });

                it('can go back a page', function() {
                    pagingContainer.setPage(1);
                    respondWithMockPage(requests);
                    pagingContainer.pagingFooter.$('.previous-page-link').click();
                    respondWithMockPage(requests);
                    expect(pagingContainer.collection.currentPage).toBe(0);
                });

                it('should be disabled on the first page', function() {
                    pagingContainer.setPage(0);
                    respondWithMockPage(requests);
                    expect(pagingContainer.pagingFooter.$('.previous-page-link')).toHaveClass('is-disabled');
                });

                it('should be enabled on the second page', function() {
                    pagingContainer.setPage(1);
                    respondWithMockPage(requests);
                    expect(pagingContainer.pagingFooter.$('.previous-page-link')).not.toHaveClass('is-disabled');
                });

                it('should be disabled for an empty page', function() {
                    pagingContainer.setPage(0);
                    AjaxHelpers.respondWithJson(requests, mockEmptyPage);
                    expect(pagingContainer.pagingFooter.$('.previous-page-link')).toHaveClass('is-disabled');
                });
            });

            describe('Current page label', function() {
                it('should show 1 on the first page', function() {
                    pagingContainer.setPage(0);
                    respondWithMockPage(requests);
                    expect(pagingContainer.pagingFooter.$('.current-page')).toHaveText('1');
                });

                it('should show 2 on the second page', function() {
                    pagingContainer.setPage(1);
                    respondWithMockPage(requests);
                    expect(pagingContainer.pagingFooter.$('.current-page')).toHaveText('2');
                });

                it('should show 1 for an empty collection', function() {
                    pagingContainer.setPage(0);
                    AjaxHelpers.respondWithJson(requests, mockEmptyPage);
                    expect(pagingContainer.pagingFooter.$('.current-page')).toHaveText('1');
                });
            });

            describe('Page total label', function() {
                it('should show the correct value with more than one page', function() {
                    pagingContainer.setPage(0);
                    respondWithMockPage(requests);
                    expect(pagingContainer.pagingFooter.$('.total-pages')).toHaveText('2');
                });

                it('should show page 1 when there are no assets', function() {
                    pagingContainer.setPage(0);
                    AjaxHelpers.respondWithJson(requests, mockEmptyPage);
                    expect(pagingContainer.pagingFooter.$('.total-pages')).toHaveText('1');
                });
            });

            describe('Page input field', function() {
                var input;

                it('should initially have a blank page input', function() {
                    pagingContainer.setPage(0);
                    respondWithMockPage(requests);
                    expect(pagingContainer.pagingFooter.$('.page-number-input')).toHaveValue('');
                });

                it('should handle invalid page requests', function() {
                    pagingContainer.setPage(0);
                    respondWithMockPage(requests);
                    pagingContainer.pagingFooter.$('.page-number-input').val('abc');
                    pagingContainer.pagingFooter.$('.page-number-input').trigger('change');
                    expect(pagingContainer.collection.currentPage).toBe(0);
                    expect(pagingContainer.pagingFooter.$('.page-number-input')).toHaveValue('');
                });

                it('should switch pages via the input field', function() {
                    pagingContainer.setPage(0);
                    respondWithMockPage(requests);
                    pagingContainer.pagingFooter.$('.page-number-input').val('2');
                    pagingContainer.pagingFooter.$('.page-number-input').trigger('change');
                    AjaxHelpers.respondWithJson(requests, mockSecondPage);
                    expect(pagingContainer.collection.currentPage).toBe(1);
                    expect(pagingContainer.pagingFooter.$('.page-number-input')).toHaveValue('');
                });

                it('should handle AJAX failures when switching pages via the input field', function() {
                    pagingContainer.setPage(0);
                    respondWithMockPage(requests);
                    pagingContainer.pagingFooter.$('.page-number-input').val('2');
                    pagingContainer.pagingFooter.$('.page-number-input').trigger('change');
                    requests[1].respond(500);
                    expect(pagingContainer.collection.currentPage).toBe(0);
                    expect(pagingContainer.pagingFooter.$('.page-number-input')).toHaveValue('');
                });
            });
        });

        describe('Previews', function() {
            describe('Toggle Previews', function() {
                var testSendsAjax,
                    defaultUrl = '/preview/xblock/handler/trigger_previews';

                testSendsAjax = function(show_previews) {
                    it('should send ' + (!show_previews) + ' when showChildrenPreviews was ' + show_previews, function() {
                        pagingContainer.collection.showChildrenPreviews = show_previews;
                        pagingContainer.togglePreviews();
                        AjaxHelpers.expectJsonRequest(requests, 'POST', defaultUrl, {showChildrenPreviews: !show_previews});
                        AjaxHelpers.respondWithJson(requests, {showChildrenPreviews: !show_previews});
                    });
                };
                testSendsAjax(true);
                testSendsAjax(false);

                it('should trigger render on success', function() {
                    spyOn(pagingContainer, 'render');

                    pagingContainer.togglePreviews();
                    AjaxHelpers.respondWithJson(requests, {showChildrenPreviews: true});

                    expect(pagingContainer.render).toHaveBeenCalled();
                });

                it('should not trigger render on failure', function() {
                    spyOn(pagingContainer, 'render');

                    pagingContainer.togglePreviews();
                    AjaxHelpers.respondWithError(requests);

                    expect(pagingContainer.render).not.toHaveBeenCalled();
                });

                it('should send force_render when new block causes page change', function() {
                    pagingContainer.setPage(0);
                    respondWithMockPage(requests);
                    spyOn(pagingContainer, 'render');
                    var mockXBlockInfo = new XBlockInfo({id: 'mock-location'});
                    var mockXBlockView = new XBlockView({model: mockXBlockInfo});
                    mockXBlockView.model.id = 'mock-location';
                    pagingContainer.refresh(mockXBlockView, true);
                    expect(pagingContainer.render).toHaveBeenCalled();
                    expect(pagingContainer.render.calls.mostRecent().args[0].force_render).toEqual('mock-location');
                });
            });
        });
    });
});
