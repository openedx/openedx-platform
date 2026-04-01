"""
Course certificates are created for a student and an offering of a course (a course run).
"""

import json
import logging
import os
import uuid
from datetime import timezone

from config_models.models import ConfigurationModel
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Count
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _
from model_utils import Choices
from model_utils.models import TimeStampedModel
from opaque_keys.edx.django.models import CourseKeyField
from simple_history.models import HistoricalRecords

from common.djangoapps.student import models_api as student_api
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.util.milestones_helpers import fulfill_course_milestone, is_prerequisite_courses_enabled
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.instructor_task.models import InstructorTask
from openedx.core.djangoapps.signals.signals import COURSE_CERT_AWARDED, COURSE_CERT_CHANGED, COURSE_CERT_REVOKED
from openedx.core.djangoapps.xmodule_django.models import NoneToEmptyManager
from openedx.features.name_affirmation_api.utils import get_name_affirmation_service

from openedx_events.learning.data import CourseData, UserData, UserPersonalData, CertificateData  # lint-amnesty, pylint: disable=wrong-import-order
from openedx_events.learning.signals import CERTIFICATE_CHANGED, CERTIFICATE_CREATED, CERTIFICATE_REVOKED  # lint-amnesty, pylint: disable=wrong-import-order

log = logging.getLogger(__name__)
User = get_user_model()


class CertificateSocialNetworks:
    """
    Enum for certificate social networks
    """
    linkedin = 'LinkedIn'
    facebook = 'Facebook'
    twitter = 'Twitter'


class CertificateAllowlist(TimeStampedModel):
    """
    Tracks students who are on the certificate allowlist for a given course run.

    .. no_pii:
    """
    class Meta:
        app_label = "certificates"
        unique_together = [['course_id', 'user']]

    objects = NoneToEmptyManager()

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    course_id = CourseKeyField(blank=True, default=None)
    allowlist = models.BooleanField(default=0)
    notes = models.TextField(default=None, null=True)

    # This is necessary because CMS does not install the certificates app, but it
    # imports this model's code. Simple History will attempt to connect to the installed
    # model in the certificates app, which will fail.
    if 'certificates' in apps.app_configs:
        history = HistoricalRecords()

    @classmethod
    def get_certificate_allowlist(cls, course_id, student=None):
        """
        Return the certificate allowlist for the given course as a list of dict objects
        with the following key-value pairs:

        [{
            id:         'id (pk) of CertificateAllowlist item'
            user_id:    'User Id of the student'
            user_name:  'name of the student'
            user_email: 'email of the student'
            course_id:  'Course key of the course to whom certificate exception belongs'
            created:    'Creation date of the certificate exception'
            notes:      'Additional notes for the certificate exception'
        }, {...}, ...]

        """
        allowlist = cls.objects.filter(course_id=course_id, allowlist=True)
        if student:
            allowlist = allowlist.filter(user=student)
        result = []
        generated_certificates = GeneratedCertificate.eligible_certificates.filter(
            course_id=course_id,
            user__in=[allowlist_item.user for allowlist_item in allowlist],
            status=CertificateStatuses.downloadable
        )
        generated_certificates = {
            certificate['user']: certificate['created_date']
            for certificate in generated_certificates.values('user', 'created_date')
        }

        for item in allowlist:
            certificate_generated = generated_certificates.get(item.user.id, '')
            result.append({
                'id': item.id,
                'user_id': item.user.id,
                'user_name': str(item.user.username),
                'user_email': str(item.user.email),
                'course_id': str(item.course_id),
                'created': item.created.strftime("%B %d, %Y"),
                'certificate_generated': certificate_generated and certificate_generated.strftime("%B %d, %Y"),
                'notes': str(item.notes or ''),
            })
        return result


class EligibleCertificateManager(models.Manager):
    """
    A manager for `GeneratedCertificate` models that automatically
    filters out ineligible certs.

    The idea is to prevent accidentally granting certificates to
    students who have not enrolled in a cert-granting mode. The
    alternative is to filter by eligible_for_certificate=True every
    time certs are searched for, which is verbose and likely to be
    forgotten.
    """

    def get_queryset(self):
        """
        Return a queryset for `GeneratedCertificate` models, filtering out
        ineligible certificates.
        """
        return super().get_queryset().exclude(
            status__in=(CertificateStatuses.audit_passing, CertificateStatuses.audit_notpassing)
        )


class EligibleAvailableCertificateManager(EligibleCertificateManager):
    """
    A manager for `GeneratedCertificate` models that automatically
    filters out ineligible certs and any linked to nonexistent courses.

    Adds to the super class filtering to also exclude certificates for
    courses that do not have a corresponding CourseOverview.
    """

    def get_queryset(self):
        """
        Return a queryset for `GeneratedCertificate` models, filtering out
        ineligible certificates and any linked to nonexistent courses.
        """
        return super().get_queryset().extra(
            tables=['course_overviews_courseoverview'],
            where=['course_id = course_overviews_courseoverview.id']
        )


class GeneratedCertificate(models.Model):
    """
    Base model for generated course certificates

    .. pii: PII can exist in the generated certificate linked to in this model. Certificate data is currently retained.
    .. pii_types: name, username
    .. pii_retirement: retained

    course_id       - Course run key
    created_date    - Date and time the certificate was created
    distinction     - Indicates whether the user passed the course with distinction. Currently unused.
    download_url    - URL where the PDF version of the certificate, if any, can be found
    download_uuid   - UUID associated with a PDF certificate
    error_reason    - Reason a PDF certificate could not be created
    grade           - User's grade in this course run. This grade is set at the same time as the status. This
                    GeneratedCertificate grade is *not* updated whenever the user's course grade changes and so it
                    should not be considered the source of truth. It is suggested that the PersistentCourseGrade be
                    used instead of the GeneratedCertificate grade.
    key             - Certificate identifier, used for PDF certificates
    mode            - Course run mode (ex. verified)
    modified_date   - Date and time the certificate was last modified
    name            - User's name
    status          - Certificate status value; see the CertificateStatuses model
    user            - User associated with the certificate
    verify_uuid     - Unique identifier for the certificate
    """
    # Import here instead of top of file since this module gets imported before
    # the course_modes app is loaded, resulting in a Django deprecation warning.
    from common.djangoapps.course_modes.models import CourseMode  # pylint: disable=reimported

    # Normal object manager, which should only be used when ineligible
    # certificates (i.e. new audit certs) should be included in the
    # results. Django requires us to explicitly declare this.
    objects = models.Manager()

    # Only returns eligible certificates. This should be used in
    # preference to the default `objects` manager in most cases.
    eligible_certificates = EligibleCertificateManager()

    # Only returns eligible certificates for courses that have an
    # associated CourseOverview
    eligible_available_certificates = EligibleAvailableCertificateManager()

    MODES = Choices(
        'verified',
        'honor',
        'audit',
        'professional',
        'no-id-professional',
        'masters',
        'executive-education',
        'paid-executive-education',
        'paid-bootcamp',
    )

    VERIFIED_CERTS_MODES = [
        CourseMode.VERIFIED, CourseMode.CREDIT_MODE, CourseMode.MASTERS, CourseMode.EXECUTIVE_EDUCATION,
        CourseMode.PAID_EXECUTIVE_EDUCATION, CourseMode.PAID_BOOTCAMP
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    course_id = CourseKeyField(blank=True, default=None)
    verify_uuid = models.CharField(max_length=32, blank=True, default='', db_index=True)
    grade = models.CharField(max_length=5, blank=True, default='')
    key = models.CharField(max_length=32, blank=True, default='')
    distinction = models.BooleanField(default=False)
    status = models.CharField(max_length=32, default='unavailable')
    mode = models.CharField(max_length=32, choices=MODES, default=MODES.honor)
    name = models.CharField(blank=True, max_length=255)
    created_date = models.DateTimeField(auto_now_add=True)
    modified_date = models.DateTimeField(auto_now=True)

    # These fields have been kept around even though they are not used.
    # See lms/djangoapps/certificates/docs/decisions/008-certificate-model-remnants.rst for the ADR.
    download_uuid = models.CharField(max_length=32, blank=True, default='')
    download_url = models.CharField(max_length=128, blank=True, default='')
    error_reason = models.CharField(max_length=512, blank=True, default='')

    # This is necessary because CMS does not install the certificates app, but it
    # imports this model's code. Simple History will attempt to connect to the installed
    # model in the certificates app, which will fail.
    if 'certificates' in apps.app_configs:
        history = HistoricalRecords()

    class Meta:
        unique_together = (('user', 'course_id'),)
        app_label = "certificates"

    @classmethod
    def certificate_for_student(cls, student, course_id):
        """
        This returns the certificate for a student for a particular course
        or None if no such certificate exits.
        """
        try:
            return cls.objects.get(user=student, course_id=course_id)
        except cls.DoesNotExist:
            pass

        return None

    @classmethod
    def course_ids_with_certs_for_user(cls, user):
        """
        Return a set of CourseKeys for which the user has certificates.

        Sometimes we just want to check if a user has already been issued a
        certificate for a given course (e.g. to test refund eligibility).
        Instead of checking if `certificate_for_student` returns `None` on each
        course_id individually, we instead just return a set of all CourseKeys
        for which this student has certificates all at once.
        """
        return {
            cert.course_id
            for cert
            in cls.objects.filter(user=user).only('course_id')
        }

    @classmethod
    def get_unique_statuses(cls, course_key=None, flat=False):
        """
        1 - Return unique statuses as a list of dictionaries containing the following key value pairs
            [
            {'status': 'status value from db', 'count': 'occurrence count of the status'},
            {...},
            ..., ]

        2 - if flat is 'True' then return unique statuses as a list
        3 - if course_key is given then return unique statuses associated with the given course

        :param course_key: Course Key identifier
        :param flat: boolean showing whether to return statuses as a list of values or a list of dictionaries.
        """
        query = cls.objects

        if course_key:
            query = query.filter(course_id=course_key)

        if flat:
            return query.values_list('status', flat=True).distinct()
        else:
            return query.values('status').annotate(count=Count('status'))

    def __repr__(self):
        return "<GeneratedCertificate: {course_id}, user={user}>".format(
            course_id=self.course_id,
            user=self.user
        )

    def invalidate(self, mode=None, source=None):
        """
        Invalidate Generated Certificate by marking it 'unavailable'. For additional information see the
        `_revoke_certificate()` function.

        Args:
            mode (String) - learner's current enrollment mode. May be none as the caller likely does not need to
                evaluate the mode before deciding to invalidate the cert.
            source (String) - source requesting invalidation of the certificate for tracking purposes
        """
        if not mode:
            mode, __ = CourseEnrollment.enrollment_mode_for_user(self.user, self.course_id)

        log.info(f'Marking certificate as unavailable for {self.user.id} : {self.course_id} with mode {mode} from '
                 f'source {source}')
        self._revoke_certificate(status=CertificateStatuses.unavailable, mode=mode, source=source)

    def mark_notpassing(self, mode, grade, source=None):
        """
        Invalidates a Generated Certificate by marking it as 'notpassing'. For additional information see the
        `_revoke_certificate()` function.

        Args:
            mode (String) - learner's current enrollment mode
            grade (float) - snapshot of the learner's current grade as a decimal
            source (String) - source requesting invalidation of the certificate for tracking purposes
        """
        log.info(f'Marking certificate as notpassing for {self.user.id} : {self.course_id} with mode {mode} from '
                 f'source {source}')
        self._revoke_certificate(status=CertificateStatuses.notpassing, mode=mode, grade=grade, source=source)

    def mark_unverified(self, mode, source=None):
        """
        Invalidates a Generated Certificate by marking it as 'unverified'. For additional information see the
        `_revoke_certificate()` function.

        Args:
            mode (String) - learner's current enrollment mode
            source (String) - source requesting invalidation of the certificate for tracking purposes
        """
        log.info(f'Marking certificate as unverified for {self.user.id} : {self.course_id} with mode {mode} from '
                 f'source {source}')
        self._revoke_certificate(status=CertificateStatuses.unverified, mode=mode, source=source)

    def _revoke_certificate(self, status, mode=None, grade=None, source=None):
        """
        Revokes a course certificate from a learner, updating the certificate's status as specified by the value of the
        `status` argument. This will prevent the learner from being able to access their certificate in the associated
        course run.

        We remove the `download_uuid` and the `download_url` as well, but this is only important to PDF certificates.

        Invalidating a certificate fires the `COURSE_CERT_REVOKED` signal. This kicks off a task to determine if there
        are any program certificates that also need to be revoked from the learner.

        If the certificate had a status of `downloadable` before being revoked then we will also emit an
        `edx.certificate.revoked` event for tracking purposes.

        Args:
            status (CertificateStatus) - certificate status to set for the `GeneratedCertificate` record
            mode (String) - learner's current enrollment mode
            grade (float) - snapshot of the learner's current grade as a decimal
            source (String) - source requesting invalidation of the certificate for tracking purposes
        """
        previous_certificate_status = self.status

        if not grade:
            grade = ''
        # the grade can come through revocation as a float, so we must convert it to a string to be compatible with the
        # `CERTIFICATE_REVOKED` event definition
        elif isinstance(grade, float):
            grade = str(grade)

        if not mode:
            mode = self.mode

        preferred_name = self._get_preferred_certificate_name(self.user)

        self.error_reason = ''
        self.download_uuid = ''
        self.download_url = ''
        self.grade = grade
        self.status = status
        self.mode = mode
        self.name = preferred_name
        self.save()

        COURSE_CERT_REVOKED.send_robust(
            sender=self.__class__,
            user=self.user,
            course_key=self.course_id,
            mode=self.mode,
            status=self.status,
        )

        # .. event_implemented_name: CERTIFICATE_REVOKED
        # .. event_type: org.openedx.learning.certificate.revoked.v1
        CERTIFICATE_REVOKED.send_event(
            time=self.modified_date.astimezone(timezone.utc),
            certificate=CertificateData(
                user=UserData(
                    pii=UserPersonalData(
                        username=self.user.username,
                        email=self.user.email,
                        name=self.user.profile.name,
                    ),
                    id=self.user.id,
                    is_active=self.user.is_active,
                ),
                course=CourseData(
                    course_key=self.course_id,
                ),
                mode=self.mode,
                grade=self.grade,
                current_status=self.status,
                download_url=self.download_url,
                name=self.name,
            )
        )

        if previous_certificate_status == CertificateStatuses.downloadable:
            # imported here to avoid a circular import issue
            from lms.djangoapps.certificates.utils import emit_certificate_event

            event_data = {
                'user_id': self.user.id,
                'course_id': str(self.course_id),
                'certificate_id': self.verify_uuid,
                'enrollment_mode': self.mode,
                'source': source or '',
            }
            emit_certificate_event('revoked', self.user, str(self.course_id), event_data=event_data)

    def _get_preferred_certificate_name(self, user):
        """
        Copy of `get_preferred_certificate_name` from utils.py - importing it here would introduce
        a circular dependency.
        """
        name_to_use = student_api.get_name(user.id)
        name_affirmation_service = get_name_affirmation_service()

        if name_affirmation_service and name_affirmation_service.should_use_verified_name_for_certs(user):
            verified_name_obj = name_affirmation_service.get_verified_name(user, is_verified=True)
            if verified_name_obj:
                name_to_use = verified_name_obj.verified_name

        if not name_to_use:
            name_to_use = ''

        return name_to_use

    def is_valid(self):
        """
        Return True if certificate is valid else return False.
        """
        return self.status == CertificateStatuses.downloadable

    def save(self, *args, **kwargs):  # pylint: disable=signature-differs
        """
        After the base save() method finishes, fire the COURSE_CERT_CHANGED signal. If the learner is currently passing
        the course we also fire the COURSE_CERT_AWARDED signal.

        The COURSE_CERT_CHANGED signal helps determine if a Course Certificate can be awarded to a learner in the
        Credentials IDA.

        The COURSE_CERT_AWARDED signal helps determine if a Program Certificate can be awarded to a learner in the
        Credentials IDA.
        """
        super().save(*args, **kwargs)

        timestamp = self.modified_date.astimezone(timezone.utc)

        COURSE_CERT_CHANGED.send_robust(
            sender=self.__class__,
            user=self.user,
            course_key=self.course_id,
            mode=self.mode,
            status=self.status,
        )

        # .. event_implemented_name: CERTIFICATE_CHANGED
        # .. event_type: org.openedx.learning.certificate.changed.v1
        CERTIFICATE_CHANGED.send_event(
            time=timestamp,
            certificate=CertificateData(
                user=UserData(
                    pii=UserPersonalData(
                        username=self.user.username,
                        email=self.user.email,
                        name=self.user.profile.name,
                    ),
                    id=self.user.id,
                    is_active=self.user.is_active,
                ),
                course=CourseData(
                    course_key=self.course_id,
                ),
                mode=self.mode,
                grade=self.grade,
                current_status=self.status,
                download_url=self.download_url,
                name=self.name,
            )
        )

        if CertificateStatuses.is_passing_status(self.status):
            COURSE_CERT_AWARDED.send_robust(
                sender=self.__class__,
                user=self.user,
                course_key=self.course_id,
                mode=self.mode,
                status=self.status,
            )

            # .. event_implemented_name: CERTIFICATE_CREATED
            # .. event_type: org.openedx.learning.certificate.created.v1
            CERTIFICATE_CREATED.send_event(
                time=timestamp,
                certificate=CertificateData(
                    user=UserData(
                        pii=UserPersonalData(
                            username=self.user.username,
                            email=self.user.email,
                            name=self.user.profile.name,
                        ),
                        id=self.user.id,
                        is_active=self.user.is_active,
                    ),
                    course=CourseData(
                        course_key=self.course_id,
                    ),
                    mode=self.mode,
                    grade=self.grade,
                    current_status=self.status,
                    download_url=self.download_url,
                    name=self.name,
                )
            )


class CertificateGenerationHistory(TimeStampedModel):
    """
    Model for storing Certificate Generation History.

    .. no_pii:
    """

    course_id = CourseKeyField()
    generated_by = models.ForeignKey(User, on_delete=models.CASCADE)
    instructor_task = models.ForeignKey(InstructorTask, on_delete=models.CASCADE)
    is_regeneration = models.BooleanField(default=False)

    def get_task_name(self):
        """
        Return "regenerated" if record corresponds to Certificate Regeneration task, otherwise returns 'generated'
        """
        # Translators: This is a past-tense verb that is used for task action messages.
        return _("regenerated") if self.is_regeneration else _("generated")

    def get_certificate_generation_candidates(self):
        """
        Return the candidates for certificate generation task. It could either be students or certificate statuses
        depending upon the nature of certificate generation task. Returned value could be one of the following,

        1. "All learners" Certificate Generation task was initiated for all learners of the given course.
        2. Comma separated list of certificate statuses, This usually happens when instructor regenerates certificates.
        3. "for exceptions", This is the case when instructor generates certificates for allowlisted
            students.
        """
        task_input = self.instructor_task.task_input
        if not task_input.strip():
            # if task input is empty, it means certificates were generated for all learners
            # Translators: This string represents task was executed for all learners.
            return _("All learners")

        task_input_json = json.loads(task_input)

        # get statuses_to_regenerate from task_input convert statuses to human readable strings and return
        statuses = task_input_json.get('statuses_to_regenerate', None)
        if statuses:
            readable_statuses = [
                CertificateStatuses.readable_statuses.get(status) for status in statuses
                if CertificateStatuses.readable_statuses.get(status) is not None
            ]
            return ", ".join(readable_statuses)

        # If "student_set" is present in task_input, then this task only
        # generates certificates for allowlisted students. Note that
        # this key used to be "students", so we include that in this conditional
        # for backwards compatibility.
        if 'student_set' in task_input_json or 'students' in task_input_json:
            # Translators: This string represents task was executed for students having exceptions.
            return _("For exceptions")
        else:
            return _("All learners")

    class Meta:
        app_label = "certificates"

    def __str__(self):
        return "certificates %s by %s on %s for %s" % \
               ("regenerated" if self.is_regeneration else "generated", self.generated_by, self.created, self.course_id)


class CertificateInvalidation(TimeStampedModel):
    """
    Model for storing Certificate Invalidation.

    .. no_pii:
    """
    generated_certificate = models.ForeignKey(GeneratedCertificate, on_delete=models.CASCADE)
    invalidated_by = models.ForeignKey(User, on_delete=models.CASCADE)
    notes = models.TextField(default=None, null=True)
    active = models.BooleanField(default=True)

    # This is necessary because CMS does not install the certificates app, but
    # this code is run when other models in this file are imported there (or in
    # common code). Simple History will attempt to connect to the installed
    # model in the certificates app, which will fail.
    if 'certificates' in apps.app_configs:
        history = HistoricalRecords()

    class Meta:
        app_label = "certificates"

    def __str__(self):
        return "Certificate %s, invalidated by %s on %s." % \
               (self.generated_certificate, self.invalidated_by, self.created)

    def deactivate(self):
        """
        Deactivate certificate invalidation by setting active to False.
        """
        self.active = False
        self.save()

    @classmethod
    def get_certificate_invalidations(cls, course_key, student=None):
        """
        Return certificate invalidations filtered based on the provided course and student (if provided),

        Returned value is JSON serializable list of dicts, dict element would have the following key-value pairs.
         1. id: certificate invalidation id (primary key)
         2. user: username of the student to whom certificate belongs
         3. invalidated_by: user id of the instructor/support user who invalidated the certificate
         4. created: string containing date of invalidation in the following format "December 29, 2015"
         5. notes: string containing notes regarding certificate invalidation.
        """
        certificate_invalidations = cls.objects.filter(
            generated_certificate__course_id=course_key,
            active=True,
        )
        if student:
            certificate_invalidations = certificate_invalidations.filter(generated_certificate__user=student)
        data = []
        for certificate_invalidation in certificate_invalidations:
            data.append({
                'id': certificate_invalidation.id,
                'user': certificate_invalidation.generated_certificate.user.username,
                'invalidated_by': certificate_invalidation.invalidated_by.username,
                'created': certificate_invalidation.created.strftime("%B %d, %Y"),
                'notes': certificate_invalidation.notes,
            })
        return data

    @classmethod
    def has_certificate_invalidation(cls, student, course_key):
        """Check that whether the student in the course has been invalidated
        for receiving certificates.

        Arguments:
            student (user): logged-in user
            course_key (CourseKey): The course associated with the certificate.

        Returns:
             Boolean denoting whether the student in the course is invalidated
             to receive certificates
        """
        return cls.objects.filter(
            generated_certificate__course_id=course_key,
            active=True,
            generated_certificate__user=student
        ).exists()


@receiver(COURSE_CERT_AWARDED, sender=GeneratedCertificate)
def handle_course_cert_awarded(sender, user, course_key, **kwargs):  # pylint: disable=unused-argument
    """
    Mark a milestone entry if user has passed the course.
    """
    if is_prerequisite_courses_enabled():
        fulfill_course_milestone(course_key, user)


class ExampleCertificateSet(TimeStampedModel):
    """
    A set of example certificates.

    Example certificates are used to verify that certificate
    generation is working for a particular course.

    A particular course may have several kinds of certificates
    (e.g. honor and verified), in which case we generate
    multiple example certificates for the course.

    .. no_pii:
    """
    course_key = CourseKeyField(db_index=True)

    class Meta:
        get_latest_by = 'created'
        app_label = "certificates"

    @classmethod
    @transaction.atomic
    def create_example_set(cls, course_key):
        """Create a set of example certificates for a course.

        Arguments:
            course_key (CourseKey)

        Returns:
            ExampleCertificateSet

        """
        # Import here instead of top of file since this module gets imported before
        # the course_modes app is loaded, resulting in a Django deprecation warning.
        from common.djangoapps.course_modes.models import CourseMode  # pylint: disable=redefined-outer-name, reimported
        cert_set = cls.objects.create(course_key=course_key)

        ExampleCertificate.objects.bulk_create([
            ExampleCertificate(
                example_cert_set=cert_set,
                description=mode.slug,
                template=cls._template_for_mode(mode.slug, course_key)
            )
            for mode in CourseMode.modes_for_course(course_key)
        ])

        return cert_set

    @classmethod
    def latest_status(cls, course_key):
        """Summarize the latest status of example certificates for a course.

        Arguments:
            course_key (CourseKey)

        Returns:
            list: List of status dictionaries.  If no example certificates
                have been started yet, returns None.

        """
        try:
            latest = cls.objects.filter(course_key=course_key).latest()
        except cls.DoesNotExist:
            return None

        queryset = ExampleCertificate.objects.filter(example_cert_set=latest).order_by('-created')
        return [cert.status_dict for cert in queryset]

    def __iter__(self):
        """Iterate through example certificates in the set.

        Yields:
            ExampleCertificate

        """
        queryset = (ExampleCertificate.objects).select_related('example_cert_set').filter(example_cert_set=self)
        yield from queryset

    @staticmethod
    def _template_for_mode(mode_slug, course_key):
        """Calculate the template PDF based on the course mode. """
        return (
            "certificate-template-{key.org}-{key.course}-verified.pdf".format(key=course_key)
            if mode_slug == 'verified'
            else "certificate-template-{key.org}-{key.course}.pdf".format(key=course_key)
        )


def _make_uuid():
    """Return a 32-character UUID. """
    return uuid.uuid4().hex


class ExampleCertificate(TimeStampedModel):
    """
    Example certificate.

    Example certificates are used to verify that certificate
    generation is working for a particular course.

    An example certificate is similar to an ordinary certificate,
    except that:

    1) Example certificates are not associated with a particular user,
        and are never displayed to students.

    2) We store the "inputs" for generating the example certificate
        to make it easier to debug when certificate generation fails.

    3) We use dummy values.

    .. no_pii:
    """
    class Meta:
        app_label = "certificates"

    # Statuses
    STATUS_STARTED = 'started'
    STATUS_SUCCESS = 'success'
    STATUS_ERROR = 'error'

    # Dummy full name for the generated certificate
    EXAMPLE_FULL_NAME = 'John Doë'

    example_cert_set = models.ForeignKey(ExampleCertificateSet, on_delete=models.CASCADE)

    description = models.CharField(
        max_length=255,
        help_text=_(
            "A human-readable description of the example certificate.  "
            "For example, 'verified' or 'honor' to differentiate between "
            "two types of certificates."
        )
    )

    # Inputs to certificate generation
    # We store this for auditing purposes if certificate
    # generation fails.
    uuid = models.CharField(
        max_length=255,
        default=_make_uuid,
        db_index=True,
        unique=True,
        help_text=_(
            "A unique identifier for the example certificate.  "
            "This is used when we receive a response from the queue "
            "to determine which example certificate was processed."
        )
    )

    access_key = models.CharField(
        max_length=255,
        default=_make_uuid,
        db_index=True,
        help_text=_(
            "An access key for the example certificate.  "
            "This is used when we receive a response from the queue "
            "to validate that the sender is the same entity we asked "
            "to generate the certificate."
        )
    )

    full_name = models.CharField(
        max_length=255,
        default=EXAMPLE_FULL_NAME,
        help_text=_("The full name that will appear on the certificate.")
    )

    template = models.CharField(
        max_length=255,
        help_text=_("The template file to use when generating the certificate.")
    )

    # Outputs from certificate generation
    status = models.CharField(
        max_length=255,
        default=STATUS_STARTED,
        choices=(
            (STATUS_STARTED, 'Started'),
            (STATUS_SUCCESS, 'Success'),
            (STATUS_ERROR, 'Error')
        ),
        help_text=_("The status of the example certificate.")
    )

    error_reason = models.TextField(
        null=True,
        default=None,
        help_text=_("The reason an error occurred during certificate generation.")
    )

    download_url = models.CharField(
        max_length=255,
        null=True,
        default=None,
        help_text=_("The download URL for the generated certificate.")
    )

    def update_status(self, status, error_reason=None, download_url=None):
        """Update the status of the example certificate.

        This will usually be called either:
        1) When an error occurs adding the certificate to the queue.
        2) When we receive a response from the queue (either error or success).

        If an error occurs, we store the error message;
        if certificate generation is successful, we store the URL
        for the generated certificate.

        Arguments:
            status (str): Either `STATUS_SUCCESS` or `STATUS_ERROR`

        Keyword Arguments:
            error_reason (unicode): A description of the error that occurred.
            download_url (unicode): The URL for the generated certificate.

        Raises:
            ValueError: The status is not a valid value.

        """
        if status not in [self.STATUS_SUCCESS, self.STATUS_ERROR]:
            msg = "Invalid status: must be either '{success}' or '{error}'.".format(
                success=self.STATUS_SUCCESS,
                error=self.STATUS_ERROR
            )
            raise ValueError(msg)

        self.status = status

        if status == self.STATUS_ERROR and error_reason:
            self.error_reason = error_reason

        if status == self.STATUS_SUCCESS and download_url:
            self.download_url = download_url

        self.save()

    @property
    def status_dict(self):
        """Summarize the status of the example certificate.

        Returns:
            dict

        """
        result = {
            'description': self.description,
            'status': self.status,
        }

        if self.error_reason:
            result['error_reason'] = self.error_reason

        if self.download_url:
            result['download_url'] = self.download_url

        return result

    @property
    def course_key(self):
        """The course key associated with the example certificate. """
        return self.example_cert_set.course_key


class CertificateGenerationCourseSetting(TimeStampedModel):
    """
    Enable or disable certificate generation for a particular course.

    In general, we should only enable self-generated certificates
    for a course once we successfully generate example certificates
    for the course.  This is enforced in the UI layer, but
    not in the data layer.

    .. no_pii:
    """
    course_key = CourseKeyField(db_index=True)

    self_generation_enabled = models.BooleanField(
        default=False,
        help_text=(
            "Allow students to generate their own certificates for the course. "
            "Enabling this does NOT affect usage of the management command used "
            "for batch certificate generation."
        )
    )
    language_specific_templates_enabled = models.BooleanField(
        default=False,
        help_text=(
            "Render translated certificates rather than using the platform's "
            "default language. Available translations are controlled by the "
            "certificate template."
        )
    )
    include_hours_of_effort = models.BooleanField(
        default=None,
        help_text=(
            "Display estimated time to complete the course, which is equal to the maximum hours of effort per week "
            "times the length of the course in weeks. This attribute will only be displayed in a certificate when the "
            "attributes 'Weeks to complete' and 'Max effort' have been provided for the course run and its "
            "certificate template includes Hours of Effort."
        ),
        null=True)

    class Meta:
        get_latest_by = 'created'
        app_label = "certificates"

    @classmethod
    def get(cls, course_key):
        """ Retrieve certificate generation settings for a course.

        Arguments:
            course_key (CourseKey): The identifier for the course.

        Returns:
            CertificateGenerationCourseSetting
        """
        try:
            latest = cls.objects.filter(course_key=course_key).latest()
        except cls.DoesNotExist:
            return None
        else:
            return latest

    @classmethod
    def is_self_generation_enabled_for_course(cls, course_key):
        """Check whether self-generated certificates are enabled for a course.

        Arguments:
            course_key (CourseKey): The identifier for the course.

        Returns:
            boolean

        """
        try:
            latest = cls.objects.filter(course_key=course_key).latest()
        except cls.DoesNotExist:
            return False
        else:
            return latest.self_generation_enabled

    @classmethod
    def set_self_generation_enabled_for_course(cls, course_key, is_enabled):
        """Enable or disable self-generated certificates for a course.

        Arguments:
            course_key (CourseKey): The identifier for the course.
            is_enabled (boolean): Whether to enable or disable self-generated certificates.

        """
        default = {
            'self_generation_enabled': is_enabled
        }
        CertificateGenerationCourseSetting.objects.update_or_create(
            course_key=course_key,
            defaults=default
        )


class CertificateGenerationConfiguration(ConfigurationModel):
    """Configure certificate generation.

    Enable or disable the self-generated certificates feature.
    When this flag is disabled, the "generate certificate" button
    will be hidden on the progress page.

    When the feature is enabled, the "generate certificate" button
    will appear for courses that have enabled self-generated
    certificates.

    .. no_pii:
    """
    class Meta(ConfigurationModel.Meta):
        app_label = "certificates"


class CertificateHtmlViewConfiguration(ConfigurationModel):
    """
    Static values for certificate HTML view context parameters.
    Default values will be applied across all certificate types (course modes)
    Matching 'mode' overrides will be used instead of defaults, where applicable
    Example configuration :
        {
            "default": {
                "url": "https://www.edx.org",
                "logo_src": "https://www.edx.org/static/images/logo.png"
            },
            "honor": {
                "logo_src": "https://www.edx.org/static/images/honor-logo.png"
            }
        }

    .. no_pii:
    """
    class Meta(ConfigurationModel.Meta):
        app_label = "certificates"

    configuration = models.TextField(
        help_text="Certificate HTML View Parameters (JSON)"
    )

    def clean(self):
        """
        Ensures configuration field contains valid JSON.
        """
        try:
            json.loads(self.configuration)
        except ValueError as e:
            raise ValidationError('Must be valid JSON string.') from e

    @classmethod
    def get_config(cls):
        """
        Retrieves the configuration field value from the database
        """
        instance = cls.current()
        json_data = json.loads(instance.configuration) if instance.enabled else {}
        return json_data


class CertificateTemplate(TimeStampedModel):
    """A set of custom web certificate templates.

    Web certificate templates are Django web templates
    to replace PDF certificate.

    A particular course may have several kinds of certificate templates
    (e.g. honor and verified).

    .. no_pii:
    """
    name = models.CharField(
        max_length=255,
        help_text=_('Name of template.'),
    )
    description = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text=_('Description and/or admin notes.'),
    )
    template = models.TextField(
        help_text=_('Django template HTML.'),
    )
    organization_id = models.IntegerField(
        null=True,
        blank=True,
        db_index=True,
        help_text=_('Organization of template.'),
    )
    course_key = CourseKeyField(
        max_length=255,
        null=True,
        blank=True,
        db_index=True,
    )
    mode = models.CharField(
        max_length=125,
        choices=GeneratedCertificate.MODES,
        default=GeneratedCertificate.MODES.honor,
        null=True,
        blank=True,
        help_text=_('The course mode for this template.'),
    )
    is_active = models.BooleanField(
        help_text=_('On/Off switch.'),
        default=False,
    )
    language = models.CharField(
        max_length=2,
        blank=True,
        null=True,
        help_text='Only certificates for courses in the selected language will be rendered using this template. '
                  'Course language is determined by the first two letters of the language code.'
    )

    def __str__(self):
        return f'{self.name}'

    class Meta:
        get_latest_by = 'created'
        unique_together = (('organization_id', 'course_key', 'mode', 'language'),)
        app_label = "certificates"


def template_assets_path(instance, filename):
    """
    Delete the file if it already exist and returns the certificate template asset file path.

    :param instance: CertificateTemplateAsset object
    :param filename: file to upload
    :return path: path of asset file e.g. certificate_template_assets/1/filename
    """
    name = os.path.join('certificate_template_assets', str(instance.id), filename)
    fullname = os.path.join(settings.MEDIA_ROOT, name)
    if os.path.exists(fullname):
        os.remove(fullname)
    return name


class CertificateTemplateAsset(TimeStampedModel):
    """A set of assets to be used in custom web certificate templates.

    This model stores assets used in custom web certificate templates
    such as image, css files.

    .. no_pii:
    """
    description = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text=_('Description of the asset.'),
    )
    asset = models.FileField(
        max_length=255,
        upload_to=template_assets_path,
        help_text=_('Asset file. It could be an image or css file.'),
    )
    asset_slug = models.SlugField(
        max_length=255,
        unique=True,
        null=True,
        help_text=_('Asset\'s unique slug. We can reference the asset in templates using this value.'),
    )

    def save(self, *args, **kwargs):
        """save the certificate template asset """
        if self.pk is None:
            asset_image = self.asset
            self.asset = None
            super().save(*args, **kwargs)
            self.asset = asset_image

        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.asset.url}'

    class Meta:
        get_latest_by = 'created'
        app_label = "certificates"


class ModifiedCertificateTemplateCommandConfiguration(ConfigurationModel):
    """
    Manages configuration for a run of the modify_cert_template management command.

    .. no_pii:
    """

    class Meta:
        app_label = "certificates"
        verbose_name = "modify_cert_template argument"

    arguments = models.TextField(
        blank=True,
        help_text=(
            "Arguments for the 'modify_cert_template' management command. Specify like '--old-text \"foo\" "
            "--new-text \"bar\" --template_ids <id1> <id2>'"
        ),
        default="",
    )

    def __str__(self):
        return str(self.arguments)


class CertificateGenerationCommandConfiguration(ConfigurationModel):
    """
    Manages configuration for a run of the cert_generation management command.

    .. no_pii:
    """

    class Meta:
        app_label = "certificates"
        verbose_name = "cert_generation argument"

    arguments = models.TextField(
        blank=True,
        help_text=(
            "Arguments for the 'cert_generation' management command. Specify like '-u <user_id> -c <course_run_key>'"
        ),
        default="",
    )

    def __str__(self):
        return str(self.arguments)


class PurgeReferencestoPDFCertificatesCommandConfiguration(ConfigurationModel):
    """
    Manages configuration for a run of the purge_references_to_pdf_certificates management command.

    .. no_pii:
    """

    class Meta:
        app_label = "certificates"
        verbose_name = "purge_references_to_pdf_certificates argument"

    arguments = models.TextField(
        blank=True,
        help_text=(
            "Arguments for the 'purge_references_to_pdf_certificates' management command. "
            "Specify like '--certificate_ids <id1> <id2>'"
        ),
        default="",
    )

    def __str__(self):
        return str(self.arguments)


class CertificateDateOverride(TimeStampedModel):
    """
    Model to manually override a given certificate date with the given date.

    .. no_pii:
    """
    generated_certificate = models.OneToOneField(
        GeneratedCertificate,
        on_delete=models.CASCADE,
        related_name='date_override',
        help_text="The id of the Generated Certificate to override",
    )
    date = models.DateTimeField(
        help_text="The date to display on the certificate. Set 'Time' to midnight (00:00:00).",
    )
    reason = models.TextField(
        help_text="The reason why you are overriding the certificate date (Update this when you add OR edit the date.)",
    )
    overridden_by = models.ForeignKey(
        User,
        on_delete=models.DO_NOTHING,
        help_text="The last person to save this record",
    )

    # This is necessary because CMS does not install the certificates app, but
    # this code is run when other models in this file are imported there (or in
    # common code). Simple History will attempt to connect to the installed
    # model in the certificates app, which will fail.
    if 'certificates' in apps.app_configs:
        history = HistoricalRecords()

    class Meta:
        app_label = "certificates"

    def __str__(self):
        return "Certificate %s, date overridden to %s by %s on %s." % \
               (self.generated_certificate, self.date, self.overridden_by, self.created)

    def send_course_cert_changed_signal(self):
        COURSE_CERT_CHANGED.send_robust(
            sender=self.__class__,
            user=self.generated_certificate.user,
            course_key=self.generated_certificate.course_id,
            mode=self.generated_certificate.mode,
            status=self.generated_certificate.status,
        )

    def save(self, *args, **kwargs):  # pylint: disable=signature-differs
        """
        After the base save() method finishes, fire the COURSE_CERT_CHANGED
        signal.
        """
        super().save(*args, **kwargs)
        transaction.on_commit(self.send_course_cert_changed_signal)


@receiver(models.signals.post_delete, sender=CertificateDateOverride)
def handle_certificate_date_override_delete(sender, instance, **kwargs):    # pylint: disable=unused-argument
    """
    After a CertificateDateOverride is deleted, fire the COURSE_CERT_CHANGED
    signal.

    We do this in a signal handler instead of overriding the
    CertificateDateOverride delete method so that this will be executed for both
    individual and bulk deletes from the Django admin. (The delete() method for
    an object is not necessarily called when deleting objects in bulk.)
    """
    transaction.on_commit(instance.send_course_cert_changed_signal)
