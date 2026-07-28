""" API v1 models. """


import logging
from itertools import groupby

from django.db import transaction
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.models import CourseMode
from lms.djangoapps.verify_student.models import VerificationDeadline
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

log = logging.getLogger(__name__)

UNDEFINED = object()


class Course:
    """ Pseudo-course model used to group CourseMode objects. """
    id = None  # pylint: disable=invalid-name
    modes = None
    _deleted_modes = None

    def __init__(self, id, modes, **kwargs):  # pylint: disable=redefined-builtin
        self.id = CourseKey.from_string(str(id))  # pylint: disable=invalid-name
        self.modes = list(modes)
        self.verification_deadline = UNDEFINED
        if 'verification_deadline' in kwargs:
            self.verification_deadline = kwargs['verification_deadline']
        self._deleted_modes = []

    @property
    def name(self):
        """ Return course name. """
        course_id = CourseKey.from_string(str(self.id))

        try:
            return CourseOverview.get_from_id(course_id).display_name
        except CourseOverview.DoesNotExist:
            # NOTE (CCB): Ideally, the course modes table should only contain data for courses that exist in
            # modulestore. If that is not the case, say for local development/testing, carry on without failure.
            log.warning('Failed to retrieve CourseOverview for [%s]. Using empty course name.', course_id)
            return None

    def get_mode_display_name(self, mode):
        """ Returns display name for the given mode. """
        slug = mode.mode_slug.strip().lower()

        if slug == 'credit':
            return 'Credit'
        if 'professional' in slug:
            return 'Professional Education'
        elif slug == 'verified':
            return 'Verified Certificate'
        elif slug == 'honor':
            return 'Honor Certificate'
        elif slug == 'audit':
            return 'Audit'

        return mode.mode_slug

    @transaction.atomic
    def save(self, *args, **kwargs):  # pylint: disable=unused-argument
        """ Save the CourseMode objects to the database. """

        if self.verification_deadline is not UNDEFINED:
            # Override the verification deadline for the course (not the individual modes)
            # This will delete verification deadlines for the course if self.verification_deadline is null
            VerificationDeadline.set_deadline(self.id, self.verification_deadline, is_explicit=True)

        for mode in self.modes:
            mode.course_id = self.id
            mode.mode_display_name = self.get_mode_display_name(mode)
            mode.save()

        deleted_mode_ids = [mode.id for mode in self._deleted_modes]
        CourseMode.objects.filter(id__in=deleted_mode_ids).delete()
        self._deleted_modes = []

    def update(self, attrs):
        """ Update the model with external data (usually passed via API call). """
        # There are possible downstream effects of settings self.verification_deadline to null,
        # so don't assign it a value here unless it is specifically included in attrs.
        if 'verification_deadline' in attrs:
            self.verification_deadline = attrs.get('verification_deadline')

        existing_modes = {mode.mode_slug: mode for mode in self.modes}
        merged_modes = set()
        merged_mode_keys = set()

        for posted_mode in attrs.get('modes', []):
            merged_mode = existing_modes.get(posted_mode.mode_slug, CourseMode())

            merged_mode.course_id = self.id
            merged_mode.mode_slug = posted_mode.mode_slug
            merged_mode.mode_display_name = posted_mode.mode_slug
            merged_mode.min_price = posted_mode.min_price
            merged_mode.currency = posted_mode.currency
            merged_mode.sku = posted_mode.sku
            merged_mode.bulk_sku = posted_mode.bulk_sku
            merged_mode.android_sku = posted_mode.android_sku
            merged_mode.ios_sku = posted_mode.ios_sku
            merged_mode.expiration_datetime = posted_mode.expiration_datetime
            merged_mode.save()

            merged_modes.add(merged_mode)
            merged_mode_keys.add(merged_mode.mode_slug)

        # Masters degrees are not sold through the eCommerce site.
        # So, Masters course modes are not included in PUT calls to this API,
        # and their omission which would normally cause them to be deleted.
        # We don't want that to happen, but for the time being,
        # we cannot include in Masters modes in the PUT calls from eCommerce.
        # So, here's hack to handle Masters course modes, along with any other
        # modes that end up in that boat.
        MODES_TO_NOT_DELETE = {
            CourseMode.MASTERS,
        }

        modes_to_delete = set(existing_modes.keys()) - merged_mode_keys
        modes_to_delete -= MODES_TO_NOT_DELETE
        self._deleted_modes = [existing_modes[mode] for mode in modes_to_delete]
        self.modes = list(merged_modes)

    @classmethod
    def get(cls, course_id):
        """ Retrieve a single course. """
        try:
            course_id = CourseKey.from_string(str(course_id))
        except InvalidKeyError:
            log.debug('[%s] is not a valid course key.', course_id)
            raise ValueError  # pylint: disable=raise-missing-from  # noqa: B904

        course_modes = CourseMode.objects.filter(course_id=course_id)

        if course_modes:
            verification_deadline = VerificationDeadline.deadline_for_course(course_id)
            return cls(course_id, list(course_modes), verification_deadline=verification_deadline)

        return None

    @classmethod
    def iterator(cls):
        """ Generator that yields all courses. """
        course_modes = CourseMode.objects.order_by('course_id')

        for course_id, modes in groupby(course_modes, lambda o: o.course_id):
            yield cls(course_id, list(modes))
