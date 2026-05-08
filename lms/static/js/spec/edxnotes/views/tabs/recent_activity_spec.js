define([
    'jquery', 'sinon', 'common/js/spec_helpers/template_helpers', 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
    'js/edxnotes/collections/notes', 'js/edxnotes/collections/tabs', 'js/edxnotes/views/tabs/recent_activity',
    'js/spec/edxnotes/helpers'
], function(
    $, sinon, TemplateHelpers, AjaxHelpers, NotesCollection, TabsCollection, RecentActivityView, Helpers
) {
    'use strict';

    describe('EdxNotes RecentActivityView', function() {
        var notes = {
                count: 3,
                current_page: 1,
                num_pages: 1,
                start: 0,
                next: null,
                previous: null,
                results: [
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
                ]
            },
            getView, tabInfo, recentActivityTabId,
            requests, xhrFactory;

        getView = function(collection, tabsCollection, options) {
            var view;

            options = _.defaults(options || {}, {
                el: $('.wrapper-student-notes'),
                collection: collection,
                tabsCollection: tabsCollection,
                createHeaderFooter: true
            });

            view = new RecentActivityView(options);
            tabsCollection.at(0).activate();

            return view;
        };

        tabInfo = {
            name: 'Recent Activity',
            identifier: 'view-recent-activity',
            icon: 'fa fa-clock-o',
            is_active: true,
            is_closable: false,
            view: 'Recent Activity'
        };

        recentActivityTabId = '#recent-panel';

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

            this.collection = new NotesCollection(notes, {perPage: 10, parse: true});
            this.collection.url = '/test/notes/';
            this.tabsCollection = new TabsCollection();
        });

        afterEach(function() {
            requests.restore();
        });

        it('displays a tab and content with proper data and order', function() {
            var view = getView(this.collection, this.tabsCollection);
            Helpers.verifyPaginationInfo(view, 'Showing 1-3 out of 3 total', true, 1, 1);
            Helpers.verifyPageData(view, this.tabsCollection, tabInfo, recentActivityTabId, notes);
        });

        it('will not render header and footer if there are no notes', function() {
            // eslint-disable-next-line no-shadow
            var notes = {
                count: 0,
                current_page: 1,
                num_pages: 1,
                start: 0,
                next: null,
                previous: null,
                results: []
            };
            var collection = new NotesCollection(notes, {perPage: 10, parse: true});
            var view = getView(collection, this.tabsCollection);
            expect(view.$('.search-tools.listing-tools')).toHaveLength(0);
            expect(view.$('.pagination.pagination-full.bottom')).toHaveLength(0);
        });

        it('can go to a page number', function() {
            // eslint-disable-next-line no-shadow
            var notes = Helpers.createNotesData(
                {
                    numNotesToCreate: 10,
                    count: 12,
                    num_pages: 2,
                    current_page: 1,
                    start: 0
                }
            );

            var collection = new NotesCollection(notes, {perPage: 10, parse: true});
            collection.url = '/test/notes/';
            var view = getView(collection, this.tabsCollection);

            Helpers.verifyPaginationInfo(view, 'Showing 1-10 out of 12 total', false, 1, 2);
            Helpers.verifyPageData(view, this.tabsCollection, tabInfo, recentActivityTabId, notes);

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
            Helpers.verifyPageData(view, this.tabsCollection, tabInfo, recentActivityTabId, notes);
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
            );
            var collection = new NotesCollection(page1Notes, {perPage: 10, parse: true});
            collection.url = '/test/notes/';
            var view = getView(collection, this.tabsCollection);

            Helpers.verifyPaginationInfo(view, 'Showing 1-10 out of 15 total', false, 1, 2);
            Helpers.verifyPageData(view, this.tabsCollection, tabInfo, recentActivityTabId, page1Notes);

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
            Helpers.verifyPageData(view, this.tabsCollection, tabInfo, recentActivityTabId, page2Notes);

            view.$('.pagination .previous-page-link').click();
            Helpers.verifyRequestParams(
                requests[requests.length - 1].url,
                {page: '1', page_size: '10'}
            );
            Helpers.respondToRequest(requests, page1Notes);

            Helpers.verifyPaginationInfo(view, 'Showing 1-10 out of 15 total', false, 1, 2);
            Helpers.verifyPageData(view, this.tabsCollection, tabInfo, recentActivityTabId, page1Notes);
        });

        it('sends correct page size value', function() {
            // eslint-disable-next-line no-shadow
            var notes = Helpers.createNotesData(
                {
                    numNotesToCreate: 5,
                    count: 7,
                    num_pages: 2,
                    current_page: 1,
                    start: 0
                }
            );
            var collection = new NotesCollection(notes, {perPage: 5, parse: true});
            collection.url = '/test/notes/';
            var view = getView(collection, this.tabsCollection);

            view.$('.pagination .next-page-link').click();
            Helpers.verifyRequestParams(
                requests[requests.length - 1].url,
                {page: '2', page_size: '5'}
            );
        });
    });
});
