define([
    'sinon',
    'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
    'js/student_account/multiple_enterprise',
    'js/student_account/utils'
],
function(sinon, AjaxHelpers, MultipleEnterpriseInterface, Utils) {
    'use strict';

    describe('MultipleEnterpriseInterface', function() {
        var LEARNER_URL = '/enterprise/api/v1/enterprise-learner/?username=test-learner',
            NEXT_URL = '/dashboard',
            REDIRECT_URL = '/enterprise/select/active/?success_url=%2Fdashboard',
            ENTERPRISE_ACTIVATION_URL = '/enterprise/select/active',
            requests, xhrFactory;

        beforeEach(function() {
            // Mock the redirect call
            spyOn(MultipleEnterpriseInterface, 'redirect').and.callFake(function() {});
            spyOn(Utils, 'userFromEdxUserCookie').and.returnValue({username: 'test-learner'});
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
        });

        afterEach(function() {
            requests.restore();
        });

        it('gets learner information and checks redirect to enterprise selection page', function() {
            // Attempt to fetch a learner
            MultipleEnterpriseInterface.check(NEXT_URL);

            // Expect that the correct request was made to the server
            AjaxHelpers.expectRequest(
                requests,
                'GET',
                LEARNER_URL,
                null
            );

            // Simulate a successful response from the server
            AjaxHelpers.respondWithJson(requests, {count: 2});

            // Verify that the user was redirected correctly
            expect(MultipleEnterpriseInterface.redirect).toHaveBeenCalledWith(REDIRECT_URL);
        });

        it('checks bypass of enterprise selection page in case of enterprise in URL', function() {
            spyOn(MultipleEnterpriseInterface, 'getEnterpriseFromUrl').and.returnValue('SomeEnterprise');
            spyOn(MultipleEnterpriseInterface, 'checkEnterpriseExists').and.returnValue(true);

            // Attempt to fetch a learner
            MultipleEnterpriseInterface.check(NEXT_URL);

            // Expect that the correct request was made to the server
            AjaxHelpers.expectRequest(
                requests,
                'GET',
                LEARNER_URL,
                null
            );

            // Simulate a successful response from the server
            AjaxHelpers.respondWithJson(requests, {count: 2});

            AjaxHelpers.expectRequest(
                requests,
                'POST',
                ENTERPRISE_ACTIVATION_URL,
                $.param({
                    enterprise: 'SomeEnterprise'
                })
            );
            AjaxHelpers.respondWithNoContent(requests);

            // Verify that the user was redirected correctly
            expect(MultipleEnterpriseInterface.redirect).toHaveBeenCalledWith(NEXT_URL);
        });

        it('checks enterprise selection page redirect in case of enterprise activation failure', function() {
            spyOn(MultipleEnterpriseInterface, 'getEnterpriseFromUrl').and.returnValue('SomeEnterprise');
            spyOn(MultipleEnterpriseInterface, 'checkEnterpriseExists').and.returnValue(true);

            // Attempt to fetch a learner
            MultipleEnterpriseInterface.check(NEXT_URL);

            // Expect that the correct request was made to the server
            AjaxHelpers.expectRequest(
                requests,
                'GET',
                LEARNER_URL,
                null
            );

            // Simulate a successful response from the server
            AjaxHelpers.respondWithJson(requests, {count: 2});

            AjaxHelpers.expectRequest(
                requests,
                'POST',
                ENTERPRISE_ACTIVATION_URL,
                $.param({
                    enterprise: 'SomeEnterprise'
                })
            );
            // Simulate an error response from the server
            AjaxHelpers.respondWithError(requests);

            // Verify that the user was redirected correctly
            expect(MultipleEnterpriseInterface.redirect).toHaveBeenCalledWith(REDIRECT_URL);
        });

        it('gets learner information and checks that enterprise selection page is bypassed', function() {
            // Attempt to fetch a learner
            MultipleEnterpriseInterface.check(NEXT_URL);

            // Expect that the correct request was made to the server
            AjaxHelpers.expectRequest(
                requests,
                'GET',
                LEARNER_URL,
                null
            );

            // Simulate a successful response from the server
            AjaxHelpers.respondWithJson(requests, {count: 1});

            // Verify that the user was redirected correctly
            expect(MultipleEnterpriseInterface.redirect).toHaveBeenCalledWith(NEXT_URL);
        });

        it('correctly redirects the user if learner information call fails', function() {
            // Attempt to fetch a learner
            MultipleEnterpriseInterface.check(NEXT_URL);

            // Simulate an error response from the server
            AjaxHelpers.respondWithError(requests);

            // Verify that the user was redirected
            expect(MultipleEnterpriseInterface.redirect).toHaveBeenCalledWith(NEXT_URL);
        });
    });
}
);
