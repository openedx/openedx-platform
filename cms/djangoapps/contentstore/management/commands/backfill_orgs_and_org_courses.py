"""
A backfill command to migrate Open edX instances to the new world of
"organizations are enabled everywhere".

For full context, see:
https://github.com/openedx/edx-organizations/blob/master/docs/decisions/0001-phase-in-db-backed-organizations-to-all.rst
"""
from typing import Dict, List, Set, Tuple  # noqa: UP035

from django.core.management import BaseCommand, CommandError
from organizations import api as organizations_api

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order


class Command(BaseCommand):
    """
    Back-populate edx-organizations models from existing course runs & content libraries.

    Before the Lilac open release, Open edX instances by default did not make
    use of the models in edx-organizations.
    In Lilac and beyond, the edx-organizations models are enabled globally.

    This command exists to migrate pre-Lilac instances that did not enable
    `FEATURES['ORGANIZATIONS_APP']`.
    It automatically creates all missing Organization and OrganizationCourse
    instances based on the course runs in the system (loaded from CourseOverview)
    and the V1 content libraries in the system (loaded from the Modulestore).

    Organizations created by this command will have their `short_name` and
    `name` equal to the `org` part of the library/course key that triggered
    their creation. For example, given an Open edX instance with the course run
    `course-v1:myOrg+myCourse+myRun` but no such Organization with the short name
    "myOrg" (case-insensitive), this command will create the following
    organization:
        > Organization(
        >     short_name='myOrg',
        >     name='myOrg',
        >     description=None,
        >     logo=None,
        >     active=True,
        > )

    Example run of command:

        root@studio:/edx/app/edxapp/edx-platform# ./manage.py cms backfill_orgs_and_org_courses
        << ... lots of logging output ... >>
        ------------------------------------------------------
        Dry-run of bulk-adding organizations...
        Will create 5 organizations:
            KyleX
            KyleX2
            KyleX3
            KyleX4
            KyleX5
        Will reactivate 2 organizations:
            BD04
            BD05
        ------------------------------------------------------
        Dry-run of bulk-adding organization-course linkages...
        Will create 5 organization-course linkages:
            kylex,course-v1:KyleX+OrgTest+1
            kylex2,course-v1:KyleX2+OrgTest+1
            kylex3,course-v1:KyleX3+OrgTest+1
            kylex4,course-v1:KyleX4+OrgTest+1
            kylex5,course-v1:KyleX5+OrgTest+1
        Will reactivate 0 organization-course linkages:
        ------------------------------------------------------
        Commit changes shown above to the database [y/n]? x
        Commit changes shown above to the database [y/n]? yes
        ------------------------------------------------------
        Bulk-adding organizations...
        Created 5 organizations:
            KyleX
            KyleX2
            KyleX3
            KyleX4
            KyleX5
        Reactivated 2 organizations:
            BD04
            BD05
        ------------------------------------------------------
        Bulk-adding organization-course linkages...
        Created 5 organization-course linkages:
            kylex,course-v1:KyleX+OrgTest+1
            kylex2,course-v1:KyleX2+OrgTest+1
            kylex3,course-v1:KyleX3+OrgTest+1
            kylex4,course-v1:KyleX4+OrgTest+1
            kylex5,course-v1:KyleX5+OrgTest+1
        Reactivated 0 organization-course linkages:
        ------------------------------------------------------
        root@studio:/edx/app/edxapp/edx-platform#
    """

    # Make help  message the first line of docstring.
    # I'd like to include the entire docstring but Django omits the newlines,
    # so it looks pretty bad.
    help = __doc__.strip().splitlines()[0]

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help="Apply backfill to database without prompting for confirmation."
        )
        parser.add_argument(
            '--dry',
            action='store_true',
            help="Show backfill, but do not apply changes to database."
        )
        parser.add_argument(
            '--inactive',
            action='store_true',
            help="Backfill data as inactive and do not re-activate any existing data."
        )

    def handle(self, *args, **options):
        """
        Handle the backfill command.
        """
        orgslug_courseid_pairs = find_orgslug_courseid_pairs()
        orgslug_libraryid_pairs = find_orgslug_libraryid_pairs()
        orgslugs = (
            {orgslug for orgslug, _ in orgslug_courseid_pairs} |
            {orgslug for orgslug, _ in orgslug_libraryid_pairs}
        )
        # Note: the `organizations.api.bulk_add_*` code will handle:
        # * not overwriting existing organizations, and
        # * skipping duplicates, based on the short name (case-insensiive),
        # so we don't have to worry about those here.
        orgs = [
            {"short_name": orgslug, "name": orgslug}
            # The `sorted` calls aren't strictly necessary, but they'll help make this
            # function more deterministic in case something goes wrong.
            for orgslug in sorted(orgslugs)
        ]
        org_courseid_pairs = [
            ({"short_name": orgslug}, courseid)
            for orgslug, courseid in sorted(orgslug_courseid_pairs)
        ]
        if not confirm_changes(options, orgs, org_courseid_pairs):
            print("No changes applied.")
            return
        bulk_add_data(
            orgs,
            org_courseid_pairs,
            dry_run=False,
            activate=(not options.get('inactive')),
        )


def confirm_changes(
        options: Dict[str, str],  # noqa: UP006
        orgs: List[dict],  # noqa: UP006
        org_courseid_pairs: List[Tuple[dict, str]],  # noqa: UP006
) -> bool:
    """
    Should we apply the changes to the database?

    If `--apply`, this just returns True.
    If `--dry`, this does a dry run and then returns False.
    Otherwise, it does a dry run and then prompts the user.

    Arguments:
        options: command-line arguments.
        orgs: org data dictionaries to bulk-add.
              should each have a "short_name" and "name" key.
        org_courseid_pairs
            list of (org data dictionary, course key string) links to bulk-add.
            each org data dictionary should have a "short_name" key.

    Returns:
        Whether user wants changes to be applied.
    """
    if options.get('apply') and options.get('dry'):
        raise CommandError("Only one of 'apply' and 'dry' may be specified")
    if options.get('apply'):
        return True
    bulk_add_data(
        orgs,
        org_courseid_pairs,
        dry_run=True,
        activate=(not options.get('inactive')),
    )
    if options.get('dry'):
        return False
    answer = ""
    while answer.lower() not in {'y', 'yes', 'n', 'no'}:
        answer = input('Commit changes shown above to the database [y/n]? ')
    return answer.lower().startswith('y')


def bulk_add_data(
        orgs: List[dict],  # noqa: UP006
        org_courseid_pairs: List[Tuple[dict, str]],  # noqa: UP006
        dry_run: bool,
        activate: bool,
):
    """
    Bulk-add the organizations and organization-course linkages.

    Print out list of organizations and organization-course linkages,
    one per line. We distinguish between records that are added by
    being created vs. those that are being added by just reactivating an
    existing record.

    Arguments:
        orgs: org data dictionaries to bulk-add.
              should each have a "short_name" and "name" key.
        org_courseid_pairs
            list of (org data dictionary, course key string) links to bulk-add.
            each org data dictionary should have a "short_name" key.
        dry_run: Whether or not this run should be "dry" (ie, don't apply changes).
        activate: Whether newly-added organizations and organization-course linkages
            should be activated, and whether existing-but-inactive
            organizations/linkages should be reactivated.
    """
    adding_phrase = "Dry-run of bulk-adding" if dry_run else "Bulk-adding"
    created_phrase = "Will create" if dry_run else "Created"
    reactivated_phrase = "Will reactivate" if dry_run else "Reactivated"

    print("------------------------------------------------------")
    print(f"{adding_phrase} organizations...")
    orgs_created, orgs_reactivated = organizations_api.bulk_add_organizations(
        orgs, dry_run=dry_run, activate=activate
    )
    print(f"{created_phrase} {len(orgs_created)} organizations:")
    for org_short_name in sorted(orgs_created):
        print(f"    {org_short_name}")
    print(f"{reactivated_phrase} {len(orgs_reactivated)} organizations:")
    for org_short_name in sorted(orgs_reactivated):
        print(f"    {org_short_name}")

    print("------------------------------------------------------")
    print(f"{adding_phrase} organization-course linkages...")
    linkages_created, linkages_reactivated = organizations_api.bulk_add_organization_courses(
        org_courseid_pairs, dry_run=dry_run, activate=activate
    )
    print(f"{created_phrase} {len(linkages_created)} organization-course linkages:")
    for org_short_name, course_id in sorted(linkages_created):
        print(f"    {org_short_name},{course_id}")
    print(f"{reactivated_phrase} {len(linkages_reactivated)} organization-course linkages:")
    for org_short_name, course_id in sorted(linkages_reactivated):
        print(f"    {org_short_name},{course_id}")
    print("------------------------------------------------------")


def find_orgslug_courseid_pairs() -> Set[Tuple[str, str]]:  # noqa: UP006
    """
    Returns the unique pairs of (organization short name, course run key string)
    from the CourseOverviews table, which should contain all course runs in the
    system.

    Returns: set[tuple[str, str]]
    """
    # Using a set comprehension removes any duplicate (org, course) pairs.
    return {
        (course_key.org, str(course_key))
        for course_key
        # Worth noting: This will load all CourseOverviews, no matter their VERSION.
        # This is intentional: there may be course runs that haven't updated
        # their CourseOverviews entry since the last schema change; we still want
        # capture those course runs.
        in CourseOverview.objects.all().values_list("id", flat=True)
    }


def find_orgslug_libraryid_pairs() -> Set[Tuple[str, str]]:  # noqa: UP006
    """
    Returns the unique pairs of (organization short name, content library key string)
    from the modulestore.

    Note that this only considers "version 1" (aka "legacy" or "modulestore-based")
    content libraries.
    We do not consider "version 2" content libraries, because those require a
    database-level link to their authoring organization, and thus would not need
    backfilling via this command.

    Returns: set[tuple[str, str]]
    """
    # Using a set comprehension removes any duplicate (org, library) pairs.
    return {
        (library_key.org, str(library_key))
        for library_key
        in modulestore().get_library_keys()
    }
