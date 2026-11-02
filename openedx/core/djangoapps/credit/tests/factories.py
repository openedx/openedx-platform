# pylint:disable=missing-docstring


import datetime
import json
import uuid

import factory
from factory.fuzzy import FuzzyText
from zoneinfo import ZoneInfo
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user

from openedx.core.djangoapps.credit.models import (
    CreditCourse,
    CreditEligibility,
    CreditProvider,
    CreditRequest,
    CreditRequirement,
    CreditRequirementStatus
)
from common.djangoapps.util.date_utils import to_timestamp


class CreditCourseFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CreditCourse

    course_key = FuzzyText(prefix='fake.org/', suffix='/fake.run')
    enabled = True


class CreditRequirementFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CreditRequirement

    course = factory.SubFactory(CreditCourseFactory)


class CreditRequirementStatusFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CreditRequirementStatus

    requirement = factory.SubFactory(CreditRequirementFactory)
    status = CreditRequirementStatus.REQUIREMENT_STATUS_CHOICES[0][0]


class CreditProviderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CreditProvider

    provider_id = FuzzyText(length=5)
    provider_url = FuzzyText(prefix='http://')


class CreditEligibilityFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CreditEligibility

    course = factory.SubFactory(CreditCourseFactory)


class CreditRequestFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CreditRequest

    uuid = factory.LazyAttribute(lambda o: uuid.uuid4().hex)  # pylint: disable=undefined-variable

    # pylint: disable=access-member-before-definition,attribute-defined-outside-init,no-self-argument,unused-argument
    @factory.post_generation
    def post(obj, create, extracted, **kwargs):
        """
        Post-generation handler.

        Sets up parameters field.
        """
        if not obj.parameters:
            course_key = obj.course.course_key
            user = User.objects.get(username=obj.username)
            user_profile = user.profile

            obj.parameters = json.dumps({
                "request_uuid": obj.uuid,
                "timestamp": to_timestamp(datetime.datetime.now(ZoneInfo("UTC"))),
                "course_org": course_key.org,
                "course_num": course_key.course,
                "course_run": course_key.run,
                "final_grade": '0.96',
                "user_username": user.username,
                "user_email": user.email,
                "user_full_name": user_profile.name,
                "user_mailing_address": "",
                "user_country": user_profile.country.code or "",
            })

        obj.save()
