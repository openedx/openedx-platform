define([
    'jquery', 'underscore', 'sinon', 'common/js/spec_helpers/template_helpers',
    'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
    'logger', 'js/edxnotes/collections/tabs', 'js/edxnotes/views/tabs/search_results',
    'js/spec/edxnotes/helpers'
], function(
    $, _, sinon, TemplateHelpers, AjaxHelpers, Logger, TabsCollection, SearchResultsView, Helpers
) {
    'use strict';

    describe('EdxNotes SearchResultsView', function() {
        var notes = [
                {
                    created: 'December 11, 2014 at 11:12AM',
                    updated: 'December 11, 2014 at 11:12AM',
                    text: 'Third added model',
                    quote: 'Should be listed first'
                },
                {
                    created: 'December 11, 2014 at 11:11AM',
                    updated: 'December 11, 2014 at 11:11AM',
                    text: 'Second added model',
                    quote: 'Should be listed second'
                },
                {
                    created: 'December 11, 2014 at 11:10AM',
                    updated: 'December 11, 2014 at 11:10AM',
                    text: 'First added model',
                    quote: 'Should be listed third'
                }
            ],
            responseJson = {
                count: 3,
                current_page: 1,
                num_pages: 1,
                start: 0,
                next: null,
                previous: null,
                results: notes
            },
            getView, submitForm, tabInfo, searchResultsTabId,
            requests, xhrFactory;

        getView = function(tabsCollection, perPage, options) {
            options = _.defaults(options || {}, {
                el: $('.wrapper-student-notes'),
                tabsCollection: tabsCollection,
                user: 'test_user',
                courseId: 'course_id',
                createTabOnInitialization: false,
                createHeaderFooter: true,
                perPage: perPage || 10
            });
            return new SearchResultsView(options);
        };

        submitForm = function(searchBox, text) {
            searchBox.$('.search-notes-input').val(text);
            searchBox.$('.search-notes-submit').click();
        };

        tabInfo = {
            name: 'Search Results',
            identifier: 'view-search-results',
            icon: 'fa fa-search',
            is_active: true,
            is_closable: true,
            view: 'Search Results'
        };

        searchResultsTabId = '#search-results-panel';

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            loadFixtures('js/fixtures/edxnotes/edxnotes.html');
            TemplateHelpers.installTemplates([
                'templates/edxnotes/note-item', 'templates/edxnotes/tab-item'
            ]);

            this.tabsCollection = new TabsCollection();
        });

        afterEach(function() {
            requests.restore();
        });

        it('does not create a tab and content on initialization', function() {
            var view = getView(this.tabsCollection);
            expect(this.tabsCollection).toHaveLength(0);
            expect(view.$('#search-results-panel')).not.toExist();
        });

        it('displays a tab and content on search with proper data and order', function() {
            var view = getView(this.tabsCollection);

            submitForm(view.searchBox, 'second');
            Helpers.respondToRequest(requests, responseJson, true);
            Helpers.verifyPageData(view, this.tabsCollection, tabInfo, searchResultsTabId, responseJson);
            Helpers.verifyPaginationInfo(view, 'Showing 1-3 out of 3 total', true, 1, 1);
        });

        it('displays loading indicator when search is running', function() {
            var view = getView(this.tabsCollection);

            submitForm(view.searchBox, 'test query');
            expect(view.$('.ui-loading')).not.toHaveClass('is-hidden');
            expect(view.$('.ui-loading')).toBeFocused();
            expect(this.tabsCollection).toHaveLength(1);
            expect(view.searchResults).toBeNull();
            expect(view.$('.tab-panel')).not.toExist();
            Helpers.respondToRequest(requests, responseJson, true);
            expect(view.$('.ui-loading')).toHaveClass('is-hidden');
        });

        it('displays no results message', function() {
            var view = getView(this.tabsCollection);

            submitForm(view.searchBox, 'some text');
            Helpers.respondToRequest(requests, _.extend(_.clone(responseJson), {count: 0, results: []}), true);

            expect(view.$('#search-results-panel')).not.toExist();
            expect(view.$('#no-results-panel')).toBeFocused();
            expect(view.$('#no-results-panel')).toExist();
            expect(view.$('#no-results-panel')).toContainText(
                'No results found for "some text".'
            );
        });

        it('does not send an additional request on switching between tabs', function() {
            var view = getView(this.tabsCollection);

            spyOn(Logger, 'log');
            submitForm(view.searchBox, 'test_query');
            AjaxHelpers.respondWithJson(requests, responseJson);

            expect(requests).toHaveLength(1);

            this.tabsCollection.add({});
            this.tabsCollection.at(1).activate();
            expect(view.$('#search-results-panel')).not.toExist();
            this.tabsCollection.at(0).activate();

            expect(requests).toHaveLength(1);
            expect(view.$('#search-results-panel')).toExist();
            expect(view.$('.note')).toHaveLength(3);
        });

        it('can clear search results if tab is closed', function() {
            var view = getView(this.tabsCollection);
            spyOn(view.searchBox, 'clearInput').and.callThrough();

            submitForm(view.searchBox, 'test_query');
            Helpers.respondToRequest(requests, responseJson, true);
            expect(view.searchResults).toBeDefined();
            this.tabsCollection.at(0).destroy();
            expect(view.searchResults).toBeNull();
            expect(view.searchBox.clearInput).toHaveBeenCalled();
        });

        it('can correctly show/hide error messages', function() {
            var view = getView(this.tabsCollection);

            submitForm(view.searchBox, 'test error');

            // First respond to the analytics event
            AjaxHelpers.respondWithNoContent(requests);

            // Now respond to the search with a 500 error
            AjaxHelpers.respondWithError(requests, 500, {error: 'test error message'});

            expect(view.$('.wrapper-msg')).not.toHaveClass('is-hidden');
            expect(view.$('.wrapper-msg .copy')).toContainText('test error message');
            expect(view.$('.ui-loading')).toHaveClass('is-hidden');

            submitForm(view.searchBox, 'Second');
            AjaxHelpers.respondWithJson(requests, responseJson);

            expect(view.$('.wrapper-msg')).toHaveClass('is-hidden');
            expect(view.$('.wrapper-msg .copy')).toBeEmpty();
        });

        it('can correctly update search results', function() {
            var view = getView(this.tabsCollection),
                newNotes = [{
                    created: 'December 11, 2014 at 11:10AM',
                    updated: 'December 11, 2014 at 11:10AM',
                    text: 'New Note',
                    quote: 'New Note'
                }];

            submitForm(view.searchBox, 'test_query');
            Helpers.respondToRequest(requests, responseJson, true);

            expect(view.$('.note')).toHaveLength(3);

            submitForm(view.searchBox, 'new_test_query');
            Helpers.respondToRequest(requests, {
                count: 1,
                current_page: 1,
                num_pages: 1,
                start: 0,
                next: null,
                previous: null,
                results: newNotes
            }, true);

            expect(view.$('.note').length).toHaveLength(1);
            view.searchResults.collection.each(function(model, index) {
                expect(model.get('text')).toBe(newNotes[index].text);
            });
        });

        it('will not render header and footer if there are no notes', function() {
            var view = getView(this.tabsCollection),
                // eslint-disable-next-line no-shadow
                notes = {
                    count: 0,
                    current_page: 1,
                    num_pages: 1,
                    start: 0,
                    next: null,
                    previous: null,
                    results: []
                };
            submitForm(view.searchBox, 'awesome');
            Helpers.respondToRequest(requests, notes, true);
            expect(view.$('.search-tools.listing-tools')).toHaveLength(0);
            expect(view.$('.pagination.pagination-full.bottom')).toHaveLength(0);
        });

        it('can go to a page number', function() {
            var view = getView(this.tabsCollection),
                // eslint-disable-next-line no-shadow
                notes = Helpers.createNotesData(
                    {
                        numNotesToCreate: 10,
                        count: 12,
                        num_pages: 2,
                        current_page: 1,
                        start: 0
                    }
                );

            submitForm(view.searchBox, 'awesome');
            Helpers.respondToRequest(requests, notes, true);
            Helpers.verifyPaginationInfo(view, 'Showing 1-10 out of 12 total', false, 1, 2);
            Helpers.verifyPageData(view, this.tabsCollection, tabInfo, searchResultsTabId, notes);

            view.$('input#page-number-input').val('2');
            view.$('input#page-number-input').trigger('change');
            Helpers.verifyRequestParams(
                requests[requests.length - 1].url,
                {page: '2', page_size: '10'}
            );

            notes = Helpers.createNotesData(
                {
                    numNotesToCreate: 2,
                    count: 12,
                    num_pages: 2,
                    current_page: 2,
                    start: 10
                }
            );
            Helpers.respondToRequest(requests, notes, true);
            Helpers.verifyPaginationInfo(view, 'Showing 11-12 out of 12 total', false, 2, 2);
            Helpers.verifyPageData(view, this.tabsCollection, tabInfo, searchResultsTabId, notes);
        });

        it('can navigate forward and backward', function() {
            var page1Notes = Helpers.createNotesData(
                    {
                        numNotesToCreate: 10,
                        count: 15,
                        num_pages: 2,
                        current_page: 1,
                        start: 0
                    }
                ),
                view = getView(this.tabsCollection);

            submitForm(view.searchBox, 'awesome');
            Helpers.respondToRequest(requests, page1Notes, true);
            Helpers.verifyPaginationInfo(view, 'Showing 1-10 out of 15 total', false, 1, 2);
            Helpers.verifyPageData(view, this.tabsCollection, tabInfo, searchResultsTabId, page1Notes);

            view.$('.pagination .next-page-link').click();
            Helpers.verifyRequestParams(
                requests[requests.length - 1].url,
                {page: '2', page_size: '10'}
            );
            var page2Notes = Helpers.createNotesData(
                {
                    numNotesToCreate: 5,
                    count: 15,
                    num_pages: 2,
                    current_page: 2,
                    start: 10
                }
            );
            Helpers.respondToRequest(requests, page2Notes, true);
            Helpers.verifyPaginationInfo(view, 'Showing 11-15 out of 15 total', false, 2, 2);
            Helpers.verifyPageData(view, this.tabsCollection, tabInfo, searchResultsTabId, page2Notes);

            view.$('.pagination .previous-page-link').click();
            Helpers.verifyRequestParams(
                requests[requests.length - 1].url,
                {page: '1', page_size: '10'}
            );
            Helpers.respondToRequest(requests, page1Notes);

            Helpers.verifyPaginationInfo(view, 'Showing 1-10 out of 15 total', false, 1, 2);
            Helpers.verifyPageData(view, this.tabsCollection, tabInfo, searchResultsTabId, page1Notes);
        });

        it('sends correct page size value', function() {
            var view = getView(this.tabsCollection, 5);

            submitForm(view.searchBox, 'awesome');
            Helpers.verifyRequestParams(
                requests[requests.length - 1].url,
                {page: '1', page_size: '5'}
            );
        });
    });
});
