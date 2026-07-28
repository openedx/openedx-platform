# pylint: disable=missing-module-docstring
###
### Script for editing the course's tabs
###

#
# Run it this way:
#   ./manage.py cms --settings dev edit_course_tabs --course Stanford/CS99/2013_spring
#


from django.core.management.base import BaseCommand, CommandError
from opaque_keys.edx.keys import CourseKey

from cms.djangoapps.contentstore.views import tabs
from openedx.core.lib.courses import get_course_by_id

from .prompt import query_yes_no


def print_course(course):
    "Prints out the course id and a numbered list of tabs."
    try:
        print(course.id)
        print('num type name')
        for index, item in enumerate(course.tabs):
            print(index + 1, '"' + item.get('type') + '"', '"' + item.get('name', '') + '"')
    # If a course is bad we will get an error here, dump it and die instead of
    # just sending up the error that .id doesn't exist.
    except AttributeError:
        print(course)
        raise


# course.tabs looks like this
# [{u'type': u'courseware'}, {u'type': u'textbooks'},
# {u'type': u'discussion', u'name': u'Discussion'}, {u'type': u'wiki', u'name': u'Wiki'},
# {u'type': u'progress', u'name': u'Progress'}]


class Command(BaseCommand):  # pylint: disable=missing-class-docstring
    help = """See and edit a course's tabs list. Only supports insertion
and deletion. Move and rename etc. can be done with a delete
followed by an insert. The tabs are numbered starting with 1.
Tabs 1 and 2 cannot be changed, and tabs of type static_tab cannot
be edited (use Studio for those).

As a first step, run the command with a courseid like this:
  --course Stanford/CS99/2013_spring
This will print the existing tabs types and names. Then run the
command again, adding --insert or --delete to edit the list.
"""

    course_help = '--course <id> required, e.g. Stanford/CS99/2013_spring'
    delete_help = '--delete <tab-number>'
    insert_help = '--insert <tab-number> <type> <name>, e.g. 4 "discussion" "Discussion"'

    def add_arguments(self, parser):
        parser.add_argument('--course',
                            dest='course',
                            default=False,
                            required=True,
                            help=self.course_help)
        parser.add_argument('--delete',
                            dest='delete',
                            default=False,
                            nargs=1,
                            help=self.delete_help)
        parser.add_argument('--insert',
                            dest='insert',
                            default=False,
                            nargs=3,
                            help=self.insert_help,
                            )

    def handle(self, *args, **options):
        course = get_course_by_id(CourseKey.from_string(options['course']))

        print('Warning: this command directly edits the list of course tabs in mongo.')
        print('Tabs before any changes:')
        print_course(course)

        try:
            if options['delete']:
                num = int(options['delete'][0])
                if num < 3:
                    raise CommandError("Tabs 1 and 2 cannot be changed.")

                if query_yes_no(f'Deleting tab {num} Confirm?', default='no'):
                    tabs.primitive_delete(course, num - 1)  # -1 for 0-based indexing
            elif options['insert']:
                num, tab_type, name = options['insert']
                num = int(num)
                if num < 3:
                    raise CommandError("Tabs 1 and 2 cannot be changed.")

                if query_yes_no(f'Inserting tab {num} "{tab_type}" "{name}" Confirm?', default='no'):
                    tabs.primitive_insert(course, num - 1, tab_type, name)  # -1 as above
        except ValueError as e:
            # Cute: translate to CommandError so the CLI error prints nicely.
            raise CommandError(e)  # pylint: disable=raise-missing-from  # noqa: B904
