(function(define) {
    'use strict';

    define([
        'jquery',
        'underscore',
        'backbone',
        'sinon',
        'common/js/spec_helpers/template_helpers',
        'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
        'js/student_account/views/AccessView',
        'js/student_account/views/FormView',
        'js/student_account/enrollment',
        'js/student_account/shoppingcart',
        'js/student_account/emailoptin'
    ],
    function($, _, Backbone, sinon, TemplateHelpers, AjaxHelpers, AccessView, FormView, EnrollmentInterface,
        ShoppingCartInterface) {
        describe('edx.student.account.AccessView', function() {
            var requests = null,
                xhrFactory = null,
                view = null,
                FORM_DESCRIPTION = {
                    method: 'post',
                    submit_url: '/submit',
                    fields: [
                        {
                            name: 'email',
                            label: 'Email',
                            defaultValue: '',
                            type: 'text',
                            required: true,
                            placeholder: 'xsy@edx.org',
                            instructions: 'Enter your email here.',
                            restrictions: {}
                        },
                        {
                            name: 'username',
                            label: 'Username',
                            defaultValue: '',
                            type: 'text',
                            required: true,
                            placeholder: 'Xsy',
                            instructions: 'Enter your username here.',
                            restrictions: {
                                max_length: 200
                            }
                        }
                    ]
                },
                FORWARD_URL = (
                    '/account/finish_auth'
                    + '?course_id=edx%2FDemoX%2FFall'
                    + '&enrollment_action=enroll'
                    + '&next=%2Fdashboard'
                ),
                THIRD_PARTY_COMPLETE_URL = '/auth/complete/provider/';

            var ajaxSpyAndInitialize = function(that, mode, nextUrl, finishAuthUrl, createAccountOption) {
                var options = {
                        initial_mode: mode,
                        third_party_auth: {
                            currentProvider: null,
                            providers: [],
                            secondaryProviders: [{name: 'provider'}],
                            finishAuthUrl: finishAuthUrl
                        },
                        login_redirect_url: nextUrl, // undefined for default
                        platform_name: 'edX',
                        login_form_desc: FORM_DESCRIPTION,
                        registration_form_desc: FORM_DESCRIPTION,
                        password_reset_form_desc: FORM_DESCRIPTION,
                        account_creation_allowed: createAccountOption
                    },
                    $logistrationElement = $('#login-and-registration-container');

                // Initialize the access view
                view = new AccessView(_.extend(options, {el: $logistrationElement}));

                // Mock the redirect call
                spyOn(view, 'redirect').and.callFake(function() {});

                // Mock the enrollment and shopping cart interfaces
                spyOn(EnrollmentInterface, 'enroll').and.callFake(function() {});
                spyOn(ShoppingCartInterface, 'addCourseToCart').and.callFake(function() {});
            };

            var assertForms = function(visibleType, hiddenType) {
                expect($(visibleType)).not.toHaveClass('hidden');
                expect($(hiddenType)).toHaveClass('hidden');
                expect($('#password-reset-form')).toHaveClass('hidden');
            };

            var selectForm = function(type) {
                // Create a fake change event to control form toggling
                var changeEvent = $.Event('change');
                changeEvent.currentTarget = $('.form-toggle[data-type="' + type + '"]');

                // Load form corresponding to the change event
                view.toggleForm(changeEvent);
            };

            beforeEach(function() {
                spyOn(window.history, 'pushState');
                setFixtures('<div id="login-and-registration-container" class="login-register" />');
                TemplateHelpers.installTemplate('templates/student_account/access');
                TemplateHelpers.installTemplate('templates/student_account/login');
                TemplateHelpers.installTemplate('templates/student_account/register');
                TemplateHelpers.installTemplate('templates/student_account/password_reset');
                TemplateHelpers.installTemplate('templates/student_account/form_field');
                TemplateHelpers.installTemplate('templates/student_account/institution_login');
                TemplateHelpers.installTemplate('templates/student_account/institution_register');

                // Stub analytics tracking
                window.analytics = jasmine.createSpyObj('analytics', ['track', 'page', 'pageview', 'trackLink']);
                xhrFactory = sinon.useFakeXMLHttpRequest();
                requests = [];
                requests.currentIndex = 0;
                requests.restore = function() { xhrFactory.restore(); };
                xhrFactory.onCreate = function(req) { requests.push(req); };
            });

            afterEach(function() {
                Backbone.history.stop();
                requests.restore();
            });

            it('can initially display the login form', function() {
                ajaxSpyAndInitialize(this, 'login');

                /* Verify that the login form is expanded, and that the
                /* registration form is collapsed.
                 */
                assertForms('#login-form', '#register-form');
            });

            it('can initially display the registration form', function() {
                ajaxSpyAndInitialize(this, 'register');

                /* Verify that the registration form is expanded, and that the
                /* login form is collapsed.
                 */
                assertForms('#register-form', '#login-form');
            });

            it('toggles between the login and registration forms', function() {
                ajaxSpyAndInitialize(this, 'login');

                // Simulate selection of the registration form
                selectForm('register');
                assertForms('#register-form', '#login-form');

                // Simulate selection of the login form
                selectForm('login');
                assertForms('#login-form', '#register-form');
            });

            it('toggles between the login and institution login view', function() {
                ajaxSpyAndInitialize(this, 'login');

                // Simulate clicking on institution login button
                $('#login-form .button-secondary-login[data-type="institution_login"]').click();
                assertForms('#institution_login-form', '#login-form');

                // Simulate selection of the login form
                selectForm('login');
                assertForms('#login-form', '#institution_login-form');
            });

            it('toggles between the register and institution register view', function() {
                ajaxSpyAndInitialize(this, 'register');

                // Simulate clicking on institution login button
                $('#register-form .button-secondary-login[data-type="institution_login"]').click();
                assertForms('#institution_login-form', '#register-form');

                // Simulate selection of the login form
                selectForm('register');
                assertForms('#register-form', '#institution_login-form');
            });

            it('displays the reset password form', function() {
                ajaxSpyAndInitialize(this, 'login');

                // Simulate a click on the reset password link
                view.resetPassword();

                // Verify that the login-form is hidden
                expect($('#login-form')).toHaveClass('hidden');

                // Verify that the password reset form is not hidden
                expect($('#password-reset-form')).not.toHaveClass('hidden');
            });

            it('redirects the user to the dashboard on auth complete', function() {
                ajaxSpyAndInitialize(this, 'register');

                // Trigger auth complete
                view.subview.register.trigger('auth-complete');

                // Since we did not provide a ?next query param, expect a redirect to the dashboard.
                expect(view.redirect).toHaveBeenCalledWith('/dashboard');
            });

            it('proceeds with the third party auth pipeline if active', function() {
                ajaxSpyAndInitialize(this, 'register', '/', THIRD_PARTY_COMPLETE_URL);

                // Trigger auth complete
                view.subview.register.trigger('auth-complete');

                // Verify that we were redirected
                expect(view.redirect).toHaveBeenCalledWith(THIRD_PARTY_COMPLETE_URL);
            });

            it('redirects the user to the next page on auth complete', function() {
                // The 'next' argument is often used to redirect to the auto-enrollment view
                ajaxSpyAndInitialize(this, 'register', FORWARD_URL);

                // Trigger auth complete
                view.subview.register.trigger('auth-complete');

                // Verify that we were redirected
                expect(view.redirect).toHaveBeenCalledWith(FORWARD_URL);
            });

            it('hides create an account section', function() {
                ajaxSpyAndInitialize(this, 'login', '', '', false);

                // Expect the Create an account section is hidden
                expect((view.$el.find('.toggle-form')).length).toEqual(0);
            });

            it('shows create an account section', function() {
                ajaxSpyAndInitialize(this, 'login', '', '', true);

                // Expect the Create an account section is visible
                expect((view.$el.find('.toggle-form')).length).toEqual(1);
            });
        });
    });
}).call(this, define || RequireJS.define);
