define(['sinon', 'common/js/spec_helpers/template_helpers',
    'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
    'js/dashboard/donation'],
function(sinon, TemplateHelpers, AjaxHelpers) {
    'use strict';

    describe('edx.dashboard.donation.DonationView', function() {
        var PAYMENT_URL = 'https://fake.processor.com/pay/';
        var PAYMENT_PARAMS = {
            orderId: 'test-order',
            signature: 'abcd1234'
        };
        var AMOUNT = '45.67';
        var COURSE_ID = 'edx/DemoX/Demo';

        var view = null;
        var requests = null;
        var xhrFactory = null;

        beforeEach(function() {
            setFixtures('<div></div>');
            TemplateHelpers.installTemplate('templates/dashboard/donation');

            view = new edx.dashboard.donation.DonationView({
                el: $('#jasmine-fixtures'),
                course: COURSE_ID
            }).render();

            // Stub out the actual submission of the payment form
            // (which would cause the page to reload)
            // This function gets passed the dynamically constructed
            // form with signed payment parameters from the LMS server,
            // so we can verify that the form is constructed correctly.
            spyOn(view, 'submitPaymentForm').and.callFake(function() {});

            // Stub the analytics event tracker
            window.analytics = jasmine.createSpyObj('analytics', ['track']);
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
        });

        afterEach(function() {
            requests.restore();
        });

        it('processes a donation for a course', function() {
            // Enter a donation amount and proceed to the payment page
            view.$amount.val(AMOUNT);
            view.donate();

            // Verify that the client contacts the server to create
            // the donation item in the shopping cart and receive
            // the signed payment params.
            AjaxHelpers.expectRequest(
                requests, 'POST', '/shoppingcart/donation/',
                $.param({amount: AMOUNT, course_id: COURSE_ID})
            );

            // Simulate a response from the server containing the signed
            // parameters to send to the payment processor
            AjaxHelpers.respondWithJson(requests, {
                payment_url: PAYMENT_URL,
                payment_params: PAYMENT_PARAMS
            });

            // Verify that the payment form has the payment parameters
            // sent by the LMS server, and that it's targeted at the
            // correct payment URL.
            // We stub out the actual submission of the form to avoid
            // leaving the current page during the test.
            expect(view.submitPaymentForm).toHaveBeenCalled();
            var form = view.submitPaymentForm.calls.mostRecent().args[0];
            expect(form.serialize()).toEqual($.param(PAYMENT_PARAMS));
            expect(form.attr('method')).toEqual('post');
            expect(form.attr('action')).toEqual(PAYMENT_URL);
        });

        it('validates the donation amount', function() {
            var assertValidAmount = function(amount, isValid) {
                expect(view.validateAmount(amount)).toBe(isValid);
            };
            assertValidAmount('', false);
            assertValidAmount('  ', false);
            assertValidAmount('abc', false);
            assertValidAmount('14.', false);
            assertValidAmount('.1', false);
            assertValidAmount('-1', false);
            assertValidAmount('-1.00', false);
            assertValidAmount('-', false);
            assertValidAmount('0', false);
            assertValidAmount('0.00', false);
            assertValidAmount('00.00', false);
            assertValidAmount('3', true);
            assertValidAmount('12.34', true);
            assertValidAmount('278', true);
            assertValidAmount('278.91', true);
            assertValidAmount('0.14', true);
        });

        it('displays validation errors', function() {
            // Attempt to submit an invalid donation amount
            view.$amount.val('');
            view.donate();

            // Verify that the amount field is marked as having a validation error
            expect(view.$amount).toHaveClass('validation-error');

            // Verify that the error message appears
            expect(view.$errorMsg.text()).toEqual('Please enter a valid donation amount.');

            // Expect that the submit button is re-enabled to allow users to submit again
            expect(view.$submit).not.toHaveClass('disabled');

            // Try again, this time submitting a valid amount
            view.$amount.val(AMOUNT);
            view.donate();

            // Expect that the errors are cleared
            expect(view.$errorMsg.text()).toEqual('');

            // Expect that the submit button is disabled
            expect(view.$submit).toHaveClass('disabled');
        });

        it('displays an error when the server cannot be contacted', function() {
            // Simulate an error from the LMS servers
            view.donate();
            AjaxHelpers.respondWithError(requests);

            // Expect that the error is displayed
            expect(view.$errorMsg.text()).toEqual('Your donation could not be submitted.');

            // Verify that the submit button is re-enabled
            // so users can try again.
            expect(view.$submit).not.toHaveClass('disabled');
        });

        it('disables the submit button once the user donates', function() {
            // Before we submit, the button should be enabled
            expect(view.$submit).not.toHaveClass('disabled');

            // Simulate starting a donation
            // Since we're not simulating the AJAX response, this will block
            // in the state just after the user kicks off the donation process.
            view.donate();

            // Verify that the submit button is disabled
            expect(view.$submit).toHaveClass('disabled');
        });

        it('sends an analytics event when the user submits a donation', function() {
            // Simulate the submission to the payment processor
            // We skip the intermediary steps here by passing in
            // the payment url and parameters,
            // which the view would ordinarily retrieve from the LMS server.
            view.startPayment({
                payment_url: PAYMENT_URL,
                payment_params: PAYMENT_PARAMS
            });

            // Verify that the analytics event was fired
            expect(window.analytics.track).toHaveBeenCalledWith(
                'edx.bi.user.payment_processor.visited',
                {
                    category: 'donations',
                    label: COURSE_ID
                }
            );
        });
    });
}
);
