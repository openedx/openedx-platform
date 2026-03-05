define([
    'jquery', 'sinon', 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers', 'common/js/spec_helpers/template_helpers',
    'js/discovery/discovery_factory'
], function($, sinon, AjaxHelpers, TemplateHelpers, DiscoveryFactory) {
    'use strict';

    var MEANINGS = {
        org: {
            name: 'Organization',
            terms: {
                edX1: 'edX_1'
            }
        },
        modes: {
            name: 'Course Type',
            terms: {
                honor: 'Honor',
                verified: 'Verified'
            }
        },
        language: {
            terms: {
                en: 'English',
                hr: 'Croatian'
            }
        }
    };

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
                    id: 'edX/DemoX/Demo_Course',
                    language: 'en'
                }
            }
        ],
        aggs: {
            org: {
                total: 26,
                terms: {
                    edX1: 1,
                    edX2: 1,
                    edX3: 1,
                    edX4: 1,
                    edX5: 1,
                    edX6: 1,
                    edX7: 1,
                    edX8: 1,
                    edX9: 1,
                    edX10: 1,
                    edX11: 1,
                    edX12: 1,
                    edX13: 1,
                    edX14: 1,
                    edX15: 1,
                    edX16: 1,
                    edX17: 1,
                    edX18: 1,
                    edX19: 1,
                    edX20: 1,
                    edX21: 1,
                    edX22: 1,
                    edX23: 1,
                    edX24: 1,
                    edX25: 1,
                    edX26: 1
                },
                other: 0
            },
            modes: {
                total: 1,
                terms: {
                    honor: 1
                },
                other: 0
            }
        }
    };

    describe('discovery.DiscoveryFactory', function() {
        var requests, xhrFactory;

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            loadFixtures('js/fixtures/discovery.html');
            TemplateHelpers.installTemplates([
                'templates/discovery/course_card',
                'templates/discovery/facet',
                'templates/discovery/facet_option',
                'templates/discovery/filter',
                'templates/discovery/filter_bar'
            ]);
            DiscoveryFactory(MEANINGS);

            jasmine.clock().install();
        });

        afterEach(function() {
            jasmine.clock().uninstall();
            requests.restore();
        });

        it('does search', function() {
            $('.discovery-input').val('test');
            $('.discovery-submit').trigger('click');
            AjaxHelpers.respondWithJson(requests, JSON_RESPONSE);
            expect($('.courses-listing article').length).toEqual(1);
            expect($('.courses-listing .course-title')).toContainHtml('edX Demonstration Course');
            expect($('.active-filter').length).toBe(1);
        });

        it('loads more', function() {

            $('.discovery-input').val('test');
            $('.discovery-submit').trigger('click');
            AjaxHelpers.respondWithJson(requests, JSON_RESPONSE);
            expect($('.courses-listing article').length).toEqual(1);
            expect($('.courses-listing .course-title')).toContainHtml('edX Demonstration Course');
            jasmine.clock().tick(500);
            window.scroll(0, $(document).height());
            $(window).trigger('scroll');

            // TODO: determine why the search API is invoked twice
            AjaxHelpers.respondWithJson(requests, JSON_RESPONSE);
            AjaxHelpers.respondWithJson(requests, JSON_RESPONSE);
            expect($('.courses-listing article').length).toEqual(2);
        });

        it('displays not found message', function() {
            $('.discovery-input').val('asdfasdf');
            $('.discovery-submit').trigger('click');
            AjaxHelpers.respondWithJson(requests, {});
            expect($('.discovery-input').val()).toEqual('');
            expect($('#discovery-message')).not.toBeEmpty();
            expect($('.courses-listing')).toBeEmpty();
        });

        it('displays error message', function() {
            $('.discovery-input').val('asdfasdf');
            $('.discovery-submit').trigger('click');
            AjaxHelpers.respondWithError(requests, 404, {error: 'some-search-error'});
            expect($('#discovery-message')).not.toBeEmpty();
            expect($('#discovery-message')).toContainHtml('some-search-error');
            expect($('.courses-listing')).toBeEmpty();
        });

        it('check filters and bar removed on clear all', function() {
            $('.discovery-input').val('test');
            $('.discovery-submit').trigger('click');
            AjaxHelpers.respondWithJson(requests, JSON_RESPONSE);
            expect($('.active-filter').length).toBe(1);
            expect($('#filter-bar')).not.toHaveClass('is-collapsed');
            $('#clear-all-filters').trigger('click');
            expect($('.active-filter').length).toBe(0);
            expect($('#filter-bar')).toHaveClass('is-collapsed');
        });

        it('check filters and bar removed on last filter cleared', function() {
            $('.discovery-input').val('test');
            $('.discovery-submit').trigger('click');
            AjaxHelpers.respondWithJson(requests, JSON_RESPONSE);
            expect($('.active-filter').length).toBe(1);
            var $filter = $('.active-filter');
            $filter.find('.discovery-button').trigger('click');
            expect($('.active-filter').length).toBe(0);
        });

        it('filter results by named facet', function() {
            $('.discovery-input').val('test');
            $('.discovery-submit').trigger('click');
            AjaxHelpers.respondWithJson(requests, JSON_RESPONSE);
            expect($('.active-filter').length).toBe(1);
            $('.search-facets li [data-value="edX1"]').trigger('click');
            expect($('.active-filter').length).toBe(2);
            expect($('.active-filter [data-value="edX1"]').length).toBe(1);
            $('.search-facets li [data-value="edX1"]').trigger('click');
            expect($('.active-filter [data-value="edX1"]').length).toBe(0);
        });
    });

    describe('discovery.DiscoveryFactory default filters', function() {
        var requests, xhrFactory;

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            loadFixtures('js/fixtures/discovery.html');
            TemplateHelpers.installTemplates([
                'templates/discovery/course_card',
                'templates/discovery/facet',
                'templates/discovery/facet_option',
                'templates/discovery/filter',
                'templates/discovery/filter_bar'
            ]);
            // setDefaultFilter to true
            DiscoveryFactory(MEANINGS, '', 'en', 'Asia/Kolkata', true);

            jasmine.clock().install();
        });

        afterEach(function() {
            jasmine.clock().uninstall();
            requests.restore();
        });

        it('filters by default language', function() {
            $('.discovery-submit').trigger('click');
            var request = AjaxHelpers.currentRequest(requests);
            // make sure language filter is set
            expect(request.requestBody).toMatch(/language=en/);
        });
    });
});
