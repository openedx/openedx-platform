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
from openedx.core.djangoapps.enrollments.api import get_enrollment_attributes, get_enrollments, update_enrollment
from openedx.core.djangoapps.enrollments.errors import CourseModeNotFoundError
from openedx.core.djangoapps.enrollments.serializers import ModeSerializer
from openedx.features.enterprise_support.api import (
    enterprise_enabled,
    get_data_sharing_consents,
    get_enterprise_course_enrollments,
)
from openedx.features.enterprise_support.serializers import EnterpriseCourseEnrollmentSerializer

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

    def _enterprise_course_enrollments_by_course_id(self, user):
        """
        Returns a dict containing enterprise course enrollments data with
        course ids as keys.
        """
        enterprise_course_enrollments = get_enterprise_course_enrollments(user)
        data_sharing_consents_for_user = get_data_sharing_consents(user)

        enterprise_enrollments_by_course_id = defaultdict(list)
        consent_by_course_and_enterprise_customer_id = {}

        # Get data sharing consent for each enterprise enrollment
        for consent in data_sharing_consents_for_user:
            key = f'{consent.course_id}-{consent.enterprise_customer_id}'
            consent_by_course_and_enterprise_customer_id[key] = consent.serialize()

        for enterprise_course_enrollment in enterprise_course_enrollments:
            serialized_enterprise_course_enrollment = EnterpriseCourseEnrollmentSerializer(
                enterprise_course_enrollment
            ).data
            course_id = enterprise_course_enrollment.course_id
            enterprise_customer_id = enterprise_course_enrollment.enterprise_customer_user.enterprise_customer_id
            key = f'{course_id}-{enterprise_customer_id}'
            consent = consent_by_course_and_enterprise_customer_id.get(key)
            serialized_enterprise_course_enrollment['data_sharing_consent'] = consent
            enterprise_enrollments_by_course_id[course_id].append(serialized_enterprise_course_enrollment)

        return enterprise_enrollments_by_course_id

    @method_decorator(require_support_permission)
    def get(self, request, username_or_email):
        """
        Returns a list of enrollments for the given user, along with
        information about previous manual enrollment changes.
        """
        try:
            user = User.objects.get(Q(username=username_or_email) | Q(email=username_or_email))
        except User.DoesNotExist:
            return JsonResponse([])

        enrollments = get_enrollments(user.username, include_inactive=True)

        for enrollment in enrollments:
            # Folds the course_details field up into the main JSON object.
            enrollment.update(**enrollment.pop('course_details'))
            course_key = CourseKey.from_string(enrollment['course_id'])
            # Get the all courses modes and replace with existing modes.
            enrollment['course_modes'] = self.get_course_modes(course_key)
            # Add the price of the course's verified mode.
            self.include_verified_mode_info(enrollment, course_key)
            # Add order number associated with the enrollment if available
            self.include_order_number(enrollment)
            self.include_source_system(enrollment)
            # Add manual enrollment history, if it exists
            enrollment['manual_enrollment'] = self.manual_enrollment_data(enrollment, course_key)

        if enterprise_enabled():
            enterprise_enrollments_by_course_id = self._enterprise_course_enrollments_by_course_id(user)
            for enrollment in enrollments:
                enterprise_course_enrollments = enterprise_enrollments_by_course_id.get(enrollment['course_id'], [])
                enrollment['enterprise_course_enrollments'] = enterprise_course_enrollments

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
    def include_order_number(enrollment):
        """
        For a provided enrollment data dictionary, include order_number from CourseEnrollmentAttribute if available.

        From all the attributes of a course enrollment:

          * Filter order_number attributes namespaced under `order`
          * Use the last/latest order number attr if multiple attrs found
             * This is to keep the usage consistent with get_order_attribute_value method in CourseEnrollment
        """
        username = enrollment['user']
        course_id = enrollment['course_id']
        enrollment_attributes = get_enrollment_attributes(username, course_id)
        order_attribute = [
            enrollment_attribute.get('value', '') for enrollment_attribute in enrollment_attributes if
            enrollment_attribute['namespace'] == 'order' and enrollment_attribute['name'] == 'order_number'
        ]
        if len(order_attribute) > 1:
            # logging this warning for info/debug purpose
            logger.warning(
                "Found multiple order name attributes for CourseEnrollment for user %s with course %s",
                username,
                course_id
            )
        enrollment['order_number'] = order_attribute[-1] if order_attribute else ''

    @staticmethod
    def include_source_system(enrollment):
        """
        For a provided enrollment data dictionary, include source_system from CourseEnrollmentAttribute if available.

        From all the attributes of a course enrollment:

          * Filter source_system attribute namespaced under `order`
        """
        username = enrollment['user']
        course_id = enrollment['course_id']
        enrollment_attributes = get_enrollment_attributes(username, course_id)
        source_system = ''
        for attribute in enrollment_attributes:
            if attribute['namespace'] == 'order' and attribute['name'] == 'source_system':
                source_system = attribute.get('value', '')
                break
        enrollment['source_system'] = source_system

    @staticmethod
    def manual_enrollment_data(enrollment_data, course_key):
        """
        Returns serialized information about the manual enrollment
        belonging to this enrollment, if it exists.

        Args:
          enrollment_data (dict): Representation of a single course enrollment.
          course_key (CourseKey): The course for this enrollment.

        Returns:
          None: If no manual enrollment change has been made.
          dict: Serialization of the latest manual enrollment change.
        """
        user = User.objects.get(username=enrollment_data['user'])
        enrollment = CourseEnrollment.get_enrollment(user, course_key)
        manual_enrollment_audit = ManualEnrollmentAudit.get_manual_enrollment(enrollment)
        if manual_enrollment_audit is None:
            return {}
        return ManualEnrollmentSerializer(instance=manual_enrollment_audit).data

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
