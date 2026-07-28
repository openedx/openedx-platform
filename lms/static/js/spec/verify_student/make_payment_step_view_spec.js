define([
    'jquery',
    'underscore',
    'backbone',
    'sinon',
    'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
    'common/js/spec_helpers/template_helpers',
    'js/verify_student/views/make_payment_step_view'
],
function($, _, Backbone, sinon, AjaxHelpers, TemplateHelpers, MakePaymentStepView) {
    'use strict';

    describe('edx.verify_student.MakePaymentStepView', function() {
        var requests, xhrFactory;
        var PAYMENT_PARAMS = {
            orderId: 'test-order',
            signature: 'abcd1234'
        };

        var STEP_DATA = {
            minPrice: '12',
            currency: 'usd',
            processors: ['test-payment-processor'],
            courseKey: 'edx/test/test',
            courseModeSlug: 'verified'
        };

        var SERVER_ERROR_MSG = 'An error occurred!';

        var createView = function(stepDataOverrides) {
            var view = new MakePaymentStepView({
                el: $('#current-step-container'),
                stepData: _.extend(_.clone(STEP_DATA), stepDataOverrides),
                errorModel: new (Backbone.Model.extend({}))()
            }).render();

            // Stub the payment form submission
            spyOn(view, 'submitForm').and.callFake(function() {});
            return view;
        };

        var expectPriceSelected = function(price) {
            var $sel = $('input[name="contribution"]');

            // check that contribution value is same as price given
            expect($sel.length).toEqual(1);
            expect($sel.val()).toEqual(price);
        };

        var expectPaymentButtonEnabled = function(isEnabled) {
            var $el = $('.payment-button'),
                appearsDisabled = $el.hasClass('is-disabled'),
                isDisabled = $el.prop('disabled');

            expect(!appearsDisabled).toEqual(isEnabled);
            expect(!isDisabled).toEqual(isEnabled);
        };

        var expectPaymentDisabledBecauseInactive = function() {
            var $payButton = $('.payment-button');

            // Payment button should be hidden
            expect($payButton.length).toEqual(0);
        };

        var goToPayment = function(requests, kwargs) {
            var params = {
                contribution: kwargs.amount || '',
                course_id: kwargs.courseId || '',
                processor: kwargs.processor || '',
                sku: kwargs.sku || ''
            };

            // Click the "go to payment" button
            $('.payment-button').click();

            // Verify that the request was made to the server
            AjaxHelpers.expectPostRequest(
                requests, '/verify_student/create_order/', $.param(params)
            );

            // Simulate the server response
            if (kwargs.succeeds) {
                // TODO put fixture responses in the right place
                AjaxHelpers.respondWithJson(requests, {payment_page_url: 'http://payment-page-url/', payment_form_data: {foo: 'bar'}});
            } else {
                AjaxHelpers.respondWithTextError(requests, 400, SERVER_ERROR_MSG);
            }
        };

        var expectPaymentSubmitted = function(view, params) {
            var form;

            expect(view.submitForm).toHaveBeenCalled();
            form = view.submitForm.calls.mostRecent().args[0];

            expect(form.serialize()).toEqual($.param(params));
            expect(form.attr('method')).toEqual('POST');
            expect(form.attr('action')).toEqual('http://payment-page-url/');
        };

        var checkPaymentButtons = function(requests, buttons) {
            var $el = $('.payment-button');
            expect($el.length).toEqual(_.size(buttons));
            _.each(buttons, function(expectedText, expectedId) {
                var $buttonEl = $('#' + expectedId),
                    request;

                $buttonEl.removeAttr('disabled');
                expect($buttonEl.length).toEqual(1);
                expect($buttonEl[0]).toHaveClass('payment-button');
                expect($buttonEl[0]).toHaveClass('action-primary');
                expect($buttonEl[0]).toHaveText(expectedText);

                $buttonEl[0].click();
                expect($buttonEl[0]).toHaveClass('is-selected');
                expectPaymentButtonEnabled(false);
                request = AjaxHelpers.currentRequest(requests);
                expect(request.requestBody.split('&')).toContain('processor=' + expectedId);
                AjaxHelpers.respondWithJson(requests, {});
            });
        };

        beforeEach(function() {
            window.analytics = jasmine.createSpyObj('analytics', ['track', 'page', 'trackLink']);

            setFixtures('<div id="current-step-container"></div>');
            TemplateHelpers.installTemplate('templates/verify_student/make_payment_step');
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
        });

        afterEach(function() {
            requests.restore();
        });

        it('shows users only minimum price', function() {
            var view = createView({});

            expectPriceSelected(STEP_DATA.minPrice);
            expectPaymentButtonEnabled(true);
            goToPayment(requests, {
                amount: STEP_DATA.minPrice,
                courseId: STEP_DATA.courseKey,
                processor: STEP_DATA.processors[0],
                succeeds: true
            });
            expectPaymentSubmitted(view, {foo: 'bar'});
        });

        it('view containing verification msg when verification deadline is set and user is active', function() {
            var verificationDeadline = '2016-08-14 23:59:00+00:00';
            createView({
                userEmail: 'test@example.com',
                userTimezone: 'PDT',
                userLanguage: 'es-ES',
                requirements: {
                    isVisible: true
                },
                verificationDeadline: verificationDeadline,
                isActive: true
            });
            // Verify user does not get user activation message when he is already activated.
            expect($('p.instruction-info:contains("test@example.com")').length).toEqual(0);
            // Verify user gets verification message.
            expect($('p.localized-datetime').attr('data-string')).toEqual(
                'You can pay now even if you don\'t have the following items available,'
                    + ' but you will need to have these by {date} to qualify to earn a Verified Certificate.'
            );
            expect($('p.localized-datetime').attr('data-timezone')).toEqual('PDT');
            expect($('p.localized-datetime').attr('data-language')).toEqual('es-ES');
        });

        it('view containing user email when verification deadline is set and user is not active', function() {
            createView({
                userEmail: 'test@example.com',
                requirements: {
                    isVisible: true
                },
                verificationDeadline: true,
                isActive: false
            });
            // Verify un-activated user gets activation message.
            expect($('p.instruction-info:contains("test@example.com")').length).toEqual(1);
        });

        it('view containing user email', function() {
            createView({userEmail: 'test@example.com', requirements: {isVisible: true}, isActive: false});
            expect($('p.instruction-info:contains("test@example.com")').length).toEqual(1);
        });

        it('provides working payment buttons for a single processor', function() {
            createView({processors: ['cybersource']});
            checkPaymentButtons(requests, {cybersource: 'Checkout'});
        });

        it('provides working payment buttons for multiple processors', function() {
            createView({processors: ['cybersource', 'paypal', 'other']});
            checkPaymentButtons(requests, {
                cybersource: 'Checkout',
                paypal: 'Checkout with PayPal',
                other: 'Checkout with other'
            });
        });

        it('by default minimum price is selected if no suggested prices are given', function() {
            var view = createView();

            expectPriceSelected(STEP_DATA.minPrice);
            expectPaymentButtonEnabled(true);

            goToPayment(requests, {
                amount: STEP_DATA.minPrice,
                courseId: STEP_DATA.courseKey,
                processor: STEP_DATA.processors[0],
                succeeds: true
            });
            expectPaymentSubmitted(view, {foo: 'bar'});
        });

        it('min price is always selected even if contribution amount is provided', function() {
            // Pre-select a price NOT in the suggestions
            createView({
                contributionAmount: '99.99'
            });

            // Expect that the price is filled in
            expectPriceSelected(STEP_DATA.minPrice);
        });

        it('disables payment for inactive users', function() {
            createView({isActive: false});
            expectPaymentDisabledBecauseInactive();
        });

        it('displays an error if the order could not be created', function() {
            var view = createView({});

            goToPayment(requests, {
                amount: STEP_DATA.minPrice,
                courseId: STEP_DATA.courseKey,
                processor: STEP_DATA.processors[0],
                succeeds: false
            });

            // Expect that an error is displayed
            expect(view.errorModel.get('shown')).toBe(true);
            expect(view.errorModel.get('errorTitle')).toEqual('Could not submit order');
            expect(view.errorModel.get('errorMsg')).toEqual(SERVER_ERROR_MSG);

            // Expect that the payment button is re-enabled
            expectPaymentButtonEnabled(true);
        });

        it('displays an error if no payment processors are available', function() {
            var view = createView({processors: []});
            expect(view.errorModel.get('shown')).toBe(true);
            expect(view.errorModel.get('errorTitle')).toEqual(
                'All payment options are currently unavailable.'
            );
            expect(view.errorModel.get('errorMsg')).toEqual(
                'Try the transaction again in a few minutes.'
            );
        });
        it('check Initialize method without AB testing ', function() {
            var view = createView();
            expect(view.templateName).toEqual('make_payment_step');
            expect(view.btnClass).toEqual('action-primary');
        });
    });
}
);
