"""
Tests who may read a course run's competency criteria on the LMS.

openedx-core serves ``GET /api/cbe/v1/courses/<course_key>/criteria/`` and checks the permission
``openedx_learning.view_course_competency_criteria``. This module covers the platform's override of that
permission (active enrollment or staff access) and the LMS mounting of the learner URLs.
"""

from datetime import UTC, datetime, timedelta

import ddt
from openedx_catalog import api as catalog_api
from openedx_catalog.models_api import CourseRun
from openedx_learning.api import associate_competency_criterion, create_competency_taxonomy
from openedx_learning.models_api import RuleType
from openedx_tagging.api import add_tag_to_taxonomy

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import CourseStaffRole
from common.djangoapps.student.tests.factories import TEST_PASSWORD, CourseEnrollmentFactory, UserFactory
from lms.djangoapps.courseware.masquerade import MASQUERADE_SETTINGS_KEY
from lms.djangoapps.courseware.tests.helpers import MasqueradeMixin
from openedx.core.djangoapps.course_groups.cohorts import set_course_cohorted
from openedx.core.djangoapps.course_groups.tests.helpers import CohortFactory
from openedx.core.djangoapps.course_groups.views import link_cohort_to_partition_group
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory
from xmodule.partitions.partitions import Group, UserPartition

PERMISSION = "openedx_learning.view_course_competency_criteria"
EMPTY_RESULT = {"taxonomies": [], "competencies": [], "groups": [], "criteria": []}


def criteria_url(course_key) -> str:
    """
    Return the LMS path of the criteria endpoint for the given course key (a CourseKey or a string).
    """
    return f"/api/cbe/v1/courses/{course_key}/criteria/"


class CourseCriteriaTestBase(ModuleStoreTestCase, MasqueradeMixin):
    """
    Creates a course run and helpers to sign users in and request its criteria.
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(org="Org1", number="Python100", run="Fall2026")
        self.url = criteria_url(self.course.id)

    def get(self, user, url=None):
        """
        Sign in the given user (or sign out for None) and GET the criteria endpoint.
        """
        self.client.logout()
        if user is not None:
            # login() rather than force_login(): the safe-sessions middleware can't parse the session cookie
            # that force_login() sets, so the request would carry no session at all.
            self.client.login(username=user.username, password=TEST_PASSWORD)
        return self.client.get(url or self.url)

    def make_staff(self, course=None):
        """
        Return a user with the course staff role in the course, who is not enrolled.
        """
        user = UserFactory()
        CourseStaffRole((course or self.course).id).add_users(user)
        return user

    def assert_allowed(self, response):
        """
        Assert the request was allowed and returned the four criteria arrays.
        """
        assert response.status_code == 200, response.content
        assert set(response.json()) == set(EMPTY_RESULT)


@skip_unless_lms
@ddt.ddt
class TestCourseCriteriaAccess(CourseCriteriaTestBase):
    """
    HTTP-level access rules of the criteria endpoint on the LMS.
    """

    def test_signed_out_is_unauthorized(self):
        response = self.get(None)

        assert response.status_code == 401

    def test_never_enrolled_is_forbidden(self):
        response = self.get(UserFactory())

        assert response.status_code == 403

    def test_enrolled_then_unenrolled_is_forbidden(self):
        user = UserFactory()
        CourseEnrollment.enroll(user, self.course.id)
        CourseEnrollment.unenroll(user, self.course.id)

        response = self.get(user)

        assert response.status_code == 403

    def test_inactive_enrollment_row_is_forbidden(self):
        user = UserFactory()
        CourseEnrollmentFactory(user=user, course_id=self.course.id, is_active=False)

        response = self.get(user)

        assert response.status_code == 403

    def test_enrollment_in_another_course_run_is_forbidden(self):
        other_course = CourseFactory.create(org="Org1", number="Python100", run="Spring2027")
        user = UserFactory()
        CourseEnrollmentFactory(user=user, course_id=other_course.id)

        response = self.get(user)

        assert response.status_code == 403

    def test_learner_with_unknown_course_run_is_forbidden(self):
        response = self.get(UserFactory(), criteria_url("course-v1:NoOrg+NoCourse+NoRun"))

        assert response.status_code == 403

    def test_global_staff_with_unknown_course_run_gets_empty_result(self):
        response = self.get(UserFactory(is_staff=True), criteria_url("course-v1:NoOrg+NoCourse+NoRun"))

        assert response.status_code == 200
        assert response.json() == EMPTY_RESULT

    @ddt.data("audit", "verified")
    def test_active_enrollment_is_allowed(self, mode):
        user = UserFactory()
        CourseEnrollmentFactory(user=user, course_id=self.course.id, mode=mode)

        self.assert_allowed(self.get(user))

    def test_active_enrollment_before_course_start_is_allowed(self):
        future_course = CourseFactory.create(
            org="Org1",
            number="Future100",
            run="Fall2027",
            metadata={"start": datetime.now(UTC) + timedelta(days=30)},
        )
        user = UserFactory()
        CourseEnrollmentFactory(user=user, course_id=future_course.id)

        self.assert_allowed(self.get(user, criteria_url(future_course.id)))

    def test_course_staff_who_is_not_enrolled_is_allowed(self):
        self.assert_allowed(self.get(self.make_staff()))

    def test_course_staff_of_another_course_run_is_forbidden(self):
        other_course = CourseFactory.create(org="Org1", number="Python100", run="Spring2027")

        response = self.get(self.make_staff(other_course))

        assert response.status_code == 403

    def test_global_staff_is_allowed(self):
        self.assert_allowed(self.get(UserFactory(is_staff=True)))

    def test_staff_masquerading_as_learner_is_still_judged_as_staff(self):
        staff = self.make_staff()
        self.client.login(username=staff.username, password=TEST_PASSWORD)
        self.update_masquerade(course=self.course, role="student")
        assert self.client.session[MASQUERADE_SETTINGS_KEY][self.course.id].role == "student"

        response = self.client.get(self.url)

        self.assert_allowed(response)

    @ddt.data(
        "/api/cbe/v1/rule_profiles/",
        "/api/cbe/v1/competencies/1/criteria-groups/",
        "/api/cbe/v1/competencies/1/criteria/",
        "/api/cbe/v1/criteria-groups/1/criteria/bulk-update/",
        "/api/cbe/v1/competencies/1/criteria-groups/1/",
    )
    def test_authoring_routes_are_not_served_by_the_lms(self, path):
        response = self.get(UserFactory(is_staff=True), path)

        assert response.status_code == 404


@skip_unless_lms
class TestCourseCriteriaContent(CourseCriteriaTestBase):
    """
    Everyone allowed to read a course run's criteria receives the same content, whatever the learner can see.
    """

    PARTITION_ID = 50

    def setUp(self):
        super().setUp()
        set_course_cohorted(self.course.id, True)
        try:
            catalog_api.get_course_run(self.course.id)
        except CourseRun.DoesNotExist:
            catalog_api.create_course_run_for_modulestore_course_with(self.course.id, title=self.course.display_name)

        chapter = BlockFactory.create(parent_location=self.course.location, category="chapter")
        released = self._add_subsection(chapter, "Released", {"start": datetime.now(UTC) - timedelta(days=30)})
        unreleased = self._add_subsection(chapter, "Unreleased", {"start": datetime.now(UTC) + timedelta(days=30)})
        hidden = self._add_subsection(chapter, "Hidden", {"visible_to_staff_only": True})
        restricted = self._add_subsection(chapter, "Restricted", {"group_access": {self.PARTITION_ID: [1]}})
        # A criterion whose subsection is no longer in the course outline. Its criterion is created after the delete
        # on purpose: deleting a block in Studio fires XBLOCK_DELETED, whose handler deletes the block's ObjectTags,
        # and that cascades to (or, with learner status, is blocked by) the criterion. This case therefore checks
        # only that the endpoint never filters criteria by course structure.
        deleted = self._add_subsection(chapter, "Deleted", {})
        self.store.delete_item(deleted.location, ModuleStoreEnum.UserID.test)
        # The partition is added after the children exist: the course is re-read so that it carries them.
        course = self.store.get_course(self.course.id)
        course.user_partitions = [
            UserPartition(
                self.PARTITION_ID, "Content groups", "", [Group(1, "Group 1"), Group(2, "Group 2")], scheme_id="cohort"
            ),
        ]
        self.store.update_item(course, ModuleStoreEnum.UserID.test)
        self.usage_keys = {str(block.location) for block in (released, unreleased, hidden, restricted, deleted)}

        taxonomy = create_competency_taxonomy(name="Competencies")
        tag = add_tag_to_taxonomy(taxonomy, "Critical Thinking")
        for usage_key in self.usage_keys:
            associate_competency_criterion(
                tag.id,
                usage_key,
                rule_type_override=RuleType.GRADE,
                rule_payload_override={"op": "gte", "value": 0.75, "scale": "percent"},
            )

    def _add_subsection(self, chapter, name, metadata):
        """
        Create a subsection with the given field values under the chapter.
        """
        return BlockFactory.create(
            parent_location=chapter.location, category="sequential", display_name=name, metadata=metadata
        )

    def _enroll_in_content_group(self, group_id):
        """
        Return a learner with an active enrollment who belongs to the given content group of the cohort scheme.
        """
        learner = UserFactory()
        CourseEnrollmentFactory(user=learner, course_id=self.course.id)
        cohort = CohortFactory(course_id=self.course.id, name=f"Cohort {group_id}", users=[learner])
        link_cohort_to_partition_group(cohort, self.PARTITION_ID, group_id)
        return learner

    def test_learners_in_different_content_groups_and_staff_receive_identical_content(self):
        responses = [
            self.get(self._enroll_in_content_group(1)),
            self.get(self._enroll_in_content_group(2)),
            self.get(self.make_staff()),
        ]

        for response in responses:
            assert response.status_code == 200, response.content
        bodies = [response.json() for response in responses]
        assert bodies[0] == bodies[1] == bodies[2]
        assert {criterion["usage_key"] for criterion in bodies[0]["criteria"]} == self.usage_keys
        assert len(bodies[0]["criteria"]) == 5
        assert [competency["name"] for competency in bodies[0]["competencies"]] == ["Critical Thinking"]

    def test_unenrolled_user_receives_no_content(self):
        response = self.get(UserFactory())

        assert response.status_code == 403
        assert "Critical Thinking" not in response.content.decode()


@skip_unless_lms
class TestCourseCriteriaPermission(CourseCriteriaTestBase):
    """
    The permission checked by name, without HTTP.
    """

    def test_missing_or_malformed_course_key_is_refused(self):
        staff = UserFactory(is_staff=True)

        assert not staff.has_perm(PERMISSION, None)
        assert not staff.has_perm(PERMISSION, str(self.course.id))
