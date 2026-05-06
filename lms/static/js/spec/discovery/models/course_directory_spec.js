define([
    'sinon', 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers', 'js/discovery/models/course_discovery'
], function(sinon, AjaxHelpers, CourseDiscovery) {
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

    describe('discovery.models.CourseDiscovery', function() {
        var requests, xhrFactory;

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            this.discovery = new CourseDiscovery();
            this.discovery.fetch();
            AjaxHelpers.respondWithJson(requests, JSON_RESPONSE);
        });

        afterEach(function() {
            requests.restore();
        });

        it('parses server response', function() {
            expect(this.discovery.courseCards.length).toBe(1);
            expect(this.discovery.facetOptions.length).toBe(27);
        });

        it('resets collections', function() {
            this.discovery.reset();
            expect(this.discovery.courseCards.length).toBe(0);
            expect(this.discovery.facetOptions.length).toBe(0);
        });

        it('returns latest course cards', function() {
            var latest = this.discovery.latest();
            expect(latest.length).toBe(1);
        });
    });
});
