"""
Helper methods for testing cohorts.
"""


import json

from factory import Sequence, post_generation
from factory.django import DjangoModelFactory
from opaque_keys.edx.locator import CourseLocator

from openedx.core.djangoapps.django_comment_common.models import CourseDiscussionSettings
from xmodule.modulestore import ModuleStoreEnum  # pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

from ..cohorts import set_course_cohorted
from ..models import CohortMembership, CourseCohort, CourseCohortsSettings, CourseUserGroup


class CohortFactory(DjangoModelFactory):
    """
    Factory for constructing mock cohorts.
    """
    class Meta:
        model = CourseUserGroup

    name = Sequence("cohort{}".format)
    course_id = CourseLocator("dummy", "dummy", "dummy")
    group_type = CourseUserGroup.COHORT

    @post_generation
    def users(self, create, extracted, **kwargs):  # pylint: disable=unused-argument
        """
        Returns the users associated with the cohort.
        """
        if extracted:
            self.users.add(*extracted)  # pylint: disable=no-member
            for user in self.users.all():  # pylint: disable=no-member
                CohortMembership.objects.create(
                    user=user,
                    course_user_group=self,
                )


class CourseCohortFactory(DjangoModelFactory):
    """
    Factory for constructing mock course cohort.
    """
    class Meta:
        model = CourseCohort


class CourseCohortSettingsFactory(DjangoModelFactory):
    """
    Factory for constructing mock course cohort settings.
    """
    class Meta:
        model = CourseCohortsSettings

    is_cohorted = False
    course_id = CourseLocator("dummy", "dummy", "dummy")
    cohorted_discussions = json.dumps([])
    always_cohort_inline_discussions = False


def config_course_cohorts_legacy(
        course,
        cohorted,
        auto_cohort_groups=None
):
    """
    Given a course with no discussion set up, add the discussions and set
    the cohort config on the course block.

    Since cohort settings are now stored in models.CourseCohortSettings,
    this is only used for testing data migration from the CourseBlock
    to the table.

    Arguments:
        course: CourseBlock
        cohorted: bool.
        auto_cohort_groups: optional list of strings
                  (names of groups to put students into).

    Returns:
        Nothing -- modifies course in place.
    """
    course.discussion_topics = {}

    config = {"cohorted": cohorted}
    if auto_cohort_groups is not None:
        config["auto_cohort_groups"] = auto_cohort_groups

    course.cohort_config = config

    try:
        # Not implemented for XMLModulestore, which is used by test_cohorts.
        modulestore().update_item(course, ModuleStoreEnum.UserID.test)
    except NotImplementedError:
        pass


# pylint: disable=dangerous-default-value
def config_course_cohorts(
        course,
        is_cohorted,
        discussion_division_scheme=CourseDiscussionSettings.COHORT,
        auto_cohorts=[],  # noqa: B006
        manual_cohorts=[],  # noqa: B006
):
    """
    Set and configure cohorts for a course.

    Arguments:
        course: CourseBlock
        is_cohorted (bool): Is the course cohorted?
        discussion_division_scheme (String): the division scheme for discussions. Default is
            CourseDiscussionSettings.COHORT.
        auto_cohorts (list): Names of auto cohorts to create.
        manual_cohorts (list): Names of manual cohorts to create.

    Returns:
        Nothing -- modifies course in place.
    """

    set_course_cohorted(course.id, is_cohorted)
    discussion_settings = CourseDiscussionSettings.get(course.id)
    discussion_settings.update({
        'division_scheme': discussion_division_scheme,
    })

    for cohort_name in auto_cohorts:
        cohort = CohortFactory(course_id=course.id, name=cohort_name)
        CourseCohortFactory(course_user_group=cohort, assignment_type=CourseCohort.RANDOM)

    for cohort_name in manual_cohorts:
        cohort = CohortFactory(course_id=course.id, name=cohort_name)
        CourseCohortFactory(course_user_group=cohort, assignment_type=CourseCohort.MANUAL)

    try:
        # Not implemented for XMLModulestore, which is used by test_cohorts.
        modulestore().update_item(course, ModuleStoreEnum.UserID.test)
    except NotImplementedError:
        pass
