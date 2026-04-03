"""
Sync course runs from catalog service.
"""

import logging
from collections import namedtuple

from django.core.management.base import BaseCommand
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.catalog.utils import get_course_runs
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Purpose is to sync course runs data from catalog service to make it accessible in edx-platform.
    """
    help = 'Refresh marketing urls from catalog service.'

    CourseRunField = namedtuple('CourseRunField', 'catalog_name course_overview_name')
    course_run_fields = (
        CourseRunField(catalog_name='marketing_url', course_overview_name='marketing_url'),
        CourseRunField(catalog_name='eligible_for_financial_aid', course_overview_name='eligible_for_financial_aid'),
        CourseRunField(catalog_name='content_language', course_overview_name='language'),
    )

    def handle(self, *args, **options):
        log.info('[sync_course_runs] Fetching course runs from catalog service.')
        course_runs = get_course_runs()

        # metrics for observability
        num_runs_found_in_catalog = len(course_runs)
        num_runs_found_in_course_overview = 0
        num_course_overviews_updated = 0

        for course_run in course_runs:
            course_key = CourseKey.from_string(course_run['key'])
            try:
                course_overview = CourseOverview.objects.get(id=course_key)
                num_runs_found_in_course_overview += 1
            except CourseOverview.DoesNotExist:
                log.info(
                    '[sync_course_runs] course overview record not found for course run: %s',
                    str(course_key),
                )
                continue

            is_course_metadata_updated = False
            for field in self.course_run_fields:
                catalog_value = course_run.get(field.catalog_name)
                if getattr(course_overview, field.course_overview_name) != catalog_value:
                    setattr(course_overview, field.course_overview_name, catalog_value)
                    is_course_metadata_updated = True

            if is_course_metadata_updated:
                course_overview.save()
                num_course_overviews_updated += 1

        log.info(
            '[sync_course_runs] '
            'course runs found in catalog: %d, '
            'course runs found in course overview: %d, '
            'course runs not found in course overview: %d, '
            'course overviews updated: %d',
            num_runs_found_in_catalog,
            num_runs_found_in_course_overview,
            num_runs_found_in_catalog - num_runs_found_in_course_overview,
            num_course_overviews_updated,
        )
