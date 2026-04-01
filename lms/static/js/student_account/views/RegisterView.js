(function(define) {
    'use strict';

    define([
        'jquery',
        'underscore',
        'gettext',
        'edx-ui-toolkit/js/utils/string-utils',
        'edx-ui-toolkit/js/utils/html-utils',
        'js/student_account/views/FormView',
        'text!templates/student_account/form_status.underscore'
    ],
    function(
        $, _, gettext,
        StringUtils,
        HtmlUtils,
        FormView,
        formStatusTpl
    ) {
        return FormView.extend({
            el: '#register-form',
            tpl: '#register-tpl',
            validationUrl: '/api/user/v1/validation/registration',
            events: {
                'click .js-register': 'submitForm',
                'click .login-provider': 'thirdPartyAuth',
                'click input[required][type="checkbox"]': 'liveValidateHandler',
                'blur input[required], textarea[required], select[required]': 'liveValidateHandler',
                'focus input[required], textarea[required], select[required]': 'handleRequiredInputFocus'
            },
            liveValidationFields: [
                'name',
                'username',
                'password',
                'email',
                'confirm_email',
                'country',
                'honor_code',
                'terms_of_service'
            ],
            formType: 'register',
            formFields: '.form-fields',
            formStatusTpl: formStatusTpl,
            authWarningJsHook: 'js-auth-warning',
            defaultFormErrorsTitle: gettext('We couldn\'t create your account.'),
            submitButton: '.js-register',
            positiveValidationIcon: 'fa-check',
            negativeValidationIcon: 'fa-exclamation',
            successfulValidationDisplaySeconds: 3,
            // These are reset to true on form submission.
            positiveValidationEnabled: true,
            negativeValidationEnabled: true,

            preRender: function(data) {
                this.providers = data.thirdPartyAuth.providers || [];
                this.hasSecondaryProviders = (
                    data.thirdPartyAuth.secondaryProviders && data.thirdPartyAuth.secondaryProviders.length
                );
                this.currentProvider = data.thirdPartyAuth.currentProvider || '';
                this.syncLearnerProfileData = data.thirdPartyAuth.syncLearnerProfileData || false;
                this.skipRegistrationOptionalCheckboxes = data.thirdPartyAuth.skipRegistrationOptionalCheckboxes || false;
                this.errorMessage = data.thirdPartyAuth.errorMessage || '';
                this.platformName = data.platformName;
                this.autoSubmit = data.thirdPartyAuth.autoSubmitRegForm;
                this.hideAuthWarnings = data.hideAuthWarnings;
                this.autoRegisterWelcomeMessage = data.thirdPartyAuth.autoRegisterWelcomeMessage || '';
                this.registerFormSubmitButtonText = data.thirdPartyAuth.registerFormSubmitButtonText || _('Create Account');
                this.is_require_third_party_auth_enabled = data.is_require_third_party_auth_enabled;
                this.enableCoppaCompliance = data.enableCoppaCompliance;

                this.listenTo(this.model, 'sync', this.saveSuccess);
                this.listenTo(this.model, 'validation', this.renderLiveValidations);
            },

            renderFields: function(fields, className) {
                var html = [],
                    i,
                    fieldTpl = this.fieldTpl;

                html.push(HtmlUtils.joinHtml(
                    HtmlUtils.HTML('<div class="'),
                    className,
                    HtmlUtils.HTML('">')
                ));
                for (i = 0; i < fields.length; i++) {
                    html.push(HtmlUtils.template(fieldTpl)($.extend(fields[i], {
                        form: this.formType,
                        requiredStr: this.requiredStr,
                        optionalStr: fields[i].name === 'marketing_emails_opt_in' ? '' : this.optionalStr,
                        supplementalText: fields[i].supplementalText || '',
                        supplementalLink: fields[i].supplementalLink || '',
                        showRegisterLinks: this.showRegisterLinks
                    })));
                }
                html.push('</div>');
                return html;
            },

            buildForm: function(data) {
                var html = [],
                    i,
                    field,
                    len = data.length,
                    requiredFields = [],
                    optionalFields = [],
                    exposedOptionalFields = [];

                this.fields = data;

                this.hasOptionalFields = false;
                for (i = 0; i < len; i++) {
                    field = data[i];
                    if (field.errorMessages) {
                        // eslint-disable-next-line no-param-reassign
                        field.errorMessages = this.escapeStrings(field.errorMessages);
                    }

                    if (field.required) {
                        requiredFields.push(field);
                    } else {
                        if (field.type !== 'hidden') {
                            // For the purporse of displaying the optional field toggle,
                            // the form should be considered to have optional fields
                            // only if all of the optional fields are being rendering as
                            // input elements that are visible on the page.
                            this.hasOptionalFields = true;
                        }

                        if (field.exposed) {
                            exposedOptionalFields.push(field);
                        } else {
                            optionalFields.push(field);
                        }
                    }
                }

                html = this.renderFields(requiredFields, 'required-fields');

                // eslint-disable-next-line prefer-spread
                html.push.apply(html, this.renderFields(exposedOptionalFields, 'exposed-optional-fields'));
                // eslint-disable-next-line prefer-spread
                html.push.apply(html, this.renderFields(
                    optionalFields, `optional-fields ${!this.enableCoppaCompliance ? '' : 'full-length-fields'}`
                ));

                this.render(html.join(''));
            },

            render: function(html) {
                var fields = html || '',
                    formErrorsTitle = gettext('An error occurred.'),
                    renderHtml = _.template(this.tpl)({
                        /* We pass the context object to the template so that
                             * we can perform variable interpolation using sprintf
                             */
                        context: {
                            fields: fields,
                            currentProvider: this.currentProvider,
                            syncLearnerProfileData: this.syncLearnerProfileData,
                            skipRegistrationOptionalCheckboxes: this.skipRegistrationOptionalCheckboxes,
                            providers: this.providers,
                            hasSecondaryProviders: this.hasSecondaryProviders,
                            platformName: this.platformName,
                            autoRegisterWelcomeMessage: this.autoRegisterWelcomeMessage,
                            registerFormSubmitButtonText: this.registerFormSubmitButtonText,
                            is_require_third_party_auth_enabled: this.is_require_third_party_auth_enabled
                        }
                    });

                HtmlUtils.setHtml($(this.el), HtmlUtils.HTML(renderHtml));

                this.postRender();

                // Must be called after postRender, since postRender sets up $formFeedback.
                if (this.errorMessage) {
                    this.renderErrors(formErrorsTitle, [this.errorMessage]);
                } else if (this.currentProvider && !this.hideAuthWarnings) {
                    this.renderAuthWarning();
                }

                if (this.autoSubmit) {
                    $(this.el).hide();
                    $('#register-honor_code, #register-terms_of_service').prop('checked', true);
                    this.submitForm();
                }

                return this;
            },

            postRender: function() {
                var inputs = this.$('.form-field'),
                    inputSelectors = 'input, select, textarea',
                    inputTipSelectors = ['tip error', 'tip tip-input'],
                    inputTipSelectorsHidden = ['tip error hidden', 'tip tip-input hidden'],
                    onInputFocus = function() {
                        // Apply on focus styles to input
                        $(this).find('label').addClass('focus-in')
                            .removeClass('focus-out');

                        // Show each input tip
                        $(this).children().each(function() {
                            if (inputTipSelectorsHidden.indexOf($(this).attr('class')) >= 0) {
                                $(this).removeClass('hidden');
                            }
                        });
                    },
                    onInputFocusOut = function() {
                        // If input has no text apply focus out styles
                        if ($(this).find(inputSelectors).val().length === 0) {
                            $(this).find('label').addClass('focus-out')
                                .removeClass('focus-in');
                        }

                        // Hide each input tip
                        $(this).children().each(function() {
                            // This is a 1 instead of 0 so the error message for a field is not
                            // hidden on blur and only the help tip is hidden.
                            if (inputTipSelectors.indexOf($(this).attr('class')) >= 1) {
                                $(this).addClass('hidden');
                            }
                        });
                    },
                    handleInputBehavior = function(input) {
                        // Initially put label in input
                        if (input.find(inputSelectors).val().length === 0) {
                            input.find('label').addClass('focus-out')
                                .removeClass('focus-in');
                        }

                        // Initially hide each input tip
                        input.children().each(function() {
                            if (inputTipSelectors.indexOf($(this).attr('class')) >= 0) {
                                $(this).addClass('hidden');
                            }
                        });

                        input.focusin(onInputFocus);
                        input.focusout(onInputFocusOut);
                    },
                    handleAutocomplete = function() {
                        $(inputs).each(function() {
                            var $input = $(this),
                                isCheckbox = $input.attr('class').indexOf('checkbox') !== -1;

                            if (!isCheckbox) {
                                if ($input.find(inputSelectors).val().length === 0
                                        && !$input.is(':-webkit-autofill')) {
                                    $input.find('label').addClass('focus-out')
                                        .removeClass('focus-in');
                                } else {
                                    $input.find('label').addClass('focus-in')
                                        .removeClass('focus-out');
                                }
                            }
                        });
                    };

                FormView.prototype.postRender.call(this);
                $('.optional-fields').addClass('hidden');
                $('#toggle_optional_fields').change(function() {
                    window.analytics.track('edx.bi.user.register.optional_fields_selected');
                    $('.optional-fields').toggleClass('hidden');
                });

                // Since the honor TOS text has a composed css selector, it is more future proof
                // to insert the not toggled optional fields before .honor_tos_combined's parent
                // that is the container for the honor TOS text and checkbox.
                // xss-lint: disable=javascript-jquery-insert-into-target
                $('.exposed-optional-fields').insertBefore(
                    $('.honor_tos_combined').parent()
                );

                // We are swapping the order of these elements here because the honor code agreement
                // is a required checkbox field and the optional fields toggle is a cosmetic
                // improvement so that we don't have to show all the optional fields.
                // xss-lint: disable=javascript-jquery-insert-into-target
                $('.checkbox-optional_fields_toggle').insertAfter('.required-fields');
                if (!this.hasOptionalFields) {
                    $('.checkbox-optional_fields_toggle').addClass('hidden');
                }
                // xss-lint: disable=javascript-jquery-insert-into-target
                $('.checkbox-honor_code').insertAfter('.optional-fields');
                // xss-lint: disable=javascript-jquery-insert-into-target
                $('.checkbox-terms_of_service').insertAfter('.optional-fields');

                // Clicking on links inside a label should open that link.
                $('label a').click(function(ev) {
                    ev.stopPropagation();
                    ev.preventDefault();
                    window.open($(this).attr('href'), $(this).attr('target'), 'noopener');
                });
                $('.form-field').each(function() {
                    $(this).find('option:first').html('');
                });
                $(inputs).each(function() {
                    var $input = $(this),
                        isCheckbox = $input.attr('class').indexOf('checkbox') !== -1;
                    if ($input.length > 0 && !isCheckbox) {
                        handleInputBehavior($input);
                    }
                });
                $('#register-confirm_email').bind('cut copy paste', function(e) {
                    e.preventDefault();
                });
                setTimeout(handleAutocomplete, 1000);
            },

            hideRequiredMessageExceptOnError: function($el) {
                // We only handle blur if not in an error state.
                if (!$el.hasClass('error')) {
                    this.hideRequiredMessage($el);
                }
            },

            hideRequiredMessage: function($el) {
                this.doOnInputLabel($el, function($label) {
                    $label.addClass('hidden');
                });
            },

            doOnInputLabel: function($el, action) {
                var $label = this.getRequiredTextLabel($el);
                action($label);
            },

            handleRequiredInputFocus: function(event) {
                var $el = $(event.currentTarget);
                // Avoid rendering for required checkboxes.
                if ($el.attr('type') !== 'checkbox') {
                    this.renderRequiredMessage($el);
                }
                if ($el.hasClass('error')) {
                    this.doOnInputLabel($el, function($label) {
                        $label.addClass('error');
                    });
                }
            },

            renderRequiredMessage: function($el) {
                this.doOnInputLabel($el, function($label) {
                    $label.removeClass('hidden').text(gettext('(required)'));
                });
            },

            getRequiredTextLabel: function($el) {
                return $('#' + $el.attr('id') + '-required-label');
            },

            renderLiveValidations: function($el, decisions) {
                var $label = this.getLabel($el),
                    $requiredTextLabel = this.getRequiredTextLabel($el),
                    $icon = this.getIcon($el),
                    $errorTip = this.getErrorTip($el),
                    name = $el.attr('name'),
                    type = $el.attr('type'),
                    isCheckbox = type === 'checkbox',
                    hasError = decisions.validation_decisions[name] !== '',
                    error = isCheckbox ? '' : decisions.validation_decisions[name];

                if (hasError && this.negativeValidationEnabled) {
                    this.addValidationErrorMsgForScreenReader($el);
                    this.renderLiveValidationError($el, $label, $requiredTextLabel, $icon, $errorTip, error);
                } else if (this.positiveValidationEnabled) {
                    this.removeValidationErrorMsgForScreenReader($el);
                    this.renderLiveValidationSuccess($el, $label, $requiredTextLabel, $icon, $errorTip);
                }
            },

            getLabel: function($el) {
                return this.$form.find('label[for=' + $el.attr('id') + ']');
            },

            getIcon: function($el) {
                return $('#' + $el.attr('id') + '-validation-icon');
            },

            addValidationErrorMsgForScreenReader: function($el) {
                var $validation_node = this.$form.find('#' + $el.attr('id') + '-validation-error');
                $validation_node.find('.sr-only').text('ERROR:');
            },

            removeValidationErrorMsgForScreenReader: function($el) {
                var $validation_node = this.$form.find('#' + $el.attr('id') + '-validation-error');
                $validation_node.find('.sr-only').text('');
            },

            getErrorTip: function($el) {
                return $('#' + $el.attr('id') + '-validation-error-msg');
            },

            getFieldTimeout: function($el) {
                return $('#' + $el.attr('id')).attr('timeout-id') || null;
            },

            setFieldTimeout: function($el, time, action) {
                $el.attr('timeout-id', setTimeout(action, time));
            },

            clearFieldTimeout: function($el) {
                var timeout = this.getFieldTimeout($el);
                if (timeout) {
                    clearTimeout(this.getFieldTimeout($el));
                    $el.removeAttr('timeout-id');
                }
            },

            renderLiveValidationError: function($el, $label, $req, $icon, $tip, error) {
                this.removeLiveValidationIndicators(
                    $el, $label, $req, $icon,
                    'success', this.positiveValidationIcon
                );
                this.addLiveValidationIndicators(
                    $el, $label, $req, $icon, $tip,
                    'error', this.negativeValidationIcon, error
                );
                this.renderRequiredMessage($el);
            },

            renderLiveValidationSuccess: function($el, $label, $req, $icon, $tip) {
                var self = this,
                    validationFadeTime = this.successfulValidationDisplaySeconds * 1000;
                this.removeLiveValidationIndicators(
                    $el, $label, $req, $icon,
                    'error', this.negativeValidationIcon
                );
                this.addLiveValidationIndicators(
                    $el, $label, $req, $icon, $tip,
                    'success', this.positiveValidationIcon, ''
                );
                this.hideRequiredMessage($el);

                // Hide success indicators after some time.
                this.clearFieldTimeout($el);
                this.setFieldTimeout($el, validationFadeTime, function() {
                    self.removeLiveValidationIndicators(
                        $el, $label, $req, $icon,
                        'success', self.positiveValidationIcon
                    );
                    self.clearFieldTimeout($el);
                });
            },

            addLiveValidationIndicators: function($el, $label, $req, $icon, $tip, indicator, icon, msg) {
                $el.addClass(indicator);
                $label.addClass(indicator);
                $req.addClass(indicator);
                $icon.addClass(indicator + ' ' + icon);
                if (['username', 'email'].indexOf($el.attr('name')) > -1) {
                    $tip.addClass(' data-hj-suppress');
                }
                $tip.text(msg);
            },

            removeLiveValidationIndicators: function($el, $label, $req, $icon, indicator, icon) {
                $el.removeClass(indicator);
                $label.removeClass(indicator);
                $req.removeClass(indicator);
                $icon.removeClass(indicator + ' ' + icon);
            },

            thirdPartyAuth: function(event) {
                var providerUrl = $(event.currentTarget).data('provider-url') || '';

                if (providerUrl) {
                    window.location.href = providerUrl;
                }
            },

            saveSuccess: function() {
                this.trigger('auth-complete');
            },

            saveError: function(error) {
                $(this.el).show(); // Show in case the form was hidden for auto-submission
                this.errors = _.flatten(
                    _.map(
                        // Something is passing this 'undefined'. Protect against this.
                        JSON.parse(error.responseText || '[]'),
                        function(errorList, key) {
                            if (key === 'error_code') {
                                return null;
                            } else {
                                return _.map(
                                    errorList,
                                    function(errorItem) {
                                        if (errorItem.user_message) {
                                            return StringUtils.interpolate('<li {suppressAttr} >{error}</li>', {
                                                error: errorItem.user_message,
                                                suppressAttr: (
                                                    key === 'email' || key === 'username'
                                                ) ? 'data-hj-suppress' : ''
                                            });
                                        }
                                    }
                                );
                            }
                        }
                    )
                );
                this.renderErrors(this.defaultFormErrorsTitle, this.errors);
                this.scrollToFormFeedback();
                this.toggleDisableButton(false);
            },

            postFormSubmission: function() {
                if (_.compact(this.errors).length) {
                    // The form did not get submitted due to validation errors.
                    $(this.el).show(); // Show in case the form was hidden for auto-submission
                }
            },

            resetValidationVariables: function() {
                this.positiveValidationEnabled = true;
                this.negativeValidationEnabled = true;
            },

            renderAuthWarning: function() {
                var msgPart1 = gettext('You\'ve successfully signed into %(currentProvider)s.'),
                    msgPart2 = gettext(
                        'We just need a little more information before you start learning with %(platformName)s.'
                    ),
                    fullMsg = _.sprintf(
                        msgPart1 + ' ' + msgPart2,
                        {currentProvider: this.currentProvider, platformName: this.platformName}
                    );

                this.renderFormFeedback(this.formStatusTpl, {
                    jsHook: this.authWarningJsHook,
                    message: fullMsg
                });
                $(this.formFields).removeClass('hidden');
            },

            submitForm: function(event) { // eslint-disable-line no-unused-vars
                var elements = this.$form[0].elements,
                    $el,
                    i;

                // As per requirements, disable positive validation for submission.
                this.positiveValidationEnabled = false;

                for (i = 0; i < elements.length; i++) {
                    $el = $(elements[i]);

                    // Simulate live validation.
                    if ($el.attr('required')) {
                        $el.blur();
                    }
                }

                FormView.prototype.submitForm.apply(this, arguments);
            },

            getFormData: function() {
                var obj = FormView.prototype.getFormData.apply(this, arguments),
                    $emailElement = this.$form.find('input[name=email]'),
                    $confirmEmail = this.$form.find('input[name=confirm_email]');

                if ($confirmEmail.length) {
                    if (!$confirmEmail.val() || ($emailElement.val() !== $confirmEmail.val())) {
                        this.errors.push(StringUtils.interpolate('<li>{error}</li>', {
                            error: $confirmEmail.data('errormsg-required')
                        }));
                    }
                    obj.confirm_email = $confirmEmail.val();
                }

                return obj;
            },

            liveValidateHandler: function(event) {
                var $el = $(event.currentTarget);
                // Until we get a back-end that can handle all available
                // registration fields, we do some generic validation here.
                if (this.inLiveValidationFields($el)) {
                    if ($el.attr('type') === 'checkbox') {
                        this.liveValidateCheckbox($el);
                    } else {
                        this.liveValidate($el);
                    }
                } else {
                    this.genericLiveValidateHandler($el);
                }
                // On blur, we do exactly as the function name says, no matter which input.
                this.hideRequiredMessageExceptOnError($el);
            },

            liveValidate: function($el) {
                var data = {},
                    field,
                    i;
                for (i = 0; i < this.liveValidationFields.length; ++i) {
                    field = this.liveValidationFields[i];
                    data[field] = $('#register-' + field).val();
                }
                FormView.prototype.liveValidate(
                    $el, this.validationUrl, 'json', data, 'POST', this.model
                );
            },

            liveValidateCheckbox: function($checkbox) {
                var validationDecisions = {validation_decisions: {}},
                    decisions = validationDecisions.validation_decisions,
                    name = $checkbox.attr('name'),
                    checked = $checkbox.is(':checked'),
                    error = $checkbox.data('errormsg-required');
                decisions[name] = checked ? '' : error;
                this.renderLiveValidations($checkbox, validationDecisions);
            },

            genericLiveValidateHandler: function($el) {
                var elementType = $el.attr('type');
                if (elementType === 'checkbox') {
                    // We are already validating checkboxes in a generic way.
                    this.liveValidateCheckbox($el);
                } else {
                    this.genericLiveValidate($el);
                }
            },

            genericLiveValidate: function($el) {
                var validationDecisions = {validation_decisions: {}},
                    decisions = validationDecisions.validation_decisions,
                    name = $el.attr('name'),
                    error = $el.data('errormsg-required');
                decisions[name] = $el.val() ? '' : error;
                this.renderLiveValidations($el, validationDecisions);
            }
        });
    });
}).call(this, define || RequireJS.define);
