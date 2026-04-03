""" Shared behavior between create_test_users and create_random_users """
from django.contrib.auth import get_user_model
from django.core.validators import ValidationError

from common.djangoapps.student.helpers import AccountValidationError, do_create_account
from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.instructor.access import allow_access
from openedx.core.djangoapps.user_authn.views.registration_form import AccountCreationForm
from xmodule.modulestore.django import modulestore

User = get_user_model()


def create_users(
    course_key,
    user_data,
    enrollment_mode=None,
    course_staff=False,
    activate=False,
    ignore_user_already_exists=False,
):
    """Create users, enrolling them in course_key if it's not None"""
    for single_user_data in user_data:
        account_creation_form = AccountCreationForm(
            data=single_user_data,
            tos_required=False
        )

        user_already_exists = False
        try:
            (user, _, _) = do_create_account(account_creation_form)
        except (ValidationError, AccountValidationError) as account_creation_error:
            # It would be convenient if we just had the AccountValidationError raised, because we include a
            # helpful error code in there, but we also do form validation on account_creation_form and those
            # are pretty opaque.
            try:
                # Check to see if there's a user with our username. If the username and email match our input,
                # we're good, it's the same user, probably. If the email doesn't match, just to be safe we will
                # continue to fail.
                user = User.objects.get(username=single_user_data['username'])
                if user.email == single_user_data['email'] and ignore_user_already_exists:
                    user_already_exists = True
                    print(f'Test user {user.username} already exists. Continuing to attempt to enroll.')
                else:
                    raise account_creation_error
            except User.DoesNotExist:
                # If a user with the username doesn't exist the error was probably something else, so reraise
                raise account_creation_error  # pylint: disable=raise-missing-from

        if activate:
            user.is_active = True
            user.save()

        if course_key is not None:
            CourseEnrollment.enroll(user, course_key, mode=enrollment_mode)
            if course_staff:
                course = modulestore().get_course(course_key, depth=1)
                allow_access(course, user, 'staff', send_email=False)

        if course_key and course_staff and not user_already_exists:
            print(f'Created user {user.username} as course staff')
        elif not user_already_exists:
            print(f'Created user {user.username}')
