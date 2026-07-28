define([
    'jquery',
    'URI',
    'underscore',
    'sinon',
    'edx-ui-toolkit/js/pagination/paging-collection',
    'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
    'common/js/components/views/paging_footer'
], function($, URI, _, sinon, PagingCollection, AjaxHelpers, PagingFooter) {
    'use strict';

    describe('PagingFooter', function() {
        var pagingFooter,
            requests,
            xhrFactory,
            mockPage = function(currentPage, numPages, collectionLength) {
                if (_.isUndefined(collectionLength)) {
                    collectionLength = 1;
                }
                return {
                    count: null,
                    page: currentPage,
                    num_pages: numPages,
                    // need to have non-empty collection to render
                    results: _.map(_.range(collectionLength), function() { return {}; })
                };
            },
            nextPageCss = '.next-page-link',
            previousPageCss = '.previous-page-link',
            currentPageCss = '.current-page',
            totalPagesCss = '.total-pages',
            pageNumberInputCss = '.page-number-input';

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            setFixtures('<div class="paging-footer"></div>');
            var collection = new PagingCollection(mockPage(1, 2), {parse: true});
            collection.url = '/test/url/';

            pagingFooter = new PagingFooter({
                el: $('.paging-footer'),
                collection: collection
            }).render();
        });

        afterEach(function() {
            requests.restore();
        });

        describe('when hideWhenOnePage is true', function() {
            beforeEach(function() {
                pagingFooter.hideWhenOnePage = true;
            });

            it('should not render itself for an empty collection', function() {
                pagingFooter.collection.reset(mockPage(0, 0, 0), {parse: true});
                expect(pagingFooter.$el).toHaveClass('hidden');
            });

            it('should not render itself for a dataset with just one page', function() {
                pagingFooter.collection.reset(mockPage(1, 1), {parse: true});
                expect(pagingFooter.$el).toHaveClass('hidden');
            });
        });

        describe('when hideWhenOnepage is false', function() {
            it('should render itself for an empty collection', function() {
                pagingFooter.collection.reset(mockPage(0, 0, 0), {parse: true});
                expect(pagingFooter.$el).not.toHaveClass('hidden');
            });

            it('should render itself for a dataset with just one page', function() {
                pagingFooter.collection.reset(mockPage(1, 1), {parse: true});
                expect(pagingFooter.$el).not.toHaveClass('hidden');
            });
        });

        describe('Next page button', function() {
            it('does not move forward if a server error occurs', function() {
                pagingFooter.$(nextPageCss).click();
                requests[0].respond(500);
                expect(pagingFooter.$(currentPageCss)).toHaveText('1');
            });

            it('can move to the next page', function() {
                pagingFooter.$(nextPageCss).click();
                AjaxHelpers.respondWithJson(requests, mockPage(2, 2));
                expect(pagingFooter.collection.getPageNumber()).toBe(2);
            });

            it('should be enabled when there is at least one more page', function() {
                // in beforeEach we're set up on page 1 out of 2
                expect(pagingFooter.$(nextPageCss)).not.toHaveClass('is-disabled');
            });

            it('should be disabled on the final page', function() {
                pagingFooter.$(nextPageCss).click();
                AjaxHelpers.respondWithJson(requests, mockPage(2, 2));
                expect(pagingFooter.$(nextPageCss)).toHaveClass('is-disabled');
            });
        });

        describe('Previous page button', function() {
            it('does not move back if a server error occurs', function() {
                pagingFooter.collection.reset(mockPage(2, 2), {parse: true});
                pagingFooter.$(previousPageCss).click();
                requests[0].respond(500);
                expect(pagingFooter.$(currentPageCss)).toHaveText('2');
            });

            it('can go back a page', function() {
                pagingFooter.$(nextPageCss).click();
                AjaxHelpers.respondWithJson(requests, mockPage(2, 2));
                pagingFooter.$(previousPageCss).click();
                AjaxHelpers.respondWithJson(requests, mockPage(1, 2));
                expect(pagingFooter.$(currentPageCss)).toHaveText('1');
            });

            it('should be disabled on the first page', function() {
                expect(pagingFooter.$(previousPageCss)).toHaveClass('is-disabled');
            });

            it('should be enabled on the second page', function() {
                pagingFooter.$(nextPageCss).click();
                AjaxHelpers.respondWithJson(requests, mockPage(2, 2));
                expect(pagingFooter.$(previousPageCss)).not.toHaveClass('is-disabled');
            });
        });

        describe('Current page label', function() {
            it('should show 1 on the first page', function() {
                expect(pagingFooter.$(currentPageCss)).toHaveText('1');
            });

            it('should show 2 on the second page', function() {
                pagingFooter.$(nextPageCss).click();
                AjaxHelpers.respondWithJson(requests, mockPage(2, 2));
                expect(pagingFooter.$(currentPageCss)).toHaveText('2');
            });
        });

        describe('Page total label', function() {
            it('should show the correct value with more than one page', function() {
                expect(pagingFooter.$(totalPagesCss)).toHaveText('2');
            });
        });

        describe('Page input field', function() {
            beforeEach(function() {
                pagingFooter.render();
            });

            it('should initially have a blank page input', function() {
                expect(pagingFooter.$(pageNumberInputCss)).toHaveValue('');
            });

            it('should handle invalid page requests', function() {
                pagingFooter.$(pageNumberInputCss).val('abc');
                pagingFooter.$(pageNumberInputCss).trigger('change');
                expect(pagingFooter.$(currentPageCss)).toHaveText('1');
                expect(pagingFooter.$(pageNumberInputCss)).toHaveValue('');
            });

            it('should switch pages via the input field', function() {
                pagingFooter.$(pageNumberInputCss).val('2');
                pagingFooter.$(pageNumberInputCss).trigger('change');
                AjaxHelpers.respondWithJson(requests, mockPage(2, 2));
                expect(pagingFooter.$(currentPageCss)).toHaveText('2');
                expect(pagingFooter.$(pageNumberInputCss)).toHaveValue('');
            });

            it('should handle AJAX failures when switching pages via the input field', function() {
                pagingFooter.$(pageNumberInputCss).val('2');
                pagingFooter.$(pageNumberInputCss).trigger('change');
                requests[0].respond(500);
                expect(pagingFooter.$(currentPageCss)).toHaveText('1');
                expect(pagingFooter.$(pageNumberInputCss)).toHaveValue('');
            });
        });
    });
});
