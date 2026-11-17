"""Helpers shared by the Grades API v2 tests."""

import json
import re
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlencode

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from openedx_authz.models.engine import PolicyCacheControl
from openedx_events.learning.signals import COHORT_MEMBERSHIP_CHANGED

import openedx.core.djangoapps.content.block_structure.api as block_structure_api
from common.djangoapps.student.models import CourseEnrollment, anonymous_id_for_user
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.grades.models import (
    PersistentCourseGrade,
    PersistentSubsectionGrade,
    PersistentSubsectionGradeOverride,
)
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.course_overviews.signals import IMPORT_COURSE_DETAILS
from openedx.core.djangoapps.course_groups import cohorts
from openedx.core.djangoapps.course_groups.models import CohortMembership
from openedx.core.djangoapps.course_groups.tests.helpers import config_course_cohorts
from openedx.core.djangoapps.oauth_dispatch.jwt import _create_jwt
from openedx.core.djangoapps.signals.signals import COURSE_GRADE_CHANGED
from xmodule.modulestore import ModuleStoreEnum
from xmodule.partitions.partitions import Group, UserPartition

GRADE_READ_SCOPE = 'grades:read'

#: The members every paginated body carries.
PAGE_FIELDS = {'count', 'num_pages', 'current_page', 'start', 'next', 'previous', 'results'}

_WRITE_STATEMENTS = ('INSERT', 'UPDATE', 'DELETE', 'REPLACE')
_WRITTEN_TABLE = re.compile(
    r'^\s*(?:INSERT(?:\s+OR\s+\w+|\s+IGNORE)?\s+INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\s+[`"]?(\w+)',
    re.IGNORECASE,
)
_SESSION_WRITE = re.compile(r'^\s*(INSERT INTO|UPDATE|DELETE FROM)\s+[`"]?django_session[`"]?\s', re.IGNORECASE)

#: Tables that hold grade and enrollment state a read must leave alone.
DOMAIN_TABLES = (
    CourseEnrollment,
    CourseOverview,
    PersistentCourseGrade,
    PersistentSubsectionGrade,
    PersistentSubsectionGradeOverride,
)


def with_query(path, **query):
    """Return ``path`` with ``query`` appended when there is anything to append."""
    return f'{path}?{urlencode(query, doseq=True)}' if query else path


def course_grade_list_url(**query):
    """Return the address of the course-grade collection."""
    return with_query(reverse('grade_v2:course_grade_list'), **query)


def course_grade_detail_url(username, course_key, **query):
    """Return the address of one learner's grade in one course."""
    url = reverse('grade_v2:course_grade_detail', kwargs={'username': username, 'course_key': course_key})
    return with_query(url, **query)


def gradebook_entry_list_url(course_key, **query):
    """Return the address of the gradebook of one course."""
    return with_query(reverse('grade_v2:gradebook_entry_list', kwargs={'course_key': course_key}), **query)


def gradebook_entry_detail_url(course_key, username, **query):
    """Return the address of one learner's row in the gradebook of one course."""
    url = reverse('grade_v2:gradebook_entry_detail', kwargs={'course_key': course_key, 'username': username})
    return with_query(url, **query)


def subsection_grade_override_list_url(course_key):
    """Return the address that records subsection grade overrides in one course."""
    return reverse('grade_v2:subsection_grade_override_list', kwargs={'course_key': course_key})


def subsection_grade_detail_url(username, usage_key, **query):
    """Return the address of one learner's grade on one subsection."""
    url = reverse('grade_v2:subsection_grade_detail', kwargs={'username': username, 'usage_key': usage_key})
    return with_query(url, **query)


def subsection_grade_override_history_url(username, usage_key, **query):
    """Return the address of the override history of one learner's grade on one subsection."""
    url = reverse(
        'grade_v2:subsection_grade_override_history_list',
        kwargs={'username': username, 'usage_key': usage_key},
    )
    return with_query(url, **query)


def course_grading_policy_url(course_key, **query):
    """Return the address of the grading policy of one course."""
    return with_query(reverse('grade_v2:course_grading_policy', kwargs={'course_key': course_key}), **query)


def submission_history_list_url(course_key, **query):
    """Return the address of the submission histories of one course."""
    return with_query(reverse('grade_v2:submission_history_list', kwargs={'course_key': course_key}), **query)


def usernames_in(response):
    """Return the username of every row of a paginated body, in the order returned."""
    return [row['username'] for row in response.json()['results']]


def token_header(user, scopes=(GRADE_READ_SCOPE,), is_restricted=False, filters=()):
    """
    Return the request headers carrying a JWT issued to ``user``.

    Arguments:
        scopes: the scopes the token carries.
        is_restricted: whether the token was issued to a restricted application,
            whose scopes and filters are enforced.
        filters: the token's filters, as ``'<type>:<value>'`` strings.
    """
    token = _create_jwt(user, scopes=list(scopes), is_restricted=is_restricted, filters=list(filters))
    return {'HTTP_AUTHORIZATION': f'JWT {token}'}


def warm_read_caches(course_key):
    """
    Fill the stores a grade read populates on first use, for ``course_key``.

    Both are caches, not grade or enrollment state, and both exist on any site
    that has served a request before, but either would hide a real write from
    the read-only assertions below:

    * The collected block structure of the course. Any read that walks the
      course content stores it on a miss, including the default course-grade
      list, not only the views that return a per-subsection breakdown.
    * The one policy-version row of the authorization engine
      (``PolicyCacheControl``). The first permission check after start-up
      creates it with ``get_or_create``. It holds only a version number the
      engine compares to decide when to reload its cached policies, and no
      receiver listens for its save.
    """
    block_structure_api.update_course_in_cache(course_key)
    PolicyCacheControl.get()


def warm_cohort_settings(course_key):
    """
    Copy the cohort settings of ``course_key`` into the database.

    The first cohort-aware read of a course copies its cohort settings from the
    course content into the database. The gradebook reads cohorts, so on a
    course that no read has touched yet it performs that copy; see
    ``GradebookColdCohortSettingsTest``.
    """
    cohorts.is_course_cohorted(course_key)


def warm_anonymous_id(user, course_key):
    """
    Store the anonymous id of ``user`` in ``course_key``.

    Computing a grade the learner has no stored grade for reads their
    submissions, which are keyed by the learner's anonymous id in the course,
    and the first lookup of that id stores it; see
    ``ComputedGradeAnonymousIdTest``.
    """
    anonymous_id_for_user(user, course_key)


def written_tables(captured):
    """Return the table each write statement in ``captured`` writes, in order, leaving out the session table."""
    tables = []
    for query in captured.captured_queries:
        match = _WRITTEN_TABLE.match(query['sql'])
        if match and match.group(1) != 'django_session':
            tables.append(match.group(1))
    return tables


#: The name of the cohort that learners with no cohort are assigned to in ``CohortedCourseMixin``.
AUTO_COHORT = 'Auto cohort'

#: What the first cohort-aware read of an uncohorted learner stores: the membership row and the
#: link from the cohort's user group to the learner.
COHORT_ASSIGNMENT_TABLES = ['course_groups_cohortmembership', 'course_groups_courseusergroup_users']

#: The tracking events that assignment emits, in order.
COHORT_ASSIGNMENT_TRACKING = ['edx.cohort.user_added', 'edx.cohort.user_add_requested']


class CohortedCourseMixin:
    """
    Makes the test course cohorted, with a content-group partition that places
    learners by cohort, and enrolls a learner who is in no cohort yet.

    On such a course, any read that asks which content group a learner is in
    assigns that learner to a cohort first; see ``capture_cohort_assignment``.
    The course of the shared fixture has neither, so no other test reaches that
    branch.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        course = cls.store.get_course(cls.course_key)
        course.user_partitions = [
            UserPartition(
                50, 'Content groups', 'Groups of learners who see different content',
                [Group(1, 'Group A'), Group(2, 'Group B')],
                scheme_id='cohort',
            ),
        ]
        cls.store.update_item(course, ModuleStoreEnum.UserID.test)

    def setUp(self):
        super().setUp()
        config_course_cohorts(self.store.get_course(self.course_key), is_cohorted=True, auto_cohorts=[AUTO_COHORT])
        # Storing the cohort settings publishes the course again, so the block structure is stale.
        warm_read_caches(self.course_key)
        warm_cohort_settings(self.course_key)
        self.uncohorted = UserFactory(username='uncohorted', password=self.password)
        CourseEnrollmentFactory(user=self.uncohorted, course_id=self.course_key)
        warm_anonymous_id(self.uncohorted, self.course_key)

    def cohort_of(self, user):
        """Return the name of the cohort ``user`` is in, in the test course, or None."""
        membership = CohortMembership.objects.filter(user=user, course_id=self.course_key).first()
        return membership.course_user_group.name if membership else None


@contextmanager
def capture_cohort_assignment():
    """
    Record what the enclosed block writes and announces about cohort membership.

    Yields a namespace that, on exit, holds ``tables`` (every table written, in
    order, the session table left out), ``memberships`` (``(username, cohort
    name)`` for each membership-changed event) and ``tracking`` (the name of each
    cohort tracking event). The events are recorded instead of sent.
    """
    record = SimpleNamespace(tables=[], memberships=[], tracking=[])
    with mock.patch.object(COHORT_MEMBERSHIP_CHANGED, 'send_event') as membership_sent, \
            mock.patch.object(cohorts, 'tracker') as tracker, \
            CaptureQueriesContext(connection) as captured:
        yield record
    record.tables = written_tables(captured)
    record.memberships = [
        (call.kwargs['cohort'].user.pii.username, call.kwargs['cohort'].name)
        for call in membership_sent.call_args_list
    ]
    record.tracking = [call.args[0] for call in tracker.emit.call_args_list]


def _is_session_write(sql):
    """Return True for a statement that writes the session table."""
    return _SESSION_WRITE.match(sql) is not None


class ReadOnlyRequestMixin:
    """Asserts that a request leaves the stored data exactly as it found it."""

    @contextmanager
    def assert_nothing_is_written(self):
        """
        Fail unless the enclosed block issues no INSERT, UPDATE or DELETE, leaves
        every grade and enrollment table at the same row count, and sends no
        course-import or grade-change signal.

        Writes to the session table are left out: request middleware saves the
        session on every view, whatever the view does.
        """
        counts_before = {model: model.objects.count() for model in DOMAIN_TABLES}
        with mock.patch.object(IMPORT_COURSE_DETAILS, 'send') as course_details_sent, \
                mock.patch.object(COURSE_GRADE_CHANGED, 'send') as grade_changed_sent, \
                CaptureQueriesContext(connection) as captured:
            yield
        writes = [
            query['sql'] for query in captured.captured_queries
            if query['sql'].lstrip().upper().startswith(_WRITE_STATEMENTS)
            and not _is_session_write(query['sql'])
        ]
        assert writes == [], f'the request wrote: {writes}'
        assert course_details_sent.call_args_list == [], 'the request sent IMPORT_COURSE_DETAILS'
        assert grade_changed_sent.call_args_list == [], 'the request sent COURSE_GRADE_CHANGED'
        counts_after = {model: model.objects.count() for model in DOMAIN_TABLES}
        assert counts_after == counts_before, f'row counts changed: {counts_before} -> {counts_after}'


def count_queries(client, url):
    """Return ``(response, number of queries)`` for a GET of ``url``."""
    with CaptureQueriesContext(connection) as captured:
        response = client.get(url)
    return response, len(captured.captured_queries)


def count_post_queries(client, url, body):
    """Return ``(response, number of queries)`` for a POST of ``body``, sent as JSON, to ``url``."""
    with CaptureQueriesContext(connection) as captured:
        response = client.post(url, data=json.dumps(body), content_type='application/json')
    return response, len(captured.captured_queries)
