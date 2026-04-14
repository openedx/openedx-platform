"""
This module provides context processors for integrating survey report functionality
into Django admin sites.

It includes functions for determining whether to display a survey report banner and
calculating the date threshold for displaying the banner.

Functions:
- admin_extra_context(request):
Sends extra context to every admin site, determining whether to display the
survey report banner based on defined settings and conditions.

- should_show_survey_report_banner():
Determines whether to show the survey report banner based on the threshold.

- get_months_threshold(months):
Calculates the date threshold based on the specified number of months.

Dependencies:
- Django: settings, reverse, shortcuts
- datetime: datetime
- dateutil.relativedelta: relativedelta

Usage:
This module is designed to be imported into Django projects with admin functionality.
It enhances the admin interface by providing dynamic context for displaying a survey
report banner based on defined conditions and settings.
"""
from datetime import datetime

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.urls import reverse

from .models import SurveyReport


def admin_extra_context(request):
    """
    This function sends extra context to every admin site.
    The current threshold to show the banner is one month but this can be redefined in the future.
    """
    if not settings.SURVEY_REPORT_ENABLE or not request.path.startswith(reverse('admin:index')):
        return {'show_survey_report_banner': False}

    return {'show_survey_report_banner': should_show_survey_report_banner()}


def should_show_survey_report_banner():
    """
    Determine whether to show the survey report banner based on the threshold.
    """
    months_threshold = get_months_threshold(settings.SURVEY_REPORT_CHECK_THRESHOLD)

    try:
        latest_report = SurveyReport.objects.latest('created_at')
        return latest_report.created_at.date() <= months_threshold
    except SurveyReport.DoesNotExist:
        return True


def get_months_threshold(months):
    """
    Calculate the date threshold based on the specified number of months.
    """
    return datetime.today().date() - relativedelta(months=months)
