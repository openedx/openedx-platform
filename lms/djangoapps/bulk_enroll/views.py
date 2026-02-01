"""
API views for Bulk Enrollment
"""


from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from six.moves import zip_longest

from common.djangoapps.util.disable_rate_limit import can_disable_rate_limit
from lms.djangoapps.bulk_enroll.serializers import BulkEnrollmentSerializer
from lms.djangoapps.instructor.views.api import StudentsUpdateEnrollmentView
from openedx.core.djangoapps.course_groups.cohorts import add_user_to_cohort, get_cohort_by_name
from openedx.core.djangoapps.course_groups.models import CourseUserGroup
from openedx.core.djangoapps.enrollments.views import EnrollmentUserThrottle
from openedx.core.lib.api.authentication import BearerAuthentication
from openedx.core.lib.api.permissions import IsStaff


@can_disable_rate_limit
class BulkEnrollView(APIView):
    """
    **Use Case**

        Enroll multiple users in one or more courses.

    **Example Request**

        POST /api/bulk_enroll/v1/bulk_enroll/ {
            "auto_enroll": true,
            "email_students": true,
            "action": "enroll",
            "courses": "course-v1:edX+Demo+123,course-v1:edX+Demo2+456",
            "cohorts": "cohortA,cohortA",
            "identifiers": "brandon@example.com,yamilah@example.com"
        }

        **POST Parameters**

          A POST request can include the following parameters.

          * auto_enroll: When set to `true`, students will be enrolled as soon
            as they register.
          * email_students: When set to `true`, students will be sent email
            notifications upon enrollment.
          * action: Can either be set to "enroll" or "unenroll". This determines the behavior
          * cohorts: Optional. If provided, the number of items in the list should be equal to
            the number of courses. first cohort coressponds with the first course and so on.
            The learners will be added to the corresponding cohort.

    **Response Values**

        If the supplied course data is valid and the enrollments were
        successful, an HTTP 200 "OK" response is returned.

        The HTTP 200 response body contains a list of response data for each
        enrollment. (See the `instructor.views.api.students_update_enrollment`
        docstring for the specifics of the response data available for each
        enrollment)

        If a cohorts list is provided, additional 'cohort' keys will be added
        to the 'before' and 'after' states.
    """

    authentication_classes = (JwtAuthentication, BearerAuthentication,)
    permission_classes = (IsStaff,)
    throttle_classes = (EnrollmentUserThrottle,)

    def post(self, request):  # lint-amnesty, pylint: disable=missing-function-docstring
        serializer = BulkEnrollmentSerializer(data=request.data)
        if serializer.is_valid():  # lint-amnesty, pylint: disable=too-many-nested-blocks
            # Setting the content type to be form data makes Django Rest Framework v3.6.3 treat all passed JSON data as
            # POST parameters. This is necessary because this request is forwarded on to the student_update_enrollment
            # view, which requires all of the parameters to be passed in via POST parameters.
            metadata = request._request.META  # pylint: disable=protected-access
            metadata['CONTENT_TYPE'] = 'application/x-www-form-urlencoded'

            response_dict = {
                'auto_enroll': serializer.data.get('auto_enroll'),
                'email_students': serializer.data.get('email_students'),
                'action': serializer.data.get('action'),
                'courses': {}
            }
            for course_id, cohort_name in zip_longest(serializer.data.get('courses'),
                                                      serializer.data.get('cohorts', [])):
                # Internal request to DRF view
                view = StudentsUpdateEnrollmentView()
                response_content = view._process_student_enrollment(  # pylint: disable=protected-access
                    request=request,
                    course_id=course_id,
                    data=request.data,
                    secure=request.is_secure()
                )

                if cohort_name:
                    try:
                        course_key = CourseKey.from_string(course_id)
                        cohort = get_cohort_by_name(course_key=course_key, name=cohort_name)
                    except (CourseUserGroup.DoesNotExist, InvalidKeyError) as exc:
                        return Response(exc.message, status=status.HTTP_400_BAD_REQUEST)

                    for user_data in response_content['results']:
                        if "after" in user_data and (
                            user_data["after"].get("enrollment", False) is True or
                            user_data["after"].get("allowed", False) is True
                        ):
                            user_id = user_data['identifier']
                            try:
                                _user_obj, previous_cohort, _pre_assigned = add_user_to_cohort(cohort, user_id)
                            except ValueError:
                                # User already present in cohort
                                previous_cohort = cohort_name

                            if previous_cohort:
                                user_data['before']['cohort'] = previous_cohort
                            else:
                                user_data['before']['cohort'] = None
                            user_data['after']['cohort'] = cohort_name

                response_dict['courses'][course_id] = response_content
            return Response(data=response_dict, status=status.HTTP_200_OK)
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
