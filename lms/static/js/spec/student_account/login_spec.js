(function(define) {
    'use strict';

    define([
        'jquery',
        'underscore',
        'sinon',
        'common/js/spec_helpers/template_helpers',
        'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
        'js/student_account/models/LoginModel',
        'js/student_account/views/LoginView',
        'js/student_account/models/PasswordResetModel'
    ],
    function($, _, sinon, TemplateHelpers, AjaxHelpers, LoginModel, LoginView, PasswordResetModel) {
        describe('edx.student.account.LoginView', function() {
            var model = null,
                resetModel = null,
                view = null,
                requests = null,
                xhrFactory = null,
                authComplete = false,
                PLATFORM_NAME = 'edX',
                ENTERPRISE_SLUG_LOGIN_URL = 'enterprise/login',
                IS_ENTERPRISE_ENABLE = true,
                USER_DATA = {
                    email: 'xsy@edx.org',
                    password: 'xsyisawesome',
                },
                THIRD_PARTY_AUTH = {
                    currentProvider: null,
                    providers: [
                        {
                            id: 'oa2-google-oauth2',
                            name: 'Google',
                            iconClass: 'fa-google-plus',
                            loginUrl: '/auth/login/google-oauth2/?auth_entry=account_login',
                            registerUrl: '/auth/login/google-oauth2/?auth_entry=account_register'
                        },
                        {
                            id: 'oa2-facebook',
                            name: 'Facebook',
                            iconClass: 'fa-facebook',
                            loginUrl: '/auth/login/facebook/?auth_entry=account_login',
                            registerUrl: '/auth/login/facebook/?auth_entry=account_register'
                        }
                    ]
                },
                FORM_DESCRIPTION = {
                    method: 'post',
                    submit_url: '/api/user/v1/account/login_session/',
                    fields: [
                        {
                            placeholder: 'username@domain.com',
                            name: 'email',
                            label: 'Email',
                            defaultValue: '',
                            type: 'email',
                            required: true,
                            instructions: 'Enter your email.',
                            restrictions: {}
                        },
                        {
                            placeholder: '',
                            name: 'password',
                            label: 'Password',
                            defaultValue: '',
                            type: 'password',
                            required: true,
                            instructions: 'Enter your password.',
                            restrictions: {}
                        }
                    ]
                },
                COURSE_ID = 'edX/demoX/Fall';

            var createLoginView = function(test) {
                // Initialize the login model
                model = new LoginModel({}, {
                    url: FORM_DESCRIPTION.submit_url,
                    method: FORM_DESCRIPTION.method
                });

                // Initialize the passwordReset model
                resetModel = new PasswordResetModel({}, {
                    method: 'GET',
                    url: '#'
                });

                // Initialize the login view
                view = new LoginView({
                    fields: FORM_DESCRIPTION.fields,
                    model: model,
                    resetModel: resetModel,
                    thirdPartyAuth: THIRD_PARTY_AUTH,
                    platformName: PLATFORM_NAME,
                    enterpriseSlugLoginURL: ENTERPRISE_SLUG_LOGIN_URL,
                    isEnterpriseEnable: IS_ENTERPRISE_ENABLE
                });

                // Intercept events from the view
                authComplete = false;
                view.on('auth-complete', function() {
                    authComplete = true;
                });
            };

            var submitForm = function(validationSuccess) {
                // Create a fake click event
                var clickEvent = $.Event('click');

                // Simulate manual entry of login form data
                $('#login-email').val(USER_DATA.email);
                $('#login-password').val(USER_DATA.password);

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
                setFixtures('<div id="login-form"></div>');
                TemplateHelpers.installTemplate('templates/student_account/login');
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

            it('logs the user in', function() {
                createLoginView(this);

                // Submit the form, with successful validation
                submitForm(true);

                // Form button should be disabled on success.
                expect(view.$submitButton).toHaveAttr('disabled');

                // Verify that the client contacts the server with the expected data
                AjaxHelpers.expectRequest(
                    requests, 'POST',
                    FORM_DESCRIPTION.submit_url,
                    $.param(USER_DATA)
                );

                // Respond with status code 200
                AjaxHelpers.respondWithJson(requests, {});

                // Verify that auth-complete is triggered
                expect(authComplete).toBe(true);
            });

            it('sends analytics info containing the enrolled course ID', function() {
                var expectedData;

                createLoginView(this);

                // Simulate that the user is attempting to enroll in a course
                // by setting the course_id query string param.
                spyOn($, 'url').and.callFake(function(param) {
                    if (param === '?course_id') {
                        return encodeURIComponent(COURSE_ID);
                    }
                });

                // Attempt to login
                submitForm(true);

                // Verify that the client sent the course ID for analytics
                expectedData = {};
                $.extend(expectedData, USER_DATA, {
                    analytics: JSON.stringify({
                        enroll_course_id: COURSE_ID
                    })
                });

                AjaxHelpers.expectRequest(
                    requests, 'POST',
                    FORM_DESCRIPTION.submit_url,
                    $.param(expectedData)
                );
            });

            it('displays third-party auth login buttons', function() {
                createLoginView(this);

                // Verify that Google and Facebook registration buttons are displayed
                expect($('.button-oa2-google-oauth2')).toBeVisible();
                expect($('.button-oa2-facebook')).toBeVisible();
            });

            it('does not display the login form', function() {
                var thirdPartyAuthView = new LoginView({
                    fields: FORM_DESCRIPTION.fields,
                    model: model,
                    resetModel: resetModel,
                    thirdPartyAuth: THIRD_PARTY_AUTH,
                    platformName: PLATFORM_NAME,
                    enterpriseSlugLoginURL: ENTERPRISE_SLUG_LOGIN_URL,
                    is_require_third_party_auth_enabled: true
                });

                expect(thirdPartyAuthView).not.toContain(view.$submitButton);
                expect(thirdPartyAuthView).not.toContain($('form-field'));
            });

            it('does not display the enterprise login button', function() {
                var enterpriseDisabledLoginView = new LoginView({
                    fields: FORM_DESCRIPTION.fields,
                    model: model,
                    resetModel: resetModel,
                    thirdPartyAuth: THIRD_PARTY_AUTH,
                    platformName: PLATFORM_NAME,
                    enterpriseSlugLoginURL: ENTERPRISE_SLUG_LOGIN_URL,
                    isEnterpriseEnable: false
                });

                expect(enterpriseDisabledLoginView).not.toContain($('.enterprise-login'));
            });

            it('displays a link to the signin help', function() {
                createLoginView(this);

                // Verify that the Signin help link is displayed
                expect($('.login-help')).toBeVisible();
            });

            it('displays a link to the enterprise slug login', function() {
                createLoginView(this);

                // Verify that the enterprise login link is displayed
                expect($('.enterprise-login')).toBeVisible();
            });

            it('displays password reset success message after password reset request', function() {
                createLoginView(this);

                // Verify that the success message is not visible
                expect(view.$formFeedback.find('.' + view.passwordResetSuccessJsHook).length).toEqual(0);

                /* After a successful password reset request, the resetModel will trigger a 'sync'
                 * event, which lets the LoginView know to render the password reset success message.
                 */
                view.resetModel.trigger('sync');

                // Verify that the success message is visible
                expect(view.$formFeedback.find('.' + view.passwordResetSuccessJsHook).length).toEqual(1);
            });

            it('validates login form fields', function() {
                createLoginView(this);

                submitForm(true);

                // Verify that validation of form fields occurred
                expect(view.validate).toHaveBeenCalledWith($('#login-email')[0]);
                expect(view.validate).toHaveBeenCalledWith($('#login-password')[0]);
            });

            it('displays login form validation errors', function() {
                createLoginView(this);

                // Submit the form, with failed validation
                submitForm(false);

                // Verify that submission errors are visible
                expect(view.$formFeedback.find('.' + view.formErrorsJsHook).length).toEqual(1);

                // Expect auth complete NOT to have been triggered
                expect(authComplete).toBe(false);

                // Form button should be re-enabled when errors occur
                expect(view.$submitButton).not.toHaveAttr('disabled');
            });

            it('displays an error if the server returns an error while logging in', function() {
                createLoginView(this);

                // Submit the form, with successful validation
                submitForm(true);

                // Simulate an error from the LMS servers
                AjaxHelpers.respondWithError(requests);

                // Expect that an error is displayed and that auth complete is not triggered
                expect(view.$formFeedback.find('.' + view.formErrorsJsHook).length).toEqual(1);

                expect(authComplete).toBe(false);

                // Form button should be re-enabled on server failure.
                expect(view.$submitButton).not.toHaveAttr('disabled');

                // If we try again and succeed, the error should go away
                submitForm();

                // Form button should be disabled on success.
                expect(view.$submitButton).toHaveAttr('disabled');

                // This time, respond with status code 200
                AjaxHelpers.respondWithJson(requests, {});

                // Expect that the error is hidden and auth complete is triggered
                expect(view.$formFeedback.find('.' + view.formErrorsJsHook).length).toEqual(0);
                expect(authComplete).toBe(true);
            });

            it('displays an error if there is no internet connection', function() {
                var clock,
                    oldTimeout,
                    timeout,
                    $error;

                // We're defining "no internet connection" in this case as the
                // request timing out. We use a combination of the sinon fake
                // timer and jQuery.ajaxSetup() to force a request timeout.
                clock = sinon.useFakeTimers();
                oldTimeout = $.ajaxSetup().timeout;
                timeout = 1;
                $.ajaxSetup({timeout: timeout});

                createLoginView(this);

                // Submit the form, with successful validation
                submitForm(true);

                // Simulate a request timeout
                clock.tick(timeout + 1);

                // Expect that an error is displayed and that auth complete is not triggered
                $error = view.$formFeedback.find('.' + view.formErrorsJsHook);
                expect($error.length).toEqual(1);
                expect($error.text()).toContain(
                    'An error has occurred. Check your Internet connection and try again.'
                );
                expect(authComplete).toBe(false);

                // Finally, restore the old timeout and turn off the fake timer.
                $.ajaxSetup({timeout: oldTimeout});
                clock.restore();
            });

            it('displays an error if there is a server error', function() {
                var $error;
                createLoginView(this);

                // Submit the form, with successful validation
                submitForm(true);

                // Simulate an error from the LMS servers
                AjaxHelpers.respondWithError(requests, 500);

                // Expect that an error is displayed and that auth complete is not triggered
                $error = view.$formFeedback.find('.' + view.formErrorsJsHook);
                expect($error.length).toEqual(1);
                expect($error.text()).toContain(
                    'An error has occurred. Try refreshing the page, or check your Internet connection.'
                );
                expect(authComplete).toBe(false);
            });
        });
    });
}).call(this, define || RequireJS.define);
