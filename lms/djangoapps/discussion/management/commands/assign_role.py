# pylint: disable=imported-auth-user, missing-module-docstring
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from openedx.core.djangoapps.django_comment_common.models import Role


class Command(BaseCommand):  # pylint: disable=missing-class-docstring
    help = 'Assign a discussion forum role to a user.'

    def add_arguments(self, parser):
        parser.add_argument('name_or_email',
                            help='username or email address of the user to assign a role')
        parser.add_argument('role',
                            help='the role to which the user will be assigned')
        parser.add_argument('course_id',
                            help='the edx course_id')
        parser.add_argument('--remove',
                            action='store_true',
                            help='remove the role instead of adding/assigning it')

    def handle(self, *args, **options):
        name_or_email = options['name_or_email']
        role = options['role']
        course_id = options['course_id']

        role = Role.objects.get(name=role, course_id=course_id)

        user = User.objects.filter(Q(email=name_or_email) | Q(username=name_or_email)).first()
        if not user:
            raise CommandError(f"User {name_or_email} does not exist.")

        if options['remove']:
            user.roles.remove(role)
        else:
            user.roles.add(role)

        print('Success!')
