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

    var checkPaymentButtons,
        expectPaymentSubmitted,
        goToPayment,
        expectPaymentDisabledBecauseInactive,
        expectPaymentButtonEnabled,
        expectPriceSelected,
        createView,
        SERVER_ERROR_MSG = 'An error occurred!';

    describe('edx.verify_student.MakePaymentStepView', function() {
        var requests, xhrFactory;
        var STEP_DATA = {
            minPrice: '12',
            currency: 'usd',
            processors: ['test-payment-processor'],
            courseKey: 'edx/test/test',
            courseModeSlug: 'verified',
            isABTesting: true
        };

        createView = function(stepDataOverrides) {
            var view = new MakePaymentStepView({
                el: $('#current-step-container'),
                stepData: _.extend(_.clone(STEP_DATA), stepDataOverrides),
                errorModel: new (Backbone.Model.extend({}))()
            }).render();

            // Stub the payment form submission
            spyOn(view, 'submitForm').and.callFake(function() {});
            return view;
        };

        expectPriceSelected = function(price) {
            var $sel = $('input[name="contribution"]');

            // check that contribution value is same as price given
            expect($sel.length).toEqual(1);
            expect($sel.val()).toEqual(price);
        };

        expectPaymentButtonEnabled = function(isEnabled) {
            var $el = $('.payment-button'),
                appearsDisabled = $el.hasClass('is-disabled'),
                isDisabled = $el.prop('disabled');

            expect(appearsDisabled).not.toEqual(isEnabled);
            expect(isDisabled).not.toEqual(isEnabled);
        };

        expectPaymentDisabledBecauseInactive = function() {
            var $payButton = $('.payment-button');

            // Payment button should be hidden
            expect($payButton.length).toEqual(0);
        };

        goToPayment = function(requests, kwargs) {
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
                AjaxHelpers.respondWithJson(
                    requests, {payment_page_url: 'http://payment-page-url/', payment_form_data: {foo: 'bar'}}
                );
            } else {
                AjaxHelpers.respondWithTextError(requests, 400, SERVER_ERROR_MSG);
            }
        };

        expectPaymentSubmitted = function(view, params) {
            var form;

            expect(view.submitForm).toHaveBeenCalled();
            form = view.submitForm.calls.mostRecent().args[0];

            expect(form.serialize()).toEqual($.param(params));
            expect(form.attr('method')).toEqual('POST');
            expect(form.attr('action')).toEqual('http://payment-page-url/');
        };

        checkPaymentButtons = function(requests, buttons) {
            var $el = $('.payment-button');
            expect($el.length).toEqual(_.size(buttons));
            _.each(buttons, function(expectedText, expectedId) {
                var $buttonEl = $('#' + expectedId),
                    request;

                $buttonEl.removeAttr('disabled');
                expect($buttonEl.length).toEqual(1);
                expect($buttonEl[0]).toHaveClass('payment-button');
                expect($buttonEl[0]).toHaveText(expectedText);
                expect($buttonEl[0]).toHaveClass('action-primary-blue');

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
            TemplateHelpers.installTemplate('templates/verify_student/make_payment_step_ab_testing');
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
        });

        afterEach(function() {
            requests.restore();
        });

        it('A/B Testing: check Initialize method with AB testing enable ', function() {
            var view = createView();
            expect(view.templateName).toEqual('make_payment_step_ab_testing');
            expect(view.btnClass).toEqual('action-primary-blue');
        });

        it('shows users only minimum price', function() {
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

        it('A/B Testing: provides working payment buttons for a single processor', function() {
            createView({processors: ['cybersource']});
            checkPaymentButtons(requests, {cybersource: 'Checkout'});
        });

        it('A/B Testing: provides working payment buttons for multiple processors', function() {
            createView({processors: ['cybersource', 'paypal', 'other']});
            checkPaymentButtons(requests, {
                cybersource: 'Checkout',
                paypal: 'Checkout with PayPal',
                other: 'Checkout with other'
            });
        });

        it('A/B Testing: by default minimum price is selected if no suggested prices are given', function() {
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

        it('A/B Testing: min price is always selected even if contribution amount is provided', function() {
            // Pre-select a price NOT in the suggestions
            createView({
                contributionAmount: '99.99'
            });

            // Expect that the price is filled in
            expectPriceSelected(STEP_DATA.minPrice);
        });

        it('A/B Testing: disables payment for inactive users', function() {
            createView({isActive: false});
            expectPaymentDisabledBecauseInactive();
        });

        it('A/B Testing: displays an error if the order could not be created', function() {
            var view = createView();

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
    });
}
);
