"""
Test the retire_user management command
"""


import csv
import os

import pytest
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core.management import CommandError, call_command

from common.djangoapps.student.tests.factories import UserFactory  # lint-amnesty, pylint: disable=wrong-import-order
from openedx.core.djangoapps.user_api.accounts.tests.retirement_helpers import (  # lint-amnesty, pylint: disable=unused-import, wrong-import-order
    setup_retirement_states,
)
from openedx.core.djangolib.testing.utils import skip_unless_lms  # lint-amnesty, pylint: disable=wrong-import-order

from ...models import UserRetirementStatus

pytestmark = pytest.mark.django_db
user_file = 'userfile.csv'


def generate_dummy_users():
    """
    Function to generate dummy users that needs to be retired
    """
    users = []
    emails = []
    for i in range(1000):
        user = UserFactory.create(username=f"user{i}", email=f"user{i}@example.com")
        users.append(user.username)
        emails.append(user.email)
    users_list = [{'username': user, 'email': email} for user, email in zip(users, emails)]
    return users_list


def create_user_file(other_email=False):
    """
    Function to create a comma spearated file with username and password

    Args:
        other_email (bool, optional): test user with email mimatch. Defaults to False.
    """
    users_to_retire = generate_dummy_users()
    if other_email:
        users_to_retire[0]['email'] = "other@edx.org"
    with open(user_file, 'w', newline='') as file:
        write = csv.writer(file)
        for user in users_to_retire:
            write.writerow(user.values())


def remove_user_file():
    """
    Function to remove user file
    """
    if os.path.exists(user_file):
        os.remove(user_file)


@skip_unless_lms
def test_successful_retire_with_userfile(setup_retirement_states):  # lint-amnesty, pylint: disable=redefined-outer-name, unused-argument
    user = UserFactory.create(username='user0', email="user0@example.com")
    username = user.username
    user_email = user.email
    create_user_file()
    call_command('retire_user', user_file=user_file)
    user = User.objects.get(username=username)
    retired_user_status = UserRetirementStatus.objects.all()[0]
    assert retired_user_status.original_username == username
    assert retired_user_status.original_email == user_email
    # Make sure that we have changed the email address linked to the original user
    assert user.email != user_email
    remove_user_file()


@skip_unless_lms
def test_retire_user_with_usename_email_mismatch(setup_retirement_states):  # lint-amnesty, pylint: disable=redefined-outer-name, unused-argument
    create_user_file(True)
    with pytest.raises(CommandError, match=r'Could not find users with specified username and email '):
        call_command('retire_user', user_file=user_file)
    remove_user_file()


@skip_unless_lms
def test_successful_retire_with_username_email(setup_retirement_states):  # lint-amnesty, pylint: disable=redefined-outer-name, unused-argument
    user = UserFactory.create(username='user0', email="user0@example.com")
    username = user.username
    user_email = user.email
    call_command('retire_user', username=username, user_email=user_email)
    user = User.objects.get(username=username)
    retired_user_status = UserRetirementStatus.objects.all()[0]
    assert retired_user_status.original_username == username
    assert retired_user_status.original_email == user_email
    # Make sure that we have changed the email address linked to the original user
    assert user.email != user_email


@skip_unless_lms
def test_retire_with_username_email_userfile(setup_retirement_states):  # lint-amnesty, pylint: disable=redefined-outer-name, unused-argument
    user = UserFactory.create(username='user0', email="user0@example.com")
    username = user.username
    user_email = user.email
    create_user_file(True)
    with pytest.raises(CommandError, match=r'You cannot use userfile option with username and user_email'):
        call_command('retire_user', user_file=user_file, username=username, user_email=user_email)
    remove_user_file()
