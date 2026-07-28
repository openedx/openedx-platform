# pylint: disable=missing-module-docstring
import factory

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.survey.models import SurveyAnswer, SurveyForm


class SurveyFormFactory(factory.django.DjangoModelFactory):  # pylint: disable=missing-class-docstring
    class Meta:
        model = SurveyForm

    name = 'Test Survey Form'
    form = '<form>First name:<input type="text" name="firstname"/></form>'


class SurveyAnswerFactory(factory.django.DjangoModelFactory):  # pylint: disable=missing-class-docstring
    class Meta:
        model = SurveyAnswer

    user = factory.SubFactory(UserFactory)
    form = factory.SubFactory(SurveyFormFactory)
