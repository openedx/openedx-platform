""" Factories for generating fake commerce-related data. """


import factory
from factory.fuzzy import FuzzyText


class OrderFactory(factory.Factory):
    """ Factory for stubbing orders resources from Ecommerce (v2). """
    class Meta:
        model = dict

    number = factory.Sequence(lambda n: 'edx-%d' % n)  # noqa: UP031
    date_placed = '2016-01-01T10:00:00Z'
    status = 'Complete'
    currency = 'USD'
    total_excl_tax = '100.00'
    lines = []


class OrderLineFactory(factory.Factory):
    """ Factory for stubbing order lines resources from Ecommerce (v2). """
    class Meta:
        model = dict

    title = FuzzyText(prefix='Seat in ')
    quantity = 1
    description = FuzzyText()
    status = 'Complete'
    line_price_excl_tax = '100.00'
    unit_price_excl_tax = '100.00'
    product = {}


class ProductFactory(factory.Factory):
    """ Factory for stubbing Product resources from Ecommerce (v2). """
    class Meta:
        model = dict

    id = factory.Sequence(lambda n: n)  # pylint: disable=invalid-name
    url = 'http://test/api/v2/products/' + str(id)
    product_class = 'Seat'
    title = FuzzyText(prefix='Seat in ')
    price = '100.00'
    attribute_values = []


class ProductAttributeFactory(factory.Factory):
    """ Factory for stubbing product attribute resources from
    Ecommerce (v2).
    """
    class Meta:
        model = dict

    name = FuzzyText()
    code = FuzzyText()
    value = FuzzyText()
