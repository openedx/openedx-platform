define(['sinon', 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers', 'js/student_account/emailoptin'],
    function(sinon, AjaxHelpers, EmailOptInInterface) {
        'use strict';

        describe('EmailOptInInterface', function() {
            var COURSE_KEY = 'edX/DemoX/Fall',
                EMAIL_OPT_IN = 'True',
                EMAIL_OPT_IN_URL = '/api/user/v1/preferences/email_opt_in/',
                requests, xhrFactory;

            beforeEach(function() {
                xhrFactory = sinon.useFakeXMLHttpRequest();
                requests = [];
                requests.currentIndex = 0;
                requests.restore = function() { xhrFactory.restore(); };
                xhrFactory.onCreate = function(req) { requests.push(req); };
            });

            afterEach(function() {
                requests.restore();
            });

            it('Opts in for organization emails', function() {

                // Attempt to enroll the user
                EmailOptInInterface.setPreference(COURSE_KEY, EMAIL_OPT_IN);

                // Expect that the correct request was made to the server
                AjaxHelpers.expectRequest(
                    requests, 'POST', EMAIL_OPT_IN_URL, 'course_id=edX%2FDemoX%2FFall&email_opt_in=True'
                );

                // Simulate a successful response from the server
                AjaxHelpers.respondWithJson(requests, {});
            });
        });
    }
);
