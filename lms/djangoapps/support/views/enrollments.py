"""
Support tool for changing course enrollments.
"""
import logging
from collections import defaultdict

import markupsafe
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.generic import View
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from openedx_filters.learning.filters import SupportEnrollmentDataRequested
from rest_framework.generics import GenericAPIView

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.edxmako.shortcuts import render_to_response
from common.djangoapps.entitlements.models import CourseEntitlement
from common.djangoapps.student.models import (
    ENROLLED_TO_ENROLLED,
    UNENROLLED_TO_ENROLLED,
    CourseEnrollment,
    CourseEnrollmentAttribute,
    ManualEnrollmentAudit,
)
from common.djangoapps.util.json_request import JsonResponse
from lms.djangoapps.support.decorators import require_support_permission
from lms.djangoapps.support.serializers import ManualEnrollmentSerializer
from lms.djangoapps.verify_student.models import VerificationDeadline
from openedx.core.djangoapps.credit.email_utils import get_credit_provider_attribute_values
from openedx.core.djangoapps.enrollments.api import get_enrollments, update_enrollment
from openedx.core.djangoapps.enrollments.errors import CourseModeNotFoundError
from openedx.core.djangoapps.enrollments.serializers import ModeSerializer

logger = logging.getLogger(__name__)


class EnrollmentSupportView(View):
    """
    View for viewing and changing learner enrollments, used by the
    support team.
    """

    @method_decorator(require_support_permission)
    def get(self, request):
        """Render the enrollment support tool view."""
        return render_to_response('support/enrollment.html', {
            'username': request.GET.get('user', ''),
            'enrollmentsUrl': reverse('support:enrollment_list'),
            'enrollmentSupportUrl': reverse('support:enrollment')
        })


class EnrollmentSupportListView(GenericAPIView):
    """
    Allows viewing and changing learner enrollments by support
    staff.
    """
    # TODO: ARCH-91
    # This view is excluded from Swagger doc generation because it
    # does not specify a serializer class.
    exclude_from_schema = True

    @method_decorator(require_support_permission)
    def get(self, request, username_or_email):
        """
        Returns a list of enrollments for the given user, along with
        information about previous manual enrollment changes.

        The optional ``order_number`` query parameter limits the list to
        enrollments from that order.
        """
        try:
            user = User.objects.get(Q(username=username_or_email) | Q(email=username_or_email))
        except User.DoesNotExist:
            return JsonResponse([])

        order_number = request.GET.get('order_number')
        order_attributes = self.get_order_attributes(user)
        manual_enrollments = self.get_manual_enrollments(user)
        enrollments = []

        for enrollment in get_enrollments(user.username, include_inactive=True):
            # Folds the course_details field up into the main JSON object.
            enrollment.update(**enrollment.pop('course_details'))
            order = order_attributes.get(enrollment['course_id'], {})
            if order_number and order.get('order_number') != order_number:
                continue
            course_key = CourseKey.from_string(enrollment['course_id'])
            # Get the all courses modes and replace with existing modes.
            enrollment['course_modes'] = self.get_course_modes(course_key)
            # Add the price of the course's verified mode.
            self.include_verified_mode_info(enrollment, course_key)
            # Add order number and source system associated with the enrollment if available
            enrollment['order_number'] = order.get('order_number', '')
            enrollment['source_system'] = order.get('source_system', '')
            # Add manual enrollment history, if it exists
            enrollment['manual_enrollment'] = manual_enrollments.get(enrollment['course_id'], {})
            enrollments.append(enrollment)

        enrollments, _ = SupportEnrollmentDataRequested.run_filter(enrollments_data=enrollments, user=user)

        return JsonResponse(enrollments)

    @method_decorator(require_support_permission)
    def post(self, request, username_or_email):
        """
        Allows support staff to create a user's enrollment.
        """
        try:
            course_id = request.data['course_id']
            course_key = CourseKey.from_string(course_id)
            mode = request.data['mode']
            reason = request.data['reason']
            user = User.objects.get(Q(username=username_or_email) | Q(email=username_or_email))
        except KeyError as err:
            return HttpResponseBadRequest(f'The field {str(err)} is required.')
        except InvalidKeyError:
            return HttpResponseBadRequest('Could not parse course key.')
        except User.DoesNotExist:
            return HttpResponseBadRequest(
                'Could not find user {username}.'.format(  # noqa: UP032
                    username=username_or_email
                )
            )

        enrollment = CourseEnrollment.get_enrollment(user=user, course_key=course_key)
        if enrollment is not None:
            return HttpResponseBadRequest(
                f'The user {str(username_or_email)} is already enrolled in {str(course_id)}.'
            )

        enrollment_modes = [
            enrollment_mode['slug']
            for enrollment_mode in self.get_course_modes(course_key)
        ]
        if mode not in enrollment_modes:
            return HttpResponseBadRequest(
                f'{markupsafe.escape(mode)} is not a valid mode for {str(course_id)}. '
                f'Possible valid modes are {str(enrollment_modes)}'
            )

        enrollment = CourseEnrollment.enroll(user=user, course_key=course_key, mode=mode)

        # Wrapped in a transaction so that we can be sure the
        # ManualEnrollmentAudit record is always created correctly.
        with transaction.atomic():
            manual_enrollment = ManualEnrollmentAudit.create_manual_enrollment_audit(
                request.user,
                enrollment.user.email,
                UNENROLLED_TO_ENROLLED,
                reason=reason,
                enrollment=enrollment
            )
            return JsonResponse(ManualEnrollmentSerializer(instance=manual_enrollment).data)

    @method_decorator(require_support_permission)
    def patch(self, request, username_or_email):
        """Allows support staff to alter a user's enrollment."""
        try:
            user = User.objects.get(Q(username=username_or_email) | Q(email=username_or_email))
            course_id = request.data['course_id']
            course_key = CourseKey.from_string(course_id)
            old_mode = request.data['old_mode']
            new_mode = request.data['new_mode']
            reason = request.data['reason']
            enrollment = CourseEnrollment.objects.get(user=user, course_id=course_key)
            if enrollment.mode != old_mode:
                return HttpResponseBadRequest(
                    f'User {user.username} is not enrolled with mode {markupsafe.escape(old_mode)}.'
                )
        except KeyError as err:
            return HttpResponseBadRequest(f'The field {str(err)} is required.')
        except InvalidKeyError:
            return HttpResponseBadRequest('Could not parse course key.')
        except (CourseEnrollment.DoesNotExist, User.DoesNotExist):
            return HttpResponseBadRequest(
                'Could not find enrollment for user {username} in course {course}.'.format(  # noqa: UP032
                    username=username_or_email,
                    course=str(course_key)
                )
            )
        try:
            # Wrapped in a transaction so that we can be sure the
            # ManualEnrollmentAudit record is always created correctly.
            with transaction.atomic():
                update_enrollment(user.username, course_id, mode=new_mode, include_expired=True)
                manual_enrollment = ManualEnrollmentAudit.create_manual_enrollment_audit(
                    request.user,
                    enrollment.user.email,
                    ENROLLED_TO_ENROLLED,
                    reason=reason,
                    enrollment=enrollment
                )
                if new_mode == CourseMode.CREDIT_MODE:
                    provider_ids = get_credit_provider_attribute_values(course_key, 'id')
                    credit_provider_attr = {
                        'namespace': 'credit',
                        'name': 'provider_id',
                        'value': provider_ids[0],
                    }
                    CourseEnrollmentAttribute.add_enrollment_attr(
                        enrollment=enrollment, data_list=[credit_provider_attr]
                    )
                entitlement = CourseEntitlement.get_fulfillable_entitlement_for_user_course_run(
                    user=user, course_run_key=course_id
                )
                if entitlement is not None and entitlement.mode == new_mode:
                    entitlement.set_enrollment(CourseEnrollment.get_enrollment(user, course_id))
                return JsonResponse(ManualEnrollmentSerializer(instance=manual_enrollment).data)
        except CourseModeNotFoundError as err:
            return HttpResponseBadRequest(str(err))

    @staticmethod
    def include_verified_mode_info(enrollment_data, course_key):
        """
        Add information about the verified mode for the given
        `course_key`, if that course has a verified mode.

        Args:
          enrollment_data (dict): Dictionary representing a single enrollment.
          course_key (CourseKey): The course which this enrollment belongs to.

        Returns:
          None
        """
        course_modes = enrollment_data['course_modes']
        for mode in course_modes:
            if mode['slug'] == CourseMode.VERIFIED:
                enrollment_data['verified_price'] = mode['min_price']
                enrollment_data['verified_upgrade_deadline'] = mode['expiration_datetime']
                enrollment_data['verification_deadline'] = VerificationDeadline.deadline_for_course(course_key)

    @staticmethod
    def get_order_attributes(user):
        """
        Returns the user's ``order`` enrollment attributes, keyed by course, in one query.

        The last ``order_number`` wins, consistent with ``CourseEnrollment.get_order_attribute_value``.
        The first ``source_system`` wins.

        Args:
          user (User): The learner whose enrollment attributes to load.

        Returns:
          dict: ``{course_id: {'order_number': str, 'source_system': str}}``. A course without
          order attributes is absent, and a course missing one of them has no key for it.
        """
        order_attributes = defaultdict(dict)
        attributes = CourseEnrollmentAttribute.objects.filter(
            enrollment__user=user, namespace='order', name__in=('order_number', 'source_system'),
        ).order_by('id').values_list('enrollment__course_id', 'name', 'value')
        for course_key, name, value in attributes:
            course_attributes = order_attributes[str(course_key)]
            if name == 'order_number' and name in course_attributes:
                # logging this warning for info/debug purpose
                logger.warning(
                    "Found multiple order name attributes for CourseEnrollment for user %s with course %s",
                    user.id,
                    course_key
                )
            if name == 'order_number' or name not in course_attributes:
                course_attributes[name] = value
        return order_attributes

    @staticmethod
    def get_manual_enrollments(user):
        """
        Returns the latest manual enrollment change for each of the user's enrollments, in one query.

        Args:
          user (User): The learner whose manual enrollment changes to load.

        Returns:
          dict: ``{course_id: dict}``, each value a ``ManualEnrollmentSerializer`` serialization.
          A course without manual enrollment changes is absent.
        """
        audits = ManualEnrollmentAudit.objects.filter(
            enrollment__user=user,
        ).select_related('enrollment', 'enrolled_by').order_by('time_stamp', 'id')
        latest_audits = {str(audit.enrollment.course_id): audit for audit in audits}
        return {
            course_id: ManualEnrollmentSerializer(instance=audit).data
            for course_id, audit in latest_audits.items()
        }

    @staticmethod
    def get_course_modes(course_key):
        """
        Returns a list of all modes including expired modes for a given course id

        Arguments:
            course_id (CourseKey): Search for course modes for this course.

        Returns:
            list of `Mode`

        """
        course_modes = CourseMode.modes_for_course(
            course_key,
            include_expired=True,
            only_selectable=False,
        )
        return [
            ModeSerializer(mode).data
            for mode in course_modes
        ]
