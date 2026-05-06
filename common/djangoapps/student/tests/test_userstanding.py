"""
These are tests for disabling and enabling student accounts, and for making sure
that students with disabled accounts are unable to access the courseware.
"""

from django.test import Client, TestCase
from django.urls import reverse

from common.djangoapps.student.models import UserStanding
from common.djangoapps.student.tests.factories import UserFactory, UserStandingFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms


class UserStandingTest(TestCase):
    """test suite for user standing view for enabling and disabling accounts"""

    def setUp(self):
        super().setUp()
        # create users
        self.bad_user = UserFactory.create(
            username='bad_user',
        )
        self.good_user = UserFactory.create(
            username='good_user',
        )
        self.non_staff = UserFactory.create(
            username='non_staff',
        )
        self.admin = UserFactory.create(
            username='admin',
            is_staff=True,
        )

        # create clients
        self.bad_user_client = Client()
        self.good_user_client = Client()
        self.non_staff_client = Client()
        self.admin_client = Client()

        for user, client in [
            (self.bad_user, self.bad_user_client),
            (self.good_user, self.good_user_client),
            (self.non_staff, self.non_staff_client),
            (self.admin, self.admin_client),
        ]:
            client.login(username=user.username, password='Password1234')

        UserStandingFactory.create(
            user=self.bad_user,
            account_status=UserStanding.ACCOUNT_DISABLED,
            changed_by=self.admin
        )

        # set stock url to test disabled accounts' access to site
        self.some_url = '/'

        # since it's only possible to disable accounts from lms, we're going
        # to skip tests for cms

    @skip_unless_lms
    def test_can_access_manage_account_page(self):
        response = self.admin_client.get(reverse('manage_user_standing'), {
            'user': self.admin,
        })
        assert response.status_code == 200

    @skip_unless_lms
    def test_disable_account(self):
        assert UserStanding.objects.filter(user=self.good_user).count() == 0
        response = self.admin_client.post(reverse('disable_account_ajax'), {  # pylint: disable=unused-variable  # noqa: F841
            'username': self.good_user.username,
            'account_action': 'disable',
        })
        assert UserStanding.objects.get(user=self.good_user).account_status == UserStanding.ACCOUNT_DISABLED

    def test_disabled_account_403s(self):
        response = self.bad_user_client.get(self.some_url)
        assert response.status_code == 403

    @skip_unless_lms
    def test_reenable_account(self):
        response = self.admin_client.post(reverse('disable_account_ajax'), {  # pylint: disable=unused-variable  # noqa: F841
            'username': self.bad_user.username,
            'account_action': 'reenable'
        })
        assert UserStanding.objects.get(user=self.bad_user).account_status == UserStanding.ACCOUNT_ENABLED

    @skip_unless_lms
    def test_non_staff_cant_access_disable_view(self):
        response = self.non_staff_client.get(reverse('manage_user_standing'), {
            'user': self.non_staff,
        })
        assert response.status_code == 404

    @skip_unless_lms
    def test_non_staff_cant_disable_account(self):
        response = self.non_staff_client.post(reverse('disable_account_ajax'), {
            'username': self.good_user.username,
            'user': self.non_staff,
            'account_action': 'disable'
        })
        assert response.status_code == 404
        assert UserStanding.objects.filter(user=self.good_user).count() == 0
