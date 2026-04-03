# lint-amnesty, pylint: disable=missing-module-docstring

import json
from datetime import timedelta

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory
from opaque_keys.edx.locator import CourseLocator

from ..models import CourseOverview


class CourseOverviewFactory(DjangoModelFactory):  # lint-amnesty, pylint: disable=missing-class-docstring
    class Meta:
        model = CourseOverview
        django_get_or_create = ('id', )
        exclude = ('run', )

    version = CourseOverview.VERSION
    pre_requisite_courses = []
    org = 'edX'
    display_number_with_default = 'toy'
    run = factory.Sequence('2012_Fall_{}'.format)

    @factory.lazy_attribute
    def _pre_requisite_courses_json(self):
        return json.dumps(self.pre_requisite_courses)

    @factory.lazy_attribute
    def _location(self):
        return self.id.make_usage_key('course', 'course')

    @factory.lazy_attribute
    def id(self):
        return CourseLocator(self.org, self.display_number_with_default, self.run)

    @factory.lazy_attribute
    def display_org_with_default(self):
        return self.org

    @factory.lazy_attribute
    def display_name(self):
        return f"{self.id} Course"

    @factory.lazy_attribute
    def start(self):
        return timezone.now()

    @factory.lazy_attribute
    def end(self):
        return timezone.now() + timedelta(30)
