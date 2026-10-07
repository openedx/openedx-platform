"""Tests for the LMS drf-spectacular hooks."""

from lms.lib.spectacular import lms_api_filter


def _endpoint(path):
    return (path, path, "GET", None)


def test_keeps_enrollment_and_the_cohorts_v2_resources_only():
    kept = lms_api_filter([
        _endpoint("/api/enrollment/v1/enrollment"),
        _endpoint("/api/enrollment/v2/enrollments/"),
        _endpoint("/api/cohorts/v2/courses/{course_key_string}/cohorts/"),
        _endpoint("/api/cohorts/v2/courses/{course_key_string}/cohorts/{cohort_id}/users/{username}/"),
        _endpoint("/api/cohorts/v2/courses/{course_key_string}/cohort_settings/"),
        _endpoint("/api/cohorts/v2/courses/{course_id}/group_configurations"),
        _endpoint("/api/cohorts/v1/courses/{course_key_string}/cohorts/"),
        _endpoint("/api/user/v1/accounts/"),
    ])
    assert [path for path, *_ in kept] == [
        "/api/enrollment/v1/enrollment",
        "/api/enrollment/v2/enrollments/",
        "/api/cohorts/v2/courses/{course_key_string}/cohorts/",
        "/api/cohorts/v2/courses/{course_key_string}/cohorts/{cohort_id}/users/{username}/",
        "/api/cohorts/v2/courses/{course_key_string}/cohort_settings/",
    ]
