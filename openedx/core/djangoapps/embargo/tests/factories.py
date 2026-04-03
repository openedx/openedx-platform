"""Factories for generating fake embargo data."""

import factory
from factory.django import DjangoModelFactory

from xmodule.modulestore.tests.factories import CourseFactory

from ..models import Country, CountryAccessRule, RestrictedCourse


class CountryFactory(DjangoModelFactory):
    class Meta:
        model = Country

    country = 'US'


class RestrictedCourseFactory(DjangoModelFactory):  # lint-amnesty, pylint: disable=missing-class-docstring
    class Meta:
        model = RestrictedCourse

    @factory.lazy_attribute
    def course_key(self):
        return CourseFactory().id  # lint-amnesty, pylint: disable=no-member


class CountryAccessRuleFactory(DjangoModelFactory):  # lint-amnesty, pylint: disable=missing-class-docstring
    class Meta:
        model = CountryAccessRule

    country = factory.SubFactory(CountryFactory)
    restricted_course = factory.SubFactory(RestrictedCourseFactory)
    rule_type = CountryAccessRule.BLACKLIST_RULE
