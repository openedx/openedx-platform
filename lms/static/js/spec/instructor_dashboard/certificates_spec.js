/* global define, onCertificatesReady */
define([
    'jquery',
    'sinon',
    'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
    'js/instructor_dashboard/certificates'
],
function($, sinon, AjaxHelpers) {
    'use strict';

    describe('edx.instructor_dashboard.certificates.regenerate_certificates', function() {
        var $regenerate_certificates_button = null,
            $certificate_regeneration_status = null,
            requests = null,
            xhrFactory = null;
        var MESSAGES = {
            success_message: 'Certificate regeneration task has been started. '
                    + 'You can view the status of the generation task in the "Pending Tasks" section.',
            error_message: 'Please select one or more certificate statuses that require certificate regeneration.',
            server_error_message: 'Error while regenerating certificates. Please try again.'
        };
        var expected = {
            url: 'test/url/',
            postData: [],
            selected_statuses: ['downloadable', 'error'],
            body: 'certificate_statuses=downloadable&certificate_statuses=error'
        };

        var select_options = function(option_values) {
            $.each(option_values, function(index, element) {
                $('#certificate-regenerating-form input[value=' + element + ']').click();
            });
        };

        beforeEach(function() {
            var fixture = '<section id="certificates">'
                    + '<form id="certificate-regenerating-form" method="post" action="' + expected.url + '">'
                    + '<p class="under-heading">To regenerate certificates for your course, '
                    + '   chose the learners who will receive regenerated certificates and click <br> '
                    + '   Regenerate Certificates.'
                    + '</p>'
                    + '<input id="certificate_status_downloadable" type="checkbox" name="certificate_statuses" '
                    + '   value="downloadable">'
                    + '<label style="display: inline" for="certificate_status_downloadable">'
                    + '   Regenerate for learners who have already received certificates. (3)'
                    + '</label><br>'
                    + '<input id="certificate_status_notpassing" type="checkbox" name="certificate_statuses" '
                    + '   value="notpassing">'
                    + '<label style="display: inline" for="certificate_status_notpassing"> '
                    + '   Regenerate for learners who have not received certificates. (1)'
                    + '</label><br>'
                    + '<input id="certificate_status_error" type="checkbox" name="certificate_statuses" '
                    + '   value="error">'
                    + '<label style="display: inline" for="certificate_status_error"> '
                    + '   Regenerate for learners in an error state. (0)'
                    + '</label><br>'
                    + '<input type="button" class="btn-blue" id="btn-start-regenerating-certificates" '
                    + '   value="Regenerate Certificates" data-endpoint="' + expected.url + '">'
                    + '</form>'
                    + '<div class="message certificate-regeneration-status"></div>'
                    + '</section>';

            setFixtures(fixture);
            onCertificatesReady();
            $regenerate_certificates_button = $('#btn-start-regenerating-certificates');
            $certificate_regeneration_status = $('.certificate-regeneration-status');
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
        });

        afterEach(function() {
            requests.restore();
        });

        it('does not regenerate certificates if user cancels operation in confirm popup', function() {
            spyOn(window, 'confirm').and.returnValue(false);
            $regenerate_certificates_button.click();
            expect(window.confirm).toHaveBeenCalled();
            AjaxHelpers.expectNoRequests(requests);
        });

        it('sends regenerate certificates request if user accepts operation in confirm popup', function() {
            spyOn(window, 'confirm').and.returnValue(true);
            $regenerate_certificates_button.click();
            expect(window.confirm).toHaveBeenCalled();
            AjaxHelpers.expectRequest(requests, 'POST', expected.url);
        });

        it('sends regenerate certificates request with selected certificate statuses', function() {
            spyOn(window, 'confirm').and.returnValue(true);

            select_options(expected.selected_statuses);

            $regenerate_certificates_button.click();
            AjaxHelpers.expectRequest(requests, 'POST', expected.url, expected.body);
        });

        it('displays error message in case of server side error', function() {
            spyOn(window, 'confirm').and.returnValue(true);
            select_options(expected.selected_statuses);

            $regenerate_certificates_button.click();
            AjaxHelpers.respondWithError(requests, 500, {message: MESSAGES.server_error_message});
            expect($certificate_regeneration_status.text()).toEqual(MESSAGES.server_error_message);
        });

        it('displays error message returned by the server in case of unsuccessful request', function() {
            spyOn(window, 'confirm').and.returnValue(true);
            select_options(expected.selected_statuses);

            $regenerate_certificates_button.click();
            AjaxHelpers.respondWithError(requests, 400, {message: MESSAGES.error_message});
            expect($certificate_regeneration_status.text()).toEqual(MESSAGES.error_message);
        });

        it('displays success message returned by the server in case of successful request', function() {
            spyOn(window, 'confirm').and.returnValue(true);
            select_options(expected.selected_statuses);

            $regenerate_certificates_button.click();
            AjaxHelpers.respondWithJson(requests, {message: MESSAGES.success_message, success: true});
            expect($certificate_regeneration_status.text()).toEqual(MESSAGES.success_message);
        });
    });
}
);
