"""
Transfer Student Management Command
"""


from textwrap import dedent

from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.db import transaction
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.track.management.tracked_command import TrackedCommand


class TransferStudentError(Exception):
    """
    Generic Error when handling student transfers.
    """
    pass  # pylint: disable=unnecessary-pass


class Command(TrackedCommand):
    """
    Transfer students enrolled in one course into one or more other courses.

    This will remove them from the first course.  Their enrollment mode (i.e.
    honor, verified, audit, etc.) will persist into the other course(s).
    """
    help = dedent(__doc__)

    def add_arguments(self, parser):
        parser.add_argument('-f', '--from',
                            metavar='SOURCE_COURSE',
                            dest='source_course',
                            required=True,
                            help='the course to transfer students from')
        parser.add_argument('-t', '--to',
                            nargs='+',
                            metavar='DEST_COURSE',
                            dest='dest_course_list',
                            required=True,
                            help='the new course(s) to enroll the student into')

    @transaction.atomic
    def handle(self, *args, **options):
        source_key = CourseKey.from_string(options['source_course'])
        dest_keys = []
        for course_key in options['dest_course_list']:
            dest_keys.append(CourseKey.from_string(course_key))

        source_students = User.objects.filter(
            courseenrollment__course_id=source_key
        )

        for user in source_students:
            with transaction.atomic():
                print(f'Moving {user.username}.')
                # Find the old enrollment.
                enrollment = CourseEnrollment.objects.get(
                    user=user,
                    course_id=source_key
                )

                # Move the Student between the classes.
                mode = enrollment.mode
                old_is_active = enrollment.is_active
                CourseEnrollment.unenroll(user, source_key, skip_refund=True)
                print(f'Unenrolled {user.username} from {str(source_key)}')

                for dest_key in dest_keys:
                    if CourseEnrollment.is_enrolled(user, dest_key):
                        # Un Enroll from source course but don't mess
                        # with the enrollment in the destination course.
                        msg = 'Skipping {}, already enrolled in destination course {}'
                        print(msg.format(user.username, str(dest_key)))
                    else:
                        new_enrollment = CourseEnrollment.enroll(user, dest_key, mode=mode)

                        # Un-enroll from the new course if the user had un-enrolled
                        # form the old course.
                        if not old_is_active:
                            new_enrollment.update_enrollment(is_active=False, skip_refund=True)
