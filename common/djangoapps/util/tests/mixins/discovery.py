"""
Mixins for the CourseDiscoveryApiClient.
"""


import json

import httpretty
from django.conf import settings
from django.core.cache import cache


class CourseCatalogServiceMockMixin:
    """
    Mocks for the Open edX service 'Course Catalog Service' responses.
    """
    COURSE_DISCOVERY_CATALOGS_URL = '{}/catalogs/'.format(  # noqa: UP032
        settings.COURSE_CATALOG_API_URL,
    )

    def setUp(self):
        super().setUp()
        cache.clear()

    def mock_course_discovery_api_for_catalog_contains(self, catalog_id=1, course_run_ids=None):
        """
        Helper function to register course catalog contains API endpoint.
        """
        course_run_ids = course_run_ids or []
        courses = {course_run_id: True for course_run_id in course_run_ids}

        course_discovery_api_response = {
            'courses': courses
        }
        course_discovery_api_response_json = json.dumps(course_discovery_api_response)
        catalog_contains_uri = '{}{}/contains/?course_run_id={}'.format(
            self.COURSE_DISCOVERY_CATALOGS_URL, catalog_id, ','.join(course_run_ids)
        )

        httpretty.register_uri(
            method=httpretty.GET,
            uri=catalog_contains_uri,
            body=course_discovery_api_response_json,
            content_type='application/json'
        )
