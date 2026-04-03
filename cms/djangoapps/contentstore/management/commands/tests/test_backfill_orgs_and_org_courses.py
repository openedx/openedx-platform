"""
Tests for `backfill_orgs_and_org_courses` CMS management command.
"""
from unittest.mock import patch

import ddt
from django.core.management import CommandError, call_command
from opaque_keys.edx.locator import CourseLocator
from organizations import api as organizations_api
from organizations.api import (
    add_organization,
    add_organization_course,
    get_organization_by_short_name,
    get_organization_courses,
    get_organizations,
)

from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import LibraryFactory  # lint-amnesty, pylint: disable=wrong-import-order

from .. import backfill_orgs_and_org_courses


@ddt.ddt
class BackfillOrgsAndOrgCoursesTest(SharedModuleStoreTestCase):
    """
    Test `backfill_orgs_and_org_courses`.

    We test:
    * That one happy path of the command works.
    * That the command line args are processed correctly.
    * That the confirmation prompt works.

    We don't test:
    * Specifics/edge cases around fetching course run keys, content library keys,
      or the actual application of the backfill. Those are handled by tests within
      `course_overviews`, `modulestore`, and `organizations`, respectively.
    """

    @ddt.data("--dry", "--apply")
    def test_end_to_end(self, run_type):
        """
        Test the happy path of the backfill command without any mocking.
        """
        # org_A: already existing, with courses and a library.
        org_a = add_organization({"short_name": "org_A", "name": "Org A"})
        course_a1_key = CourseOverviewFactory(org="org_A", run="1").id
        CourseOverviewFactory(org="org_A", run="2")
        LibraryFactory(org="org_A")

        # Write linkage for org_a->course_a1.
        # (Linkage for org_a->course_a2 is purposefully left out here;
        # it should be created by the backfill).
        add_organization_course(org_a, course_a1_key)

        # org_B: already existing, but has no content.
        add_organization({"short_name": "org_B", "name": "Org B"})

        # org_C: has a few courses; should be created.
        CourseOverviewFactory(org="org_C", run="1")
        CourseOverviewFactory(org="org_C", run="2")
        # Include an Old Mongo Modulestore -style deprecated course key.
        # This can be safely removed when Old Mongo Modulestore support is
        # removed.
        CourseOverviewFactory(
            id=CourseLocator.from_string("org_C/toy/3"),
            org="org_C",
            run="3",
        )

        # org_D: has both a course and a library; should be created.
        CourseOverviewFactory(org="org_D", run="1")
        LibraryFactory(org="org_D")

        # org_E: just has a library; should be created.
        LibraryFactory(org="org_E")

        # Confirm starting condition:
        # Only orgs are org_A and org_B, and only linkage is org_a->course_a1.
        assert {
            org["short_name"] for org in get_organizations()
        } == {
            "org_A", "org_B"
        }
        assert len(get_organization_courses(get_organization_by_short_name('org_A'))) == 1
        assert len(get_organization_courses(get_organization_by_short_name('org_B'))) == 0

        # Run the backfill.
        call_command("backfill_orgs_and_org_courses", run_type)

        if run_type == "--dry":
            # Confirm ending conditions are the same as the starting conditions.
            assert {
                org["short_name"] for org in get_organizations()
            } == {
                "org_A", "org_B"
            }
            assert len(get_organization_courses(get_organization_by_short_name('org_A'))) == 1
            assert len(get_organization_courses(get_organization_by_short_name('org_B'))) == 0
        else:
            # Confirm ending condition:
            # All five orgs present. Each org a has expected number of org-course linkages.
            assert {
                org["short_name"] for org in get_organizations()
            } == {
                "org_A", "org_B", "org_C", "org_D", "org_E"
            }
            assert len(get_organization_courses(get_organization_by_short_name('org_A'))) == 2
            assert len(get_organization_courses(get_organization_by_short_name('org_B'))) == 0
            assert len(get_organization_courses(get_organization_by_short_name('org_C'))) == 3
            assert len(get_organization_courses(get_organization_by_short_name('org_D'))) == 1
            assert len(get_organization_courses(get_organization_by_short_name('org_E'))) == 0

    @ddt.data(
        {
            "command_line_args": [],
            "user_inputs": ["n"],
            "should_apply_changes": False,
            "should_data_be_activated": True,
        },
        {
            "command_line_args": [],
            "user_inputs": ["x", "N"],
            "should_apply_changes": False,
            "should_data_be_activated": True,
        },
        {
            "command_line_args": [],
            "user_inputs": ["", "", "YeS"],
            "should_apply_changes": True,
            "should_data_be_activated": True,
        },
        {
            "command_line_args": ["--inactive"],
            "user_inputs": ["y"],
            "should_apply_changes": True,
            "should_data_be_activated": False,
        },
        {
            "command_line_args": ["--dry"],
            "user_inputs": [],
            "should_apply_changes": False,
            "should_data_be_activated": True,
        },
        {
            "command_line_args": ["--dry", "--inactive"],
            "user_inputs": [],
            "should_apply_changes": False,
            "should_data_be_activated": False,
        },
        {
            "command_line_args": ["--apply"],
            "user_inputs": [],
            "should_apply_changes": True,
            "should_data_be_activated": True,
        },
    )
    @ddt.unpack
    @patch.object(
        # Mock out `bulk_add_organizations` to do nothing and return empty
        # lists, indicating no organizations created or reactivated.
        organizations_api,
        'bulk_add_organizations',
        return_value=([], []),
    )
    @patch.object(
        # Mock out `bulk_add_organization_courses` to do nothing and return empty
        # lists, indicating no linkages created or reactivated.
        organizations_api,
        'bulk_add_organization_courses',
        return_value=([], []),
    )
    def test_arguments_and_input(
            self,
            mock_add_orgs,
            mock_add_org_courses,
            command_line_args,
            user_inputs,
            should_apply_changes,
            should_data_be_activated,
    ):
        """
        Test that the command-line arguments and user input processing works as
        expected.

        Given a list of `command_line_args` and a sequence of `user_inputs`
        that will be supplied, we expect that:
        * the user will be prompted a number of times equal to the length of `user_inputs`, and
        * the command will/won't apply changes according to `should_apply_changes`.
        """
        with patch.object(
            backfill_orgs_and_org_courses, "input", side_effect=user_inputs
        ) as mock_input:
            call_command("backfill_orgs_and_org_courses", *command_line_args)

        # Make sure user was prompted the number of times we expected.
        assert mock_input.call_count == len(user_inputs)

        if should_apply_changes and user_inputs:
            # If we DID apply changes and the user WAS prompted first,
            # then we expect one DRY bulk-add run *and* one REAL bulk-add run.
            assert mock_add_orgs.call_count == 2
            assert mock_add_org_courses.call_count == 2
            assert mock_add_orgs.call_args_list[0].kwargs["dry_run"] is True
            assert mock_add_org_courses.call_args_list[0].kwargs["dry_run"] is True
            assert mock_add_orgs.call_args_list[1].kwargs["dry_run"] is False
            assert mock_add_org_courses.call_args_list[1].kwargs["dry_run"] is False
        elif should_apply_changes:
            # If DID apply changes but the user WASN'T prompted,
            # then we expect just one REAL bulk-add run.
            assert mock_add_orgs.call_count == 1
            assert mock_add_org_courses.call_count == 1
            assert mock_add_orgs.call_args.kwargs["dry_run"] is False
            assert mock_add_org_courses.call_args.kwargs["dry_run"] is False
        elif user_inputs:
            # If we DIDN'T apply changes but the user WAS prompted
            # then we expect just one DRY bulk-add run.
            assert mock_add_orgs.call_count == 1
            assert mock_add_org_courses.call_count == 1
            assert mock_add_orgs.call_args.kwargs["dry_run"] is True
            assert mock_add_org_courses.call_args.kwargs["dry_run"] is True
        else:
            # Similarly, if we DIDN'T apply changes and the user WASN'T prompted
            # then we expect just one DRY bulk-add run.
            assert mock_add_orgs.call_count == 1
            assert mock_add_org_courses.call_count == 1
            assert mock_add_orgs.call_args.kwargs["dry_run"] is True
            assert mock_add_org_courses.call_args.kwargs["dry_run"] is True

        # Assert that the value of of the "active" kwarg is correct for all
        # calls both bulk-add functions, whether or not they were dry runs.
        for call in mock_add_orgs:
            assert call.kwargs["activate"] == should_data_be_activated
        for call in mock_add_org_courses:
            assert call.kwargs["activate"] == should_data_be_activated

    def test_conflicting_arguments(self):
        """
        Test that calling the command with both "--dry" and "--apply" raises an exception.
        """
        with self.assertRaises(CommandError):
            call_command("backfill_orgs_and_org_courses", "--dry", "--apply")
