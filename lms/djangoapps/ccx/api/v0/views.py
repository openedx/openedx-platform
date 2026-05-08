""" API v0 views. """


import datetime
import json
import logging

import pytz
from ccx_keys.locator import CCXLocator
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.db import transaction
from django.http import Http404
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import CourseCcxCoachRole
from lms.djangoapps.ccx.models import CcxFieldOverride, CustomCourseForEdX
from lms.djangoapps.ccx.overrides import override_field_for_ccx
from lms.djangoapps.ccx.utils import add_master_course_staff_to_ccx, assign_staff_role_to_ccx, is_email
from lms.djangoapps.courseware.courses import get_course_chapter_ids
from lms.djangoapps.instructor.enrollment import enroll_email, get_email_params
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.lib.api import authentication, permissions
from openedx.core.lib.courses import get_course_by_id
from xmodule.modulestore.django import SignalHandler  # pylint: disable=wrong-import-order

from .paginators import CCXAPIPagination
from .serializers import CCXCourseSerializer

log = logging.getLogger(__name__)
TODAY = datetime.datetime.today  # for patching in tests


def get_valid_course(course_id, is_ccx=False, advanced_course_check=False):
    """
    Helper function used to validate and get a course from a course_id string.
    It works with both master and ccx course id.

    Args:
        course_id (str): A string representation of a Master or CCX Course ID.
        is_ccx (bool): Flag to perform the right validation
        advanced_course_check (bool): Flag to perform extra validations for the master course

    Returns:
        tuple: a tuple of course_object, course_key, error_code, http_status_code
    """
    if course_id is None:
        # the ccx detail view cannot call this function with a "None" value
        # so the following `error_code` should be never used, but putting it
        # to avoid a `NameError` exception in case this function will be used
        # elsewhere in the future
        error_code = 'course_id_not_provided'
        if not is_ccx:
            log.info('Master course ID not provided')
            error_code = 'master_course_id_not_provided'

        return None, None, error_code, status.HTTP_400_BAD_REQUEST

    try:
        course_key = CourseKey.from_string(course_id)
    except InvalidKeyError:
        log.info('Course ID string "%s" is not valid', course_id)
        return None, None, 'course_id_not_valid', status.HTTP_400_BAD_REQUEST

    if not is_ccx:
        try:
            course_object = get_course_by_id(course_key)
        except Http404:
            log.info('Master Course with ID "%s" not found', course_id)
            return None, None, 'course_id_does_not_exist', status.HTTP_404_NOT_FOUND
        if advanced_course_check:
            if course_object.id.deprecated:
                return None, None, 'deprecated_master_course_id', status.HTTP_400_BAD_REQUEST
            if not course_object.enable_ccx:
                return None, None, 'ccx_not_enabled_for_master_course', status.HTTP_403_FORBIDDEN
        return course_object, course_key, None, None
    else:
        try:
            ccx_id = course_key.ccx
        except AttributeError:
            log.info('Course ID string "%s" is not a valid CCX ID', course_id)
            return None, None, 'course_id_not_valid_ccx_id', status.HTTP_400_BAD_REQUEST
        # get the master_course key
        master_course_key = course_key.to_course_locator()
        try:
            ccx_course = CustomCourseForEdX.objects.get(id=ccx_id, course_id=master_course_key)
            return ccx_course, course_key, None, None
        except CustomCourseForEdX.DoesNotExist:
            log.info('CCX Course with ID "%s" not found', course_id)
            return None, None, 'ccx_course_id_does_not_exist', status.HTTP_404_NOT_FOUND


def get_valid_input(request_data, ignore_missing=False):
    """
    Helper function to validate the data sent as input and to
    build field based errors.

    Args:
        request_data (OrderedDict): the request data object
        ignore_missing (bool): whether or not to ignore fields
            missing from the input data

    Returns:
        tuple: a tuple of two dictionaries for valid input and field errors
    """
    valid_input = {}
    field_errors = {}
    mandatory_fields = ('coach_email', 'display_name', 'max_students_allowed',)

    # checking first if all the fields are present and they are not null
    if not ignore_missing:
        for field in mandatory_fields:
            if field not in request_data:
                field_errors[field] = {'error_code': f'missing_field_{field}'}
        if field_errors:
            return valid_input, field_errors

    # at this point I can assume that if the fields are present,
    # they must be validated, otherwise they can be skipped
    coach_email = request_data.get('coach_email')
    if coach_email is not None:
        if is_email(coach_email):
            valid_input['coach_email'] = coach_email
        else:
            field_errors['coach_email'] = {'error_code': 'invalid_coach_email'}
    elif 'coach_email' in request_data:
        field_errors['coach_email'] = {'error_code': 'null_field_coach_email'}

    display_name = request_data.get('display_name')
    if display_name is not None:
        if not display_name:
            field_errors['display_name'] = {'error_code': 'invalid_display_name'}
        else:
            valid_input['display_name'] = display_name
    elif 'display_name' in request_data:
        field_errors['display_name'] = {'error_code': 'null_field_display_name'}

    max_students_allowed = request_data.get('max_students_allowed')
    if max_students_allowed is not None:
        try:
            max_students_allowed = int(max_students_allowed)
            valid_input['max_students_allowed'] = max_students_allowed
        except (TypeError, ValueError):
            field_errors['max_students_allowed'] = {'error_code': 'invalid_max_students_allowed'}
    elif 'max_students_allowed' in request_data:
        field_errors['max_students_allowed'] = {'error_code': 'null_field_max_students_allowed'}

    course_modules = request_data.get('course_modules')
    if course_modules is not None:
        if isinstance(course_modules, list):
            # de-duplicate list of modules
            course_modules = list(set(course_modules))
            for course_module_id in course_modules:
                try:
                    UsageKey.from_string(course_module_id)
                except InvalidKeyError:
                    field_errors['course_modules'] = {'error_code': 'invalid_course_module_keys'}
                    break
            else:
                valid_input['course_modules'] = course_modules
        else:
            field_errors['course_modules'] = {'error_code': 'invalid_course_module_list'}
    elif 'course_modules' in request_data:
        # case if the user actually passed null as input
        valid_input['course_modules'] = None

    return valid_input, field_errors


def valid_course_modules(course_module_list, master_course_key):
    """
    Function to validate that each element in the course_module_list belongs
    to the master course structure.
    Args:
        course_module_list (list): A list of strings representing Block Usage Keys
        master_course_key (CourseKey): An object representing the master course key id

    Returns:
        bool: whether or not all the course module strings belong to the master course
    """
    course_chapters = get_course_chapter_ids(master_course_key)
    return set(course_module_list).intersection(set(course_chapters)) == set(course_module_list)


def make_user_coach(user, master_course_key):
    """
    Makes an user coach on the master course.
    This function is needed because an user cannot become a coach of the CCX if s/he is not
    coach on the master course.

    Args:
        user (User): User object
        master_course_key (CourseKey): Key locator object for the course
    """
    coach_role_on_master_course = CourseCcxCoachRole(master_course_key)
    coach_role_on_master_course.add_users(user)


class CCXListView(GenericAPIView):
    """
        **Use Case**

            * Get the list of CCX courses for a given master course.

            * Creates a new CCX course for a given master course.

        **Example Request**

            GET /api/ccx/v0/ccx/?master_course_id={master_course_id}

            POST /api/ccx/v0/ccx {

                "master_course_id": "course-v1:Organization+EX101+RUN-FALL2099",
                "display_name": "CCX example title",
                "coach_email": "john@example.com",
                "max_students_allowed": 123,
                "course_modules" : [
                    "block-v1:Organization+EX101+RUN-FALL2099+type@chapter+block@week1",
                    "block-v1:Organization+EX101+RUN-FALL2099+type@chapter+block@week4",
                    "block-v1:Organization+EX101+RUN-FALL2099+type@chapter+block@week5"
                ]

            }

        **GET Parameters**

            A GET request can include the following parameters.

            * master_course_id: A string representation of a Master Course ID. Note that this must be properly
              encoded by the client.

            * page: Optional. An integer representing the pagination instance number.

            * order_by: Optional. A string representing the field by which sort the results.

            * sort_order: Optional. A string (either "asc" or "desc") indicating the desired order.

        **POST Parameters**

            A POST request can include the following parameters.

            * master_course_id: A string representation of a Master Course ID.

            * display_name: A string representing the CCX Course title.

            * coach_email: A string representing the CCX owner email.

            * max_students_allowed: An integer representing he maximum number of students that
              can be enrolled in the CCX Course.

            * course_modules: Optional. A list of course modules id keys.

        **GET Response Values**

            If the request for information about the course is successful, an HTTP 200 "OK" response
            is returned with a collection of CCX courses for the specified master course.

            The HTTP 200 response has the following values.

            * results: a collection of CCX courses. Each CCX course contains the following values:

                * ccx_course_id: A string representation of a CCX Course ID.

                * display_name: A string representing the CCX Course title.

                * coach_email: A string representing the CCX owner email.

                * start: A string representing the start date for the CCX Course.

                * due: A string representing the due date for the CCX Course.

                * max_students_allowed: An integer representing he maximum number of students that
                  can be enrolled in the CCX Course.

                * course_modules: A list of course modules id keys.

            * count: An integer representing the total number of records that matched the request parameters.

            * next: A string representing the URL where to retrieve the next page of results. This can be `null`
              in case the response contains the complete list of results.

            * previous: A string representing the URL where to retrieve the previous page of results. This can be
              `null` in case the response contains the first page of results.

        **Example GET Response**

            {
                "count": 99,
                "next": "https://openedx-ccx-api-instance.org/api/ccx/v0/ccx/?course_id=<course_id>&page=2",
                "previous": null,
                "results": {
                    {
                        "ccx_course_id": "ccx-v1:Organization+EX101+RUN-FALL2099+ccx@1",
                        "display_name": "CCX example title",
                        "coach_email": "john@example.com",
                        "start": "2019-01-01",
                        "due": "2019-06-01",
                        "max_students_allowed": 123,
                        "course_modules" : [
                            "block-v1:Organization+EX101+RUN-FALL2099+type@chapter+block@week1",
                            "block-v1:Organization+EX101+RUN-FALL2099+type@chapter+block@week4",
                            "block-v1:Organization+EX101+RUN-FALL2099+type@chapter+block@week5"
                        ]
                    },
                    { ... }
                }
            }

        **POST Response Values**

            If the request for the creation of a CCX Course is successful, an HTTP 201 "Created" response
            is returned with the newly created CCX details.

            The HTTP 201 response has the following values.

            * ccx_course_id: A string representation of a CCX Course ID.

            * display_name: A string representing the CCX Course title.

            * coach_email: A string representing the CCX owner email.

            * start: A string representing the start date for the CCX Course.

            * due: A string representing the due date for the CCX Course.

            * max_students_allowed: An integer representing he maximum number of students that
              can be enrolled in the CCX Course.

            * course_modules: A list of course modules id keys.

        **Example POST Response**

            {
                "ccx_course_id": "ccx-v1:Organization+EX101+RUN-FALL2099+ccx@1",
                "display_name": "CCX example title",
                "coach_email": "john@example.com",
                "start": "2019-01-01",
                "due": "2019-06-01",
                "max_students_allowed": 123,
                "course_modules" : [
                    "block-v1:Organization+EX101+RUN-FALL2099+type@chapter+block@week1",
                    "block-v1:Organization+EX101+RUN-FALL2099+type@chapter+block@week4",
                    "block-v1:Organization+EX101+RUN-FALL2099+type@chapter+block@week5"
                ]
    }
    """
    authentication_classes = (
        JwtAuthentication,
        authentication.BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated, permissions.IsMasterCourseStaffInstructor)
    serializer_class = CCXCourseSerializer
    pagination_class = CCXAPIPagination

    def get(self, request):
        """
        Gets a list of CCX Courses for a given Master Course.

        Additional parameters are allowed for pagination purposes.

        Args:
            request (Request): Django request object.

        Return:
            A JSON serialized representation of a list of CCX courses.
        """
        master_course_id = request.GET.get('master_course_id')
        master_course_object, master_course_key, error_code, http_status = get_valid_course(master_course_id)
        if master_course_object is None:
            return Response(
                status=http_status,
                data={
                    'error_code': error_code
                }
            )

        queryset = CustomCourseForEdX.objects.filter(course_id=master_course_key)
        order_by_input = request.query_params.get('order_by')
        sort_order_input = request.query_params.get('sort_order')
        if order_by_input in ('id', 'display_name'):
            sort_direction = ''
            if sort_order_input == 'desc':
                sort_direction = '-'
            queryset = queryset.order_by(f'{sort_direction}{order_by_input}')
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page, many=True)
        response = self.get_paginated_response(serializer.data)
        return response

    def post(self, request):
        """
        Creates a new CCX course for a given Master Course.

        Args:
            request (Request): Django request object.

        Return:
            A JSON serialized representation a newly created CCX course.
        """
        master_course_id = request.data.get('master_course_id')
        master_course_object, master_course_key, error_code, http_status = get_valid_course(
            master_course_id,
            advanced_course_check=True
        )
        if master_course_object is None:
            return Response(
                status=http_status,
                data={
                    'error_code': error_code
                }
            )

        # validating the rest of the input
        valid_input, field_errors = get_valid_input(request.data)
        if field_errors:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={
                    'field_errors': field_errors
                }
            )

        try:
            # Retired users should effectively appear to not exist when
            # attempts are made to modify them, so a direct User model email
            # lookup is sufficient here.  This corner case relies on the fact
            # that we scramble emails immediately during user lock-out.  Of
            # course, the normal cases are that the email just never existed,
            # or it is currently associated with an active account.
            coach = User.objects.get(email=valid_input['coach_email'])
        except User.DoesNotExist:
            return Response(
                status=status.HTTP_404_NOT_FOUND,
                data={
                    'error_code': 'coach_user_does_not_exist'
                }
            )

        if valid_input.get('course_modules'):
            if not valid_course_modules(valid_input['course_modules'], master_course_key):
                return Response(
                    status=status.HTTP_400_BAD_REQUEST,
                    data={
                        'error_code': 'course_module_list_not_belonging_to_master_course'
                    }
                )
        # prepare the course_modules to be stored in a json stringified field
        course_modules_json = json.dumps(valid_input.get('course_modules'))

        with transaction.atomic():
            ccx_course_object = CustomCourseForEdX(
                course_id=master_course_object.id,
                coach=coach,
                display_name=valid_input['display_name'],
                structure_json=course_modules_json
            )
            ccx_course_object.save()

            # Make sure start/due are overridden for entire course
            start = TODAY().replace(tzinfo=pytz.UTC)
            override_field_for_ccx(ccx_course_object, master_course_object, 'start', start)
            override_field_for_ccx(ccx_course_object, master_course_object, 'due', None)

            # Enforce a static limit for the maximum amount of students that can be enrolled
            override_field_for_ccx(
                ccx_course_object,
                master_course_object,
                'max_student_enrollments_allowed',
                valid_input['max_students_allowed']
            )

            # Hide anything that can show up in the schedule
            hidden = 'visible_to_staff_only'
            for chapter in master_course_object.get_children():
                override_field_for_ccx(ccx_course_object, chapter, hidden, True)
                for sequential in chapter.get_children():
                    override_field_for_ccx(ccx_course_object, sequential, hidden, True)
                    for vertical in sequential.get_children():
                        override_field_for_ccx(ccx_course_object, vertical, hidden, True)

            # make the coach user a coach on the master course
            make_user_coach(coach, master_course_key)

            # pull the ccx course key
            ccx_course_key = CCXLocator.from_course_locator(
                master_course_object.id,
                str(ccx_course_object.id)
            )
            # enroll the coach in the newly created ccx
            email_params = get_email_params(
                master_course_object,
                auto_enroll=True,
                course_key=ccx_course_key,
                display_name=ccx_course_object.display_name
            )
            enroll_email(
                course_id=ccx_course_key,
                student_email=coach.email,
                auto_enroll=True,
                message_students=True,
                message_params=email_params,
            )
            # assign staff role for the coach to the newly created ccx
            assign_staff_role_to_ccx(ccx_course_key, coach, master_course_object.id)
            # assign staff role for all the staff and instructor of the master course to the newly created ccx
            add_master_course_staff_to_ccx(
                master_course_object,
                ccx_course_key,
                ccx_course_object.display_name,
                send_email=False
            )

        serializer = self.get_serializer(ccx_course_object)

        # using CCX object as sender here.
        responses = SignalHandler.course_published.send(
            sender=ccx_course_object,
            course_key=ccx_course_key
        )
        for rec, response in responses:
            log.info('Signal fired when course is published. Receiver: %s. Response: %s', rec, response)
        return Response(
            status=status.HTTP_201_CREATED,
            data=serializer.data
        )


class CCXDetailView(GenericAPIView):
    """
        **Use Case**

            * Get the details of CCX course.

            * Modify a CCX course.

            * Delete a CCX course.

        **Example Request**

            GET /api/ccx/v0/ccx/{ccx_course_id}

            PATCH /api/ccx/v0/ccx/{ccx_course_id} {

                "display_name": "CCX example title modified",
                "coach_email": "joe@example.com",
                "max_students_allowed": 111,
                "course_modules" : [
                    "block-v1:Organization+EX101+RUN-FALL2099+type@chapter+block@week1",
                    "block-v1:Organization+EX101+RUN-FALL2099+type@chapter+block@week4",
                    "block-v1:Organization+EX101+RUN-FALL2099+type@chapter+block@week5"
                ]
            }

            DELETE /api/ccx/v0/ccx/{ccx_course_id}

        **GET and DELETE Parameters**

            A GET or DELETE request must include the following parameter.

            * ccx_course_id: A string representation of a CCX Course ID.

        **PATCH Parameters**

            A PATCH request can include the following parameters

            * ccx_course_id: A string representation of a CCX Course ID.

            * display_name: Optional. A string representing the CCX Course title.

            * coach_email: Optional. A string representing the CCX owner email.

            * max_students_allowed: Optional. An integer representing he maximum number of students that
              can be enrolled in the CCX Course.

            * course_modules: Optional. A list of course modules id keys.

        **GET Response Values**

            If the request for information about the CCX course is successful, an HTTP 200 "OK" response
            is returned.

            The HTTP 200 response has the following values.

            * ccx_course_id: A string representation of a CCX Course ID.

            * display_name: A string representing the CCX Course title.

            * coach_email: A string representing the CCX owner email.

            * start: A string representing the start date for the CCX Course.

            * due: A string representing the due date for the CCX Course.

            * max_students_allowed: An integer representing he maximum number of students that
              can be enrolled in the CCX Course.

            * course_modules: A list of course modules id keys.

        **PATCH and DELETE Response Values**

            If the request for modification or deletion of a CCX course is successful, an HTTP 204 "No Content"
            response is returned.
    """

    authentication_classes = (
        JwtAuthentication,
        authentication.BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated, permissions.IsCourseStaffInstructor)
    serializer_class = CCXCourseSerializer

    def get_object(self, course_id, is_ccx=False):  # pylint: disable=arguments-differ
        """
        Override the default get_object to allow a custom getter for the CCX
        """
        course_object, course_key, error_code, http_status = get_valid_course(course_id, is_ccx)
        self.check_object_permissions(self.request, course_object)
        return course_object, course_key, error_code, http_status

    def get(self, request, ccx_course_id=None):
        """
        Gets a CCX Course information.

        Args:
            request (Request): Django request object.
            ccx_course_id (string): URI element specifying the CCX course location.

        Return:
            A JSON serialized representation of the CCX course.
        """
        ccx_course_object, _, error_code, http_status = self.get_object(ccx_course_id, is_ccx=True)
        if ccx_course_object is None:
            return Response(
                status=http_status,
                data={
                    'error_code': error_code
                }
            )
        serializer = self.get_serializer(ccx_course_object)
        return Response(serializer.data)

    def delete(self, request, ccx_course_id=None):
        """
        Deletes a CCX course.

        Args:
            request (Request): Django request object.
            ccx_course_id (string): URI element specifying the CCX course location.
        """
        ccx_course_object, ccx_course_key, error_code, http_status = self.get_object(ccx_course_id, is_ccx=True)
        if ccx_course_object is None:
            return Response(
                status=http_status,
                data={
                    'error_code': error_code
                }
            )
        ccx_course_overview = CourseOverview.get_from_id(ccx_course_key)
        # clean everything up with a single transaction
        with transaction.atomic():
            CcxFieldOverride.objects.filter(ccx=ccx_course_object).delete()
            # remove all users enrolled in the CCX from the CourseEnrollment model
            CourseEnrollment.objects.filter(course_id=ccx_course_key).delete()
            ccx_course_overview.delete()
            ccx_course_object.delete()
        return Response(
            status=status.HTTP_204_NO_CONTENT,
        )

    def patch(self, request, ccx_course_id=None):
        """
        Modifies a CCX course.

        Args:
            request (Request): Django request object.
            ccx_course_id (string): URI element specifying the CCX course location.
        """
        ccx_course_object, ccx_course_key, error_code, http_status = self.get_object(ccx_course_id, is_ccx=True)
        if ccx_course_object is None:
            return Response(
                status=http_status,
                data={
                    'error_code': error_code
                }
            )

        master_course_id = request.data.get('master_course_id')
        if master_course_id is not None and str(ccx_course_object.course_id) != master_course_id:
            return Response(
                status=status.HTTP_403_FORBIDDEN,
                data={
                    'error_code': 'master_course_id_change_not_allowed'
                }
            )

        valid_input, field_errors = get_valid_input(request.data, ignore_missing=True)
        if field_errors:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={
                    'field_errors': field_errors
                }
            )

        # get the master course key and master course object
        master_course_object, master_course_key, _, _ = get_valid_course(str(ccx_course_object.course_id))

        with transaction.atomic():
            # update the display name
            if 'display_name' in valid_input:
                ccx_course_object.display_name = valid_input['display_name']
            # check if the coach has changed and in case update it
            old_coach = None
            if 'coach_email' in valid_input:
                try:
                    coach = User.objects.get(email=valid_input['coach_email'])
                except User.DoesNotExist:
                    return Response(
                        status=status.HTTP_404_NOT_FOUND,
                        data={
                            'error_code': 'coach_user_does_not_exist'
                        }
                    )
                if ccx_course_object.coach.id != coach.id:
                    old_coach = ccx_course_object.coach
                    ccx_course_object.coach = coach
            if 'course_modules' in valid_input:
                if valid_input.get('course_modules'):
                    if not valid_course_modules(valid_input['course_modules'], master_course_key):
                        return Response(
                            status=status.HTTP_400_BAD_REQUEST,
                            data={
                                'error_code': 'course_module_list_not_belonging_to_master_course'
                            }
                        )
                # course_modules to be stored in a json stringified field
                ccx_course_object.structure_json = json.dumps(valid_input.get('course_modules'))
            ccx_course_object.save()
            # update the overridden field for the maximum amount of students
            if 'max_students_allowed' in valid_input:
                override_field_for_ccx(
                    ccx_course_object,
                    ccx_course_object.course,
                    'max_student_enrollments_allowed',
                    valid_input['max_students_allowed']
                )
            # if the coach has changed, update the permissions
            if old_coach is not None:
                # make the new ccx coach a coach on the master course
                make_user_coach(coach, master_course_key)
                # enroll the coach in the ccx
                email_params = get_email_params(
                    master_course_object,
                    auto_enroll=True,
                    course_key=ccx_course_key,
                    display_name=ccx_course_object.display_name
                )
                enroll_email(
                    course_id=ccx_course_key,
                    student_email=coach.email,
                    auto_enroll=True,
                    message_students=True,
                    message_params=email_params,
                )
                # make the new coach staff on the CCX
                assign_staff_role_to_ccx(ccx_course_key, coach, master_course_object.id)

        # using CCX object as sender here.
        responses = SignalHandler.course_published.send(
            sender=ccx_course_object,
            course_key=ccx_course_key
        )
        for rec, response in responses:
            log.info('Signal fired when course is published. Receiver: %s. Response: %s', rec, response)

        return Response(
            status=status.HTTP_204_NO_CONTENT,
        )
