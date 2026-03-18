"""Models for course enrollment"""
import hashlib  # lint-amnesty, pylint: disable=wrong-import-order
import logging  # lint-amnesty, pylint: disable=wrong-import-order
import uuid  # lint-amnesty, pylint: disable=wrong-import-order
from collections import defaultdict, namedtuple  # lint-amnesty, pylint: disable=wrong-import-order
from datetime import date, datetime, timedelta  # lint-amnesty, pylint: disable=wrong-import-order
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from config_models.models import ConfigurationModel
from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core.cache import cache
from django.core.exceptions import MultipleObjectsReturned, ObjectDoesNotExist
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import Count, Index, Q
from django.dispatch import receiver
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from edx_django_utils.cache import RequestCache, TieredCache, get_cache_key
from eventtracking import tracker
from model_utils.models import TimeStampedModel
from opaque_keys.edx.django.models import CourseKeyField
from opaque_keys.edx.keys import CourseKey
from openedx_events.learning.data import CourseData, CourseEnrollmentData, UserData, UserPersonalData
from openedx_events.learning.signals import (
    COURSE_ENROLLMENT_CHANGED,
    COURSE_ENROLLMENT_CREATED,
    COURSE_UNENROLLMENT_COMPLETED,
)
from openedx_filters.learning.filters import CourseEnrollmentStarted, CourseUnenrollmentStarted
from requests.exceptions import HTTPError, RequestException
from simple_history.models import HistoricalRecords

from common.djangoapps.course_modes.models import CourseMode, get_cosmetic_verified_display_price
from common.djangoapps.student.signals import ENROLL_STATUS_CHANGE, ENROLLMENT_TRACK_UPDATED, UNENROLL_DONE
from common.djangoapps.track import contexts, segment
from common.djangoapps.util.query import use_read_replica_if_available
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.courseware.models import (
    CourseDynamicUpgradeDeadlineConfiguration,
    DynamicUpgradeDeadlineConfiguration,
    OrgDynamicUpgradeDeadlineConfiguration,
)
from lms.djangoapps.verify_student.models import SoftwareSecurePhotoVerification
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.enrollments.api import (
    _default_course_mode,
    get_enrollment_attributes,
    set_enrollment_attributes,
)
from openedx.core.djangolib.model_mixins import DeletableByUserValue

log = logging.getLogger(__name__)
AUDIT_LOG = logging.getLogger("audit")


# ENROLL signal used for free enrollment only
class EnrollStatusChange:
    """
    Possible event types for ENROLL_STATUS_CHANGE signal
    """
    # enroll for a course
    enroll = 'enroll'
    # unenroll for a course
    unenroll = 'unenroll'
    # add an upgrade to cart
    upgrade_start = 'upgrade_start'
    # complete an upgrade purchase
    upgrade_complete = 'upgrade_complete'
    # add a paid course to the cart
    paid_start = 'paid_start'
    # complete a paid course purchase
    paid_complete = 'paid_complete'


UNENROLLED_TO_ALLOWEDTOENROLL = 'from unenrolled to allowed to enroll'
ALLOWEDTOENROLL_TO_ENROLLED = 'from allowed to enroll to enrolled'
ENROLLED_TO_ENROLLED = 'from enrolled to enrolled'
ENROLLED_TO_UNENROLLED = 'from enrolled to unenrolled'
UNENROLLED_TO_ENROLLED = 'from unenrolled to enrolled'
ALLOWEDTOENROLL_TO_UNENROLLED = 'from allowed to enroll to unenrolled'
UNENROLLED_TO_UNENROLLED = 'from unenrolled to unenrolled'
DEFAULT_TRANSITION_STATE = 'N/A'
SCORE_RECALCULATION_DELAY_ON_ENROLLMENT_UPDATE = 30

TRANSITION_STATES = (
    (UNENROLLED_TO_ALLOWEDTOENROLL, UNENROLLED_TO_ALLOWEDTOENROLL),
    (ALLOWEDTOENROLL_TO_ENROLLED, ALLOWEDTOENROLL_TO_ENROLLED),
    (ENROLLED_TO_ENROLLED, ENROLLED_TO_ENROLLED),
    (ENROLLED_TO_UNENROLLED, ENROLLED_TO_UNENROLLED),
    (UNENROLLED_TO_ENROLLED, UNENROLLED_TO_ENROLLED),
    (ALLOWEDTOENROLL_TO_UNENROLLED, ALLOWEDTOENROLL_TO_UNENROLLED),
    (UNENROLLED_TO_UNENROLLED, UNENROLLED_TO_UNENROLLED),
    (DEFAULT_TRANSITION_STATE, DEFAULT_TRANSITION_STATE)
)

EVENT_NAME_ENROLLMENT_ACTIVATED = 'edx.course.enrollment.activated'
EVENT_NAME_ENROLLMENT_DEACTIVATED = 'edx.course.enrollment.deactivated'
EVENT_NAME_ENROLLMENT_MODE_CHANGED = 'edx.course.enrollment.mode_changed'


class CourseEnrollmentException(Exception):
    pass


class NonExistentCourseError(CourseEnrollmentException):
    pass


class EnrollmentClosedError(CourseEnrollmentException):
    pass


class CourseFullError(CourseEnrollmentException):
    pass


class AlreadyEnrolledError(CourseEnrollmentException):
    pass


class EnrollmentNotAllowed(CourseEnrollmentException):
    pass


class UnenrollmentNotAllowed(CourseEnrollmentException):
    pass


class CourseEnrollmentQuerySet(models.QuerySet):
    """
    Custom queryset for CourseEnrollment with Table-level filter methods.
    """

    def active(self):
        """
        Returns a queryset of CourseEnrollment objects for courses that are currently active.
        """
        return self.filter(is_active=True)

    def without_certificates(self, username):
        """
        Returns a queryset of CourseEnrollment objects for courses that do not have a certificate.
        """
        return self.exclude(course_id__in=self.get_user_course_ids_with_certificates(username))

    def with_certificates(self, username):
        """
        Returns a queryset of CourseEnrollment objects for courses that have a certificate.
        """
        return self.filter(course_id__in=self.get_user_course_ids_with_certificates(username))

    def in_progress(self, username, time_zone=ZoneInfo("UTC")):
        """
        Returns a queryset of CourseEnrollment objects for courses that are currently in progress.
        """
        now = datetime.now(time_zone)
        return self.active().without_certificates(username).filter(
            Q(course__start__lte=now, course__end__gte=now)
            | Q(course__start__isnull=True, course__end__isnull=True)
            | Q(course__start__isnull=True, course__end__gte=now)
            | Q(course__start__lte=now, course__end__isnull=True),
        )

    def completed(self, username):
        """
        Returns a queryset of CourseEnrollment objects for courses that have been completed.
        """
        return self.active().with_certificates(username)

    def expired(self, username, time_zone=ZoneInfo("UTC")):
        """
        Returns a queryset of CourseEnrollment objects for courses that have expired.
        """
        now = datetime.now(time_zone)
        return self.active().without_certificates(username).filter(course__end__lt=now)

    def get_user_course_ids_with_certificates(self, username):
        """
        Retrieve the list of course IDs for which the given user has earned certificates.
        """
        from lms.djangoapps.certificates.api import get_course_ids_from_certs_for_user
        return get_course_ids_from_certs_for_user(username)


class CourseEnrollmentManager(models.Manager):
    """
    Custom manager for CourseEnrollment with Table-level filter methods.
    """

    def get_queryset(self):
        return CourseEnrollmentQuerySet(self.model, using=self._db)

    def is_small_course(self, course_id):
        """
        Returns false if the number of enrollments are one greater than 'max_enrollments' else true

        'course_id' is the course_id to return enrollments
        """
        max_enrollments = settings.FEATURES.get("MAX_ENROLLMENT_INSTR_BUTTONS")

        enrollment_number = super().get_queryset().filter(
            course_id=course_id,
            is_active=1
        )[:max_enrollments + 1].count()

        return enrollment_number <= max_enrollments

    def num_enrolled_in_exclude_admins(self, course_id):
        """
        Returns the count of active enrollments in a course excluding instructors, staff and CCX coaches.

        Arguments:
            course_id (CourseLocator): course_id to return enrollments (count).

        Returns:
            int: Count of enrollments excluding staff, instructors and CCX coaches.

        """
        # To avoid circular imports.
        from common.djangoapps.student.roles import CourseCcxCoachRole, CourseInstructorRole, CourseStaffRole
        course_locator = course_id

        if getattr(course_id, 'ccx', None):
            course_locator = course_id.to_course_locator()

        staff = CourseStaffRole(course_locator).users_with_role()
        admins = CourseInstructorRole(course_locator).users_with_role()
        coaches = CourseCcxCoachRole(course_locator).users_with_role()

        return super().get_queryset().filter(
            course_id=course_id,
            is_active=1,
        ).exclude(user__in=staff).exclude(user__in=admins).exclude(user__in=coaches).count()

    def is_course_full(self, course):
        """
        Returns a boolean value regarding whether a course has already reached it's max enrollment
        capacity
        """
        is_course_full = False
        if course.max_student_enrollments_allowed is not None:
            is_course_full = self.num_enrolled_in_exclude_admins(course.id) >= course.max_student_enrollments_allowed

        return is_course_full

    def users_enrolled_in(self, course_id, include_inactive=False, verified_only=False):
        """
        Return a queryset of User for every user enrolled in the course.

        Arguments:
            course_id (CourseLocator): course_id to return enrollees for.
            include_inactive (boolean): is a boolean when True, returns both active and inactive enrollees
            verified_only (boolean): is a boolean when True, returns only verified enrollees.

        Returns:
            Returns a User queryset.
        """
        filter_kwargs = {
            'courseenrollment__course_id': course_id,
        }
        if not include_inactive:
            filter_kwargs['courseenrollment__is_active'] = True
        if verified_only:
            filter_kwargs['courseenrollment__mode'] = CourseMode.VERIFIED
        return User.objects.filter(**filter_kwargs)

    def enrollment_counts(self, course_id):
        """
        Returns a dictionary that stores the total enrollment count for a course, as well as the
        enrollment count for each individual mode.
        """
        # Unfortunately, Django's "group by"-style queries look super-awkward
        query = use_read_replica_if_available(
            super().get_queryset().filter(course_id=course_id, is_active=True).values(
                'mode').order_by().annotate(Count('mode')))
        total = 0
        enroll_dict = defaultdict(int)
        for item in query:
            enroll_dict[item['mode']] = item['mode__count']
            total += item['mode__count']
        enroll_dict['total'] = total
        return enroll_dict


# Named tuple for fields pertaining to the state of
# CourseEnrollment for a user in a course.  This type
# is used to cache the state in the request cache.
CourseEnrollmentState = namedtuple('CourseEnrollmentState', 'mode, is_active')


class CourseEnrollment(models.Model):
    """
    Represents a Student's Enrollment record for a single Course. You should
    generally not manipulate CourseEnrollment objects directly, but use the
    classmethods provided to enroll, unenroll, or check on the enrollment status
    of a given student.

    We're starting to consolidate course enrollment logic in this class, but
    more should be brought in (such as checking against CourseEnrollmentAllowed,
    checking course dates, user permissions, etc.) This logic is currently
    scattered across our views.

    .. no_pii:
    """
    MODEL_TAGS = ['course', 'is_active', 'mode']

    user = models.ForeignKey(User, on_delete=models.CASCADE)

    course = models.ForeignKey(
        CourseOverview,
        db_constraint=False,
        on_delete=models.DO_NOTHING,
    )

    @property
    def course_price(self):
        return get_cosmetic_verified_display_price(self.course)

    created = models.DateTimeField(auto_now_add=True, null=True, db_index=True)

    # If is_active is False, then the student is not considered to be enrolled
    # in the course (is_enrolled() will return False)
    is_active = models.BooleanField(default=True)

    # Represents the modes that are possible. We'll update this later with a
    # list of possible values.
    mode = models.CharField(default=CourseMode.get_default_mode_slug, max_length=100)

    # An audit row will be created for every change to a CourseEnrollment. This
    # will create a new model behind the scenes - HistoricalCourseEnrollment and a
    # table named 'student_courseenrollment_history'.
    history = HistoricalRecords(
        history_id_field=models.UUIDField(default=uuid.uuid4),
        table_name='student_courseenrollment_history'
    )

    objects = CourseEnrollmentManager()

    # cache key format e.g enrollment.<username>.<course_key>.mode = 'honor'
    COURSE_ENROLLMENT_CACHE_KEY = "enrollment.{}.{}.mode"

    MODE_CACHE_NAMESPACE = 'CourseEnrollment.mode_and_active'

    class Meta:
        unique_together = (('user', 'course'),)
        indexes = [Index(fields=['user', '-created'])]
        ordering = ('user', 'course')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Private variable for storing course_overview to minimize calls to the database.
        # When the property .course_overview is accessed for the first time, this variable will be set.
        self._course_overview = None

    def __str__(self):
        return (
            "[CourseEnrollment] {}: {} ({}); active: ({})"
        ).format(self.user, self.course_id, self.created, self.is_active)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        # Delete the cached status hash, forcing the value to be recalculated the next time it is needed.
        cache.delete(self.enrollment_status_hash_cache_key(self.user))

    @classmethod
    def get_or_create_enrollment(cls, user, course_key):
        """
        Create an enrollment for a user in a class. By default *this enrollment
        is not active*. This is useful for when an enrollment needs to go
        through some sort of approval process before being activated. If you
        don't need this functionality, just call `enroll()` instead.

        Returns a CourseEnrollment object.

        `user` is a Django User object. If it hasn't been saved yet (no `.id`
               attribute), this method will automatically save it before
               adding an enrollment for it.

        `course_key` must be a opaque_keys CourseKey object.

        It is expected that this method is called from a method which has already
        verified the user authentication and access.

        If the enrollment is done due to a CourseEnrollmentAllowed, the CEA will be
        linked to the user being enrolled so that it can't be used by other users.
        """
        # If we're passing in a newly constructed (i.e. not yet persisted) User,
        # save it to the database so that it can have an ID that we can throw
        # into our CourseEnrollment object. Otherwise, we'll get an
        # IntegrityError for having a null user_id.
        assert isinstance(course_key, CourseKey)

        if user.id is None:
            user.save()

        enrollment, __ = cls.objects.get_or_create(
            user=user,
            course_id=course_key,
            defaults={
                'mode': CourseMode.DEFAULT_MODE_SLUG,
                'is_active': False
            }
        )

        # If there was an unlinked CEA, it becomes linked now
        CourseEnrollmentAllowed.objects.filter(
            email=user.email,
            course_id=course_key,
            user__isnull=True
        ).update(user=user)

        return enrollment

    @classmethod
    def get_enrollment(cls, user, course_key, select_related=None):
        """Returns a CourseEnrollment object.

        Args:
            user (User): The user associated with the enrollment.
            course_key (CourseKey): The key of the course associated with the enrollment.

        Returns:
            Course enrollment object or None
        """
        assert user

        if user.is_anonymous:
            return None
        try:
            request_cache = RequestCache('get_enrollment')
            if select_related:
                cache_key = (user.id, course_key, ','.join(select_related))
            else:
                cache_key = (user.id, course_key)
            cache_response = request_cache.get_cached_response(cache_key)
            if cache_response.is_found:
                return cache_response.value

            query = cls.objects
            if select_related is not None:
                query = query.select_related(*select_related)
            enrollment = query.get(
                user=user,
                course_id=course_key
            )
            request_cache.set(cache_key, enrollment)
            return enrollment
        except cls.DoesNotExist:
            return None

    @classmethod
    def is_enrollment_closed(cls, user, course):
        """
        Returns a boolean value regarding whether the user has access to enroll in the course. Returns False if the
        enrollment has been closed.
        """
        from openedx.core.djangoapps.enrollments.permissions import ENROLL_IN_COURSE
        return not user.has_perm(ENROLL_IN_COURSE, course)

    def update_enrollment(self, mode=None, is_active=None, skip_refund=False, enterprise_uuid=None):
        """
        Updates an enrollment for a user in a class.  This includes options
        like changing the mode, toggling is_active True/False, etc.

        Also emits relevant events for analytics purposes.

        This saves immediately.

        """
        RequestCache('get_enrollment').clear()

        activation_changed = False
        # if is_active is None, then the call to update_enrollment didn't specify
        # any value, so just leave is_active as it is
        if self.is_active != is_active and is_active is not None:
            self.is_active = is_active
            activation_changed = True

        mode_changed = False
        # if mode is None, the call to update_enrollment didn't specify a new
        # mode, so leave as-is
        if self.mode != mode and mode is not None:
            self.mode = mode
            mode_changed = True

        try:
            course_data = CourseData(
                course_key=self.course_id,
                display_name=self.course.display_name,
            )
        except CourseOverview.DoesNotExist:
            course_data = CourseData(
                course_key=self.course_id,
            )

        if activation_changed or mode_changed:
            self.save()
            self._update_enrollment_state_in_request_cache(
                self.user,
                self.course_id,
                CourseEnrollmentState(self.mode, self.is_active),
            )

            # .. event_implemented_name: COURSE_ENROLLMENT_CHANGED
            # .. event_type: org.openedx.learning.course.enrollment.changed.v1
            COURSE_ENROLLMENT_CHANGED.send_event(
                enrollment=CourseEnrollmentData(
                    user=UserData(
                        pii=UserPersonalData(
                            username=self.user.username,
                            email=self.user.email,
                            name=self.user.profile.name,
                        ),
                        id=self.user.id,
                        is_active=self.user.is_active,
                    ),
                    course=course_data,
                    mode=self.mode,
                    is_active=self.is_active,
                    creation_date=self.created,
                )
            )

        if activation_changed:
            if self.is_active:
                self.emit_event(EVENT_NAME_ENROLLMENT_ACTIVATED, enterprise_uuid=enterprise_uuid)
            else:
                UNENROLL_DONE.send(sender=None, course_enrollment=self, skip_refund=skip_refund)
                self.emit_event(EVENT_NAME_ENROLLMENT_DEACTIVATED, enterprise_uuid=enterprise_uuid)
                self.send_signal(EnrollStatusChange.unenroll)

                # .. event_implemented_name: COURSE_UNENROLLMENT_COMPLETED
                # .. event_type: org.openedx.learning.course.unenrollment.completed.v1
                COURSE_UNENROLLMENT_COMPLETED.send_event(
                    enrollment=CourseEnrollmentData(
                        user=UserData(
                            pii=UserPersonalData(
                                username=self.user.username,
                                email=self.user.email,
                                name=self.user.profile.name,
                            ),
                            id=self.user.id,
                            is_active=self.user.is_active,
                        ),
                        course=course_data,
                        mode=self.mode,
                        is_active=self.is_active,
                        creation_date=self.created,
                    )
                )

        if mode_changed:
            from common.djangoapps.student.email_helpers import (
                generate_proctoring_requirements_email_context,
                should_send_proctoring_requirements_email,
            )
            from common.djangoapps.student.emails import send_proctoring_requirements_email

            # If mode changed to one that requires proctoring, send proctoring requirements email
            if should_send_proctoring_requirements_email(self.user.username, self.course_id):
                email_context = generate_proctoring_requirements_email_context(self.user, self.course_id)
                send_proctoring_requirements_email(context=email_context)

            # Only emit mode change events when the user's enrollment
            # mode has changed from its previous setting
            self.emit_event(EVENT_NAME_ENROLLMENT_MODE_CHANGED)
            # this signal is meant to trigger a score recalculation celery task,
            # `countdown` is added to celery task as delay so that cohort is duly updated
            # before starting score recalculation
            ENROLLMENT_TRACK_UPDATED.send(
                sender=None,
                user=self.user,
                course_key=self.course_id,
                mode=self.mode,
                countdown=SCORE_RECALCULATION_DELAY_ON_ENROLLMENT_UPDATE,
            )

    def send_signal(self, event, cost=None, currency=None):
        """
        Sends a signal announcing changes in course enrollment status.
        """
        ENROLL_STATUS_CHANGE.send(sender=None, event=event, user=self.user,
                                  mode=self.mode, course_id=self.course_id,
                                  cost=cost, currency=currency)

    @classmethod
    def send_signal_full(cls, event, user=user, mode=mode, course_id=None, cost=None, currency=None):
        """
        Sends a signal announcing changes in course enrollment status.
        This version should be used if you don't already have a CourseEnrollment object
        """
        ENROLL_STATUS_CHANGE.send(sender=None, event=event, user=user,
                                  mode=mode, course_id=course_id,
                                  cost=cost, currency=currency)

    def emit_event(self, event_name, enterprise_uuid=None):
        """
        Emits an event to explicitly track course enrollment and unenrollment.
        """
        from openedx.core.djangoapps.schedules.config import set_up_external_updates_for_enrollment
        from common.djangoapps.student.toggles import should_send_enrollment_email
        from common.djangoapps.student.tasks import send_course_enrollment_email

        segment_properties = {
            'category': 'conversion',
            'label': str(self.course_id),
            'org': self.course_id.org,
            'course': self.course_id.course,
            'run': self.course_id.run,
            'mode': self.mode,
        }

        try:
            context = contexts.course_context_from_course_id(self.course_id)
            if enterprise_uuid:
                context["enterprise_uuid"] = enterprise_uuid
                context["enterprise_enrollment"] = True
                segment_properties["enterprise_uuid"] = enterprise_uuid
                segment_properties["enterprise_enrollment"] = True
            assert isinstance(self.course_id, CourseKey)
            data = {
                'user_id': self.user.id,
                'course_id': str(self.course_id),
                'mode': self.mode,
            }
            if enterprise_uuid and 'username' not in context:
                data['username'] = self.user.username

            # DENG-803: For segment events forwarded along to Hubspot, duplicate the `properties`
            # section of the event payload into the `traits` section so that they can be received.
            # This is a temporary fix until we implement this behavior outside of the LMS.
            # TODO: DENG-804: remove the properties duplication in the event traits.
            segment_traits = dict(segment_properties)
            # Add course_title to the traits, as it is used by Hubspot filters
            segment_traits['course_title'] = self.course_overview.display_name if self.course_overview else None
            # Hubspot requires all incoming events have an email address to link it
            # to a Contact object.
            segment_traits['email'] = self.user.email

            if event_name == EVENT_NAME_ENROLLMENT_ACTIVATED:
                if should_send_enrollment_email() and self.course_id:
                    course_pacing_type = 'self-paced' if self.course_overview.self_paced else 'instructor-paced'
                    send_course_enrollment_email.apply_async((self.user.id, str(self.course_id),
                                                              self.course_overview.display_name,
                                                              self.course_overview.short_description,
                                                              self.course_overview.has_ended(),
                                                              course_pacing_type,
                                                              self.mode))
                segment_properties['email'] = self.user.email
                # This next property is for an experiment, see method's comments for more information
                segment_properties['external_course_updates'] = set_up_external_updates_for_enrollment(self.user,
                                                                                                       self.course_id)
                segment_properties['course_start'] = self.course.start
                segment_properties['course_pacing'] = self.course.pacing

            with tracker.get_tracker().context(event_name, context):
                tracker.emit(event_name, data)
                segment.track(self.user_id, event_name, segment_properties, traits=segment_traits)

        except Exception:  # pylint: disable=broad-except
            if event_name and self.course_id:
                log.exception(
                    'Unable to emit event %s for user %s and course %s',
                    event_name,
                    self.user.username,
                    self.course_id,
                )

    @classmethod
    def enroll(cls, user, course_key, mode=None, check_access=False, can_upgrade=False, enterprise_uuid=None):
        """
        Enroll a user in a course. This saves immediately.

        Returns a CourseEnrollment object.

        `user` is a Django User object. If it hasn't been saved yet (no `.id`
               attribute), this method will automatically save it before
               adding an enrollment for it.

        `course_key` is our usual course_id string (e.g. "edX/Test101/2013_Fall)

        `mode` is a string specifying what kind of enrollment this is. The
               default is the default course mode, 'audit'. Other options
               include 'professional', 'verified', 'honor',
               'no-id-professional' and 'credit'.
               See CourseMode in common/djangoapps/course_modes/models.py.

        `check_access`: if True, we check that an accessible course actually
                exists for the given course_key before we enroll the student.
                The default is set to False to avoid breaking legacy code or
                code with non-standard flows (ex. beta tester invitations), but
                for any standard enrollment flow you probably want this to be True.

        `can_upgrade`: if course is upgradeable, alow learners to enroll even
                if enrollment is closed. This is a special case for entitlements
                while selecting a session. The default is set to False to avoid
                breaking the orignal course enroll code.

        enterprise_uuid (str): Add course enterprise uuid

        Exceptions that can be raised: NonExistentCourseError,
        EnrollmentClosedError, CourseFullError, AlreadyEnrolledError.  All these
        are subclasses of CourseEnrollmentException if you want to catch all of
        them in the same way.

        It is expected that this method is called from a method which has already
        verified the user authentication.

        Also emits relevant events for analytics purposes.
        """
        try:
            # .. filter_implemented_name: CourseEnrollmentStarted
            # .. filter_type: org.openedx.learning.course.enrollment.started.v1
            user, course_key, mode = CourseEnrollmentStarted.run_filter(
                user=user, course_key=course_key, mode=mode,
            )
        except CourseEnrollmentStarted.PreventEnrollment as exc:
            raise EnrollmentNotAllowed(str(exc)) from exc

        if mode is None:
            mode = _default_course_mode(str(course_key))
        # All the server-side checks for whether a user is allowed to enroll.
        try:
            course = CourseOverview.get_from_id(course_key)
            course_data = CourseData(
                course_key=course.id,
                display_name=course.display_name,
            )
        except CourseOverview.DoesNotExist:
            # This is here to preserve legacy behavior which allowed enrollment in courses
            # announced before the start of content creation.
            course_data = CourseData(
                course_key=course_key,
            )
            if check_access:
                log.warning("User %s failed to enroll in non-existent course %s", user.username, str(course_key))
                raise NonExistentCourseError  # lint-amnesty, pylint: disable=raise-missing-from

        if check_access:
            if cls.is_enrollment_closed(user, course) and not can_upgrade:
                log.warning(
                    "User %s failed to enroll in course %s because enrollment is closed (can_upgrade=%s).",
                    user.username,
                    str(course_key),
                    can_upgrade,
                )
                raise EnrollmentClosedError

            if cls.objects.is_course_full(course):
                log.warning(
                    "Course %s has reached its maximum enrollment of %d learners. User %s failed to enroll.",
                    str(course_key),
                    course.max_student_enrollments_allowed,
                    user.username,
                )
                raise CourseFullError
        if cls.is_enrolled(user, course_key):
            log.warning(
                "User %s attempted to enroll in %s, but they were already enrolled",
                user.username,
                str(course_key)
            )
            if check_access:
                raise AlreadyEnrolledError

        # User is allowed to enroll if they've reached this point.
        enrollment = cls.get_or_create_enrollment(user, course_key)
        enrollment.update_enrollment(is_active=True, mode=mode, enterprise_uuid=enterprise_uuid)
        enrollment.send_signal(EnrollStatusChange.enroll)

        # .. event_implemented_name: COURSE_ENROLLMENT_CREATED
        # .. event_type: org.openedx.learning.course.enrollment.created.v1
        COURSE_ENROLLMENT_CREATED.send_event(
            enrollment=CourseEnrollmentData(
                user=UserData(
                    pii=UserPersonalData(
                        username=user.username,
                        email=user.email,
                        name=user.profile.name,
                    ),
                    id=user.id,
                    is_active=user.is_active,
                ),
                course=course_data,
                mode=enrollment.mode,
                is_active=enrollment.is_active,
                creation_date=enrollment.created,
            )
        )

        return enrollment

    @classmethod
    def enroll_by_email(cls, email, course_id, mode=None, ignore_errors=True):
        """
        Enroll a user in a course given their email. This saves immediately.

        Note that  enrolling by email is generally done in big batches and the
        error rate is high. For that reason, we supress User lookup errors by
        default.

        Returns a CourseEnrollment object. If the User does not exist and
        `ignore_errors` is set to `True`, it will return None.

        `email` Email address of the User to add to enroll in the course.

        `course_id` is our usual course_id string (e.g. "edX/Test101/2013_Fall)

        `mode` is a string specifying what kind of enrollment this is. The
               default is the default course mode, 'audit'. Other options
               include 'professional', 'verified', 'honor',
               'no-id-professional' and 'credit'.
               See CourseMode in common/djangoapps/course_modes/models.py.

        `ignore_errors` is a boolean indicating whether we should suppress
                        `User.DoesNotExist` errors (returning None) or let it
                        bubble up.

        It is expected that this method is called from a method which has already
        verified the user authentication and access.
        """
        try:
            user = User.objects.get(email=email)
            return cls.enroll(user, course_id, mode)
        except User.DoesNotExist:
            err_msg = "Tried to enroll email {} into course {}, but user not found"
            log.error(err_msg.format(email, course_id))
            if ignore_errors:
                return None
            raise

    @classmethod
    def unenroll(cls, user, course_id, skip_refund=False):
        """
        Remove the user from a given course. If the relevant `CourseEnrollment`
        object doesn't exist, we log an error but don't throw an exception.

        `user` is a Django User object. If it hasn't been saved yet (no `.id`
               attribute), this method will automatically save it before
               adding an enrollment for it.

        `course_id` is our usual course_id string (e.g. "edX/Test101/2013_Fall)

        `skip_refund` can be set to True to avoid the refund process.
        """
        RequestCache('get_enrollment').clear()

        try:
            record = cls.objects.get(user=user, course_id=course_id)

            try:
                # .. filter_implemented_name: CourseUnenrollmentStarted
                # .. filter_type: org.openedx.learning.course.unenrollment.started.v1
                record = CourseUnenrollmentStarted.run_filter(enrollment=record)
            except CourseUnenrollmentStarted.PreventUnenrollment as exc:
                raise UnenrollmentNotAllowed(str(exc)) from exc

            record.update_enrollment(is_active=False, skip_refund=skip_refund)

        except cls.DoesNotExist:
            log.error(
                "Tried to unenroll student %s from %s but they were not enrolled",
                user,
                course_id
            )

    @classmethod
    def unenroll_by_email(cls, email, course_id):
        """
        Unenroll a user from a course given their email. This saves immediately.
        User lookup errors are logged but will not throw an exception.

        `email` Email address of the User to unenroll from the course.

        `course_id` is our usual course_id string (e.g. "edX/Test101/2013_Fall)
        """
        RequestCache('get_enrollment').clear()

        try:
            user = User.objects.get(email=email)
            return cls.unenroll(user, course_id)
        except User.DoesNotExist:
            log.error(
                "Tried to unenroll email %s from course %s, but user not found",
                email,
                course_id
            )

    @classmethod
    def is_enrolled(cls, user, course_key):
        """
        Returns True if the user is enrolled in the course (the entry must exist
        and it must have `is_active=True`). Otherwise, returns False.

        `user` is a Django User object. If it hasn't been saved yet (no `.id`
               attribute), this method will automatically save it before
               adding an enrollment for it.

        `course_id` is our usual course_id string (e.g. "edX/Test101/2013_Fall)
        """
        enrollment_state = cls._get_enrollment_state(user, course_key)
        return enrollment_state.is_active or False

    @classmethod
    def is_enrolled_by_partial(cls, user, course_id_partial):
        """
        Returns `True` if the user is enrolled in a course that starts with
        `course_id_partial`. Otherwise, returns False.

        Can be used to determine whether a student is enrolled in a course
        whose run name is unknown.

        `user` is a Django User object. If it hasn't been saved yet (no `.id`
               attribute), this method will automatically save it before
               adding an enrollment for it.

        `course_id_partial` (CourseKey) is missing the run component
        """
        assert isinstance(course_id_partial, CourseKey)
        assert not course_id_partial.run  # None or empty string
        course_key = CourseKey.from_string('/'.join([course_id_partial.org, course_id_partial.course, '']))
        querystring = str(course_key)
        try:
            return cls.objects.filter(
                user=user,
                course__id__startswith=querystring,
                is_active=1
            ).exists()
        except cls.DoesNotExist:
            return False

    @classmethod
    def enrollment_mode_for_user(cls, user, course_id):
        """
        Returns the enrollment mode for the given user for the given course

        `user` is a Django User object
        `course_id` is our usual course_id string (e.g. "edX/Test101/2013_Fall)

        Returns (mode, is_active) where mode is the enrollment mode of the student
            and is_active is whether the enrollment is active.
        Returns (None, None) if the courseenrollment record does not exist.
        """
        enrollment_state = cls._get_enrollment_state(user, course_id)
        return enrollment_state.mode, enrollment_state.is_active

    @classmethod
    def enrollments_for_user(cls, user):
        return cls.objects.filter(user=user, is_active=1).select_related('user')

    @classmethod
    def enrollments_for_user_with_overviews_preload(cls, user, courses_limit=None):  # pylint: disable=invalid-name
        """
        List of user's CourseEnrollments, CourseOverviews preloaded if possible.

        We try to preload all CourseOverviews, which are usually lazily loaded
        as the .course_overview property. This is to avoid making an extra
        query for every enrollment when displaying something like the student
        dashboard. If some of the CourseOverviews are not found, we make no
        attempt to initialize them -- we just fall back to existing lazy-load
        behavior. The goal is to optimize the most common case as simply as
        possible, without changing any of the existing contracts.

        The name of this method is long, but was the end result of hashing out a
        number of alternatives, so pylint can stuff it (disable=invalid-name)
        """
        enrollments = cls.enrollments_for_user(user).select_related('schedule', 'course', 'course__image_set')

        if courses_limit:
            return enrollments.order_by('-created')[:courses_limit]
        else:
            return enrollments

    @classmethod
    def enrollment_status_hash_cache_key(cls, user):
        """ Returns the cache key for the cached enrollment status hash.

        Args:
            user (User): User whose cache key should be returned.

        Returns:
            str: Cache key.
        """
        return 'enrollment_status_hash_' + user.username

    @classmethod
    def generate_enrollment_status_hash(cls, user):
        """ Generates a hash encoding the given user's *active* enrollments.

         Args:
             user (User): User whose enrollments should be hashed.

        Returns:
            str: Hash of the user's active enrollments. If the user is anonymous, `None` is returned.
        """
        assert user

        if user.is_anonymous:
            return None

        cache_key = cls.enrollment_status_hash_cache_key(user)
        status_hash = cache.get(cache_key)

        if not status_hash:
            enrollments = cls.enrollments_for_user(user).values_list('course_id', 'mode')
            enrollments = [(str(e[0]).lower(), e[1].lower()) for e in enrollments]
            enrollments = sorted(enrollments, key=lambda e: e[0])
            hash_elements = [user.username]
            hash_elements += [f'{e[0]}={e[1]}' for e in enrollments]
            status_hash = hashlib.md5('&'.join(hash_elements).encode('utf-8')).hexdigest()

            # The hash is cached indefinitely. It will be invalidated when the user enrolls/unenrolls.
            cache.set(cache_key, status_hash, None)

        return status_hash

    def is_paid_course(self):
        """
        Returns True, if course is paid
        """
        paid_course = CourseMode.is_white_label(self.course_id)
        if paid_course or CourseMode.is_professional_slug(self.mode):
            return True

        return False

    def activate(self):
        """Makes this `CourseEnrollment` record active. Saves immediately."""
        self.update_enrollment(is_active=True)

    def deactivate(self):
        """Makes this `CourseEnrollment` record inactive. Saves immediately. An
        inactive record means that the student is not enrolled in this course.
        """
        self.update_enrollment(is_active=False)

    def change_mode(self, mode):
        """Changes this `CourseEnrollment` record's mode to `mode`.  Saves immediately."""
        self.update_enrollment(mode=mode)

    def refundable(self):
        """
        For paid/verified certificates, students may always receive a refund if
        this CourseEnrollment's `can_refund` attribute is not `None` (that
        overrides all other rules).

        If the `.can_refund` attribute is `None` or doesn't exist, then ALL of
        the following must be true for this enrollment to be refundable:

            * The user does not have a certificate issued for this course.
            * We are not past the refund cutoff date
            * There exists a 'verified' CourseMode for this course.

        Returns:
            bool: Whether is CourseEnrollment can be refunded.
        """
        # In order to support manual refunds past the deadline, set can_refund on this object.
        # On unenrolling, the "UNENROLL_DONE" signal calls CertificateItem.refund_cert_callback(),
        # which calls this method to determine whether to refund the order.
        # This can't be set directly because refunds currently happen as a side-effect of unenrolling.
        # (side-effects are bad)

        if getattr(self, 'can_refund', None) is not None:
            return True

        # Due to circular import issues this import was placed close to usage. To move this to the
        # top of the file would require a large scale refactor of the refund code.
        import lms.djangoapps.certificates.api

        # If the student has already been given a certificate in a non refundable status they should not be refunded
        certificate = lms.djangoapps.certificates.api.get_certificate_for_user_id(
            self.user,
            self.course_id
        )
        if certificate and not CertificateStatuses.is_refundable_status(certificate.status):
            log.info(f"{self.user} has already been given a certificate therefore cannot be refunded.")
            return False

        # If it is after the refundable cutoff date they should not be refunded.
        refund_cutoff_date = self.refund_cutoff_date()
        # `refund_cuttoff_date` will be `None` if there is no order. If there is no order return `False`.
        if refund_cutoff_date is None:
            log.info("Refund cutoff date is null")
            return False
        if datetime.now(ZoneInfo("UTC")) > refund_cutoff_date:
            log.info(f"Refund cutoff date: {refund_cutoff_date} has passed")
            return False

        course_mode = CourseMode.mode_for_course(self.course_id, 'verified', include_expired=True)
        if course_mode is None:
            log.info(f"Course mode for {self.course_id} doesn't exist.")
            return False
        else:
            return True

    def refund_cutoff_date(self):
        """ Calculate and return the refund window end date. """
        # NOTE: This is here to avoid circular references
        from openedx.core.djangoapps.commerce.utils import ECOMMERCE_DATE_FORMAT
        date_placed = self.get_order_attribute_value('date_placed')
        log.info(f"Successfully retrieved date_placed: {date_placed} from order")

        if not date_placed:
            order_number = self.get_order_attribute_value('order_number')
            if not order_number:
                log.info("Failed to get order number")
                return None

            date_placed = self.get_order_attribute_from_ecommerce('date_placed')
            if not date_placed:
                log.info("Failed to get date_placed attribute")
                return None

            # also save the attribute so that we don't need to call ecommerce again.
            username = self.user.username
            enrollment_attributes = get_enrollment_attributes(username, str(self.course_id))
            enrollment_attributes.append(
                {
                    "namespace": "order",
                    "name": "date_placed",
                    "value": date_placed,
                }
            )
            set_enrollment_attributes(username, str(self.course_id), enrollment_attributes)

        refund_window_start_date = max(
            datetime.strptime(date_placed, ECOMMERCE_DATE_FORMAT),
            self.course_overview.start.replace(tzinfo=None)
        )

        return (
            refund_window_start_date.replace(tzinfo=ZoneInfo("UTC"))
            + EnrollmentRefundConfiguration.current().refund_window
        )

    def is_order_voucher_refundable(self):
        """ Checks if the coupon batch expiration date has passed to determine whether order voucher is refundable. """
        from openedx.core.djangoapps.commerce.utils import ECOMMERCE_DATE_FORMAT
        vouchers = self.get_order_attribute_from_ecommerce('vouchers')
        if not vouchers:
            return False
        voucher_end_datetime_str = vouchers[0]['end_datetime']
        voucher_expiration_date = (
            datetime.strptime(voucher_end_datetime_str, ECOMMERCE_DATE_FORMAT)
            .replace(tzinfo=ZoneInfo("UTC"))
        )
        return datetime.now(ZoneInfo("UTC")) < voucher_expiration_date

    def get_order_attribute_from_ecommerce(self, attribute_name):
        """
        Fetches the order details from ecommerce to return the value of the attribute passed as argument.

        Arguments:
            attribute_name (str): The name of the attribute that you want to fetch from response e:g 'number' or
            'vouchers', etc.

        Returns:
            (str | array | None): Returns the attribute value if it exists, returns None if the order doesn't exist or
            attribute doesn't exist in the response.
        """

        # NOTE: This is here to avoid circular references
        from openedx.core.djangoapps.commerce.utils import get_ecommerce_api_base_url, get_ecommerce_api_client
        order_number = self.get_order_attribute_value('order_number')
        if not order_number:
            return None

        # check if response is already cached
        cache_key = get_cache_key(user_id=self.user.id, order_number=order_number)
        cached_response = TieredCache.get_cached_response(cache_key)
        if cached_response.is_found:
            order = cached_response.value
        else:
            try:
                # response is not cached, so make a call to ecommerce to fetch order details
                api_url = urljoin(f"{get_ecommerce_api_base_url()}/", f"orders/{order_number}/")
                response = get_ecommerce_api_client(self.user).get(api_url)
                response.raise_for_status()
                order = response.json()
            except HTTPError as err:
                log.warning(
                    "Encountered HTTPError while getting order details from ecommerce. "
                    "Status code was %d, Order=%s and user %s", err.response.status_code, order_number, self.user.id
                )
                return None
            except RequestException:
                log.warning(
                    "Encountered an error while getting order details from ecommerce. "
                    "Order=%s and user %s", order_number, self.user.id
                )
                return None

            cache_time_out = getattr(settings, 'ECOMMERCE_ORDERS_API_CACHE_TIMEOUT', 3600)
            TieredCache.set_all_tiers(cache_key, order, cache_time_out)
        try:
            return order[attribute_name]
        except KeyError:
            return None

    def get_order_attribute_value(self, attr_name):
        """ Get and return course enrollment order attribute's value."""
        try:
            attribute = self.attributes.get(namespace='order', name=attr_name)
        except ObjectDoesNotExist:
            return None
        except MultipleObjectsReturned:
            # If there are multiple attributes then return the last one.
            enrollment_id = self.get_enrollment(self.user, self.course_id).id
            log.warning(
                "Multiple CourseEnrollmentAttributes found for user %s with enrollment-ID %s",
                self.user.id,
                enrollment_id
            )
            attribute = self.attributes.filter(namespace='order', name=attr_name).last()

        return attribute.value

    @property
    def username(self):
        return self.user.username

    @property
    def course_overview(self):
        """
        Returns a CourseOverview of the course to which this enrollment refers.
        Returns None if an error occurred while trying to load the course.

        Note:
            If the course is re-published within the lifetime of this
            CourseEnrollment object, then the value of this property will
            become stale.
        """
        if not self._course_overview:
            try:
                self._course_overview = self.course
            except CourseOverview.DoesNotExist:
                log.info('Course Overviews: unable to find course overview for enrollment, loading from modulestore.')
                try:
                    self._course_overview = CourseOverview.get_from_id(self.course_id)
                except (CourseOverview.DoesNotExist, OSError):
                    self._course_overview = None
        return self._course_overview

    @cached_property
    def verified_mode(self):
        return CourseMode.verified_mode_for_course(self.course_id)

    @cached_property
    def upgrade_deadline(self):
        """
        Returns the upgrade deadline for this enrollment, if it is upgradeable.
        If the seat cannot be upgraded, None is returned.
        Note:
            When loading this model, use `select_related` to retrieve the associated schedule object.
        Returns:
            datetime|None
        """
        log.debug('Schedules: Determining upgrade deadline for CourseEnrollment %d...', self.id)
        if not CourseMode.is_mode_upgradeable(self.mode):
            log.debug(
                'Schedules: %s mode of %s is not upgradeable. Returning None for upgrade deadline.',
                self.mode, self.course_id
            )
            return None

        if self.dynamic_upgrade_deadline is not None:
            # When course modes expire they aren't found any more and None would be returned.
            # Replicate that behavior here by returning None if the personalized deadline is in the past.
            if self.dynamic_upgrade_deadline <= datetime.now(ZoneInfo("UTC")):
                log.debug('Schedules: Returning None since dynamic upgrade deadline has already passed.')
                return None

            if self.verified_mode is None or CourseMode.is_professional_mode(self.verified_mode):
                log.debug('Schedules: Returning None for dynamic upgrade deadline since the course does not have a '
                          'verified mode.')
                return None

            return self.dynamic_upgrade_deadline

        return self.course_upgrade_deadline

    @cached_property
    def dynamic_upgrade_deadline(self):
        """
        Returns the learner's personalized upgrade deadline if one exists, otherwise it returns None.

        Note that this will return a value even if the deadline is in the past. This property can be used
        to modify behavior for users with personalized deadlines by checking if it's None or not.

        Returns:
            datetime|None
        """
        if not self.course_overview.self_paced:
            return None

        if not DynamicUpgradeDeadlineConfiguration.is_enabled():
            return None

        course_config = CourseDynamicUpgradeDeadlineConfiguration.current(self.course_id)
        if course_config.opted_out():
            # Course-level config should be checked first since it overrides the org-level config
            return None

        org_config = OrgDynamicUpgradeDeadlineConfiguration.current(self.course_id.org)
        if org_config.opted_out() and not course_config.opted_in():
            return None

        try:
            if not self.schedule or not self.schedule.enrollment.is_active:  # pylint: disable=no-member
                return None

            log.debug(
                'Schedules: Pulling upgrade deadline for CourseEnrollment %d from Schedule %d.',
                self.id, self.schedule.id  # lint-amnesty, pylint: disable=no-member
            )
            return self.schedule.upgrade_deadline  # lint-amnesty, pylint: disable=no-member
        except ObjectDoesNotExist:
            # NOTE: Schedule has a one-to-one mapping with CourseEnrollment. If no schedule is associated
            # with this enrollment, Django will raise an exception rather than return None.
            log.debug('Schedules: No schedule exists for CourseEnrollment %d.', self.id)
            return None

    @cached_property
    def course_upgrade_deadline(self):
        """
        Returns the expiration datetime for the verified course mode.

        If the mode is already expired, return None. Also return None if the course does not have a verified
        course mode.

        Returns:
            datetime|None
        """
        try:
            if self.verified_mode:
                log.debug('Schedules: Defaulting to verified mode expiration date-time for %s.', self.course_id)
                return self.verified_mode.expiration_datetime
            else:
                log.debug('Schedules: No verified mode located for %s.', self.course_id)
                return None
        except CourseMode.DoesNotExist:
            log.debug('Schedules: %s has no verified mode.', self.course_id)
            return None

    def is_verified_enrollment(self):
        """
        Check the course enrollment mode is verified or not
        """
        return CourseMode.is_verified_slug(self.mode)

    def is_professional_enrollment(self):
        """
        Check the course enrollment mode is professional or not
        """
        return CourseMode.is_professional_slug(self.mode)

    @classmethod
    def is_enrolled_as_verified(cls, user, course_key):
        """
        Check whether the course enrollment is for a verified mode.

        Arguments:
            user (User): The user object.
            course_key (CourseKey): The identifier for the course.

        Returns: bool

        """
        enrollment = cls.get_enrollment(user, course_key)
        return (
            enrollment is not None and
            enrollment.is_active and
            enrollment.is_verified_enrollment()
        )

    @classmethod
    def cache_key_name(cls, user_id, course_key):
        """Return cache key name to be used to cache current configuration.
        Args:
            user_id(int): Id of user.
            course_key(unicode): Unicode of course key

        Returns:
            Unicode cache key
        """
        return cls.COURSE_ENROLLMENT_CACHE_KEY.format(user_id, str(course_key))

    @classmethod
    def _get_enrollment_state(cls, user, course_key):
        """
        Returns the CourseEnrollmentState for the given user
        and course_key, caching the result for later retrieval.
        """
        assert user

        if user.is_anonymous:
            return CourseEnrollmentState(None, None)
        enrollment_state = cls._get_enrollment_in_request_cache(user, course_key)
        if not enrollment_state:
            try:
                record = cls.objects.get(user=user, course_id=course_key)
                enrollment_state = CourseEnrollmentState(record.mode, record.is_active)
            except cls.DoesNotExist:
                enrollment_state = CourseEnrollmentState(None, None)
            cls._update_enrollment_state_in_request_cache(user, course_key, enrollment_state)
        return enrollment_state

    @classmethod
    def bulk_fetch_enrollment_states(cls, users, course_key):
        """
        Bulk pre-fetches the enrollment states for the given users
        for the given course.
        """
        # before populating the cache with another bulk set of data,
        # remove previously cached entries to keep memory usage low.
        RequestCache(cls.MODE_CACHE_NAMESPACE).clear()

        records = cls.objects.filter(user__in=users, course_id=course_key).select_related('user')
        cache = cls._get_mode_active_request_cache()  # lint-amnesty, pylint: disable=redefined-outer-name
        for record in records:
            enrollment_state = CourseEnrollmentState(record.mode, record.is_active)
            cls._update_enrollment_state_in_cache(cache, record.user.id, course_key, enrollment_state)

    @classmethod
    def _get_mode_active_request_cache(cls):
        """
        Returns the request-specific cache for CourseEnrollment as dict.
        """
        return RequestCache(cls.MODE_CACHE_NAMESPACE).data

    @classmethod
    def _get_enrollment_in_request_cache(cls, user, course_key):
        """
        Returns the cached value (CourseEnrollmentState) for the user's
        enrollment in the request cache.  If not cached, returns None.
        """
        return cls._get_mode_active_request_cache().get((user.id, course_key))

    @classmethod
    def _update_enrollment_state_in_request_cache(cls, user, course_key, enrollment_state):
        """
        Updates the cached value for the user's enrollment in the
        request cache.
        """
        cls._update_enrollment_state_in_cache(cls._get_mode_active_request_cache(),
                                              user.id, course_key, enrollment_state)

    @classmethod
    def _update_enrollment_state_in_cache(cls, cache, user_id, course_key, enrollment_state):  # lint-amnesty, pylint: disable=redefined-outer-name
        """
        Updates the cached value for the user's enrollment in the
        given cache.
        """
        cache[(user_id, course_key)] = enrollment_state

    @classmethod
    def get_active_enrollments_in_course(cls, course_key):
        """
        Retrieves active CourseEnrollments for a given course and
        prefetches user information.
        """
        return cls.objects.filter(
            course_id=course_key,
            is_active=True,
        ).select_related(
            'user',
            'user__profile',
        )


class FBEEnrollmentExclusion(models.Model):
    """
    Disable FBE for enrollments in this table.

    .. no_pii:
    """
    enrollment = models.OneToOneField(
        CourseEnrollment,
        on_delete=models.DO_NOTHING,
    )

    def __str__(self):
        return f"[FBEEnrollmentExclusion] {self.enrollment}"


@receiver(models.signals.post_save, sender=CourseEnrollment)
@receiver(models.signals.post_delete, sender=CourseEnrollment)
def invalidate_enrollment_mode_cache(sender, instance, **kwargs):  # pylint: disable=unused-argument
    """
    Invalidate the cache of CourseEnrollment model.
    """

    cache_key = CourseEnrollment.cache_key_name(
        instance.user.id,
        str(instance.course_id)
    )
    cache.delete(cache_key)


@receiver(models.signals.post_save, sender=CourseEnrollment)
def update_expiry_email_date(sender, instance, **kwargs):  # pylint: disable=unused-argument
    """
    If the user has enrolled in verified track of a course and has expired ID
    verification then send email to get the ID verified by setting the
    expiry_email_date field.
    """
    email_config = getattr(settings, 'VERIFICATION_EXPIRY_EMAIL', {'DAYS_RANGE': 1, 'RESEND_DAYS': 15})

    if instance.mode == CourseMode.VERIFIED:
        SoftwareSecurePhotoVerification.update_expiry_email_date_for_user(instance.user, email_config)


class ManualEnrollmentAudit(models.Model):
    """
    Table for tracking which enrollments were performed through manual enrollment.

    .. pii: Contains enrolled_email, retired in LMSAccountRetirementView
    .. pii_types: email_address
    .. pii_retirement: local_api
    """
    enrollment = models.ForeignKey(CourseEnrollment, null=True, on_delete=models.CASCADE)
    enrolled_by = models.ForeignKey(User, null=True, on_delete=models.CASCADE)
    enrolled_email = models.CharField(max_length=255, db_index=True)
    time_stamp = models.DateTimeField(auto_now_add=True, null=True)
    state_transition = models.CharField(max_length=255, choices=TRANSITION_STATES)
    reason = models.TextField(null=True)
    role = models.CharField(blank=True, null=True, max_length=64)
    history = HistoricalRecords()

    @classmethod
    def create_manual_enrollment_audit(cls, user, email, state_transition, reason, enrollment=None, role=None):
        """
        saves the student manual enrollment information
        """
        return cls.objects.create(
            enrolled_by=user,
            enrolled_email=email,
            state_transition=state_transition,
            reason=reason,
            enrollment=enrollment,
            role=role,
        )

    @classmethod
    def get_manual_enrollment_by_email(cls, email):
        """
        if matches returns the most recent entry in the table filtered by email else returns None.
        """
        try:
            manual_enrollment = cls.objects.filter(enrolled_email=email).latest('time_stamp')
        except cls.DoesNotExist:
            manual_enrollment = None
        return manual_enrollment

    @classmethod
    def get_manual_enrollment(cls, enrollment):
        """
        Returns the most recent entry for the given enrollment, or None if there are no matches
        """
        try:
            manual_enrollment = cls.objects.filter(enrollment=enrollment).latest('time_stamp')
        except cls.DoesNotExist:
            manual_enrollment = None
        return manual_enrollment

    @classmethod
    def retire_manual_enrollments(cls, user, retired_email):
        """
        Removes PII (enrolled_email and reason) associated with the User passed in. Bubbles up any exceptions.
        """
        # This bit of ugliness is to fix a perfmance issue with Django using a slow
        # sub-select that caused the original query to take several seconds (PLAT-2371).
        # It is possible that this could also be bad if a user has thousands of manual
        # enrollments, but currently that number tends to be very low.
        manual_enrollment_ids = list(cls.objects.filter(enrollment__user=user).values_list('id', flat=True))
        manual_enrollment_audits = cls.objects.filter(id__in=manual_enrollment_ids)

        if not manual_enrollment_audits:
            return False

        for manual_enrollment_audit in manual_enrollment_audits:
            manual_enrollment_audit.history.update(reason="", enrolled_email=retired_email)
        manual_enrollment_audits.update(reason="", enrolled_email=retired_email)
        return True


class CourseEnrollmentAllowed(DeletableByUserValue, models.Model):
    """
    Table of users (specified by email address strings) who are allowed to enroll in a specified course.
    The user may or may not (yet) exist.  Enrollment by users listed in this table is allowed
    even if the enrollment time window is past.  Once an enrollment from this list effectively happens,
    the object is marked with the student who enrolled, to prevent students from changing e-mails and
    enrolling many accounts through the same e-mail.

    .. no_pii:
    """
    email = models.CharField(max_length=255, db_index=True)
    course_id = CourseKeyField(max_length=255, db_index=True)
    auto_enroll = models.BooleanField(default=0)
    user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        help_text="First user which enrolled in the specified course through the specified e-mail. "
                  "Once set, it won't change.",
        on_delete=models.CASCADE,
    )

    created = models.DateTimeField(auto_now_add=True, null=True, db_index=True)

    class Meta:
        unique_together = (('email', 'course_id'),)

    def __str__(self):
        return f"[CourseEnrollmentAllowed] {self.email}: {self.course_id} ({self.created})"

    @classmethod
    def for_user(cls, user):
        """
        Returns the CourseEnrollmentAllowed objects that can effectively be used by a particular `user`.
        This includes the ones that match the user's e-mail and excludes those CEA which were already consumed
        by a different user.
        """
        return cls.objects.filter(email=user.email).filter(Q(user__isnull=True) | Q(user=user))

    def valid_for_user(self, user):
        """
        Returns True if the CEA is usable by the given user, or False if it was already consumed by another user.
        """
        return self.user is None or self.user == user

    @classmethod
    def may_enroll_and_unenrolled(cls, course_id):
        """
        Return QuerySet of students who are allowed to enroll in a course.

        Result excludes students who have already enrolled in the
        course. Even if they change their emails after registration.

        `course_id` identifies the course for which to compute the QuerySet.
        """
        return CourseEnrollmentAllowed.objects.filter(course_id=course_id, user__isnull=True)


class CourseEnrollmentAttribute(models.Model):
    """
    Provide additional information about the user's enrollment.

    .. no_pii: This stores key/value pairs, of which there is no full list, but the ones currently in use are not PII
    """
    enrollment = models.ForeignKey(CourseEnrollment, related_name="attributes", on_delete=models.CASCADE)
    namespace = models.CharField(
        max_length=255,
        help_text=_("Namespace of enrollment attribute")
    )
    name = models.CharField(
        max_length=255,
        help_text=_("Name of the enrollment attribute")
    )
    value = models.CharField(
        max_length=255,
        help_text=_("Value of the enrollment attribute")
    )

    def __str__(self):
        """Unicode representation of the attribute. """
        return "{namespace}:{name}, {value}".format(
            namespace=self.namespace,
            name=self.name,
            value=self.value,
        )

    @classmethod
    def add_enrollment_attr(cls, enrollment, data_list):
        """
        Delete all the enrollment attributes for the given enrollment and
        add new attributes.

        Args:
            enrollment (CourseEnrollment): 'CourseEnrollment' for which attribute is to be added
            data_list: list of dictionaries containing data to save
        """
        cls.objects.filter(enrollment=enrollment).delete()
        attributes = [
            cls(enrollment=enrollment, namespace=data['namespace'], name=data['name'], value=data['value'])
            for data in data_list
        ]
        cls.objects.bulk_create(attributes)

    @classmethod
    def get_enrollment_attributes(cls, enrollment):
        """Retrieve list of all enrollment attributes.

        Args:
            enrollment(CourseEnrollment): 'CourseEnrollment' for which list is to retrieve

        Returns: list

        Example:
        >>> CourseEnrollmentAttribute.get_enrollment_attributes(CourseEnrollment)
        [
            {
                "namespace": "credit",
                "name": "provider_id",
                "value": "hogwarts",
            },
        ]
        """
        return [
            {
                "namespace": attribute.namespace,
                "name": attribute.name,
                "value": attribute.value,
            }
            for attribute in cls.objects.filter(enrollment=enrollment)
        ]


class EnrollmentRefundConfiguration(ConfigurationModel):
    """
    Configuration for course enrollment refunds.

    .. no_pii:
    """

    # TODO: Django 1.8 introduces a DurationField
    # (https://docs.djangoproject.com/en/1.8/ref/models/fields/#durationfield)
    # for storing timedeltas which uses MySQL's bigint for backing
    # storage. After we've completed the Django upgrade we should be
    # able to replace this field with a DurationField named
    # `refund_window` without having to run a migration or change
    # other code.
    refund_window_microseconds = models.BigIntegerField(
        default=1209600000000,
        help_text=_(
            "The window of time after enrolling during which users can be granted"
            " a refund, represented in microseconds. The default is 14 days."
        )
    )

    @property
    def refund_window(self):
        """Return the configured refund window as a `datetime.timedelta`."""
        return timedelta(microseconds=self.refund_window_microseconds)

    @refund_window.setter
    def refund_window(self, refund_window):
        """Set the current refund window to the given timedelta."""
        self.refund_window_microseconds = int(refund_window.total_seconds() * 1000000)


class BulkUnenrollConfiguration(ConfigurationModel):  # lint-amnesty, pylint: disable=empty-docstring
    """
    .. no_pii:
    """
    csv_file = models.FileField(
        validators=[FileExtensionValidator(allowed_extensions=['csv'])],
        help_text=_("It expect that the data will be provided in a csv file format with \
                    first row being the header and columns will be as follows: \
                    user_id, username, email, course_id, is_verified, verification_date")
    )


class BulkChangeEnrollmentConfiguration(ConfigurationModel):
    """
    config model for the bulk_change_enrollment_csv command

    .. no_pii:
    """
    csv_file = models.FileField(
        validators=[FileExtensionValidator(allowed_extensions=['csv'])],
        help_text=_("It expect that the data will be provided in a csv file format with \
                    first row being the header and columns will be as follows: \
                    course_id, username, mode")
    )


class CourseEnrollmentCelebration(TimeStampedModel):
    """
    Keeps track of how we've celebrated a user's course progress.

    An example of a celebration is a dialog that pops up after you complete your first section
    in a course saying "good job!". Just some positive feedback like that. (This specific example is
    controlled by the celebrated_first_section field below.)

    In general, if a row does not exist for an enrollment, we don't want to show any celebrations.
    We don't want to suddenly inject celebrations in the middle of a course, because they
    might not make contextual sense and it's an inconsistent experience. The helper methods below
    (starting with "should_") can help by looking up values with appropriate fallbacks.

    See the create_course_enrollment_celebration signal handler for how these get created.

    .. no_pii:
    """
    enrollment = models.OneToOneField(CourseEnrollment, models.CASCADE, related_name='celebration')
    celebrate_first_section = models.BooleanField(default=False)
    celebrate_weekly_goal = models.BooleanField(default=False)

    def __str__(self):
        return (
            '[CourseEnrollmentCelebration] course: {}; user: {}'
        ).format(self.enrollment.course.id, self.enrollment.user.username)

    @staticmethod
    def should_celebrate_first_section(enrollment):
        """
        Returns the celebration value for first_section with appropriate fallback if it doesn't exist.

        The frontend will use this result and additional information calculated to actually determine
        if the first section celebration will render. In other words, the value returned here is
        NOT the final value used.
        """
        if not enrollment:
            return False
        try:
            return enrollment.celebration.celebrate_first_section
        except CourseEnrollmentCelebration.DoesNotExist:
            return False

    @staticmethod
    def should_celebrate_weekly_goal(enrollment):
        """
        Returns the celebration value for weekly_goal with appropriate fallback if it doesn't exist.

        The frontend will use this result directly to determine if the weekly goal celebration
        should be rendered. The value returned here IS the final value used.
        """
        # Avoiding circular import
        from lms.djangoapps.course_goals.models import CourseGoal, UserActivity
        try:
            if not enrollment or not enrollment.celebration.celebrate_weekly_goal:
                return False
        except CourseEnrollmentCelebration.DoesNotExist:
            return False

        try:
            goal = CourseGoal.objects.get(user=enrollment.user, course_key=enrollment.course.id)
            if not goal.days_per_week:
                return False

            today = date.today()
            monday_date = today - timedelta(days=today.weekday())
            week_activity_count = UserActivity.objects.filter(
                user=enrollment.user, course_key=enrollment.course.id, date__gte=monday_date,
            ).count()
            return week_activity_count == goal.days_per_week
        except CourseGoal.DoesNotExist:
            return False
