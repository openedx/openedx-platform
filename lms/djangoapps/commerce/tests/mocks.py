""" Commerce app tests package. """
# pylint: disable=invalid-name


import json

import responses
from django.conf import settings

from . import factories


class mock_ecommerce_api_endpoint:
    """
    Base class for contextmanagers used to mock calls to api endpoints.

    The contextmanager registers its response with ``responses`` on entry and
    checks on exit whether the endpoint was called, so the test (or its setUp)
    must already have ``responses`` active, e.g. via ``@responses.activate``
    or ``responses.start()``.
    """

    # override this in subclasses.
    default_response = None

    # override this in subclasses, using one of responses' method constants
    method = None

    host = settings.ECOMMERCE_API_URL.strip('/')

    def __init__(self, response=None, status=200, expect_called=True, exception=None):
        """
        Keyword Arguments:
            response: a JSON-serializable Python type representing the desired response body.
            status: desired HTTP status for the response.
            expect_called: a boolean indicating whether an API request was expected; set
                to False if we should ensure that no request arrived, or None to skip checking
                if the request arrived
            exception: raise this exception instead of returning an HTTP response when called.
        """
        self.response = response or self.default_response
        self.status = status
        self.expect_called = expect_called
        self.exception = exception
        self._calls_on_enter = 0

    def get_uri(self):
        """
        Returns the uri to register with responses for this contextmanager.
        """
        return self.host + '/' + self.get_path().lstrip('/')

    def get_path(self):
        """
        Returns the path of the URI to register with responses for this contextmanager.

        Subclasses must override this method.

        Returns:
            str
        """
        raise NotImplementedError

    def _matching_calls(self):
        """Count the requests made so far to this contextmanager's endpoint."""
        uri = self.get_uri()
        return sum(
            1 for call in responses.calls
            if call.request.method == self.method and call.request.url.split('?', 1)[0] == uri
        )

    def __enter__(self):
        self._calls_on_enter = self._matching_calls()
        if self.exception is not None:
            # responses raises an Exception instance given as the body instead of returning a response.
            body = self.exception
        else:
            body = json.dumps(self.response)
        responses.add(
            self.method,
            self.get_uri(),
            status=self.status,
            body=body,
            content_type='application/json',
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.expect_called is None:
            return
        called = self._matching_calls() > self._calls_on_enter
        assert called == self.expect_called


class mock_basket_order(mock_ecommerce_api_endpoint):
    """ Mocks calls to E-Commerce API client basket order method. """

    default_response = {'number': 1}
    method = responses.GET

    def __init__(self, basket_id, **kwargs):
        super().__init__(**kwargs)
        self.basket_id = basket_id

    def get_path(self):
        return f'/baskets/{self.basket_id}/order/'


class mock_create_refund(mock_ecommerce_api_endpoint):
    """ Mocks calls to E-Commerce API client refund creation method. """

    default_response = []
    method = responses.POST

    def get_path(self):
        return '/refunds/'


class mock_payment_processors(mock_ecommerce_api_endpoint):
    """
    Mocks calls to E-Commerce API payment processors method.
    """

    default_response = ['foo', 'bar']
    method = responses.GET

    def get_path(self):
        return "/payment/processors/"


class mock_process_refund(mock_ecommerce_api_endpoint):
    """ Mocks calls to E-Commerce API client refund process method. """

    default_response = []
    method = responses.PUT

    def __init__(self, refund_id, **kwargs):
        super().__init__(**kwargs)
        self.refund_id = refund_id

    def get_path(self):
        return f'/refunds/{self.refund_id}/process/'


class mock_order_endpoint(mock_ecommerce_api_endpoint):
    """ Mocks calls to E-Commerce API client basket order method. """

    default_response = {'number': 'EDX-100001'}
    method = responses.GET

    def __init__(self, order_number, **kwargs):
        super().__init__(**kwargs)
        self.order_number = order_number

    def get_path(self):
        return f'/orders/{self.order_number}/'


class mock_get_orders(mock_ecommerce_api_endpoint):
    """ Mocks calls to E-Commerce API client order get method. """

    default_response = {
        'results': [
            factories.OrderFactory(
                lines=[
                    factories.OrderLineFactory(
                        product=factories.ProductFactory(attribute_values=[factories.ProductAttributeFactory(
                            name='certificate_type',
                            value='verified'
                        )])
                    )
                ]
            ),
            factories.OrderFactory(
                lines=[
                    factories.OrderLineFactory(
                        product=factories.ProductFactory(attribute_values=[factories.ProductAttributeFactory(
                            name='certificate_type',
                            value='verified'
                        )])
                    ),
                    factories.OrderLineFactory(
                        product=factories.ProductFactory(attribute_values=[factories.ProductAttributeFactory(
                            name='certificate_type',
                            value='verified'
                        )])
                    ),
                ]
            ),
            factories.OrderFactory(
                lines=[
                    factories.OrderLineFactory(product=factories.ProductFactory(product_class='Coupon'))
                ]
            ),
        ]
    }
    method = responses.GET

    def get_path(self):
        return '/orders/'
