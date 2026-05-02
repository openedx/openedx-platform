define(['sinon', 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers', 'js/student_account/enrollment'],
    function(sinon, AjaxHelpers, EnrollmentInterface) {
        'use strict';

        describe('EnrollmentInterface', function() {
            var COURSE_KEY = 'edX/DemoX/Fall',
                ENROLL_URL = '/api/commerce/v0/baskets/',
                FORWARD_URL = '/course_modes/choose/edX/DemoX/Fall/',
                EMBARGO_MSG_URL = '/embargo/blocked-message/enrollment/default/',
                requests, xhrFactory;

            beforeEach(function() {
                // Mock the redirect call
                spyOn(EnrollmentInterface, 'redirect').and.callFake(function() {});
                xhrFactory = sinon.useFakeXMLHttpRequest();
                requests = [];
                requests.currentIndex = 0;
                requests.restore = function() { xhrFactory.restore(); };
                xhrFactory.onCreate = function(req) { requests.push(req); };
            });

            afterEach(function() {
                requests.restore();
            });

            it('enrolls a user in a course', function() {
                // Attempt to enroll the user
                EnrollmentInterface.enroll(COURSE_KEY, FORWARD_URL);

                // Expect that the correct request was made to the server
                AjaxHelpers.expectRequest(
                    requests,
                    'POST',
                    ENROLL_URL,
                    '{"course_id":"edX/DemoX/Fall"}'
                );

                // Simulate a successful response from the server
                AjaxHelpers.respondWithJson(requests, {});

                // Verify that the user was redirected correctly
                expect(EnrollmentInterface.redirect).toHaveBeenCalledWith(FORWARD_URL);
            });

            it('redirects the user if enrollment fails', function() {
                // Attempt to enroll the user
                EnrollmentInterface.enroll(COURSE_KEY, FORWARD_URL);

                // Simulate an error response from the server
                AjaxHelpers.respondWithError(requests);

                // Verify that the user was still redirected
                expect(EnrollmentInterface.redirect).toHaveBeenCalledWith(FORWARD_URL);
            });

            it('redirects the user if blocked by an embargo', function() {
                // Attempt to enroll the user
                EnrollmentInterface.enroll(COURSE_KEY, FORWARD_URL);

                // Simulate an error response (403) from the server
                // with a "user_message_url" parameter for the redirect.
                // This will redirect the user to a page with messaging
                // explaining why he/she can't enroll.
                AjaxHelpers.respondWithError(
                    requests, 403,
                    {user_message_url: EMBARGO_MSG_URL}
                );

                // Verify that the user was redirected
                expect(EnrollmentInterface.redirect).toHaveBeenCalledWith(EMBARGO_MSG_URL);
            });
        });
    }
);
