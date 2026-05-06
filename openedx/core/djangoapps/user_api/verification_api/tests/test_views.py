""" Tests for API endpoints. """


import datetime
import json

import freezegun
from django.conf import settings
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.verify_student.models import ManualVerification, SoftwareSecurePhotoVerification
from lms.djangoapps.verify_student.tests.factories import SSOVerificationFactory

FROZEN_TIME = '2015-01-01'
VERIFY_STUDENT = {'DAYS_GOOD_FOR': 365, 'EXPIRING_SOON_WINDOW': 20}


class VerificationViewTestsMixinBase:
    """ Base class for the tests on verification views """
    VIEW_NAME = None
    CREATED_AT = datetime.datetime.strptime(FROZEN_TIME, '%Y-%m-%d')
    PASSWORD = 'Password1234'

    def setUp(self):
        freezer = freezegun.freeze_time(FROZEN_TIME)
        freezer.start()
        self.addCleanup(freezer.stop)
        super().setUp()
        self.user = UserFactory(password=self.PASSWORD)
        self.staff = UserFactory(is_staff=True, password=self.PASSWORD)
        self.photo_verification = SoftwareSecurePhotoVerification.objects.create(user=self.user, status='submitted')
        self.client.login(username=self.staff.username, password=self.PASSWORD)

    @property
    def path(self):
        raise NotImplementedError

    def get_expected_response(self, *args, **kwargs):
        raise NotImplementedError

    def assert_verification_returned(self, verified=False):
        """ Assert the path returns HTTP 200 """
        response = self.client.get(self.path)
        assert response.status_code == 200

        return response

    def test_authentication_required(self):
        """ The endpoint should return HTTP 403 if the user is not authenticated. """
        self.client.logout()
        response = self.client.get(self.path)
        assert response.status_code == 401

    def test_staff_user(self):
        """ The endpoint should be accessible to staff users. """
        self.client.logout()
        self.client.login(username=self.staff.username, password=self.PASSWORD)
        self.assert_verification_returned()

    def test_non_owner_nor_staff_user(self):
        """ The endpoint should NOT be accessible if the request is not made by the submitter or staff user. """
        user = UserFactory()
        self.client.login(username=user.username, password=self.PASSWORD)
        response = self.client.get(self.path)
        assert response.status_code == 403


class VerificationStatusViewTestsMixin(VerificationViewTestsMixinBase):
    """ Base class for the tests on verification status views """

    @property
    def path(self):
        return reverse(self.VIEW_NAME, kwargs={'username': self.user.username})

    def assert_path_not_found(self, path):
        """ Assert the path returns HTTP 404. """
        response = self.client.get(path)
        assert response.status_code == 404

    def get_expected_response(self, *args, **kwargs):
        raise NotImplementedError

    def assert_verification_returned(self, verified=False):
        """ Assert the path returns HTTP 200 and returns appropriately-serialized data. """
        response = super().assert_verification_returned()
        expected_expires = self.CREATED_AT + datetime.timedelta(settings.VERIFY_STUDENT['DAYS_GOOD_FOR'])

        expected = self.get_expected_response(verified=verified, expected_expires=expected_expires)
        assert json.loads(response.content.decode('utf-8')) == expected

    def test_authentication_required(self):
        """ The endpoint should return HTTP 403 if the user is not authenticated. """
        self.client.logout()
        response = self.client.get(self.path)
        assert response.status_code == 401

    def test_no_verifications(self):
        """ The endpoint should return HTTP 404 if the user has no verifications. """
        user = UserFactory()
        path = reverse(self.VIEW_NAME, kwargs={'username': user.username})
        self.assert_path_not_found(path)

    def test_staff_user(self):
        """ The endpoint should be accessible to staff users. """
        self.client.logout()
        self.client.login(username=self.staff.username, password=self.PASSWORD)
        self.assert_verification_returned()

    def test_owner(self):
        """ The endpoint should be accessible to the user who submitted the verification request. """
        self.client.logout()
        self.client.login(username=self.user.username, password=self.PASSWORD)
        self.assert_verification_returned()

    def test_non_owner_nor_staff_user(self):
        """ The endpoint should NOT be accessible if the request is not made by the submitter or staff user. """
        user = UserFactory()
        self.client.login(username=user.username, password=self.PASSWORD)
        response = self.client.get(self.path)
        assert response.status_code == 403

    def test_non_existent_user(self):
        """ The endpoint should return HTTP 404 if the user does not exist. """
        path = reverse(self.VIEW_NAME, kwargs={'username': 'abc123'})
        self.assert_path_not_found(path)


@override_settings(VERIFY_STUDENT=VERIFY_STUDENT)
class PhotoVerificationStatusViewTests(VerificationStatusViewTestsMixin, TestCase):
    """ Tests for the PhotoVerificationStatusView endpoint. """
    VIEW_NAME = 'verification_status'

    def get_expected_response(self, *args, **kwargs):
        verification_status = self.photo_verification.status
        if self.photo_verification.status == 'submitted':
            verification_status = 'pending'
        return {
            'status': verification_status,
            'expiration_datetime': '',
            'is_verified': kwargs.get('verified')
        }

    def test_approved_verification(self):
        """ The endpoint should return that the user is verified if the user's verification is accepted. """
        self.photo_verification.status = 'approved'
        self.photo_verification.save()
        self.client.logout()
        self.client.login(username=self.user.username, password=self.PASSWORD)
        self.assert_verification_returned(verified=True)

    def test_multiple_verifications(self):
        self.photo_verification.status = 'approved'
        self.photo_verification.save()
        SoftwareSecurePhotoVerification.objects.create(user=self.user, status='denied')
        self.client.logout()
        self.client.login(username=self.user.username, password=self.PASSWORD)
        self.assert_verification_returned(verified=True)


@override_settings(VERIFY_STUDENT=VERIFY_STUDENT)
class VerificationsDetailsViewTests(VerificationStatusViewTestsMixin, TestCase):
    """ Tests for the IDVerificationDetails endpoint. """
    VIEW_NAME = 'verification_details'

    def get_expected_response(self, *args, **kwargs):
        return [{
            'type': 'Software Secure',
            'status': self.photo_verification.status,
            'expiration_datetime': '{}Z'.format(kwargs.get('expected_expires').isoformat()),
            'message': '',
            'updated_at': f'{self.CREATED_AT.isoformat()}Z',
            'receipt_id': self.photo_verification.receipt_id,
        }]

    def test_multiple_verification_types(self):
        self.manual_verification = ManualVerification.objects.create(  # pylint: disable=attribute-defined-outside-init
            user=self.user,
            status='approved',
            reason='testing'
        )
        self.sso_verification = SSOVerificationFactory(user=self.user, status='approved')  # pylint: disable=attribute-defined-outside-init
        self.photo_verification.error_msg = 'tested_error'
        self.photo_verification.error_code = 'error_code'
        self.photo_verification.status = 'denied'
        self.photo_verification.save()
        response = self.client.get(self.path)
        assert response.status_code == 200
        expected_expires = self.CREATED_AT + datetime.timedelta(settings.VERIFY_STUDENT['DAYS_GOOD_FOR'])

        expected = [
            {
                'type': 'Software Secure',
                'status': self.photo_verification.status,
                'expiration_datetime': f'{expected_expires.isoformat()}Z',
                'message': self.photo_verification.error_msg,
                'updated_at': f'{self.CREATED_AT.isoformat()}Z',
                'receipt_id': self.photo_verification.receipt_id
            },
            {
                'type': 'SSO',
                'status': self.sso_verification.status,
                'expiration_datetime': f'{expected_expires.isoformat()}Z',
                'message': '',
                'updated_at': f'{self.CREATED_AT.isoformat()}Z',
                'receipt_id': None,
            },
            {
                'type': 'Manual',
                'status': self.manual_verification.status,
                'expiration_datetime': f'{expected_expires.isoformat()}Z',
                'message': self.manual_verification.reason,
                'updated_at': f'{self.CREATED_AT.isoformat()}Z',
                'receipt_id': None,
            },
        ]
        assert json.loads(response.content.decode('utf-8')) == expected

    def test_multiple_verification_instances(self):
        self.sso_verification = SSOVerificationFactory(user=self.user, status='approved')  # pylint: disable=attribute-defined-outside-init
        second_ss_photo_verification = SoftwareSecurePhotoVerification.objects.create(
            user=self.user,
            status='denied',
            error_msg='test error message for denial',
            error_code='plain_code'
        )
        response = self.client.get(self.path)
        assert response.status_code == 200
        expected_expires = self.CREATED_AT + datetime.timedelta(settings.VERIFY_STUDENT['DAYS_GOOD_FOR'])

        expected = [
            {
                'type': 'Software Secure',
                'status': self.photo_verification.status,
                'expiration_datetime': f'{expected_expires.isoformat()}Z',
                'message': self.photo_verification.error_msg,
                'updated_at': f'{self.CREATED_AT.isoformat()}Z',
                'receipt_id': self.photo_verification.receipt_id,
            },
            {
                'type': 'Software Secure',
                'status': second_ss_photo_verification.status,
                'expiration_datetime': f'{expected_expires.isoformat()}Z',
                'message': second_ss_photo_verification.error_msg,
                'updated_at': f'{self.CREATED_AT.isoformat()}Z',
                'receipt_id': second_ss_photo_verification.receipt_id,
            },
            {
                'type': 'SSO',
                'status': self.sso_verification.status,
                'expiration_datetime': f'{expected_expires.isoformat()}Z',
                'message': '',
                'updated_at': f'{self.CREATED_AT.isoformat()}Z',
                'receipt_id': None,
            },
        ]
        assert json.loads(response.content.decode('utf-8')) == expected
