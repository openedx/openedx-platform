define(['sinon', 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers', 'js/shoppingcart/shoppingcart'],
    function(sinon, AjaxHelpers) {
        'use strict';

        describe('edx.shoppingcart.showcart.CartView', function() {
            var view = null;
            var requests = null;
            var xhrFactory = null;

            beforeEach(function() {
                setFixtures('<section class="wrapper confirm-enrollment shopping-cart cart-view"><form action="" method="post"><input type="hidden" name="" value="" /><span class="icon fa fa-caret-right"></span><input type="submit" value="Payment"/></form></section>'); // eslint-disable-line max-len

                view = new edx.shoppingcart.showcart.CartView({
                    el: $('.confirm-enrollment.cart-view form')
                });

                spyOn(view, 'responseFromServer').and.callFake(function() {});

                xhrFactory = sinon.useFakeXMLHttpRequest();
                requests = [];
                requests.currentIndex = 0;
                requests.restore = function() { xhrFactory.restore(); };
                xhrFactory.onCreate = function(req) { requests.push(req); };

                view.submit();

                // Verify that the client contacts the server to
                // check for all th valid cart items
                AjaxHelpers.expectRequest(
                    requests, 'GET', '/shoppingcart/verify_cart/'
                );
            });

            afterEach(function() {
                requests.restore();
            });

            it('cart has invalid items, course enrollment has been closed', function() {
                // Simulate a response from the server containing the
                // parameter 'is_course_enrollment_closed'. This decides that
                // do we have all the cart items valid in the cart or not
                AjaxHelpers.respondWithJson(requests, {
                    is_course_enrollment_closed: true
                });

                expect(view.responseFromServer).toHaveBeenCalled();
                var data = view.responseFromServer.calls.mostRecent().args[0];
                expect(data.is_course_enrollment_closed).toBe(true);
            });

            it('cart has all valid items, course enrollment is still open', function() {
                // Simulate a response from the server containing the
                // parameter 'is_course_enrollment_closed'. This decides that
                // do we have all the cart items valid in the cart or not
                AjaxHelpers.respondWithJson(requests, {
                    is_course_enrollment_closed: false
                });

                expect(view.responseFromServer).toHaveBeenCalled();
                var data = view.responseFromServer.calls.mostRecent().args[0];
                expect(data.is_course_enrollment_closed).toBe(false);
            });
        });
    }
);
