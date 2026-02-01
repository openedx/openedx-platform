"""
This file contains implementation override of SearchFilterGenerator which will allow
    * Filter by all courses in which the user is enrolled in
"""
from django.conf import settings
from search.filter_generator import SearchFilterGenerator

from openedx.core.djangoapps.course_groups.partition_scheme import CohortPartitionScheme
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_api.partition_schemes import RandomUserPartitionScheme
from common.djangoapps.student.models import CourseEnrollment
from xmodule.course_block import CATALOG_VISIBILITY_ABOUT, CATALOG_VISIBILITY_NONE

INCLUDE_SCHEMES = [CohortPartitionScheme, RandomUserPartitionScheme, ]
SCHEME_SUPPORTS_ASSIGNMENT = [RandomUserPartitionScheme, ]


class LmsSearchFilterGenerator(SearchFilterGenerator):
    """ SearchFilterGenerator for LMS Search """

    _user_enrollments = {}

    def _enrollments_for_user(self, user):
        """ Return the specified user's course enrollments """
        if user not in self._user_enrollments:
            self._user_enrollments[user] = CourseEnrollment.enrollments_for_user(user)
        return self._user_enrollments[user]

    def field_dictionary(self, **kwargs):
        """ add course if provided otherwise add courses in which the user is enrolled in """
        field_dictionary = super().field_dictionary(**kwargs)
        course_id = kwargs.get('course_id')
        if not kwargs.get('user'):
            field_dictionary['course'] = []
        elif not course_id:
            user_enrollments = self._enrollments_for_user(kwargs['user'])
            field_dictionary['course'] = [str(enrollment.course_id) for enrollment in user_enrollments]

        # if we have no specific course and an org filter, only include results for this org filter
        if not course_id:
            course_org_filter = configuration_helpers.get_current_site_orgs()
            if course_org_filter:
                field_dictionary['org'] = course_org_filter

        return field_dictionary

    def exclude_dictionary(self, **kwargs):
        """
            Exclude any courses defined outside the current org.
        """
        exclude_dictionary = super().exclude_dictionary(**kwargs)
        # If we are already filtering to a single course, we do not need
        # the further course filters below.
        if kwargs.get('course_id'):
            return exclude_dictionary

        course_org_filter = configuration_helpers.get_current_site_orgs()
        # If we have a course filter we are ensuring that we only get those courses above
        if not course_org_filter:
            org_filter_out_set = configuration_helpers.get_all_orgs()
            if org_filter_out_set:
                exclude_dictionary['org'] = list(org_filter_out_set)

        if not getattr(settings, "SEARCH_SKIP_INVITATION_ONLY_FILTERING", True):
            exclude_dictionary['invitation_only'] = True
        if not getattr(settings, "SEARCH_SKIP_SHOW_IN_CATALOG_FILTERING", True):
            exclude_dictionary['catalog_visibility'] = [CATALOG_VISIBILITY_ABOUT, CATALOG_VISIBILITY_NONE]

        return exclude_dictionary