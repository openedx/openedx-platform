"""
Tests for waffle utils views.
"""
# pylint: disable=toggle-missing-annotation
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from common.djangoapps.student.tests.factories import UserFactory

from .. import models
from .. import views as toggle_state_views


class ToggleStateViewTests(TestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    """
    Tests for the toggle state report view.
    """

    def test_success_for_staff(self):
        response = get_toggle_state_response()
        assert response.status_code == 200
        assert response.data

    def test_failure_for_non_staff(self):
        response = get_toggle_state_response(is_staff=False)
        assert response.status_code == 403

    def test_response_with_existing_setting_toggle(self):
        response = get_toggle_state_response()
        assert {
            "name": "MILESTONES_APP",
            "is_active": True,
            "module": "common.djangoapps.util.milestones_helpers",
            "class": "SettingToggle",
        } in response.data["django_settings"]

    def test_response_with_course_override(self):
        models.WaffleFlagCourseOverrideModel.objects.create(waffle_flag="my.flag", enabled=True)
        response = get_toggle_state_response()
        assert response.data["waffle_flags"]
        assert "my.flag" == response.data["waffle_flags"][0]["name"]
        assert response.data["waffle_flags"][0]["course_overrides"]
        assert "None" == response.data["waffle_flags"][0]["course_overrides"][0]["course_id"]
        assert "on" == response.data["waffle_flags"][0]["course_overrides"][0]["force"]
        assert "both" == response.data["waffle_flags"][0]["computed_status"]

    def test_response_with_org_override(self):
        models.WaffleFlagOrgOverrideModel.objects.create(waffle_flag="my.flag", enabled=True)
        response = get_toggle_state_response()
        assert response.data["waffle_flags"]
        assert "my.flag" == response.data["waffle_flags"][0]["name"]
        assert response.data["waffle_flags"][0]["org_overrides"]
        assert "" == response.data["waffle_flags"][0]["org_overrides"][0]["org"]
        assert "on" == response.data["waffle_flags"][0]["org_overrides"][0]["force"]
        assert "both" == response.data["waffle_flags"][0]["computed_status"]

    def test_course_and_org_overrides(self):
        models.WaffleFlagCourseOverrideModel.objects.create(waffle_flag='my.flag', enabled=True)
        models.WaffleFlagOrgOverrideModel.objects.create(waffle_flag='my.flag', enabled=True)
        overrides = {}

        report = toggle_state_views.OverrideToggleStateReport()
        report.add_waffle_flag_instances(overrides)
        report.add_waffle_flag_computed_status(overrides)

        assert 'my.flag' in overrides

        assert 'course_overrides' in overrides['my.flag']
        assert 1 == len(overrides['my.flag']['course_overrides'])
        assert 'None' == overrides['my.flag']['course_overrides'][0]['course_id']
        assert 'on' == overrides['my.flag']['course_overrides'][0]['force']
        assert 'both' == overrides['my.flag']['computed_status']

        assert 'org_overrides' in overrides['my.flag']
        assert 1 == len(overrides['my.flag']['org_overrides'])
        assert '' == overrides['my.flag']['org_overrides'][0]['org']
        assert 'on' == overrides['my.flag']['org_overrides'][0]['force']
        assert 'both' == overrides['my.flag']['computed_status']

    def test_computed_status(self):
        models.WaffleFlagCourseOverrideModel.objects.create(
            waffle_flag="my.overriddenflag1", enabled=True, course_id="org/course/id"
        )
        models.WaffleFlagCourseOverrideModel.objects.create(
            waffle_flag="my.overriddenflag2", enabled=True
        )
        models.WaffleFlagCourseOverrideModel.objects.create(
            waffle_flag="my.disabledflag1", enabled=False, course_id="org/course/id"
        )
        models.WaffleFlagOrgOverrideModel.objects.create(
            waffle_flag="my.overriddenflag3", enabled=True, org="org"
        )
        models.WaffleFlagOrgOverrideModel.objects.create(
            waffle_flag="my.overriddenflag4", enabled=True
        )
        models.WaffleFlagOrgOverrideModel.objects.create(
            waffle_flag="my.disabledflag2", enabled=False, org="org"
        )

        overrides = {}
        report = toggle_state_views.OverrideToggleStateReport()
        report.add_waffle_flag_instances(overrides)
        report.add_waffle_flag_computed_status(overrides)

        assert "both" == overrides["my.overriddenflag1"]["computed_status"]
        assert "org/course/id" == overrides["my.overriddenflag1"]["course_overrides"][0]["course_id"]
        assert "on" == overrides["my.overriddenflag1"]["course_overrides"][0]["force"]

        assert "both" == overrides["my.overriddenflag2"]["computed_status"]
        assert "None" == overrides["my.overriddenflag2"]["course_overrides"][0]["course_id"]
        assert "on" == overrides["my.overriddenflag2"]["course_overrides"][0]["force"]

        assert "both" == overrides["my.overriddenflag3"]["computed_status"]
        assert "org" == overrides["my.overriddenflag3"]["org_overrides"][0]["org"]
        assert "on" == overrides["my.overriddenflag3"]["org_overrides"][0]["force"]

        assert "both" == overrides["my.overriddenflag4"]["computed_status"]
        assert "" == overrides["my.overriddenflag4"]["org_overrides"][0]["org"]
        assert "on" == overrides["my.overriddenflag4"]["org_overrides"][0]["force"]

        assert "my.disabledflag1" not in overrides
        assert "my.disabledflag2" not in overrides


def get_toggle_state_response(is_staff=True):
    """
    Query the toggle state API endpoint.
    """
    request = APIRequestFactory().get('/api/toggles/state/')
    request.user = UserFactory(is_staff=is_staff)
    view = toggle_state_views.ToggleStateView.as_view()
    response = view(request)
    return response
