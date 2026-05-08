(function(define) {
    'use strict';

    define([
        'jquery',
        'underscore',
        'sinon',
        'common/js/spec_helpers/template_helpers',
        'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
        'js/student_account/models/RegisterModel',
        'js/student_account/views/RegisterView',
        'js/student_account/tos_modal'
    ],
    function($, _, sinon, TemplateHelpers, AjaxHelpers, RegisterModel, RegisterView) {
        describe('edx.student.account.RegisterView', function() {
            var model = null,
                view = null,
                requests = null,
                xhrFactory = null,
                authComplete = false,
                PLATFORM_NAME = 'edX',
                COURSE_ID = 'edX/DemoX/Fall',
                USER_DATA = {
                    email: 'xsy@edx.org',
                    name: 'Xsy M. Education',
                    username: 'Xsy',
                    password: 'xsyisawesome',
                    level_of_education: 'p',
                    gender: 'm',
                    year_of_birth: 2014,
                    mailing_address: '141 Portland',
                    goals: 'To boldly learn what no letter of the alphabet has learned before',
                    confirm_email: 'xsy@edx.org',
                    honor_code: true
                },
                $email = null,
                $name = null,
                $username = null,
                $password = null,
                $levelOfEducation = null,
                $gender = null,
                $yearOfBirth = null,
                $mailingAddress = null,
                $goals = null,
                $confirmEmail = null,
                $honorCode = null,
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
                VALIDATION_DECISIONS_POSITIVE = {
                    validation_decisions: {
                        email: '',
                        username: '',
                        password: '',
                        confirm_email: ''
                    }
                },
                VALIDATION_DECISIONS_NEGATIVE = {
                    validation_decisions: {
                        email: 'Error.',
                        username: 'Error.',
                        password: 'Error.',
                        confirm_email: 'Error'
                    }
                },
                FORM_DESCRIPTION = {
                    method: 'post',
                    submit_url: '/api/user/v1/account/registration/',
                    validation_url: '/api/user/v1/validation/registration',
                    fields: [
                        {
                            placeholder: 'username@domain.com',
                            name: 'email',
                            label: 'Email',
                            defaultValue: '',
                            type: 'email',
                            required: true,
                            exposed: true,
                            instructions: 'Enter your email.',
                            restrictions: {}
                        },
                        {
                            placeholder: '',
                            name: 'confirm_email',
                            label: 'Confirm Email',
                            defaultValue: '',
                            type: 'text',
                            required: true,
                            exposed: true,
                            instructions: 'Enter your email.',
                            restrictions: {}
                        },
                        {
                            placeholder: 'Jane Doe',
                            name: 'name',
                            label: 'Full Name',
                            defaultValue: '',
                            type: 'text',
                            required: true,
                            exposed: true,
                            instructions: 'Enter your username.',
                            restrictions: {}
                        },
                        {
                            placeholder: 'JaneDoe',
                            name: 'username',
                            label: 'Username',
                            defaultValue: '',
                            type: 'text',
                            required: true,
                            exposed: true,
                            instructions: 'Enter your username.',
                            restrictions: {}
                        },
                        {
                            placeholder: '',
                            name: 'password',
                            label: 'Password',
                            defaultValue: '',
                            type: 'password',
                            required: true,
                            exposed: true,
                            instructions: 'Enter your password.',
                            restrictions: {}
                        },
                        {
                            placeholder: '',
                            name: 'level_of_education',
                            label: 'Highest Level of Education Completed',
                            defaultValue: '',
                            type: 'select',
                            options: [
                                {value: '', name: '--'},
                                {value: 'p', name: 'Doctorate'},
                                {value: 'm', name: "Master's or professional degree"},
                                {value: 'b', name: "Bachelor's degree"}
                            ],
                            required: false,
                            exposed: false,
                            instructions: 'Select your education level.',
                            restrictions: {}
                        },
                        {
                            placeholder: '',
                            name: 'gender',
                            label: 'Gender',
                            defaultValue: '',
                            type: 'select',
                            options: [
                                {value: '', name: '--'},
                                {value: 'm', name: 'Male'},
                                {value: 'f', name: 'Female'},
                                {value: 'o', name: 'Other'}
                            ],
                            required: false,
                            exposed: false,
                            instructions: 'Select your gender.',
                            restrictions: {}
                        },
                        {
                            placeholder: '',
                            name: 'year_of_birth',
                            label: 'Year of Birth',
                            defaultValue: '',
                            type: 'select',
                            options: [
                                {value: '', name: '--'},
                                {value: 1900, name: '1900'},
                                {value: 1950, name: '1950'},
                                {value: 2014, name: '2014'}
                            ],
                            required: false,
                            exposed: false,
                            instructions: 'Select your year of birth.',
                            restrictions: {}
                        },
                        {
                            placeholder: '',
                            name: 'mailing_address',
                            label: 'Mailing Address',
                            defaultValue: '',
                            type: 'textarea',
                            required: false,
                            exposed: false,
                            instructions: 'Enter your mailing address.',
                            restrictions: {}
                        },
                        {
                            placeholder: '',
                            name: 'goals',
                            label: 'Goals',
                            defaultValue: '',
                            type: 'textarea',
                            required: false,
                            exposed: false,
                            instructions: "If you'd like, tell us why you're interested in edX.",
                            restrictions: {}
                        },
                        {
                            placeholder: '',
                            name: 'honor_code',
                            label: 'I agree to the Terms of Service and Honor Code',
                            defaultValue: '',
                            type: 'checkbox',
                            required: true,
                            exposed: true,
                            instructions: '',
                            restrictions: {},
                            supplementalLink: '/honor',
                            supplementalText: 'Review the Terms of Service and Honor Code'
                        },
                    ]
                };
            var createRegisterView = function(that, formFields) {
                var fields = formFields;
                if (typeof fields === 'undefined') {
                    fields = FORM_DESCRIPTION.fields;
                }

                // Initialize the register model
                model = new RegisterModel({}, {
                    url: FORM_DESCRIPTION.submit_url,
                    method: FORM_DESCRIPTION.method
                });

                // Initialize the register view
                view = new RegisterView({
                    fields: fields,
                    model: model,
                    thirdPartyAuth: THIRD_PARTY_AUTH,
                    platformName: PLATFORM_NAME
                });

                // Intercept events from the view
                authComplete = false;
                view.on('auth-complete', function() {
                    authComplete = true;
                });

                // Target each form field.
                $email = $('#register-email');
                $confirmEmail = $('#register-confirm_email');
                $name = $('#register-name');
                $username = $('#register-username');
                $password = $('#register-password');
                $levelOfEducation = $('#register-level_of_education');
                $gender = $('#register-gender');
                $yearOfBirth = $('#register-year_of_birth');
                $mailingAddress = $('#register-mailing_address');
                $goals = $('#register-goals');
                $honorCode = $('#register-honor_code');
            };

            var fillData = function() {
                $email.val(USER_DATA.email);
                $confirmEmail.val(USER_DATA.email);
                $name.val(USER_DATA.name);
                $username.val(USER_DATA.username);
                $password.val(USER_DATA.password);
                $levelOfEducation.val(USER_DATA.level_of_education);
                $gender.val(USER_DATA.gender);
                $yearOfBirth.val(USER_DATA.year_of_birth);
                $mailingAddress.val(USER_DATA.mailing_address);
                $goals.val(USER_DATA.goals);
                // Check the honor code checkbox
                $honorCode.prop('checked', USER_DATA.honor_code);
            };

            var liveValidate = function($el, validationSuccess) {
                $el.focus();
                if (!_.isUndefined(validationSuccess) && !validationSuccess) {
                    model.trigger('validation', $el, VALIDATION_DECISIONS_NEGATIVE);
                } else {
                    model.trigger('validation', $el, VALIDATION_DECISIONS_POSITIVE);
                }
            };

            var submitForm = function(validationSuccess) {
                // Create a fake click event
                var clickEvent = $.Event('click');

                $('#toggle_optional_fields').click();

                // Simulate manual entry of registration form data
                fillData();

                // If validationSuccess isn't passed, we avoid
                // spying on `view.validate` twice
                if (!_.isUndefined(validationSuccess)) {
                    // Force validation to return as expected
                    spyOn(view, 'validate').and.returnValue({
                        isValid: validationSuccess,
                        message: 'Submission was validated.'
                    });
                    // Successful validation means there's no need to use AJAX calls from liveValidate,
                    if (validationSuccess) {
                        spyOn(view, 'liveValidate').and.callFake(function() {});
                    }
                }

                // Submit the email address
                view.submitForm(clickEvent);
            };

            beforeEach(function() {
                setFixtures('<div id="register-form"></div>');
                TemplateHelpers.installTemplate('templates/student_account/register');
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

            it('registers a new user', function() {
                createRegisterView(this);

                // Submit the form, with successful validation
                submitForm(true);

                // Verify that the client contacts the server with the expected data
                AjaxHelpers.expectRequest(
                    requests, 'POST',
                    FORM_DESCRIPTION.submit_url,
                    $.param(USER_DATA)
                );

                // Respond with status code 200
                AjaxHelpers.respondWithJson(requests, {});

                // Verify that auth complete is triggered
                expect(authComplete).toBe(true);

                // Form button should be disabled on success.
                expect(view.$submitButton).toHaveAttr('disabled');
            });

            it('sends analytics info containing the enrolled course ID', function() {
                var expectedData;

                createRegisterView(this);

                // Simulate that the user is attempting to enroll in a course
                // by setting the course_id query string param.
                spyOn($, 'url').and.callFake(function(param) {
                    if (param === '?course_id') {
                        return encodeURIComponent(COURSE_ID);
                    }
                    return null;
                });

                // Attempt to register
                submitForm(true);

                // Verify that the client sent the course ID for analytics
                expectedData = {course_id: COURSE_ID};
                $.extend(expectedData, USER_DATA);

                AjaxHelpers.expectRequest(
                    requests, 'POST',
                    FORM_DESCRIPTION.submit_url,
                    $.param(expectedData)
                );
            });

            it('displays third-party auth registration buttons', function() {
                createRegisterView(this);

                // Verify that Google and Facebook registration buttons are displayed
                expect($('.button-oa2-google-oauth2')).toBeVisible();
                expect($('.button-oa2-facebook')).toBeVisible();
            });

            it('does not display the registration form', function() {
                var thirdPartyAuthView = new RegisterView({
                    fields: FORM_DESCRIPTION.fields,
                    model: model,
                    thirdPartyAuth: THIRD_PARTY_AUTH,
                    platformName: PLATFORM_NAME,
                    is_require_third_party_auth_enabled: true
                });

                expect(thirdPartyAuthView).not.toContain(view.$submitButton);
                expect(thirdPartyAuthView).not.toContain($('form-field'));
            });

            it('validates registration form fields on form submission', function() {
                createRegisterView(this);

                // Submit the form, with successful validation
                submitForm(true);

                // Verify that validation of form fields occurred
                expect(view.validate).toHaveBeenCalledWith($email[0]);
                expect(view.validate).toHaveBeenCalledWith($name[0]);
                expect(view.validate).toHaveBeenCalledWith($username[0]);
                expect(view.validate).toHaveBeenCalledWith($password[0]);

                // Verify that no submission errors are visible
                expect(view.$formFeedback.find('.' + view.formErrorsJsHook).length).toEqual(0);

                // Form button should be disabled on success.
                expect(view.$submitButton).toHaveAttr('disabled');
            });

            it('live validates registration form fields', function() {
                var requiredValidationFields = [$email, $confirmEmail, $username, $password],
                    i,
                    $el;
                createRegisterView(this);

                for (i = 0; i < requiredValidationFields.length; ++i) {
                    $el = requiredValidationFields[i];

                    // Perform successful live validations.
                    liveValidate($el);

                    // Confirm success.
                    expect($el).toHaveClass('success');

                    // Confirm that since we've blurred from each input, required text doesn't show.
                    expect(view.getRequiredTextLabel($el)).toHaveClass('hidden');

                    // Confirm fa-check shows.
                    expect(view.getIcon($el)).toHaveClass('fa-check');
                    expect(view.getIcon($el)).toBeVisible();

                    // Confirm the error tip is empty.
                    expect(view.getErrorTip($el).val().length).toBe(0);
                }
            });

            it('displays registration form validation errors on form submission', function() {
                createRegisterView(this);

                // Submit the form, with failed validation
                submitForm(false);

                // Verify that submission errors are visible
                expect(view.$formFeedback.find('.' + view.formErrorsJsHook).length).toEqual(1);

                // Expect that auth complete is NOT triggered
                expect(authComplete).toBe(false);

                // Form button should be re-enabled on error.
                expect(view.$submitButton).not.toHaveAttr('disabled');
            });

            it('displays live registration form validation errors', function() {
                var requiredValidationFields = [$email, $confirmEmail, $username, $password],
                    i,
                    $el;
                createRegisterView(this);

                for (i = 0; i < requiredValidationFields.length; ++i) {
                    $el = requiredValidationFields[i];

                    // Perform invalid live validations.
                    liveValidate($el, false);

                    // Confirm error.
                    expect($el).toHaveClass('error');

                    // Confirm that since we've blurred from each input, required text still shows for errors.
                    expect(view.getRequiredTextLabel($el)).not.toHaveClass('hidden');

                    // Confirm fa-times shows.
                    expect(view.getIcon($el)).toHaveClass('fa-exclamation');
                    expect(view.getIcon($el)).toBeVisible();

                    // Confirm the error tip shows an error message.
                    expect(view.getErrorTip($el).val()).not.toBeEmpty();
                }
            });

            it('displays an error on form submission if the server returns an error', function() {
                createRegisterView(this);

                // Submit the form, with successful validation
                submitForm(true);

                // Simulate an error from the LMS servers
                AjaxHelpers.respondWithError(requests);

                // Expect that an error is displayed and that auth complete is NOT triggered
                expect(view.$formFeedback.find('.' + view.formErrorsJsHook).length).toEqual(1);
                expect(authComplete).toBe(false);

                // If we try again and succeed, the error should go away
                submitForm();

                // This time, respond with status code 200
                AjaxHelpers.respondWithJson(requests, {});

                // Expect that the error is hidden and that auth complete is triggered
                expect(view.$formFeedback.find('.' + view.formErrorsJsHook).length).toEqual(0);
                expect(authComplete).toBe(true);

                // Form button should be disabled on success.
                expect(view.$submitButton).toHaveAttr('disabled');
            });

            it('shows optional exposed fields', function() {
                var formFields = FORM_DESCRIPTION.fields;
                formFields.push({
                    placeholder: '',
                    name: 'exposed_custom_optional_field',
                    label: 'Exposed custom optional field.',
                    defaultValue: '',
                    type: 'checkbox',
                    required: false,
                    exposed: true,
                    instructions: 'Check this field if you would like to.',
                    restrictions: {}
                });

                createRegisterView(this, formFields);
                var elementClasses = view.$('.exposed-optional-fields').attr('class');
                var elementChildren = view.$('.exposed-optional-fields .form-field');
                // Expect the exposed optional fields container does not have other
                // classes assigned, like .hidden
                expect(elementClasses).toEqual('exposed-optional-fields');
                expect(elementChildren.length).toEqual(1);
            });

            it('hides optional fields by default', function() {
                createRegisterView(this);
                expect(view.$('.optional-fields')).toHaveClass('hidden');
            });

            it('displays optional fields when checkbox is selected', function() {
                createRegisterView(this);
                $('#toggle_optional_fields').click();
                expect(view.$('.optional-fields')).not.toHaveClass('hidden');
            });

            it('displays a modal with the terms of service', function() {
                var $modal,
                    $content;

                createRegisterView(this);

                // Check there is no modal container initially
                expect($('.tos-modal').length).toEqual(0);

                // And no modal is being displayed
                expect($('body').hasClass('open-modal')).toBe(false);

                // Click TOS button
                $('.checkbox-honor_code .supplemental-link a').click();

                // TOS modal container has been added and is visible
                $modal = $('.tos-modal');
                expect($modal.length).toEqual(1);
                expect($modal).toBeVisible();
                expect($('body').hasClass('open-modal')).toBe(true);

                // The modal has a content area, a Close button and a title matching the TOS link
                $content = $modal.find('.modal-content');
                expect($content.length).toEqual(1);
                expect($content.find('.modal-close-button').text()).toEqual('Close');
                expect($content.find('#modal-header-text').text()).toEqual(
                    'Terms of Service and Honor Code'
                );

                // The content area has an iframe displaying the TOS link
                expect($content.find('iframe').length).toEqual(1);
                expect($content.find('iframe').attr('src').endsWith('/honor')).toBe(true);

                // Click the close button
                $('.modal-close-button').click();

                // The modal is now hidden
                expect($modal).toBeHidden();
                expect($('body').hasClass('open-modal')).toBe(false);

                // The iframe has been deleted
                expect($content.find('iframe').length).toEqual(0);
            });

            it('displays optional fields toggle', function() {
                createRegisterView(this);
                expect(view.$('.checkbox-optional_fields_toggle')).toBeVisible();
            });

            it('hides optional fields toggle when there are no visible optional fields', function() {
                createRegisterView(this, [
                    {
                        placeholder: '',
                        name: 'hidden_optional',
                        label: 'Hidden Optional',
                        defaultValue: '',
                        type: 'hidden',
                        required: false,
                        instructions: 'Used for testing hidden input fields that are optional.',
                        restrictions: {}
                    }
                ]);
                expect(view.$('.checkbox-optional_fields_toggle')).toHaveClass('hidden');
            });
        });
    });
}).call(this, define || RequireJS.define);
