"""
Course Info serializers
"""

from typing import Dict, Union  # noqa: UP035

from rest_framework import serializers
from rest_framework.reverse import reverse

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.util.course import get_encoded_course_sharing_utm_params, get_link_for_about_page
from common.djangoapps.util.milestones_helpers import get_pre_requisite_courses_not_completed
from lms.djangoapps.courseware.access import administrative_accesses_to_course_for_user, has_access
from lms.djangoapps.courseware.access_utils import check_course_open_for_learner
from lms.djangoapps.courseware.courses import get_assignments_completions
from lms.djangoapps.mobile_api.course_info.utils import get_user_certificate_download_url
from lms.djangoapps.mobile_api.users.serializers import ModeSerializer
from lms.djangoapps.mobile_api.utils import get_course_organization_logo
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.features.course_duration_limits.access import get_user_course_expiration_date


class CourseInfoOverviewSerializer(serializers.ModelSerializer):
    """
    Serializer for additional course fields that should be returned in BlocksInfoInCourseView.
    """

    name = serializers.CharField(source='display_name')
    number = serializers.CharField(source='display_number_with_default')
    org = serializers.CharField(source='display_org_with_default')
    org_logo = serializers.SerializerMethodField()
    is_self_paced = serializers.BooleanField(source='self_paced')
    media = serializers.SerializerMethodField()
    course_sharing_utm_parameters = serializers.SerializerMethodField()
    course_about = serializers.SerializerMethodField('get_course_about_url')
    course_modes = serializers.SerializerMethodField()
    course_progress = serializers.SerializerMethodField()

    class Meta:
        model = CourseOverview
        fields = (
            'name',
            'number',
            'org',
            'org_logo',
            'start',
            'start_display',
            'start_type',
            'end',
            'is_self_paced',
            'media',
            'course_sharing_utm_parameters',
            'course_about',
            'course_modes',
            'course_progress',
        )

    @staticmethod
    def get_media(obj):
        """
        Return course images in the correct format.
        """
        return {'image': obj.image_urls}

    def get_course_sharing_utm_parameters(self, obj):
        return get_encoded_course_sharing_utm_params()

    def get_course_about_url(self, course_overview):
        return get_link_for_about_page(course_overview)

    def get_course_modes(self, course_overview):
        """
        Retrieve course modes associated with the course.
        """
        course_modes = CourseMode.modes_for_course(
            course_overview.id,
            only_selectable=False
        )
        return [
            ModeSerializer(mode).data
            for mode in course_modes
        ]

    def get_course_progress(self, obj: CourseOverview) -> Dict[str, int]:  # noqa: UP006
        """
        Gets course progress calculated by course completed assignments.
        """
        return get_assignments_completions(obj.id, self.context.get('user'))

    def get_org_logo(self, course_overview):
        return get_course_organization_logo(course_overview.id)


class MobileCourseEnrollmentSerializer(serializers.ModelSerializer):
    """
    Serializer for the CourseEnrollment object used in the BlocksInfoInCourseView.
    """

    class Meta:
        fields = ('created', 'mode', 'is_active', 'upgrade_deadline')
        model = CourseEnrollment
        lookup_field = 'username'


class CourseAccessSerializer(serializers.Serializer):
    """
    Get info whether a user should be able to view course material.
    """

    has_unmet_prerequisites = serializers.SerializerMethodField(method_name='get_has_unmet_prerequisites')
    is_too_early = serializers.SerializerMethodField(method_name='get_is_too_early')
    is_staff = serializers.SerializerMethodField(method_name='get_is_staff')
    audit_access_expires = serializers.SerializerMethodField()
    courseware_access = serializers.SerializerMethodField()

    def get_has_unmet_prerequisites(self, data: dict) -> bool:
        """
        Check whether or not a course has unmet prerequisites.
        """
        return any(get_pre_requisite_courses_not_completed(data.get('user'), [data.get('course_id')]))

    def get_is_too_early(self, data: dict) -> bool:
        """
        Determine if the course is open to a learner (course has started or user has early beta access).
        """
        return not check_course_open_for_learner(data.get('user'), data.get('course'))

    def get_is_staff(self, data: dict) -> bool:
        """
        Determine whether a user has staff access to this course.
        """
        return any(administrative_accesses_to_course_for_user(data.get('user'), data.get('course_id')))

    def get_audit_access_expires(self, data: dict) -> Union[str, None]:  # noqa: UP007
        """
        Returns expiration date for a course audit expiration, if any or null
        """
        return get_user_course_expiration_date(data.get('user'), data.get('course'))

    def get_courseware_access(self, data: dict) -> dict:
        """
        Determine if the learner has access to the course, otherwise show error message.
        """
        return has_access(data.get('user'), 'load_mobile', data.get('course')).to_json()


class CourseDetailSerializer(serializers.Serializer):
    """
    Serializer for Course enrollment and overview details.
    """

    id = serializers.SerializerMethodField()
    course_access_details = serializers.SerializerMethodField()
    certificate = serializers.SerializerMethodField()
    enrollment_details = serializers.SerializerMethodField()
    course_handouts = serializers.SerializerMethodField()
    course_updates = serializers.SerializerMethodField()
    discussion_url = serializers.SerializerMethodField()
    course_info_overview = serializers.SerializerMethodField()

    @staticmethod
    def get_id(data):
        """
        Returns course id.
        """
        return str(data['course_id'])

    @staticmethod
    def get_course_overview(course_id):
        """
        Returns course overview.
        """
        return CourseOverview.get_from_id(course_id)

    def get_course_info_overview(self, data):
        """
        Returns course info overview.
        """
        course_overview = self.get_course_overview(data['course_id'])
        course_info_context = {'user': data['user']}
        return CourseInfoOverviewSerializer(course_overview, context=course_info_context).data

    @staticmethod
    def get_discussion_url(data):
        """
        Returns discussion url.
        """
        course_overview = CourseOverview.get_from_id(data['course_id'])
        if not course_overview.is_discussion_tab_enabled(data['user']):
            return

        return reverse('discussion_course', kwargs={'course_id': data['course_id']}, request=data['request'])

    def get_course_access_details(self, data):
        """
        Returns course access details.
        """
        course_access_data = {
            'course': self.get_course_overview(data['course_id']),
            'course_id': data['course_id'],
            'user': data['user'],
        }
        return CourseAccessSerializer(course_access_data).data

    @staticmethod
    def get_certificate(data):
        """
        Returns course certificate url.
        """
        return get_user_certificate_download_url(data['request'], data['user'], data['course_id'])

    @staticmethod
    def get_enrollment_details(data):
        """
        Retrieve course enrollment details of the course.
        """
        user_enrollment = CourseEnrollment.get_enrollment(user=data['user'], course_key=data['course_id'])
        return MobileCourseEnrollmentSerializer(user_enrollment).data

    @staticmethod
    def get_course_handouts(data):
        """
        Returns course_handouts.
        """

        url_params = {'api_version': data['api_version'], 'course_id': data['course_id']}
        return reverse('course-handouts-list', kwargs=url_params, request=data['request'])

    @staticmethod
    def get_course_updates(data):
        """
        Returns course_updates.
        """
        url_params = {'api_version': data['api_version'], 'course_id': data['course_id']}
        return reverse('course-updates-list', kwargs=url_params, request=data['request'])
