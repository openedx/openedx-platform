"""
Tests for `field_overrides` module.
"""
import unittest

import pytest
from django.test.utils import override_settings
from xblock.field_data import DictFieldData

from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

from ..field_overrides import (
    FieldOverrideProvider,
    OverrideFieldData,
    OverrideModulestoreFieldData,
    disable_overrides,
    resolve_dotted,
)
from ..testutils import FieldOverrideTestMixin

TESTUSER = "testuser"


class TestOverrideProvider(FieldOverrideProvider):
    """
    A concrete implementation of `FieldOverrideProvider` for testing.
    """
    def get(self, block, name, default):
        if self.user:
            assert self.user is TESTUSER

        assert block == 'block'

        if name == 'foo':
            return 'fu'
        elif name == 'oh':
            return 'man'

        return default

    @classmethod
    def enabled_for(cls, course):  # pylint: disable=arguments-differ
        return True


class OverrideFieldBase(SharedModuleStoreTestCase):
    """
    Base class for field data override tests.  Using override_settings and
    a setUpClass() override in a test class which is inherited by another
    test class doesn't work well with pytest-django.
    """
    @classmethod
    def setUpClass(cls):
        """
        Course is created here and shared by all the class's tests.
        """
        super().setUpClass()
        cls.course = CourseFactory.create(enable_ccx=True)


@override_settings(FIELD_OVERRIDE_PROVIDERS=(
    'lms.djangoapps.courseware.tests.test_field_overrides.TestOverrideProvider',))
class OverrideFieldDataTests(OverrideFieldBase):
    """
    Tests for `OverrideFieldData`.
    """

    def setUp(self):
        super().setUp()
        OverrideFieldData.provider_classes = None

    def tearDown(self):
        super().tearDown()
        OverrideFieldData.provider_classes = None

    def make_one(self):
        """
        Factory method.
        """
        return OverrideFieldData.wrap(TESTUSER, self.course, DictFieldData({
            'foo': 'bar',
            'bees': 'knees',
        }))

    def test_get(self):
        data = self.make_one()
        assert data.get('block', 'foo') == 'fu'
        assert data.get('block', 'bees') == 'knees'
        with disable_overrides():
            assert data.get('block', 'foo') == 'bar'

    def test_set(self):
        data = self.make_one()
        data.set('block', 'foo', 'yowza')
        assert data.get('block', 'foo') == 'fu'
        with disable_overrides():
            assert data.get('block', 'foo') == 'yowza'

    def test_delete(self):
        data = self.make_one()
        data.delete('block', 'foo')
        assert data.get('block', 'foo') == 'fu'
        with disable_overrides():
            # Since field_data is responsible for attribute access, you'd
            # expect it to raise AttributeError. In fact, it raises KeyError,
            # so we check for that.
            with pytest.raises(KeyError):
                data.get('block', 'foo')

    def test_has(self):
        data = self.make_one()
        assert data.has('block', 'foo')
        assert data.has('block', 'bees')
        assert data.has('block', 'oh')
        with disable_overrides():
            assert not data.has('block', 'oh')

    def test_many(self):
        data = self.make_one()
        data.set_many('block', {'foo': 'baz', 'ah': 'ic'})
        assert data.get('block', 'foo') == 'fu'
        assert data.get('block', 'ah') == 'ic'
        with disable_overrides():
            assert data.get('block', 'foo') == 'baz'

    @override_settings(FIELD_OVERRIDE_PROVIDERS=())
    def test_no_overrides_configured(self):
        data = self.make_one()
        assert isinstance(data, DictFieldData)


@override_settings(
    MODULESTORE_FIELD_OVERRIDE_PROVIDERS=['lms.djangoapps.courseware.tests.test_field_overrides.TestOverrideProvider']
)
class OverrideModulestoreFieldDataTests(FieldOverrideTestMixin, OverrideFieldDataTests):  # lint-amnesty, pylint: disable=missing-class-docstring, test-inherits-tests
    def make_one(self):
        return OverrideModulestoreFieldData.wrap(self.course, DictFieldData({
            'foo': 'bar',
            'bees': 'knees',
        }))

    @override_settings(MODULESTORE_FIELD_OVERRIDE_PROVIDERS=[])
    def test_no_overrides_configured(self):
        data = self.make_one()
        assert isinstance(data, DictFieldData)


class ResolveDottedTests(unittest.TestCase):
    """
    Tests for `resolve_dotted`.
    """

    def test_bad_sub_import(self):
        with pytest.raises(ImportError):
            resolve_dotted('lms.djangoapps.courseware.tests.test_foo')

    def test_bad_import(self):
        with pytest.raises(ImportError):
            resolve_dotted('nosuchpackage')

    def test_import_something_that_isnt_already_loaded(self):
        assert resolve_dotted('lms.djangoapps.courseware.tests.animport.SOMENAME') == 'bar'


def inject_field_overrides(blocks, course, user):
    """
    Apparently the test harness doesn't use LmsFieldStorage, and I'm
    not sure if there's a way to poke the test harness to do so.  So,
    we'll just inject the override field storage in this brute force
    manner.
    """
    OverrideFieldData.provider_classes = None
    for block in blocks:
        block._field_data = OverrideFieldData.wrap(   # pylint: disable=protected-access
            user, course, block._field_data)   # pylint: disable=protected-access
