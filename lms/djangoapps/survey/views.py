"""
View endpoints for Survey
"""


import json
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseNotFound, HttpResponseRedirect
from django.urls import reverse
from django.utils.html import escape
from django.views.decorators.http import require_POST
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.edxmako.shortcuts import render_to_response
from lms.djangoapps.survey.models import SurveyForm
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers

log = logging.getLogger("edx.survey")


@login_required
def view_survey(request, survey_name):
    """
    View to render the survey to the end user
    """
    return view_student_survey(request.user, survey_name)


def view_student_survey(user, survey_name, course=None, redirect_url=None, is_required=False, skip_redirect_url=None):
    """
    Shared utility method to render a survey form
    NOTE: This method is shared between the Survey and Courseware Djangoapps
    """

    redirect_url = redirect_url if redirect_url else reverse('dashboard')
    dashboard_redirect_url = reverse('dashboard')
    skip_redirect_url = skip_redirect_url if skip_redirect_url else dashboard_redirect_url

    survey = SurveyForm.get(survey_name, throw_if_not_found=False)
    if not survey:
        return HttpResponseRedirect(redirect_url)

    # the result set from get_answers, has an outer key with the user_id
    # just remove that outer key to make the JSON payload simplier
    existing_answers = survey.get_answers(user=user).get(user.id, {})

    platform_name = configuration_helpers.get_value('platform_name', settings.PLATFORM_NAME)

    context = {
        'existing_data_json': json.dumps(existing_answers),
        'postback_url': reverse('submit_answers', args=[survey_name]),
        'redirect_url': redirect_url,
        'skip_redirect_url': skip_redirect_url,
        'dashboard_redirect_url': dashboard_redirect_url,
        'survey_form': survey.form,
        'is_required': is_required,
        'mail_to_link': configuration_helpers.get_value('email_from_address', settings.CONTACT_EMAIL),
        'platform_name': platform_name,
        'course': course,
    }

    return render_to_response("survey/survey.html", context)


@require_POST
@login_required
def submit_answers(request, survey_name):
    """
    Form submission post-back endpoint.

    NOTE: We do not have a formal definition of a Survey Form, it's just some authored HTML
    form fields (via Django Admin site). Therefore we do not do any validation of the submission server side. It is
    assumed that all validation is done via JavaScript in the survey.html file
    """
    survey = SurveyForm.get(survey_name, throw_if_not_found=False)

    if not survey:
        return HttpResponseNotFound()

    answers = {}
    for key in request.POST.keys():
        # support multi-SELECT form values, by string concatenating them with a comma separator
        array_val = request.POST.getlist(key)
        answers[key] = request.POST[key] if len(array_val) == 0 else ','.join(array_val)

    redirect_url = reverse('dashboard')

    course_key = CourseKey.from_string(answers['course_id']) if 'course_id' in answers else None

    allowed_field_names = survey.get_field_names()

    # scrub the answers to make sure nothing malicious from the user gets stored in
    # our database, e.g. JavaScript
    filtered_answers = {}
    for answer_key in answers.keys():  # pylint: disable=consider-iterating-dictionary
        # only allow known input fields
        if answer_key in allowed_field_names:
            filtered_answers[answer_key] = escape(answers[answer_key])

    survey.save_user_answers(request.user, filtered_answers, course_key)

    response_params = json.dumps({
        # The HTTP end-point for the payment processor.
        "redirect_url": redirect_url,
    })

    return HttpResponse(response_params, content_type="text/json")
