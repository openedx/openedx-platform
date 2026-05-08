"""
Serializer for user API
"""

from typing import Dict, List, Optional  # noqa: UP035

from completion.exceptions import UnavailableCompletionData
from completion.utilities import get_key_to_last_completed_block
from opaque_keys.edx.keys import UsageKey
from rest_framework import serializers
from rest_framework.reverse import reverse

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment, User
from common.djangoapps.util.course import get_encoded_course_sharing_utm_params, get_link_for_about_page
from lms.djangoapps.certificates.api import certificate_downloadable_status
from lms.djangoapps.course_home_api.dates.serializers import DateSummarySerializer
from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.courseware.courses import get_assignments_completions, get_past_and_future_course_assignments
from lms.djangoapps.mobile_api.utils import API_V4, get_course_organization_logo
from openedx.features.course_duration_limits.access import get_user_course_expiration_date
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError, NoPathToItem
from xmodule.modulestore.search import path_to_location


class CourseOverviewField(serializers.RelatedField):  # pylint: disable=abstract-method
    """
    Custom field to wrap a CourseOverview object. Read-only.
    """
    def to_representation(self, course_overview):  # pylint: disable=arguments-differ
        course_id = str(course_overview.id)
        request = self.context.get('request')
        api_version = self.context.get('api_version')
        enrollment = CourseEnrollment.get_enrollment(user=self.context.get('request').user, course_key=course_id)
        return {
            # identifiers
            'id': course_id,
            'name': course_overview.display_name,
            'number': course_overview.display_number_with_default,
            'org': course_overview.display_org_with_default,
            'org_logo': get_course_organization_logo(course_id),

            # dates
            'start': course_overview.start,
            'start_display': course_overview.start_display,
            'start_type': course_overview.start_type,
            'end': course_overview.end,
            'dynamic_upgrade_deadline': enrollment.upgrade_deadline,

            # notification info
            'subscription_id': course_overview.clean_id(padding_char='_'),

            # access info
            'courseware_access': has_access(
                request.user,
                'load_mobile',
                course_overview
            ).to_json(),

            # various URLs
            # course_image is sent in both new and old formats
            # (within media to be compatible with the new Course API)
            'media': {
                'course_image': {
                    'uri': course_overview.course_image_url,
                    'name': 'Course Image',
                }
            },
            'course_image': course_overview.course_image_url,
            'course_about': get_link_for_about_page(course_overview),
            'course_sharing_utm_parameters': get_encoded_course_sharing_utm_params(),
            'course_updates': reverse(
                'course-updates-list',
                kwargs={'api_version': api_version, 'course_id': course_id},
                request=request,
            ),
            'course_handouts': reverse(
                'course-handouts-list',
                kwargs={'api_version': api_version, 'course_id': course_id},
                request=request,
            ),
            'discussion_url': reverse(
                'discussion_course',
                kwargs={'course_id': course_id},
                request=request,
            ) if course_overview.is_discussion_tab_enabled(request.user) else None,

            # This is an old API that was removed as part of DEPR-4. We keep the
            # field present in case API parsers expect it, but this API is now
            # removed.
            'video_outline': None,

            'is_self_paced': course_overview.self_paced
        }


class CourseEnrollmentSerializer(serializers.ModelSerializer):
    """
    Serializes CourseEnrollment models
    """
    course = CourseOverviewField(source="course_overview", read_only=True)
    certificate = serializers.SerializerMethodField()
    audit_access_expires = serializers.SerializerMethodField()
    course_modes = serializers.SerializerMethodField()

    def get_audit_access_expires(self, model):
        """
        Returns expiration date for a course audit expiration, if any or null
        """
        return get_user_course_expiration_date(model.user, model.course, model)

    def get_certificate(self, model):
        """Returns the information about the user's certificate in the course."""
        certificate_info = certificate_downloadable_status(model.user, model.course_id)
        if certificate_info['is_downloadable']:
            return {
                'url': self.context['request'].build_absolute_uri(
                    certificate_info['download_url']
                ),
            }
        else:
            return {}

    def get_course_modes(self, obj):
        """
        Retrieve course modes associated with the course.
        """
        course_modes = CourseMode.modes_for_course(
            obj.course.id,
            only_selectable=False
        )
        return [
            ModeSerializer(mode).data
            for mode in course_modes
        ]

    def to_representation(self, instance: CourseEnrollment) -> 'OrderedDict':  # pylint: disable=unused-variable, line-too-long  # noqa: F821
        """
        Override the to_representation method to add the course_status field to the serialized data.
        """
        data = super().to_representation(instance)

        if 'course_progress' in self.context.get('requested_fields', []) and self.context.get('api_version') == API_V4:
            data['course_progress'] = get_assignments_completions(instance.course_id, instance.user)

        return data

    class Meta:
        model = CourseEnrollment
        fields = ('audit_access_expires', 'created', 'mode', 'is_active', 'course', 'certificate', 'course_modes')
        lookup_field = 'username'


class CourseEnrollmentSerializerv05(CourseEnrollmentSerializer):
    """
    Serializes CourseEnrollment models for v0.5 api
    Does not include 'audit_access_expires' field that is present in v1 api
    """
    class Meta:
        model = CourseEnrollment
        fields = ('created', 'mode', 'is_active', 'course', 'certificate')
        lookup_field = 'username'


class CourseEnrollmentSerializerModifiedForPrimary(CourseEnrollmentSerializer):
    """
    Serializes CourseEnrollment models for API v4.

    Adds `course_status` field into serializer data.
    """

    course_status = serializers.SerializerMethodField()
    course_progress = serializers.SerializerMethodField()
    course_assignments = serializers.SerializerMethodField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.course = modulestore().get_course(self.instance.course.id)

    def get_course_status(self, model: CourseEnrollment) -> Optional[Dict[str, List[str]]]:  # noqa: UP006, UP045
        """
        Gets course status for the given user's enrollments.
        """
        try:
            block_key = get_key_to_last_completed_block(model.user, model.course.id)
            path = path_to_location(modulestore(), block_key, self.context['request'], full_path=True)
        except (ItemNotFoundError, NoPathToItem, UnavailableCompletionData):
            return None

        path_ids = [str(block) for block in path]
        unit = modulestore().get_item(UsageKey.from_string(path_ids[3]), depth=0)

        return {
            'last_visited_module_id': path_ids[2],
            'last_visited_module_path': path_ids[:3],
            'last_visited_block_id': path_ids[-1],
            'last_visited_unit_display_name': unit.display_name,
        }

    def get_course_progress(self, model: CourseEnrollment) -> Dict[str, int]:  # noqa: UP006
        """
        Returns the progress of the user in the course.
        """
        return get_assignments_completions(model.course_id, model.user)

    def get_course_assignments(self, model: CourseEnrollment) -> Dict[str, Optional[List[Dict[str, str]]]]:  # noqa: UP006, UP045  # pylint: disable=line-too-long
        """
        Returns the future assignment data and past assignments data for the user in the course.
        """
        next_assignments, past_assignments = get_past_and_future_course_assignments(
            self.context.get('request'), model.user, self.course
        )
        return {
            'future_assignments': DateSummarySerializer(next_assignments, many=True).data,
            'past_assignments': DateSummarySerializer(past_assignments, many=True).data,
        }

    class Meta:
        model = CourseEnrollment
        fields = (
            'audit_access_expires',
            'created',
            'mode',
            'is_active',
            'course',
            'certificate',
            'course_modes',
            'course_status',
            'course_progress',
            'course_assignments',
        )
        lookup_field = 'username'


class UserSerializer(serializers.ModelSerializer):
    """
    Serializes User models
    """
    name = serializers.ReadOnlyField(source='profile.name')
    course_enrollments = serializers.SerializerMethodField()

    def get_course_enrollments(self, model):
        request = self.context.get('request')
        api_version = self.context.get('api_version')

        return reverse(
            'courseenrollment-detail',
            kwargs={'api_version': api_version, 'username': model.username},
            request=request
        )

    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'name', 'course_enrollments')
        lookup_field = 'username'
        # For disambiguating within the drf-yasg swagger schema
        ref_name = 'mobile_api.User'


class ModeSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Serializes a course's 'Mode' tuples

    Returns a serialized representation of the modes available for course enrollment. The course
    modes models are designed to return a tuple instead of the model object itself. This serializer
    handles the given tuple.

    """
    slug = serializers.CharField(max_length=100)
    sku = serializers.CharField()
    android_sku = serializers.CharField()
    ios_sku = serializers.CharField()
    min_price = serializers.IntegerField()
