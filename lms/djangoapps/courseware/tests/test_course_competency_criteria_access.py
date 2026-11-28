"""
Tests who may read a course run's competency criteria on the LMS.

openedx-core serves ``GET /api/cbe/v1/courses/<course_key>/criteria/`` and checks the permission
``openedx_learning.view_course_competency_criteria``. This module covers the platform's override of that
permission (active enrollment or staff access) and the LMS mounting of the learner URLs.
"""

from datetime import UTC, datetime, timedelta

import ddt
import pytest
from django.test import override_settings
from django.urls import Resolver404, resolve
from opaque_keys.edx.keys import UsageKey
from openedx_catalog import api as catalog_api
from openedx_learning.api import associate_competency_criterion, create_competency_taxonomy
from openedx_learning.models_api import RuleType
from openedx_tagging.api import add_tag_to_taxonomy

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import CourseStaffRole
from common.djangoapps.student.tests.factories import TEST_PASSWORD, CourseEnrollmentFactory, UserFactory
from lms.djangoapps.courseware.access import has_access
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
    Creates a course run, a competency tag, and helpers to add criteria, sign users in and request the criteria.
    """

    COMPETENCY_NAME = "Critical Thinking"

    def setUp(self):
        super().setUp()
        self.course = self.create_course(
            org="Org1", number="Python100", run="Fall2026", metadata={"start": datetime.now(UTC) - timedelta(days=60)}
        )
        self.url = criteria_url(self.course.id)
        taxonomy = create_competency_taxonomy(name="Competencies")
        self.tag = add_tag_to_taxonomy(taxonomy, self.COMPETENCY_NAME)

    def create_course(self, **kwargs):
        """
        Create a course run in the modulestore and its catalog course run, which the criteria endpoint looks up.
        """
        course = CourseFactory.create(**kwargs)
        catalog_api.create_course_run_for_modulestore_course_with(course.id, title=course.display_name)
        return course

    def add_subsection(self, course, display_name, metadata, chapter=None):
        """
        Create a subsection with the given field values, under the chapter or under a new chapter of the course.
        """
        if chapter is None:
            chapter = BlockFactory.create(parent_location=course.location, category="chapter")
        return BlockFactory.create(
            parent_location=chapter.location, category="sequential", display_name=display_name, metadata=metadata
        )

    def associate_criterion(self, usage_key):
        """
        Add a criterion on the block with the given usage key to the competency tag.
        """
        associate_competency_criterion(
            self.tag.id,
            usage_key,
            rule_type_override=RuleType.GRADE,
            rule_payload_override={"op": "gte", "value": 0.75, "scale": "percent"},
        )

    def add_subsection_with_criterion(self, course, display_name, metadata, chapter=None):
        """
        Create a subsection, add a criterion on it, and return its usage key as a string.
        """
        usage_key = str(self.add_subsection(course, display_name, metadata, chapter).location)
        self.associate_criterion(usage_key)
        return usage_key

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

    def assert_full_result(self, response, usage_keys):
        """
        Assert the request was allowed and returned the criteria of exactly the given usage keys, under the tag.
        """
        assert response.status_code == 200, response.content
        body = response.json()
        assert {criterion["object_id"] for criterion in body["criteria"]} == set(usage_keys)
        assert [competency["name"] for competency in body["competencies"]] == [self.COMPETENCY_NAME]

    def assert_refused(self, response, usage_keys):
        """
        Assert the request was refused and the body discloses neither the competency nor any of the usage keys.
        """
        assert response.status_code == 403
        content = response.content.decode()
        assert self.COMPETENCY_NAME not in content
        for usage_key in usage_keys:
            assert usage_key not in content


@skip_unless_lms
@ddt.ddt
class TestCourseCriteriaAccess(CourseCriteriaTestBase):
    """
    HTTP-level access rules of the criteria endpoint on the LMS, against a course run that has criteria.
    """

    def setUp(self):
        super().setUp()
        released_start = datetime.now(UTC) - timedelta(days=30)
        self.usage_keys = {self.add_subsection_with_criterion(self.course, "Released", {"start": released_start})}

    def test_signed_out_is_unauthorized(self):
        response = self.get(None)

        assert response.status_code == 401
        assert self.COMPETENCY_NAME not in response.content.decode()

    @ddt.data(
        "never enrolled",
        "enrolled in this course run and later unenrolled",
        "active enrollment only in a different course run",
        "course staff only of a different course run",
    )
    def test_user_without_active_enrollment_or_staff_role_is_refused(self, case):
        other_course = CourseFactory.create(org="Org1", number="Python100", run="Spring2027")
        if case == "course staff only of a different course run":
            user = self.make_staff(other_course)
        else:
            user = UserFactory()
        if case == "enrolled in this course run and later unenrolled":
            CourseEnrollment.enroll(user, self.course.id)
            CourseEnrollment.unenroll(user, self.course.id)
        if case == "active enrollment only in a different course run":
            CourseEnrollmentFactory(user=user, course_id=other_course.id)

        self.assert_refused(self.get(user), self.usage_keys)

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

        self.assert_full_result(self.get(user), self.usage_keys)

    @override_settings(DISABLE_START_DATES=False)
    def test_active_enrollment_before_course_start_is_allowed(self):
        future_course = self.create_course(
            org="Org1",
            number="Future100",
            run="Fall2027",
            metadata={"start": datetime.now(UTC) + timedelta(days=30)},
        )
        usage_key = self.add_subsection_with_criterion(future_course, "Subsection", {})
        user = UserFactory()
        CourseEnrollmentFactory(user=user, course_id=future_course.id)
        assert not has_access(user, "load", self.store.get_course(future_course.id))

        self.assert_full_result(self.get(user, criteria_url(future_course.id)), {usage_key})

    def test_course_staff_who_is_not_enrolled_is_allowed(self):
        self.assert_full_result(self.get(self.make_staff()), self.usage_keys)

    def test_global_staff_is_allowed(self):
        self.assert_full_result(self.get(UserFactory(is_staff=True)), self.usage_keys)

    def test_staff_masquerading_as_learner_is_still_judged_as_staff(self):
        staff = self.make_staff()
        self.client.login(username=staff.username, password=TEST_PASSWORD)
        self.update_masquerade(course=self.course, role="student")
        assert self.client.session[MASQUERADE_SETTINGS_KEY][self.course.id].role == "student"

        response = self.client.get(self.url)

        self.assert_full_result(response, self.usage_keys)

    @ddt.data(
        "/api/cbe/v1/rule_profiles/",
        "/api/cbe/v1/competencies/1/criteria/",
        "/api/cbe/v1/competencies/1/criteria/1/",
        "/api/cbe/v1/competencies/1/criteria-groups/",
        "/api/cbe/v1/competencies/1/criteria-groups/1/",
        "/api/cbe/v1/criteria-groups/1/criteria/bulk-update/",
        "/api/cbe/v1/competencies/1/blocked-courses/",
    )
    def test_authoring_routes_are_not_served_by_the_lms(self, path):
        with pytest.raises(Resolver404):
            resolve(path)


@skip_unless_lms
@override_settings(DISABLE_START_DATES=False)
class TestCourseCriteriaContent(CourseCriteriaTestBase):
    """
    Everyone allowed to read a course run's criteria receives the same content, whatever the learner can see.
    """

    PARTITION_ID = 50

    def setUp(self):
        super().setUp()
        set_course_cohorted(self.course.id, True)

        chapter = BlockFactory.create(parent_location=self.course.location, category="chapter")
        self.released = self.add_subsection_with_criterion(
            self.course, "Released", {"start": datetime.now(UTC) - timedelta(days=30)}, chapter
        )
        self.unreleased = self.add_subsection_with_criterion(
            self.course, "Unreleased", {"start": datetime.now(UTC) + timedelta(days=30)}, chapter
        )
        self.hidden = self.add_subsection_with_criterion(
            self.course, "Hidden", {"visible_to_staff_only": True}, chapter
        )
        self.restricted = self.add_subsection_with_criterion(
            self.course, "Restricted", {"group_access": {self.PARTITION_ID: [1]}}, chapter
        )
        # A criterion whose subsection is no longer in the course outline. Its criterion is created after the delete
        # on purpose: deleting a block in Studio fires XBLOCK_DELETED, whose handler deletes the block's ObjectTags,
        # and that cascades to (or, with learner status, is blocked by) the criterion. This case therefore checks
        # only that the endpoint never filters criteria by course structure.
        deleted_block = self.add_subsection(self.course, "Deleted", {}, chapter)
        self.store.delete_item(deleted_block.location, ModuleStoreEnum.UserID.test)
        deleted = str(deleted_block.location)
        self.associate_criterion(deleted)
        # The partition is added after the children exist: the course is re-read so that it carries them.
        course = self.store.get_course(self.course.id)
        course.user_partitions = [
            UserPartition(
                self.PARTITION_ID, "Content groups", "", [Group(1, "Group 1"), Group(2, "Group 2")], scheme_id="cohort"
            ),
        ]
        self.store.update_item(course, ModuleStoreEnum.UserID.test)
        self.usage_keys = {self.released, self.unreleased, self.hidden, self.restricted, deleted}

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
        learner = self._enroll_in_content_group(1)
        other_learner = self._enroll_in_content_group(2)
        for user, usage_key, can_load in (
            (learner, self.released, True),
            (learner, self.unreleased, False),
            (learner, self.hidden, False),
            (learner, self.restricted, True),
            (other_learner, self.restricted, False),
        ):
            block = self.store.get_item(UsageKey.from_string(usage_key))
            assert bool(has_access(user, "load", block, self.course.id)) == can_load, usage_key

        responses = [
            self.get(learner),
            self.get(other_learner),
            self.get(self.make_staff()),
        ]

        for response in responses:
            self.assert_full_result(response, self.usage_keys)
        bodies = [response.json() for response in responses]
        assert bodies[0] == bodies[1] == bodies[2]
        assert len(bodies[0]["criteria"]) == 5


@skip_unless_lms
class TestCourseCriteriaPermission(CourseCriteriaTestBase):
    """
    The permission checked by name, without HTTP.
    """

    def test_missing_or_malformed_course_key_is_refused(self):
        staff = UserFactory(is_staff=True)

        assert not staff.has_perm(PERMISSION, None)
        assert not staff.has_perm(PERMISSION, str(self.course.id))
