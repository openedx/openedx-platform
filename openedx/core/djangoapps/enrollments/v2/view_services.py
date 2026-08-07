"""
Shared service layer for enrollment v2 HTTP operations.

ADR 0031 (Merge Similar Endpoints) — consolidates the business logic
behind the three v2 viewset actions that previously had partially duplicated
implementations in v1's ``EnrollmentListView`` / ``UnenrollmentView`` /
``EnrollmentAllowedView``.

Authorization model
-------------------
Each operation is enforced in two layers:

1. The viewset declares a coarse permission class (``IsAuthenticated``,
   ``IsAdminUser``, ``CanRetireUser``, ``ApiKeyHeaderPermissionIsAuthenticated``)
   on the action.
2. The service method enforces the per-operation rules — e.g. only API-key
   callers or global staff may deactivate enrollments, downgrade modes, or
   force-enroll a user.

ADR 0029 — service methods raise DRF exceptions (``NotFound``,
``ValidationError``, ``PermissionDenied``, ``Conflict``) instead of returning
``Response`` objects with non-2xx status. The exceptions flow through the
viewset's :class:`StandardizedErrorMixin` to produce the standardized
envelope.
"""

import logging

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from rest_framework.exceptions import (
    APIException,
    NotFound,
    PermissionDenied,
    ValidationError,
)

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.auth import user_has_role
from common.djangoapps.student.models import CourseEnrollment, CourseEnrollmentAllowed, EnrollmentNotAllowed
from common.djangoapps.student.roles import CourseStaffRole, GlobalStaff
from openedx.core.djangoapps.course_groups.cohorts import CourseUserGroup, add_user_to_cohort, get_cohort_by_name
from openedx.core.djangoapps.embargo import api as embargo_api
from openedx.core.djangoapps.enrollments import api
from openedx.core.djangoapps.enrollments.errors import (
    CourseEnrollmentError,
    CourseEnrollmentExistsError,
    CourseModeNotFoundError,
    InvalidEnrollmentAttribute,
)
from openedx.core.djangoapps.user_api.models import UserRetirementStatus
from openedx.core.djangoapps.user_api.preferences.api import update_email_opt_in
from openedx.core.lib.api.exceptions import Conflict
from openedx.core.lib.exceptions import CourseNotFoundError
from openedx.core.lib.log_utils import audit_log
from openedx.features.enterprise_support.api import (
    ConsentApiServiceClient,
    EnterpriseApiException,
    EnterpriseApiServiceClient,
    enterprise_enabled,
)

log = logging.getLogger(__name__)

User = get_user_model()

REQUIRED_ATTRIBUTES = {
    "credit": ["credit:provider_id"],
}


class EnrollmentOperationsService:
    """
    Operation handlers for the v2 EnrollmentViewSet.

    All methods raise DRF exceptions on error paths so the viewset's
    :class:`StandardizedErrorMixin` can produce the ADR 0029 envelope.
    """

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------
    def list_enrollments_for_user(self, request_user, target_username, has_api_key):
        """
        Return enrollments visible to ``request_user`` for ``target_username``.

        - Self / global staff / api-key requests → full list.
        - Otherwise filtered to courses ``request_user`` staffs.
        """
        enrollments = CourseEnrollment.objects.filter(
            user__username=target_username
        ).select_related("user", "course")
        if (
            target_username == request_user.username
            or GlobalStaff().has_user(request_user)
            or has_api_key
        ):
            return list(enrollments)
        return [
            enrollment for enrollment in enrollments
            if user_has_role(request_user, CourseStaffRole(enrollment.course_id))
        ]

    # ------------------------------------------------------------------
    # Create / update
    # ------------------------------------------------------------------
    def create_or_update_enrollment(self, request, has_api_key, course_id):
        """
        Handle the POST /enrollment/ create-or-update flow.

        ``course_id`` is a parsed :class:`CourseKey`. The viewset is
        responsible for the up-front ``InvalidKeyError → ValidationError``
        translation before calling this method.

        Returns the enrollment dict on success. Raises DRF exceptions on
        any error path.
        """
        # pylint: disable=too-many-statements,too-many-branches
        username = request.data.get("user")
        mode = request.data.get("mode")
        is_active = None
        user = None
        cohort_name = None

        # Per-operation authz layer 1: only admin/api-key callers may enroll
        # other users. Non-staff callers can only enroll themselves.
        if (
            username
            and username != request.user.username
            and not has_api_key
            and not GlobalStaff().has_user(request.user)
        ):
            raise NotFound()

        if not username:
            email = request.data.get("email")
            if email:
                if not has_api_key and not GlobalStaff().has_user(request.user):
                    raise NotFound()
                try:
                    username = User.objects.get(email=email).username
                except ObjectDoesNotExist as exc:
                    raise NotFound(
                        f"The user with the email address {email} does not exist."
                    ) from exc
            else:
                username = request.user.username

        # Per-operation authz layer 2: non-default modes require api-key or
        # global-staff privileges.
        if (
            mode not in (CourseMode.AUDIT, CourseMode.HONOR, None)
            and not has_api_key
            and not GlobalStaff().has_user(request.user)
        ):
            raise PermissionDenied(
                f"User does not have permission to create enrollment with mode [{mode}]."
            )

        try:
            user = User.objects.get(username=username)
        except ObjectDoesNotExist as exc:
            raise NotFound(f"The user {username} does not exist.") from exc

        embargo_response = embargo_api.get_embargo_response(request, course_id, user)
        if embargo_response:
            # Embargo returns a fully-formed Response; surface its body as a
            # PermissionDenied so the standardized envelope wraps it.
            raise PermissionDenied(detail=getattr(embargo_response, "data", "Embargoed."))

        try:
            is_active = request.data.get("is_active")
            if is_active is not None and not isinstance(is_active, bool):
                raise ValidationError(f"'{is_active}' is an invalid enrollment activation status.")

            explicit_linked_enterprise = request.data.get("linked_enterprise_customer")
            if explicit_linked_enterprise and has_api_key and enterprise_enabled():
                enterprise_api_client = EnterpriseApiServiceClient()
                consent_client = ConsentApiServiceClient()
                try:
                    enterprise_api_client.post_enterprise_course_enrollment(username, str(course_id))
                except EnterpriseApiException as error:
                    log.exception(
                        "An unexpected error occurred while creating the new EnterpriseCourseEnrollment "
                        "for user [%s] in course run [%s]", username, course_id,
                    )
                    raise CourseEnrollmentError(str(error)) from error
                consent_client.provide_consent(
                    username=username,
                    course_id=str(course_id),
                    enterprise_customer_uuid=explicit_linked_enterprise,
                )

            enrollment_attributes = request.data.get("enrollment_attributes")
            force_enrollment = request.data.get("force_enrollment")
            if force_enrollment is not None and not isinstance(force_enrollment, bool):
                raise ValidationError(f"'{force_enrollment}' is an invalid force enrollment status.")
            force_enrollment = force_enrollment and GlobalStaff().has_user(request.user)

            enrollment = api.get_enrollment(username, str(course_id))
            mode_changed = enrollment and mode is not None and enrollment["mode"] != mode
            active_changed = enrollment and is_active is not None and enrollment["is_active"] != is_active
            missing_attrs = []
            if enrollment_attributes:
                actual_attrs = ["{namespace}:{name}".format(**attr) for attr in enrollment_attributes]
                missing_attrs = set(REQUIRED_ATTRIBUTES.get(mode, [])) - set(actual_attrs)

            if (GlobalStaff().has_user(request.user) or has_api_key) and (mode_changed or active_changed):
                if mode_changed and active_changed and not is_active:
                    msg = (
                        f"Enrollment mode mismatch: active mode={enrollment['mode']}, "
                        f"requested mode={mode}. Won't deactivate."
                    )
                    log.warning(msg)
                    raise ValidationError(msg)

                if missing_attrs:
                    msg = (
                        f"Missing enrollment attributes: requested mode={mode} "
                        f"required attributes={REQUIRED_ATTRIBUTES.get(mode)}"
                    )
                    log.warning(msg)
                    raise ValidationError(msg)

                response_data = api.update_enrollment(
                    username,
                    str(course_id),
                    mode=mode,
                    is_active=is_active,
                    enrollment_attributes=enrollment_attributes,
                    include_expired=has_api_key,
                )
            else:
                response_data = api.add_enrollment(
                    username,
                    str(course_id),
                    mode=mode,
                    is_active=is_active,
                    enrollment_attributes=enrollment_attributes,
                    enterprise_uuid=request.data.get("enterprise_uuid"),
                    force_enrollment=force_enrollment,
                    include_expired=force_enrollment,
                )

            cohort_name = request.data.get("cohort")
            if cohort_name is not None:
                cohort = get_cohort_by_name(course_id, cohort_name)
                try:
                    add_user_to_cohort(cohort, user)
                except ValueError:
                    log.exception("Cohort re-addition")

            email_opt_in = request.data.get("email_opt_in", None)
            if email_opt_in is not None:
                update_email_opt_in(request.user, course_id.org, email_opt_in)

            log.info("The user [%s] has already been enrolled in course run [%s].", username, course_id)
            return response_data

        except InvalidEnrollmentAttribute as error:
            raise ValidationError(str(error)) from error
        except EnrollmentNotAllowed as error:
            raise PermissionDenied(str(error)) from error
        except CourseModeNotFoundError as error:
            raise ValidationError(
                f"The [{mode}] course mode is expired or otherwise unavailable for course run [{course_id}]."
            ) from error
        except CourseNotFoundError as error:
            raise ValidationError(f"No course '{course_id}' found for enrollment") from error
        except CourseEnrollmentExistsError as error:
            log.warning("An enrollment already exists for user [%s] in course run [%s].", username, course_id)
            # Caller-visible signal that the enrollment already exists. Use 200 + existing enrollment body
            # (matches v1 semantics) — surface as a successful return, not an exception.
            return error.enrollment
        except CourseEnrollmentError as error:
            log.exception(
                "An error occurred while creating the new course enrollment for user [%s] in course run [%s]",
                username, course_id,
            )
            raise ValidationError(
                f"An error occurred while creating the new course enrollment "
                f"for user '{username}' in course '{course_id}'"
            ) from error
        except CourseUserGroup.DoesNotExist as error:
            log.exception("Missing cohort [%s] in course run [%s]", cohort_name, course_id)
            raise ValidationError(
                f"An error occured while adding to cohort [{cohort_name}]"
            ) from error
        finally:
            # Audit-log every API-key-driven enrollment change.
            if has_api_key and user is not None:
                try:
                    current = CourseEnrollment.objects.get(user__username=username, course_id=course_id)
                    actual_mode = current.mode
                    actual_activation = current.is_active
                except CourseEnrollment.DoesNotExist:
                    actual_mode = None
                    actual_activation = None
                audit_log(
                    "enrollment_change_requested",
                    course_id=str(course_id),
                    requested_mode=mode,
                    actual_mode=actual_mode,
                    requested_activation=is_active,
                    actual_activation=actual_activation,
                    user_id=user.id,
                )

    # ------------------------------------------------------------------
    # Unenroll (retirement pipeline)
    # ------------------------------------------------------------------
    def unenroll_user_for_retirement(self, username):
        """
        Handle the retirement-pipeline /enrollment/unenroll/ flow.

        Returns:
            - ``None`` if the user has no active enrollments (caller should
              return 204 No Content).
            - A dict (the unenroll-result payload) on success (caller returns 200).

        Raises:
            ValidationError: if ``username`` is missing.
            NotFound: if no retirement-status row exists for the user.
            APIException: on any other unexpected error (mapped to 500).
        """
        if not username:
            raise ValidationError("Username not specified.")
        try:
            UserRetirementStatus.get_retirement_for_retirement_action(username)
        except UserRetirementStatus.DoesNotExist as exc:
            raise NotFound("No retirement request status for username.") from exc
        try:
            if not CourseEnrollment.objects.filter(user__username=username, is_active=True).exists():
                return None
            return api.unenroll_user_from_all_courses(username)
        except Exception as exc:  # pylint: disable=broad-except
            log.exception("Unexpected error during unenrollment for user %s", username)
            raise APIException("An unexpected error occurred during unenrollment.") from exc

    # ------------------------------------------------------------------
    # Allowed enrollments
    # ------------------------------------------------------------------
    def list_allowed_for_email(self, email):
        """Return the ``CourseEnrollmentAllowed`` queryset for ``email``."""
        return CourseEnrollmentAllowed.objects.filter(email=email)

    def create_allowed_enrollment(self, serializer):
        """
        Persist the allowed-enrollment described by ``serializer``.

        Raises:
            Conflict: if a row already exists for the (email, course_id) pair.
        """
        from django.db import IntegrityError  # local import — avoid heavy startup cost
        try:
            return serializer.save()
        except IntegrityError as exc:
            email = serializer.validated_data.get("email")
            course_id = serializer.validated_data.get("course_id")
            raise Conflict(
                f"An enrollment allowed with email {email} and course {course_id} already exists."
            ) from exc

    def delete_allowed_enrollment(self, email, course_id):
        """
        Delete the allowed-enrollment row identified by (email, course_id).

        Raises:
            NotFound: if no such row exists.
        """
        try:
            CourseEnrollmentAllowed.objects.get(email=email, course_id=course_id).delete()
        except ObjectDoesNotExist as exc:
            raise NotFound(
                f"An enrollment allowed with email {email} and course {course_id} doesn't exists."
            ) from exc
