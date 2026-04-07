"""
Tests for the django management command `change_enterprise_user_username`.
"""


from unittest import mock

from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.db.models.signals import post_save
from django.test import TestCase
from enterprise.models import EnterpriseCustomer, EnterpriseCustomerUser
from pytest import mark

from common.djangoapps.student.tests.factories import UserFactory


@mark.django_db
class ChangeEnterpriseUserUsernameCommandTests(TestCase):
    """
    Test command `change_enterprise_user_username`.
    """
    command = 'change_enterprise_user_username'

    @mock.patch('common.djangoapps.student.management.commands.change_enterprise_user_username.LOGGER')
    def test_user_not_enterprise(self, logger_mock):
        """
        Test that the command does not update a user's username if it is not linked to an Enterprise.
        """
        user = UserFactory.create(is_active=True, username='old_username', email='test@example.com')
        new_username = 'new_username'

        post_save_handler = mock.MagicMock()
        post_save.connect(post_save_handler, sender=User)

        call_command(self.command, user_id=user.id, new_username=new_username)

        logger_mock.info.assert_called_with(f'User {user.id} must be an Enterprise User.')
        post_save_handler.assert_not_called()

    @mock.patch('common.djangoapps.student.management.commands.change_enterprise_user_username.LOGGER')
    def test_username_updated_successfully(self, logger_mock):
        """
        Test that the command updates the user's username when the user is linked to an Enterprise.
        """
        user = UserFactory.create(is_active=True, username='old_username', email='test@example.com')
        site, _ = Site.objects.get_or_create(domain='example.com')
        enterprise_customer = EnterpriseCustomer.objects.create(
            name='Test EnterpriseCustomer',
            site=site
        )
        EnterpriseCustomerUser.objects.create(
            user_id=user.id,
            enterprise_customer=enterprise_customer
        )
        new_username = 'new_username'

        post_save_handler = mock.MagicMock()
        post_save.connect(post_save_handler, sender=User)

        call_command(self.command, user_id=user.id, new_username=new_username)

        logger_mock.info.assert_called_with(f'User {user.id} has been updated with username {new_username}.')
        post_save_handler.assert_called()

        updated_user = User.objects.get(id=user.id)
        assert updated_user.username == new_username
