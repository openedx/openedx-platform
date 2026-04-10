""" Tests for custom DRF fields. """

import ddt
from django.test import TestCase

from openedx.core.lib.api.fields import AbsoluteURLField


class MockRequest:
    """ Mock request object. """
    ROOT = 'http://example.com'

    def build_absolute_uri(self, value):
        """ Mocks `Request.build_absolute_uri`. """
        return self.ROOT + value


@ddt.ddt
class AbsoluteURLFieldTests(TestCase):
    """ Tests for the AbsoluteURLField. """

    def setUp(self):
        super().setUp()
        self.field = AbsoluteURLField()
        self.field._context = {'request': MockRequest()}  # pylint:disable=protected-access

    def test_to_representation_without_request(self):
        """ Verify an AssertionError is raised if no request is passed as context to the field. """
        self.field._context = {}  # pylint:disable=protected-access
        self.assertRaises(AssertionError, self.field.to_representation, '/image.jpg')  # noqa: PT027

    @ddt.data(
        'http://example.com',
        'https://example.org'
    )
    def test_to_representation_with_absolute_url(self, value):
        """ Verify the method returns the passed value, if the value is an absolute URL. """
        assert self.field.to_representation(value) == value

    def test_to_representation(self):
        """ Verify the method returns an absolute URL. """
        assert self.field.to_representation('/image.jpg') == (MockRequest.ROOT + '/image.jpg')
