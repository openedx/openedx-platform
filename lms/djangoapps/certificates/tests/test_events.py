"""
Test classes for the events sent in the certification process.

Classes:
    CertificateEventTest: Test event sent after creating, changing or deleting
    certificates.
"""
from unittest.mock import Mock

from openedx_events.learning.data import CertificateData, CourseData, UserData, UserPersonalData
from openedx_events.learning.signals import CERTIFICATE_CHANGED, CERTIFICATE_CREATED, CERTIFICATE_REVOKED
from openedx_events.tests.utils import OpenEdxEventsTestMixin

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
from lms.djangoapps.certificates.models import GeneratedCertificate, CertificateStatuses
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms

from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from common.test.utils import assert_dict_contains_subset


@skip_unless_lms
class CertificateEventTest(SharedModuleStoreTestCase, OpenEdxEventsTestMixin):
    """
    Tests for the Open edX Events associated with the student's certification
    process.

    This class guarantees that the following events are sent during the user's
    certification process, with the exact Data Attributes as the event definition stated:

        - CERTIFICATE_CREATED: after the user's certificate generation has been
        completed.
        - CERTIFICATE_CHANGED: after the certificate update has been completed.
        - CERTIFICATE_REVOKED: after the certificate revocation has been completed.
    """

    ENABLED_OPENEDX_EVENTS = [
        "org.openedx.learning.certificate.created.v1",
        "org.openedx.learning.certificate.changed.v1",
        "org.openedx.learning.certificate.revoked.v1",
    ]

    @classmethod
    def setUpClass(cls):
        """
        Set up class method for the Test class.

        This method starts manually events isolation. Explanation here:
        openedx/core/djangoapps/user_authn/views/tests/test_events.py#L44
        """
        super().setUpClass()
        cls.start_events_isolation()

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.course = CourseOverviewFactory()
        self.user = UserFactory.create(
            username="somestudent",
            first_name="Student",
            last_name="Person",
            email="robot@robot.org",
            is_active=True
        )
        self.receiver_called = False

    def _event_receiver_side_effect(self, **kwargs):  # pylint: disable=unused-argument
        """
        Used show that the Open edX Event was called by the Django signal handler.
        """
        self.receiver_called = True

    def test_send_certificate_created_event(self):
        """
        Test whether the certificate created event is sent at the end of the
        certificate creation process.

        Expected result:
            - CERTIFICATE_CREATED is sent and received by the mocked receiver.
            - The arguments that the receiver gets are the arguments sent by the event
            except the metadata generated on the fly.
        """
        event_receiver = Mock(side_effect=self._event_receiver_side_effect)
        CERTIFICATE_CREATED.connect(event_receiver)

        certificate = GeneratedCertificateFactory.create(
            status=CertificateStatuses.downloadable,
            user=self.user,
            course_id=self.course.id,
            mode=GeneratedCertificate.MODES.honor,
            name="Certificate",
            grade="100",
            download_url="https://certificate.pdf"
        )

        self.assertTrue(self.receiver_called)
        assert_dict_contains_subset(
            self,
            {
                "signal": CERTIFICATE_CREATED,
                "sender": None,
                "certificate": CertificateData(
                    user=UserData(
                        pii=UserPersonalData(
                            username=certificate.user.username,
                            email=certificate.user.email,
                            name=certificate.user.profile.name,
                        ),
                        id=certificate.user.id,
                        is_active=certificate.user.is_active,
                    ),
                    course=CourseData(
                        course_key=certificate.course_id,
                    ),
                    mode=certificate.mode,
                    grade=certificate.grade,
                    current_status=certificate.status,
                    download_url=certificate.download_url,
                    name=certificate.name,
                ),
            },
            event_receiver.call_args.kwargs,
        )

    def test_send_certificate_changed_event(self):
        """
        Test whether the certificate changed event is sent at the end of the
        certificate update process.

        Expected result:
            - CERTIFICATE_CHANGED is sent and received by the mocked receiver.
            - The arguments that the receiver gets are the arguments sent by the event
            except the metadata generated on the fly.
        """
        event_receiver = Mock(side_effect=self._event_receiver_side_effect)
        CERTIFICATE_CHANGED.connect(event_receiver)
        certificate = GeneratedCertificateFactory.create(
            status=CertificateStatuses.downloadable,
            user=self.user,
            course_id=self.course.id,
            mode=GeneratedCertificate.MODES.honor,
            name="Certificate",
            grade="100",
            download_url="https://certificate.pdf"
        )

        certificate.grade = "50"
        certificate.save()

        self.assertTrue(self.receiver_called)
        assert_dict_contains_subset(
            self,
            {
                "signal": CERTIFICATE_CHANGED,
                "sender": None,
                "certificate": CertificateData(
                    user=UserData(
                        pii=UserPersonalData(
                            username=certificate.user.username,
                            email=certificate.user.email,
                            name=certificate.user.profile.name,
                        ),
                        id=certificate.user.id,
                        is_active=certificate.user.is_active,
                    ),
                    course=CourseData(
                        course_key=certificate.course_id,
                    ),
                    mode=certificate.mode,
                    grade=certificate.grade,
                    current_status=certificate.status,
                    download_url=certificate.download_url,
                    name=certificate.name,
                ),
            },
            event_receiver.call_args.kwargs,
        )

    def test_send_certificate_revoked_event(self):
        """
        Test whether the certificate revoked event is sent at the end of the
        user certificate's revoking process.

        Expected result:
            - CERTIFICATE_REVOKED is sent and received by the mocked receiver.
            - The arguments that the receiver gets are the arguments sent by the event
            except the metadata generated on the fly.
        """
        event_receiver = Mock(side_effect=self._event_receiver_side_effect)
        CERTIFICATE_REVOKED.connect(event_receiver)
        certificate = GeneratedCertificateFactory.create(
            status=CertificateStatuses.downloadable,
            user=self.user,
            course_id=self.course.id,
            mode=GeneratedCertificate.MODES.honor,
            name="Certificate",
            grade="100",
            download_url="https://certificate.pdf"
        )

        certificate.invalidate()

        self.assertTrue(self.receiver_called)
        assert_dict_contains_subset(
            self,
            {
                "signal": CERTIFICATE_REVOKED,
                "sender": None,
                "certificate": CertificateData(
                    user=UserData(
                        pii=UserPersonalData(
                            username=certificate.user.username,
                            email=certificate.user.email,
                            name=certificate.user.profile.name,
                        ),
                        id=certificate.user.id,
                        is_active=certificate.user.is_active,
                    ),
                    course=CourseData(
                        course_key=certificate.course_id,
                    ),
                    mode=certificate.mode,
                    grade=certificate.grade,
                    current_status=certificate.status,
                    download_url=certificate.download_url,
                    name=certificate.name,
                ),
            },
            event_receiver.call_args.kwargs,
        )
