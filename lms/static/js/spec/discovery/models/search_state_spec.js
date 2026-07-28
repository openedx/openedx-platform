define([
    'sinon', 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers', 'js/discovery/models/search_state'
], function(sinon, AjaxHelpers, SearchState) {
    'use strict';

    var JSON_RESPONSE = {
        total: 365,
        results: [
            {
                data: {
                    modes: [
                        'honor'
                    ],
                    course: 'edX/DemoX/Demo_Course',
                    enrollment_start: '2015-04-21T00:00:00+00:00',
                    number: 'DemoX',
                    content: {
                        overview: ' About This Course Include your long course description here.',
                        display_name: 'edX Demonstration Course',
                        number: 'DemoX'
                    },
                    start: '1970-01-01T05:00:00+00:00',
                    image_url: '/c4x/edX/DemoX/asset/images_course_image.jpg',
                    org: 'edX',
                    id: 'edX/DemoX/Demo_Course'
                }
            }
        ]
    };

    describe('discovery.models.SearchState', function() {
        var requests, xhrFactory;

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            this.search = new SearchState();
            this.onSearch = jasmine.createSpy('onSearch');
            this.onNext = jasmine.createSpy('onNext');
            this.onError = jasmine.createSpy('onError');
            this.search.on('search', this.onSearch);
            this.search.on('next', this.onNext);
            this.search.on('error', this.onError);
        });

        afterEach(function() {
            requests.restore();
        });

        it('perform search', function() {
            this.search.performSearch('dummy');
            AjaxHelpers.respondWithJson(requests, JSON_RESPONSE);
            expect(this.onSearch).toHaveBeenCalledWith('dummy', 365);
            expect(this.search.discovery.courseCards.length).toBe(1);
            this.search.refineSearch({modes: 'honor'});
            AjaxHelpers.respondWithJson(requests, JSON_RESPONSE);
            expect(this.onSearch).toHaveBeenCalledWith('dummy', 365);
        });

        it('returns an error', function() {
            this.search.performSearch('');
            AjaxHelpers.respondWithError(requests, 500);
            expect(this.onError).toHaveBeenCalled();
        });

        it('loads next page', function() {
            this.search.performSearch('dummy');
            AjaxHelpers.respondWithJson(requests, JSON_RESPONSE);
            this.search.loadNextPage();
            AjaxHelpers.respondWithJson(requests, JSON_RESPONSE);
            expect(this.onNext).toHaveBeenCalled();
        });

        it('shows all results when there are none', function() {
            this.search.performSearch('dummy', {modes: 'SomeOption'});
            // no results
            AjaxHelpers.respondWithJson(requests, {total: 0});
            expect(this.onSearch).not.toHaveBeenCalled();
            // there should be another Ajax call to fetch all courses
            AjaxHelpers.respondWithJson(requests, JSON_RESPONSE);
            expect(this.onSearch).toHaveBeenCalledWith('dummy', 0);
            // new search
            this.search.performSearch('something');
            // no results
            AjaxHelpers.respondWithJson(requests, {total: 0});
            // should load cached results
            expect(this.onSearch).toHaveBeenCalledWith('dummy', 0);
        });
    });
});
