(function(define) {
    'use strict';

    define([
        'jquery',
        'underscore',
        'sinon',
        'common/js/spec_helpers/template_helpers',
        'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
        'js/student_account/models/PasswordResetModel',
        'js/student_account/views/PasswordResetView'
    ],
    function($, _, sinon, TemplateHelpers, AjaxHelpers, PasswordResetModel, PasswordResetView) {
        describe('edx.student.account.PasswordResetView', function() {
            var model = null,
                view = null,
                requests = null,
                xhrFactory = null,
                EMAIL = 'xsy@edx.org',
                FORM_DESCRIPTION = {
                    method: 'post',
                    submit_url: '/account/password',
                    fields: [{
                        name: 'email',
                        label: 'Email',
                        defaultValue: '',
                        type: 'text',
                        required: true,
                        placeholder: 'place@holder.org',
                        instructions: 'Enter your email.',
                        restrictions: {}
                    }]
                };

            var createPasswordResetView = function(that) {
                // Initialize the password reset model
                model = new PasswordResetModel({}, {
                    url: FORM_DESCRIPTION.submit_url,
                    method: FORM_DESCRIPTION.method
                });

                // Initialize the password reset view
                view = new PasswordResetView({
                    fields: FORM_DESCRIPTION.fields,
                    model: model
                });

            };

            var submitEmail = function(validationSuccess) {
                // Create a fake click event
                var clickEvent = $.Event('click');

                // Simulate manual entry of an email address
                $('#password-reset-email').val(EMAIL);

                // If validationSuccess isn't passed, we avoid
                // spying on `view.validate` twice
                if (!_.isUndefined(validationSuccess)) {
                    // Force validation to return as expected
                    spyOn(view, 'validate').and.returnValue({
                        isValid: validationSuccess,
                        message: 'Submission was validated.'
                    });
                }

                // Submit the email address
                view.submitForm(clickEvent);
            };

            beforeEach(function() {
                setFixtures('<div id="password-reset-form" class="form-wrapper hidden"></div>');
                TemplateHelpers.installTemplate('templates/student_account/password_reset');
                TemplateHelpers.installTemplate('templates/student_account/form_field');
                xhrFactory = sinon.useFakeXMLHttpRequest();
                requests = [];
                requests.currentIndex = 0;
                requests.restore = function() { xhrFactory.restore(); };
                xhrFactory.onCreate = function(req) { requests.push(req); };
            });

            afterEach(function() {
                requests.restore();
            });

            it('allows the user to request a new password', function() {
                var syncSpy, passwordEmailSentSpy;

                createPasswordResetView(this);

                // We expect these events to be triggered upon a successful password reset
                syncSpy = jasmine.createSpy('syncEvent');
                passwordEmailSentSpy = jasmine.createSpy('passwordEmailSentEvent');
                view.listenTo(view.model, 'sync', syncSpy);
                view.listenTo(view, 'password-email-sent', passwordEmailSentSpy);

                // Submit the form, with successful validation
                submitEmail(true);

                // Verify that the client contacts the server with the expected data
                AjaxHelpers.expectRequest(
                    requests, 'POST',
                    FORM_DESCRIPTION.submit_url,
                    $.param({email: EMAIL})
                );

                // Respond with status code 200
                AjaxHelpers.respondWithJson(requests, {});

                // Verify that the events were triggered
                expect(syncSpy).toHaveBeenCalled();
                expect(passwordEmailSentSpy).toHaveBeenCalled();

                // Verify that password reset view has been removed
                expect($(view.el).html().length).toEqual(0);
            });

            it('displays a link to the password reset help', function() {
                createPasswordResetView(this);

                // Verify that the password reset help link is displayed
                expect($('.reset-help')).toBeVisible();
            });

            it('validates the email field', function() {
                createPasswordResetView(this);

                // Submit the form, with successful validation
                submitEmail(true);

                // Verify that validation of the email field occurred
                expect(view.validate).toHaveBeenCalledWith($('#password-reset-email')[0]);

                // Verify that no submission errors are visible
                expect(view.$formFeedback.find('.' + view.formErrorsJsHook).length).toEqual(0);
            });

            it('displays password reset validation errors', function() {
                createPasswordResetView(this);

                // Submit the form, with failed validation
                submitEmail(false);

                // Verify that submission errors are visible
                expect(view.$formFeedback.find('.' + view.formErrorsJsHook).length).toEqual(1);
            });

            it('displays an error if the server returns an error while sending a password reset email', function() {
                createPasswordResetView(this);
                submitEmail(true);

                // Simulate an error from the LMS servers
                AjaxHelpers.respondWithError(requests);

                // Expect that an error is displayed
                expect(view.$formFeedback.find('.' + view.formErrorsJsHook).length).toEqual(1);

                // If we try again and succeed, the error should go away
                submitEmail();

                // This time, respond with status code 200
                AjaxHelpers.respondWithJson(requests, {});

                // Expect that the error is hidden
                expect(view.$formFeedback.find('.' + view.formErrorsJsHook).length).toEqual(0);
            });
        });
    });
}).call(this, define || RequireJS.define);
