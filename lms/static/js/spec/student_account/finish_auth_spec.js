(function(define) {
    'use strict';

    define([
        'jquery',
        'jquery.url',
        'sinon',
        'utility',
        'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
        'js/student_account/views/FinishAuthView',
        'js/student_account/enrollment',
        'js/student_account/shoppingcart',
        'js/student_account/emailoptin'
    ],
    function($, url, sinon, utility, AjaxHelpers, FinishAuthView, EnrollmentInterface, ShoppingCartInterface,
        EmailOptInInterface) {
        describe('FinishAuthView', function() {
            var requests = null,
                view = null,
                xhrFactory = null,
                FORWARD_URL = '/courseware/next',
                COURSE_KEY = 'course-v1:edX+test+15';

            var ajaxSpyAndInitialize = function() {

                // Initialize the access view
                view = new FinishAuthView({});

                // Mock the redirect call
                spyOn(view, 'redirect').and.callFake(function() {});

                // Mock the enrollment and shopping cart interfaces
                spyOn(EnrollmentInterface, 'enroll').and.callFake(function() {});
                spyOn(ShoppingCartInterface, 'addCourseToCart').and.callFake(function() {});
                spyOn(EmailOptInInterface, 'setPreference')
                    .and.callFake(function() { return {always: function(r) { r(); }}; });

                view.render();
            };

            /**
             * Simulate query string params.
             *
             * @param {object} params Parameters to set, each of which
             * should be prefixed with '?'
             */
            var setFakeQueryParams = function(params) {
                spyOn($, 'url').and.callFake(function(requestedParam) {
                    if (params.hasOwnProperty(requestedParam)) {
                        return params[requestedParam];
                    }
                });
            };

            beforeEach(function() {
                // Stub analytics tracking
                window.analytics = jasmine.createSpyObj('analytics', ['track', 'page', 'pageview', 'trackLink']);
                xhrFactory = sinon.useFakeXMLHttpRequest();
                requests = [];
                requests.currentIndex = 0;
                requests.restore = function() { xhrFactory.restore(); };
                xhrFactory.onCreate = function(req) { requests.push(req); };
            });

            afterEach(function() {
                requests.restore();
            });

            it('saves the email opt-in preference before enrollment', function() {
                // Simulate providing enrollment query string params
                setFakeQueryParams({
                    '?enrollment_action': 'enroll',
                    '?course_id': COURSE_KEY,
                    '?email_opt_in': 'true'
                });

                ajaxSpyAndInitialize(this);

                // Expect that the view tried to save the email opt in preference
                expect(EmailOptInInterface.setPreference).toHaveBeenCalledWith(
                    COURSE_KEY,
                    'true'
                );
                // Expect that the view tried to enroll the student
                expect(EnrollmentInterface.enroll).toHaveBeenCalledWith(
                    COURSE_KEY,
                    '/course_modes/choose/' + COURSE_KEY + '/'
                );
            });

            it('enrolls the user on auth complete', function() {
                // Simulate providing enrollment query string params
                setFakeQueryParams({
                    '?enrollment_action': 'enroll',
                    '?course_id': COURSE_KEY
                });

                ajaxSpyAndInitialize(this);

                // Expect that the view tried to enroll the student
                expect(EnrollmentInterface.enroll).toHaveBeenCalledWith(
                    COURSE_KEY,
                    '/course_modes/choose/' + COURSE_KEY + '/'
                );
            });

            it('sends the user to the course mode selection flow with bulk purchase workflow', function() {
                // Simulate providing enrollment query string params
                setFakeQueryParams({
                    '?enrollment_action': 'enroll',
                    '?course_id': COURSE_KEY,
                    '?purchase_workflow': 'bulk'
                });

                ajaxSpyAndInitialize(this);

                // Expect that the view redirected to the course
                // mode select flow with the purchase_workflow parameter
                expect(EnrollmentInterface.enroll).toHaveBeenCalledWith(
                    COURSE_KEY,
                    '/course_modes/choose/' + COURSE_KEY + '/?purchase_workflow=bulk'
                );
            });

            it('sends the user to the payment flow for a paid course mode', function() {
                // Simulate providing enrollment query string params
                // AND specifying a course mode.
                setFakeQueryParams({
                    '?enrollment_action': 'enroll',
                    '?course_id': COURSE_KEY,
                    '?course_mode': 'verified'
                });

                ajaxSpyAndInitialize(this);

                // Expect that the view tried to auto-enroll the student
                // with a redirect into the payment flow.
                expect(EnrollmentInterface.enroll).toHaveBeenCalledWith(
                    COURSE_KEY,
                    '/verify_student/start-flow/' + COURSE_KEY + '/'
                );
            });

            it('sends the user to the payment flow for a paid course mode with bulk purchase workflow', function() {
                // Simulate providing enrollment query string params
                // AND specifying a course mode
                // AND purchase workflow type.
                setFakeQueryParams({
                    '?enrollment_action': 'enroll',
                    '?course_id': COURSE_KEY,
                    '?course_mode': 'professional-no-id',
                    '?purchase_workflow': 'bulk'
                });

                ajaxSpyAndInitialize(this);

                // Expect that the view tried to auto-enroll the student
                // with a redirect into the payment flow including the
                // purchase_workflow parameter.
                expect(EnrollmentInterface.enroll).toHaveBeenCalledWith(
                    COURSE_KEY,
                    '/verify_student/start-flow/' + COURSE_KEY + '/?purchase_workflow=bulk'
                );
            });

            it('sends the user to the student dashboard for an unpaid course mode', function() {
                // Simulate providing enrollment query string params
                // AND specifying a course mode.
                setFakeQueryParams({
                    '?enrollment_action': 'enroll',
                    '?course_id': COURSE_KEY,
                    '?course_mode': 'audit'
                });

                ajaxSpyAndInitialize(this);

                // Expect that the view tried auto-enrolled the student
                // and sent the student to the dashboard
                // (skipping the payment flow).
                expect(EnrollmentInterface.enroll).toHaveBeenCalledWith(COURSE_KEY, '/dashboard');
            });

            it('adds a white-label course to the shopping cart on auth complete', function() {
                // Simulate providing "add to cart" query string params
                setFakeQueryParams({
                    '?enrollment_action': 'add_to_cart',
                    '?course_id': COURSE_KEY
                });

                ajaxSpyAndInitialize(this);

                // Expect that the view tried to add the course to the user's shopping cart
                expect(ShoppingCartInterface.addCourseToCart).toHaveBeenCalledWith(COURSE_KEY);
            });

            it('redirects the user to the dashboard if no course is provided', function() {
                ajaxSpyAndInitialize(this);

                // Since we did not provide a ?next query param, expect a redirect to the dashboard.
                expect(view.redirect).toHaveBeenCalledWith('/dashboard');
            });

            it('redirects the user to the next page when done', function() {
                // Simulate providing a ?next query string parameter
                setFakeQueryParams({'?next': FORWARD_URL});

                ajaxSpyAndInitialize(this);

                // Verify that we were redirected
                expect(view.redirect).toHaveBeenCalledWith(FORWARD_URL);
            });

            it('ignores redirect to external URLs', function() {
                // Simulate providing a ?next query string parameter
                // that goes to an external URL
                setFakeQueryParams({'?next': 'http://www.example.com'});

                ajaxSpyAndInitialize(this);

                // Expect that we ignore the external URL and redirect to the dashboard
                expect(view.redirect).toHaveBeenCalledWith('/dashboard');
            });
        });
    });
}).call(this, define || RequireJS.define);
