"""
Views for the Entitlements v1 API.
"""

import logging

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django_filters.rest_framework import DjangoFilterBackend
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.paginators import DefaultPagination
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework import permissions, status, viewsets
from rest_framework.response import Response

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.entitlements.models import (  # pylint: disable=line-too-long
    CourseEntitlement,
    CourseEntitlementPolicy,
    CourseEntitlementSupportDetail,
)
from common.djangoapps.entitlements.rest_api.v1.filters import CourseEntitlementFilter
from common.djangoapps.entitlements.rest_api.v1.permissions import IsAdminOrSupportOrAuthenticatedReadOnly
from common.djangoapps.entitlements.rest_api.v1.serializers import CourseEntitlementSerializer
from common.djangoapps.entitlements.utils import is_course_run_entitlement_fulfillable
from common.djangoapps.student.models import AlreadyEnrolledError, CourseEnrollment, CourseEnrollmentException
from openedx.core.djangoapps.catalog.utils import get_course_runs_for_course, get_owners_for_course
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.cors_csrf.authentication import SessionAuthenticationCrossDomainCsrf
from openedx.core.djangoapps.user_api.preferences.api import update_email_opt_in

User = get_user_model()
log = logging.getLogger(__name__)


class EntitlementsPagination(DefaultPagination):
    """
    Paginator for entitlements API.
    """
    page_size = 50
    max_page_size = 100


@transaction.atomic
def _unenroll_entitlement(course_entitlement, course_run_key):
    """
    Internal method to handle the details of Unenrolling a User in a Course Run.
    """
    CourseEnrollment.unenroll(course_entitlement.user, course_run_key, skip_refund=True)


@transaction.atomic
def _process_revoke_and_unenroll_entitlement(course_entitlement, is_refund=False):
    """
    Process the revoke of the Course Entitlement and refund if needed

    Arguments:
        course_entitlement: Course Entitlement Object

        is_refund (bool): True if a refund should be processed

    Exceptions:
        IntegrityError if there is an issue that should reverse the database changes
    """
    if course_entitlement.expired_at is None:
        course_entitlement.expire_entitlement()
        log.info(
            'Set expired_at to [%s] for course entitlement [%s]',
            course_entitlement.expired_at,
            course_entitlement.uuid
        )

    if course_entitlement.enrollment_course_run is not None:
        course_id = course_entitlement.enrollment_course_run.course_id
        _unenroll_entitlement(course_entitlement, course_id)
        log.info(
            'Unenrolled user [%s] from course run [%s] as part of revocation of course entitlement [%s]',
            course_entitlement.user.username,
            course_id,
            course_entitlement.uuid
        )

    if is_refund:
        course_entitlement.refund()


def set_entitlement_policy(entitlement, site):
    """
    Assign the appropriate CourseEntitlementPolicy to the given CourseEntitlement based on its mode and site.

    Arguments:
        entitlement: Course Entitlement object
        site: string representation of a Site object

    Notes:
        Site-specific, mode-agnostic policies take precedence over mode-specific, site-agnostic policies.
        If no appropriate CourseEntitlementPolicy is found, the default CourseEntitlementPolicy is assigned.
    """
    policy_mode = entitlement.mode
    if CourseMode.is_professional_slug(policy_mode):
        policy_mode = CourseMode.PROFESSIONAL
    filter_query = (Q(site=site) | Q(site__isnull=True)) & (Q(mode=policy_mode) | Q(mode__isnull=True))
    policy = CourseEntitlementPolicy.objects.filter(filter_query).order_by('-site', '-mode').first()
    entitlement.policy = policy if policy else None
    entitlement.save()


class EntitlementViewSet(viewsets.ModelViewSet):
    """
    ViewSet for the Entitlements API.
    """
    ENTITLEMENT_UUID4_REGEX = '[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}'

    authentication_classes = (JwtAuthentication, SessionAuthenticationCrossDomainCsrf,)
    permission_classes = (permissions.IsAuthenticated, IsAdminOrSupportOrAuthenticatedReadOnly,)
    lookup_value_regex = ENTITLEMENT_UUID4_REGEX
    lookup_field = 'uuid'
    serializer_class = CourseEntitlementSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = CourseEntitlementFilter
    pagination_class = EntitlementsPagination

    def get_queryset(self):
        user = self.request.user

        if self.request.method in permissions.SAFE_METHODS:
            if (user.is_staff and
                    (self.request.query_params.get('user', None) is not None or
                     self.kwargs.get('uuid', None) is not None)):
                # Return the full query set so that the Filters class can be used to apply,
                # - The UUID Filter
                # - The User Filter to the GET request
                return CourseEntitlement.objects.all().select_related('user').select_related('enrollment_course_run')
            # Non Staff Users will only be able to retrieve their own entitlements
            return CourseEntitlement.objects.filter(user=user).select_related('user').select_related(
                'enrollment_course_run'
            )
        # All other methods require the full Query set and the Permissions class already restricts access to them
        # to Admin users
        return CourseEntitlement.objects.all().select_related('user').select_related('enrollment_course_run')

    def get_upgradeable_enrollments_for_entitlement(self, entitlement):
        """
        Retrieve all the CourseEnrollments that are upgradeable for a given CourseEntitlement

        Arguments:
            entitlement: CourseEntitlement that we are requesting the CourseEnrollments for.

        Returns:
            list: List of upgradeable CourseEnrollments
        """
        # find all course_runs within the course
        course_runs = get_course_runs_for_course(entitlement.course_uuid)

        # check if the user has enrollments for any of the course_runs
        upgradeable_enrollments = []
        for course_run in course_runs:
            course_run_id = CourseKey.from_string(course_run.get('key'))
            enrollment = CourseEnrollment.get_enrollment(entitlement.user, course_run_id)

            if (enrollment and
                    enrollment.is_active and
                    is_course_run_entitlement_fulfillable(course_run_id, entitlement)):
                upgradeable_enrollments.append(enrollment)

        return upgradeable_enrollments

    def create(self, request, *args, **kwargs):
        support_details = request.data.pop('support_details', [])
        email_opt_in = request.data.pop('email_opt_in', False)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)

        entitlement = serializer.instance
        set_entitlement_policy(entitlement, request.site)

        # The owners for a course are the organizations that own the course. By taking owner.key,
        # we are able to pass in the organization key for email_opt_in
        owners = get_owners_for_course(entitlement.course_uuid)
        for owner in owners:
            update_email_opt_in(entitlement.user, owner['key'], email_opt_in)

        if support_details:
            for support_detail in support_details:
                support_detail['entitlement'] = entitlement
                support_detail['support_user'] = request.user
                CourseEntitlementSupportDetail.objects.create(**support_detail)
        else:
            user = entitlement.user
            upgradeable_enrollments = self.get_upgradeable_enrollments_for_entitlement(entitlement)

            # if there is only one upgradeable enrollment, update the mode to the paid entitlement.mode
            # if there is any ambiguity about which enrollment to upgrade
            # (i.e. multiple upgradeable enrollments or no available upgradeable enrollment), don't alter
            # the enrollment
            if len(upgradeable_enrollments) == 1:
                enrollment = upgradeable_enrollments[0]
                log.info(
                    'Upgrading enrollment [%s] from %s to %s while adding entitlement for user [%s] for course [%s]',
                    enrollment,
                    enrollment.mode,
                    serializer.data.get('mode'),
                    user.username,
                    serializer.data.get('course_uuid')
                )
                enrollment.update_enrollment(mode=entitlement.mode)
                entitlement.set_enrollment(enrollment)
            else:
                log.info(
                    'No enrollment upgraded while adding entitlement for user [%s] for course [%s] ',
                    user.username,
                    serializer.data.get('course_uuid')
                )

        headers = self.get_success_headers(serializer.data)
        # Note, the entitlement is re-serialized before getting added to the Response,
        # so that the 'modified' date reflects changes that occur when upgrading enrollment.
        return Response(
            CourseEntitlementSerializer(entitlement).data,
            status=status.HTTP_201_CREATED, headers=headers
        )

    def retrieve(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        """
        Override the retrieve method to expire a record that is past the
        policy and is requested via the API before returning that record.
        """
        entitlement = self.get_object()
        entitlement.update_expired_at()
        serializer = self.get_serializer(entitlement)
        return Response(serializer.data)

    def list(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        """
        Override the list method to expire records that are past the
        policy and requested via the API before returning those records.
        """
        queryset = self.filter_queryset(self.get_queryset())
        user = self.request.user
        if not user.is_staff:
            with transaction.atomic():
                for entitlement in queryset:
                    entitlement.update_expired_at()

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def perform_destroy(self, instance):
        """
        This method is an override and is called by the destroy method, which is called when a DELETE operation occurs

        This method will revoke the User's entitlement and unenroll the user if they are enrolled
        in a Course Run

        It is assumed the user has already been refunded.
        """
        log.info(
            'Entitlement Revoke requested for Course Entitlement[%s]',
            instance.uuid
        )
        # This is not called with is_refund=True here because it is assumed the user has already been refunded.
        _process_revoke_and_unenroll_entitlement(instance)

    def partial_update(self, request, *args, **kwargs):
        entitlement_uuid = kwargs.get('uuid', None)

        try:
            entitlement = CourseEntitlement.objects.get(uuid=entitlement_uuid)
        except CourseEntitlement.DoesNotExist:
            return HttpResponseBadRequest(
                'Could not find entitlement {entitlement_uuid} to update'.format(  # noqa: UP032
                    entitlement_uuid=entitlement_uuid
                )
            )
        support_details = request.data.pop('support_details', [])

        # If a patch request does not explicitly update an entitlement's refundability status, we want to ensure that
        # changes made to other attributes of the entitlement do not implicitly change its ability to be refunded.
        if request.data.get('refund_locked') is None:
            request.data['refund_locked'] = not entitlement.is_entitlement_refundable()

        for support_detail in support_details:
            support_detail['entitlement'] = entitlement
            support_detail['support_user'] = request.user
            unenrolled_run_id = support_detail.get('unenrolled_run', None)
            if unenrolled_run_id:
                try:
                    unenrolled_run_course_key = CourseKey.from_string(unenrolled_run_id)
                    _unenroll_entitlement(entitlement, unenrolled_run_course_key)
                    support_detail['unenrolled_run'] = CourseOverview.objects.get(id=unenrolled_run_course_key)
                except (InvalidKeyError, CourseOverview.DoesNotExist) as error:
                    return HttpResponseBadRequest(
                        'Error raised while trying to unenroll user {user} from course run {course_id}: {error}'  # noqa: UP032  # pylint: disable=line-too-long
                        .format(user=entitlement.user.username, course_id=unenrolled_run_id, error=error)
                    )
            CourseEntitlementSupportDetail.objects.create(**support_detail)

        return super().partial_update(request, *args, **kwargs)  # pylint: disable=no-member, super-with-arguments


class EntitlementEnrollmentViewSet(viewsets.GenericViewSet):
    """
    Endpoint in the Entitlement API to handle the Enrollment of a User's Entitlement.
    This API will handle
        - Enroll
        - Unenroll
        - Switch Enrollment
    """
    # TODO: ARCH-91
    # This view is excluded from Swagger doc generation because it
    # does not specify a serializer class.
    exclude_from_schema = True
    permission_classes = (permissions.IsAuthenticated,)
    queryset = CourseEntitlement.objects.all()

    def _verify_course_run_for_entitlement(self, entitlement, course_run_id):
        """
        Verifies that a Course run is a child of the Course assigned to the entitlement.
        """
        course_runs = get_course_runs_for_course(entitlement.course_uuid)
        for run in course_runs:
            if course_run_id == run.get('key', ''):
                return True
        return False

    @transaction.atomic
    def _enroll_entitlement(self, entitlement, course_run_key, user):
        """
        Internal method to handle the details of enrolling a User in a Course Run.

        Returns a response object is there is an error or exception, None otherwise
        """
        try:
            unexpired_paid_modes = [mode.slug for mode in CourseMode.paid_modes_for_course(course_run_key)]
            can_upgrade = unexpired_paid_modes and entitlement.mode in unexpired_paid_modes
            enrollment = CourseEnrollment.enroll(
                user=user,
                course_key=course_run_key,
                mode=entitlement.mode,
                check_access=True,
                can_upgrade=can_upgrade
            )
        except AlreadyEnrolledError:
            enrollment = CourseEnrollment.get_enrollment(user, course_run_key)
            if enrollment.mode == entitlement.mode:
                entitlement.set_enrollment(enrollment)
            elif enrollment.mode not in unexpired_paid_modes:
                enrollment.update_enrollment(mode=entitlement.mode)
                entitlement.set_enrollment(enrollment)
            # Else the User is already enrolled in another paid Mode and we should
            # not do anything else related to Entitlements.
        except CourseEnrollmentException:
            message = (  # noqa: UP032
                'Course Entitlement Enroll for {username} failed for course: {course_id}, '
                'mode: {mode}, and entitlement: {entitlement}'
            ).format(
                username=user.username,
                course_id=course_run_key,
                mode=entitlement.mode,
                entitlement=entitlement.uuid
            )
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={'message': message}
            )

        entitlement.set_enrollment(enrollment)
        return None

    def create(self, request, uuid):
        """
        On POST this method will be called and will handle enrolling a user in the
        provided course_run_id from the data. This is called on a specific entitlement
        UUID so the course_run_id has to correspond to the Course that is assigned to
        the Entitlement.

        When this API is called for a user who is already enrolled in a run that User
        will be unenrolled from their current run and enrolled in the new run if it is
        available.
        """
        course_run_id = request.data.get('course_run_id', None)

        if not course_run_id:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data='The Course Run ID was not provided.'
            )

        # Verify that the user has an Entitlement for the provided Entitlement UUID.
        try:
            entitlement = CourseEntitlement.objects.get(uuid=uuid, user=request.user, expired_at=None)
        except CourseEntitlement.DoesNotExist:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data='The Entitlement for this UUID does not exist or is Expired.'
            )

        # Verify the course run ID is of the same Course as the Course entitlement.
        course_run_valid = self._verify_course_run_for_entitlement(entitlement, course_run_id)
        if not course_run_valid:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={
                    'message': 'The Course Run ID is not a match for this Course Entitlement.'
                }
            )

        try:
            course_run_key = CourseKey.from_string(course_run_id)
        except InvalidKeyError:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={
                    'message': f'Invalid {course_run_id}'
                }
            )

        # Verify that the run is fullfillable
        if not is_course_run_entitlement_fulfillable(course_run_key, entitlement):
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={
                    'message': 'The User is unable to enroll in Course Run {course_id}, it is not available.'.format(  # noqa: UP032  # pylint: disable=line-too-long
                        course_id=course_run_id
                    )
                }
            )

        # Determine if this is a Switch session or a simple enroll and handle both.
        if entitlement.enrollment_course_run is None:
            response = self._enroll_entitlement(
                entitlement=entitlement,
                course_run_key=course_run_key,
                user=request.user
            )
            if response:
                return response
        elif entitlement.enrollment_course_run.course_id != course_run_id:
            _unenroll_entitlement(
                course_entitlement=entitlement,
                course_run_key=entitlement.enrollment_course_run.course_id
            )
            response = self._enroll_entitlement(
                entitlement=entitlement,
                course_run_key=course_run_key,
                user=request.user
            )
            if response:
                return response

        return Response(
            status=status.HTTP_201_CREATED,
            data={
                'course_run_id': course_run_id,
            }
        )

    def destroy(self, request, uuid):
        """
        On DELETE call to this API we will unenroll the course enrollment for the provided uuid

        If is_refund parameter is provided then unenroll the user, set Entitlement expiration, and issue
        a refund
        """
        is_refund = request.query_params.get('is_refund', 'false') == 'true'

        # Retrieve the entitlement for the UUID belongs to the current user.
        try:
            entitlement = CourseEntitlement.objects.get(uuid=uuid, user=request.user, expired_at=None)
        except CourseEntitlement.DoesNotExist:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data='The Entitlement for this UUID does not exist or is Expired.'
            )

        if is_refund and entitlement.is_entitlement_refundable():
            # Revoke the Course Entitlement and issue Refund
            log.info(
                'Entitlement Refund requested for Course Entitlement[%s]',
                entitlement.uuid
            )

            try:
                _process_revoke_and_unenroll_entitlement(course_entitlement=entitlement, is_refund=True)
            except IntegrityError:
                # This state is reached when there was a failure in revoke and refund process resulting
                # in a reversion of DB changes
                return Response(
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    data={
                        'message': 'Entitlement revoke and refund failed due to refund internal process failure'
                    })

        elif not is_refund:
            if entitlement.enrollment_course_run is not None:
                _unenroll_entitlement(
                    course_entitlement=entitlement,
                    course_run_key=entitlement.enrollment_course_run.course_id
                )
        else:
            log.info(
                'Entitlement Refund failed for Course Entitlement [%s]. Entitlement is not refundable',
                entitlement.uuid
            )
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={
                    'message': 'Entitlement refund failed, Entitlement is not refundable'
                })

        return Response(status=status.HTTP_204_NO_CONTENT)
