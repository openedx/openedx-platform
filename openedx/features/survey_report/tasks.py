"""
Tasks for Survey Report.
"""


import logging

from celery import shared_task
from edx_django_utils.monitoring import set_code_owner_attribute

from .api import generate_report

log = logging.getLogger('edx.celery.task')


@shared_task(name='openedx.features.survey_report.tasks.generate_survey_report')
@set_code_owner_attribute
def generate_survey_report():
    """
    Tasks to generate a new survey report with non-sensitive data.
    """
    log.info(
        'Started - generate survey report'
    )

    try:
        generate_report()
        log.info('Done - generate survey report')
    except (Exception, ):  # pylint: disable=broad-except  # noqa: B013
        log.error('Error - generate survey report')
