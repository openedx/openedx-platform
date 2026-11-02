"""
Certificate Manager.
"""
import json
import logging

from django.conf import settings
from django.utils.translation import gettext as _

from common.djangoapps.course_modes.models import CourseMode
from eventtracking import tracker
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import AssetKey
from .assets import delete_asset

from common.djangoapps.util.db import MYSQL_MAX_INT, generate_int_id

from ..exceptions import AssetNotFoundException

CERTIFICATE_SCHEMA_VERSION = 1
CERTIFICATE_MINIMUM_ID = 100

LOGGER = logging.getLogger(__name__)


class CertificateException(Exception):
    """
    Base exception for Certificates workflows
    """
    pass  # lint-amnesty, pylint: disable=unnecessary-pass


class CertificateValidationError(CertificateException):
    """
    An exception raised when certificate information is invalid.
    """
    pass  # lint-amnesty, pylint: disable=unnecessary-pass


def _delete_asset(course_key, asset_key_string):
    """
    Internal method used to create asset key from string and
    remove asset by calling delete_asset method of assets module.
    """
    if asset_key_string:
        try:
            asset_key = AssetKey.from_string(asset_key_string)
        except InvalidKeyError:
            # remove first slash in asset path
            # otherwise it generates InvalidKeyError in case of split modulestore
            if '/' == asset_key_string[0]:
                asset_key_string = asset_key_string[1:]
                try:
                    asset_key = AssetKey.from_string(asset_key_string)
                except InvalidKeyError:
                    # Unable to parse the asset key, log and return
                    LOGGER.info(
                        "In course %r, unable to parse asset key %r, not attempting to delete signatory.",
                        course_key,
                        asset_key_string,
                    )
                    return
            else:
                # Unable to parse the asset key, log and return
                LOGGER.info(
                    "In course %r, unable to parse asset key %r, not attempting to delete signatory.",
                    course_key,
                    asset_key_string,
                )
                return

        try:
            delete_asset(course_key, asset_key)
        # If the asset was not found, it doesn't have to be deleted...
        except AssetNotFoundException:
            pass


class CertificateManager:
    """
    The CertificateManager is responsible for storage, retrieval, and manipulation of Certificates
    Certificates are not stored in the Django ORM, they are a field/setting on the course block
    """
    @staticmethod
    def parse(json_string):
        """
        Deserialize the provided JSON data into a standard Python object
        """
        try:
            certificate = json.loads(json_string)
        except ValueError:
            raise CertificateValidationError(_("invalid JSON"))  # lint-amnesty, pylint: disable=raise-missing-from
        # Include the data contract version
        certificate["version"] = CERTIFICATE_SCHEMA_VERSION
        # Ensure a signatories list is always returned
        if certificate.get("signatories") is None:
            certificate["signatories"] = []
        certificate["editing"] = False
        return certificate

    @staticmethod
    def validate(certificate_data):
        """
        Ensure the certificate data contains all of the necessary fields and the values match our rules
        """
        # Ensure the schema version meets our expectations
        if certificate_data.get("version") != CERTIFICATE_SCHEMA_VERSION:
            raise TypeError(
                "Unsupported certificate schema version: {}.  Expected version: {}.".format(
                    certificate_data.get("version"),
                    CERTIFICATE_SCHEMA_VERSION
                )
            )
        if not certificate_data.get("name"):
            raise CertificateValidationError(_("must have name of the certificate"))

    @staticmethod
    def is_activated(course):
        """
        Returns whether certificates are activated for the given course,
        along with the certificates.
        """
        is_active = False
        certificates = []
        if settings.FEATURES.get('CERTIFICATES_HTML_VIEW', False):
            certificates = CertificateManager.get_certificates(course)
            # we are assuming only one certificate in certificates collection.
            for certificate in certificates:
                is_active = certificate.get('is_active', False)
                break
        return is_active, certificates

    @staticmethod
    def get_used_ids(course):
        """
        Return a list of certificate identifiers that are already in use for this course
        """
        if not course.certificates or not course.certificates.get('certificates'):
            return []
        return [cert['id'] for cert in course.certificates['certificates']]

    @staticmethod
    def assign_id(course, certificate_data, certificate_id=None):
        """
        Assign an identifier to the provided certificate data.
        If the caller did not provide an identifier, we autogenerate a unique one for them
        In addition, we check the certificate's signatories and ensure they also have unique ids
        """
        used_ids = CertificateManager.get_used_ids(course)
        if certificate_id:
            certificate_data['id'] = int(certificate_id)
        else:
            certificate_data['id'] = generate_int_id(
                CERTIFICATE_MINIMUM_ID,
                MYSQL_MAX_INT,
                used_ids
            )

        for index, signatory in enumerate(certificate_data['signatories']):  # pylint: disable=unused-variable
            if signatory and not signatory.get('id', False):
                signatory['id'] = generate_int_id(used_ids=used_ids)
            used_ids.append(signatory['id'])

        return certificate_data

    @staticmethod
    def serialize_certificate(certificate):
        """
        Serialize the Certificate object's locally-stored certificate data to a JSON representation
        We use direct access here for specific keys in order to enforce their presence
        """
        certificate_data = certificate.certificate_data
        certificate_response = {
            "id": certificate_data['id'],
            "name": certificate_data['name'],
            "description": certificate_data['description'],
            "is_active": certificate_data['is_active'],
            "version": CERTIFICATE_SCHEMA_VERSION,
            "signatories": certificate_data['signatories']
        }

        # Some keys are not required, such as the title override...
        if certificate_data.get('course_title'):
            certificate_response["course_title"] = certificate_data['course_title']

        return certificate_response

    @staticmethod
    def deserialize_certificate(course, value):
        """
        Deserialize from a JSON representation into a Certificate object.
        'value' should be either a Certificate instance, or a valid JSON string
        """
        if isinstance(value, bytes):
            value = value.decode('utf-8')

        # Ensure the schema fieldset meets our expectations
        for key in ("name", "description", "version"):
            if key not in value:
                raise CertificateValidationError(_("Certificate dict {0} missing value key '{1}'").format(value, key))

        # Load up the Certificate data
        certificate_data = CertificateManager.parse(value)
        CertificateManager.validate(certificate_data)
        certificate_data = CertificateManager.assign_id(course, certificate_data, certificate_data.get('id', None))
        certificate = Certificate(course, certificate_data)

        # Return a new Certificate object instance
        return certificate

    @staticmethod
    def get_certificates(course, only_active=False):
        """
        Retrieve the certificates list from the provided course,
        if `only_active` is True it would skip inactive certificates.
        """
        # The top-level course field is 'certificates', which contains various properties,
        # including the actual 'certificates' list that we're working with in this context
        certificates = course.certificates.get('certificates', [])
        if only_active:
            certificates = [certificate for certificate in certificates if certificate.get('is_active', False)]
        return certificates

    @staticmethod
    def get_course_modes(course):
        """
        Retrieve certificate modes for the given course,
        including expired modes but excluding audit mode.
        """
        course_modes = [
            mode.slug for mode in CourseMode.modes_for_course(
                course=course, include_expired=True
            ) if mode.slug != CourseMode.AUDIT
        ]
        return course_modes

    @staticmethod
    def is_enabled(course):
        """
        Is enabled when there is at least one course mode for the given course,
        including expired modes but excluding audit mode
        """
        course_modes = CertificateManager.get_course_modes(course)
        return len(course_modes) > 0

    @staticmethod
    def remove_certificate(request, store, course, certificate_id):
        """
        Remove certificate from the course
        """
        for index, cert in enumerate(course.certificates['certificates']):
            if int(cert['id']) == int(certificate_id):
                certificate = course.certificates['certificates'][index]
                # Remove any signatory assets prior to dropping the entire cert record from the course
                for sig_index, signatory in enumerate(certificate.get('signatories')):  # pylint: disable=unused-variable
                    _delete_asset(course.id, signatory['signature_image_path'])
                # Now drop the certificate record
                course.certificates['certificates'].pop(index)
                store.update_item(course, request.user.id)
                break

    # pylint-disable: unused-variable
    @staticmethod
    def remove_signatory(request, store, course, certificate_id, signatory_id):
        """
        Remove the specified signatory from the provided course certificate
        """
        for cert_index, cert in enumerate(course.certificates['certificates']):  # pylint: disable=unused-variable
            if int(cert['id']) == int(certificate_id):
                for sig_index, signatory in enumerate(cert.get('signatories')):
                    if int(signatory_id) == int(signatory['id']):
                        _delete_asset(course.id, signatory['signature_image_path'])
                        del cert['signatories'][sig_index]
                        store.update_item(course, request.user.id)
                        break

    @staticmethod
    def track_event(event_name, event_data):
        """Track certificate configuration event.

        Arguments:
            event_name (str):  Name of the event to be logged.
            event_data (dict): A Dictionary containing event data
        Returns:
            None

        """
        event_name = '.'.join(['edx', 'certificate', 'configuration', event_name])
        tracker.emit(event_name, event_data)


class Certificate:
    """
    The logical representation of an individual course certificate
    """
    def __init__(self, course, certificate_data):
        """
        Instantiate a Certificate object instance using the provided information.
        """
        self.course = course
        self._certificate_data = certificate_data
        self.id = certificate_data['id']  # pylint: disable=invalid-name

    @property
    def certificate_data(self):
        """
        Retrieve the locally-stored certificate data from the Certificate object via a helper method
        """
        return self._certificate_data
